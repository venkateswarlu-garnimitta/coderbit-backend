from typing import Generic, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model_cls: type[ModelType]):
        self.model_cls = model_cls

    async def get(self, db: AsyncSession, obj_id: str) -> ModelType | None:
        return await db.get(self.model_cls, obj_id)

    async def get_all(self, db: AsyncSession) -> Sequence[ModelType]:
        result = await db.execute(select(self.model_cls))
        return result.scalars().all()

    async def create(self, db: AsyncSession, obj: ModelType) -> ModelType:
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    async def update(
        self, db: AsyncSession, db_obj: ModelType, update_data: dict
    ) -> ModelType:
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def delete(self, db: AsyncSession, obj_id: str) -> bool:
        obj = await self.get(db, obj_id)
        if obj is None:
            return False
        await db.delete(obj)
        await db.commit()
        return True
