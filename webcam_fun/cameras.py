"""Which camera to watch with.

macOS gives every camera a unique ID that stays the same when it is unplugged and
plugged back in; the saved choice is that ID (or "auto"). OpenCV only takes a number,
which is the camera's position in AVCaptureDevice.devicesWithMediaType: (the same list
OpenCV itself enumerates), so the number is worked out fresh each time a camera opens.
"""

from typing import NamedTuple

from webcam_fun.settings import AUTO_CAMERA

BUILT_IN_TYPE = "AVCaptureDeviceTypeBuiltInWideAngleCamera"


class Camera(NamedTuple):
    uid: str
    name: str
    built_in: bool
    index: int  # OpenCV's number for it right now


def list_cameras() -> list[Camera]:
    """Connected cameras in OpenCV's order. Empty if AVFoundation is unavailable."""
    try:
        import AVFoundation
    except ImportError:
        return []
    devices = AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeVideo)
    return [Camera(str(d.uniqueID()), str(d.localizedName()),
                   str(d.deviceType()) == BUILT_IN_TYPE, i)
            for i, d in enumerate(devices or [])]


def choose(cameras: list[Camera], wanted: str) -> Camera | None:
    """The camera to use: the one you picked if it is connected, otherwise the automatic
    choice (an external camera if there is one, else the built-in camera)."""
    if not cameras:
        return None
    if wanted != AUTO_CAMERA:
        for camera in cameras:
            if camera.uid == wanted:
                return camera
    external = [c for c in cameras if not c.built_in]
    return (external or cameras)[0]


def describe(cameras: list[Camera]) -> list[dict]:
    """Plain data for the settings page."""
    return [{"id": c.uid, "name": c.name, "built_in": c.built_in} for c in cameras]
