import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Configure logging so diagnostic messages from the VM integration are visible.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from .database import AsyncSessionLocal
from .lib import microvm_manager
from .lib.seed_metrics import seed_metrics
from .lib.seed_problems import seed_problems
from .lib.seed_users import seed_users
from .lib.artifact_uploader import collect_and_upload_artifacts
from .lib.session_import import import_session_logs
from .lib.s3_recording import ensure_lifecycle_rule
from .repositories.interview_repository import interview_repository
from .routers import auth, ide_proxy, interviews, llm_gateway, metrics, problems, scoring, tools_router, users
from .routers.scoring import run_scoring_background


def _run_migrations() -> None:
    """Apply pending Alembic migrations before handling requests."""
    alembic_cfg_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    alembic_cfg = Config(str(alembic_cfg_path))
    command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_run_migrations)

    async with AsyncSessionLocal() as db:
        await seed_metrics(db)
        await seed_problems(db)
        await seed_users(db)

    await asyncio.to_thread(ensure_lifecycle_rule)
    await asyncio.to_thread(microvm_manager.ensure_resource_limits)

    task = asyncio.create_task(_expiry_worker())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _expiry_worker():
    while True:
        await asyncio.sleep(60)
        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                expired = await interview_repository.get_expired_active(db, now)
                for interview in expired:
                    try:
                        # Upload candidate artifacts to S3 before destroying the
                        # environment on session timeout.
                        artifact_result = None
                        if interview.microvm_endpoint:
                            artifact_result = await collect_and_upload_artifacts(
                                interview
                            )
                        if interview.microvm_id:
                            await asyncio.to_thread(
                                microvm_manager.terminate_microvm, interview.microvm_id
                            )
                        await interview_repository.update_status(
                            db, interview.id, "completed", ended_at=now
                        )
                        await import_session_logs(
                            db,
                            interview.id,
                            codebase_path=artifact_result["prefix"]
                            if artifact_result
                            else None,
                            logs_path=artifact_result.get("log_key")
                            if artifact_result
                            else None,
                        )
                        await run_scoring_background(interview.id)
                    except Exception:
                        # Skip individual failures so one bad MicroVM doesn't block others.
                        pass
        except Exception:
            # Background cleanup should never crash the server.
            pass


app = FastAPI(lifespan=lifespan)

# ── Request body size limit ──────────────────────────────────────────────────────────
# Reject payloads larger than 10 MB at the middleware layer before any route
# handler reads the body, preventing large-payload DoS.
_MAX_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024)))


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Exempt recording uploads — webcam videos routinely exceed 10 MB.
        if request.url.path.endswith("/recording"):
            return await call_next(request)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large"},
            )
        return await call_next(request)


# ── CSRF protection ───────────────────────────────────────────────────────────────────
# Double-submit cookie pattern: the frontend must echo the csrf_token cookie
# value in the X-CSRF-Token request header on all state-changing requests.
# Safe methods (GET, HEAD, OPTIONS) and requests using Bearer auth (API clients,
# IDE proxy) are exempt.
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_EXEMPT_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/health"}


def _is_csrf_exempt(path: str) -> bool:
    if path in _CSRF_EXEMPT_PATHS:
        return True
    if path.startswith("/api/interviews/") and ("/ide/" in path or "/tools/" in path):
        return True
    return False


class _CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _CSRF_SAFE_METHODS:
            return await call_next(request)
        if _is_csrf_exempt(request.url.path):
            return await call_next(request)
        # Exempt requests that carry a Bearer token — these are API/IDE-proxy
        # calls that cannot set cookies and are not CSRF-vulnerable.
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            return await call_next(request)
        # Validate double-submit: cookie value must match header value.
        csrf_cookie = request.cookies.get("csrf_token")
        csrf_header = request.headers.get("x-csrf-token")
        if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )
        return await call_next(request)


app.add_middleware(_BodySizeLimitMiddleware)
app.add_middleware(_CSRFMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(problems.router, prefix="/api")
app.include_router(interviews.router, prefix="/api")
app.include_router(scoring.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(ide_proxy.router, prefix="/api")
app.include_router(tools_router.router, prefix="/api")
app.include_router(llm_gateway.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("src.main:app", host=host, port=3001, reload=True)
