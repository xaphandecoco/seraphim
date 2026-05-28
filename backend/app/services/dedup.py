from collections import deque
from datetime import datetime, timezone
from typing import Dict, Optional

import cv2
import numpy as np


class DedupCache:
    WINDOW_SECONDS = 60

    def __init__(self):
        self._seen: Dict[str, datetime] = {}
        self._unknown_hashes: deque = deque(maxlen=100)  # recent unknown face hashes

    def is_duplicate(self, subject_id: Optional[str], face_crop: Optional[np.ndarray] = None) -> bool:
        now = datetime.now(timezone.utc)
        if subject_id:
            last = self._seen.get(subject_id)
            if last and (now - last).total_seconds() < self.WINDOW_SECONDS:
                return True
            self._seen[subject_id] = now
            return False
        # For unknowns, use perceptual hash of face crop
        if face_crop is not None:
            face_hash = self._hash_face(face_crop)
            for h, t in self._unknown_hashes:
                if h == face_hash and (now - t).total_seconds() < self.WINDOW_SECONDS:
                    return True
            self._unknown_hashes.append((face_hash, now))
        return False

    def _hash_face(self, face_crop: np.ndarray) -> str:
        # Simple hash: resize to 8x8, grayscale, compare to mean
        small = cv2.resize(face_crop, (8, 8), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean = gray.mean()
        bits = (gray > mean).flatten().tolist()
        return ''.join('1' if b else '0' for b in bits)

    def cleanup(self):
        now = datetime.now(timezone.utc)
        self._seen = {k: v for k, v in self._seen.items() if (now - v).total_seconds() < self.WINDOW_SECONDS}
        # Also clean up unknown_hashes deque
        self._unknown_hashes = deque(
            [(h, t) for h, t in self._unknown_hashes if (now - t).total_seconds() < self.WINDOW_SECONDS],
            maxlen=100
        )
