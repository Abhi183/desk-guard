"""Recognises your face with OpenCV's SFace model, so the app can tell you from visitors.

Your profile is a gallery of up to 40 face vectors (128 numbers each) in face_profile.npy:
the first one from the initial learning, the rest picked up over time as the app sees
you from new angles and in new light. A face is you when it is close to any of them.
The file stays on this Mac and is git-ignored. No photo of you is stored.
"""

from collections.abc import Callable

import cv2
import numpy as np

from webcam_fun.identity import has_company, pick_me
from webcam_fun.vision import FACE_ID_MODEL, ROOT, Face, require_model

PROFILE_FILE = ROOT / "face_profile.npy"
EMBEDDING_SIZE = 128
# Cosine similarity to the closest gallery sample. See README "Your face" for the
# numbers these were chosen from.
MATCH_THRESHOLD = 0.5
GALLERY_MAX = 40
# A new sample joins the gallery when it is clearly you (GROW_MIN_SCORE to some sample,
# and not unlike the first sample: GROW_MIN_ANCHOR) yet adds something new
# (under GROW_MAX_SCORE to every sample). The anchor check stops the gallery drifting,
# one lookalike at a time, toward somebody else.
GROW_MIN_SCORE = 0.55
GROW_MAX_SCORE = 0.85
GROW_MIN_ANCHOR = 0.3
GROW_MIN_GAP_S = 1.0
ENROLL_SAMPLES = 20
ENROLL_MIN_GAP_S = 0.15
ENROLL_MAX_GAP_S = 2.0  # longer than this without a sample and learning starts over


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


class Enroller:
    """Collects face embeddings spread over a few seconds and averages them.

    A pause longer than max_gap_s throws the samples away: the person in the chair
    may have changed, and the profile must not be a mix of two people.
    """

    def __init__(self, samples: int = ENROLL_SAMPLES, min_gap_s: float = ENROLL_MIN_GAP_S,
                 max_gap_s: float = ENROLL_MAX_GAP_S) -> None:
        self._needed = samples
        self._min_gap_s = min_gap_s
        self._max_gap_s = max_gap_s
        self._samples: list[np.ndarray] = []
        self._last_at = float("-inf")

    @property
    def progress(self) -> float:
        return len(self._samples) / self._needed

    def add(self, embedding: np.ndarray, now: float) -> np.ndarray | None:
        """Returns the finished profile once enough samples are in, else None."""
        if now - self._last_at < self._min_gap_s:
            return None
        kept = self._samples if now - self._last_at <= self._max_gap_s else []
        self._samples = [*kept, embedding]
        self._last_at = now
        if len(self._samples) < self._needed:
            return None
        return _unit(np.mean(self._samples, axis=0))


def gallery_score(gallery: np.ndarray, embedding: np.ndarray) -> float:
    """How much a face looks like you: similarity to the closest gallery sample."""
    return float(np.max(gallery @ embedding))


def grown(gallery: np.ndarray, embedding: np.ndarray) -> np.ndarray | None:
    """The gallery with this sample added, or None when the sample doesn't qualify."""
    if len(gallery) >= GALLERY_MAX:
        return None
    if float(gallery[0] @ embedding) < GROW_MIN_ANCHOR:
        return None
    if not GROW_MIN_SCORE <= gallery_score(gallery, embedding) < GROW_MAX_SCORE:
        return None
    return np.vstack([gallery, embedding])


def load_profile() -> np.ndarray | None:
    """The saved gallery (rows of unit vectors), or None when there is no valid one."""
    try:
        profile = np.load(PROFILE_FILE, allow_pickle=False)
    except (OSError, ValueError):
        return None
    if profile.ndim == 1:  # file from before the gallery: a single sample
        profile = profile.reshape(1, -1)
    if (profile.ndim != 2 or profile.shape[1] != EMBEDDING_SIZE
            or not 1 <= len(profile) <= GALLERY_MAX or not np.all(np.isfinite(profile))):
        return None
    profile = profile.astype(np.float32)
    norms = np.linalg.norm(profile, axis=1, keepdims=True)
    if np.any(norms == 0):
        return None
    return profile / norms


