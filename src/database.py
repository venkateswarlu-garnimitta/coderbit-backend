from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from . import config

# Ensure the parent directory exists for SQLite file-based URLs.
if config.DATABASE_URL.startswith("sqlite"):
    db_path = Path(config.DATABASE_URL.replace("sqlite+aiosqlite:///", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(config.DATABASE_URL, future=True, echo=False)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session and commit on success / rollback on exception."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
