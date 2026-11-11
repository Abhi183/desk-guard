"""Desk monitor: shoulder-surfer alarm, auto-lock when you leave, posture nudges,
and hand-gesture control, all from one camera feed.

Keys: q quit  m mute  t always-on-top  c recalibrate posture  l lock on/off  g gestures on/off
"""

import argparse
import time

import cv2
import numpy as np

from webcam_fun import macos
from webcam_fun.dock import QUIT_ALL_REQUEST, on_dock_quit, use_desk_guard_icon
from webcam_fun.gestures import GestureApp, GestureEngine
from webcam_fun.posture import PostureJudge
from webcam_fun.presence import AutoLock
from webcam_fun.vision import (
    ROOT,
    Box,
    FaceDetector,
    open_camera,
    put_text,
    try_open_camera,
)
from webcam_fun.watch import ShoulderWatch

WINDOW = "Desk Guard"
CAUGHT_DIR = ROOT / "caught"
RED, GREEN, AMBER, GRAY, WHITE = (40, 40, 230), (80, 200, 80), (0, 170, 255), (150, 150, 150), (255, 255, 255)
AMBER_HEX, GREEN_HEX = "#f5a623", "#30a46c"  # pop-up accent colors
LOCK_WARNING_S = 5     # heads-up notification, only used when --lock-after is longer
DEFAULT_LOCK_AFTER_S = 2  # short grace so one missed frame or a glance away doesn't lock
MIN_LOCK_AFTER_S = 1
RECONNECT_DELAY_S = 2
WINDOW_SIZE = (480, 360)
POSTURE_TEXT = {"good": "posture ok", "too_close": "too close to the screen",
                "slouching": "slouching", "calibrating": "posture: sit normally, calibrating",
                "no_face": "posture: -"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lock-after", type=float, default=DEFAULT_LOCK_AFTER_S,
                   help="seconds with no face before locking (default 2)")
    p.add_argument("--no-lock", action="store_true", help="disable auto-lock")
    p.add_argument("--no-guard", action="store_true", help="disable shoulder-surfer alarm")
    p.add_argument("--no-posture", action="store_true", help="disable posture alerts")
    p.add_argument("--dry-run", action="store_true", help="show 'would lock' instead of locking")
    p.add_argument("--no-window", action="store_true",
                   help="run without a window; you only get notifications (Ctrl+C to stop)")
    p.add_argument("--mute", action="store_true", help="no sounds")
    p.add_argument("--no-gestures", action="store_true", help="disable hand-gesture control")
    p.add_argument("--no-keys", action="store_true",
                   help="gestures: don't switch tabs when pointing")
    p.add_argument("--camera", type=int, default=0)
    args = p.parse_args()
    if args.lock_after < MIN_LOCK_AFTER_S:
        p.error(f"--lock-after must be at least {MIN_LOCK_AFTER_S} second")
    return args


class DeskApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.detector = FaceDetector()
        self.watch = ShoulderWatch()
        self.lock = AutoLock(args.lock_after)
        self.posture = PostureJudge()
        self.lock_enabled = not args.no_lock
        self.muted = args.mute
        self.on_top = True
        self.lock_warned = False
        self.caught = 0
        self.last_guard_alert = float("-inf")
        self.message, self.message_until = "", 0.0
        self.posture_status = "calibrating"
        self.gestures_enabled = not args.no_gestures
        self.gesture_app = GestureApp(argparse.Namespace(no_keys=args.no_keys))
        self.gesture_engine: GestureEngine | None = None
        self.gesture_result = None
        self.quit_requested = False

    # ---------- events ----------
    @staticmethod
    def log(event: str) -> None:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event}", flush=True)

    def request_quit_all(self, reason: str) -> None:
        """Quit Desk Guard entirely: this loop and the menu bar app (via a request file)."""
        if self.quit_requested:
            return
        self.log(f"quit ({reason})")
        try:
            QUIT_ALL_REQUEST.touch()
        except OSError as e:
            self.log(f"could not tell the menu bar to quit: {e}")
        self.quit_requested = True

    def say(self, text: str, now: float, seconds: float = 4.0) -> None:
        self.message, self.message_until = text, now + seconds

    def on_intruder(self, frame: np.ndarray, now: float) -> None:
        if now - self.last_guard_alert < 8:
            return
        self.last_guard_alert = now
        self.log("someone behind you")
        macos.alert("Desk Guard", "Behind you!", "Someone is looking at your screen")
        if not self.muted:
            macos.play_sound()
        CAUGHT_DIR.mkdir(exist_ok=True)
        cv2.imwrite(str(CAUGHT_DIR / time.strftime("caught_%Y%m%d_%H%M%S.jpg")), frame)
        self.caught += 1

    def on_away(self, now: float) -> None:
        if self.args.dry_run:
            self.log("would lock (dry run)")
            self.say("would lock now (dry run)", now)
            return
        result = macos.lock_screen()
        self.log(f"away -> screen {result}")
        self.say(f"screen {result}", now)

    def check_lock_warning(self, now: float) -> None:
        """One heads-up notification per countdown, LOCK_WARNING_S before locking."""
        left = self.lock.seconds_left(now)
        if left is None:
            self.lock_warned = False
        elif (self.args.lock_after > LOCK_WARNING_S * 2 and left <= LOCK_WARNING_S
              and not self.lock_warned):
            self.lock_warned = True
            macos.alert("Desk Guard", f"Locking in {LOCK_WARNING_S} seconds",
                        "Look at the camera to stay unlocked", AMBER_HEX)
            if not self.muted:
                macos.play_sound("/System/Library/Sounds/Pop.aiff")

    def on_bad_posture(self, status: str) -> None:
        text = "Sit back a bit" if status == "too_close" else "Sit up straight"
        self.log(f"posture: {status}")
        macos.alert("Desk Guard", "Posture", text, AMBER_HEX)
        if not self.muted:
            macos.play_sound("/System/Library/Sounds/Tink.aiff")

    # ---------- per frame ----------
    def step(self, frame: np.ndarray, now: float) -> list[Box]:
        faces = self.detector.detect(frame)
        if not self.args.no_guard and self.watch.update(len(faces) > 1):
            self.on_intruder(frame, now)
        if self.lock_enabled:
            if self.lock.update(bool(faces), now):
                self.on_away(now)
            self.check_lock_warning(now)
        if not self.args.no_posture:
            main_face = faces[0] if faces else None
            was_calibrated = self.posture.calibrated
            self.posture_status, alert = self.posture.update(main_face, frame.shape[0], now)
            if alert:
                self.on_bad_posture(self.posture_status)
            if self.posture.calibrated and not was_calibrated:
                macos.alert("Desk Guard", "Posture saved",
                            "This is your good posture. I will nudge you if you drift",
                            GREEN_HEX)
        self._step_gestures(frame, now)
        return faces

    def _step_gestures(self, frame: np.ndarray, now: float) -> None:
        if not self.gestures_enabled:
            self.gesture_result = None
            return
        if self.gesture_engine is None:
            self.gesture_engine = GestureEngine()  # loaded on first use (~1s)
        self.gesture_result = self.gesture_engine.process(frame, now)
        self.gesture_app.step(self.gesture_result, frame, now)

    def close(self) -> None:
        if self.gesture_engine is not None:
            self.gesture_engine.close()

    def handle_key(self, key: int, now: float) -> bool:
        """Returns False when the app should quit."""
        if key == ord("q"):
            self.request_quit_all("q pressed in the video window")
            return False
        if key == ord("m"):
            self.muted = not self.muted
        elif key == ord("t"):
            self.on_top = not self.on_top
            set_on_top(self.on_top)
        elif key == ord("c"):
            self.posture.recalibrate()
            self.say("recalibrating posture, sit how you want to sit", now)
        elif key == ord("l"):
            self.lock_enabled = not self.lock_enabled
            self.lock = AutoLock(self.args.lock_after)
            self.say(f"auto-lock {'on' if self.lock_enabled else 'off'}", now)
        elif key == ord("g"):
            self.gestures_enabled = not self.gestures_enabled
            self.say(f"gestures {'on' if self.gestures_enabled else 'off'}", now)
        return True

    # ---------- drawing ----------
    def render(self, frame: np.ndarray, faces: list[Box], now: float) -> np.ndarray:
        img = frame.copy()
        for i, (x, y, w, h) in enumerate(faces):
            color = GREEN if i == 0 else RED
            cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
            put_text(img, "you" if i == 0 else "WHO'S THIS?", (x, max(y - 8, 92)), color)
        if self.gesture_result is not None:
            img = self.gesture_app.draw_effects(img, self.gesture_result, now)
        self._draw_banner(img, faces, now)
        if self.gesture_result is not None:
            self.gesture_app.draw_status_strip(img, self.gesture_result, now, top=44)
        self._draw_status(img, now)
        return img

    def _draw_banner(self, img: np.ndarray, faces: list[Box], now: float) -> None:
        left = self.lock.seconds_left(now) if self.lock_enabled else None
        if self.watch.alarm and not self.args.no_guard:
            if int(now * 4) % 2 == 0:
                cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), RED, 18)
            text, color = "SOMEONE IS BEHIND YOU", RED
        elif left is not None:
            text, color = f"Locking in {left:0.0f}s", AMBER
        elif not faces:
            text, color = "No one at the desk", GRAY
        else:
            text, color = "All clear", GREEN
        cv2.rectangle(img, (0, 0), (img.shape[1], 44), color, -1)
        put_text(img, text, (14, 31), WHITE, scale=0.9)

    def _draw_status(self, img: np.ndarray, now: float) -> None:
        h = img.shape[0]
        lines = []
        if not self.args.no_posture:
            lines.append(POSTURE_TEXT.get(self.posture_status, self.posture_status))
        if self.message and now < self.message_until:
            lines.append(self.message)
        gesture_message = self.gesture_app.message_now(now) if self.gestures_enabled else None
        if gesture_message:
            lines.append(gesture_message)
        lock = "off" if not self.lock_enabled else f"{self.args.lock_after:g}s"
        lines.append(f"caught {self.caught} | lock {lock} | sound {'off' if self.muted else 'on'}"
                     " | q m t c l g")
        for i, line in enumerate(reversed(lines)):
            put_text(img, line, (10, h - 12 - i * 22), WHITE, scale=0.5, thickness=1)


