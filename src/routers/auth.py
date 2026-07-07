from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db, require_role
from ..middleware.auth import create_access_token
from ..models.user import User
from ..repositories.user_repository import user_repository
from ..schemas.auth import RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


@router.post(
    "/register", status_code=status.HTTP_201_CREATED
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> dict:
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
    )

    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "created_at": user.created_at.isoformat(),
    }


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = await user_repository.get_by_email(db, form_data.username)
    if user is None or not _verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        {"sub": user.id, "email": user.email, "role": user.role}
    )
    return TokenResponse(
        access_token=access_token,
        role=user.role,
        user_id=user.id,
    )


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
    }
