"""Camera and face detection helpers shared by the apps."""

import sys
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
FACE_MODEL = MODELS / "face_detection_yunet.onnx"
FACE_ID_MODEL = MODELS / "face_recognition_sface_2021dec.onnx"
GESTURE_MODEL = MODELS / "gesture_recognizer.task"
BODY_MODEL = MODELS / "selfie_segmenter.tflite"

Box = tuple[int, int, int, int]  # x, y, w, h


class Face(NamedTuple):
    box: Box
    row: np.ndarray  # raw YuNet row (box, 5 landmarks, score); face recognition aligns with it


def try_open_camera(index: int = 0, width: int = 640,
                    height: int = 480) -> cv2.VideoCapture | None:
    cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def open_camera(index: int = 0, width: int = 640, height: int = 480) -> cv2.VideoCapture:
    cap = try_open_camera(index, width, height)
    if cap is None:
        sys.exit("Could not open the webcam. Allow camera access for the app that started this "
                 "(Terminal or Desk Guard) in System Settings > Privacy & Security > Camera.")
    return cap


def require_model(path: Path) -> Path:
    if not path.exists():
        sys.exit(f"Missing model file {path}. See README.md for the download link.")
    return path


class FaceDetector:
    """YuNet face detector. Results come largest face first."""

    def __init__(self, score_threshold: float = 0.75, min_face_px: int = 18) -> None:
        self._net = cv2.FaceDetectorYN.create(
            str(require_model(FACE_MODEL)), "", (320, 240), score_threshold)
        self._min_face_px = min_face_px

    def detect_faces(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        self._net.setInputSize((w, h))
        _, rows = self._net.detect(frame)
        if rows is None:
            return []
        faces = [Face(tuple(int(v) for v in row[:4]), row) for row in rows
                 if min(row[2], row[3]) >= self._min_face_px]
        return sorted(faces, key=lambda f: f.box[2] * f.box[3], reverse=True)

    def detect(self, frame: np.ndarray) -> list[Box]:
        return [face.box for face in self.detect_faces(frame)]


def put_text(img: np.ndarray, text: str, org: tuple[int, int], color: tuple,
             scale: float = 0.6, thickness: int = 2) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)
