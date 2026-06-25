"""s22_name_matching_tables

S22 — Name Matching: NameAlias, CommunityReport, NameMatchReviewQueue tables.

Creates tables in CN-22 dependency order:
  (1) name_alias           — no inter-S22 dependencies
  (2) community_report     — no inter-S22 dependencies
  (3) name_match_review_queue — FKs community_report.id (must come after #2)

Note (CN-07): participants.source value 'community_report' needs no DDL —
it is already accommodated by the String(30) source column that exists on the
participants table as of the initial schema.

Revision ID: b1c2d3e4f5a6
Revises: a2b3c4d5e6f7
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
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

    # (1) name_alias — canonical alias → contact mapping
    op.create_table(
        "name_alias",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alias_text", sa.String(255), nullable=False),
        sa.Column("alias_type", sa.String(30), nullable=False, server_default="nick"),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(30), nullable=False, server_default="admin"),
        sa.Column("meta", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_name_alias_text",
        "name_alias",
        ["alias_text"],
        unique=True,
    )
    op.create_index("ix_name_alias_contact_id", "name_alias", ["contact_id"])
    op.create_index("ix_name_alias_alias_type", "name_alias", ["alias_type"])

    # (2) community_report — volunteer-submitted name-list entries
    # Note (CN-07): participants.source value 'community_report' needs no DDL;
    # the String(30) source column on participants already accommodates it.
    op.create_table(
        "community_report",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "submitted_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "parsed_names",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        # DateTime (not Date) — matches utc_now pattern; handler coerces date → datetime
        sa.Column("date_of_activity", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_community_report_event_id", "community_report", ["event_id"])
    op.create_index("ix_community_report_status", "community_report", ["status"])
    op.create_index(
        "ix_community_report_submitted_by_id",
        "community_report",
        ["submitted_by_id"],
    )

    # (3) name_match_review_queue — must come after community_report (FK dependency)
    op.create_table(
        "name_match_review_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "community_report_id",
            sa.Integer(),
            sa.ForeignKey("community_report.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_name", sa.String(255), nullable=False),
        sa.Column(
            "candidate_contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("score", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "resolved_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_nmrq_community_report_id",
        "name_match_review_queue",
        ["community_report_id"],
    )
    op.create_index("ix_nmrq_event_id", "name_match_review_queue", ["event_id"])
    op.create_index("ix_nmrq_contact_id", "name_match_review_queue", ["contact_id"])
    op.create_index("ix_nmrq_status", "name_match_review_queue", ["status"])
    op.create_index(
        "ix_nmrq_candidate_contact_id",
        "name_match_review_queue",
        ["candidate_contact_id"],
    )


def downgrade() -> None:
    # Drop in exact reverse order of creation (NMRQ depends on community_report)
    op.drop_index("ix_nmrq_candidate_contact_id", table_name="name_match_review_queue")
    op.drop_index("ix_nmrq_status", table_name="name_match_review_queue")
    op.drop_index("ix_nmrq_contact_id", table_name="name_match_review_queue")
    op.drop_index("ix_nmrq_event_id", table_name="name_match_review_queue")
    op.drop_index("ix_nmrq_community_report_id", table_name="name_match_review_queue")
    op.drop_table("name_match_review_queue")

    op.drop_index("ix_community_report_submitted_by_id", table_name="community_report")
    op.drop_index("ix_community_report_status", table_name="community_report")
    op.drop_index("ix_community_report_event_id", table_name="community_report")
    op.drop_table("community_report")

    op.drop_index("ix_name_alias_alias_type", table_name="name_alias")
    op.drop_index("ix_name_alias_contact_id", table_name="name_alias")
    op.drop_index("uq_name_alias_text", table_name="name_alias")
    op.drop_table("name_alias")
