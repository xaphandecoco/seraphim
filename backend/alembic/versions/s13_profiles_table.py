"""s13_profiles_table

S13 — Profiles & Public Newcomer Form: create profiles table and seed three
preset profiles (New Friend, Community Member, Volunteer).

Revision ID: s13a1b2c3d4e5
Revises: s12a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

import datetime
import json
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "s13a1b2c3d4e5"
down_revision: Union[str, None] = "s12a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Cross-dialect JSON: JSONB on Postgres, plain JSON on SQLite (test path).
# Must NOT use bare postgresql.JSONB here — that breaks SQLite tests.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

# ---------------------------------------------------------------------------
# Preset seed data (fields=[] per spec §6 safe approach — owner configures
# fields via admin UI after both S02 and S13 are deployed).
# ---------------------------------------------------------------------------

_NEW_FRIEND_SETTINGS = {
    "contact_subtype_default": "New Friend",
    "submit_label": "Register as New Friend",
    "success_message": "Thank you! Your information has been recorded.",
    "notify_google_chat": True,
    "notify_gmail": True,
    "prayer_request_field": "prayer_request",
    "invited_by_field": "invited_by",
    "consolidated_by_field": "consolidated_by",
}

_COMMUNITY_MEMBER_SETTINGS = {
    "contact_subtype_default": "Community Member",
    "submit_label": "Submit",
    "success_message": "Thank you!",
}

_VOLUNTEER_SETTINGS = {
    "contact_subtype_default": "Volunteer",
    "submit_label": "Submit",
    "success_message": "Thank you!",
}


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create profiles table
    # ------------------------------------------------------------------
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "entity", sa.String(30), nullable=False, server_default="contact"
        ),
        sa.Column("fields", _JSONB, nullable=False, server_default="[]"),
        sa.Column("settings", _JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "is_public", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Unique index on name (also enforces the DB-level unique constraint)
    op.create_index("uq_profiles_name", "profiles", ["name"], unique=True)

    # ------------------------------------------------------------------
    # 2. Seed three preset profiles (idempotent: skip existing names)
    # ------------------------------------------------------------------
    conn = op.get_bind()
    existing_rows = conn.execute(
        sa.text("SELECT name FROM profiles")
    ).fetchall()
    existing_names = {r[0] for r in existing_rows}

    # Capture a single timestamp for all seed rows
    now = datetime.datetime.utcnow()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")

    _seeds = [
        {
            "name": "New Friend",
            "entity": "contact",
            "fields": [],
            "settings": _NEW_FRIEND_SETTINGS,
            "is_public": True,
        },
        {
            "name": "Community Member",
            "entity": "contact",
            "fields": [],
            "settings": _COMMUNITY_MEMBER_SETTINGS,
            "is_public": False,
        },
        {
            "name": "Volunteer",
            "entity": "contact",
            "fields": [],
            "settings": _VOLUNTEER_SETTINGS,
            "is_public": False,
        },
    ]

    for seed in _seeds:
        if seed["name"] in existing_names:
            continue
        # Use explicit parameterised text INSERT with json.dumps() for JSON
        # columns so the values serialise correctly on both SQLite and
        # PostgreSQL regardless of how op.bulk_insert handles JSONB variants.
        conn.execute(
            sa.text(
                "INSERT INTO profiles "
                "(name, entity, fields, settings, is_public, created_at, updated_at) "
                "VALUES (:name, :entity, :fields, :settings, :is_public, "
                ":created_at, :updated_at)"
            ),
            {
                "name": seed["name"],
                "entity": seed["entity"],
                "fields": json.dumps(seed["fields"]),
                "settings": json.dumps(seed["settings"]),
                # bool subclasses int; sqlite3 stores True→1, False→0.
                # PostgreSQL's DBAPI also accepts Python bool in text queries.
                "is_public": seed["is_public"],
                "created_at": now_str,
                "updated_at": now_str,
            },
        )


def downgrade() -> None:
    # Drop index before table (required by some backends)
    op.drop_index("uq_profiles_name", table_name="profiles")
    op.drop_table("profiles")
    # outbox is NOT dropped here — it is shared infrastructure (S12/S17).
