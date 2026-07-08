"""merge microvm and recording_path heads

Revision ID: 47f05fe52f5d
Revises: 32fb988d773c, b1c2d3e4f5a6
Create Date: 2026-07-07 12:18:04.940998

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47f05fe52f5d'
down_revision: Union[str, Sequence[str], None] = ('32fb988d773c', 'b1c2d3e4f5a6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
