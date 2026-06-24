"""add_custom_field_engine

S02 — Dynamic Custom-Field Engine.

Creates custom_field_group and custom_field_def tables with all canonical
columns, unique constraints, and indexes.  The upgrade also runs the
idempotent church seed so six field groups exist immediately after apply.

Inspector guards for contacts.custom_data and audit_log are kept for
merge-order safety (S01 already owns both; both guards are confirmed no-ops
when migrations are applied in the documented order S01→S02).

Asymmetric downgrade notice
----------------------------
``downgrade()`` drops custom_field_def THEN custom_field_group ONLY.
It does NOT drop contacts.custom_data or audit_log — those are owned by S01
(MASTER C7 and C3 respectively).  If rollback occurs after contacts have
had custom_data written (post-S03), the JSONB column is inert until the
tables are re-created by a subsequent upgrade.

Revision ID: h1i2j3k4l5m6
Revises: g7h8i9j0k1l2
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

from app.seeds.custom_fields_seed import seed_church_custom_fields

# Cross-dialect JSON: JSONB on Postgres, plain JSON on SQLite (test path).
# Exact pattern from g7h8i9j0k1l2 line 25.
_JSONB = sa.JSON().with_variant(PG_JSONB(), "postgresql")

revision: str = "h1i2j3k4l5m6"
down_revision: Union[str, None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Create custom_field_group
    # Unique constraint: (entity, name)
    # Index: (entity, is_active, weight) — schema-assembly hot path
    # ------------------------------------------------------------------
    op.create_table(
        "custom_field_group",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column(
            "entity",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'contact'"),
        ),
        sa.Column("weight", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("entity", "name", name="uq_custom_field_group_entity_name"),
    )
    op.create_index(
        "ix_cfg_entity_active_weight",
        "custom_field_group",
        ["entity", "is_active", "weight"],
    )

    # ------------------------------------------------------------------
    # Step 2: Create custom_field_def
    # FK to custom_field_group.id with ON DELETE CASCADE.
    # Unique constraint: (group_id, name)
    # Index: (group_id, is_active, weight)
    # ------------------------------------------------------------------
    op.create_table(
        "custom_field_def",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("custom_field_group.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=False),
        sa.Column(
            "options",
            _JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_multi",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("weight", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("help_text", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("group_id", "name", name="uq_custom_field_def_group_name"),
    )
    op.create_index(
        "ix_cfd_group_active_weight",
        "custom_field_def",
        ["group_id", "is_active", "weight"],
    )

    # ------------------------------------------------------------------
    # Step 3: Inspector-guarded no-op add of contacts.custom_data
    # S01 (g7h8i9j0k1l2) already added this column; this guard exists
    # solely for merge-order safety and is a confirmed no-op.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns("contacts")]
    if "custom_data" not in cols:
        op.add_column(
            "contacts",
            sa.Column(
                "custom_data",
                _JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )

    # ------------------------------------------------------------------
    # Step 4: Inspector-guarded no-op create of audit_log
    # S01 (g7h8i9j0k1l2) already created this table; this guard exists
    # solely for merge-order safety and is a confirmed no-op.
    # ------------------------------------------------------------------
    existing_tables = insp.get_table_names()
    if "audit_log" not in existing_tables:
        op.create_table(
            "audit_log",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "actor_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("entity", sa.String(100), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("before", _JSONB, nullable=True),
            sa.Column("after", _JSONB, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_audit_log_entity_entity_id", "audit_log", ["entity", "entity_id"]
        )
        op.create_index(
            "ix_audit_log_created_at", "audit_log", ["created_at"]
        )

    # ------------------------------------------------------------------
    # Step 5: Seed the six church field groups (idempotent).
    # Must be the LAST upgrade step so both tables exist.
    # ------------------------------------------------------------------
    seed_church_custom_fields(bind)


def downgrade() -> None:
    """Remove the S02-owned tables only.

    IMPORTANT — asymmetric downgrade:
      - contacts.custom_data is NOT dropped (owned by S01 / MASTER C7).
      - audit_log is NOT dropped (owned by S01 / MASTER C3).
    Dropping those in S02's downgrade would corrupt S01's schema invariants.
    """
    # Drop child table first (FK constraint; CASCADE removes rows).
    op.drop_index("ix_cfd_group_active_weight", table_name="custom_field_def")
    op.drop_table("custom_field_def")

    # Then drop parent table.
    op.drop_index("ix_cfg_entity_active_weight", table_name="custom_field_group")
    op.drop_table("custom_field_group")
