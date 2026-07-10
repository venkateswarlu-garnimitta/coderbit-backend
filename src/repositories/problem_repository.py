from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.problem import Problem
from .base import BaseRepository


class ProblemRepository(BaseRepository[Problem]):
    def __init__(self):
        super().__init__(Problem)

    async def create_problem(
        self,
        db: AsyncSession,
        *,
        title: str,
        markdown_content: str,
        duration_minutes: int,
        difficulty: str = "Medium",
        acceptance_criteria: str | None = None,
        metric_ids: list[str] | None = None,
        created_at: datetime | None = None,
    ) -> Problem:
        problem = Problem(
            id=str(uuid4()),
            title=title,
            markdown_content=markdown_content,
            duration_minutes=duration_minutes,
            difficulty=difficulty,
            acceptance_criteria=acceptance_criteria,
            metric_ids=metric_ids or [],
            created_at=created_at or datetime.now(timezone.utc),
        )
        return await self.create(db, problem)


problem_repository = ProblemRepository()
