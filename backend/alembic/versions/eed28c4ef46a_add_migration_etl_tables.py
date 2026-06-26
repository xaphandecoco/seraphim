"""add_migration_etl_tables

ETL Migration pipeline: import_batch + import_row_result tables, plus
raw_payload/source columns on name_match_review_queue.

Creates tables in dependency order:
  (1) import_batch           — parent table, no inter-ETL dependencies
  (2) import_row_result      — child; FK import_batch.id ON DELETE CASCADE

Then extends name_match_review_queue (created in b1c2d3e4f5a6) with:
  - raw_payload  (JSONB nullable) — links phase stores batch row payload here
  - source       (String 20 nullable) — discriminator for T06 runner

All op.create_table / op.create_index / op.add_column calls are wrapped in
sa_inspect guards for idempotency, consistent with j4k5l6m7n8o9 pattern.

Revision ID: eed28c4ef46a
Revises: c1d2e3f4a5b6
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from alembic import op

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path).  Use with_variant so the column type is portable.
# postgresql.JSONB(astext_type=sa.Text()) enables the .astext operator used by
# T07 pending_review_count queries.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

revision: str = "eed28c4ef46a"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 1: import_batch (parent — must be created first)
    # 16 columns per spec.
    # ------------------------------------------------------------------
    if "import_batch" not in existing_tables:
        op.create_table(
            "import_batch",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_filename", sa.String(512), nullable=True),
            sa.Column("entity", sa.String(20), nullable=False),
            sa.Column("mode", sa.String(10), nullable=False),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default=sa.text("'running'"),
            ),
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
                "total_rows",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "created_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "updated_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "skipped_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "error_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "review_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    # Indexes on import_batch (guarded by existing index names)
    existing_ib_indexes = {
        idx["name"] for idx in insp.get_indexes("import_batch")
    } if "import_batch" in existing_tables else set()

    # Re-inspect after potential table creation above
    insp2 = sa_inspect(bind)
    if "import_batch" in insp2.get_table_names():
        existing_ib_indexes = {idx["name"] for idx in insp2.get_indexes("import_batch")}

    if "ix_import_batch_entity_status" not in existing_ib_indexes:
        op.create_index(
            "ix_import_batch_entity_status",
            "import_batch",
            ["entity", "status"],
        )

    if "ix_import_batch_started_at" not in existing_ib_indexes:
        op.create_index(
            "ix_import_batch_started_at",
            "import_batch",
            ["started_at"],
        )

    # ------------------------------------------------------------------
    # Step 2: import_row_result (child — FK import_batch.id CASCADE)
    # 9 columns per spec.
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    if "import_row_result" not in insp3.get_table_names():
        op.create_table(
            "import_row_result",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "batch_id",
                sa.Integer(),
                sa.ForeignKey("import_batch.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("external_id", sa.String(64), nullable=True),
            sa.Column("outcome", sa.String(20), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("raw", _JSONB, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    # Indexes on import_row_result
    insp4 = sa_inspect(bind)
    existing_irr_indexes = {
        idx["name"] for idx in insp4.get_indexes("import_row_result")
    } if "import_row_result" in insp4.get_table_names() else set()

    if "ix_import_row_result_batch" not in existing_irr_indexes:
        op.create_index(
            "ix_import_row_result_batch",
            "import_row_result",
            ["batch_id"],
        )

    if "ix_import_row_result_outcome" not in existing_irr_indexes:
        op.create_index(
            "ix_import_row_result_outcome",
            "import_row_result",
            ["batch_id", "outcome"],
        )

    # ------------------------------------------------------------------
    # Step 3: Extend name_match_review_queue with raw_payload + source.
    # Each column individually inspector-guarded for idempotency.
    # ------------------------------------------------------------------
    nmrq_cols = [c["name"] for c in insp4.get_columns("name_match_review_queue")]

    if "raw_payload" not in nmrq_cols:
        op.add_column(
            "name_match_review_queue",
            sa.Column("raw_payload", _JSONB, nullable=True),
        )

    if "source" not in nmrq_cols:
        op.add_column(
            "name_match_review_queue",
            sa.Column("source", sa.String(20), nullable=True),
        )

    # Index on source column
    existing_nmrq_indexes = {
        idx["name"] for idx in insp4.get_indexes("name_match_review_queue")
    }

    if "ix_nmrq_source" not in existing_nmrq_indexes:
        op.create_index(
            "ix_nmrq_source",
            "name_match_review_queue",
            ["source"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # ------------------------------------------------------------------
    # Step 3 reversal: Remove raw_payload, source, ix_nmrq_source from
    # name_match_review_queue (inspector-guarded).
    # ------------------------------------------------------------------
    existing_nmrq_indexes = {
        idx["name"] for idx in insp.get_indexes("name_match_review_queue")
    }
    if "ix_nmrq_source" in existing_nmrq_indexes:
        op.drop_index("ix_nmrq_source", table_name="name_match_review_queue")

    nmrq_cols = [c["name"] for c in insp.get_columns("name_match_review_queue")]
    if "source" in nmrq_cols:
        op.drop_column("name_match_review_queue", "source")
    if "raw_payload" in nmrq_cols:
        op.drop_column("name_match_review_queue", "raw_payload")

    # ------------------------------------------------------------------
    # Step 2 reversal: Drop import_row_result (child first).
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    if "import_row_result" in insp2.get_table_names():
        existing_irr_indexes = {
            idx["name"] for idx in insp2.get_indexes("import_row_result")
        }
        if "ix_import_row_result_outcome" in existing_irr_indexes:
            op.drop_index(
                "ix_import_row_result_outcome", table_name="import_row_result"
            )
        if "ix_import_row_result_batch" in existing_irr_indexes:
            op.drop_index(
                "ix_import_row_result_batch", table_name="import_row_result"
            )
        op.drop_table("import_row_result")

    # ------------------------------------------------------------------
    # Step 1 reversal: Drop import_batch (parent last).
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    if "import_batch" in insp3.get_table_names():
        existing_ib_indexes = {
            idx["name"] for idx in insp3.get_indexes("import_batch")
        }
        if "ix_import_batch_started_at" in existing_ib_indexes:
            op.drop_index(
                "ix_import_batch_started_at", table_name="import_batch"
            )
        if "ix_import_batch_entity_status" in existing_ib_indexes:
            op.drop_index(
                "ix_import_batch_entity_status", table_name="import_batch"
            )
        op.drop_table("import_batch")
