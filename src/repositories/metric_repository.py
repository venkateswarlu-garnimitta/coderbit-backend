from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.metric import Metric
from .base import BaseRepository


class MetricRepository(BaseRepository[Metric]):
    def __init__(self):
        super().__init__(Metric)

    async def get_by_key(self, db: AsyncSession, key: str) -> Metric | None:
        result = await db.execute(select(Metric).where(Metric.key == key))
        return result.scalar_one_or_none()

    async def get_by_name(self, db: AsyncSession, name: str) -> Metric | None:
        result = await db.execute(select(Metric).where(Metric.name == name))
        return result.scalar_one_or_none()

    async def list_defaults(self, db: AsyncSession) -> list[Metric]:
        result = await db.execute(
            select(Metric).where(Metric.is_custom == False).order_by(Metric.name)
        )
        return list(result.scalars().all())

    async def list_all(self, db: AsyncSession) -> list[Metric]:
        result = await db.execute(select(Metric).order_by(Metric.name))
        return list(result.scalars().all())


metric_repository = MetricRepository()
