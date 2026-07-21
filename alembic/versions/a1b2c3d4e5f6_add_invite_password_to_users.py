"""add invite_password to users

Revision ID: a1b2c3d4e5f6
Revises: 0329cade6a93
Create Date: 2026-06-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0329cade6a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("invite_password", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "invite_password")
