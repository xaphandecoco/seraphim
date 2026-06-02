from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


# ============================================================================
# Auth
# ============================================================================

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    email: str
    name: Optional[str] = None
    role: str = "volunteer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: Optional[str] = None
    role: str
    is_active: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


def _validate_password_strength(v: str) -> str:
    if len(v) < 12:
        raise ValueError("Password must be at least 12 characters long")
    if not any(c.isupper() for c in v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one number")
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in v):
        raise ValueError("Password must contain at least one special character")
    return v


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class AddVolunteerRequest(BaseModel):
    email: EmailStr
    name: str
    role: Literal["volunteer", "admin"] = "volunteer"
    temporary_password: str

    @field_validator("temporary_password")
    @classmethod
    def validate_temp_password(cls, v: str) -> str:
        return _validate_password_strength(v)


# ============================================================================
# Setup
# ============================================================================

class SetupStatusResponse(BaseModel):
    setup_complete: bool


class ConnectionTestRequest(BaseModel):
    database_url: Optional[str] = None
    redis_url: Optional[str] = None


class ConnectionTestResponse(BaseModel):
    database_ok: bool
    database_message: str = ""
    redis_ok: bool
    redis_message: str = ""


class ServiceTestRequest(BaseModel):
    compreface_url: Optional[str] = None
    compreface_api_key: Optional[str] = None
    civicrm_url: Optional[str] = None


class ServiceTestResponse(BaseModel):
    compreface_ok: bool
    compreface_message: str = ""
    civicrm_ok: Optional[bool] = None
    civicrm_message: str = ""


class SetupRequest(BaseModel):
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    compreface_url: str
    compreface_api_key: str
    civicrm_url: Optional[str] = None
    civicrm_api_key: Optional[str] = None
    civicrm_site_key: Optional[str] = None
    admin_email: str
    admin_password: str
    admin_name: str = "Admin"
    cameras: List[dict] = []
    jwt_secret: Optional[str] = None

    @field_validator("admin_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


# ============================================================================
# Tasks
# ============================================================================

class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    detection_id: int
    status: str
    required_approvals: int
    current_approvals: int
    skip_count: int
    skip_reasons: List[str] = []
    tier: Optional[str] = None
    confidence: Optional[float] = None
    matched_name: Optional[str] = None
    face_thumbnail_path: Optional[str] = None
    camera_name: Optional[str] = None
    detected_at: Optional[datetime] = None
    expiry_date: datetime
    enrollment_progress: Optional[str] = None


class TaskActionRequest(BaseModel):
    member_id: Optional[int] = None
    reason: Optional[str] = None


class PaginatedTaskResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[TaskResponse]


# ============================================================================
# Leaderboard
# ============================================================================

class LeaderboardEntry(BaseModel):
    volunteer_id: str
    volunteer_email: str
    total_points: int
    tasks_completed: int = 0
    accuracy_percent: float = 100.0


class LeaderboardResponse(BaseModel):
    period: str
    entries: List[LeaderboardEntry]


# ============================================================================
# Cameras
# ============================================================================

def _validate_rtsp_url(v: str) -> str:
    if not v.startswith(("rtsp://", "rtsps://")):
        raise ValueError("Camera URL must start with rtsp:// or rtsps://")
    return v


class CameraCreateRequest(BaseModel):
    name: str
    rtsp_url: str
    zone_label: Optional[str] = None
    fps: int = 1
    enable_health_check: bool = True

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp(cls, v: str) -> str:
        return _validate_rtsp_url(v)


class CameraUpdateRequest(BaseModel):
    name: Optional[str] = None
    rtsp_url: Optional[str] = None
    zone_label: Optional[str] = None
    fps: Optional[int] = None
    enable_health_check: Optional[bool] = None

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_rtsp_url(v)
        return v


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    rtsp_url: str
    zone_label: Optional[str] = None
    fps: int
    enable_health_check: bool
    status: str
    offline_since: Optional[datetime] = None
    created_at: datetime


# ============================================================================
# Logs
# ============================================================================

class LogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    detection_id: Optional[int] = None
    face_snapshot_path: Optional[str] = None
    timestamp: datetime
    camera_id: Optional[int] = None
    matched_name: Optional[str] = None
    confidence: Optional[float] = None
    tier: Optional[str] = None
    action: str
    volunteer_id: Optional[int] = None
    second_volunteer_id: Optional[int] = None
    event_id: Optional[int] = None
    push_status: Optional[str] = None
    created_at: datetime


class PaginatedLogResponse(BaseModel):
    items: List[LogResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# Settings
# ============================================================================

class SettingItem(BaseModel):
    key: str
    value: dict
    category: str
    description: Optional[str] = None
    requires_restart: bool = False
    sensitive: bool = False


class SettingsResponse(BaseModel):
    settings: List[SettingItem]


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


# ============================================================================
# Health
# ============================================================================

class HealthCheck(BaseModel):
    status: str
    postgres: bool
    redis: bool
    compreface: bool
    timestamp: datetime


# ============================================================================
# Camera Preview
# ============================================================================

class CameraPreviewResponse(BaseModel):
    content_type: str = "image/jpeg"
    data_url: str


# ============================================================================
# Attendance
# ============================================================================

class AttendanceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contact_id: Optional[int] = None
    event_id: Optional[int] = None
    detection_id: Optional[int] = None
    status: str
    push_status: str
    created_at: datetime


# ============================================================================
# Attendance Push
# ============================================================================

class AttendeeSummary(BaseModel):
    member_id: int
    name: str
    detected_at: Optional[datetime] = None
    camera_name: Optional[str] = None
    included: bool = True


class PushDiff(BaseModel):
    event_id: int
    event_title: str
    will_attend: List[AttendeeSummary]
    missing: List[AttendeeSummary]
    duplicates_warn: List[str]


# ============================================================================
# Events
# ============================================================================

class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_id: int
    title: str
    start_date: datetime
    end_date: datetime | None = None


# ============================================================================
# Members
# ============================================================================

class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    contact_id: int
    first_name: str
    last_name: str
    email: str | None = None


class AttendeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    contact_id: int
    first_name: str
    last_name: str
    email: str | None = None
    nickname: str | None = None
    face_thumbnail_path: str | None = None
    sample_count: int = 0


# ============================================================================
# Dead-Letter Queue
# ============================================================================

class DeadLetterRecord(BaseModel):
    """Attendance record that has been moved to the dead-letter queue."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    event_id: int
    detection_id: Optional[int] = None
    status: str
    push_status: str
    push_attempts: int
    last_push_error: Optional[str] = None
    created_at: datetime


class DeadLetterListResponse(BaseModel):
    """Paginated list of dead-letter attendance records."""

    total: int
    items: List[DeadLetterRecord]


class DeadLetterRetryResponse(BaseModel):
    """Confirmation returned when a dead-letter record is reset for retry."""

    id: int
    push_status: str
    push_attempts: int
    message: str


# ============================================================================
# Audit
# ============================================================================

class AuditTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    detection_id: int
    face_thumbnail_path: Optional[str] = None
    matched_name: Optional[str] = None
    confidence: Optional[float] = None
    tier: Optional[str] = None
    camera_name: Optional[str] = None
    detected_at: Optional[datetime] = None
    contact_id: Optional[int] = None


class AuditActionRequest(BaseModel):
    contact_id: Optional[int] = None


# ============================================================================
# Uploads
# ============================================================================

class FaceUploadResponse(BaseModel):
    faces_detected: int
    quality_passed: int
    quality_failed: int
    tasks_created: int
    auto_logged: int
    skipped: int
    deduplicated: int
