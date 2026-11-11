# Desk Guard

A macOS menu bar app that watches your webcam while you work. It warns you when someone is
looking over your shoulder, locks the Mac when you walk away, nudges you about posture, and
lets you control volume and browser tabs with hand gestures. Everything runs locally; no
video leaves the machine.

Built and tested on an Apple Silicon MacBook Pro with macOS 26.

## Setup

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/Abhi183/desk-guard.git
cd desk-guard
./run desk
```

The first `./run` creates `.venv` and installs the packages in `requirements.txt`. After that,
double-click **Desk Guard.app** in the repo folder (or drag a copy anywhere; run
`tools/make_app.sh` to build a fresh one on your Desktop).

## What it does

| Feature | Behaviour |
|---|---|
| Someone behind you | A second face in view for about half a second: red pop-up, sound, photo saved to `caught/` |
| Auto-lock | No face in view for 2 seconds (`--lock-after N`): the Mac locks. It re-arms when you come back |
| Posture | Learns how you sit in the first couple of seconds. Leaning in or slouching for 5 seconds gives a pop-up, at most once a minute |
| Gestures | See the table below |

Alerts are drawn by the app itself in the top-right corner, because macOS often hides banners
from scripts.

### Gestures

| Gesture | Action |
|---|---|
| Thumbs up / down (hold) | Volume up / down, with the normal macOS volume pop-up |
| Fist (hold) | Mute toggle |
| Index finger pointing left / right (hold) | Previous / next browser tab |
| Peace sign (hold) | Webcam selfie to `snaps/` |
| Love-you sign | Hearts |

A gesture fires when the green bar under the banner fills (about 0.4 s).

## Menu bar

The eye icon has: start/stop watching, show video window, lock when I leave, posture alerts,
gesture control, open caught photos, open log, quit. Choices are saved in
`menubar_settings.json`. Quitting from the Dock or pressing `q` in the video window closes the
whole app; "Stop watching" only pauses.

Video window keys: `q` quit, `m` mute, `t` always-on-top, `c` recalibrate posture,
`l` auto-lock on/off, `g` gestures on/off.

## Permissions

- **Camera**: macOS asks the first time. It is granted per app, so Terminal and Desk Guard.app
  each need it.
- **Accessibility**: needed for tab switching and the volume pop-up (the app presses the real
  keys). Add Desk Guard (or Python) under System Settings > Privacy & Security > Accessibility.
- **Menu bar icon missing**: macOS 26 hides new menu bar items. Turn on **Python** under
  System Settings > Menu Bar > Allow in the Menu Bar.

## Known limits

- Auto-lock checks for *a* face, not *your* face, so someone who sits in your chair keeps it
  unlocked.
- A hand in front of your face hides it, which can trigger the 2-second lock. Gesture at chest
  height.
- Locking uses `SACLockScreenImmediate` from the private `login.framework`; if Apple removes it,
  the app falls back to sleeping the display.

## Other toys

```
./run gestures    # gestures only, in their own window
./run ascii       # webcam as live ASCII art in the terminal (--style matrix|color|mono)
python3 extras/matrix.py            # Matrix rain in the terminal (--devanagari, --dense N)
```

## Layout

- `webcam_fun/desk.py` main loop: faces, lock, posture, gestures, video window
- `webcam_fun/menubar.py` menu bar app that starts and stops `desk.py`
- `webcam_fun/gestures.py`, `pointing.py`, `gesture_trigger.py` gesture recognition and actions
- `webcam_fun/presence.py`, `watch.py`, `posture.py` decision logic (unit tested)
- `webcam_fun/macos.py`, `dock.py`, `toast.py` macOS glue: lock, keys, pop-ups, Dock icon
- `tools/make_app.sh` builds the `.app`; `tools/make_icon.py` draws the icon
- `models/` YuNet face detector (opencv_zoo) and MediaPipe `gesture_recognizer.task`

## Tests

```
.venv/bin/python -m pytest -q
```
