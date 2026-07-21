"""Reverse proxy from the backend to candidate Lambda MicroVM IDE instances.

Browsers cannot attach custom headers to <iframe> requests, so candidates talk
through this backend route. The backend injects the required AWS MicroVM auth
header (`X-aws-proxy-auth`) on every forwarded HTTP request and uses the AWS
WebSocket subprotocol handshake when proxying WebSocket traffic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
import websockets
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    status,
)
from starlette.websockets import WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..dependencies import get_db
from ..lib import microvm_manager
from ..lib.dt import to_utc
from ..models.interview import Interview
from ..repositories.interview_repository import interview_repository

router = APIRouter(tags=["ide"])

logger = logging.getLogger(__name__)

# Allowed Origins for WebSocket upgrades. Populated from FRONTEND_URL and
# any additional values in WS_ALLOWED_ORIGINS (comma-separated).
def _build_ws_origin_allowlist() -> frozenset[str]:
    origins = set()
    if config.FRONTEND_URL:
        origins.add(config.FRONTEND_URL.rstrip("/"))
    extra = os.getenv("WS_ALLOWED_ORIGINS", "")
    for o in extra.split(","):
        o = o.strip()
        if o:
            origins.add(o)
    # Always allow localhost variants for local development.
    # localhost:8080 is the code-server origin when proxied through the backend
    # (seen in webview parentOrigin).
    origins.update([
        "http://localhost:5173",
        "http://localhost:3001",
        "http://localhost:8080",
        "http://127.0.0.1:5173",
    ])
    return frozenset(origins)

_WS_ALLOWED_ORIGINS = _build_ws_origin_allowlist()

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_HEADERS_TO_STRIP = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
}

# Browser/request headers that should not be forwarded to the AWS MicroVM proxy.
# Cookies, auth, and forwarded-for headers can cause the upstream proxy to reject
# an otherwise valid X-aws-proxy-auth token.
_HEADERS_TO_NOT_FORWARD = {
    "cookie",
    "authorization",
    "referer",
    "origin",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-real-ip",
    "x-request-id",
}

_TOKEN_REFRESH_BUFFER_MINUTES = 5
_MAX_WS_RECONNECT_ATTEMPTS = 5


def _set_cookie_header(token: str, request: Request) -> str:
    """Return a Set-Cookie value for the IDE proxy token.

    The cookie is scoped to /api/interviews/ so it is sent on every iframe
    request (including redirects) without needing the token in the query string.
    """
    parts = [
        f"interviewai_ide_token={token}",
        "Path=/api/interviews/",
        "HttpOnly",
    ]
    # Only mark Secure + SameSite=None when on HTTPS. On HTTP (local dev)
    # browsers reject SameSite=None without Secure, silently dropping the
    # cookie and breaking WebSocket auth (1006). Lax is the default on HTTP
    # and works fine for same-origin proxied loads.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if request.url.scheme == "https" or (forwarded_proto and forwarded_proto.lower() == "https"):
        parts.append("Secure")
        parts.append("SameSite=None")
    return "; ".join(parts)


def _decode_token(token: str | None) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return {"id": user_id, "role": payload.get("role")}
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def _authorize_request(request: Request, token_query: str | None) -> dict:
    token = token_query
    if not token:
        token = request.cookies.get("interviewai_ide_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    current_user = _decode_token(token)
    if current_user.get("role") not in {"admin", "candidate"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    return current_user


async def _authorize_and_load_interview(
    interview_id: str, current_user: dict, db: AsyncSession
) -> Interview:
    """Authorize the user for the interview and load active MicroVM details.

    Combines the authorization and load checks into a single DB read.
    """
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )
    if current_user["role"] == "candidate" and interview.candidate_id != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    if interview.status == "scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview has not started yet",
        )
    if interview.microvm_endpoint is None or interview.auth_token is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Interview has no MicroVM connection details",
        )
    return interview


async def _ensure_token_fresh(interview, db: AsyncSession):
    """Refresh the MicroVM auth token if it is close to expiry."""
    expires_at = to_utc(interview.token_expires_at)
    if expires_at is None:
        return interview

    if datetime.now(timezone.utc) >= expires_at - timedelta(
        minutes=_TOKEN_REFRESH_BUFFER_MINUTES
    ):
        logger.info(
            "Refreshing MicroVM auth token for interview %s (MicroVM %s)",
            interview.id,
            interview.microvm_id,
        )
        token_data = await asyncio.to_thread(
            microvm_manager.refresh_token, interview.microvm_id
        )
        interview = await interview_repository.update(
            db,
            interview,
            {
                "auth_token": token_data["auth_token"],
                "token_expires_at": token_data["token_expires_at"],
            },
        )
    return interview


def _build_upstream_url(endpoint: str, path: str, query_params: list[tuple[str, str]]) -> str:
    endpoint = endpoint.strip()
    if not endpoint.startswith(("http://", "https://", "ws://", "wss://")):
        endpoint = f"https://{endpoint}"
    upstream = endpoint.rstrip("/") + "/" + path
    if query_params:
        upstream += "?" + "&".join(f"{k}={v}" for k, v in query_params)
    return upstream


def _parse_proxy_path(path: str) -> tuple[int | None, str]:
    prefix = "proxy/"
    if path.startswith(prefix):
        after_prefix = path[len(prefix):]
        slash = after_prefix.find("/")
        port_str = after_prefix[:slash] if slash != -1 else after_prefix
        if port_str.isdigit():
            port = int(port_str)
            rest = after_prefix[slash + 1:] if slash != -1 else ""
            return port, rest
    return None, path


def _clean_response_headers(response_headers: httpx.Headers) -> dict[str, str]:
    """Return upstream response headers suitable for forwarding to the client.

    Used when the response body is read, modified, and re-encoded. Content-Length
    and Content-Encoding are dropped so FastAPI can compute fresh values for the
    rewritten body.
    """
    cleaned: dict[str, str] = {}
    for name, value in response_headers.items():
        lower = name.lower()
        if lower in _HOP_BY_HOP_HEADERS or lower in _HEADERS_TO_STRIP:
            continue
        if lower in {"content-length", "content-encoding"}:
            continue
        cleaned[name] = value
    return cleaned


async def _rewrite_docs_response(
    response: httpx.Response,
    interview_id: str,
    proxy_port: int,
) -> Response:
    """Rewrite FastAPI's /docs HTML so Swagger UI loads openapi.json through the proxy."""
    try:
        body = await response.aread()
        text = body.decode("utf-8", errors="replace")
        proxied_openapi_url = (
            f"/api/interviews/{interview_id}/ide/proxy/{proxy_port}/openapi.json"
        )
        text = text.replace('"/openapi.json"', f'"{proxied_openapi_url}"')
        text = text.replace("'/openapi.json'", f"'{proxied_openapi_url}'")
        return Response(
            content=text.encode("utf-8"),
            status_code=response.status_code,
            headers=_clean_response_headers(response.headers),
            media_type=response.headers.get("content-type"),
        )
    finally:
        await response.aclose()


