"""Control the Mac with hand gestures.

  thumbs up / down (hold)   volume up / down
  fist (hold a little longer) mute / unmute
  two fingers up (hold)     scroll: move the hand up / down like two fingers on a trackpad;
                            lower the hand to let go (needs Accessibility)
  open palm (hold 1 s)      take a webcam selfie
  love-you sign (hold)      play / pause media (YouTube, Spotify, Music...)
  point left / right (hold)  previous / next browser tab (needs Accessibility)
  point up (hold)           take the mouse pointer: it follows your hand. Then pinch thumb
                            and index, or hold an open palm, to click; fist to lift the
                            mouse; lower the hand to let go (needs Accessibility)

Keys: q quit
"""

import argparse
import time
from typing import Callable

import cv2
import mediapipe as mp
import numpy as np

from webcam_fun import macos
from webcam_fun.cursor import (
    INDEX_MCP,
    CursorDriver,
    CursorMode,
    CursorStep,
    pinch_ratio,
)
from webcam_fun.gesture_trigger import HoldTrigger
from webcam_fun.scroll import MIDDLE_MCP, ScrollMode, wheel_delta
from webcam_fun.pointing import classify_point
from webcam_fun.vision import GESTURE_MODEL, ROOT, open_camera, put_text, require_model

WINDOW = "Gestures"
MAX_HANDS = 2  # so a visitor's hand in view doesn't hide yours from the recognizer
SNAP_DIR = ROOT / "snaps"
WHITE, CYAN, GREEN = (255, 255, 255), (255, 220, 0), (80, 200, 80)
LABELS = {"Thumb_Up": "volume up", "Thumb_Down": "volume down", "Closed_Fist": "mute",
          "Open_Palm": "selfie", "ILoveYou": "play / pause", "Point_Right": "next tab",
          "Point_Left": "previous tab", "Pointing_Up": "hold to take the pointer"}
CURSOR_HELP = "pointer: pinch or open palm = click | fist = lift | lower hand = stop"
CURSOR_LIFTED = "pointer lifted: move your hand, then point again"
NO_MOUSE_ACCESS = "pointer blocked: allow Desk Guard / Python in Accessibility"
NO_SCROLL_ACCESS = "scroll blocked: allow Desk Guard in Accessibility"
SCROLL_HELP = "scrolling: move two fingers up / down | lower hand = stop"
# Which setting switches each gesture on and off (see webcam_fun.settings).
GESTURE_ACTIONS = {"Thumb_Up": "volume", "Thumb_Down": "volume", "Closed_Fist": "mute",
                   "Open_Palm": "selfie", "ILoveYou": "media", "Point_Right": "tabs",
                   "Point_Left": "tabs"}
# Gestures that are a mode you hold rather than a one-off action. Two fingers up is always
# scrolling (it used to be the selfie).
MODE_ACTIONS = frozenset({"scroll"})
DEFAULT_HOLD_S = 0.4
MIN_HOLD_S, MAX_HOLD_S = 0.1, 2.0
# A fist also shows up while you lower or relax your hand, so mute needs a longer hold.
MUTE_HOLD_S = 0.8
# An open palm is a natural resting hand, so a selfie needs a deliberate one-second hold.
SELFIE_HOLD_S = 1.0
# Lowering your hand after using the pointer passes through fists and palms; ignore them.
AFTER_POINTER_S = 0.8
HAND_CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
                    (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-keys", action="store_true", help="don't switch tabs when pointing")
    p.add_argument("--no-cursor", action="store_true",
                   help="don't let a raised index finger take the mouse pointer")
    add_gesture_options(p)
    p.add_argument("--camera", type=int, default=0)
    return check_gesture_options(p, p.parse_args())


def add_gesture_options(p: argparse.ArgumentParser) -> None:
    """Options shared with desk.py."""
    names = ", ".join(sorted(set(GESTURE_ACTIONS.values()) | MODE_ACTIONS))
    p.add_argument("--gestures-off", default="", metavar="LIST",
                   help=f"comma-separated gestures to ignore: {names}")
    p.add_argument("--gesture-hold", type=float, default=DEFAULT_HOLD_S, metavar="SECONDS",
                   help=f"how long to hold a gesture before it fires (default {DEFAULT_HOLD_S})")


def check_gesture_options(p: argparse.ArgumentParser,
                          args: argparse.Namespace) -> argparse.Namespace:
    known = set(GESTURE_ACTIONS.values()) | MODE_ACTIONS
    unknown = parse_off(args.gestures_off) - known
    if unknown:
        p.error(f"--gestures-off: unknown gesture {', '.join(sorted(unknown))}; "
                f"choose from {', '.join(sorted(known))}")
    if not MIN_HOLD_S <= args.gesture_hold <= MAX_HOLD_S:
        p.error(f"--gesture-hold must be between {MIN_HOLD_S} and {MAX_HOLD_S} seconds")
    return args


def parse_off(text: str) -> set[str]:
    return {part.strip() for part in text.split(",") if part.strip()}


def gesture_action(gesture: str | None) -> str | None:
    """The setting that controls this gesture, or None if it has none."""
    return GESTURE_ACTIONS.get(gesture)


def make_recognizer():
    vision = mp.tasks.vision
    options = vision.GestureRecognizerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(require_model(GESTURE_MODEL))),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=MAX_HANDS,
        min_hand_detection_confidence=0.6,
    )
    return vision.GestureRecognizer.create_from_options(options)


