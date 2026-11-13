"""The live video window: a native AppKit window that shows the watcher's frames.

Main thread only. The app calls show_frame() from a ~30 Hz timer with the newest frame.
The title bar has a gear (settings) and a pin (keep on top). Closing the window only
hides the video; Desk Guard keeps watching.
"""

from typing import Callable

import cv2
import numpy as np
import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSNormalWindowLevel,
    NSTextField,
    NSTitlebarAccessoryViewController,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSData, NSNull, NSObject
from Quartz import (
    CALayer,
    CGColorSpaceCreateDeviceRGB,
    CGDataProviderCreateWithCFData,
    CGImageCreate,
    kCGImageAlphaNoneSkipLast,
    kCGRenderingIntentDefault,
)

TITLE = "Desk Guard"
AUTOSAVE = "DeskGuardVideo"
SIZE = (480, 360)
MIN_SIZE = (320, 240)
LAYOUT_RIGHT = 2  # NSLayoutAttributeRight, for the title bar buttons
# Pinned, the window behaves like picture-in-picture: on every Space and above full-screen
# apps. Unpinned, it is an ordinary window that comes to whichever Space you are on.
ALL_SPACES, MOVE_TO_ACTIVE, FULLSCREEN_AUX = 1 << 0, 1 << 1, 1 << 8
STYLE = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
         | NSWindowStyleMaskResizable)
# Keys the watcher understands while this window is focused (s opens settings here).
WATCHER_KEYS = frozenset("mclgf")


def frame_to_cgimage(frame_bgr: np.ndarray):
    """A CGImage of a BGR frame (copies the pixels, so the frame may be reused)."""
    rgba = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA)
    h, w = rgba.shape[:2]
    data = NSData.dataWithBytes_length_(rgba.tobytes(), rgba.nbytes)
    return CGImageCreate(w, h, 8, 32, w * 4, CGColorSpaceCreateDeviceRGB(),
                         kCGImageAlphaNoneSkipLast, CGDataProviderCreateWithCFData(data),
                         None, False, kCGRenderingIntentDefault)


class _VideoNSWindow(NSWindow):
    """Passes key presses to the app."""

    def keyDown_(self, event) -> None:
        chars = event.charactersIgnoringModifiers() or ""
        handler = getattr(self, "on_key", None)
        if handler is None or not chars or not handler(chars.lower()):
            objc.super(_VideoNSWindow, self).keyDown_(event)


class _Actions(NSObject):
    """Target for the title bar buttons and the window delegate."""

    def initWithOwner_(self, owner):
        self = objc.super(_Actions, self).init()
        if self is None:
            return None
        self.owner = owner
        return self

    def openSettings_(self, _sender) -> None:
        self.owner.on_settings()

    def togglePin_(self, _sender) -> None:
        self.owner.on_pin(not self.owner.on_top)

    def windowShouldClose_(self, _window) -> bool:
        self.owner.on_close()  # hide instead of closing: the app keeps running
        return False


def _symbol_button(symbol: str, tip: str, target, action: str, x: float) -> NSButton:
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, tip)
    button = NSButton.buttonWithImage_target_action_(image, target, action)
    button.setBordered_(False)
    button.setToolTip_(tip)
    button.setFrame_(NSMakeRect(x, 2, 26, 22))
    return button


