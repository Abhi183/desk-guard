"""Desk monitor: shoulder-surfer alarm, auto-lock when you leave, posture nudges,
hand-gesture control that only obeys you, and closing personal apps when someone
sits down with you, all from one camera feed.

Keys: q quit  s settings  m mute  t always-on-top  c recalibrate posture  l lock on/off
      g gestures on/off  f relearn my face
Click the gear in the top-right corner of the video window for settings.
"""

import argparse
import math
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import cv2
import numpy as np

from webcam_fun import macos, personal
from webcam_fun import settings as cfg
from webcam_fun.body import BODY_MIN_SHARE, BodyDetector
from webcam_fun.dock import QUIT_ALL_REQUEST, on_dock_quit, use_desk_guard_icon
from webcam_fun.gestures import (
    GestureApp,
    GestureEngine,
    add_gesture_options,
    check_gesture_options,
)
from webcam_fun.identity import (
    CompanyWatch,
    MeTracker,
    OwnerTracker,
    clean_name,
    hand_owner,
    has_company,
)
from webcam_fun.posture import PostureJudge
from webcam_fun.presence import AutoLock
from webcam_fun.recognition import FaceId
from webcam_fun.vision import (
    ROOT,
    Box,
    Face,
    FaceDetector,
    open_camera,
    put_text,
    try_open_camera,
)
from webcam_fun.watch import ShoulderWatch

