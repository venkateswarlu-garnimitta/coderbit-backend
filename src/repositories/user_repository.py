from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from .base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)

    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def list_all(
        self, db: AsyncSession, query: str = "", limit: int = 100
    ) -> list[User]:
        stmt = select(User).order_by(User.created_at.desc())
        if query:
            stmt = stmt.where(User.email.ilike(f"%{query}%"))
        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def search_candidates(
        self, db: AsyncSession, query: str = "", limit: int = 10
    ) -> list[User]:
        stmt = select(User).where(User.role == "candidate")
        if query:
            stmt = stmt.where(User.email.ilike(f"%{query}%"))
        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def create_user(
        self,
        db: AsyncSession,
        *,
        email: str,
        password_hash: str,
        role: str,
        invite_password: str | None = None,
        created_at: datetime | None = None,
    ) -> User:
        user = User(
            id=str(uuid4()),
            email=email,
            password_hash=password_hash,
            invite_password=invite_password,
            role=role,
            created_at=created_at or datetime.now(timezone.utc),
        )
        return await self.create(db, user)


user_repository = UserRepository()
