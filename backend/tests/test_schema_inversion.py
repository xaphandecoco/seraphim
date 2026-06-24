"""test_schema_inversion.py — S01 Phase 1 (RED → GREEN)

Asserts the target schema via SQLAlchemy reflection after Base.metadata.create_all
runs on a fresh in-memory SQLite database, and asserts migration file content for
properties that only the migration creates (stash columns CN-28).

We use create_all (not alembic upgrade head) because the initial migration
(74e9ab60ea7e) uses raw postgresql.JSONB which is incompatible with SQLite.
The test suite has always used create_all for its isolation model; this is
consistent with that pattern.
"""
from __future__ import annotations

import pathlib

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Session-level in-memory DB built from ORM metadata
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine_and_meta():
    """Build an in-memory SQLite DB using Base.metadata.create_all.

    app.models must be imported BEFORE create_all so all table definitions
    are registered on Base.metadata (SQLAlchemy uses declarative registration).
    """
    import app.models  # noqa: F401 — registers all ORM classes on Base.metadata
    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def inspector(engine_and_meta):
    return inspect(engine_and_meta)


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------


def test_contacts_table_exists(inspector):
    assert inspector.has_table("contacts"), "contacts table must exist"


def test_events_table_exists(inspector):
    assert inspector.has_table("events"), "events table must exist"


def test_participants_table_exists(inspector):
    assert inspector.has_table("participants"), "participants table must exist"


def test_audit_log_table_exists(inspector):
    assert inspector.has_table("audit_log"), "audit_log table must exist"


def test_civicrm_members_does_not_exist(inspector):
    assert not inspector.has_table("civicrm_members"), \
        "civicrm_members must not exist after S01"


def test_civicrm_events_does_not_exist(inspector):
    assert not inspector.has_table("civicrm_events"), \
        "civicrm_events must not exist after S01"


def test_attendance_does_not_exist(inspector):
    assert not inspector.has_table("attendance"), \
        "attendance must not exist after S01"


# ---------------------------------------------------------------------------
# contacts columns
# ---------------------------------------------------------------------------


def test_contacts_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("contacts")}
    required = {
        "id", "external_id", "contact_type", "contact_subtype",
        "first_name", "last_name", "nickname", "suffix", "gender",
        "birth_date", "phone", "email", "street_address", "custom_data",
        "is_deleted", "created_at", "updated_at",
        "last_attended_at", "attendance_count", "weeks_absent",
        "tier", "is_active", "is_regular", "is_connected",
    }
    missing = required - cols
    assert not missing, f"contacts missing columns: {missing}"


# ---------------------------------------------------------------------------
# events columns (minimal core only per CN-16)
# ---------------------------------------------------------------------------


def test_events_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("events")}
    required = {"id", "external_id", "title", "start_at", "end_at", "created_at"}
    missing = required - cols
    assert not missing, f"events missing columns: {missing}"


def test_events_has_no_s04_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("events")}
    s04_columns = {"event_type", "session_time", "occurrence_date",
                   "recurring_series_id", "location"}
    premature = s04_columns & cols
    assert not premature, f"events has S04 columns that belong to S04: {premature}"


# ---------------------------------------------------------------------------
# participants columns
# ---------------------------------------------------------------------------


def test_participants_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("participants")}
    required = {
        "id", "contact_id", "event_id", "status", "role",
        "source", "detection_id", "registered_by_id", "created_at",
    }
    missing = required - cols
    assert not missing, f"participants missing columns: {missing}"


# ---------------------------------------------------------------------------
# audit_log columns
# ---------------------------------------------------------------------------


def test_audit_log_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("audit_log")}
    required = {
        "id", "actor_id", "action", "entity", "entity_id",
        "before", "after", "created_at",
    }
    missing = required - cols
    assert not missing, f"audit_log missing columns: {missing}"


# ---------------------------------------------------------------------------
# contacts.external_id partial unique — multiple NULLs allowed
# ---------------------------------------------------------------------------


def test_contacts_external_id_partial_unique_allows_multiple_nulls(engine_and_meta):
    """Insert two contacts with external_id=NULL; must not raise a unique violation."""
    with engine_and_meta.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO contacts "
                "(contact_type, first_name, last_name, custom_data, is_deleted, created_at, updated_at) "
                "VALUES ('Individual', 'Alice', 'Test', '{}', 0, datetime('now'), datetime('now'))"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO contacts "
                "(contact_type, first_name, last_name, custom_data, is_deleted, created_at, updated_at) "
                "VALUES ('Individual', 'Bob', 'Test', '{}', 0, datetime('now'), datetime('now'))"
            )
        )
        conn.commit()
    # If we reach here, no unique violation was raised — partial index works.


