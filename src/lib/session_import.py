"""Create or update an interview session record with S3 artifact paths."""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.interview_repository import interview_repository
from ..repositories.session_repository import session_repository

logger = logging.getLogger(__name__)


async def import_session_logs(
    db: AsyncSession,
    interview_id: str,
    codebase_path: str | None = None,
    logs_path: str | None = None,
) -> None:
    """Create or update a session record with S3 artifact paths.

    ``codebase_path`` and ``logs_path`` are stored whenever provided.
    Log data is never stored in the DB — the S3 key is kept for on-demand reads.
    """
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        return

    persisted_prefix = str(codebase_path) if codebase_path else None

    existing = await session_repository.get_by_interview(db, interview_id)
    if existing is not None:
        existing.uploaded_at = datetime.now(timezone.utc)
        if persisted_prefix:
            existing.codebase_path = persisted_prefix
        if logs_path:
            existing.logs_path = logs_path
    else:
        session = session_repository.model_cls(
            id=str(uuid4()),
            interview_id=interview_id,
            codebase_path=persisted_prefix,
            logs_path=logs_path,
            uploaded_at=datetime.now(timezone.utc),
        )
        db.add(session)

    await db.commit()
