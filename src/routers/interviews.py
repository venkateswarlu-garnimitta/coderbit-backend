import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal

from .. import config
from ..dependencies import get_current_user, get_db, require_role
from ..lib import microvm_manager
from ..middleware.auth import create_access_token
from ..lib.artifact_uploader import collect_and_upload_artifacts
from ..lib.dt import to_utc
from ..lib.email import send_interview_scheduled_email_task
from ..lib.session_import import import_session_logs
from ..models.interview import Interview
from ..models.user import User
from ..repositories.interview_repository import interview_repository
from ..repositories.problem_repository import problem_repository
from ..repositories.session_repository import session_repository
from ..repositories.user_repository import user_repository
from ..routers.scoring import run_scoring_background
from ..schemas.interviews import InterviewCreate, InterviewRow, InterviewUpdate

router = APIRouter(prefix="/interviews", tags=["interviews"])
logger = logging.getLogger(__name__)

_ALLOWED_STATUSES = {"scheduled", "active", "completed", "cancelled"}


class LogEntryCreate(BaseModel):
    entry: dict


class EnableRunHookRequest(BaseModel):
    image_arn: str | None = None


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _interviews_overlap(
    existing: Interview, new_start: datetime, new_duration_minutes: int
) -> bool:
    """Return True if the new interview's time range overlaps the existing one."""
    existing_start = to_utc(existing.scheduled_at)
    existing_end = existing_start + timedelta(minutes=existing.duration_minutes)
    new_start_utc = to_utc(new_start)
    new_end = new_start_utc + timedelta(minutes=new_duration_minutes)
    return new_start_utc < existing_end and new_end > existing_start


def _format_interview(interview: Interview) -> InterviewRow:
    # Only expose container/MicroVM details while the interview is active.
    is_active = interview.status == "active"
    return InterviewRow(
        id=interview.id,
        candidate_id=interview.candidate_id,
        candidate_email=interview.candidate.email,
        problem_id=interview.problem_id,
        problem_title=interview.problem.title,
        scheduled_at=to_utc(interview.scheduled_at).isoformat(),
        duration_minutes=interview.duration_minutes,
        status=interview.status,
        scoring_status=interview.scoring_status,
        container_id=interview.container_id if is_active else None,
        container_port=interview.container_port if is_active else None,
        started_at=to_utc(interview.started_at).isoformat() if interview.started_at else None,
        ended_at=to_utc(interview.ended_at).isoformat() if interview.ended_at else None,
        created_at=to_utc(interview.created_at).isoformat(),
        overall_score=interview.score.overall_score if interview.score else None,
    )


@router.post("", response_model=InterviewRow, status_code=status.HTTP_201_CREATED)
async def create_interview(
    body: InterviewCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "interviewer")),
) -> InterviewRow:
    problem = await problem_repository.get(db, body.problem_id)
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
        )

    candidate = await user_repository.get(db, body.candidate_id)
    if candidate is None or candidate.role != "candidate":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found"
        )

    scheduled_at = _parse_dt(body.scheduled_at)

    # Prevent overlapping scheduled/active interviews for the same candidate.
    existing = await interview_repository.get_active_or_scheduled_for_candidate(
        db, body.candidate_id
    )
    for iv in existing:
        if _interviews_overlap(iv, scheduled_at, problem.duration_minutes):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Candidate already has an interview scheduled during this time period",
            )

    interview = await interview_repository.create_interview(
        db,
        candidate_id=body.candidate_id,
        problem_id=body.problem_id,
        scheduled_at=scheduled_at,
        duration_minutes=problem.duration_minutes,
    )
    # Eagerly load relationships for the response.
    interview = await interview_repository.get_with_session(db, interview.id)

    background_tasks.add_task(
        send_interview_scheduled_email_task,
        email=candidate.email,
        password=candidate.invite_password,
        scheduled_at=scheduled_at,
        duration_minutes=problem.duration_minutes,
    )

    return _format_interview(interview)


