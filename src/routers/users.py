from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db, require_role
from ..models.interview import Interview
from ..models.score import Score
from ..models.session import InterviewSession
from ..models.user import User
from ..repositories.user_repository import user_repository
from ..schemas.users import CreateUserRequest, UpdateUserRequest, UserResponse

router = APIRouter(prefix="/users", tags=["users"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


@router.get("/candidates")
async def search_candidates(
    email: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "interviewer")),
) -> list[dict]:
    query = email.strip().lower()
    users = await user_repository.search_candidates(db, query=query, limit=50 if not query else 10)
    return [{"id": u.id, "email": u.email, "role": u.role} for u in users]


@router.get("")
async def list_users(
    email: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> list[UserResponse]:
    query = email.strip().lower()
    users = await user_repository.list_all(db, query=query)
    return [
        UserResponse(
            id=u.id,
            email=u.email,
            role=u.role,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> UserResponse:
    existing = await user_repository.get_by_email(db, body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = await user_repository.create_user(
        db,
        email=body.email,
        password_hash=_hash_password(body.password),
        role=body.role,
        invite_password=body.password if body.role == "candidate" else None,
    )

    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at.isoformat(),
    )


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> UserResponse:
    user = await user_repository.get(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    update_data: dict = {}
    if body.email is not None:
        existing = await user_repository.get_by_email(db, body.email)
        if existing is not None and existing.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )
        update_data["email"] = body.email
    if body.password is not None:
        update_data["password_hash"] = _hash_password(body.password)
        if user.role == "candidate":
            update_data["invite_password"] = body.password
    if body.role is not None:
        update_data["role"] = body.role

    if update_data:
        user = await user_repository.update(db, user, update_data)

    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at.isoformat(),
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> None:
    user = await user_repository.get(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    # Delete all scores, sessions, and interviews belonging to this user
    stmt = select(Interview).where(Interview.candidate_id == user_id)
    result = await db.execute(stmt)
    interviews = list(result.scalars().all())
    for interview in interviews:
        score_stmt = select(Score).where(Score.interview_id == interview.id)
        score_result = await db.execute(score_stmt)
        for s in score_result.scalars().all():
            await db.delete(s)

        session_stmt = select(InterviewSession).where(InterviewSession.interview_id == interview.id)
        session_result = await db.execute(session_stmt)
        for s in session_result.scalars().all():
            await db.delete(s)

        await db.delete(interview)
    await db.commit()

    await user_repository.delete(db, user_id)
