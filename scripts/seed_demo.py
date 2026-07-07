"""Seed demo data for local testing of the VM integration.

Run from the backend/ directory:

    python scripts/seed_demo.py

This creates:
  - admin@test.com / admin123  (admin)
  - candidate@test.com / test123  (candidate)
  - A demo coding problem
  - A demo interview scheduled for candidate@test.com (ready to start)
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

# Make src/ importable when running from backend/scripts/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import AsyncSessionLocal, engine, Base
from src.models.user import User
from src.models.problem import Problem
from src.models.interview import Interview
from src.repositories.user_repository import user_repository
from src.repositories.problem_repository import problem_repository
from src.repositories.interview_repository import interview_repository

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEMO_USERS = [
    {"email": "admin@test.com", "password": "admin123", "role": "admin", "invite_password": "admin123"},
    {"email": "candidate@test.com", "password": "test123", "role": "candidate", "invite_password": "test123"},
]

DEMO_PROBLEM = {
    "title": "Two Sum",
    "markdown_content": """# Two Sum

Given an array of integers `nums` and an integer `target`, return indices of the two numbers such that they add up to `target`.

You may assume that each input would have exactly one solution, and you may not use the same element twice.

## Example

Input: nums = [2,7,11,15], target = 9
Output: [0,1]

## Constraints

- 2 <= nums.length <= 10^4
- -10^9 <= nums[i] <= 10^9
- -10^9 <= target <= 10^9
""",
    "duration_minutes": 60,
    "difficulty": "Medium",
}


async def _create_tables() -> None:
    """Create tables if they do not exist (helpful for fresh SQLite DBs)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_users(db: AsyncSession) -> dict[str, User]:
    users = {}
    for demo in DEMO_USERS:
        existing = await user_repository.get_by_email(db, demo["email"])
        if existing is not None:
            print(f"User already exists: {demo['email']}")
            users[demo["role"]] = existing
            continue

        user = await user_repository.create_user(
            db,
            email=demo["email"],
            password_hash=pwd_context.hash(demo["password"]),
            role=demo["role"],
            invite_password=demo["invite_password"],
        )
        print(f"Created {demo['role']}: {demo['email']} / {demo['password']}")
        users[demo["role"]] = user
    return users


async def _seed_problem(db: AsyncSession) -> Problem:
    result = await db.execute(
        select(Problem).where(Problem.title == DEMO_PROBLEM["title"])
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        print(f"Problem already exists: {existing.title}")
        return existing

    problem = await problem_repository.create_problem(
        db,
        title=DEMO_PROBLEM["title"],
        markdown_content=DEMO_PROBLEM["markdown_content"],
        duration_minutes=DEMO_PROBLEM["duration_minutes"],
        difficulty=DEMO_PROBLEM["difficulty"],
    )
    print(f"Created problem: {problem.title} ({problem.difficulty}, {problem.duration_minutes} min)")
    return problem


async def _seed_interview(
    db: AsyncSession, candidate: User, problem: Problem
) -> Interview:
    existing = await interview_repository.get_by_candidate(db, candidate.id)
    for interview in existing:
        if interview.problem_id == problem.id and interview.status in {"scheduled", "active"}:
            print(f"Demo interview already exists: {interview.id}")
            return interview

    # Schedule 5 minutes ago so the interview can be started immediately.
    scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    interview = await interview_repository.create_interview(
        db,
        candidate_id=candidate.id,
        problem_id=problem.id,
        scheduled_at=scheduled_at,
        duration_minutes=problem.duration_minutes,
    )
    print(f"Created demo interview: {interview.id}")
    print(f"  Candidate: {candidate.email}")
    print(f"  Problem: {problem.title}")
    print(f"  Scheduled at: {scheduled_at.isoformat()}")
    print("  You can start it via POST /api/interviews/{id}/start")
    return interview


async def _seed() -> None:
    await _create_tables()
    async with AsyncSessionLocal() as db:
        users = await _seed_users(db)
        problem = await _seed_problem(db)
        candidate = users.get("candidate")
        if candidate is not None:
            await _seed_interview(db, candidate, problem)
        await db.commit()
    print("Demo seed complete.")


if __name__ == "__main__":
    asyncio.run(_seed())
