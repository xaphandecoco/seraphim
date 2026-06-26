import re
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator, model_validator


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

EVENT_TYPE_VALUES = Literal[
    "Sunday Celebration",
    "Prayer Meeting",
    "Powerhouse",
    "Community Meeting",
    "Conference",
    "Event",
]

SESSION_TIME_VALUES = Literal["8AM", "10AM", "3PM"]


class EventCreate(BaseModel):
    title: str
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    external_id: Optional[int] = None
    event_type: Optional[EVENT_TYPE_VALUES] = None
    session_time: Optional[SESSION_TIME_VALUES] = None
    occurrence_date: Optional[date] = None
    location: Optional[str] = None
    recurring_series_id: Optional[int] = None
    is_active: bool = False


class EventUpdate(BaseModel):
    title: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    external_id: Optional[int] = None
    event_type: Optional[EVENT_TYPE_VALUES] = None
    session_time: Optional[SESSION_TIME_VALUES] = None
    occurrence_date: Optional[date] = None
    location: Optional[str] = None
    recurring_series_id: Optional[int] = None
    is_active: Optional[bool] = None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    external_id: Optional[int] = None
    event_type: Optional[str] = None
    session_time: Optional[str] = None
    occurrence_date: Optional[date] = None
    location: Optional[str] = None
    recurring_series_id: Optional[int] = None
    is_active: Optional[bool] = None
    created_at: Optional[datetime] = None


class ParticipantCounts(BaseModel):
    unique_count: int = 0
    total_count: int = 0
    present: int = 0
    absent: int = 0
    unknown: int = 0


class EventDetailResponse(EventResponse):
    participant_counts: ParticipantCounts = ParticipantCounts()


class PaginatedEventResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[EventResponse]


class EventSeriesCreate(BaseModel):
    name: str
    event_type: EVENT_TYPE_VALUES
    default_session_time: Optional[SESSION_TIME_VALUES] = None
    default_location: Optional[str] = None
    is_active: bool = True


class EventSeriesUpdate(BaseModel):
    name: Optional[str] = None
    event_type: Optional[EVENT_TYPE_VALUES] = None
    default_session_time: Optional[SESSION_TIME_VALUES] = None
    default_location: Optional[str] = None
    is_active: Optional[bool] = None


class EventSeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    event_type: str
    default_session_time: Optional[str] = None
    default_location: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None


class EventParticipantItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    participant_id: int
    contact_id: Optional[int] = None
    status: str
    source: str
    role: Optional[str] = None
    created_at: datetime
    # source fields drawn from the joined Contact; excluded from serialized output
    nickname: Optional[str] = Field(default=None, exclude=True)
    first_name: Optional[str] = Field(default=None, exclude=True)
    last_name: Optional[str] = Field(default=None, exclude=True)

    @computed_field
    @property
    def contact_display_name(self) -> Optional[str]:
        if self.nickname:
            return self.nickname
        full = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return full or None


class ParticipantListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[EventParticipantItem]


class ParticipantManualAdd(BaseModel):
    contact_id: int
    status: str = "present"
    role: Optional[str] = None


class ParticipantStatusUpdate(BaseModel):
    status: str
    role: Optional[str] = None


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
# Contacts (CRUD)
# ============================================================================

