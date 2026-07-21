import secrets
import time
from collections import defaultdict
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db, require_role
from ..middleware.auth import create_access_token
from ..models.user import User
from ..repositories.user_repository import user_repository
from ..schemas.auth import RegisterRequest, TokenResponse
from .. import config

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12, deprecated="auto")

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Sliding-window counters keyed by IP and by account email.
# Limits: 10 attempts per IP per minute, 5 attempts per account per minute.
_WINDOW_SECONDS = 60
_IP_MAX_ATTEMPTS = 10
_ACCOUNT_MAX_ATTEMPTS = 5

_ip_attempts: dict[str, list[float]] = defaultdict(list)
_account_attempts: dict[str, list[float]] = defaultdict(list)
_rate_lock = Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str, email: str) -> None:
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    with _rate_lock:
        _ip_attempts[ip] = [t for t in _ip_attempts[ip] if t > cutoff]
        _account_attempts[email] = [t for t in _account_attempts[email] if t > cutoff]

        if len(_ip_attempts[ip]) >= _IP_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Please try again later.",
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )
        if len(_account_attempts[email]) >= _ACCOUNT_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts for this account. Please try again later.",
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )

        _ip_attempts[ip].append(now)
        _account_attempts[email].append(now)


def _clear_rate_limit(ip: str, email: str) -> None:
    """Clear counters on successful login so legitimate users aren't locked out."""
    with _rate_lock:
        _ip_attempts.pop(ip, None)
        _account_attempts.pop(email, None)


# ── Cookie helpers ────────────────────────────────────────────────────────────
_COOKIE_NAME = "auth_token"
_COOKIE_MAX_AGE = config.JWT_EXPIRES_MINUTES * 60


def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    # CSRF double-submit token for cookie-based requests. The frontend reads
    # this cookie and echoes it as the X-CSRF-Token header on mutating requests
    # that do not carry a Bearer token.
    response.set_cookie(
        key="csrf_token",
        value=secrets.token_urlsafe(32),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=is_https,
        samesite="strict",
        path="/",
    )


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
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    ip = _client_ip(request)
    _check_rate_limit(ip, form_data.username)

    user = await user_repository.get_by_email(db, form_data.username)
    if user is None or not _verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _clear_rate_limit(ip, form_data.username)
    access_token = create_access_token(
        {"sub": user.id, "email": user.email, "role": user.role}
    )
    _set_auth_cookie(response, access_token, request)
    return TokenResponse(
        access_token=access_token,
        role=user.role,
        user_id=user.id,
    )


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(key=_COOKIE_NAME, path="/")
    return {"message": "logged out"}


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
    }