def draw_hand(img: np.ndarray, landmarks) -> None:
    h, w = img.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(img, pts[a], pts[b], CYAN, 2, cv2.LINE_AA)
    for p in pts:
        cv2.circle(img, p, 4, WHITE, -1, cv2.LINE_AA)


class GestureApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.send_keys = not getattr(args, "no_keys", False)
        self.pointer_released_at = float("-inf")
        self.last_off: str | None = None  # a gesture you're making that is switched off
        self.cursor_mode = CursorMode()
        self.cursor_driver = CursorDriver()
        self.cursor_lifted = False
        self.scroll_mode = ScrollMode()
        self.natural_scrolling = macos.natural_scrolling()
        self.scroll_blocked = False
        self.on_used: Callable[[str], None] | None = None  # for the daily stats
        self.hold: float | None = None
        self.cursor_enabled = False
        self.configure(disabled=parse_off(getattr(args, "gestures_off", "")),
                       cursor=not getattr(args, "no_cursor", False),
                       hold=getattr(args, "gesture_hold", DEFAULT_HOLD_S))
        self.flash_until = 0.0
        self.message, self.message_until = "", 0.0
        self.ignoring_hand = False  # a hand is in view but it isn't yours

    def configure(self, disabled: set[str], cursor: bool, hold: float) -> None:
        """Change which gestures work and how long to hold them, while running."""
        self.disabled = frozenset(disabled)
        if self.cursor_enabled and not cursor:  # let go of the pointer if just switched off
            self.cursor_mode = CursorMode()
        self.cursor_enabled = cursor
        if "scroll" in self.disabled:
            self.scroll_mode.reset()
        if hold != self.hold:
            self.hold = hold
            self.trigger = HoldTrigger(
                hold_s=hold, repeat_s={"Thumb_Up": 0.4, "Thumb_Down": 0.4},
                hold_overrides={"Closed_Fist": max(MUTE_HOLD_S, hold * 2),
                                "Open_Palm": max(SELFIE_HOLD_S, hold * 2.5)})

    def say(self, text: str, now: float) -> None:
        self.message, self.message_until = text, now + 2.5
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} gesture: {text}", flush=True)

    def _used(self, what: str) -> None:
        if self.on_used is not None:
            self.on_used(what)

    def act(self, gesture: str, frame: np.ndarray, now: float) -> None:
        self._used(gesture_action(gesture) or gesture)
        if gesture in ("Thumb_Up", "Thumb_Down", "Closed_Fist"):
            self.adjust_audio(gesture, now)
        elif gesture == "Open_Palm":
            SNAP_DIR.mkdir(exist_ok=True)
            path = SNAP_DIR / time.strftime("selfie_%Y%m%d_%H%M%S.jpg")
            cv2.imwrite(str(path), frame)
            self.flash_until = now + 0.25
            self.say(f"saved {path.name}", now)
        elif gesture == "ILoveYou":
            if macos.play_pause():
                self.say("play / pause", now)
            else:
                self.say("play / pause blocked: allow Desk Guard / Python in Accessibility", now)
        elif gesture in ("Point_Right", "Point_Left"):
            self.switch_tab(gesture == "Point_Right", now)

    def adjust_audio(self, gesture: str, now: float) -> None:
        """Presses the real volume/mute keys so the macOS volume pop-up shows."""
        actions = {"Thumb_Up": (macos.volume_up, "volume +"),
                   "Thumb_Down": (macos.volume_down, "volume -"),
                   "Closed_Fist": (macos.toggle_mute, "mute toggled")}
        press, label = actions[gesture]
        if press():
            self.say(label, now)
        else:
            self.say(f"{label} (no pop-up: allow Desk Guard / Python in Accessibility)", now)

    def switch_tab(self, to_right: bool, now: float) -> None:
        label = "next tab ->" if to_right else "<- previous tab"
        if not self.send_keys:
            self.say(f"{label} (keys off)", now)
            return
        error = macos.next_tab() if to_right else macos.previous_tab()
        self.say(error or label, now)

    def step(self, result, frame: np.ndarray, now: float, hand: int | None = 0) -> str | None:
        """Act on the gesture of hand number `hand`; None means no hand in view is yours."""
        gesture, points = None, None
        self.ignoring_hand = hand is None and bool(result.hand_landmarks)
        if hand is not None and hand < len(result.gestures) and result.gestures[hand]:
            gesture = result.gestures[hand][0].category_name
        if hand is not None and hand < len(result.hand_landmarks):
            points = [(lm.x, lm.y) for lm in result.hand_landmarks[hand]]
        if self.cursor_enabled and self._step_cursor(gesture, points, now):
            self.trigger.update(None, now)  # other gestures are off while you hold the pointer
            return gesture
        if "scroll" not in self.disabled and self._step_scroll(gesture, points, now):
            self.trigger.update(None, now)  # and while you scroll
            return gesture
        if points is not None:  # sideways pointing isn't a built-in gesture
            gesture = classify_point(points) or gesture
        if now - self.pointer_released_at < AFTER_POINTER_S:
            self.trigger.update(None, now)
            return gesture
        self.last_off = gesture if gesture_action(gesture) in self.disabled else None
        if self.last_off:
            self.trigger.update(None, now)  # switched off in settings: don't even charge
            return gesture
        fired = self.trigger.update(gesture, now)
        if fired:
            self.act(fired, frame, now)
        return gesture

    def _step_scroll(self, gesture: str | None, points: list | None, now: float) -> bool:
        """Two-finger scrolling for this frame. True while your hand holds the page."""
        hand_y = points[MIDDLE_MCP][1] if points else None
        step = self.scroll_mode.update(gesture, hand_y, now)
        if step.entered:
            self.scroll_blocked = False
            self._used("scroll")
            self.say("scrolling", now)
        if step.left:
            self.pointer_released_at = now  # lowering the hand shouldn't fire a gesture
        if step.pixels and not self.scroll_blocked:
            if not macos.scroll(wheel_delta(step.pixels, self.natural_scrolling)):
                self.scroll_blocked = True  # say it once, not every frame
                self.say(NO_SCROLL_ACCESS, now)
        return step.active

    def _step_cursor(self, gesture: str | None, points: list | None, now: float) -> bool:
        """Pointer control for this frame. True while your hand holds the pointer."""
        step = self.cursor_mode.update(gesture, pinch_ratio(points) if points else None, now)
        if step.left:
            self.pointer_released_at = now
            self.say("pointer released", now)
        if not step.active or points is None:
            return step.active
        knuckle = points[INDEX_MCP]  # steadier than the fingertip, and it stays put in a pinch
        if step.entered:
            self.cursor_driver.start(knuckle, macos.mouse_position() or (0.0, 0.0), now)
            self.say("pointer taken", now)
        self._apply_cursor(step, knuckle, now)
        return True

    def _apply_cursor(self, step: CursorStep, knuckle: tuple[float, float], now: float) -> None:
        position = macos.mouse_position()
        bounds = macos.desktop_bounds()
        if position is None or bounds is None:
            return
        self.cursor_driver.sync(position)
        self.cursor_lifted = step.lifted
        if not step.move:
            self.cursor_driver.hold(knuckle, now)
        elif not macos.move_mouse(self.cursor_driver.move(knuckle, bounds, now)):
            self.say(NO_MOUSE_ACCESS, now)
        if step.left_click:
            self.say("click" if macos.click_mouse(position) else NO_MOUSE_ACCESS, now)

    # ---------- drawing pieces, shared with the desk app ----------
    def status_text(self, result) -> tuple[str, bool]:
        """(text, is_gesture_held) for a status line."""
        if self.cursor_mode.active:
            return (CURSOR_LIFTED if self.cursor_lifted else CURSOR_HELP), True
        if self.scroll_mode.active:
            return SCROLL_HELP, True
        held = self.trigger.current
        if held:
            return f"{held.replace('_', ' ')}: {LABELS.get(held, '')}", True
        if self.ignoring_hand:
            return "not your hand, ignored", False
        if self.last_off:
            return f"{self.last_off.replace('_', ' ')}: switched off in settings", False
        if result.hand_landmarks:
            return "hand seen, no gesture yet", False
        return "no hand in view", False

    def message_now(self, now: float) -> str | None:
        return self.message if self.message and now < self.message_until else None

    def draw_effects(self, img: np.ndarray, result, now: float) -> np.ndarray:
        """Hand skeleton and selfie flash. Returns the (possibly new) image."""
        for hand in result.hand_landmarks:
            draw_hand(img, hand)
        if now < self.flash_until:
            img = cv2.addWeighted(img, 0.3, np.full_like(img, 255), 0.7, 0)
        return img

    def draw_status_strip(self, img: np.ndarray, result, now: float, top: int,
                          height: int = 28) -> None:
        """Gesture name plus a hold-progress bar, as a strip starting at y=top."""
        text, held = self.status_text(result)
        cv2.rectangle(img, (0, top), (img.shape[1], top + height), (40, 40, 40), -1)
        put_text(img, text, (12, top + height - 9), GREEN if held else WHITE, scale=0.55,
                 thickness=1)
        bar = int(img.shape[1] * self.trigger.progress(now))
        cv2.rectangle(img, (0, top + height - 4), (bar, top + height), GREEN, -1)

    def draw_legend(self, img: np.ndarray, top: int) -> None:
        y = top
        for name in ("Thumb_Up", "Thumb_Down", "Closed_Fist", "Open_Palm", "ILoveYou"):
            put_text(img, f"{name.replace('_', ' ')}: {LABELS[name]}", (12, y), WHITE,
                     scale=0.45, thickness=1)
            y += 20
        put_text(img, "point left/right: change tab", (12, y), WHITE, scale=0.45, thickness=1)
        put_text(img, "two fingers up: scroll", (12, y + 20), WHITE, scale=0.45, thickness=1)

    def render(self, frame: np.ndarray, result, now: float) -> np.ndarray:
        img = self.draw_effects(frame.copy(), result, now)
        self.draw_status_strip(img, result, now, top=0, height=50)
        self.draw_legend(img, top=76)
        message = self.message_now(now)
        if message:
            put_text(img, message, (14, img.shape[0] - 16), WHITE, scale=0.6)
        return img


class GestureEngine:
    """MediaPipe recognizer plus the strictly increasing timestamps it needs."""

    def __init__(self) -> None:
        self._recognizer = make_recognizer()
        self._last_ts = 0

    def process(self, frame_bgr: np.ndarray, now: float):
        ts = max(int(now * 1000), self._last_ts + 1)
        self._last_ts = ts
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return self._recognizer.recognize_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

    def close(self) -> None:
        self._recognizer.close()


def main() -> None:
    args = parse_args()
    app = GestureApp(args)
    cap = open_camera(args.camera)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    engine = GestureEngine()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Lost the camera feed.")
                break
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            result = engine.process(frame, now)
            app.step(result, frame, now)
            cv2.imshow(WINDOW, app.render(frame, result, now))
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
