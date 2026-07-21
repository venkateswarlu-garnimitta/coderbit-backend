"""dynamic_metrics

Revision ID: 8f4e2c1a9b3d
Revises: a1b2c3d4e5f6
Create Date: 2026-06-29 00:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f4e2c1a9b3d'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_CREATED_AT = datetime.now(timezone.utc)


_DEFAULT_METRICS = [
    {
        "name": "Prompt Quality",
        "key": "prompt_quality",
        "rubric": "Were prompts specific, iterative, and well-scoped? Low: vague one-shot dumps. High: precise, context-rich, refined over time.",
    },
    {
        "name": "Problem Decomposition",
        "key": "problem_decomposition",
        "rubric": "Did they break the problem down before prompting? Low: asked AI to solve everything at once. High: tackled sub-problems independently.",
    },
    {
        "name": "Critical Evaluation",
        "key": "critical_evaluation",
        "rubric": "Did they review and modify AI suggestions or accept blindly? Low: accepted every suggestion unchanged. High: edited, questioned, or rejected bad suggestions.",
    },
    {
        "name": "Independent Coding",
        "key": "independent_coding",
        "rubric": "How much code did they write themselves vs paste from AI? Low: entire solution is AI-generated. High: AI used for guidance, candidate wrote most code.",
    },
    {
        "name": "Debugging Ability",
        "key": "debugging_ability",
        "rubric": "How did they handle errors and terminal failures? Low: immediately asked AI to fix every error. High: read errors, attempted fixes first.",
    },
    {
        "name": "Time Management",
        "key": "time_management",
        "rubric": "Did they plan, start early, and test incrementally? Low: chaotic, no testing until the end. High: structured approach with early testing.",
    },
]


_METRIC_KEYS = [
    "prompt_quality",
    "problem_decomposition",
    "critical_evaluation",
    "independent_coding",
    "debugging_ability",
    "time_management",
]


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create metrics table
    op.create_table(
        'metrics',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('rubric', sa.Text(), nullable=False),
        sa.Column('is_custom', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
        sa.UniqueConstraint('name'),
    )
    op.create_index(op.f('ix_metrics_key'), 'metrics', ['key'], unique=True)

    # 2. Seed default metrics
    metrics_table = sa.table(
        'metrics',
        sa.column('id', sa.String()),
        sa.column('key', sa.String()),
        sa.column('name', sa.String()),
        sa.column('rubric', sa.Text()),
        sa.column('is_custom', sa.Boolean()),
        sa.column('created_at', sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        metrics_table,
        [
            {
                "id": str(uuid4()),
                "key": m["key"],
                "name": m["name"],
                "rubric": m["rubric"],
                "is_custom": False,
                "created_at": _DEFAULT_CREATED_AT,
            }
            for m in _DEFAULT_METRICS
        ],
    )

    # 3. Add metric_ids to problems
    op.add_column(
        'problems',
        sa.Column('metric_ids', sa.JSON(), nullable=True)
    )
    op.execute("UPDATE problems SET metric_ids = '[]'")
    with op.batch_alter_table('problems', schema=None) as batch_op:
        batch_op.alter_column('metric_ids', nullable=False)

    # 4. Add scores JSON column to scores
    op.add_column(
        'scores',
        sa.Column('scores', sa.JSON(), nullable=True)
    )

    # 5. Migrate existing fixed-column scores into the new JSON column
    if _is_sqlite():
        op.execute(f"""
            UPDATE scores
            SET scores = json_object(
                {', '.join(f"'{key}', {key}" for key in _METRIC_KEYS)}
            )
        """)
    else:
        # PostgreSQL path
        op.execute(f"""
            UPDATE scores
            SET scores = json_build_object(
                {', '.join(f"'{key}', {key}" for key in _METRIC_KEYS)}
            )
        """)

    # Ensure non-null constraint on scores JSON
    with op.batch_alter_table('scores', schema=None) as batch_op:
        batch_op.alter_column('scores', nullable=False)

    # 6. Drop the six fixed metric columns
    with op.batch_alter_table('scores', schema=None) as batch_op:
        for key in _METRIC_KEYS:
            batch_op.drop_column(key)


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Re-add fixed metric columns
    with op.batch_alter_table('scores', schema=None) as batch_op:
        for key in _METRIC_KEYS:
            batch_op.add_column(sa.Column(key, sa.Float(), nullable=True))

    # 2. Migrate JSON scores back to fixed columns (best effort)
    if _is_sqlite():
        set_clause = ",\n            ".join(
            f"{key} = COALESCE(json_extract(scores, '$.{key}'), 0)"
            for key in _METRIC_KEYS
        )
    else:
        set_clause = ",\n            ".join(
            f"{key} = COALESCE((scores ->> '{key}')::float, 0)"
            for key in _METRIC_KEYS
        )

    op.execute(f"""
        UPDATE scores
        SET
            {set_clause}
    """)

    with op.batch_alter_table('scores', schema=None) as batch_op:
        for key in _METRIC_KEYS:
            batch_op.alter_column(key, nullable=False)

    # 3. Drop JSON scores column and problem metric_ids
    with op.batch_alter_table('scores', schema=None) as batch_op:
        batch_op.drop_column('scores')

    op.drop_column('problems', 'metric_ids')

    # 4. Drop metrics table
    op.drop_index(op.f('ix_metrics_key'), table_name='metrics')
    op.drop_table('metrics')
