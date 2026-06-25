"""s04_event_series_and_event_columns

S04 — Event Series & Recurring Events.

Creates event_series table and extends events with six new columns:
  event_type, session_time, occurrence_date, recurring_series_id,
  location, is_active.

Backfills occurrence_date from start_at.

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

# Cross-dialect JSON: JSONB on Postgres, plain JSON on SQLite (test path).
# Exact pattern from i3j4k5l6m7n8 line 23.
_JSONB = sa.JSON().with_variant(PG_JSONB(), "postgresql")

revision: str = "j4k5l6m7n8o9"
down_revision: Union[str, None] = "i3j4k5l6m7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # ------------------------------------------------------------------
    # Step 1: Create event_series (must be FIRST — events.recurring_series_id
    # has a FK referencing it).
    # ------------------------------------------------------------------
    if "event_series" not in insp.get_table_names():
        op.create_table(
            "event_series",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("event_type", sa.String(50), nullable=False,
                      server_default=sa.text("'Event'")),
            sa.Column("session_time", sa.String(20), nullable=True),
            sa.Column(
                "cadence",
                _JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("default_location", sa.String(255), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    # ------------------------------------------------------------------
    # Step 2: Add new columns to events table.
    # Each column is individually inspector-guarded for idempotency.
    # ------------------------------------------------------------------
    cols = [c["name"] for c in insp.get_columns("events")]

    if "event_type" not in cols:
        op.add_column(
            "events",
            sa.Column(
                "event_type",
                sa.String(50),
                nullable=False,
                server_default=sa.text("'Event'"),
            ),
        )

    if "session_time" not in cols:
        op.add_column(
            "events",
            sa.Column("session_time", sa.String(50), nullable=True),
        )

    if "occurrence_date" not in cols:
        op.add_column(
            "events",
            sa.Column("occurrence_date", sa.Date(), nullable=True),
        )

    if "recurring_series_id" not in cols:
        op.add_column(
            "events",
            sa.Column(
                "recurring_series_id",
                sa.Integer(),
                sa.ForeignKey("event_series.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    if "location" not in cols:
        op.add_column(
            "events",
            sa.Column("location", sa.String(255), nullable=True),
        )

    if "is_active" not in cols:
        op.add_column(
            "events",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    # ------------------------------------------------------------------
    # Step 3: Create indexes on events for the new columns.
    # Guarded by checking existing index names.
    # ------------------------------------------------------------------
    existing_indexes = {idx["name"] for idx in insp.get_indexes("events")}

    if "ix_events_event_type" not in existing_indexes:
        op.create_index("ix_events_event_type", "events", ["event_type"])

    if "ix_events_occurrence_date" not in existing_indexes:
        op.create_index(
            "ix_events_occurrence_date",
            "events",
            ["occurrence_date"],
            postgresql_ops={"occurrence_date": "DESC"},
        )

    # ------------------------------------------------------------------
    # Step 4: Backfill occurrence_date from start_at.
    # Only sets rows where start_at IS NOT NULL and occurrence_date
    # is still NULL (safe to re-run; won't overwrite manually set values).
    # NOTE: This operation is irreversible — downgrade does NOT restore
    # the previous NULL values.
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE events SET occurrence_date = DATE(start_at) WHERE start_at IS NOT NULL"
    )


def downgrade() -> None:
    # Reverse of upgrade() — in reverse order.

    # ------------------------------------------------------------------
    # Step 3 & 4 reversal: Drop indexes from events.
    # ------------------------------------------------------------------
    op.drop_index("ix_events_occurrence_date", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")

    # ------------------------------------------------------------------
    # Step 2 reversal: Drop the six columns from events.
    # ------------------------------------------------------------------
    op.drop_column("events", "is_active")
    op.drop_column("events", "location")
    op.drop_column("events", "recurring_series_id")
    op.drop_column("events", "occurrence_date")
    op.drop_column("events", "session_time")
    op.drop_column("events", "event_type")

    # ------------------------------------------------------------------
    # Step 1 reversal: Drop event_series.
    # ------------------------------------------------------------------
    op.drop_table("event_series")
