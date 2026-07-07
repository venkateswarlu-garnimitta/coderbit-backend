from datetime import datetime, timedelta, timezone

from jose import jwt

from .. import config
from ..dependencies import oauth2_scheme


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRES_MINUTES)
    to_encode.update({"exp": expire})
    encoded = jwt.encode(to_encode, config.JWT_SECRET, algorithm="HS256")
    return encoded


# Re-export from dependencies so the rest of the app has a single source of truth.
from ..dependencies import get_current_user, require_role  # noqa: E402

__all__ = ["create_access_token", "oauth2_scheme", "get_current_user", "require_role"]
