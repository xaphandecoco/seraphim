"""add_dead_letter_queue_fields

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add push_attempts: integer, not null, default 0.
    # server_default ensures existing rows get 0 without a full table rewrite.
    op.add_column(
        'attendance',
        sa.Column(
            'push_attempts',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )

    # Add last_push_error: nullable text to store the last exception message.
    op.add_column(
        'attendance',
        sa.Column('last_push_error', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('attendance', 'last_push_error')
    op.drop_column('attendance', 'push_attempts')
