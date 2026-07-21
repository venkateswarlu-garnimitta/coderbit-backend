from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..lib.dt import to_utc
from ..lib.token_encryption import decrypt_token, encrypt_token
from ..models.interview import Interview
from .base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..models.user import User


def _encrypt_auth_token(data: dict) -> dict:
    """Return a copy of update_data with auth_token encrypted if present."""
    if "auth_token" in data and data["auth_token"]:
        data = {**data, "auth_token": encrypt_token(data["auth_token"])}
    return data


def _decrypt_interview(interview: Interview) -> Interview:
    """Decrypt auth_token on a loaded Interview object in-place."""
    if interview.auth_token:
        try:
            interview.auth_token = decrypt_token(interview.auth_token)
        except ValueError:
            logger.warning(
                "auth_token for interview %s could not be decrypted "
                "(falling back to plaintext — likely a row written before "
                "encryption was enabled or the encryption key was rotated).",
                interview.id,
            )
    return interview


class InterviewRepository(BaseRepository[Interview]):
    def __init__(self):
        super().__init__(Interview)

    async def get(
        self, db: "AsyncSession", obj_id: str
    ) -> Interview | None:
        interview = await super().get(db, obj_id)
        return _decrypt_interview(interview) if interview else None

    async def update(
        self, db: "AsyncSession", db_obj: Interview, update_data: dict
    ) -> Interview:
        """Encrypt auth_token before persisting, then decrypt after refresh."""
        update_data = _encrypt_auth_token(update_data)
        interview = await super().update(db, db_obj, update_data)
        return _decrypt_interview(interview)

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
        interview = result.scalar_one_or_none()
        return _decrypt_interview(interview) if interview else None

    async def get_active(self, db: "AsyncSession") -> list[Interview]:
        result = await db.execute(
            select(Interview)
            .where(Interview.status == "active")
            .options(selectinload(Interview.candidate))
        )
        return [_decrypt_interview(i) for i in result.scalars().all()]

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
