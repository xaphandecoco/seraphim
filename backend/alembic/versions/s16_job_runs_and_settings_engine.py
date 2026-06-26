"""s16_job_runs_and_settings_engine

S16 — Scheduler Engine Foundation.

Creates the job_runs audit table, adds the label column to admin_settings,
and seeds two scheduler config keys (sunday_event_series_id,
powerhouse_event_series_id).

All operations are idempotency-guarded using the pattern established in
s24_fr_transition_bridge: inspect before each mutating step and skip if the
object already exists.

Revision ID: s16a1b2c3d4e5
Revises: a3b4c5d6e7f8
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from alembic import op

revision: str = "s16a1b2c3d4e5"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 1: Create job_runs table (guarded)
    # ------------------------------------------------------------------
    if "job_runs" not in tables:
        op.create_table(
            "job_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_name", sa.String(100), nullable=False),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="running",
            ),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
        )

    # Create indexes for job_runs (guarded by name)
    insp2 = sa_inspect(bind)
    if "job_runs" in insp2.get_table_names():
        existing_jr_indexes = {idx["name"] for idx in insp2.get_indexes("job_runs")}
        if "ix_job_runs_job_name" not in existing_jr_indexes:
            op.create_index("ix_job_runs_job_name", "job_runs", ["job_name"])
        if "ix_job_runs_started_at" not in existing_jr_indexes:
            op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])

    # ------------------------------------------------------------------
    # Step 2: Add label column to admin_settings (SQLite-safe, guarded)
    # ------------------------------------------------------------------
    as_cols = [c["name"] for c in insp.get_columns("admin_settings")]
    if "label" not in as_cols:
        with op.batch_alter_table("admin_settings") as b:
            b.add_column(sa.Column("label", sa.String(120), nullable=True))

    # ------------------------------------------------------------------
    # Step 3: Seed scheduler config keys in admin_settings (idempotent)
    # Each row is inserted only if the key does not already exist.
    # value is JSON null on both SQLite (plain JSON) and Postgres (JSONB).
    # ------------------------------------------------------------------
    _seed_rows = [
        ("sunday_event_series_id", "Sunday Service series"),
        ("powerhouse_event_series_id", "Powerhouse series"),
    ]

    for key, label_val in _seed_rows:
        existing = bind.execute(
            sa.text("SELECT 1 FROM admin_settings WHERE key = :key"),
            {"key": key},
        ).fetchone()
        if existing is None:
            if bind.dialect.name == "postgresql":
                bind.execute(
                    sa.text(
                        "INSERT INTO admin_settings"
                        " (key, value, category, requires_restart, sensitive,"
                        "  updated_at, label)"
                        " VALUES (:key, 'null'::jsonb, 'scheduler',"
                        "  false, false, CURRENT_TIMESTAMP, :lbl)"
                    ),
                    {"key": key, "lbl": label_val},
                )
            else:
                bind.execute(
                    sa.text(
                        "INSERT INTO admin_settings"
                        " (key, value, category, requires_restart, sensitive,"
                        "  updated_at, label)"
                        " VALUES (:key, 'null', 'scheduler',"
                        "  0, 0, CURRENT_TIMESTAMP, :lbl)"
                    ),
                    {"key": key, "lbl": label_val},
                )


def downgrade() -> None:
    """Remove job_runs table and admin_settings.label column.

    Seeded admin_settings rows are intentionally NOT deleted — removing
    scheduler config keys could break running services that depend on them.
    """
    bind = op.get_bind()
    insp = sa_inspect(bind)
    tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 1 reversal: Drop job_runs indexes then the table
    # ------------------------------------------------------------------
    if "job_runs" in tables:
        existing_jr_indexes = {idx["name"] for idx in insp.get_indexes("job_runs")}
        if "ix_job_runs_started_at" in existing_jr_indexes:
            op.drop_index("ix_job_runs_started_at", table_name="job_runs")
        if "ix_job_runs_job_name" in existing_jr_indexes:
            op.drop_index("ix_job_runs_job_name", table_name="job_runs")
        op.drop_table("job_runs")

    # ------------------------------------------------------------------
    # Step 2 reversal: Drop admin_settings.label (SQLite-safe, guarded)
    # ------------------------------------------------------------------
    as_cols = [c["name"] for c in insp.get_columns("admin_settings")]
    if "label" in as_cols:
        with op.batch_alter_table("admin_settings") as b:
            b.drop_column("label")
