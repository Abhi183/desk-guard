"""Closes personal apps and browser tabs when someone sits down with you.

Apps are asked to quit the normal way (same as Cmd+Q), so nothing is force-killed.
Browser tabs are closed with AppleScript; macOS asks once per browser for permission
("Python wants to control Google Chrome"). All AppleScript here is built from the
constants below, never from outside input.
"""

import subprocess

# bundle id -> name shown in the pop-up and the log
PERSONAL_APPS = {
    "com.apple.MobileSMS": "Messages",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "desktop.WhatsApp": "WhatsApp",
    "com.burbn.instagram": "Instagram",
}
# Tabs whose address contains one of these are closed.
PERSONAL_SITES = ("//www.instagram.com", "//web.whatsapp.com")
# Browsers with AppleScript tab support. Firefox has none, so its tabs are left alone.
BROWSERS = {
    "com.google.Chrome": "Chrome",
    "com.brave.Browser": "Brave",
    "com.apple.Safari": "Safari",
}
SCRIPT_TIMEOUT_S = 15


def _running(bundle_id: str) -> list:
    try:
        from AppKit import NSRunningApplication
    except ImportError:
        return []
    return list(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id))


def running_personal_apps() -> list[str]:
    return sorted({name for bundle_id, name in PERSONAL_APPS.items() if _running(bundle_id)})


def quit_personal_apps() -> list[str]:
    """Quit the personal apps that are open. Returns the names of the ones asked to quit."""
    closed = set()
    for bundle_id, name in PERSONAL_APPS.items():
        for app in _running(bundle_id):
            if app.terminate():
                closed.add(name)
    return sorted(closed)


def ask_tab_permission() -> None:
    """Make macOS show its "Python wants to control <browser>" prompt now, at start-up.

    Otherwise the prompt would come up the first time a visitor sits down, and the tabs
    would stay open while it waits for an answer. Only asks browsers that are running.
    """
    for bundle_id in BROWSERS:
        if _running(bundle_id):
            subprocess.Popen(["osascript", "-e",
                              f'tell application id "{bundle_id}" to count windows'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _close_tabs_script(bundle_id: str) -> str:
    matches = " or ".join(f'u contains "{site}"' for site in PERSONAL_SITES)
    return f'''
tell application id "{bundle_id}"
    set closedCount to 0
    repeat with w in windows
        repeat with i from (count of tabs of w) to 1 by -1
            try
                set u to URL of tab i of w
                if {matches} then
                    close tab i of w
                    set closedCount to closedCount + 1
                end if
            end try
        end repeat
    end repeat
    return closedCount
end tell'''


def close_personal_tabs() -> tuple[int, list[str]]:
    """Close personal-site tabs in every running browser. Returns (tabs closed, errors)."""
    total, errors = 0, []
    for bundle_id, name in BROWSERS.items():
        if not _running(bundle_id):
            continue  # asking a closed browser would launch it
        try:
            result = subprocess.run(["osascript", "-e", _close_tabs_script(bundle_id)],
                                    capture_output=True, text=True, check=False,
                                    timeout=SCRIPT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            errors.append(f"{name}: no answer (permission prompt still open?)")
            continue
        if result.returncode != 0:
            errors.append(f"{name}: {result.stderr.strip() or 'could not close tabs'}")
            continue
        try:
            total += int(result.stdout.strip() or 0)
        except ValueError:
            errors.append(f"{name}: unexpected reply {result.stdout.strip()!r}")
    return total, errors