def test_contacts_external_id_unique_enforced_for_non_null(engine_and_meta):
    """Two contacts with the same non-NULL external_id must raise an error."""
    with engine_and_meta.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO contacts "
                "(external_id, contact_type, first_name, last_name, custom_data, is_deleted, created_at, updated_at) "
                "VALUES (8888, 'Individual', 'Charlie', 'Test', '{}', 0, datetime('now'), datetime('now'))"
            )
        )
        conn.commit()

    with pytest.raises(Exception):
        with engine_and_meta.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO contacts "
                    "(external_id, contact_type, first_name, last_name, custom_data, is_deleted, created_at, updated_at) "
                    "VALUES (8888, 'Individual', 'Dave', 'Test', '{}', 0, datetime('now'), datetime('now'))"
                )
            )
            conn.commit()


# ---------------------------------------------------------------------------
# compreface_subjects.contact_id FK -> contacts.id
# ---------------------------------------------------------------------------


def test_compreface_subjects_contact_id_fk_points_to_contacts(inspector):
    fks = inspector.get_foreign_keys("compreface_subjects")
    contact_fk = [fk for fk in fks if fk["constrained_columns"] == ["contact_id"]]
    assert contact_fk, "compreface_subjects.contact_id must have a FK"
    assert contact_fk[0]["referred_table"] == "contacts", \
        f"Expected FK to contacts, got {contact_fk[0]['referred_table']}"


# ---------------------------------------------------------------------------
# detections.event_id FK -> events.id
# ---------------------------------------------------------------------------


def test_detections_event_id_fk_points_to_events(inspector):
    fks = inspector.get_foreign_keys("detections")
    event_fk = [fk for fk in fks if fk["constrained_columns"] == ["event_id"]]
    assert event_fk, "detections.event_id must have a FK"
    assert event_fk[0]["referred_table"] == "events", \
        f"Expected FK to events, got {event_fk[0]['referred_table']}"


# ---------------------------------------------------------------------------
# logs.event_id FK -> events.id AND logs.push_status column gone
# ---------------------------------------------------------------------------


def test_logs_event_id_fk_points_to_events(inspector):
    fks = inspector.get_foreign_keys("logs")
    event_fk = [fk for fk in fks if fk["constrained_columns"] == ["event_id"]]
    assert event_fk, "logs.event_id must have a FK"
    assert event_fk[0]["referred_table"] == "events", \
        f"Expected FK to events, got {event_fk[0]['referred_table']}"


def test_logs_push_status_column_removed(inspector):
    cols = {c["name"] for c in inspector.get_columns("logs")}
    assert "push_status" not in cols, \
        "logs.push_status must not exist in the S01 model"


# ---------------------------------------------------------------------------
# CN-28 stash columns -- verified via migration file content
# (stash columns are only in the migration, not in Base models)
# ---------------------------------------------------------------------------


def test_migration_file_has_legacy_civicrm_contact_id_stash():
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "g7h8i9j0k1l2_schema_inversion_civicrm_excision.py"
    )
    assert mig.exists(), "Migration file g7h8i9j0k1l2 must exist"
    content = mig.read_text(encoding="utf-8")
    assert "_legacy_civicrm_contact_id" in content, \
        "Migration must add _legacy_civicrm_contact_id stash column (CN-28)"
    assert "UPDATE compreface_subjects SET _legacy_civicrm_contact_id = contact_id" in content, \
        "Migration must backfill _legacy_civicrm_contact_id from contact_id (CN-28)"


def test_migration_file_has_legacy_civicrm_event_id_stash():
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "g7h8i9j0k1l2_schema_inversion_civicrm_excision.py"
    )
    content = mig.read_text(encoding="utf-8")
    assert "_legacy_civicrm_event_id" in content, \
        "Migration must add _legacy_civicrm_event_id stash column (CN-28)"
    assert "UPDATE detections SET _legacy_civicrm_event_id = event_id" in content, \
        "Migration must backfill _legacy_civicrm_event_id from event_id (CN-28)"


# ---------------------------------------------------------------------------
# Migration file structural checks
# ---------------------------------------------------------------------------


def test_migration_down_revision_is_f3a4b5c6d7e8():
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "g7h8i9j0k1l2_schema_inversion_civicrm_excision.py"
    )
    content = mig.read_text(encoding="utf-8")
    assert "down_revision" in content
    assert '"f3a4b5c6d7e8"' in content or "'f3a4b5c6d7e8'" in content, \
        "Migration down_revision must be f3a4b5c6d7e8"


def test_migration_does_not_create_event_series():
    """S04 owns event_series; S01 must not create it (supersedes CN-27)."""
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "g7h8i9j0k1l2_schema_inversion_civicrm_excision.py"
    )
    content = mig.read_text(encoding="utf-8")
    assert "create_table('event_series'" not in content and \
           'create_table("event_series"' not in content, \
        "S01 migration must NOT create event_series table (S04 owns it)"
