"""Tells whether a person's body is in a part of the frame, without needing a face.

Used by auto-lock for the moments your face can't be seen: head down on the desk,
turned right away, hidden behind a hand. MediaPipe's selfie segmenter marks which
pixels belong to a person; body_share() is the fraction of a zone it marks.
"""

import cv2
import mediapipe as mp
import numpy as np

from webcam_fun.vision import BODY_MODEL, require_model

PERSON_CONFIDENCE = 0.5
# Share of your zone that must be "person" for your body to count as there. Measured on
# real frames: 0.55-0.85 with you in the chair, 0.00 for an empty background.
BODY_MIN_SHARE = 0.35


class BodyDetector:
    def __init__(self) -> None:
        vision = mp.tasks.vision
        self._segmenter = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(require_model(BODY_MODEL))),
            running_mode=vision.RunningMode.IMAGE,
            output_confidence_masks=True, output_category_mask=False))

    def body_share(self, frame_bgr: np.ndarray, zone: tuple[int, int, int, int]) -> float:
        """Fraction (0..1) of the zone (left, top, right, bottom) covered by a person."""
        left, top, right, bottom = zone
        if right <= left or bottom <= top:
            return 0.0
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        mask = result.confidence_masks[-1].numpy_view()
        return float((mask[top:bottom, left:right] > PERSON_CONFIDENCE).mean())

    def close(self) -> None:
        self._segmenter.close()