def save_profile(profile: np.ndarray) -> str | None:
    """Returns an error message, or None on success."""
    try:
        np.save(PROFILE_FILE, profile)
        PROFILE_FILE.chmod(0o600)
    except OSError as e:
        return f"could not save your face profile: {e}"
    return None


def forget_profile() -> None:
    PROFILE_FILE.unlink(missing_ok=True)


class FaceId:
    """Says which detected faces are you. Learns your face the first time it sees you
    without company, then keeps adding samples as it sees you in new poses."""

    def __init__(self, on_learned: Callable[[str | None], None]) -> None:
        self._net = cv2.FaceRecognizerSF.create(str(require_model(FACE_ID_MODEL)), "")
        self._on_learned = on_learned
        self._gallery = load_profile()
        self._enroller = self._new_enroller()
        self._embed_failed = False
        self._scores: tuple[float, ...] = ()
        self._grew_at = float("-inf")

    @property
    def learning(self) -> bool:
        return self._gallery is None

    @property
    def progress(self) -> float:
        return 1.0 if self._gallery is not None else self._enroller.progress

    @property
    def samples(self) -> int:
        return 0 if self._gallery is None else len(self._gallery)

    def is_you(self, index: int) -> bool:
        """Whether face `index` of the last identify() call matched you (a photo of you
        held up next to you matches too, so more than one face can)."""
        if self._gallery is None:
            return index == 0
        return index < len(self._scores) and self._scores[index] >= MATCH_THRESHOLD

    def relearn(self) -> None:
        forget_profile()
        self._gallery = None
        self._enroller = self._new_enroller()

    @staticmethod
    def _new_enroller() -> Enroller:
        return Enroller(ENROLL_SAMPLES, ENROLL_MIN_GAP_S, ENROLL_MAX_GAP_S)

    def embed(self, frame: np.ndarray, face: Face) -> np.ndarray:
        aligned = self._net.alignCrop(frame, face.row)
        return _unit(self._net.feature(aligned).flatten())

    def identify(self, frame: np.ndarray, faces: list[Face], now: float) -> int | None:
        """Index into `faces` of your face, or None when you are not in the frame."""
        self._scores = ()
        try:
            if self._gallery is None:
                return self._identify_while_learning(frame, faces, now)
            embeddings = [self.embed(frame, face) for face in faces]
        except cv2.error as e:  # a bad crop must not stop the camera loop
            if not self._embed_failed:
                print(f"face recognition failed on a frame: {e}", flush=True)
                self._embed_failed = True
            return None
        self._scores = tuple(gallery_score(self._gallery, e) for e in embeddings)
        if now - self._grew_at >= GROW_MIN_GAP_S:
            for embedding in embeddings:
                if self.train(embedding):
                    self._grew_at = now
                    break
        return pick_me(list(self._scores), MATCH_THRESHOLD)

    def train(self, embedding: np.ndarray) -> bool:
        """Add a sample to the gallery if it qualifies (see grown). True when it was added."""
        if self._gallery is None:
            return False
        bigger = grown(self._gallery, embedding)
        if bigger is None:
            return False
        self._gallery = bigger
        error = save_profile(bigger)
        if error:
            print(error, flush=True)
        return True

    def _identify_while_learning(self, frame: np.ndarray, faces: list[Face],
                                 now: float) -> int | None:
        if not faces:
            return None  # a long absence restarts learning inside the Enroller
        if has_company([face.box for face in faces]):
            # Someone is sitting with you: start over rather than risk learning them.
            if self._enroller.progress > 0:
                self._enroller = self._new_enroller()
            return 0
        self._learn(self.embed(frame, faces[0]), now)  # faces in the background don't matter
        return 0  # until your face is known, the nearest face counts as you

    def _learn(self, embedding: np.ndarray, now: float) -> None:
        sample = self._enroller.add(embedding, now)
        if sample is None:
            return
        self._gallery = sample.reshape(1, -1)
        self._on_learned(save_profile(self._gallery))