@router.get("", response_model=list[InterviewRow])
async def list_interviews(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[InterviewRow]:
    interviews = await interview_repository.list_for_user(db, current_user)
    return [_format_interview(i) for i in interviews]


@router.get("/{interview_id}", response_model=InterviewRow)
async def get_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InterviewRow:
    interview = await interview_repository.get_with_session(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if current_user.role == "candidate" and interview.candidate_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )

    return _format_interview(interview)


@router.patch("/{interview_id}", response_model=InterviewRow)
async def update_interview(
    interview_id: str,
    body: InterviewUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "interviewer")),
) -> InterviewRow:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        interview = await interview_repository.get_with_session(db, interview_id)
        return _format_interview(interview)

    if "status" in updates and updates["status"] not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Allowed: {_ALLOWED_STATUSES}",
        )

    if "problem_id" in updates:
        problem = await problem_repository.get(db, updates["problem_id"])
        if problem is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
            )
        updates["duration_minutes"] = problem.duration_minutes

    if "scheduled_at" in updates:
        updates["scheduled_at"] = _parse_dt(updates["scheduled_at"])
        duration = updates.get("duration_minutes", interview.duration_minutes)
        existing = await interview_repository.get_active_or_scheduled_for_candidate(
            db, interview.candidate_id
        )
        for iv in existing:
            if iv.id == interview.id:
                continue
            if _interviews_overlap(
                iv, updates["scheduled_at"], duration
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Candidate already has an interview scheduled during this time period",
                )

    interview = await interview_repository.update(db, interview, updates)
    interview = await interview_repository.get_with_session(db, interview.id)
    return _format_interview(interview)


@router.delete("/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "interviewer")),
) -> None:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if interview.status != "scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel an interview with status '{interview.status}'. Only scheduled interviews can be cancelled.",
        )

    if not await interview_repository.delete(db, interview_id):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete interview",
        )


@router.post("/{interview_id}/start")
async def start_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    interview = await interview_repository.get_with_session(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if current_user.role not in {"admin", "candidate"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    if current_user.role == "candidate" and interview.candidate_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )

    if interview.status != "scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview is not scheduled",
        )

    if datetime.now(timezone.utc) < to_utc(interview.scheduled_at) - timedelta(
        minutes=10
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview cannot be started more than 10 minutes early",
        )

    candidate_jwt_payload = {
        "sub": interview.candidate_id,
        "email": interview.candidate.email,
        "role": "candidate",
    }
    if config.GATEWAY_JWT_ISSUER:
        candidate_jwt_payload["iss"] = config.GATEWAY_JWT_ISSUER
    candidate_jwt = create_access_token(candidate_jwt_payload)

    result = await asyncio.to_thread(
        microvm_manager.start_microvm,
        interview_id,
        interview.candidate.email,
        interview.problem.markdown_content,
        candidate_jwt,
    )
    logger.info(
        "Started MicroVM for interview %s: candidate_email=%s problem_title=%s "
        "problem_markdown_length=%s microvm_id=%s endpoint=%s token_expires_at=%s",
        interview_id,
        interview.candidate.email,
        interview.problem.title,
        len(interview.problem.markdown_content),
        result.get("microvm_id"),
        result.get("endpoint"),
        result.get("token_expires_at"),
    )

    now = datetime.now(timezone.utc)
    interview = await interview_repository.update(
        db,
        interview,
        {
            "status": "active",
            "microvm_id": result["microvm_id"],
            "microvm_endpoint": result["endpoint"],
            "auth_token": result["auth_token"],
            "token_expires_at": result["token_expires_at"],
            "started_at": now,
        },
    )

    expires_at = now + timedelta(minutes=interview.duration_minutes)
    return {
        "ide_url": f"/api/interviews/{interview_id}/ide/",
        "microvm_id": result["microvm_id"],
        "expires_at": expires_at.isoformat(),
    }