class ContactCore(BaseModel):
    """Base fields shared by ContactCreate and sub-models."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nickname: Optional[str] = None
    suffix: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    street_address: Optional[str] = None
    contact_type: Literal["individual", "household", "organization"] = "individual"
    contact_subtype: Optional[str] = None

    @model_validator(mode="after")
    def validate_name_rules(self) -> "ContactCore":
        """Require first+last for individual; allow name-only for household/org."""
        if self.contact_type == "individual":
            if not self.first_name or not self.last_name:
                raise ValueError(
                    "first_name and last_name are required for individual contacts"
                )
        else:
            # household / organization: require at least one of the name fields
            if not any([self.first_name, self.last_name, self.nickname]):
                raise ValueError(
                    "At least one of first_name, last_name, or nickname is required"
                )
        return self

    @field_validator("birth_date", mode="before")
    @classmethod
    def reject_future_birth_date(cls, v: Any) -> Any:
        if v is None:
            return v
        # Accept date or ISO string
        if isinstance(v, str):
            try:
                from datetime import date as _date
                v = _date.fromisoformat(v)
            except ValueError:
                raise ValueError("birth_date must be a valid ISO date (YYYY-MM-DD)")
        if isinstance(v, date) and not isinstance(v, datetime):
            if v > date.today():
                raise ValueError("birth_date must not be in the future")
        return v


class ContactCreate(ContactCore):
    """Payload for POST /contacts."""
    custom_data: Dict[str, Any] = {}
    external_id: Optional[int] = None


class ContactUpdate(BaseModel):
    """Payload for PATCH /contacts/{id} — all fields optional; no external_id."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nickname: Optional[str] = None
    suffix: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    street_address: Optional[str] = None
    contact_type: Optional[Literal["individual", "household", "organization"]] = None
    contact_subtype: Optional[str] = None
    custom_data: Optional[Dict[str, Any]] = None

    @field_validator("birth_date", mode="before")
    @classmethod
    def reject_future_birth_date(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, str):
            try:
                from datetime import date as _date
                v = _date.fromisoformat(v)
            except ValueError:
                raise ValueError("birth_date must be a valid ISO date (YYYY-MM-DD)")
        if isinstance(v, date) and not isinstance(v, datetime):
            if v > date.today():
                raise ValueError("birth_date must not be in the future")
        return v


class ContactReferenceChip(BaseModel):
    """Compact contact representation for contact_reference custom fields."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    contact_type: str


class DerivedBadges(BaseModel):
    """Snapshot-column derived status badges — all nullable (NULL passthrough)."""
    tier: Optional[str] = None
    is_active: Optional[bool] = None
    is_regular: Optional[bool] = None
    is_connected: Optional[bool] = None


class FaceSummary(BaseModel):
    """Face enrollment summary for a contact."""
    enrolled: bool = False
    sample_count: int = 0
    face_thumbnail_path: Optional[str] = None


class ContactListItem(BaseModel):
    """Contact row returned in paginated list responses."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_type: str
    contact_subtype: Optional[str] = None
    tier: Optional[str] = None
    is_regular: Optional[bool] = None
    is_connected: Optional[bool] = None
    face_thumbnail_path: Optional[str] = None


class PaginatedContactResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ContactListItem]


