from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from .. import config

security = HTTPBearer(auto_error=False)


def _auth_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_jwt(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, str]:
    """Validate JWT signature and expiry; return the payload dict (with 'sub').

    This is intentionally lightweight — it does NOT hit the database.
    """
    if credentials is None:
        raise _auth_exception()

    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=["HS256"],
        )
    except JWTError:
        raise _auth_exception()

    if payload.get("exp") is None:
        raise _auth_exception()

    if config.GATEWAY_JWT_ISSUER and payload.get("iss") != config.GATEWAY_JWT_ISSUER:
        raise _auth_exception()

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise _auth_exception()

    # Do not log the full token — only the user identifier.
    return payload
