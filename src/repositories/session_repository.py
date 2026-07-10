import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.session import InterviewSession
from .base import BaseRepository
from src.lib.workspace_extractor import extract_workspace_from_s3
from src.lib.s3_recording import get_raw_log_json

logger = logging.getLogger(__name__)


class SessionRepository(BaseRepository[InterviewSession]):
    def __init__(self):
        super().__init__(InterviewSession)

    async def get_by_interview(
        self, db: AsyncSession, interview_id: str
    ) -> InterviewSession | None:
        result = await db.execute(
            select(InterviewSession).where(
                InterviewSession.interview_id == interview_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self, db: AsyncSession, interview_id: str
    ) -> InterviewSession:
        session = await self.get_by_interview(db, interview_id)
        if session is not None:
            return session
        session = InterviewSession(
            id=str(uuid4()),
            interview_id=interview_id,
            uploaded_at=datetime.now(timezone.utc),
        )
        return await self.create(db, session)

    async def get_or_fetch_from_s3(
        self, db: AsyncSession, interview_id: str
    ) -> InterviewSession | None:
        """Return existing session or create one by probing S3 for log data.

        Uses ``get_raw_log_json`` with no key to search the interview's artifact
        prefix. Returns ``None`` when neither a DB session nor S3 data exists.
        """
        existing = await self.get_by_interview(db, interview_id)
        if existing is not None:
            return existing

        raw = get_raw_log_json(interview_id, None)
        if raw is None:
            return None

        session = InterviewSession(
            id=str(uuid4()),
            interview_id=interview_id,
            uploaded_at=datetime.now(timezone.utc),
        )
        return await self.create(db, session)

    async def set_recording_path(
        self,
        db: AsyncSession,
        interview_id: str,
        recording_path: str,
    ) -> InterviewSession:
        session = await self.get_or_create(db, interview_id)
        session.recording_path = recording_path
        session.uploaded_at = datetime.now(timezone.utc)
        await db.commit()
        return session

    async def get_S3_logs(self, interview_id: str, log_key: str | None = None) -> list:
        logs = get_raw_log_json(interview_id, log_key)
        if isinstance(logs, dict) and "events" in logs:
            return list(logs["events"])
        return []

    async def get_files_path(self, interview_id) -> str:
        folder = extract_workspace_from_s3(interview_id)
        return folder.as_posix()


session_repository = SessionRepository()
