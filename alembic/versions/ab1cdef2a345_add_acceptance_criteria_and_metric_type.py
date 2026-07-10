"""add acceptance_criteria to problems, metric_type to metrics

Revision ID: ab1cdef2a345
Revises: 93added83163
Create Date: 2026-07-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab1cdef2a345'
down_revision: Union[str, Sequence[str], None] = '93added83163'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "problems",
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
    )
    op.add_column(
        "metrics",
        sa.Column("metric_type", sa.String(), nullable=False, server_default="turn_based"),
    )


def downgrade() -> None:
    op.drop_column("metrics", "metric_type")
    op.drop_column("problems", "acceptance_criteria")
