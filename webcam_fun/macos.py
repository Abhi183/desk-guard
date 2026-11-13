"""Small wrappers around macOS features. All AppleScript here is constant text."""

import ctypes
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGIN_FRAMEWORK = "/System/Library/PrivateFrameworks/login.framework/Versions/Current/login"
DEFAULT_SOUND = "/System/Library/Sounds/Sosumi.aiff"
QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def _osascript_async(script: str) -> None:
    subprocess.Popen(["osascript", "-e", script], **QUIET)


def notify(title: str, subtitle: str, message: str) -> None:
    """Show a notification banner. Arguments must be trusted constant strings."""
    for text in (title, subtitle, message):
        if '"' in text or "\\" in text:
            raise ValueError("notification text cannot contain quotes or backslashes")
    _osascript_async(f'display notification "{message}" with title "{title}" '
                     f'subtitle "{subtitle}"')


def popup(title: str, message: str, color: str = "#e5484d", seconds: float = 5.0) -> None:
    """Our own on-screen pop-up (webcam_fun.toast); shows even when banners are hidden."""
    subprocess.Popen([sys.executable, "-m", "webcam_fun.toast", title, message, color,
                      str(seconds)], cwd=PROJECT_ROOT, **QUIET)


def alert(title: str, subtitle: str, message: str, color: str = "#e5484d") -> None:
    """Pop-up on screen, plus a notification so it is kept in Notification Center."""
    popup(f"{title}: {subtitle}", message, color)
    notify(title, subtitle, message)


def play_sound(path: str = DEFAULT_SOUND) -> None:
    if Path(path).exists():
        subprocess.Popen(["afplay", path], **QUIET)


def lock_screen() -> str:
    """Lock the Mac. Returns how it was done, for the on-screen log."""
    try:
        lib = ctypes.CDLL(LOGIN_FRAMEWORK)
        lib.SACLockScreenImmediate()
        return "locked"
    except (OSError, AttributeError):
        # Falls back to sleeping the display, which locks when
        # "Require password immediately after sleep" is on.
        subprocess.run(["pmset", "displaysleepnow"], check=False, **QUIET)
        return "display slept (fallback)"


NX_KEYTYPE_SOUND_UP, NX_KEYTYPE_SOUND_DOWN, NX_KEYTYPE_MUTE, NX_KEYTYPE_PLAY = 0, 1, 7, 16
NS_SYSTEM_DEFINED, MEDIA_KEY_SUBTYPE = 14, 8


def _press_media_key(key: int) -> bool:
    """Press a keyboard media key, so macOS shows its own volume pop-up.

    Returns False when this process may not post key events (no Accessibility
    permission); callers then fall back to AppleScript, which changes the volume
    without the pop-up.
    """
    try:
        import Quartz
        from AppKit import NSEvent
    except ImportError:
        return False
    if not Quartz.CGPreflightPostEventAccess():
        return False
    for down in (True, False):
        state = 0xA if down else 0xB
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            NS_SYSTEM_DEFINED, (0, 0), state << 8, 0, 0, None, MEDIA_KEY_SUBTYPE,
            (key << 16) | (state << 8), -1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())
    return True


def volume_up() -> bool:
    """One volume-key step up with the macOS pop-up. False = fell back, no pop-up."""
    if _press_media_key(NX_KEYTYPE_SOUND_UP):
        return True
    _osascript_async("set volume output volume ((output volume of (get volume settings)) + 6)")
    return False


def volume_down() -> bool:
    if _press_media_key(NX_KEYTYPE_SOUND_DOWN):
        return True
    _osascript_async("set volume output volume ((output volume of (get volume settings)) - 6)")
    return False


def toggle_mute() -> bool:
    if _press_media_key(NX_KEYTYPE_MUTE):
        return True
    _osascript_async("set volume output muted not (output muted of (get volume settings))")
    return False


def play_pause() -> bool:
    """Press the Play/Pause media key (pauses YouTube in Chrome, Spotify, Music...).

    False when key events are blocked (no Accessibility permission); there is no
    fallback because no AppleScript reaches every player.
    """
    return _press_media_key(NX_KEYTYPE_PLAY)


def mouse_position() -> tuple[float, float] | None:
    """Where the pointer is, in screen points (origin top-left of the main display)."""
    try:
        import Quartz
    except ImportError:
        return None
    location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (location.x, location.y)


def desktop_bounds() -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) of the box around all displays, in screen points."""
    try:
        import Quartz
    except ImportError:
        return None
    error, displays, _count = Quartz.CGGetActiveDisplayList(16, None, None)
    if error or not displays:
        return None
    rects = [Quartz.CGDisplayBounds(d) for d in displays]
    return (min(r.origin.x for r in rects), min(r.origin.y for r in rects),
            max(r.origin.x + r.size.width for r in rects),
            max(r.origin.y + r.size.height for r in rects))


CLICK_PRESS_S = 0.015  # how long the button stays down


def _post_mouse(kinds: tuple, position: tuple[float, float], button: int,
                click: bool = False) -> bool:
    """Post mouse events. False when this process may not (no Accessibility permission).

    For a click the events carry click count 1 and the button is held for a moment.
    Without the count, apps receive a "click number zero" and buttons and links ignore
    it (context menus don't check, which is why only right-click used to work).
    """
    try:
        import Quartz
    except ImportError:
        return False
    if not Quartz.CGPreflightPostEventAccess():
        return False
    for i, kind in enumerate(kinds):
        if click and i:
            time.sleep(CLICK_PRESS_S)
        event = Quartz.CGEventCreateMouseEvent(None, kind, position, button)
        if click:
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return True


def move_mouse(position: tuple[float, float]) -> bool:
    try:
        import Quartz
    except ImportError:
        return False
    return _post_mouse((Quartz.kCGEventMouseMoved,), position, Quartz.kCGMouseButtonLeft)


def click_mouse(position: tuple[float, float], right: bool = False) -> bool:
    """Press and release a mouse button at `position`."""
    try:
        import Quartz
    except ImportError:
        return False
    if right:
        return _post_mouse((Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp),
                           position, Quartz.kCGMouseButtonRight, click=True)
    return _post_mouse((Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp),
                       position, Quartz.kCGMouseButtonLeft, click=True)


def keystroke(script_body: str) -> str | None:
    """Send keys via System Events. Returns an error message, or None on success.

    Needs Accessibility permission for the terminal app.
    """
    result = subprocess.run(
        ["osascript", "-e", f'tell application "System Events" to {script_body}'],
        capture_output=True, text=True, timeout=3, check=False,
    )
    if result.returncode != 0:
        return ("Keystrokes blocked: allow Desk Guard / Python (or Terminal) in System "
                "Settings > Privacy & Security > Accessibility")
    return None


def next_tab() -> str | None:
    return keystroke("key code 48 using control down")  # ctrl+tab


def previous_tab() -> str | None:
    return keystroke("key code 48 using {control down, shift down}")  # ctrl+shift+tab
