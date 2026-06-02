"""add_task_action_approval_unique

Adds a partial unique index enforcing that a volunteer may take at most ONE
approval action (confirm/edit/add) on a given task. Skips are deliberately
excluded from the predicate so a volunteer can still skip the same task more
than once. This closes the dual-approval concurrency hole where the "already
acted" pre-check could be raced by a concurrent duplicate submit.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_task_action_approval",
        "task_actions",
        ["task_id", "volunteer_id"],
        unique=True,
        postgresql_where=sa.text("action IN ('confirm', 'edit', 'add')"),
    )


def downgrade() -> None:
    op.drop_index("uq_task_action_approval", table_name="task_actions")
