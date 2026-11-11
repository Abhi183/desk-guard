"""Dock icon control for our Python processes (they have no app bundle of their own)."""

from pathlib import Path

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def hide_from_dock() -> None:
    """Run as an accessory: windows still work, but no Dock icon or app menu."""
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
    except ImportError:
        pass  # pyobjc missing: worst case an extra Dock icon


def use_desk_guard_icon() -> None:
    """Replace the generic Python icon with the Desk Guard eye."""
    try:
        from AppKit import NSApplication, NSImage
    except ImportError:
        return
    image = NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH))
    if image is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(image)


QUIT_ALL_REQUEST = Path(__file__).resolve().parent.parent / ".quit_all"
_quit_handler = None  # strong reference: the Apple Event manager doesn't retain it


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def on_dock_quit(callback) -> None:
    """Call `callback()` when the Dock (or anything else) sends this app 'Quit'.

    OpenCV's window code swallows the normal quit request, so we take over the
    'quit' Apple Event ourselves. The callback runs inside cv2.waitKey().
    """
    global _quit_handler
    try:
        import objc
        from Foundation import NSAppleEventManager, NSObject
    except ImportError:
        return

    class _QuitHandler(NSObject):
        def handleQuit_withReplyEvent_(self, _event, _reply):
            callback()
        handleQuit_withReplyEvent_ = objc.selector(handleQuit_withReplyEvent_,
                                                   signature=b"v@:@@")

    _quit_handler = _QuitHandler.alloc().init()
    NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
        _quit_handler, b"handleQuit:withReplyEvent:", _fourcc("aevt"), _fourcc("quit"))
