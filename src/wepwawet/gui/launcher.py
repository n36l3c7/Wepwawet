"""Open Wepwawet as a desktop application.

The API starts on a free loopback port with a fresh session token, the window
opens ``/auth?token=...`` (which sets the session cookie), and closing the window
stops the server. ``--browser`` opens the system browser instead, for machines
where Qt WebEngine cannot run.
"""

import contextlib
import logging
import os
import sys
import threading
import webbrowser
from typing import Any

from wepwawet.api.app import create_app
from wepwawet.api.server import BackgroundServer, base_url, bind_socket, new_token
from wepwawet.config import Config

log = logging.getLogger(__name__)

WINDOW_TITLE = "Wepwawet"
WINDOW_BACKGROUND = "#100e0a"  # canvas colour of the Sand & turquoise palette, avoids a white flash
SMOKE_TEST_TIMEOUT_SECONDS = 60.0


class GuiUnavailableError(Exception):
    """The desktop window cannot be shown on this machine."""


def has_display(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def _import_webview() -> Any:
    os.environ.setdefault("QT_API", "pyside6")
    try:
        import webview
    except ImportError as exc:
        raise GuiUnavailableError(
            "The desktop window needs the 'gui' extra (pywebview with PySide6). "
            "Install it with: pip install 'wepwawet[gui]', or run: wepwawet gui --browser"
        ) from exc
    return webview


def launch(config: Config, *, browser: bool = False, smoke_test: bool = False) -> int:
    """Run the application until the window closes. Returns the process exit code."""
    config.check_bind_address()
    if not browser and sys.platform.startswith("linux") and not has_display():
        raise GuiUnavailableError(
            "No graphical display found (DISPLAY and WAYLAND_DISPLAY are unset). "
            "Use 'wepwawet serve' to start the server only, or see 'wepwawet --help'."
        )
    webview = None if browser else _import_webview()

    token = new_token()
    sock = bind_socket(config.server.host, config.server.port)
    ui_connected = threading.Event()
    server = BackgroundServer(create_app(config, token, on_ui_connected=ui_connected.set), sock)
    server.start()
    url = f"{base_url(sock)}/auth?token={token}"
    log.info("Local API ready", extra={"fields": {"url": base_url(sock)}})
    try:
        if browser:
            return _run_in_browser(url)
        return _run_in_window(webview, url, ui_connected if smoke_test else None)
    finally:
        server.stop()


def _run_in_browser(url: str) -> int:
    if not webbrowser.open(url):
        print(f"Open this address in a browser: {url}")
    print("Wepwawet is running. Press Ctrl+C to stop.")
    stop = threading.Event()
    with contextlib.suppress(KeyboardInterrupt):
        stop.wait()
    return 0


def _run_in_window(webview: Any, url: str, smoke_test: threading.Event | None) -> int:
    """Show the window. With ``smoke_test``, close it as soon as the UI connects and report success."""
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1440,
        height=900,
        min_size=(960, 640),
        background_color=WINDOW_BACKGROUND,
        text_select=True,
    )
    result = {"code": 0}
    if smoke_test is not None:
        # The UI opens /api/events only after React has mounted, so the event proves that Qt,
        # WebEngine, the session cookie, the JavaScript bundle and the API all work together.
        def watch() -> None:
            ok = smoke_test.wait(SMOKE_TEST_TIMEOUT_SECONDS)
            print(f"smoke-test: {'PASS' if ok else 'FAIL'} (interface {'connected' if ok else 'never connected'})")
            result["code"] = 0 if ok else 1
            window.destroy()

        threading.Thread(target=watch, name="smoke-test", daemon=True).start()

    # Exports (JSON, KML, PNG...) are downloads; pywebview blocks them unless allowed.
    settings = getattr(webview, "settings", None)
    if isinstance(settings, dict):
        settings["ALLOW_DOWNLOADS"] = True
    webview.start(gui="qt", private_mode=True)
    return result["code"]
