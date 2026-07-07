import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, status

from .. import config


@dataclass
class _Window:
    requests: list[float] = field(default_factory=list)


class RateLimiter:
    """In-memory sliding-window rate limiter keyed by JWT subject.

    Suitable for a single uvicorn worker process. For horizontal scaling,
    swap this backend for a Redis-based implementation without changing
    the interface.
    """

    def __init__(self, rpm: int | None = None) -> None:
        self._rpm = rpm if rpm is not None else config.GATEWAY_RATE_LIMIT_RPM
        self._windows: dict[str, _Window] = defaultdict(_Window)

    def check(self, user_id: str) -> None:
        """Raise 429 if the user has exceeded the rate limit."""
        now = time.time()
        window = self._windows[user_id]

        # Prune entries older than 60 seconds
        cutoff = now - 60.0
        window.requests = [t for t in window.requests if t > cutoff]

        if len(window.requests) >= self._rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down.",
            )

        window.requests.append(now)
