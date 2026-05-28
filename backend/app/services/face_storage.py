import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np


class FaceStorage:
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    async def save_detection(self, frame: np.ndarray, face_box: dict) -> Tuple[str, str]:
        """
        Crop face from frame, save full + thumbnail.
        Returns (full_path, thumb_path) relative to storage root.
        """
        x, y, w, h = face_box['x'], face_box['y'], face_box['w'], face_box['h']
        face_crop = frame[y:y+h, x:x+w]

        now = datetime.now(timezone.utc)
        uuid_str = str(uuid.uuid4())[:8]
        rel_dir = f"faces/{now.year}/{now.month:02d}/{now.day:02d}"
        dir_path = self.base_path / rel_dir
        dir_path.mkdir(parents=True, exist_ok=True)

        full_path = dir_path / f"det_{int(now.timestamp())}_{uuid_str}_full.jpg"
        thumb_path = dir_path / f"det_{int(now.timestamp())}_{uuid_str}_thumb.jpg"

        # Save full quality
        cv2.imwrite(str(full_path), face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # Save thumbnail 300x300
        thumb = cv2.resize(face_crop, (300, 300), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(thumb_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 70])

        return str(full_path.relative_to(self.base_path)), str(thumb_path.relative_to(self.base_path))

    def read_detection_full_image(self, thumb_path: str) -> Optional[bytes]:
        """Given a thumb path, read the corresponding full image bytes."""
        full_path = Path(str(self.base_path / thumb_path).replace("_thumb.jpg", "_full.jpg"))
        try:
            return full_path.read_bytes()
        except FileNotFoundError:
            return None

    async def save_enrollment(self, subject_id: str, face_crop: np.ndarray) -> Tuple[str, str]:
        """Save enrolled face sample. Returns (full_path, thumb_path)."""
        dir_path = self.base_path / "enrolled" / subject_id
        dir_path.mkdir(parents=True, exist_ok=True)

        existing = list(dir_path.glob("sample_*_full.jpg"))
        idx = len(existing) + 1

        full_path = dir_path / f"sample_{idx:03d}_full.jpg"
        thumb_path = dir_path / f"sample_{idx:03d}_thumb.jpg"

        cv2.imwrite(str(full_path), face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        thumb = cv2.resize(face_crop, (300, 300), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(thumb_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 70])

        return str(full_path.relative_to(self.base_path)), str(thumb_path.relative_to(self.base_path))
