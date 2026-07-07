from fastapi.responses import StreamingResponse


def build_streaming_response(agen):
    """Wrap a raw SSE text generator in a FastAPI StreamingResponse.

    CodeVector already returns OpenAI-compatible SSE, so we just pass
    the raw chunks through.
    """
    return StreamingResponse(
        agen,
        media_type="text/event-stream",
    )