WINDOW = "Desk Guard"
APP_BUNDLE_ID = "local.webcamfun.deskguard"  # opening the app shows its settings window
CAUGHT_DIR = ROOT / "caught"
RED, GREEN, AMBER, GRAY, WHITE = (40, 40, 230), (80, 200, 80), (0, 170, 255), (150, 150, 150), (255, 255, 255)
AMBER_HEX, GREEN_HEX = "#f5a623", "#30a46c"  # pop-up accent colors
LOCK_WARNING_S = 5     # heads-up notification, only used when --lock-after is longer
DEFAULT_LOCK_AFTER_S = 2  # short grace so one missed frame or a glance away doesn't lock
MIN_LOCK_AFTER_S = 1
DEFAULT_COMPANY_AFTER_S = 8  # someone sitting with you this long closes personal apps
MIN_COMPANY_AFTER_S = 1
RECONNECT_DELAY_S = 2
WINDOW_SIZE = (480, 360)
BANNER_H = 44
GEAR_R, GEAR_MARGIN, GEAR_TEETH = 11, 24, 8
SETTINGS_REOPEN_S = 1.0  # one click opens the window once, even if the mouse bounces
POSTURE_TEXT = {"good": "posture ok", "too_close": "too close to the screen",
                "slouching": "slouching", "calibrating": "posture: sit normally, calibrating",
                "no_face": "posture: -"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    p.add_argument("--no-cursor", action="store_true",
                   help="gestures: don't let a raised index finger take the mouse pointer")
    add_gesture_options(p)
    p.add_argument("--no-privacy", action="store_true",
                   help="don't close personal apps when someone sits with you")
    p.add_argument("--company-after", type=float, default=DEFAULT_COMPANY_AFTER_S,
                   help="seconds someone sits with you before personal apps close (default 8)")
    p.add_argument("--name", default="you",
                   help="what to call you on the box around your face (default: you)")
    p.add_argument("--camera", type=int, default=0)
    args = check_gesture_options(p, p.parse_args(argv))
    args.name = clean_name(args.name)
    if args.lock_after < MIN_LOCK_AFTER_S:
        p.error(f"--lock-after must be at least {MIN_LOCK_AFTER_S} second")
    if args.company_after < MIN_COMPANY_AFTER_S:
        p.error(f"--company-after must be at least {MIN_COMPANY_AFTER_S} second")
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
        self.gesture_app = GestureApp(argparse.Namespace(
            no_keys=args.no_keys, no_cursor=args.no_cursor, gestures_off=args.gestures_off,
            gesture_hold=args.gesture_hold))
        self.gesture_engine: GestureEngine | None = None
        # Hand recognition runs on its own thread while OpenCV finds and recognises faces
        # on this one, which takes about a third off each frame. It is collected before any
        # other MediaPipe model runs, so two MediaPipe graphs never run at the same time.
        self._gesture_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gestures")
        self.gesture_result = None
        self.quit_requested = False
        self.face_id = FaceId(self.on_face_learned)
        self.me_tracker = MeTracker()
        self.me: int | None = None  # index of your face in this frame's face list
        self.company = CompanyWatch(args.company_after)
        self.owner = OwnerTracker()
        self.body: BodyDetector | None = None  # loaded the first time your face is hidden
        self.body_share: float | None = None   # last body check, for the status line
        self.mouse: tuple[int, int] = (-1, -1)  # last pointer position over the video
        self.settings_opened_at = float("-inf")
        self._banner_color = GRAY
        self.show_gear = True  # the app has a real gear button in the title bar instead
        self.frame_width = WINDOW_SIZE[0]

    # ---------- live settings ----------
    def apply(self, settings: cfg.Settings) -> None:
        """Take new settings while running, without reloading anything."""
        flags = settings.flags
        lock_after = float(settings.lock_after)
        if flags["auto_lock"] != self.lock_enabled or lock_after != self.args.lock_after:
            self.lock, self.lock_warned = AutoLock(lock_after), False
        if flags["privacy"] and self.args.no_privacy:
            self.company = CompanyWatch(self.args.company_after)  # fresh countdown
        self.args = argparse.Namespace(**{
            **vars(self.args), "no_guard": not flags["guard"], "no_posture": not flags["posture"],
            "no_privacy": not flags["privacy"], "name": settings.name, "lock_after": lock_after})
        self.lock_enabled = flags["auto_lock"]
        self.muted = not flags["sounds"]
        self.gestures_enabled = flags["gestures"]
        self.gesture_app.configure(
            disabled={key for key in cfg.GESTURE_ACTIONS if not flags[key]},
            cursor=flags["cursor"], hold=cfg.SENSITIVITY[settings.sensitivity])

    def camera_changed(self) -> None:
        """A different camera sees you from a different angle and distance: start the
        camera-specific state over. No lock until you have been seen through it."""
        self.lock, self.lock_warned = AutoLock(self.args.lock_after), False
        self.owner = OwnerTracker()
        self.me_tracker = MeTracker()
        self.posture.recalibrate()

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

    def on_away(self, now: float, stranger: bool) -> None:
        reason = "unrecognised face" if stranger else "away"
        if self.args.dry_run:
            self.log(f"{reason} -> would lock (dry run)")
            self.say("would lock now (dry run)", now)
            return
        result = macos.lock_screen()
        self.log(f"{reason} -> screen {result}")
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

    def on_face_learned(self, error: str | None) -> None:
        if error:
            self.log(error)
            macos.alert("Desk Guard", "Face not saved", "I will have to learn it again next time",
                        AMBER_HEX)
            return
        self.log("learned your face")
        macos.alert("Desk Guard", "I know your face now",
                    "Gestures only work for you. Press f in the video window to relearn",
                    GREEN_HEX)

    def on_company(self, now: float) -> None:
        if self.args.dry_run:
            self.log("would close personal apps (dry run)")
            self.say("would close personal apps now (dry run)", now)
            return
        self.say("company: closing personal apps", now)
        # Closing browser tabs waits on AppleScript, so keep it off the camera loop.
        threading.Thread(target=self._close_personal, daemon=True).start()

    def _close_personal(self) -> None:
        apps = personal.quit_personal_apps()
        tabs, errors = personal.close_personal_tabs()
        for error in errors:
            self.log(f"company: could not close tabs: {error}")
        closed = [*apps, *([f"{tabs} browser tab{'s' if tabs != 1 else ''}"] if tabs else [])]
        if not closed:
            self.log("company: nothing personal was open")
            return
        self.log(f"company: closed {', '.join(closed)}")
        macos.alert("Desk Guard", "Company", f"Closed {', '.join(closed)}", AMBER_HEX)

    def on_bad_posture(self, status: str) -> None:
        text = "Sit back a bit" if status == "too_close" else "Sit up straight"
        self.log(f"posture: {status}")
        macos.alert("Desk Guard", "Posture", text, AMBER_HEX)
        if not self.muted:
            macos.play_sound("/System/Library/Sounds/Tink.aiff")

    # ---------- per frame ----------
    def step(self, frame: np.ndarray, now: float) -> list[Box]:
        hands = self._gesture_pool.submit(self._recognize_hands, frame, now) \
            if self.gestures_enabled else None
        detected: list[Face] = self.detector.detect_faces(frame)
        faces = [face.box for face in detected]
        self.me = self.face_id.identify(frame, detected, now)
        hand_result = self._collect_hands(hands)
        if not self.args.no_guard and self.watch.update(len(faces) > 1):
            self.on_intruder(frame, now)
        if not self.args.no_privacy and self.company.update(has_company(faces), now):
            self.on_company(now)
        if self.lock_enabled:
            if self.lock.update(self._owner_at_desk(frame, faces, now), now):
                self.on_away(now, stranger=bool(faces))
            self.check_lock_warning(now)
        if not self.args.no_posture:
            if self.me is not None:
                main_face = faces[self.me]
            else:  # a lone face that doesn't match is most likely you with your head turned
                main_face = faces[0] if len(faces) == 1 else None
            was_calibrated = self.posture.calibrated
            self.posture_status, alert = self.posture.update(main_face, frame.shape[0], now)
            if alert:
                self.on_bad_posture(self.posture_status)
            if self.posture.calibrated and not was_calibrated:
                macos.alert("Desk Guard", "Posture saved",
                            "This is your good posture. I will nudge you if you drift",
                            GREEN_HEX)
        self._step_gestures(frame, faces, now, hand_result)
        return faces

    def _owner_at_desk(self, frame: np.ndarray, faces: list[Box], now: float) -> bool:
        """You, physically at the desk: your face, or failing that your body in your spot."""
        self.body_share = None
        if self.face_id.learning:
            return bool(faces)  # nobody to recognise yet: any face keeps it unlocked
        my_face = faces[self.me] if self.me is not None else None
        zone = self.owner.zone(frame.shape[1], frame.shape[0])
        if my_face is None and zone is not None:
            if self.body is None:
                self.body = BodyDetector()
            self.body_share = self.body.body_share(frame, zone)
        body_there = self.body_share is not None and self.body_share >= BODY_MIN_SHARE
        return self.owner.update(my_face, faces, body_there, now)

    def _recognize_hands(self, frame: np.ndarray, now: float):
        if self.gesture_engine is None:
            self.gesture_engine = GestureEngine()  # loaded on first use (~1s)
        return self.gesture_engine.process(frame, now)

    def _collect_hands(self, hands: Future | None):
        """The hand recognizer's result, or None. A MediaPipe failure costs one frame: the
        recognizer is rebuilt on the next one instead of taking the whole watcher down."""
        if hands is None:
            return None
        try:
            return hands.result()
        except RuntimeError as e:
            self.log(f"hand recognizer failed, restarting it: {str(e).splitlines()[-1][:120]}")
            broken, self.gesture_engine = self.gesture_engine, None
            if broken is not None:
                try:
                    broken.close()
                except RuntimeError:
                    pass  # it is already broken; dropping it is all we need
            return None

    def _step_gestures(self, frame: np.ndarray, faces: list[Box], now: float,
                       result) -> None:
        my_box = self.me_tracker.update(faces[self.me] if self.me is not None else None, now)
        self.gesture_result = result
        if result is None:
            return
        hand = self._my_hand(result, faces, my_box, frame.shape)
        self.gesture_app.step(self.gesture_result, frame, now, hand=hand)

    def _my_hand(self, result, faces: list[Box], my_box: Box | None,
                 frame_shape: tuple) -> int | None:
        """Which of the hands in view is yours: the one nearer your face than anyone else's."""
        if my_box is None:
            return None  # you are not in the frame, so nobody gets to gesture
        if self.me is not None:
            boxes, mine = faces, self.me
        else:  # your face is covered this frame (often by the hand): use where it just was
            boxes, mine = [*faces, my_box], len(faces)
        h, w = frame_shape[:2]
        for i, landmarks in enumerate(result.hand_landmarks):
            wrist = (landmarks[0].x * w, landmarks[0].y * h)
            if hand_owner(wrist, boxes) == mine:
                return i
        return None

    def close(self) -> None:
        self._gesture_pool.shutdown(wait=True)
        if self.gesture_engine is not None:
            self.gesture_engine.close()
        if self.body is not None:
            self.body.close()

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
        elif key == ord("s"):
            self.open_settings(now)
        elif key == ord("f"):
            self.face_id.relearn()
            self.log("relearning your face")
            self.say("relearning your face, look at the camera alone", now)
        return True

    # ---------- settings gear ----------
    def gear_center(self, frame_width: int) -> tuple[int, int]:
        return frame_width - GEAR_MARGIN, BANNER_H // 2

    def on_mouse(self, event: int, x: int, y: int, frame_width: int) -> None:
        self.mouse = (x, y)
        if event != cv2.EVENT_LBUTTONUP:
            return
        gx, gy = self.gear_center(frame_width)
        if math.hypot(x - gx, y - gy) <= GEAR_R + 8:
            self.open_settings(time.monotonic())

    def open_settings(self, now: float) -> None:
        if now - self.settings_opened_at < SETTINGS_REOPEN_S:
            return
        self.settings_opened_at = now
        try:
            subprocess.Popen(["open", "-b", APP_BUNDLE_ID])
        except OSError as e:
            self.log(f"could not open settings: {e}")
            self.say("could not open settings, see the log", now)

    def _draw_gear(self, img: np.ndarray) -> None:
        cx, cy = self.gear_center(img.shape[1])
        hover = math.hypot(self.mouse[0] - cx, self.mouse[1] - cy) <= GEAR_R + 8
        if hover:
            cv2.circle(img, (cx, cy), GEAR_R + 8, (255, 255, 255), -1, cv2.LINE_AA)
        color = (60, 60, 60) if hover else WHITE
        for i in range(GEAR_TEETH):
            a = 2 * math.pi * i / GEAR_TEETH
            inner = (int(cx + math.cos(a) * GEAR_R * 0.6), int(cy + math.sin(a) * GEAR_R * 0.6))
            outer = (int(cx + math.cos(a) * GEAR_R * 1.15), int(cy + math.sin(a) * GEAR_R * 1.15))
            cv2.line(img, inner, outer, color, 4, cv2.LINE_AA)
        cv2.circle(img, (cx, cy), int(GEAR_R * 0.8), color, -1, cv2.LINE_AA)
        hole = (255, 255, 255) if hover else self._banner_color
        cv2.circle(img, (cx, cy), int(GEAR_R * 0.35), hole, -1, cv2.LINE_AA)

    # ---------- drawing ----------
    def render(self, frame: np.ndarray, faces: list[Box], now: float) -> np.ndarray:
        img = frame.copy()
        for i, (x, y, w, h) in enumerate(faces):
            label, color = self._face_label(i, len(faces))
            cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
            put_text(img, label, (x, max(y - 8, 92)), color)
        if self.gesture_result is not None:
            img = self.gesture_app.draw_effects(img, self.gesture_result, now)
        self._draw_banner(img, faces, now)
        if self.gesture_result is not None:
            self.gesture_app.draw_status_strip(img, self.gesture_result, now, top=44)
        self._draw_status(img, now)
        return img

    def _face_label(self, index: int, face_count: int) -> tuple[str, tuple]:
        if not self.face_id.is_you(index):
            return ("WHO'S THIS?", RED) if face_count > 1 else ("not recognised", RED)
        if self.face_id.learning:
            return f"learning your face {self.face_id.progress:.0%}", AMBER
        return self.args.name, GREEN

    def _draw_banner(self, img: np.ndarray, faces: list[Box], now: float) -> None:
        left = self.lock.seconds_left(now) if self.lock_enabled else None
        company_left = None if self.args.no_privacy else self.company.seconds_left(now)
        if self.watch.alarm and not self.args.no_guard:
            if int(now * 4) % 2 == 0:
                cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), RED, 18)
            text, color = "SOMEONE IS BEHIND YOU", RED
        elif company_left is not None:
            text, color = f"Company: closing personal apps in {company_left:0.0f}s", AMBER
        elif left is not None:
            who = f"Not {self.args.name}: locking" if faces else "Locking"
            text, color = f"{who} in {left:0.0f}s", AMBER
        elif not faces:
            text, color = "No one at the desk", GRAY
        else:
            text, color = "All clear", GREEN
        cv2.rectangle(img, (0, 0), (img.shape[1], BANNER_H), color, -1)
        put_text(img, text, (14, 31), WHITE, scale=0.9)
        self._banner_color = color
        if self.show_gear:
            self._draw_gear(img)

    def _draw_status(self, img: np.ndarray, now: float) -> None:
        h = img.shape[0]
        lines = []
        if not self.args.no_posture:
            lines.append(POSTURE_TEXT.get(self.posture_status, self.posture_status))
        if self.body_share is not None:  # your face is hidden: lock goes by your body
            lines.append(f"face hidden, body in your spot: {self.body_share:.0%} "
                         f"(needs {BODY_MIN_SHARE:.0%})")
        if self.message and now < self.message_until:
            lines.append(self.message)
        gesture_message = self.gesture_app.message_now(now) if self.gestures_enabled else None
        if gesture_message:
            lines.append(gesture_message)
        lock = "off" if not self.lock_enabled else f"{self.args.lock_after:g}s"
        lines.append(f"caught {self.caught} | lock {lock} | sound {'off' if self.muted else 'on'}"
                     " | s settings")
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
        cv2.setMouseCallback(WINDOW, lambda event, x, y, _flags, _param:
                             app.on_mouse(event, x, y, app.frame_width))
    if not args.no_privacy and not args.dry_run:
        personal.ask_tab_permission()
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
            app.frame_width = frame.shape[1]
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
