"""Reverse proxy from the backend to candidate Lambda MicroVM IDE instances.

Browsers cannot attach custom headers to <iframe> requests, so candidates talk
through this backend route. The backend injects the required AWS MicroVM auth
header (`X-aws-proxy-auth`) on every forwarded HTTP request and uses the AWS
WebSocket subprotocol handshake when proxying WebSocket traffic.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import websockets
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
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
    # In local dev the request may be http://; only mark Secure when we are
    # actually on HTTPS so the browser does not drop the cookie.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if request.url.scheme == "https" or (forwarded_proto and forwarded_proto.lower() == "https"):
        parts.append("Secure")
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


async def _authorize_interview(
    interview_id: str, current_user: dict, db: AsyncSession
) -> None:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )
    if current_user["role"] == "candidate" and interview.candidate_id != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )


async def _load_active_interview(
    interview_id: str, db: AsyncSession
) -> Interview:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
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
    await _authorize_interview(interview_id, current_user, db)
    interview = await _load_active_interview(interview_id, db)
    interview = await _ensure_token_fresh(interview, db)

    query_params = [
        (k, v)
        for k, v in request.query_params.multi_items()
        if k != "token"
    ]
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

    body = await request.body()

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

    async def _close():
        await response.aclose()
        await client.aclose()

    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
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

    try:
        current_user = _decode_token(effective_token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    if current_user.get("role") not in {"admin", "candidate"}:
        await websocket.close(code=1008)
        return

    try:
        await _authorize_interview(interview_id, current_user, db)
        interview = await _load_active_interview(interview_id, db)
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
            ) as upstream:
                reconnect_attempts = 0
                await asyncio.gather(
                    _client_to_server(websocket, upstream),
                    _server_to_client(websocket, upstream),
                )
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
