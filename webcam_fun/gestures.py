"""Control the Mac with hand gestures.

  thumbs up / down (hold)   volume up / down
  fist (hold)               mute / unmute
  peace sign (hold)         take a webcam selfie
  love-you sign (hold)      play / pause media (YouTube, Spotify, Music...)
  point left / right (hold)  previous / next browser tab (needs Accessibility)

Keys: q quit
"""

import argparse
import time

import cv2
import mediapipe as mp
import numpy as np

from webcam_fun import macos
from webcam_fun.gesture_trigger import HoldTrigger
from webcam_fun.pointing import classify_point
from webcam_fun.vision import GESTURE_MODEL, ROOT, open_camera, put_text, require_model

WINDOW = "Gestures"
SNAP_DIR = ROOT / "snaps"
WHITE, CYAN, GREEN = (255, 255, 255), (255, 220, 0), (80, 200, 80)
LABELS = {"Thumb_Up": "volume up", "Thumb_Down": "volume down", "Closed_Fist": "mute",
          "Victory": "selfie", "ILoveYou": "play / pause", "Point_Right": "next tab",
          "Point_Left": "previous tab", "Pointing_Up": "point left or right instead"}
HAND_CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
                    (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-keys", action="store_true", help="don't switch tabs when pointing")
    p.add_argument("--camera", type=int, default=0)
    return p.parse_args()


def make_recognizer():
    vision = mp.tasks.vision
    options = vision.GestureRecognizerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(require_model(GESTURE_MODEL))),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
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
        self.send_keys = not args.no_keys
        self.trigger = HoldTrigger(hold_s=0.4, repeat_s={"Thumb_Up": 0.4, "Thumb_Down": 0.4})
        self.flash_until = 0.0
        self.message, self.message_until = "", 0.0

    def say(self, text: str, now: float) -> None:
        self.message, self.message_until = text, now + 2.5
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} gesture: {text}", flush=True)

    def act(self, gesture: str, frame: np.ndarray, now: float) -> None:
        if gesture in ("Thumb_Up", "Thumb_Down", "Closed_Fist"):
            self.adjust_audio(gesture, now)
        elif gesture == "Victory":
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

    def step(self, result, frame: np.ndarray, now: float) -> str | None:
        gesture = None
        if result.gestures:
            gesture = result.gestures[0][0].category_name
        if result.hand_landmarks:  # sideways pointing isn't a built-in gesture
            pointing = classify_point([(lm.x, lm.y) for lm in result.hand_landmarks[0]])
            gesture = pointing or gesture
        fired = self.trigger.update(gesture, now)
        if fired:
            self.act(fired, frame, now)
        return gesture

    # ---------- drawing pieces, shared with the desk app ----------
    def status_text(self, result) -> tuple[str, bool]:
        """(text, is_gesture_held) for a status line."""
        held = self.trigger.current
        if held:
            return f"{held.replace('_', ' ')}: {LABELS.get(held, '')}", True
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
        for name in ("Thumb_Up", "Thumb_Down", "Closed_Fist", "Victory", "ILoveYou"):
            put_text(img, f"{name.replace('_', ' ')}: {LABELS[name]}", (12, y), WHITE,
                     scale=0.45, thickness=1)
            y += 20
        put_text(img, "point left/right: change tab", (12, y), WHITE, scale=0.45, thickness=1)

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