def show_window_up_front() -> None:
    """Put the video window in the top-right of the main screen and bring it forward.

    Launched from the menu bar app, this process starts in the background, so without
    this the window can open behind other windows or on another display.
    """
    try:
        from AppKit import NSApplication, NSScreen
        screen = NSScreen.mainScreen().frame()
        cv2.moveWindow(WINDOW, int(screen.size.width) - WINDOW_SIZE[0] - 20, 60)
        use_desk_guard_icon()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except (ImportError, AttributeError, cv2.error) as e:  # cosmetic only
        print(f"could not bring the window forward: {e}", flush=True)


def set_on_top(on: bool) -> None:
    try:
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1 if on else 0)
    except cv2.error:
        pass  # not every GUI backend supports it


def reconnect_camera(cap: cv2.VideoCapture, index: int) -> cv2.VideoCapture:
    """Block until the camera delivers frames again, retrying every few seconds."""
    cap.release()
    print("Camera feed lost (screen locked?). Waiting for it to come back...", flush=True)
    while True:
        time.sleep(RECONNECT_DELAY_S)
        new_cap = try_open_camera(index)
        if new_cap is not None and new_cap.read()[0]:
            print("Camera back.", flush=True)
            return new_cap
        if new_cap is not None:
            new_cap.release()


def main() -> None:
    args = parse_args()
    app = DeskApp(args)
    cap = open_camera(args.camera)
    if args.no_window:
        print("Desk Guard running with no window. Ctrl+C to stop.")
    else:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, *WINDOW_SIZE)
        set_on_top(True)
        cv2.imshow(WINDOW, np.zeros((WINDOW_SIZE[1], WINDOW_SIZE[0], 3), np.uint8))
        cv2.waitKey(1)
        show_window_up_front()
        on_dock_quit(lambda: app.request_quit_all("Quit from the Dock"))
    macos.alert("Desk Guard", "Watching", "Sit normally for a moment so I can learn your posture",
                GREEN_HEX)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                # macOS cuts the camera while the screen is locked; wait for it to come back.
                cap = reconnect_camera(cap, args.camera)
                continue
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            faces = app.step(frame, now)
            if app.quit_requested:
                break
            if args.no_window:
                continue
            cv2.imshow(WINDOW, app.render(frame, faces, now))
            key = cv2.waitKey(1) & 0xFF
            if not app.handle_key(key, now) or app.quit_requested:  # q or Dock > Quit
                break
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
