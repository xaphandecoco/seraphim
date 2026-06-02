"""add_performance_indexes

Adds indexes on the hot, continuously-growing tables (detections, tasks,
attendance, logs) that are filtered by status/date/push_status in normal operation.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_detections_status", "detections", ["status"])
    op.create_index("ix_detections_created_at", "detections", ["created_at"])
    op.create_index("ix_detections_enrolled_created", "detections", ["is_enrolled", "created_at"])
    op.create_index("ix_detections_compreface_subject", "detections", ["compreface_subject_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_attendance_push_status", "attendance", ["push_status"])
    op.create_index("ix_attendance_event_id", "attendance", ["event_id"])
    op.create_index("ix_logs_timestamp", "logs", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_logs_timestamp", table_name="logs")
    op.drop_index("ix_attendance_event_id", table_name="attendance")
    op.drop_index("ix_attendance_push_status", table_name="attendance")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_detections_compreface_subject", table_name="detections")
    op.drop_index("ix_detections_enrolled_created", table_name="detections")
    op.drop_index("ix_detections_created_at", table_name="detections")
    op.drop_index("ix_detections_status", table_name="detections")
