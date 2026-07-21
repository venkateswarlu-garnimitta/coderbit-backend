"""Thin async HTTP proxy to the CodeVector OpenAI-compatible endpoint."""

import logging

import httpx
from fastapi import HTTPException

from .. import config

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.CODEVECTOR_API_KEY}",
        "Content-Type": "application/json",
        "x-client-app": "opencode",
    }


def _log_key_status() -> None:
    """Log whether the API key is configured (never log the key itself)."""
    if not config.CODEVECTOR_API_KEY:
        logger.error("CODEVECTOR_API_KEY is empty — requests will be rejected.")


def _handle_upstream_error(exc: httpx.HTTPStatusError) -> None:
    """Log upstream error details and raise a FastAPI HTTPException."""
    status = exc.response.status_code
    try:
        body = exc.response.text
    except (httpx.ResponseNotRead, AttributeError):
        body = "<response body not available>"
    logger.error(
        "CodeVector returned %s for %s. Response: %s",
        status,
        exc.request.url,
        body[:500],
    )
    raise HTTPException(
        status_code=status,
        detail=f"CodeVector error: {body[:500]}",
    ) from exc


async def chat_completions(request_body: bytes) -> dict:
    """Forward a non-streaming chat request to CodeVector and return JSON."""
    _log_key_status()
    url = f"{config.CODEVECTOR_BASE_URL.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                content=request_body,
                headers=_headers(),
                timeout=config.CODEVECTOR_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _handle_upstream_error(exc)
        return response.json()


async def chat_completions_stream(request_body: bytes):
    """Forward a streaming chat request to CodeVector and yield raw SSE chunks."""
    _log_key_status()
    url = f"{config.CODEVECTOR_BASE_URL.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            url,
            content=request_body,
            headers=_headers(),
            timeout=config.CODEVECTOR_TIMEOUT_SECONDS,
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await response.aread()
                _handle_upstream_error(exc)
            async for chunk in response.aiter_text():
                yield chunk