async def _rewrite_openapi_response(
    response: httpx.Response,
    interview_id: str,
    proxy_port: int,
) -> Response:
    """Inject the proxy path into the OpenAPI servers field for Swagger UI."""
    try:
        body = await response.aread()
        try:
            spec = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            spec = {}
        if not isinstance(spec, dict):
            spec = {}
        server_url = f"/api/interviews/{interview_id}/ide/proxy/{proxy_port}"
        spec["servers"] = [{"url": server_url}]
        return Response(
            content=json.dumps(spec).encode("utf-8"),
            status_code=response.status_code,
            headers=_clean_response_headers(response.headers),
            media_type=response.headers.get("content-type"),
        )
    finally:
        await response.aclose()


def _aws_ws_subprotocols(auth_token: str) -> list[str]:
    return [
        "lambda-microvms",
        f"lambda-microvms.authentication.{auth_token}",
        f"lambda-microvms.port.{microvm_manager.TARGET_PORT}",
    ]


@router.api_route(
    "/interviews/{interview_id}/ide",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
@router.api_route(
    "/interviews/{interview_id}/ide/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_ide_http(
    request: Request,
    interview_id: str,
    path: str = "",
    token_query: str | None = Query(None, alias="token"),
    db: AsyncSession = Depends(get_db),
):
    current_user = _authorize_request(request, token_query)
    auth_source = "query" if token_query else "cookie"
    logger.info(
        "ide_proxy request interview=%s user=%s role=%s auth_source=%s",
        interview_id,
        current_user.get("id"),
        current_user.get("role"),
        auth_source,
    )
    interview = await _authorize_and_load_interview(interview_id, current_user, db)
    interview = await _ensure_token_fresh(interview, db)

    query_params = [
        (k, v)
        for k, v in request.query_params.multi_items()
        if k != "token"
    ]

    # Parse proxy/{port}/ prefix for Swagger rewrite logic. The full path
    # (including /proxy/{port}/) is forwarded as-is to code-server, which has a
    # built-in proxy that handles routing to localhost:{port}. X-aws-proxy-port
    # must stay 8080 — MicroVM only allows that port.
    proxy_port, proxy_rest = _parse_proxy_path(path)

    upstream_url = _build_upstream_url(
        interview.microvm_endpoint, path, query_params
    )

    # Only send the AWS MicroVM proxy auth headers. Forwarding browser headers
    # (User-Agent, Accept-Encoding, Sec-Fetch-*, etc.) causes the upstream proxy
    # to reject an otherwise valid token with 403 "Token authentication failed".
    headers: dict[str, str] = {
        "X-aws-proxy-auth": interview.auth_token,
        "X-aws-proxy-port": str(microvm_manager.TARGET_PORT),
    }
    # For proxy requests (paths starting with proxy/) forward cookies so that
    # user apps with CSRF/auth cookies work through the proxy chain.
    if path.startswith("proxy/"):
        cookie = request.headers.get("cookie")
        if cookie:
            headers["Cookie"] = cookie
    # Preserve Content-Type for POST/PUT requests so the upstream can parse body.
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type

    logger.info(
        "Proxying interview %s to %s (token prefix=%s, port=%s)",
        interview_id,
        upstream_url,
        interview.auth_token[:8] if interview.auth_token else "",
        microvm_manager.TARGET_PORT,
    )
    logger.debug(
        "Upstream request headers for interview %s: %s",
        interview_id,
        headers,
    )

    try:
        body = await request.body()
    except ClientDisconnect:
        logger.warning(
            "Client disconnected before body read for interview %s; skipping proxy",
            interview_id,
        )
        raise HTTPException(status_code=499, detail="Client disconnected")

    client = httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=False,
        http1=True,
        http2=False,
    )
    try:
        req = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
        response = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise

    response_headers: dict[str, str] = {}
    for name, value in response.headers.items():
        lower = name.lower()
        if lower in _HOP_BY_HOP_HEADERS or lower in _HEADERS_TO_STRIP:
            continue
        response_headers[name] = value

    # Persist the token in a cookie so redirects and subsequent iframe requests
    # (e.g. code-server's ?folder=... redirect) are still authenticated even
    # when the query parameter is lost.
    if token_query:
        response_headers["Set-Cookie"] = _set_cookie_header(token_query, request)

    # For error responses, read the body into memory so we can close the client
    # immediately and avoid httpx streaming issues (e.g. StreamConsumed).
    if response.status_code >= 400:
        try:
            error_body = await response.aread()
        except Exception as exc:
            logger.warning(
                "Failed to read upstream error body for interview %s: %s",
                interview_id,
                exc,
            )
            error_body = b""
        finally:
            await response.aclose()
            await client.aclose()

        # Diagnostic retry: if the upstream rejected the request, try again with
        # the absolute minimum headers. This tells us whether a forwarded browser
        # header is causing the auth failure.
        if response.status_code == 403:
            minimal_headers = {
                "X-aws-proxy-auth": interview.auth_token,
                "X-aws-proxy-port": str(microvm_manager.TARGET_PORT),
            }
            logger.warning(
                "Retrying interview %s upstream %s with minimal headers: %s",
                interview_id,
                upstream_url,
                minimal_headers,
            )
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                http1=True,
                http2=False,
            ) as retry_client:
                retry_response = await retry_client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=minimal_headers,
                    content=body,
                )
                if retry_response.status_code < 400:
                    logger.warning(
                        "Minimal-header retry succeeded for interview %s: %s",
                        interview_id,
                        retry_response.status_code,
                    )
                    retry_response_headers: dict[str, str] = {}
                    for name, value in retry_response.headers.items():
                        lower = name.lower()
                        if lower in _HOP_BY_HOP_HEADERS or lower in _HEADERS_TO_STRIP:
                            continue
                        retry_response_headers[name] = value
                    return Response(
                        content=await retry_response.aread(),
                        status_code=retry_response.status_code,
                        headers=retry_response_headers,
                        media_type=retry_response.headers.get("content-type"),
                    )
                logger.warning(
                    "Minimal-header retry also failed for interview %s: %s",
                    interview_id,
                    retry_response.status_code,
                )

        logger.warning(
            "Proxy error for interview %s: upstream %s returned %s body=%s response_headers=%s",
            interview_id,
            upstream_url,
            response.status_code,
            error_body[:1000].decode("utf-8", errors="replace"),
            dict(response.headers),
        )
        return Response(
            content=error_body,
            status_code=response.status_code,
            headers=response_headers,
            media_type=response.headers.get("content-type"),
        )

    # Rewrite FastAPI Swagger docs responses so the openapi.json URL and the
    # "Try it out" base URL point back through the proxy. All other responses
    # are passed through unchanged.
    if proxy_port is not None:
        if proxy_rest.rstrip("/") == "docs":
            try:
                return await _rewrite_docs_response(response, interview_id, proxy_port)
            finally:
                await client.aclose()
        if proxy_rest == "openapi.json":
            try:
                return await _rewrite_openapi_response(response, interview_id, proxy_port)
            finally:
                await client.aclose()

    async def _close():
        await response.aclose()
        await client.aclose()

    response_content_type = response.headers.get("content-type", "")
    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=response_headers,
        media_type=response_content_type,
        background=BackgroundTask(_close),
    )


