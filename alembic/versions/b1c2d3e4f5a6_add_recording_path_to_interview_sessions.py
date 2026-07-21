"""add recording_path to interview_sessions

Revision ID: b1c2d3e4f5a6
Revises: 60f04a034e54
Create Date: 2026-07-06 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "60f04a034e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column("recording_path", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "recording_path")
