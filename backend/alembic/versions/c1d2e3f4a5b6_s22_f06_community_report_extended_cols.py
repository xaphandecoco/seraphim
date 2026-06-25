"""s22_f06_community_report_extended_cols

S22-F06 — Community Report router: add extended columns to community_report
table for the full F06 feature set (zone, topics, prayer_items, remarks,
attendee_names, event_leader_name, event_leader_contact_id, photo_paths,
match_status, matched_count, review_count, event_title,
submitted_by_contact_id).

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type(bind: sa.engine.Connection) -> sa.types.TypeEngine:
    """Return JSONB on Postgres, plain JSON on SQLite (test path)."""
    if bind.dialect.name == "postgresql":
        return PG_JSONB()
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    json_type = _json_type(bind)

    # Free-text event title fallback (when event_id is not supplied)
    op.add_column(
        "community_report",
        sa.Column("event_title", sa.String(255), nullable=True),
    )

    # Auto-resolved submitter contact FK
    op.add_column(
        "community_report",
        sa.Column(
            "submitted_by_contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Zone / ministry area
    op.add_column(
        "community_report",
        sa.Column("zone", sa.String(100), nullable=True),
    )

    # Free-text content columns
    op.add_column(
        "community_report",
        sa.Column("topics", sa.Text(), nullable=True),
    )
    op.add_column(
        "community_report",
        sa.Column("prayer_items", sa.Text(), nullable=True),
    )
    op.add_column(
        "community_report",
        sa.Column("remarks", sa.Text(), nullable=True),
    )
    op.add_column(
        "community_report",
        sa.Column("event_leader_name", sa.String(255), nullable=True),
    )

    # FK to the matched leader contact
    op.add_column(
        "community_report",
        sa.Column(
            "event_leader_contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # JSONB list columns — default to empty array
    op.add_column(
        "community_report",
        sa.Column(
            "attendee_names",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "community_report",
        sa.Column(
            "photo_paths",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    # Name-matching progress tracking
    op.add_column(
        "community_report",
        sa.Column(
            "match_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "community_report",
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "community_report",
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # Indexes
    op.create_index("ix_community_report_match_status", "community_report", ["match_status"])
    op.create_index("ix_community_report_zone", "community_report", ["zone"])


def downgrade() -> None:
    op.drop_index("ix_community_report_zone", table_name="community_report")
    op.drop_index("ix_community_report_match_status", table_name="community_report")

    op.drop_column("community_report", "review_count")
    op.drop_column("community_report", "matched_count")
    op.drop_column("community_report", "match_status")
    op.drop_column("community_report", "photo_paths")
    op.drop_column("community_report", "attendee_names")
    op.drop_column("community_report", "event_leader_contact_id")
    op.drop_column("community_report", "event_leader_name")
    op.drop_column("community_report", "remarks")
    op.drop_column("community_report", "prayer_items")
    op.drop_column("community_report", "topics")
    op.drop_column("community_report", "zone")
    op.drop_column("community_report", "submitted_by_contact_id")
    op.drop_column("community_report", "event_title")
