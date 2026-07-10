"""add_score_type_drop_unique_interview_id

Revision ID: 09af7723ab2c
Revises: b384d9f80a27
Create Date: 2026-07-09 16:28:14.064362

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '09af7723ab2c'
down_revision: Union[str, Sequence[str], None] = 'ab1cdef2a345'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite: use recreate='always' to rebuild the table, dropping the unnamed
    # UNIQUE(interview_id) constraint and adding score_type in one pass.
    with op.batch_alter_table('scores', schema=None, recreate='always') as batch_op:
        batch_op.add_column(sa.Column('score_type', sa.String(), nullable=True))

    # Backfill existing rows with a default type
    op.execute("UPDATE scores SET score_type = 'output'")

    # Make score_type non-nullable and add composite unique constraint
    with op.batch_alter_table('scores', schema=None, recreate='always') as batch_op:
        batch_op.alter_column('score_type', existing_type=sa.String(), nullable=False)
        batch_op.create_unique_constraint('uq_scores_interview_id_score_type', ['interview_id', 'score_type'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('scores', schema=None) as batch_op:
        batch_op.drop_constraint('uq_scores_interview_id_score_type', type_='unique')
        batch_op.create_unique_constraint('uq_scores_interview_id', ['interview_id'])
        batch_op.alter_column('score_type', existing_type=sa.String(), nullable=True)
        batch_op.drop_column('score_type')