class VideoWindow:
    def __init__(self, on_settings: Callable[[], None], on_pin: Callable[[bool], None],
                 on_close: Callable[[], None], on_key: Callable[[str], bool]) -> None:
        self.on_settings, self.on_pin, self.on_close = on_settings, on_pin, on_close
        self.on_top = True
        self._actions = _Actions.alloc().initWithOwner_(self)
        self.window = _VideoNSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, *SIZE), STYLE, NSBackingStoreBuffered, False)
        self.window.on_key = on_key
        self.window.setTitle_(TITLE)
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self._actions)
        self.window.setContentAspectRatio_(NSMakeSize(4, 3))
        self.window.setContentMinSize_(NSMakeSize(*MIN_SIZE))
        self.window.setBackgroundColor_(NSColor.blackColor())
        if not self.window.setFrameUsingName_(AUTOSAVE):
            self._place_top_right()
        self.window.setFrameAutosaveName_(AUTOSAVE)
        self._view = self._make_video_view()
        self._placeholder = self._make_placeholder()
        self._pin = self._add_title_buttons()
        self.set_on_top(True)

    # ---------- building ----------
    def _make_video_view(self) -> NSView:
        """A layer-hosting view: we own its layer and set each frame as its contents."""
        content = self.window.contentView()
        layer = CALayer.layer()
        layer.setBackgroundColor_(NSColor.blackColor().CGColor())
        layer.setContentsGravity_("resizeAspect")
        layer.setActions_({"contents": NSNull.null()})  # no 0.25 s cross-fade per frame
        view = NSView.alloc().initWithFrame_(content.bounds())
        view.setLayer_(layer)  # before setWantsLayer_: makes it layer-hosting
        view.setWantsLayer_(True)
        view.setAutoresizingMask_(2 | 16)  # follow the window's width and height
        content.addSubview_(view)
        self._layer = layer
        return content

    def _make_placeholder(self) -> NSTextField:
        label = NSTextField.labelWithString_("Starting camera…")
        label.setTextColor_(NSColor.secondaryLabelColor())
        label.setFont_(NSFont.systemFontOfSize_(15))
        label.sizeToFit()
        frame = self._view.bounds()
        size = label.frame().size
        label.setFrameOrigin_(((frame.size.width - size.width) / 2,
                               (frame.size.height - size.height) / 2))
        label.setAutoresizingMask_(1 | 4 | 8 | 32)  # keep centred while resizing
        self._view.addSubview_(label)
        return label

    def _add_title_buttons(self) -> NSButton:
        container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 64, 26))
        pin = _symbol_button("pin.fill", "Keep on top", self._actions, "togglePin:", 2)
        gear = _symbol_button("gearshape", "Settings (S)", self._actions, "openSettings:", 32)
        container.addSubview_(pin)
        container.addSubview_(gear)
        accessory = NSTitlebarAccessoryViewController.alloc().init()
        accessory.setView_(container)
        accessory.setLayoutAttribute_(LAYOUT_RIGHT)
        self.window.addTitlebarAccessoryViewController_(accessory)
        self._accessory = accessory  # keep a reference
        return pin

    def _place_top_right(self) -> None:
        from AppKit import NSScreen
        screen = NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + screen.size.width - SIZE[0] - 20
        y = screen.origin.y + screen.size.height - SIZE[1] - 40
        self.window.setFrameOrigin_((x, y))

    # ---------- use ----------
    def show(self, focus: bool = False) -> None:
        if focus:
            self.window.makeKeyAndOrderFront_(None)
        else:
            self.window.orderFront_(None)

    def hide(self) -> None:
        self.window.orderOut_(None)

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

    def set_on_top(self, on: bool) -> None:
        self.on_top = on
        self.window.setLevel_(NSFloatingWindowLevel if on else NSNormalWindowLevel)
        self.window.setCollectionBehavior_(ALL_SPACES | FULLSCREEN_AUX if on else MOVE_TO_ACTIVE)
        symbol = "pin.fill" if on else "pin"
        self._pin.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol, "Keep on top"))
        self._pin.setToolTip_("Floating above other windows" if on else "Keep on top")

    def set_message(self, text: str | None) -> None:
        """Grey text in the middle while there is no video (starting, stopped...)."""
        if text is None:
            self._placeholder.setHidden_(True)
            return
        self._placeholder.setStringValue_(text)
        self._placeholder.sizeToFit()
        frame, size = self._view.bounds(), self._placeholder.frame().size
        self._placeholder.setFrameOrigin_(((frame.size.width - size.width) / 2,
                                           (frame.size.height - size.height) / 2))
        self._placeholder.setHidden_(False)
        self._layer.setContents_(None)

    def show_frame(self, frame_bgr: np.ndarray) -> None:
        if not self._placeholder.isHidden():
            self._placeholder.setHidden_(True)
        self._layer.setContents_(frame_to_cgimage(frame_bgr))
