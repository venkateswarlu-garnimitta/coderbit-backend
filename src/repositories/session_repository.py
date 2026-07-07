from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.session import InterviewSession
from .base import BaseRepository


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
            logs=[],
            uploaded_at=datetime.now(timezone.utc),
        )
        return await self.create(db, session)

    async def append_log(
        self,
        db: AsyncSession,
        session_id: str,
        log_entry: dict,
    ) -> InterviewSession | None:
        session = await self.get(db, session_id)
        if session is None:
            return None
        logs = list(session.logs) if session.logs else []
        logs.append(log_entry)
        session.logs = logs
        session.uploaded_at = datetime.now(timezone.utc)
        await db.commit()
        return session

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

    async def get_logs(self, db: AsyncSession, session_id: str) -> list:
        session = await self.get(db, session_id)
        if session is None or session.logs is None:
            return []
        # Logs may be stored as the full session JSON object (from workspace
        # import) or as a flat list of events (from append_interview_log).
        if isinstance(session.logs, dict) and "events" in session.logs:
            return list(session.logs["events"])
        return list(session.logs)


session_repository = SessionRepository()
