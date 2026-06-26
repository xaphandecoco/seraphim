"""s10_import_wizard

S10 — CSV/XLSX Import Wizard.

Creates import_mapping_preset table and extends import_batch with
staging_file + expires_at columns. Widens import_batch.mode to VARCHAR(20)
on PostgreSQL only (SQLite is typeless — advisory column sizes are not
enforced, so the alter is skipped there).

All operations are idempotency-guarded via sa.inspect() consistent with
s09/s08/s24 migration patterns.

Revision ID: s10a1b2c3d4e5
Revises: s09a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from alembic import op

logger = logging.getLogger("alembic.s10_import_wizard")

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path). Consistent with s09/s08 _JSONB convention.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

revision: str = "s10a1b2c3d4e5"
down_revision: Union[str, None] = "s09a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 1: import_mapping_preset (new table)
    # ------------------------------------------------------------------
    if "import_mapping_preset" not in existing_tables:
        op.create_table(
            "import_mapping_preset",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("entity", sa.String(20), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column(
                "column_map",
                _JSONB,
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "options",
                _JSONB,
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "is_shared",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "entity", "name", name="uq_import_mapping_preset_entity_name"
            ),
        )

    # Index on owner_id (guarded; re-inspect after potential table creation)
    insp2 = sa_inspect(bind)
    if "import_mapping_preset" in insp2.get_table_names():
        existing_imp_indexes = {
            idx["name"] for idx in insp2.get_indexes("import_mapping_preset")
        }
        if "ix_import_mapping_preset_owner_id" not in existing_imp_indexes:
            op.create_index(
                "ix_import_mapping_preset_owner_id",
                "import_mapping_preset",
                ["owner_id"],
            )

    # ------------------------------------------------------------------
    # Step 2: Extend import_batch with staging_file + expires_at
    # Each column individually inspector-guarded for idempotency.
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    if "import_batch" in insp3.get_table_names():
        ib_cols = [c["name"] for c in insp3.get_columns("import_batch")]

        if "staging_file" not in ib_cols:
            op.add_column(
                "import_batch",
                sa.Column("staging_file", sa.Text(), nullable=True),
            )

        if "expires_at" not in ib_cols:
            op.add_column(
                "import_batch",
                sa.Column("expires_at", sa.DateTime(), nullable=True),
            )

    # ------------------------------------------------------------------
    # Step 3: Widen import_batch.mode VARCHAR(10) → VARCHAR(20) on PG only.
    # SQLite has no enforced column widths — altering type is a no-op and
    # may error on older drivers; skip entirely for that dialect.
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "import_batch",
            "mode",
            type_=sa.String(20),
            existing_type=sa.String(10),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # ------------------------------------------------------------------
    # Step 3 reversal: No narrowing of mode width — data may exist that
    # would be silently truncated; leave mode VARCHAR as-is on downgrade.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Step 2 reversal: Remove staging_file + expires_at from import_batch.
    # Drop in reverse-add order (expires_at first, then staging_file).
    # ------------------------------------------------------------------
    if "import_batch" in insp.get_table_names():
        ib_cols = [c["name"] for c in insp.get_columns("import_batch")]

        if "expires_at" in ib_cols:
            op.drop_column("import_batch", "expires_at")

        if "staging_file" in ib_cols:
            op.drop_column("import_batch", "staging_file")

    # ------------------------------------------------------------------
    # Step 1 reversal: Drop import_mapping_preset (index first, table last).
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    if "import_mapping_preset" in insp2.get_table_names():
        existing_imp_indexes = {
            idx["name"] for idx in insp2.get_indexes("import_mapping_preset")
        }
        if "ix_import_mapping_preset_owner_id" in existing_imp_indexes:
            op.drop_index(
                "ix_import_mapping_preset_owner_id",
                table_name="import_mapping_preset",
            )
        op.drop_table("import_mapping_preset")
