"""
Queue producer — DEPRECATED.

The RTSP worker now creates Detection and Task records directly.
This module is kept for backwards compatibility during migration.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DetectionPayload(BaseModel):
    camera_id: int
    face_snapshot_path: str
    face_thumbnail_path: str
    compreface_subject_id: Optional[str]
    similarity_score: Optional[float]
    tier: str
    detected_at: datetime


async def enqueue_recognition(session, payload: DetectionPayload) -> None:
    """No-op: recognition tasks are created inline by the RTSP worker."""
    pass
