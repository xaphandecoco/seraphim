"""s07_face_enrollment_and_samples

S07 — Face Enrollment & Photo Ingest.

Creates face_samples and photo_ingest_batches tables.
Extends compreface_subjects with enrollment_source, last_trained_at,
is_orphan, and purged_at columns.

Revision ID: i3j4k5l6m7n8
Revises: h1i2j3k4l5m6
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

# Cross-dialect JSON: JSONB on Postgres, plain JSON on SQLite (test path).
# Exact pattern from g7h8i9j0k1l2 line 25.
_JSONB = sa.JSON().with_variant(PG_JSONB(), "postgresql")

revision: str = "i3j4k5l6m7n8"
down_revision: Union[str, None] = "h1i2j3k4l5m6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Create face_samples
    # FKs: compreface_subjects.compreface_subject_id (CASCADE),
    #       contacts.id (SET NULL), users.id (SET NULL)
    # ------------------------------------------------------------------
    op.create_table(
        "face_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "compreface_subject_id",
            sa.String(255),
            sa.ForeignKey("compreface_subjects.compreface_subject_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("thumb_path", sa.Text(), nullable=True),
        sa.Column("compreface_image_id", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "added_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("quality_score", sa.Numeric(5, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_face_samples_contact_id",
        "face_samples",
        ["contact_id"],
    )
    op.create_index(
        "ix_face_samples_subject_id",
        "face_samples",
        ["compreface_subject_id"],
    )

    # ------------------------------------------------------------------
    # Step 2: Create photo_ingest_batches
    # FKs: events.id (SET NULL), users.id (SET NULL)
    # report uses cross-dialect JSONB with server_default of empty array.
    # ------------------------------------------------------------------
    op.create_table(
        "photo_ingest_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'processing'"),
        ),
        sa.Column("total_images", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "processed_images", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("faces_detected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("auto_logged", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "tasks_created", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deduplicated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("errors", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "report",
            _JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_photo_ingest_batches_status",
        "photo_ingest_batches",
        ["status"],
    )

    # ------------------------------------------------------------------
    # Step 3: Extend compreface_subjects with four new columns.
    # batch_alter_table with recreate="always" for SQLite safety.
    # ------------------------------------------------------------------
    with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
        batch.add_column(
            sa.Column("enrollment_source", sa.String(20), nullable=True)
        )
        batch.add_column(
            sa.Column("last_trained_at", sa.DateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "is_orphan",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch.add_column(
            sa.Column("purged_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    # Reverse of upgrade() — in reverse order.

    # ------------------------------------------------------------------
    # Step 3 reversal: Remove the four columns from compreface_subjects.
    # batch_alter_table with recreate="always" for SQLite safety.
    # ------------------------------------------------------------------
    with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
        batch.drop_column("purged_at")
        batch.drop_column("is_orphan")
        batch.drop_column("last_trained_at")
        batch.drop_column("enrollment_source")

    # ------------------------------------------------------------------
    # Step 2 reversal: Drop photo_ingest_batches.
    # ------------------------------------------------------------------
    op.drop_index("ix_photo_ingest_batches_status", table_name="photo_ingest_batches")
    op.drop_table("photo_ingest_batches")

    # ------------------------------------------------------------------
    # Step 1 reversal: Drop face_samples.
    # ------------------------------------------------------------------
    op.drop_index("ix_face_samples_subject_id", table_name="face_samples")
    op.drop_index("ix_face_samples_contact_id", table_name="face_samples")
    op.drop_table("face_samples")
