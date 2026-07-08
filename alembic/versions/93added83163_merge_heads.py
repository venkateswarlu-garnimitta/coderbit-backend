"""merge heads

Revision ID: 93added83163
Revises: 0c79e1b2a60b, 0d829aefd2de
Create Date: 2026-07-08 12:18:36.178582

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93added83163'
down_revision: Union[str, Sequence[str], None] = ('0c79e1b2a60b', '0d829aefd2de')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
