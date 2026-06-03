import logging
from dataclasses import dataclass
from typing import List, Literal, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.config import dynamic_settings

logger = logging.getLogger(__name__)


@dataclass
class RecognitionResult:
    subject_id: Optional[str]
    similarity_score: Optional[float]
    tier: Optional[Literal["100", "91-99", "below90", "unknown"]]
    box: Optional[dict]


@dataclass
class Subject:
    subject: str


def _map_tier(
    similarity: Optional[float],
    high: float = 0.98,
    medium: float = 0.91,
) -> Literal["100", "91-99", "below90", "unknown"]:
    """Map a CompreFace similarity score to a confidence tier.

    Thresholds default to the domain values (≥0.98 → "100", ≥0.91 → "91-99")
    but callers pass the admin-configurable thresholds from dynamic_settings so
    the tuning surfaced in the Settings UI actually takes effect.
    """
    if similarity is None:
        return "unknown"
    if similarity >= high:
        return "100"
    if similarity >= medium:
        return "91-99"
    return "below90"


class ComprefaceClient:
    def __init__(self):
        self.base_url = dynamic_settings.get_compreface_url().rstrip("/")
        # Legacy single key — used as fallback when the service-specific keys are blank.
        _legacy_key = dynamic_settings.get_compreface_api_key()
        # Detection Service key (POST /api/v1/detection/detect).
        # Falls back to the legacy key so single-key installs (Recognition-only
        # service) are unaffected.
        self.detect_api_key = (
            dynamic_settings.get_compreface_detect_api_key() or _legacy_key
        )
        # Recognition Service key (POST /api/v1/recognition/*, subject CRUD).
        self.recognize_api_key = (
            dynamic_settings.get_compreface_recognize_api_key() or _legacy_key
        )
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def detect(self, image_bytes: bytes) -> List[dict]:
        """Detect faces in image. Returns list of face boxes."""
        url = f"{self.base_url}/api/v1/detection/detect"
        headers = {"x-api-key": self.detect_api_key}
        files = {"file": ("image.jpg", image_bytes, "image/jpeg")}
        resp = await self._client.post(url, headers=headers, files=files)
        resp.raise_for_status()
        data = resp.json()
        faces = []
        for item in data.get("result", []):
            box = item.get("box", {})
            if box:
                faces.append({
                    "x": box.get("x_min", 0),
                    "y": box.get("y_min", 0),
                    "w": box.get("x_max", 0) - box.get("x_min", 0),
                    "h": box.get("y_max", 0) - box.get("y_min", 0),
                    "probability": item.get("probability"),
                })
        return faces

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def recognize(self, image_bytes: bytes) -> RecognitionResult:
        url = f"{self.base_url}/api/v1/recognition/recognize"
        headers = {"x-api-key": self.recognize_api_key}
        files = {"file": ("image.jpg", image_bytes, "image/jpeg")}
        resp = await self._client.post(url, headers=headers, files=files)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("result", [])
        if not results:
            return RecognitionResult(subject_id=None, similarity_score=None, tier="unknown", box=None)

        top = results[0]
        box = top.get("box", {})
        subjects = top.get("subjects", [])

        norm_box = None
        if box:
            norm_box = {
                "x": box.get("x_min", 0),
                "y": box.get("y_min", 0),
                "w": box.get("x_max", 0) - box.get("x_min", 0),
                "h": box.get("y_max", 0) - box.get("y_min", 0),
            }

        if not subjects:
            return RecognitionResult(subject_id=None, similarity_score=None, tier="unknown", box=norm_box)

        best = subjects[0]
        similarity = best.get("similarity")
        subject_id = best.get("subject")
        tier = _map_tier(
            similarity,
            dynamic_settings.get_similarity_threshold_high(),
            dynamic_settings.get_similarity_threshold_medium(),
        )

        return RecognitionResult(
            subject_id=subject_id,
            similarity_score=similarity,
            tier=tier,
            box=norm_box,
        )

    async def add_subject(self, subject_id: str) -> bool:
        url = f"{self.base_url}/api/v1/recognition/subjects"
        headers = {"x-api-key": self.recognize_api_key}
        resp = await self._client.post(url, headers=headers, json={"subject": subject_id})
        return resp.status_code in (200, 201)

    async def add_example(self, subject_id: str, image_bytes: bytes) -> bool:
        url = f"{self.base_url}/api/v1/recognition/faces?subject={subject_id}"
        headers = {"x-api-key": self.recognize_api_key}
        files = {"file": ("image.jpg", image_bytes, "image/jpeg")}
        resp = await self._client.post(url, headers=headers, files=files)
        return resp.status_code in (200, 201)

    async def list_subjects(self) -> List[Subject]:
        url = f"{self.base_url}/api/v1/recognition/subjects"
        headers = {"x-api-key": self.recognize_api_key}
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [Subject(subject=s) for s in data.get("subjects", [])]

    async def delete_subject(self, subject_id: str) -> bool:
        url = f"{self.base_url}/api/v1/recognition/subjects/{subject_id}"
        headers = {"x-api-key": self.recognize_api_key}
        resp = await self._client.delete(url, headers=headers)
        return resp.status_code in (200, 204)
