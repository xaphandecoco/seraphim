"""s11_dedupe_rule_set

S11 — Find & Merge Duplicates: Dedupe Rule Set table.

Creates dedupe_rule_set table and seeds one Default rule set.

NOTE: This migration touches ONLY dedupe_rule_set.
      It does NOT touch audit_log (already exists, owned by S01/S02).

All operations are idempotency-guarded via sa.inspect() consistent with
s10_import_wizard.py / eed28c4ef46a migration patterns.

Revision ID: s11a1b2c3d4e5
Revises: s10a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

import json
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from alembic import op

logger = logging.getLogger("alembic.s11_dedupe_rule_set")

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path). Consistent with s10/s09/s08 _JSONB convention.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

revision: str = "s11a1b2c3d4e5"
down_revision: Union[str, None] = "s10a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default rules seeded on first upgrade
_DEFAULT_RULES = [
    {"field": "email",      "weight": 100},
    {"field": "first_name", "weight": 30},
    {"field": "last_name",  "weight": 40},
    {"field": "phone",      "weight": 50},
    {"field": "birth_date", "weight": 40},
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 1: Create dedupe_rule_set table (guarded)
    # ------------------------------------------------------------------
    if "dedupe_rule_set" not in existing_tables:
        op.create_table(
            "dedupe_rule_set",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(120), nullable=False),
            # Dialect-safe boolean server defaults:
            #   sa.false() → '0' on SQLite, 'false' on PostgreSQL
            #   sa.true()  → '1' on SQLite, 'true'  on PostgreSQL
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "threshold",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("70"),
            ),
            sa.Column(
                "rules",
                _JSONB,
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("name", name="uq_dedupe_rule_set_name"),
        )
        logger.info("Created table dedupe_rule_set")

    # ------------------------------------------------------------------
    # Step 2: Seed Default row (idempotent — only if no row named 'Default')
    # Uses a SELECT-exists guard so re-running upgrade() is safe.
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    if "dedupe_rule_set" in insp2.get_table_names():
        result = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM dedupe_rule_set WHERE name = 'Default'"
            )
        )
        count = result.scalar()
        if not count:
            rules_json = json.dumps(_DEFAULT_RULES)
            bind.execute(
                sa.text(
                    "INSERT INTO dedupe_rule_set "
                    "(name, is_default, is_active, threshold, rules, created_at, updated_at) "
                    "VALUES (:name, :is_default, :is_active, :threshold, :rules, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "name": "Default",
                    "is_default": True,
                    "is_active": True,
                    "threshold": 70,
                    "rules": rules_json,
                },
            )
            logger.info("Seeded Default dedupe_rule_set row")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # ------------------------------------------------------------------
    # Drop dedupe_rule_set (guarded). Data loss is intentional on downgrade.
    # ------------------------------------------------------------------
    if "dedupe_rule_set" in insp.get_table_names():
        op.drop_table("dedupe_rule_set")
        logger.info("Dropped table dedupe_rule_set")
