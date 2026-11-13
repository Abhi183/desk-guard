"""Which camera to watch with.

macOS gives every camera a unique ID that stays the same when it is unplugged and
plugged back in; the saved choice is that ID (or "auto"), and avcamera.py opens cameras
by that ID, so you always get the camera you picked.
"""

from typing import NamedTuple

from webcam_fun.settings import AUTO_CAMERA

BUILT_IN_TYPE = "AVCaptureDeviceTypeBuiltInWideAngleCamera"


class Camera(NamedTuple):
    uid: str
    name: str
    built_in: bool
    continuity: bool = False  # an iPhone used as a webcam (Continuity Camera)


def list_cameras() -> list[Camera]:
    """Connected cameras. Empty if AVFoundation is unavailable."""
    try:
        import AVFoundation
    except ImportError:
        return []
    devices = AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeVideo)
    return [Camera(str(d.uniqueID()), str(d.localizedName()),
                   str(d.deviceType()) == BUILT_IN_TYPE,
                   bool(d.respondsToSelector_("isContinuityCamera") and d.isContinuityCamera()))
            for d in devices or []]


def automatic_rank(camera: Camera) -> int:
    """Automatic's preference: a plugged-in webcam, then the built-in camera, then an
    iPhone (it connects by itself whenever it is nearby, so it should never take over)."""
    if camera.continuity:
        return 2
    return 1 if camera.built_in else 0


def choose(cameras: list[Camera], wanted: str) -> Camera | None:
    """The camera you picked if it is connected, otherwise the automatic choice."""
    if not cameras:
        return None
    if wanted != AUTO_CAMERA:
        for camera in cameras:
            if camera.uid == wanted:
                return camera
    return min(cameras, key=automatic_rank)  # min() keeps the first of equal ranks


def describe(cameras: list[Camera]) -> list[dict]:
    """Plain data for the settings page."""
    return [{"id": c.uid, "name": c.name, "built_in": c.built_in, "continuity": c.continuity}
            for c in cameras]
