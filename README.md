# Desk Guard

A macOS app that watches your webcam while you work. It warns you when someone is
looking over your shoulder, locks the Mac when you walk away, nudges you about posture, and
lets you control volume, browser tabs and media playback with hand gestures. It learns your
face, so only your hands can gesture, and it closes Messages, WhatsApp and Instagram when
someone sits down next to you. Everything runs locally; no video leaves the machine.

Built and tested on an Apple Silicon MacBook Pro with macOS 26.

## Setup

You need [uv](https://docs.astral.sh/uv/) and the Xcode command line tools (for `clang`).

```
git clone https://github.com/Abhi183/desk-guard.git
cd desk-guard
./run desk
```

The first `./run` creates `.venv` with uv's native Python 3.12 (an Intel Python under
Rosetta runs the models about half as fast), installs `requirements.txt` and downloads the
face recognition model (37 MB). Then:

```
tools/make_app.sh      # builds Desk Guard.app on your Desktop (or pass a folder)
./run app              # opens it (builds it first if it is missing)
```

## What it does

| Feature | Behaviour |
|---|---|
| Someone behind you | A second face in view for about half a second: red pop-up, sound, photo saved to `caught/` |
| Auto-lock | You not at the desk for 2 seconds (`--lock-after N`): the Mac locks. "At the desk" means your face, or your body still in your spot. See "Auto-lock" below. It re-arms when you come back |
| Posture | Learns how you sit in the first couple of seconds. Leaning in or slouching for 5 seconds gives a pop-up, at most once a minute |
| Gestures | See the table below |
| Knows your face | The first time it sees you with nobody sitting next to you it learns your face (about 3 seconds). After that your box says "you" (or your name, see below) and everyone else is "WHO'S THIS?" |
| Only your gestures | A hand counts only if it is nearer your face than anyone else's. Other people's hands show "not your hand, ignored". If you are not in the frame, nobody can gesture |
| Company | Someone sitting next to you for 8 seconds (`--company-after N`): Messages, WhatsApp and Instagram quit, and their tabs close in Chrome, Brave and Safari |

Alerts are drawn by the app itself in the top-right corner, because macOS often hides banners
from scripts.

### Gestures

| Gesture | Action |
|---|---|
| Thumbs up / down (hold) | Volume up / down, with the normal macOS volume pop-up |
| Fist (hold a bit longer, 0.8 s) | Mute toggle |
| Index finger pointing left / right (hold) | Previous / next browser tab |
| Peace sign (hold) | Webcam selfie to `snaps/` |
| Love-you sign 🤟 (hold) | Play / pause whatever is playing (YouTube, Spotify, Music) |
| Index finger pointing up (hold) | Take the mouse pointer. See below |

A gesture fires when the green bar under the banner fills (0.4 s by default; change it under
Gestures > Hold time). Mute needs a longer hold because a relaxed or lowered hand often looks
like a fist. Right after you let go of the pointer, gestures pause for a moment so lowering
your hand doesn't trigger anything. Each gesture can be switched off on its own; a switched-off
gesture shows "switched off in settings" in the status strip instead of firing.

### Moving the pointer

Point your index finger up for a third of a second and your hand takes the mouse pointer. It
works like a trackpad: the pointer starts where it is and follows how your hand moves, across
all displays. Slow movement is fine control, a quick sweep goes much further. While you hold
it:

- pinch thumb and index finger together: click
- open palm, held for 0.4 seconds: click (same as the pinch; there is no right click)
- fist: lift the mouse. The pointer stays put while you move your hand back to a comfortable
  spot; point again to carry on
- lower your hand out of view: let go

The pointer stands still during a pinch or an open palm, so the click lands where you
pointed. The other gestures are off until you let go. There is no right click, drag or double-click.
Turn it off with "Hand moves the pointer" in the menu or `--no-cursor`.

### Auto-lock

The lock goes by whether you are physically at the desk, so looking away or putting your
head down does not lock the Mac. Your face has to be recognised once. From then on you
count as present while something of you stays in your spot without a break: a face there,
even turned so far that it no longer matches, or your body (a person-segmentation model
checks the area under where your head was). When the spot has been empty for 1.5 seconds
that chain ends, and only your recognised face starts a new one. So an empty chair locks,
and so does a stranger who sits down after you left.

When your face is hidden the video window shows the body reading, for example
`face hidden, body in your spot: 62% (needs 35%)`.

### Your face

The profile is a gallery of up to 40 samples (128 numbers each) in `face_profile.npy`,
readable only by you and ignored by git. No photo is kept. The first sample comes from the
initial learning. After that the app adds a sample whenever it sees you looking different
enough from what it has (a side view, new light, a photo of you on a phone), so recognition
gets better the longer it runs. A face counts as you at 0.5 similarity to any sample. On 93
test faces, every view of the owner that matched scored 0.51 or more and the closest
stranger scored 0.42.

`./run train` adds samples from photos you already have: the selfies in `snaps/` and your
own face in `caught/`, or any folders you pass it. Faces that don't already look like you
are skipped.

A photo of you counts as you: hold one up and its box gets your name too. If the app learned the wrong face, or recognition gets worse after a haircut or
new glasses, press `f` in the video window or pick "Relearn my face" in the menu, then look at
the camera on your own.

To see your name on the box, set it under Settings > Face & identity (or pass `--name Alex`
to `./run desk`). Plain letters only, 20 characters at most.

