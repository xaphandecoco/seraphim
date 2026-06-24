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
    text,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Use Postgres JSONB in production (matches the columns created by migrations) and
# fall back to the cross-dialect JSON type on SQLite (used by the test suite).
JSONB = JSON().with_variant(_PG_JSONB, "postgresql")

from app.database import Base


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
    contact_type: Mapped[str] = mapped_column(String(50), nullable=False, default="Individual")
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

    __table_args__ = (
        Index(
            "ix_events_external_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_events_start_at", "start_at"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


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
