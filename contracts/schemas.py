"""
Shared Pydantic schemas used across backend agents.
These define the contracts between Recognition Pipeline, Task Worker, and API.
"""
from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, Field


# ============================================================================
# Contract 1: Face Detection -> Queue
# ============================================================================

class DetectionPayload(BaseModel):
    """Produced by Recognition Pipeline, consumed by Queue Worker."""
    camera_id: int
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    face_snapshot_path: str  # /storage/faces/YYYY/MM/DD/{uuid}_full.jpg
    face_thumbnail_path: str  # /storage/faces/YYYY/MM/DD/{uuid}_thumb.jpg
    quality_passed: bool = True
    quality_reason: Optional[str] = None
    # Compreface results (if recognition already ran)
    compreface_subject_id: Optional[str] = None
    similarity_score: Optional[float] = None
    tier: Optional[Literal["100", "91-99", "below90", "unknown"]] = None


# ============================================================================
# Contract 2: Task Action -> Compreface Update
# ============================================================================

class EnrollmentPayload(BaseModel):
    """Produced by Task Worker, consumed by Recognition Pipeline via queue."""
    action: Literal["enroll", "retrain", "delete_subject"]
    member_id: int
    compreface_subject_id: Optional[str] = None  # null if new enrollment
    face_snapshot_path: str  # source image for training


# ============================================================================
# Contract 3: SSE Event Stream
# ============================================================================

class SSETaskEvent(BaseModel):
    """Shape of SSE events broadcast to frontend."""
    event: Literal["task.created", "task.updated", "task.resolved", "audit.available", "heartbeat", "queue.saturated", "safe_mode.changed"]
    data: dict


# ============================================================================
# Contract 4: Admin Push Diff
# ============================================================================

class AttendeeSummary(BaseModel):
    member_id: int
    name: str
    detected_at: Optional[datetime] = None
    camera_name: Optional[str] = None
    included: bool = True  # admin can uncheck before push


class PushDiff(BaseModel):
    """Response shown to admin before pushing attendance to CiviCRM."""
    event_id: int
    event_title: str
    will_attend: List[AttendeeSummary]
    missing: List[AttendeeSummary]  # RSVP'd but not detected
    duplicates_warn: List[str]  # member names with multiple detections


# ============================================================================
# Auth Contracts
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
    id: int
    email: str
    name: Optional[str] = None
    role: str
    is_active: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class AddVolunteerRequest(BaseModel):
    email: str
    name: str
    role: Literal["volunteer", "admin"] = "volunteer"
    temporary_password: str


# ============================================================================
# Task Contracts
# ============================================================================

class TaskResponse(BaseModel):
    id: int
    detection_id: int
    status: str
    required_approvals: int
    current_approvals: int
    skip_count: int
    skip_reasons: List[str]
    tier: Optional[str]
    confidence: Optional[float]
    matched_name: Optional[str]
    face_thumbnail_path: Optional[str]
    camera_name: Optional[str]
    detected_at: Optional[datetime]
    expiry_date: datetime
    enrollment_progress: Optional[str] = None  # "2/3" etc


class TaskActionRequest(BaseModel):
    member_id: Optional[int] = None
    reason: Optional[str] = None


class PaginatedTaskResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[TaskResponse]


# ============================================================================
# Setup & Settings Contracts
# ============================================================================

class SetupStatusResponse(BaseModel):
    setup_complete: bool


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
    cameras: List[dict] = Field(default_factory=list)
    jwt_secret: Optional[str] = None


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
    settings: dict  # key -> value mapping


# ============================================================================
# Health Check
# ============================================================================

class HealthCheck(BaseModel):
    status: Literal["ok", "degraded", "down"]
    postgres: bool
    redis: bool
    compreface: bool
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Upload Contracts
# ============================================================================

class FaceUploadResponse(BaseModel):
    faces_detected: int
    quality_passed: int
    quality_failed: int
    tasks_created: int
    auto_logged: int
    skipped: int
    deduplicated: int
