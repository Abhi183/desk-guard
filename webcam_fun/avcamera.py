"""Camera capture straight from AVFoundation, by the camera's unique ID.

OpenCV opens cameras by number, and its numbering doesn't match the order macOS lists
them in (picking the FaceTime camera could open an iPhone). Here a camera is opened by
its ID, so you always get the one you picked, and switching swaps the input inside the
running session instead of closing and reopening everything.

Frames arrive on a capture queue; only the newest is kept (read() hands it over), so a
slow frame never makes the video fall behind. AVFoundation scales every camera to
FRAME_WIDTH wide in hardware, keeping its shape.
"""

import threading

import AVFoundation
import CoreMedia
import cv2
import libdispatch
import numpy as np
import objc
import Quartz
from Foundation import NSObject

FRAME_WIDTH = 640
FIRST_FRAME_S = 4.0  # a camera that shows nothing for this long didn't open
READ_ONLY = 1        # kCVPixelBufferLock_ReadOnly
SampleDelegate = objc.protocolNamed("AVCaptureVideoDataOutputSampleBufferDelegate")


def _frame_size(device) -> tuple[int, int]:
    """FRAME_WIDTH wide, with the camera's own shape (4:3, 16:9...). Even numbers."""
    dims = CoreMedia.CMVideoFormatDescriptionGetDimensions(
        device.activeFormat().formatDescription())
    width, height = (dims.width, dims.height) if dims.width else (4, 3)
    return FRAME_WIDTH, int(round(FRAME_WIDTH * height / width / 2) * 2)


def _bgr(sample) -> np.ndarray | None:
    """Copy a camera sample (32-bit BGRA) into a BGR numpy frame."""
    pixels = CoreMedia.CMSampleBufferGetImageBuffer(sample)
    if pixels is None:
        return None
    Quartz.CVPixelBufferLockBaseAddress(pixels, READ_ONLY)
    try:
        width = Quartz.CVPixelBufferGetWidth(pixels)
        height = Quartz.CVPixelBufferGetHeight(pixels)
        row = Quartz.CVPixelBufferGetBytesPerRow(pixels)
        base = Quartz.CVPixelBufferGetBaseAddress(pixels)
        raw = np.frombuffer(base.as_buffer(row * height), np.uint8).reshape(height, row)
        bgra = raw[:, :width * 4].reshape(height, width, 4)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)  # copies, so we can unlock
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(pixels, READ_ONLY)


class _Sink(NSObject, protocols=[SampleDelegate]):
    def initWithOwner_(self, owner):
        self = objc.super(_Sink, self).init()
        if self is None:
            return None
        self.owner = owner
        return self

    def captureOutput_didOutputSampleBuffer_fromConnection_(self, _out, sample, _conn):
        frame = _bgr(sample)
        if frame is not None:
            self.owner._deliver(frame)


class AVCamera:
    """One capture session. read()/close() match LatestFrameCamera in engine.py."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._session = AVFoundation.AVCaptureSession.alloc().init()
        self._session.setSessionPreset_(AVFoundation.AVCaptureSessionPresetHigh)
        self._output = AVFoundation.AVCaptureVideoDataOutput.alloc().init()
        self._output.setAlwaysDiscardsLateVideoFrames_(True)
        self._sink = _Sink.alloc().initWithOwner_(self)
        self._queue = libdispatch.dispatch_queue_create(b"deskguard.camera", None)
        self._output.setSampleBufferDelegate_queue_(self._sink, self._queue)
        self._input = None
        self.uid: str | None = None

    # ---------- opening and switching ----------
    def open(self, uid: str) -> bool:
        """Start watching with camera `uid`. False if it isn't there or shows nothing."""
        device = AVFoundation.AVCaptureDevice.deviceWithUniqueID_(uid)
        if device is None:
            return False
        self._session.beginConfiguration()
        ok = self._use(device)
        if ok and self._session.canAddOutput_(self._output):
            self._session.addOutput_(self._output)
        self._session.commitConfiguration()
        if not ok:
            return False
        self._session.startRunning()
        return self._wait_for_frame(0)

    def switch_to(self, uid: str) -> bool:
        """Swap to camera `uid` without stopping the session. False keeps the old one."""
        device = AVFoundation.AVCaptureDevice.deviceWithUniqueID_(uid)
        if device is None:
            return False
        before = self._seq
        self._session.beginConfiguration()
        old, old_uid = self._input, self.uid
        if old is not None:
            self._session.removeInput_(old)
        ok = self._use(device)
        if not ok and old is not None:  # put the old camera back
            self._session.addInput_(old)
            self._input, self.uid = old, old_uid
        self._session.commitConfiguration()
        return ok and self._wait_for_frame(before)

    def _use(self, device) -> bool:
        new_input, error = AVFoundation.AVCaptureDeviceInput.deviceInputWithDevice_error_(
            device, None)
        if new_input is None or not self._session.canAddInput_(new_input):
            if error is not None:
                print(f"camera: cannot use {device.localizedName()}: {error}", flush=True)
            return False
        self._session.addInput_(new_input)
        width, height = _frame_size(device)
        self._output.setVideoSettings_({
            Quartz.kCVPixelBufferPixelFormatTypeKey: Quartz.kCVPixelFormatType_32BGRA,
            Quartz.kCVPixelBufferWidthKey: width,
            Quartz.kCVPixelBufferHeightKey: height,
        })
        self._input, self.uid = new_input, str(device.uniqueID())
        return True

    def _wait_for_frame(self, after_seq: int) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._seq > after_seq, FIRST_FRAME_S)

    # ---------- frames ----------
    def _deliver(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame, self._seq = frame, self._seq + 1
            self._cond.notify_all()

    def read(self, last_seq: int, timeout: float) -> tuple[np.ndarray | None, int]:
        """The newest frame after `last_seq`; (None, last_seq) if none came in time."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > last_seq, timeout)
            if self._seq > last_seq:
                return self._frame, self._seq
            return None, last_seq

    def close(self) -> None:
        if self._session.isRunning():
            self._session.stopRunning()
        self._output.setSampleBufferDelegate_queue_(None, None)
