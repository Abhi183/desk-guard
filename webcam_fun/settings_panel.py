"""The settings window: native AppKit window with a WKWebView showing settings_ui.html.

Main thread only. The page calls Python through a WebKit message handler:
    window.webkit.messageHandlers.deskguard.postMessage(JSON.stringify({id, method, args}))
and gets the reply through window.__dgReply(id, reply). The app pushes fresh state with
push_state() whenever something changes (status, a menu click), so the page never polls.
"""

import json
from pathlib import Path

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSMakeRect,
    NSMakeSize,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSURL
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from webcam_fun.settings_api import SettingsApi

UI_FILE = Path(__file__).with_name("settings_ui.html")
HANDLER = "deskguard"
TITLE = "Desk Guard Settings"
AUTOSAVE = "DeskGuardSettings"
SIZE, MIN_SIZE = (920, 640), (780, 540)
STYLE = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
         | NSWindowStyleMaskResizable)
WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")
WKNavigationDelegate = objc.protocolNamed("WKNavigationDelegate")


def _js_call(function: str, *values) -> str:
    """A JavaScript call with JSON arguments (JSON is valid JavaScript)."""
    return f"{function}({', '.join(json.dumps(v) for v in values)})"


def _from_settings_page(message) -> bool:
    """Only the settings page itself may talk to Desk Guard: the main frame, loaded from a
    local file. If the view ever ended up on a web page, or in an iframe, it gets nothing."""
    frame = message.frameInfo()
    if frame is None or not frame.isMainFrame():
        return False
    origin = frame.securityOrigin()
    return origin is not None and origin.protocol() == "file"


class _Bridge(NSObject, protocols=[WKScriptMessageHandler]):
    def initWithPanel_(self, panel):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self.panel = panel
        return self

    def userContentController_didReceiveScriptMessage_(self, _controller, message) -> None:
        if not _from_settings_page(message):
            print("settings: ignored a message from outside the settings page", flush=True)
            return
        self.panel.handle(message.body())


class _Navigation(NSObject, protocols=[WKNavigationDelegate]):
    """Logs page load problems, and reloads the page if its web process dies."""

    def webView_didFailProvisionalNavigation_withError_(self, _web, _nav, error) -> None:
        print(f"settings page failed to load: {error}", flush=True)

    def webView_didFailNavigation_withError_(self, _web, _nav, error) -> None:
        print(f"settings page failed: {error}", flush=True)

    def webViewWebContentProcessDidTerminate_(self, web) -> None:
        print("settings page process ended; reloading", flush=True)
        web.reload()


class SettingsPanel:
    def __init__(self, api: SettingsApi) -> None:
        self.api = api
        self.loaded = False
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, *SIZE), STYLE, NSBackingStoreBuffered, False)
        self.window.setTitle_(TITLE)
        self.window.setReleasedWhenClosed_(False)
        self.window.setContentMinSize_(NSMakeSize(*MIN_SIZE))
        self.window.setTabbingMode_(2)  # NSWindowTabbingModeDisallowed
        self.window.setCollectionBehavior_(1 << 1)  # come to the current Space when opened
        if not self.window.setFrameUsingName_(AUTOSAVE):
            self.window.center()
        self.window.setFrameAutosaveName_(AUTOSAVE)
        self._bridge = _Bridge.alloc().initWithPanel_(self)
        self._navigation = _Navigation.alloc().init()
        self.web = self._make_web_view()
        self.window.setContentView_(self.web)

    def _make_web_view(self) -> WKWebView:
        controller = WKUserContentController.alloc().init()
        controller.addScriptMessageHandler_name_(self._bridge, HANDLER)
        config = WKWebViewConfiguration.alloc().init()
        config.setUserContentController_(controller)
        web = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, *SIZE), config)
        web.setValue_forKey_(False, "drawsBackground")  # no white flash before the page paints
        web.setNavigationDelegate_(self._navigation)
        base = NSURL.fileURLWithPath_(str(UI_FILE.parent))
        web.loadHTMLString_baseURL_(UI_FILE.read_text(), base)
        return web

    # ---------- bridge ----------
    def handle(self, body) -> None:
        try:
            message = json.loads(str(body))
            call_id, method, args = int(message["id"]), str(message["method"]), message["args"]
        except (KeyError, TypeError, ValueError):
            print(f"settings: bad message from page: {str(body)[:200]!r}", flush=True)
            return
        self.loaded = True
        try:
            reply = self.api.call(method, args)
        except Exception as e:  # noqa: BLE001 - the page must always get an answer
            print(f"settings: {method} failed: {e!r}", flush=True)
            reply = {"ok": False, "state": None, "error": "Something went wrong. See the log."}
        self._run(_js_call("window.__dgReply", call_id, reply))

    def push_state(self) -> None:
        """Send fresh state to the page (status changed, or settings changed elsewhere)."""
        if self.loaded and self.visible:
            self._run(_js_call("window.__dgPush", self.api.get_state()))

    def _run(self, script: str) -> None:
        self.web.evaluateJavaScript_completionHandler_(script, None)

    # ---------- window ----------
    def show(self, page: str | None = None) -> None:
        if not self.loaded:  # the page never reported in (first launch hiccup): load again
            self.web.loadHTMLString_baseURL_(UI_FILE.read_text(),
                                             NSURL.fileURLWithPath_(str(UI_FILE.parent)))
        if page:
            self._run(_js_call("window.__dgShowPage", page))
        self.window.makeKeyAndOrderFront_(None)
        self.push_state()

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

