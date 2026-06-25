import re
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
    compreface_detect_api_key: Optional[str] = None
    compreface_recognize_api_key: Optional[str] = None


class ServiceTestResponse(BaseModel):
    compreface_ok: bool
    compreface_message: str = ""


# SetupRequest is defined later in this file (after CameraCreateRequest) so that
# the List[CameraCreateRequest] annotation resolves at class-body parse time
# without requiring a forward reference or model_rebuild().

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


class SetupRequest(BaseModel):
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    compreface_url: str
    compreface_api_key: str = ""
    compreface_detect_api_key: Optional[str] = None
    compreface_recognize_api_key: Optional[str] = None
    admin_email: str
    admin_password: str
    admin_name: str = "Admin"
    # Typed list ensures rtsp:// validation fires at setup time, not later in the worker.
    # Defined here (after CameraCreateRequest) to avoid a forward-reference NameError.
    cameras: List[CameraCreateRequest] = []
    jwt_secret: Optional[str] = None

    @field_validator("admin_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


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

class ParticipantRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contact_id: Optional[int] = None
    event_id: Optional[int] = None
    detection_id: Optional[int] = None
    status: str
    source: str
    created_at: datetime


# ============================================================================
# Events
# ============================================================================

class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


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


# ============================================================================
# Custom Fields
# ============================================================================

VALID_DATA_TYPES = Literal[
    "text", "textarea", "select", "multiselect",
    "date", "number", "checkbox", "contact_reference"
]


def _snake_case_name(v: str) -> str:
    if not re.match(r'^[a-z][a-z0-9_]*$', v):
        raise ValueError(
            "name must be snake_case: start with a-z, then a-z/0-9/_ only"
        )
    if len(v) > 100:
        raise ValueError("name must be <= 100 characters")
    return v


class OptionItem(BaseModel):
    value: str
    label: str


class CustomFieldDefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    name: str
    label: str
    data_type: str
    options: List[OptionItem]
    is_required: bool
    is_multi: bool
    weight: int
    is_active: bool
    help_text: Optional[str] = None


class CustomFieldGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    label: str
    entity: str
    weight: int
    is_active: bool
    fields: List[CustomFieldDefResponse] = []


class CustomFieldSchemaResponse(BaseModel):
    entity: str
    groups: List[CustomFieldGroupResponse]


class CustomFieldGroupCreate(BaseModel):
    name: str
    label: str
    entity: Literal["contact", "event", "activity"] = "contact"
    weight: int = 0

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _snake_case_name(v)


class CustomFieldGroupUpdate(BaseModel):
    """Mutable fields for PATCH /groups/{id}.

    name and entity are immutable — sending them causes a 422 (extra='forbid').
    """
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = None
    weight: Optional[int] = None
    is_active: Optional[bool] = None


class CustomFieldDefCreate(BaseModel):
    group_id: int
    name: str
    label: str
    data_type: VALID_DATA_TYPES
    options: List[OptionItem] = []
    is_required: bool = False
    is_multi: bool = False
    weight: int = 0
    help_text: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _snake_case_name(v)

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: List[OptionItem], info: Any) -> List[OptionItem]:
        data_type = info.data.get("data_type")
        if data_type in ("select", "multiselect") and not v:
            raise ValueError("options must be non-empty for select/multiselect fields")
        values = [opt.value for opt in v]
        if len(values) != len(set(values)):
            raise ValueError("option values must be unique within the field")
        return v


class CustomFieldDefUpdate(BaseModel):
    """Mutable fields for PATCH /defs/{id}.

    name and data_type are immutable — sending them causes a 422 (extra='forbid').
    """
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = None
    options: Optional[List[OptionItem]] = None
    is_required: Optional[bool] = None
    is_multi: Optional[bool] = None
    weight: Optional[int] = None
    is_active: Optional[bool] = None
    help_text: Optional[str] = None


class CustomDataValidateRequest(BaseModel):
    entity: str = "contact"
    custom_data: Dict[str, Any]


class CustomDataValidateResponse(BaseModel):
    custom_data: Dict[str, Any]
    normalized: bool = True


# Included in PATCH /defs response when options are removed
class CustomFieldDefPatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    name: str
    label: str
    data_type: str
    options: List[OptionItem]
    is_required: bool
    is_multi: bool
    weight: int
    is_active: bool
    help_text: Optional[str] = None
    affected_contacts: int = 0


# ============================================================================
# S07 — Face Enrollment & Photo Ingest
# ============================================================================


class FaceSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    compreface_subject_id: str
    contact_id: Optional[int] = None
    image_path: str
    thumb_path: Optional[str] = None
    thumb_url: Optional[str] = None
    compreface_image_id: Optional[str] = None
    source: str
    quality_score: Optional[float] = None
    created_at: datetime


class FacePanelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contact_id: Optional[int] = None
    subject_id: Optional[int] = None
    compreface_subject_id: Optional[str] = None
    enrollment_status: Optional[str] = None
    sample_count: int
    is_orphan: bool
    purged_at: Optional[datetime] = None
    last_trained_at: Optional[datetime] = None
    samples: List[FaceSampleResponse]


class RecognitionHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    contact_id: int
    status: str
    source: str
    detection_id: Optional[int] = None
    created_at: datetime


class RecognitionHistoryResponse(BaseModel):
    items: List[RecognitionHistoryItem]
    total: int
    page: int
    page_size: int


class PhotoIngestBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: Optional[int] = None
    status: str
    total_images: int
    processed_images: int
    faces_detected: int
    auto_logged: int
    tasks_created: int
    skipped: int
    deduplicated: int
    errors: int
    report: List[Dict[str, Any]]
    finished_at: Optional[datetime] = None
    created_at: datetime


# ============================================================================
# PIT Enrollment request body (S07)
# ============================================================================

class PitEnrollRequest(BaseModel):
    contact_id: int


class BackfillRequest(BaseModel):
    dry_run: bool = True


class BackfillReportResponse(BaseModel):
    orphans_found: int
    samples_seeded: int
    unregistered_cf_subjects: int
    dry_run: bool


# Used by enrollment router backfill endpoint
class BackfillResponse(BaseModel):
    subjects_scanned: int
    samples_found: int
    samples_pushed: int
    errors: int
    dry_run: bool


# Used by enrollment router retrain endpoint
class RetrainResponse(BaseModel):
    compreface_subject_id: str
    samples_pushed: int
    last_trained_at: Optional[datetime] = None
