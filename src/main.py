import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging so diagnostic messages from the VM integration are visible.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from .database import AsyncSessionLocal
from .lib import microvm_manager
from .lib import container_manager
from .lib.seed_metrics import seed_metrics
from .lib.seed_problems import seed_problems
from .lib.artifact_uploader import collect_and_upload_artifacts
from .lib.session_import import import_session_logs
from .repositories.interview_repository import interview_repository
from .routers import auth, ide_proxy, interviews, llm_gateway, metrics, problems, scoring, users
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
                            log_s3_key=artifact_result.get("log_key")
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
app.include_router(llm_gateway.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("src.main:app", host=host, port=3001, reload=True)
