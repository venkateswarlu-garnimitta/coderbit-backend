"""Seed initial admin and candidate users if the users table is empty."""

import logging
from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.user_repository import user_repository

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_DEFAULT_USERS = [
    {
        "email": "admin@test.com",
        "password": "admin123",
        "role": "admin",
    },
    {
        "email": "interviewer@test.com",
        "password": "test123",
        "role": "interviewer",
    },
    {
        "email": "candidate@test.com",
        "password": "test123",
        "role": "candidate",
    },
]


async def seed_users(db: AsyncSession) -> int:
    """Create default users if the users table is empty.

    Returns the number of users created.
    """
    existing = await user_repository.list_all(db, limit=1)
    if existing:
        return 0

    created = 0
    for u in _DEFAULT_USERS:
        await user_repository.create_user(
            db,
            email=u["email"],
            password_hash=pwd_context.hash(u["password"]),
            role=u["role"],
        )
        created += 1

    await db.commit()
    logger.info("Seeded %d default user(s)", created)
    return created
