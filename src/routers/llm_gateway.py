import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import config
from ..llm_gateway.auth import get_current_user_jwt
from ..llm_gateway.client import chat_completions, chat_completions_stream
from ..llm_gateway.rate_limiter import RateLimiter
from ..llm_gateway.streaming import build_streaming_response

router = APIRouter(prefix="/v1", tags=["llm-gateway"])

# Module-level singleton — fine for a single worker process.
_rate_limiter = RateLimiter()


def _normalize_model(model: str) -> str:
    """Strip provider prefix (e.g. 'codevector/') so upstream gets bare model id."""
    return model.rsplit("/", 1)[-1].strip()


@router.post("/chat/completions", response_model=None)
async def create_chat_completion(
    request: Request,
    jwt_payload: dict = Depends(get_current_user_jwt),
) -> JSONResponse | StreamingResponse:
    user_id: str = jwt_payload["sub"]
    _rate_limiter.check(user_id)

    json_body = await request.json()
    json_body["model"] = _normalize_model(config.CODEVECTOR_MODEL)
    is_stream = json_body.get("stream", False)
    body = json.dumps(json_body).encode("utf-8")

    if is_stream:
        return build_streaming_response(chat_completions_stream(body))

    result = await chat_completions(body)
    return JSONResponse(content=result)
