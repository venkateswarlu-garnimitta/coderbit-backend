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
    When ``logs_path`` is *not* provided, the S3 artifact prefix is probed as a
    fallback — this covers the case where the artifact uploader stored the log
    in S3 but the calling context did not return the key.
    Log data is never stored in the DB — the S3 key is kept for on-demand reads.
    """
    logger.info(
        "[END_SESSION] Importing session logs for interview %s (codebase_path=%s logs_path=%s)",
        interview_id,
        codebase_path,
        logs_path,
    )
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        logger.error("[END_SESSION] Cannot import logs: interview %s not found", interview_id)
        return

    persisted_prefix = str(codebase_path) if codebase_path else None

    # If logs_path was not provided, probe S3 to see if the hook server
    # already uploaded the log (e.g. the artifact uploader returned success
    # but the log_key was not propagated correctly).
    effective_logs_path = logs_path
    if not effective_logs_path:
        try:
            from src.lib.s3_recording import _find_log_key, _get_s3_client

            client = _get_s3_client()
            found = _find_log_key(client, interview_id)
            if found:
                logger.info(
                    "[END_SESSION] Found log in S3 via fallback probe for interview %s: key=%s",
                    interview_id,
                    found,
                )
                effective_logs_path = found
            else:
                logger.warning(
                    "[END_SESSION] S3 probe found no log for interview %s",
                    interview_id,
                )
        except Exception:
            logger.exception(
                "[END_SESSION] S3 probe failed for interview %s",
                interview_id,
            )

    existing = await session_repository.get_by_interview(db, interview_id)
    if existing is not None:
        logger.info("[END_SESSION] Updating existing session record for interview %s", interview_id)
        existing.uploaded_at = datetime.now(timezone.utc)
        if persisted_prefix:
            existing.codebase_path = persisted_prefix
        if effective_logs_path:
            existing.logs_path = effective_logs_path
    else:
        logger.info("[END_SESSION] Creating new session record for interview %s", interview_id)
        session = session_repository.model_cls(
            id=str(uuid4()),
            interview_id=interview_id,
            codebase_path=persisted_prefix,
            logs_path=effective_logs_path,
            uploaded_at=datetime.now(timezone.utc),
        )
        db.add(session)

    await db.commit()
    logger.info(
        "[END_SESSION] Session record persisted for interview %s (logs_path=%s)",
        interview_id,
        effective_logs_path,
    )
