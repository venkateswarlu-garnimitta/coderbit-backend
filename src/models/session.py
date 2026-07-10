from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .interview import Interview


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    interview_id: Mapped[str] = mapped_column(
        String, ForeignKey("interviews.id"), unique=True, nullable=False
    )
    codebase_path: Mapped[str | None] = mapped_column(String, nullable=True)
    recording_path: Mapped[str | None] = mapped_column(String, nullable=True)
    logs_path: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    interview: Mapped["Interview"] = relationship(
        "Interview", back_populates="session", lazy="selectin"
    )
