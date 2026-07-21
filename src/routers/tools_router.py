from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ..tools.pages import get_preview_page, get_api_tester_page

router = APIRouter(tags=["tools"])


@router.get("/interviews/{interview_id}/tools/preview")
async def tools_preview(
    request: Request,
    interview_id: str,
    token: str = Query(None),
    url: str = Query(None),
):
    base_url = str(request.base_url).rstrip("/")
    proxy_base = f"{base_url}/api/interviews/{interview_id}/ide" if interview_id else ""
    html = get_preview_page(
        interview_id=interview_id,
        token=token or "",
        url=url or "",
        proxy_base=proxy_base,
    )
    return HTMLResponse(content=html)


@router.get("/interviews/{interview_id}/tools/api-tester")
async def tools_api_tester(
    request: Request,
    interview_id: str,
    token: str = Query(None),
):
    base_url = str(request.base_url).rstrip("/")
    proxy_base = f"{base_url}/api/interviews/{interview_id}/ide" if interview_id else ""
    html = get_api_tester_page(
        interview_id=interview_id,
        token=token or "",
        proxy_base=proxy_base,
    )
    return HTMLResponse(content=html)
