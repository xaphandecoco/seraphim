"""s08_biometric_consent_rtbf

S08 — Biometric Consent & Right to Be Forgotten (RTBF).

Extends the biometric_consent table (created by S24) with RTBF lifecycle
columns: retention tracking, deletion request workflow, purge audit trail,
and a consent linkage on compreface_subjects for cascade-purge support.
Seeds the biometric_retention_years admin setting (7 years) for UI visibility.

All operations are idempotency-guarded using sa.inspect() checks consistent
with the pattern established in s24_fr_transition_bridge and
s16_job_runs_and_settings_engine.

SQLite note: op.add_column with inline ForeignKey is not supported on SQLite.
Column additions to biometric_consent are therefore done inside a single
batch_alter_table(recreate="always") block so the FK columns are embedded in
the recreated table's CREATE TABLE statement (the only FK approach SQLite
supports).  The batch is skipped entirely when all columns already exist to
avoid disrupting any separately-created indexes on the idempotency path.

Revision ID: s08a1b2c3d4e5
Revises: s16a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from alembic import op

logger = logging.getLogger("alembic.s08_biometric_consent_rtbf")

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path).  Consistent with s24_fr_transition_bridge._JSONB.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

revision: str = "s08a1b2c3d4e5"
down_revision: Union[str, None] = "s16a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Ordered list of the 7 new S08 columns — used for both upgrade guards and
# downgrade drops to keep the two paths in sync.
_S08_BC_COLS = [
    "recorded_by_id",
    "retention_until",
    "deletion_requested_at",
    "deletion_requested_by_id",
    "purged_at",
    "purge_detail",
    "updated_at",
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # Dialect-branched server default for now() — mirrors S24's recorded_at
    # default: sa.text("(datetime('now'))") on SQLite, sa.text("now()") on PG.
    if bind.dialect.name == "postgresql":
        now_default = sa.text("now()")
    else:
        now_default = sa.text("(datetime('now'))")

    # ------------------------------------------------------------------
    # Step 1: Add RTBF lifecycle columns to biometric_consent.
    #
    # Uses batch_alter_table(recreate="always") to handle FK columns on
    # SQLite (plain op.add_column does not support inline ForeignKey on
    # SQLite).  The batch is skipped entirely when all columns already
    # exist (idempotency path) to preserve any pre-existing separate indexes.
    # ------------------------------------------------------------------
    bc_cols = {c["name"] for c in insp.get_columns("biometric_consent")}
    _missing = {n for n in _S08_BC_COLS if n not in bc_cols}

    if _missing:
        with op.batch_alter_table("biometric_consent", recreate="always") as batch:
            if "recorded_by_id" in _missing:
                batch.add_column(
                    sa.Column(
                        "recorded_by_id",
                        sa.Integer(),
                        sa.ForeignKey(
                            "users.id",
                            name="fk_biometric_consent_recorded_by_id_users",
                            ondelete="SET NULL",
                        ),
                        nullable=True,
                    )
                )
            if "retention_until" in _missing:
                batch.add_column(
                    sa.Column("retention_until", sa.DateTime(), nullable=True)
                )
            if "deletion_requested_at" in _missing:
                batch.add_column(
                    sa.Column("deletion_requested_at", sa.DateTime(), nullable=True)
                )
            if "deletion_requested_by_id" in _missing:
                batch.add_column(
                    sa.Column(
                        "deletion_requested_by_id",
                        sa.Integer(),
                        sa.ForeignKey(
                            "users.id",
                            name="fk_biometric_consent_del_req_by_id_users",
                            ondelete="SET NULL",
                        ),
                        nullable=True,
                    )
                )
            if "purged_at" in _missing:
                batch.add_column(
                    sa.Column("purged_at", sa.DateTime(), nullable=True)
                )
            if "purge_detail" in _missing:
                batch.add_column(
                    sa.Column("purge_detail", _JSONB, nullable=True)
                )
            if "updated_at" in _missing:
                batch.add_column(
                    sa.Column(
                        "updated_at",
                        sa.DateTime(),
                        nullable=False,
                        server_default=now_default,
                    )
                )

    # ------------------------------------------------------------------
    # Step 2: Partial indexes on biometric_consent WHERE purged_at IS NULL.
    # Re-inspect after the optional batch (batch recreate drops separate
    # indexes).  Create each index only if absent.
    # Dual-dialect where clause: postgresql_where + sqlite_where.
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    existing_bc_indexes = {
        idx["name"] for idx in insp2.get_indexes("biometric_consent")
    }

    if "ix_biometric_consent_retention" not in existing_bc_indexes:
        op.create_index(
            "ix_biometric_consent_retention",
            "biometric_consent",
            ["retention_until"],
            postgresql_where=sa.text("purged_at IS NULL"),
            sqlite_where=sa.text("purged_at IS NULL"),
        )

    if "ix_biometric_consent_deletion_requested" not in existing_bc_indexes:
        op.create_index(
            "ix_biometric_consent_deletion_requested",
            "biometric_consent",
            ["deletion_requested_at"],
            postgresql_where=sa.text("purged_at IS NULL"),
            sqlite_where=sa.text("purged_at IS NULL"),
        )

    # ------------------------------------------------------------------
    # Step 3: compreface_subjects
    #   (a) Add consent_id FK → biometric_consent.id (SET NULL, nullable)
    #   (b) Make compreface_subject_id nullable=True (preserves unique=True)
    # Uses batch_alter_table(recreate="always") for SQLite compatibility.
    # The add_column is guarded by inspector; alter_column is always applied
    # (idempotent: setting a nullable column to nullable is a no-op in the
    # recreated table).
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    cs_cols = {c["name"] for c in insp3.get_columns("compreface_subjects")}

    with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
        if "consent_id" not in cs_cols:
            batch.add_column(
                sa.Column(
                    "consent_id",
                    sa.Integer(),
                    sa.ForeignKey(
                        "biometric_consent.id",
                        name="fk_compreface_subjects_consent_id_biometric_consent",
                        ondelete="SET NULL",
                    ),
                    nullable=True,
                )
            )
        batch.alter_column(
            "compreface_subject_id",
            existing_type=sa.String(255),
            nullable=True,
        )

    # ------------------------------------------------------------------
    # Step 4: Seed biometric_retention_years = 7 into admin_settings.
    # Category "biometric", guarded by SELECT-exists check (dialect-safe).
    # Mirrors the S16 seeding pattern for scheduler config keys.
    # ------------------------------------------------------------------
    existing_setting = bind.execute(
        sa.text("SELECT 1 FROM admin_settings WHERE key = :key"),
        {"key": "biometric_retention_years"},
    ).fetchone()

    if existing_setting is None:
        if bind.dialect.name == "postgresql":
            bind.execute(
                sa.text(
                    "INSERT INTO admin_settings"
                    " (key, value, category, requires_restart, sensitive, updated_at)"
                    " VALUES (:key, '7'::jsonb, 'biometric', false, false, CURRENT_TIMESTAMP)"
                ),
                {"key": "biometric_retention_years"},
            )
        else:
            bind.execute(
                sa.text(
                    "INSERT INTO admin_settings"
                    " (key, value, category, requires_restart, sensitive, updated_at)"
                    " VALUES (:key, '7', 'biometric', 0, 0, CURRENT_TIMESTAMP)"
                ),
                {"key": "biometric_retention_years"},
            )
        logger.info("S08: seeded biometric_retention_years = 7")


def downgrade() -> None:
    """Reverse S08 schema extensions.

    Order (reverse of upgrade):
      1. Drop consent_id from compreface_subjects.
      2. Drop partial indexes on biometric_consent.
      3. Drop the 7 RTBF columns from biometric_consent in a single batch.

    NOTE: compreface_subject_id is intentionally left nullable after downgrade.
    The original NOT NULL constraint is not restored because some rows may have
    been set to NULL during the purge workflow; restoring NOT NULL would require
    manual data remediation.  This is documented here to prevent a future
    migration from silently assuming NOT NULL.

    NOTE: The biometric_retention_years admin_settings row is intentionally not
    deleted — removing live config keys can break running services, mirroring
    the S16 pattern for scheduler config keys.
    """
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # ------------------------------------------------------------------
    # Step 3 reversal: Drop consent_id from compreface_subjects (guarded)
    # ------------------------------------------------------------------
    cs_cols = {c["name"] for c in insp.get_columns("compreface_subjects")}
    if "consent_id" in cs_cols:
        with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
            batch.drop_column("consent_id")

    # ------------------------------------------------------------------
    # Step 2 reversal: Drop partial indexes on biometric_consent (guarded).
    # Indexes must be dropped before the columns they reference.
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    if "biometric_consent" in insp2.get_table_names():
        existing_bc_indexes = {
            idx["name"] for idx in insp2.get_indexes("biometric_consent")
        }
        if "ix_biometric_consent_deletion_requested" in existing_bc_indexes:
            op.drop_index(
                "ix_biometric_consent_deletion_requested",
                table_name="biometric_consent",
            )
        if "ix_biometric_consent_retention" in existing_bc_indexes:
            op.drop_index(
                "ix_biometric_consent_retention",
                table_name="biometric_consent",
            )

    # ------------------------------------------------------------------
    # Step 1 reversal: Drop the 7 RTBF columns from biometric_consent.
    # All drops in a single batch (one table recreation) for efficiency.
    # Drop order is reverse of addition order.
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    bc_cols = {c["name"] for c in insp3.get_columns("biometric_consent")}

    cols_to_drop = [c for c in reversed(_S08_BC_COLS) if c in bc_cols]

    if cols_to_drop:
        with op.batch_alter_table("biometric_consent", recreate="always") as batch:
            for col_name in cols_to_drop:
                batch.drop_column(col_name)
