"""s05_export_jobs

S05 — Async Export Jobs.

Creates export_jobs table with all 13 columns per spec §3.

Revision ID: a2b3c4d5e6f7
Revises: j4k5l6m7n8o9
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Dialect-branched JSONB: JSONB on Postgres, plain JSON on SQLite (test path).
    if bind.dialect.name == "postgresql":
        json_type = PG_JSONB()
    else:
        json_type = sa.JSON()

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("fmt", sa.String(10), nullable=False),
        sa.Column(
            "params",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "requested_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )

    op.create_index(
        "ix_export_jobs_status_created",
        "export_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_export_jobs_requested_by",
        "export_jobs",
        ["requested_by_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_export_jobs_requested_by", table_name="export_jobs")
    op.drop_index("ix_export_jobs_status_created", table_name="export_jobs")
    op.drop_table("export_jobs")