async def _client_to_server(websocket: WebSocket, upstream):
    while True:
        msg = await websocket.receive()
        if msg["type"] == "websocket.disconnect":
            raise WebSocketDisconnect()
        if msg.get("bytes") is not None:
            await upstream.send(msg["bytes"])
        elif msg.get("text") is not None:
            await upstream.send(msg["text"])


async def _server_to_client(websocket: WebSocket, upstream):
    async for data in upstream:
        if isinstance(data, bytes):
            await websocket.send_bytes(data)
        else:
            await websocket.send_text(data)


@router.websocket("/interviews/{interview_id}/ide/{path:path}")
async def proxy_ide_ws(
    websocket: WebSocket,
    interview_id: str,
    path: str,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # code-server WebSocket connections do not include ?token=... in the URL.
    # The HTTP proxy sets a cookie on the first iframe request; browsers send
    # that cookie automatically on same-origin WebSocket upgrades.
    effective_token = token
    if not effective_token:
        effective_token = websocket.cookies.get("interviewai_ide_token")
    if not effective_token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            effective_token = auth_header[7:].strip()
    if not effective_token:
        logger.warning(
            "WebSocket auth missing for interview %s (no token query param or cookie)",
            interview_id,
        )
        await websocket.close(code=1008)
        return

    # Validate Origin header against the allowlist to prevent cross-site
    # WebSocket hijacking of live terminal sessions. Missing/empty origin
    # is also rejected so the check cannot be bypassed by omitting the header.
    origin = websocket.headers.get("origin", "")
    if not origin or origin not in _WS_ALLOWED_ORIGINS:
        logger.warning(
            "WebSocket upgrade rejected for interview %s: Origin %r not in allowlist",
            interview_id,
            origin,
        )
        await websocket.close(code=1008)
        return

    try:
        current_user = _decode_token(effective_token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    if current_user.get("role") not in {"admin", "candidate"}:
        await websocket.close(code=1008)
        return

    try:
        interview = await _authorize_and_load_interview(interview_id, current_user, db)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    query_params = [
        (k, v)
        for k, v in websocket.query_params.multi_items()
        if k != "token"
    ]

    reconnect_attempts = 0
    while reconnect_attempts < _MAX_WS_RECONNECT_ATTEMPTS:
        try:
            interview = await _ensure_token_fresh(interview, db)
            upstream_url = _build_upstream_url(
                interview.microvm_endpoint.replace("https://", "wss://"),
                path,
                query_params,
            )
            subprotocols = _aws_ws_subprotocols(interview.auth_token)
            ws_headers = {
                "X-aws-proxy-auth": interview.auth_token,
                "X-aws-proxy-port": str(microvm_manager.TARGET_PORT),
            }
            logger.info(
                "WebSocket proxy interview %s to %s (subprotocols=%s)",
                interview_id,
                upstream_url,
                subprotocols,
            )

            async with websockets.connect(
                upstream_url,
                subprotocols=subprotocols,
                additional_headers=ws_headers,
                # Keep the upstream (code-server) leg alive through any proxy
                # idle timeout. code-server's underlying `ws` library auto-answers
                # ping frames with pong, so this does not trigger premature closes.
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as upstream:
                reconnect_attempts = 0
                client_task = asyncio.create_task(
                    _client_to_server(websocket, upstream)
                )
                server_task = asyncio.create_task(
                    _server_to_client(websocket, upstream)
                )
                done, pending = await asyncio.wait(
                    {client_task, server_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Always cancel the still-running sibling so we never leak a
                # task (and a dangling websocket.receive) across reconnects.
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        raise exc
        except WebSocketDisconnect:
            break
        except websockets.exceptions.ConnectionClosed:
            reconnect_attempts += 1
            logger.info(
                "MicroVM WebSocket closed for interview %s, reconnect attempt %d/%d",
                interview_id,
                reconnect_attempts,
                _MAX_WS_RECONNECT_ATTEMPTS,
            )
            await asyncio.sleep(min(2 ** reconnect_attempts, 10))
            continue
        except Exception:
            logger.exception("Unexpected error proxying WebSocket for interview %s", interview_id)
            break

    try:
        await websocket.close()
    except Exception:
        pass
