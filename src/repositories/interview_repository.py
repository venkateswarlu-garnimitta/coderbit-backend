from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..lib.dt import to_utc
from ..models.interview import Interview
from .base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..models.user import User


class InterviewRepository(BaseRepository[Interview]):
    def __init__(self):
        super().__init__(Interview)

    async def get_by_candidate(
        self, db: "AsyncSession", candidate_id: str
    ) -> list[Interview]:
        result = await db.execute(
            select(Interview).where(Interview.candidate_id == candidate_id)
        )
        return list(result.scalars().all())

    async def get_active_or_scheduled_for_candidate(
        self, db: "AsyncSession", candidate_id: str
    ) -> list[Interview]:
        """Return scheduled or active interviews for overlap checks."""
        result = await db.execute(
            select(Interview).where(
                Interview.candidate_id == candidate_id,
                Interview.status.in_(["scheduled", "active"]),
            )
        )
        return list(result.scalars().all())

    async def get_with_session(
        self, db: "AsyncSession", interview_id: str
    ) -> Interview | None:
        result = await db.execute(
            select(Interview)
            .where(Interview.id == interview_id)
            .options(
                selectinload(Interview.candidate),
                selectinload(Interview.problem),
                selectinload(Interview.session),
                selectinload(Interview.scores),
            )
        )
        return result.scalar_one_or_none()

    async def get_active(self, db: "AsyncSession") -> list[Interview]:
        result = await db.execute(
            select(Interview)
            .where(Interview.status == "active")
            .options(selectinload(Interview.candidate))
        )
        return list(result.scalars().all())

    async def list_for_user(self, db: "AsyncSession", user: "User") -> list[Interview]:
        stmt = (
            select(Interview)
            .options(
                selectinload(Interview.candidate),
                selectinload(Interview.problem),
                selectinload(Interview.scores),
            )
            .order_by(Interview.created_at.desc())
        )
        if user.role == "candidate":
            stmt = stmt.where(Interview.candidate_id == user.id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        db: "AsyncSession",
        interview_id: str,
        status: str,
        **kwargs,
    ) -> Interview | None:
        interview = await self.get(db, interview_id)
        if interview is None:
            return None
        update_data = {"status": status}
        update_data.update(kwargs)
        return await self.update(db, interview, update_data)

    async def get_expired_active(
        self, db: "AsyncSession", now: datetime | None = None
    ) -> list[Interview]:
        if now is None:
            now = datetime.now(timezone.utc)
        expired = []
        for interview in await self.get_active(db):
            if interview.started_at is None:
                continue
            if to_utc(interview.started_at) + timedelta(
                minutes=interview.duration_minutes
            ) < now:
                expired.append(interview)
        return expired

    async def create_interview(
        self,
        db: "AsyncSession",
        *,
        candidate_id: str,
        problem_id: str,
        scheduled_at: datetime,
        duration_minutes: int,
        status: str = "scheduled",
    ) -> Interview:
        interview = Interview(
            id=str(uuid4()),
            candidate_id=candidate_id,
            problem_id=problem_id,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            status=status,
        )
        return await self.create(db, interview)

    async def delete(self, db: "AsyncSession", interview_id: str) -> bool:
        interview = await self.get(db, interview_id)
        if interview is None:
            return False
        await db.delete(interview)
        await db.flush()
        return True

    async def update_scoring_status(
        self, db: "AsyncSession", interview_id: str, scoring_status: str
    ) -> Interview | None:
        interview = await self.get(db, interview_id)
        if interview is None:
            return None
        return await self.update(
            db, interview, {"scoring_status": scoring_status}
        )


interview_repository = InterviewRepository()