class ContactDetailResponse(BaseModel):
    """Full contact detail including enrichment."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    external_id: Optional[int] = None
    contact_type: str
    contact_subtype: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nickname: Optional[str] = None
    suffix: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    street_address: Optional[str] = None
    custom_data: Dict[str, Any] = {}
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime
    # Snapshot columns
    last_attended_at: Optional[datetime] = None
    attendance_count: Optional[int] = None
    weeks_absent: Optional[int] = None
    # Enrichment
    contact_reference_chips: List[ContactReferenceChip] = []
    face_summary: FaceSummary = FaceSummary()
    derived_badges: DerivedBadges = DerivedBadges()
    # Consent status (T08) — computed from biometric_consent table
    # Values: 'none' | 'pending' | 'pre_cutover' | 'given'
    consent_status: str = "none"
    # Non-blocking warnings from create/update operations (F02)
    warnings: List[str] = []


class ContactAttendanceItem(BaseModel):
    """One attendance record for the contact attendance history endpoint."""
    model_config = ConfigDict(from_attributes=True)
    participant_id: int
    event_id: int
    event_title: str
    start_at: Optional[datetime] = None
    status: str
    source: str
    role: Optional[str] = None
    created_at: datetime
    event_type: Optional[str] = None


class PaginatedAttendanceResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ContactAttendanceItem]


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


# ============================================================================
# S05 — Bulk Participant Operations & Export Jobs
# ============================================================================

class AudienceSelector(BaseModel):
    """Selects a set of contacts to act on in bulk participant operations."""
    mode: Literal["all", "group", "saved_search", "ids"]
    group_id: Optional[int] = None
    saved_search_id: Optional[int] = None
    contact_ids: Optional[List[int]] = Field(default=None, max_length=50_000)
    include_deleted: bool = False

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "AudienceSelector":
        if self.mode == "group" and self.group_id is None:
            raise ValueError("group_id is required when mode is 'group'")
        if self.mode == "saved_search" and self.saved_search_id is None:
            raise ValueError("saved_search_id is required when mode is 'saved_search'")
        if self.mode == "ids" and not self.contact_ids:
            raise ValueError("contact_ids is required and must be non-empty when mode is 'ids'")
        return self


class BulkParticipantAddRequest(BaseModel):
    audience: AudienceSelector
    status: Literal["attended", "registered", "no_show", "cancelled"] = "registered"
    source: Literal["manual", "import", "bulk"] = "bulk"


class BulkParticipantStatusRequest(BaseModel):
    audience: AudienceSelector
    status: Literal["attended", "registered", "no_show", "cancelled"]


class BulkParticipantRemoveRequest(BaseModel):
    audience: AudienceSelector


class BulkParticipantPreviewRequest(BaseModel):
    audience: AudienceSelector


class BulkParticipantResult(BaseModel):
    inserted: int = 0
    skipped: int = 0
    matched: int = 0
    updated: int = 0


class BulkParticipantPreview(BaseModel):
    contact_count: int
    already_participating: int


class ExportJobCreate(BaseModel):
    event_id: Optional[int] = None
    export_type: str
    filters: Optional[Dict[str, Any]] = None


class ExportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: Optional[int] = None
    export_type: str
    status: str
    filters: Optional[Dict[str, Any]] = None
    file_path: Optional[str] = None
    row_count: Optional[int] = None
    error_message: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


# ============================================================================
# S22 — Name-List Intake & Matching
# ============================================================================

class NameListIntakeRequest(BaseModel):
    event_id: int
    names: List[str] = Field(min_length=1, max_length=500)
    source: str = "name_list"
    community_report_id: Optional[int] = None


class MatchCandidateSchema(BaseModel):
    """One fuzzy-match candidate returned for a single input name."""
    contact_id: int
    display_name: str
    score: float
    match_tier: Literal["exact", "high", "medium", "low"]


class NameMatchResultItem(BaseModel):
    """Per-name result entry inside NameListIntakeResponse."""
    input_name: str
    status: Literal["matched", "review", "unmatched"]
    matched_contact_id: Optional[int] = None
    matched_contact_name: Optional[str] = None
    candidates: List[MatchCandidateSchema] = []


class NameListIntakeResponse(BaseModel):
    event_id: int
    total: int = 0
    matched: int = 0
    skipped_existing: int = 0
    review_queue: int = 0
    # Set to True when >50 names are queued for async Claude processing (spec sec4.4 step5)
    claude_pending: Optional[bool] = None
    results: List[NameMatchResultItem] = []


# ============================================================================
# S22 — Review Queue
# ============================================================================

class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: int
    input_name: str
    status: Literal["pending", "resolved", "skipped"]
    candidates: List[MatchCandidateSchema] = []
    resolved_contact_id: Optional[int] = None
    # Populated by router serializer; not an ORM attribute
    event_title: Optional[str] = None
    resolved_contact_name: Optional[str] = None
    submitted_by_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PaginatedReviewQueueResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ReviewQueueItem]


class ReviewQueueResolveRequest(BaseModel):
    contact_id: Optional[int] = None
    action: Literal["accept", "skip", "create"]


# ============================================================================
# S22 — Name Aliases
# ============================================================================

class NameAliasCreate(BaseModel):
    alias_name: str = Field(min_length=1, max_length=255)
    contact_id: int


class NameAliasUpdate(BaseModel):
    alias_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    contact_id: Optional[int] = None
    is_active: Optional[bool] = None


class NameAliasTeach(BaseModel):
    """Teach the matcher a new alias from a resolved review-queue item."""
    review_queue_item_id: int
    contact_id: int


class NameAliasResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alias_name: str
    contact_id: int
    is_active: bool
    # Populated by router serializer; not an ORM attribute
    contact_display_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PaginatedNameAliasResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[NameAliasResponse]


# ============================================================================
# S22 — Community Reports
# ============================================================================

class CommunityReportCreate(BaseModel):
    event_id: Optional[int] = None
    event_title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    date_of_activity: date
    zone: Optional[str] = Field(default=None, max_length=100)
    topics: Optional[str] = None
    prayer_items: Optional[str] = None
    remarks: Optional[str] = None
    attendee_names: List[str] = Field(default_factory=list)
    event_leader_name: Optional[str] = Field(default=None, max_length=255)
    photo_paths: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_event_id_or_event_title(self) -> "CommunityReportCreate":
        if self.event_id is None and not self.event_title:
            raise ValueError("Either event_id or event_title must be provided")
        return self


class CommunityReportUpdate(BaseModel):
    status: Optional[str] = None
    zone: Optional[str] = Field(default=None, max_length=100)
    topics: Optional[str] = None
    prayer_items: Optional[str] = None
    remarks: Optional[str] = None
    event_id: Optional[int] = None
    event_title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    date_of_activity: Optional[date] = None


class CommunityReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: Optional[int] = None
    event_title: Optional[str] = None
    date_of_activity: Optional[date] = None
    zone: Optional[str] = None
    topics: Optional[str] = None
    prayer_items: Optional[str] = None
    remarks: Optional[str] = None
    attendee_names: List[str] = []
    event_leader_name: Optional[str] = None
    event_leader_contact_id: Optional[int] = None
    photo_paths: List[str] = []
    match_status: str = "pending"
    matched_count: int = 0
    review_count: int = 0
    status: str = "pending"
    submitted_by_id: Optional[int] = None
    submitted_by_contact_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    # Populated by router serializer; not an ORM attribute
    submitted_by_name: Optional[str] = None


class CommunityReportDetailResponse(CommunityReportResponse):
    """Extended community report response including review queue items and matched contacts."""
    review_queue_items: List[ReviewQueueItem] = []
    matched_contacts: List[Any] = []


class PaginatedCommunityReportResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[CommunityReportResponse]


# ============================================================================
# S30 — CiviCRM Migration Import
# ============================================================================

class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_filename: Optional[str] = None
    entity: str
    mode: str
    status: str
    column_map: Dict[str, Any]
    options: Dict[str, Any]
    total_rows: int
    created_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    review_count: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    created_by_id: Optional[int] = None


class ImportBatchListResponse(BaseModel):
    items: List[ImportBatchOut]
    total: int
    limit: int
    offset: int


class ImportBatchDetailResponse(ImportBatchOut):
    pending_review_count: int


class ImportRowResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    row_number: int
    external_id: Optional[str] = None
    outcome: str
    entity_id: Optional[int] = None
    message: Optional[str] = None
    created_at: datetime


class ImportRowResultListResponse(BaseModel):
    items: List[ImportRowResultOut]
    total: int
    limit: int
    offset: int


class MigrationSummaryResponse(BaseModel):
    contacts: Optional[ImportBatchOut] = None
    events: Optional[ImportBatchOut] = None
    participants: Optional[ImportBatchOut] = None
    links: Optional[ImportBatchOut] = None
    pending_reviews: int


# ============================================================================
# S16 — Scheduler / Settings / System Status
# ============================================================================


class JobRunItem(BaseModel):
    """One job_runs row for the /settings/jobs list."""
    id: int
    job_name: str
    status: str
    detail: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class JobRunsResponse(BaseModel):
    items: List[JobRunItem]
    total: int
    page: int
    page_size: int


class SystemStatusServicesDetail(BaseModel):
    postgres: bool
    redis: bool
    compreface: bool


class SystemStatusJobItem(BaseModel):
    job_name: str
    last_status: Optional[str] = None
    last_run_at: Optional[datetime] = None


class SystemStatusResponse(BaseModel):
    overall: Literal["ok", "degraded", "down"]
    services: SystemStatusServicesDetail
    queue_depth: int
    safe_mode: bool
    active_event_id: Optional[int] = None
    jobs: List[SystemStatusJobItem]


class ConfigChecklistItem(BaseModel):
    key: str
    label: str
    is_set: bool
    required: bool


class ConfigChecklistResponse(BaseModel):
    items: List[ConfigChecklistItem]
    required_complete: bool
    recommended_complete: bool
    all_complete: bool


class SettingKeyUpdateRequest(BaseModel):
    """Payload for PUT /settings/{key}."""
    value: Optional[str] = None


class SettingKeyValueResponse(BaseModel):
    key: str
    value: Optional[str] = None


class ConnectionTestResult(BaseModel):
    """Result of a POST /settings/test/{service} probe."""
    ok: bool
    detail: str = ""


# ============================================================================
# S08 — Biometric Consent & RTBF
# ============================================================================


class ConsentResponse(BaseModel):
    """Full consent status response — never 404."""
    status: str  # none | pending | given | revoked | purged
    consent_given: bool = False
    consented_at: Optional[datetime] = None
    basis_note: Optional[str] = None
    retention_until: Optional[datetime] = None
    deletion_requested_at: Optional[datetime] = None
    purged_at: Optional[datetime] = None
    recorded_by_id: Optional[int] = None
    enrolled_photo_count: int = 0
    subject_active: bool = False


class ConsentRecordRequest(BaseModel):
    """Body for POST /biometric/contacts/{id}/consent."""
    basis_note: Optional[str] = None
    retention_years: Optional[int] = None


class ConsentUpdateRequest(BaseModel):
    """Body for PATCH /biometric/contacts/{id}/consent."""
    basis_note: Optional[str] = None
    retention_until: Optional[datetime] = None


class DeletionRequest(BaseModel):
    """Body for POST /biometric/contacts/{id}/deletion-request."""
    immediate: Optional[bool] = False


class PurgeDetail(BaseModel):
    files_deleted: int = 0
    samples_deleted: int = 0
    detections_cleared: int = 0
    compreface_deleted: bool = False
    errors: List[str] = []


class PurgeResultResponse(BaseModel):
    status: str  # purged | already_purged
    purge_detail: PurgeDetail


class RetentionReportItem(BaseModel):
    contact_id: int
    contact_name: str
    consent_given: bool
    retention_until: Optional[datetime] = None
    deletion_requested_at: Optional[datetime] = None
    purged_at: Optional[datetime] = None
    status: str


class RetentionReportResponse(BaseModel):
    items: List[RetentionReportItem]
    total: int
    page: int
    page_size: int


# ============================================================================
# S09 — Advanced Search, Saved Searches & Smart Groups
# ============================================================================


class SearchRequest(BaseModel):
    """Body for POST /search."""
    criteria: Optional[Dict[str, Any]] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    sort: Optional[str] = None
    include_deleted: bool = False


class ValidateRequest(BaseModel):
    """Body for POST /search/validate."""
    criteria: Dict[str, Any]


class FieldSpecOut(BaseModel):
    """One entry in the field registry response."""
    key: str
    label: str
    kind: str          # core | derived | custom
    type: str          # string | int | number | bool | date | datetime | enum | multiselect
    ops: List[str]
    options: Optional[List[Dict[str, str]]] = None
    nullable: bool


class FieldRegistryResponse(BaseModel):
    fields: List[FieldSpecOut]


class SavedSearchCreate(BaseModel):
    name: str = Field(max_length=120)
    entity: str = "contact"
    criteria: Dict[str, Any]


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    criteria: Optional[Dict[str, Any]] = None


class SavedSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: int
    name: str
    entity: str
    criteria: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class GroupCreate(BaseModel):
    name: str = Field(max_length=120)
    entity: str = "contact"
    group_type: Literal["smart", "static"]
    criteria: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def smart_requires_criteria(self) -> "GroupCreate":
        if self.group_type == "smart" and not self.criteria:
            raise ValueError("criteria is required for smart groups")
        return self


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    criteria: Optional[Dict[str, Any]] = None


class GroupResponse(BaseModel):
    """Group row returned by GET /groups and friends."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    entity: str
    group_type: str
    criteria: Optional[Dict[str, Any]] = None
    owner_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    member_count: Optional[int] = None  # None when with_counts=false


class GroupMemberAdd(BaseModel):
    contact_ids: List[int] = Field(max_length=500)


class PopulateRequest(BaseModel):
    criteria: Optional[Dict[str, Any]] = None
    saved_search_id: Optional[int] = None
    mode: Literal["replace", "append"] = "append"


class PromoteRequest(BaseModel):
    group_name: str = Field(max_length=120)
    entity: str = "contact"
