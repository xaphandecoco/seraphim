from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Use Postgres JSONB in production (matches the columns created by migrations) and
# fall back to the cross-dialect JSON type on SQLite (used by the test suite).
JSONB = JSON().with_variant(_PG_JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_provider: Mapped[str] = mapped_column(
        String(20), default="local"
    )  # local | google
    role: Mapped[str] = mapped_column(
        String(20), default="volunteer"
    )  # admin | volunteer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(255))
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(Text, nullable=False)
    zone_label: Mapped[Optional[str]] = mapped_column(String(100))
    fps: Mapped[int] = mapped_column(Integer, default=1)
    enable_health_check: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(
        String(20), default="streaming"
    )  # streaming | reconnecting | offline
    offline_since: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Contact(Base):
    """App-minted contact record (replaces CiviCRMMember).

    external_id: nullable CiviCRM contact_id kept for migration traceability (S06).
    Derived snapshot columns (last_attended_at … is_connected) are populated by
    the S23 nightly recompute job; all are nullable here.
    """
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # ORM default is lowercase to match Pydantic Literal['individual','household','organization'].
    # server_default='Individual' in migration g7h8i9j0k1l2 cannot be retro-changed; any pre-prod
    # rows carrying 'Individual' must be normalized by a one-time UPDATE (data task S06, not this PR).
    contact_type: Mapped[str] = mapped_column(String(50), nullable=False, default="individual")
    contact_subtype: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # CN-01: nickname is a core column created here; S03 does NOT re-add it
    nickname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    suffix: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    birth_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    street_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Derived snapshot fields — populated by S23 nightly job
    last_attended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attendance_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weeks_absent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tier: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # Tier0|Tier1|Tier2|Tier3|Inactive
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_connected: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    __table_args__ = (
        # Partial unique: multiple NULLs allowed; only non-NULL values are unique
        Index(
            "ix_contacts_external_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_contacts_email", "email"),
        Index("ix_contacts_last_name", "last_name"),
        Index("ix_contacts_is_deleted", "is_deleted"),
    )


class EventSeries(Base):
    """Recurring event series (e.g. Sunday Service, Powerhouse).

    cadence: JSONB dict describing the recurrence pattern (e.g. day-of-week,
    frequency).  Defaults to an empty dict; the application layer populates it.
    """
    __tablename__ = "event_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cadence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    default_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Event(Base):
    """App-minted event record (replaces CiviCRMEvent).

    Minimal core columns only per CN-16.  S04 adds event_type, session_time,
    occurrence_date, recurring_series_id, is_active, location, and the
    event_series table.
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, default="Event")
    session_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    occurrence_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    recurring_series_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("event_series.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index(
            "ix_events_external_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_events_start_at", "start_at"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_occurrence_date", "occurrence_date"),
    )


class ComprefaceSubject(Base):
    __tablename__ = "compreface_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False)
    compreface_subject_id: Mapped[str] = mapped_column(String(255), unique=True)
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL")
    )
    enrollment_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | active
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    last_trained_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # S07: soft-delete/purge support + enrollment provenance + orphan tracking
    enrollment_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_orphan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 3))
    tier: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # 100 | 91-99 | below90 | unknown
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # auto_logged | tasked | skipped | resolved | expired | pit
    matched_name: Mapped[Optional[str]] = mapped_column(String(255))
    compreface_subject_id: Mapped[Optional[str]] = mapped_column(
        String(255)
    )
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL")
    )
    is_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    detection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("detections.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | confirmed | resolved | skipped | expired | pit
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)  # 1 | 2
    current_approvals: Mapped[int] = mapped_column(Integer, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, default=0)
    skip_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    pit_status: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # awaiting | enrolled | deleted | non_person
    expiry_date: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: utc_now() + timedelta(days=31)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class TaskAction(Base):
    __tablename__ = "task_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    volunteer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # confirm | edit | add | skip | admin_override | audit_confirm | audit_deny | audit_edit
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Partial unique index: a volunteer may take at most ONE approval action
    # (confirm/edit/add) on a given task, but may still skip the same task multiple
    # times. Declared in the model (not only the migration) because the test suite
    # builds its schema via `create_all`. Both dialect predicates are provided so the
    # partial constraint applies on SQLite (tests) and Postgres (prod).
    __table_args__ = (
        Index(
            "uq_task_action_approval",
            "task_id",
            "volunteer_id",
            unique=True,
            postgresql_where=text("action IN ('confirm', 'edit', 'add')"),
            sqlite_where=text("action IN ('confirm', 'edit', 'add')"),
        ),
    )


class Participant(Base):
    """Attendance record (replaces Attendance/attendance table).

    source values (CN-07): face | manual | zoom | name_list |
    community_report | import | bulk | migration
    """
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="attended"
    )  # attended | registered | no_show | cancelled
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="manual"
    )  # face | manual | zoom | name_list | community_report | import | bulk | migration
    detection_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("detections.id", ondelete="SET NULL"), nullable=True
    )
    registered_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("event_id", "contact_id", name="uq_participant_event_contact"),
        Index("ix_participants_contact_id", "contact_id"),
        Index("ix_participants_event_id", "event_id"),
        Index("ix_participants_source", "source"),
    )


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    detection_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("detections.id", ondelete="SET NULL")
    )
    face_snapshot_path: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    camera_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL")
    )
    matched_name: Mapped[Optional[str]] = mapped_column(String(255))
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 3))
    tier: Mapped[Optional[str]] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # auto | confirmed | edited | added | skipped | expired | admin_override | audit_confirmed | audit_denied | audit_edited
    volunteer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    second_volunteer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL")
    )
    # push_status column removed in S01 migration
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AuditLog(Base):
    """Audit trail for all create/update/delete operations.

    Table + model owned by S01 (per CN-03).
    The app/services/audit.py::record() write helper is S02's responsibility.

    actor_id is NULL for system/API-key actors.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    __table_args__ = (
        Index("ix_audit_log_entity_entity_id", "entity", "entity_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )


class PitQueue(Base):
    __tablename__ = "pit_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    admin_action: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # enroll | delete | non_person
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PhotoIngestBatch(Base):
    """Tracks progress and per-image results for a bulk-upload job.

    The UI polls GET /uploads/photos/batch/{batch_id} to show live progress.
    report: list of per-image dicts capped at 500 entries in-process.
    """
    __tablename__ = "photo_ingest_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="processing"
    )  # processing | completed | failed
    total_images: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_images: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    faces_detected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_logged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplicated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_photo_ingest_batches_status", "status"),
    )


class FaceSample(Base):
    """One enrolled face image for a CompreFace subject.

    source values: manual | detection | bulk_ingest | backfill
    compreface_image_id: UUID returned by CompreFace add_example; NULL if
        enrollment via the remote API has not yet occurred.
    """
    __tablename__ = "face_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    compreface_subject_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("compreface_subjects.compreface_subject_id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    thumb_path: Mapped[str] = mapped_column(Text, nullable=False)
    compreface_image_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual"
    )  # manual | detection | bulk_ingest | backfill
    added_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    quality_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    __table_args__ = (
        Index("ix_face_samples_contact_id", "contact_id"),
        Index("ix_face_samples_subject_id", "compreface_subject_id"),
    )