### Company

"Sitting with you" means a second face at least a quarter the height of the nearest one, for
8 seconds. That covers someone on a chair just behind you. People at desks across the room
are smaller than that, and people walking past don't stay long enough. Apps are asked to
quit the same way Cmd+Q does. It runs once per visit: if you reopen Messages while they are
still there, it stays open until they leave and someone sits down again. The apps and sites
are listed at the top of `webcam_fun/personal.py`.

## The app

Desk Guard.app is one process with its own Dock icon, menu bar, video window and settings
window. Its executable (`tools/launcher.c`) runs Python inside itself, so macOS sees a real
app called Desk Guard rather than "Python". The watcher runs on background threads; one
thread keeps only the newest camera frame so the video never falls behind, and hand
recognition runs alongside face detection.

Settings apply instantly, with nothing restarted, and are saved to `menubar_settings.json`.

**Settings window**: Cmd-, , the gear in the video window's title bar, `s` in the video
window, clicking the Dock icon, or Settings... in the eye menu.

- Overview: status with start/stop, Enable all / Disable all features, a switch for each
- Security: auto-lock with lock delay (2/5/10/30 s), shoulder-surfer alarm, close personal
  apps with company
- Wellbeing: posture alerts, sounds, recalibrate posture
- Gestures: master switch, one switch per gesture, Enable all / Disable all, hold time
  (fast 0.25 s, normal 0.4 s, careful 0.7 s)
- Face & identity: your name, relearn my face
- Display: video window on/off, keep it on top
- Data & logs: caught photos, selfies, activity log

**Video window**: Cmd-1 shows or hides it, and so does the red close button; closing it
never stops watching. The pin in the title bar floats it above other windows, on every Space
and over full-screen apps. Keys: `s` settings, `m` mute, `c` recalibrate posture,
`l` auto-lock on/off, `g` gestures on/off, `f` relearn my face.

**Eye menu** in the menu bar: status, start/stop, show video window, Enable all / Disable all
features, settings, quit. Cmd-Q quits from anywhere in the app.

## Permissions

- **Camera**: Desk Guard asks the first time it starts. It is granted per app, so Terminal
  (for `./run desk`) and Desk Guard.app each need it. Rebuilding the app with
  `tools/make_app.sh` gives it a new signature, so macOS asks again.
- **Accessibility**: needed for tab switching, moving the pointer and the volume pop-up (the app presses the real
  keys). Add Desk Guard under System Settings > Privacy & Security > Accessibility.
- **Automation**: the first time a visitor triggers tab closing, macOS asks whether Desk Guard may
  control Chrome (or Brave, Safari). Say yes, or tabs stay open. Quitting apps needs nothing.
- **Menu bar icon missing**: macOS 26 hides new menu bar items. Turn on **Desk Guard** under
  System Settings > Menu Bar > Allow in the Menu Bar.

## Known limits

- Auto-lock follows whoever stays in your spot. Someone who slides into your chair within
  1.5 seconds of you leaving it is taken for you until they leave. Until your face has been
  learned, any face keeps it unlocked.
- Hands are matched to faces by distance in the picture. Someone who reaches across in front
  of you can still trigger a gesture.
- Firefox has no AppleScript support for tabs, so Instagram or WhatsApp tabs there stay open.
- Face recognition is for convenience. A photo of you would pass, so don't treat it as a lock.
- Locking uses `SACLockScreenImmediate` from the private `login.framework`; if Apple removes it,
  the app falls back to sleeping the display.

## Other toys

```
./run gestures    # gestures only, in their own window
./run ascii       # webcam as live ASCII art in the terminal (--style matrix|color|mono)
```

## Layout

- `webcam_fun/menubar.py` the app: menus, windows, camera permission, Dock
- `webcam_fun/engine.py` watcher threads: newest-frame camera reader, DeskApp loop
- `webcam_fun/desk.py` DeskApp (faces, lock, posture, gestures) and the Terminal version
  with an OpenCV window (`./run desk`)
- `webcam_fun/video_window.py`, `settings_panel.py`, `native_toast.py` AppKit windows
- `webcam_fun/settings_ui.html`, `settings_api.py` settings page and what it may call (tested)
- `webcam_fun/settings.py` saved options and the `desk.py` flags they turn into (unit tested)
- `webcam_fun/gestures.py`, `pointing.py`, `gesture_trigger.py` gesture recognition and actions
- `webcam_fun/cursor.py` pointer control logic (unit tested)
- `webcam_fun/presence.py`, `watch.py`, `posture.py`, `identity.py` decision logic (unit tested)
- `webcam_fun/body.py` person segmentation, for auto-lock when your face is hidden
- `webcam_fun/recognition.py` face profile: learn, save, match, grow
- `webcam_fun/train_face.py` adds profile samples from saved photos (`./run train`)
- `webcam_fun/personal.py` quits personal apps and closes their browser tabs
- `webcam_fun/macos.py`, `dock.py`, `toast.py` macOS glue: lock, keys, pop-ups, Dock icon
- `tools/make_app.sh` builds the `.app` around `tools/launcher.c`; `tools/make_icon.py` draws the icon
- `models/` YuNet face detector and SFace face recognizer (opencv_zoo), MediaPipe
  `gesture_recognizer.task` and `selfie_segmenter.tflite`

## Tests

```
.venv/bin/python -m pytest -q
```
