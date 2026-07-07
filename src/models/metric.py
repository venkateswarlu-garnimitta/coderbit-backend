from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    rubric: Mapped[str] = mapped_column(Text, nullable=False)
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
