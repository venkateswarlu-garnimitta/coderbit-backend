"""cleanup: drop logs/container columns, rename log_s3_key

Revision ID: 4ecd6f55119c
Revises: 09af7723ab2c
Create Date: 2026-07-10 10:14:02.528454

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '4ecd6f55119c'
down_revision: Union[str, Sequence[str], None] = '09af7723ab2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('interview_sessions', 'log_s3_key', new_column_name='logs_path')
    op.drop_column('interview_sessions', 'logs')
    op.drop_column('interviews', 'container_port')
    op.drop_column('interviews', 'container_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('interviews', sa.Column('container_id', sa.VARCHAR(), nullable=True))
    op.add_column('interviews', sa.Column('container_port', sa.INTEGER(), nullable=True))
    op.add_column('interview_sessions', sa.Column('logs', sqlite.JSON(), nullable=False))
    op.alter_column('interview_sessions', 'logs_path', new_column_name='log_s3_key')