@router.post("/{interview_id}/terminate")
async def terminate_interview(
    interview_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    interview = await interview_repository.get_with_session(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if current_user.role not in {"admin", "candidate"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    if current_user.role == "candidate" and interview.candidate_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )

    # Collect and upload the candidate's workspace archive and Coding Assistant
    # log to S3 before the MicroVM is terminated. Termination destroys the
    # MicroVM filesystem, so this must complete first. Failures are logged but
    # do not block termination, otherwise an unresponsive MicroVM could leak.
    artifact_s3_prefix = None
    if interview.microvm_endpoint:
        artifact_s3_prefix = await collect_and_upload_artifacts(interview)

    if interview.microvm_id:
        await asyncio.to_thread(
            microvm_manager.terminate_microvm, interview.microvm_id
        )

    await interview_repository.update(
        db,
        interview,
        {
            "status": "completed",
            "ended_at": datetime.now(timezone.utc),
            "container_id": None,
            "container_port": None,
        },
    )

    # Import the candidate's workspace session log into the DB so scoring has
    # data to evaluate. Do this in a background task because it only touches
    # the filesystem and the DB.
    async def _import_and_score():
        async with AsyncSessionLocal() as score_db:
            await import_session_logs(
                score_db,
                interview_id,
                codebase_path=artifact_s3_prefix,
            )
            await run_scoring_background(interview_id)

    background_tasks.add_task(_import_and_score)
    return {"message": "completed"}


@router.get("/{interview_id}/ide-status")
async def ide_status(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if current_user.role not in {"admin", "candidate"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    if current_user.role == "candidate" and interview.candidate_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )

    if interview.status == "scheduled" or interview.microvm_endpoint is None:
        return {"ready": False, "reason": "interview_not_started"}

    endpoint = interview.microvm_endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"

    # Try the root path first because that is what the IDE iframe loads. Some
    # MicroVM images expose /healthz or /health as well, but a 200 on a health
    # endpoint is misleading if / itself rejects auth.
    paths = ["/", "/healthz", "/health"]
    last_status: int | None = None
    last_error: str | None = None

    for path in paths:
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                follow_redirects=False,
                http1=True,
                http2=False,
            ) as client:
                logger.info(
                    "ide-status interview %s path=%s token_prefix=%s",
                    interview_id,
                    path,
                    interview.auth_token[:8] if interview.auth_token else "",
                )
                resp = await client.get(
                    f"{endpoint}{path}",
                    headers={
                        "X-aws-proxy-auth": interview.auth_token,
                        "X-aws-proxy-port": str(microvm_manager.TARGET_PORT),
                    },
                    follow_redirects=False,
                )
            last_status = resp.status_code
            # 2xx or 3xx means the endpoint is reachable and auth is accepted.
            # 401/403 indicates a token/port mismatch; treat as not ready so
            # the frontend keeps polling while the operator checks the token.
            if 200 <= resp.status_code < 400:
                logger.info(
                    "IDE status for interview %s: ready (path=%s status=%s)",
                    interview_id,
                    path,
                    resp.status_code,
                )
                return {"ready": True}
            if resp.status_code in (401, 403):
                logger.error(
                    "IDE status for interview %s: auth rejected (path=%s status=%s). "
                    "The auth token may be invalid or the MicroVM image may reject it.",
                    interview_id,
                    path,
                    resp.status_code,
                )
                return {
                    "ready": False,
                    "reason": "auth_rejected",
                    "last_status": resp.status_code,
                }
        except Exception as exc:
            last_error = str(exc)

    logger.warning(
        "IDE status for interview %s: not ready (last_status=%s last_error=%s)",
        interview_id,
        last_status,
        last_error,
    )
    return {
        "ready": False,
        "reason": "microvm_unhealthy",
        "last_status": last_status,
        "last_error": last_error,
    }


@router.get("/{interview_id}/log")
async def get_interview_log(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "interviewer")),
) -> dict:
    session = await session_repository.get_by_interview(db, interview_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Log not found"
        )

    logs = session.logs
    if logs is None:
        logs = []
    if isinstance(logs, list):
        interview = await interview_repository.get_with_session(db, interview_id)
        return {
            "sessionId": interview_id,
            "candidateName": interview.candidate.email if interview and interview.candidate else "",
            "startedAt": interview.started_at.isoformat() if interview and interview.started_at else datetime.now(timezone.utc).isoformat(),
            "events": logs,
        }
    return logs


@router.post("/{interview_id}/log")
async def append_interview_log(
    interview_id: str,
    body: LogEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "candidate")),
) -> dict:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if current_user.role == "candidate" and interview.candidate_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )

    session = await session_repository.get_or_create(db, interview_id)
    await session_repository.append_log(db, session.id, body.entry)
    return {"message": "logged"}


@router.post("/admin/enable-run-hook", response_model=dict)
async def enable_run_hook(
    body: EnableRunHookRequest,
    current_user: User = Depends(require_role("admin")),
) -> dict:
    """Enable the Lambda MicroVM run hook on the configured or provided image.

    This is an administrative operation. If it fails, the backend will still
    fall back to starting MicroVMs without runHookPayload until the image is
    fixed.
    """
    return microvm_manager.ensure_run_hook_enabled(body.image_arn)
@router.post("/{interview_id}/recording")
async def upload_interview_recording(
    interview_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if current_user.role not in {"admin", "candidate"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    if current_user.role == "candidate" and interview.candidate_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    if interview.status not in {"active", "completed"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Recording can only be uploaded for active or completed interviews",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Recording file is empty",
        )

    config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.RECORDINGS_DIR / f"{interview_id}.webm"
    dest.write_bytes(content)

    await session_repository.set_recording_path(db, interview_id, str(dest))
    return {"message": "recording uploaded", "path": str(dest)}


@router.get("/{interview_id}/recording")
async def get_interview_recording(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "interviewer")),
):
    session = await session_repository.get_by_interview(db, interview_id)
    if session is None or not session.recording_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found"
        )

    path = Path(session.recording_path)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recording file missing"
        )

    return FileResponse(path, media_type="video/webm", filename=path.name)
