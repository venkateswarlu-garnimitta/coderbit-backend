"""Import candidate session logs from workspace JSON files into the DB."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..repositories.interview_repository import interview_repository
from ..repositories.session_repository import session_repository

WORKSPACE_DIR = Path(config.WORKSPACE_DIR)


def _find_session_file(interview_id: str, candidate_email: str) -> Path | None:
    """Return the most recently modified workspace log file for an interview.

    Log files are named `{interview_id}_{candidate_email}_{session_id}.json`
    by the IDE extension so each interview can be matched unambiguously even
    when many candidates run sessions concurrently.
    """
    if not WORKSPACE_DIR.exists():
        return None

    # Prefer the interview-id-based filename introduced in the current extension.
    files = sorted(
        WORKSPACE_DIR.glob(f"{interview_id}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if files:
        return files[0]

    # Fallback: legacy files named `{candidate_email}_{session_id}.json`.
    files = sorted(
        WORKSPACE_DIR.glob(f"{candidate_email}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


async def import_session_logs(
    db: AsyncSession,
    interview_id: str,
    codebase_path: Path | str | None = None,
) -> dict | None:
    """Read the candidate's workspace session file and store it in interview_sessions.

    If ``codebase_path`` is provided, it is persisted alongside the logs so the
    candidate's archived workspace can be retrieved later.

    Returns the imported log payload or None if no file was found.
    """
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        return None

    interview_with_relations = await interview_repository.get_with_session(db, interview_id)
    if interview_with_relations is None or interview_with_relations.candidate is None:
        return None

    candidate_email = interview_with_relations.candidate.email
    log_file = _find_session_file(interview_id, candidate_email)
    if log_file is None:
        return None

    try:
        with log_file.open("r", encoding="utf-8") as f:
            logs = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(logs, dict):
        return None

    persisted_path = str(codebase_path) if codebase_path else None

    existing = await session_repository.get_by_interview(db, interview_id)
    if existing is not None:
        existing.logs = logs
        existing.uploaded_at = datetime.now(timezone.utc)
        if persisted_path:
            existing.codebase_path = persisted_path
    else:
        session = session_repository.model_cls(
            id=str(uuid4()),
            interview_id=interview_id,
            logs=logs,
            codebase_path=persisted_path,
            uploaded_at=datetime.now(timezone.utc),
        )
        db.add(session)

    await db.commit()
    return logs
