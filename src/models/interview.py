from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .problem import Problem
    from .score import Score
    from .session import InterviewSession
    from .user import User


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False
    )
    problem_id: Mapped[str] = mapped_column(
        String, ForeignKey("problems.id"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="scheduled"
    )
    scoring_status: Mapped[str] = mapped_column(
        String, nullable=False, default="not_scored"
    )
    microvm_id: Mapped[str | None] = mapped_column(String, nullable=True)
    microvm_endpoint: Mapped[str | None] = mapped_column(String, nullable=True)
    auth_token: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    candidate: Mapped["User"] = relationship(
        "User", back_populates="interviews", lazy="selectin"
    )
    problem: Mapped["Problem"] = relationship(
        "Problem", back_populates="interviews", lazy="selectin"
    )
    session: Mapped["InterviewSession | None"] = relationship(
        "InterviewSession", back_populates="interview", uselist=False, lazy="selectin"
    )
    scores: Mapped[list["Score"]] = relationship(
        "Score", back_populates="interview", lazy="selectin"
    )
