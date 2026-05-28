"""add_detection_deleted_at

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add deleted_at to detections for tracking face image cleanup
    op.add_column(
        'detections',
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )

    # Create partial index for efficient cleanup queries
    op.create_index(
        'ix_detections_cleanup',
        'detections',
        ['is_enrolled', 'created_at'],
        postgresql_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_detections_cleanup', table_name='detections')
    op.drop_column('detections', 'deleted_at')
