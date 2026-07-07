from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.interview import Interview
from ..models.score import Score
from .base import BaseRepository


class ScoreRepository(BaseRepository[Score]):
    def __init__(self):
        super().__init__(Score)

    async def get_by_interview(
        self, db: AsyncSession, interview_id: str
    ) -> Score | None:
        result = await db.execute(
            select(Score).where(Score.interview_id == interview_id)
        )
        return result.scalar_one_or_none()

    async def list_with_details(self, db: AsyncSession) -> list[Score]:
        result = await db.execute(
            select(Score)
            .order_by(Score.scored_at.desc())
            .options(
                selectinload(Score.interview).selectinload(Interview.candidate),
                selectinload(Score.interview).selectinload(Interview.problem),
            )
        )
        return list(result.scalars().all())


score_repository = ScoreRepository()
