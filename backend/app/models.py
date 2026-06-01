from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

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


class CiviCRMMember(Base):
    __tablename__ = "civicrm_members"

    contact_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class CiviCRMEvent(Base):
    __tablename__ = "civicrm_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ComprefaceSubject(Base):
    __tablename__ = "compreface_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False)
    compreface_subject_id: Mapped[str] = mapped_column(String(255), unique=True)
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("civicrm_members.contact_id")
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
        Integer, ForeignKey("civicrm_events.event_id", ondelete="SET NULL")
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


class Attendance(Base):
    __tablename__ = "attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("civicrm_members.contact_id", ondelete="CASCADE")
    )
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("civicrm_events.event_id", ondelete="CASCADE")
    )
    detection_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("detections.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | confirmed
    push_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | queued | pushed | failed | expired | dead_letter
    push_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    last_push_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("contact_id", "event_id", name="uq_attendance_contact_event"),
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
        Integer, ForeignKey("civicrm_events.event_id", ondelete="SET NULL")
    )
    push_status: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


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
