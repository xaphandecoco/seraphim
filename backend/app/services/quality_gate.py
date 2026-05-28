from typing import Tuple

import cv2
import numpy as np


class FaceQualityGate:
    MIN_FACE_SIZE = 100
    MIN_BLUR_SCORE = 100.0

    @staticmethod
    def check(face_crop: np.ndarray) -> Tuple[bool, str]:
        h, w = face_crop.shape[:2]
        if h < FaceQualityGate.MIN_FACE_SIZE or w < FaceQualityGate.MIN_FACE_SIZE:
            return False, "Face too small"
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        blur = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur < FaceQualityGate.MIN_BLUR_SCORE:
            return False, "Face too blurry"
        return True, "OK"
