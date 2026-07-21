"""add microvm fields to interviews

Revision ID: 32fb988d773c
Revises: 60f04a034e54
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "32fb988d773c"
down_revision: Union[str, Sequence[str], None] = "60f04a034e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("interviews", sa.Column("microvm_id", sa.String(), nullable=True))
    op.add_column(
        "interviews", sa.Column("microvm_endpoint", sa.String(), nullable=True)
    )
    op.add_column("interviews", sa.Column("auth_token", sa.String(), nullable=True))
    op.add_column(
        "interviews",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("interviews", "token_expires_at")
    op.drop_column("interviews", "auth_token")
    op.drop_column("interviews", "microvm_endpoint")
    op.drop_column("interviews", "microvm_id")
