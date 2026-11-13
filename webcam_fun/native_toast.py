"""In-app pop-ups: small floating panels in the top-right corner, drawn by AppKit.

Main thread only (the app hands calls over with AppHelper.callAfter). They never take
focus, show on every Space, stack when several arrive together, and fade out. Click one
to dismiss it. Replaces the separate Tk process used when running from Terminal.
"""

from AppKit import (
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSView,
    NSVisualEffectView,
)
from PyObjCTools import AppHelper

WIDTH, HEIGHT = 360, 78
MARGIN_X, MARGIN_Y, GAP = 16, 12, 8
MAX_SHOWN = 4
FADE_S = 0.35
BORDERLESS, NONACTIVATING = 0, 1 << 7
ALL_SPACES, STATIONARY, IGNORES_CYCLE = 1 << 0, 1 << 4, 1 << 6
HUD_MATERIAL, ACTIVE_STATE = 13, 1  # NSVisualEffectMaterialHUDWindow, ...StateActive


def parse_hex(color: str) -> NSColor:
    text = color.lstrip("#")
    try:
        r, g, b = (int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        r, g, b = 0.9, 0.28, 0.3
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)


class _ClickView(NSView):
    def mouseDown_(self, _event) -> None:
        handler = getattr(self, "on_click", None)
        if handler is not None:
            handler()


def _label(text: str, size: float, bold: bool, color: NSColor, frame) -> NSTextField:
    label = NSTextField.wrappingLabelWithString_(text)
    label.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    label.setTextColor_(color)
    label.setFrame_(frame)
    label.setMaximumNumberOfLines_(2)
    return label


class Toasts:
    def __init__(self) -> None:
        self._shown: list[NSPanel] = []

    def show(self, title: str, message: str, color: str = "#e5484d",
             seconds: float = 5.0) -> None:
        while len(self._shown) >= MAX_SHOWN:
            self._close(self._shown[0], fade=False)
        panel = self._make_panel(title[:60], message[:160], parse_hex(color))
        self._shown.append(panel)
        self._layout()
        panel.orderFrontRegardless()
        AppHelper.callLater(max(1.0, min(seconds, 30.0)), self._close, panel)

    def _make_panel(self, title: str, message: str, accent: NSColor) -> NSPanel:
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), BORDERLESS | NONACTIVATING,
            NSBackingStoreBuffered, False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(ALL_SPACES | STATIONARY | IGNORES_CYCLE)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)

        blur = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        blur.setMaterial_(HUD_MATERIAL)
        blur.setState_(ACTIVE_STATE)
        blur.setWantsLayer_(True)
        blur.layer().setCornerRadius_(12)
        blur.layer().setMasksToBounds_(True)

        stripe = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 5, HEIGHT))
        stripe.setWantsLayer_(True)
        stripe.layer().setBackgroundColor_(accent.CGColor())
        blur.addSubview_(stripe)
        blur.addSubview_(_label(title, 13.5, True, NSColor.labelColor(),
                                NSMakeRect(18, HEIGHT - 30, WIDTH - 30, 20)))
        blur.addSubview_(_label(message, 12, False, NSColor.secondaryLabelColor(),
                                NSMakeRect(18, 8, WIDTH - 30, HEIGHT - 40)))

        click = _ClickView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        click.on_click = lambda: self._close(panel)
        blur.addSubview_(click)
        panel.setContentView_(blur)
        return panel

    def _layout(self) -> None:
        screen = NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + screen.size.width - WIDTH - MARGIN_X
        top = screen.origin.y + screen.size.height - MARGIN_Y
        for i, panel in enumerate(reversed(self._shown)):  # newest at the top
            panel.setFrameOrigin_((x, top - HEIGHT - i * (HEIGHT + GAP)))

    def _close(self, panel: NSPanel, fade: bool = True) -> None:
        if panel not in self._shown:
            return
        self._shown.remove(panel)
        if not fade:
            panel.orderOut_(None)
            self._layout()
            return

        def changes(context) -> None:
            context.setDuration_(FADE_S)
            panel.animator().setAlphaValue_(0.0)

        def done() -> None:
            panel.orderOut_(None)
            self._layout()

        NSAnimationContext.runAnimationGroup_completionHandler_(changes, done)