class VolunteerStat(Base):
    __tablename__ = "volunteer_stats"

    volunteer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[str] = mapped_column(
        String(7), primary_key=True
    )  # YYYY-MM format
    tasks_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    tasks_edited: Mapped[int] = mapped_column(Integer, default=0)
    tasks_added: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_score: Mapped[float] = mapped_column(Numeric(5, 2), default=100.0)
    total_points: Mapped[int] = mapped_column(Integer, default=0)


class AdminSetting(Base):
    __tablename__ = "admin_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    category: Mapped[str] = mapped_column(String(50), default="general")
    description: Mapped[Optional[str]] = mapped_column(Text)
    requires_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )


class ExportJob(Base):
    """Async export job — tracks status and result for CSV/XLSX exports.

    job_type: attendance | contacts | audit_log (extensible)
    fmt: csv | xlsx
    params: arbitrary filter params passed by the requester.
    status: pending | running | done | error
    """

    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    fmt: Mapped[str] = mapped_column(String(10), nullable=False)
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'"), default=dict
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending", default="pending"
    )
    requested_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), default=utc_now
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_export_jobs_status_created", "status", "created_at"),
        Index("ix_export_jobs_requested_by", "requested_by_id"),
    )


class CustomFieldGroup(Base):
    """Admin-defined group of custom fields for an entity type.

    entity: lowercase contact | event | activity (C8).
    name: machine snake_case name; UNIQUE per (entity, name).
    """
    __tablename__ = "custom_field_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="contact")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("entity", "name", name="uq_custom_field_group_entity_name"),
        Index("ix_cfg_entity_active_weight", "entity", "is_active", "weight"),
    )


