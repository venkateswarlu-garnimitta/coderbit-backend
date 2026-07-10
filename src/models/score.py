from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .interview import Interview


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("interview_id", "score_type"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    interview_id: Mapped[str] = mapped_column(
        String, ForeignKey("interviews.id"), nullable=False
    )
    score_type: Mapped[str] = mapped_column(String, nullable=False)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    red_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_llm_response: Mapped[str] = mapped_column(Text, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    interview: Mapped["Interview"] = relationship(
        "Interview", back_populates="scores", lazy="selectin"
    )
