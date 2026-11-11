"""Small wrappers around macOS features. All AppleScript here is constant text."""

import ctypes
import subprocess
import sys
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


NX_KEYTYPE_SOUND_UP, NX_KEYTYPE_SOUND_DOWN, NX_KEYTYPE_MUTE = 0, 1, 7
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