class CustomFieldDef(Base):
    """One field definition within a CustomFieldGroup.

    data_type: text | textarea | select | multiselect | date | number |
               checkbox | contact_reference
    options: list of {value: str, label: str} for select/multiselect; [] otherwise.
    is_multi: true → stored value is a JSON array; auto-forced for multiselect.
    """
    __tablename__ = "custom_field_def"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("custom_field_group.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    options: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'"), default=list
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_multi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    help_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("group_id", "name", name="uq_custom_field_def_group_name"),
        Index("ix_cfd_group_active_weight", "group_id", "is_active", "weight"),
    )

    def __init__(self, **kwargs: object) -> None:
        # Ensure options is [] immediately at construction time (before flush/commit).
        # mapped_column(default=list) only fires at INSERT; the Python-side attribute
        # stays None until the ORM issues a SQL INSERT.  This __init__ bridges the gap
        # so that any code that reads .options pre-flush (e.g. routers, seed functions,
        # service layer) gets [] rather than None.
        if "options" not in kwargs:
            kwargs["options"] = []
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# S22 — Name Matching tables (spec §3.1-3.3)
# ---------------------------------------------------------------------------

class NameAlias(Base):
    """Canonical alias → contact mapping used by the name-matching pipeline.

    alias_text: normalised lowercase string (e.g. 'liz', 'beth').
    alias_type: nick | typo | alt_spelling | maiden | preferred
    source: who created this alias — admin | bulk_import | community_report | inferred
    meta: arbitrary JSONB bag for future provenance fields.
    contact_id ondelete CASCADE — alias records are purged when the contact is deleted.
    created_by_id ondelete SET NULL — user row may be removed without losing the alias.
    """
    __tablename__ = "name_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="nick"
    )  # nick | typo | alt_spelling | maiden | preferred
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="admin"
    )  # admin | bulk_import | community_report | inferred
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("uq_name_alias_text", "alias_text", unique=True),
        Index("ix_name_alias_contact_id", "contact_id"),
        Index("ix_name_alias_alias_type", "alias_type"),
    )


class CommunityReport(Base):
    """Volunteer-submitted community report with name-list and metadata.

    raw_text: the original free-form name string submitted by the volunteer.
    parsed_names: JSONB list of dicts produced by the name-extraction step;
        each dict has at minimum {"raw": str, "matched_contact_id": int|null}.
    status: pending | processing | complete | partial | archived
    match_status: pending | complete | partial  — tracks name-matching progress
    date_of_activity: DateTime (not Date) — mirrors the utc_now pattern; the
        CommunityReportCreate Pydantic schema accepts a `date` field and the
        handler coerces it to a naive UTC datetime before persisting.
    All FK references use ondelete SET NULL so that deleting a contact, event,
    or user does not cascade-delete the report.
    participants.source value 'community_report' needs no DDL — it is already
    covered by the String(30) source column on the participants table (CN-07).
    """
    __tablename__ = "community_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    # event_title: free-text fallback when event_id is not supplied
    event_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    submitted_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # submitted_by_contact_id: auto-resolved from users.email → contacts.email
    submitted_by_contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_names: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # S22-F06 extended fields
    zone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    topics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prayer_items: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attendee_names: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    event_leader_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    event_leader_contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    photo_paths: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # match_status: pending | complete | partial
    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | processing | complete | partial | archived
    date_of_activity: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("ix_community_report_event_id", "event_id"),
        Index("ix_community_report_status", "status"),
        Index("ix_community_report_submitted_by_id", "submitted_by_id"),
        Index("ix_community_report_match_status", "match_status"),
        Index("ix_community_report_zone", "zone"),
    )


class NameMatchReviewQueue(Base):
    """One unresolved name-match candidate requiring human review.

    raw_name: the raw string token being matched.
    candidate_contact_id: the top-scoring contact suggestion (nullable — may be
        null when no candidate clears the confidence threshold).
    score: Jaro-Winkler or composite score in [0, 1].
    status: pending | accepted | rejected | skipped
    community_report_id FK → community_report.id ondelete SET NULL; the review
        queue row becomes orphaned (community_report_id = NULL) if the source
        report is deleted, preserving the audit trail.
    resolved_by_id ondelete SET NULL — user may be removed without losing the
        queue entry.
    """
    __tablename__ = "name_match_review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    community_report_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("community_report.id", ondelete="SET NULL"), nullable=True
    )
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    score: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | accepted | rejected | skipped
    resolved_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("ix_nmrq_community_report_id", "community_report_id"),
        Index("ix_nmrq_event_id", "event_id"),
        Index("ix_nmrq_contact_id", "contact_id"),
        Index("ix_nmrq_status", "status"),
        Index("ix_nmrq_candidate_contact_id", "candidate_contact_id"),
    )
