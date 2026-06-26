"""schema_inversion_civicrm_excision

S01 — Schema Inversion & CiviCRM Excision.

Creates contacts, events, participants, audit_log tables with app-minted PKs.
Drops civicrm_members, civicrm_events, attendance.
Repoints compreface_subjects.contact_id → contacts.id,
         detections.event_id         → events.id,
         logs.event_id               → events.id.
Drops logs.push_status.
Stashes old CiviCRM FK values in _legacy_* columns (CN-28) for S24 remapping.

Revision ID: g7h8i9j0k1l2
Revises: f3a4b5c6d7e8
Create Date: 2026-06-22 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from alembic import op

# Cross-dialect JSON: JSONB on Postgres, plain JSON on SQLite
_JSONB = sa.JSON().with_variant(PG_JSONB(), "postgresql")

revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: event_series is created by S04, not here (supersedes CN-27)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Step 2: Create contacts (no FK deps)
    # ------------------------------------------------------------------
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.Integer(), nullable=True),
        sa.Column("contact_type", sa.String(50), nullable=False, server_default="Individual"),
        sa.Column("contact_subtype", sa.String(100), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=False),
        sa.Column("last_name", sa.String(255), nullable=False),
        sa.Column("nickname", sa.String(255), nullable=True),
        sa.Column("suffix", sa.String(50), nullable=True),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("street_address", sa.Text(), nullable=True),
        sa.Column(
            "custom_data",
            _JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        # Derived snapshot columns (S23 nightly job populates these)
        sa.Column("last_attended_at", sa.DateTime(), nullable=True),
        sa.Column("attendance_count", sa.Integer(), nullable=True),
        sa.Column("weeks_absent", sa.Integer(), nullable=True),
        sa.Column("tier", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_regular", sa.Boolean(), nullable=True),
        sa.Column("is_connected", sa.Boolean(), nullable=True),
    )
    # Partial unique index — multiple NULLs allowed; only non-NULL values must be unique
    op.create_index(
        "ix_contacts_external_id",
        "contacts",
        ["external_id"],
        unique=True,
        postgresql_where=text("external_id IS NOT NULL"),
        sqlite_where=text("external_id IS NOT NULL"),
    )
    op.create_index("ix_contacts_email", "contacts", ["email"])
    op.create_index("ix_contacts_last_name", "contacts", ["last_name"])
    op.create_index("ix_contacts_is_deleted", "contacts", ["is_deleted"])

    # ------------------------------------------------------------------
    # Step 3: Create events (minimal core only per CN-16)
    # S04 adds event_type, session_time, occurrence_date,
    # recurring_series_id, is_active, location.
    # ------------------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_events_external_id",
        "events",
        ["external_id"],
        unique=True,
        postgresql_where=text("external_id IS NOT NULL"),
        sqlite_where=text("external_id IS NOT NULL"),
    )
    op.create_index("ix_events_start_at", "events", ["start_at"])

    # ------------------------------------------------------------------
    # Step 4: Drop attendance (had FKs to both old tables)
    # ------------------------------------------------------------------
    op.drop_table("attendance")

    # ------------------------------------------------------------------
    # Step 5: Repoint FKs using batch_alter_table (SQLite-safe).
    # Per CN-28: stash old CiviCRM ids into transitional columns BEFORE
    # the FK repoint so S24 can remap face/detection links later.
    # ------------------------------------------------------------------

    # 5a. compreface_subjects.contact_id → contacts.id
    with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
        batch.add_column(sa.Column("_legacy_civicrm_contact_id", sa.Integer(), nullable=True))
        batch.drop_constraint("compreface_subjects_contact_id_fkey", type_="foreignkey")
        batch.create_foreign_key(
            "fk_compreface_subjects_contact_id",
            "contacts",
            ["contact_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # Backfill the stash column with the (now-orphaned) old CiviCRM contact ids
    op.execute(
        "UPDATE compreface_subjects SET _legacy_civicrm_contact_id = contact_id"
    )

    # 5b. detections.event_id → events.id
    with op.batch_alter_table("detections", recreate="always") as batch:
        batch.add_column(sa.Column("_legacy_civicrm_event_id", sa.Integer(), nullable=True))
        batch.drop_constraint("detections_event_id_fkey", type_="foreignkey")
        batch.create_foreign_key(
            "fk_detections_event_id",
            "events",
            ["event_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # Backfill the stash column
    op.execute("UPDATE detections SET _legacy_civicrm_event_id = event_id")

    # 5c. logs.event_id → events.id; drop push_status
    with op.batch_alter_table("logs", recreate="always") as batch:
        batch.drop_constraint("logs_event_id_fkey", type_="foreignkey")
        batch.create_foreign_key(
            "fk_logs_event_id",
            "events",
            ["event_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_column("push_status")

    # ------------------------------------------------------------------
    # Step 6: Drop legacy CiviCRM tables (no remaining FKs point to them)
    # ------------------------------------------------------------------
    op.drop_table("civicrm_events")
    op.drop_table("civicrm_members")

    # ------------------------------------------------------------------
    # Step 7: Create participants
    # ------------------------------------------------------------------
    op.create_table(
        "participants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="attended"),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column(
            "detection_id",
            sa.Integer(),
            sa.ForeignKey("detections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "registered_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "contact_id", name="uq_participant_event_contact"),
    )
    op.create_index("ix_participants_contact_id", "participants", ["contact_id"])
    op.create_index("ix_participants_event_id", "participants", ["event_id"])
    op.create_index("ix_participants_source", "participants", ["source"])

    # ------------------------------------------------------------------
    # Step 8: Create audit_log (per CN-03; S02 adds the record() helper)
    # ------------------------------------------------------------------
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
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    # Reverse of upgrade() — drop in reverse dependency order.
    # Does NOT touch event_series — S01 never created it; S04 owns it.

    # Drop audit_log (per CN-03 — S01 created it, S01 removes it on downgrade)
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_entity_entity_id", table_name="audit_log")
    op.drop_table("audit_log")

    # Drop participants
    op.drop_index("ix_participants_source", table_name="participants")
    op.drop_index("ix_participants_event_id", table_name="participants")
    op.drop_index("ix_participants_contact_id", table_name="participants")
    op.drop_table("participants")

    # Recreate legacy tables (empty schema; data was not preserved — dev environment)
    op.create_table(
        "civicrm_members",
        sa.Column("contact_id", sa.Integer(), primary_key=True),
        sa.Column("first_name", sa.String(255), nullable=False),
        sa.Column("last_name", sa.String(255), nullable=False),
        sa.Column("nickname", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "civicrm_events",
        sa.Column("event_id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=False),
        sa.Column("end_date", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("civicrm_members.contact_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("civicrm_events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "detection_id",
            sa.Integer(),
            sa.ForeignKey("detections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("push_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("push_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_push_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("contact_id", "event_id", name="uq_attendance_contact_event"),
    )

    # Restore old FKs on compreface_subjects, detections, logs;
    # drop the CN-28 stash columns; restore logs.push_status.

    # compreface_subjects
    with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
        batch.drop_constraint("fk_compreface_subjects_contact_id", type_="foreignkey")
        batch.create_foreign_key(
            "compreface_subjects_contact_id_fkey",
            "civicrm_members",
            ["contact_id"],
            ["contact_id"],
        )
        batch.drop_column("_legacy_civicrm_contact_id")

    # detections
    with op.batch_alter_table("detections", recreate="always") as batch:
        batch.drop_constraint("fk_detections_event_id", type_="foreignkey")
        batch.create_foreign_key(
            "detections_event_id_fkey",
            "civicrm_events",
            ["event_id"],
            ["event_id"],
            ondelete="SET NULL",
        )
        batch.drop_column("_legacy_civicrm_event_id")

    # logs
    with op.batch_alter_table("logs", recreate="always") as batch:
        batch.drop_constraint("fk_logs_event_id", type_="foreignkey")
        batch.create_foreign_key(
            "logs_event_id_fkey",
            "civicrm_events",
            ["event_id"],
            ["event_id"],
            ondelete="SET NULL",
        )
        batch.add_column(sa.Column("push_status", sa.String(20), nullable=True))

    # Drop contacts and events (after FKs pointing to them are gone)
    op.drop_index("ix_events_start_at", table_name="events")
    op.drop_index("ix_events_external_id", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_contacts_is_deleted", table_name="contacts")
    op.drop_index("ix_contacts_last_name", table_name="contacts")
    op.drop_index("ix_contacts_email", table_name="contacts")
    op.drop_index("ix_contacts_external_id", table_name="contacts")
    op.drop_table("contacts")
