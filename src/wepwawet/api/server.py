"""Run the API with uvicorn, in the foreground or on a background thread (for the desktop window)."""

import logging
import secrets
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

log = logging.getLogger(__name__)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def bind_socket(host: str, port: int) -> socket.socket:
    """Bind the listening socket ourselves so port 0 resolves to a known free port before start-up."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


def base_url(sock: socket.socket) -> str:
    host, port = sock.getsockname()[:2]
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}"


def _uvicorn_config(app: FastAPI) -> uvicorn.Config:
    # log_config=None keeps our structured logging instead of uvicorn's.
    return uvicorn.Config(app, log_config=None, access_log=False, lifespan="on")


def serve_forever(app: FastAPI, sock: socket.socket) -> None:
    uvicorn.Server(_uvicorn_config(app)).run(sockets=[sock])


class BackgroundServer:
    """uvicorn on a daemon thread; ``start()`` returns once it accepts connections."""

    def __init__(self, app: FastAPI, sock: socket.socket) -> None:
        self._server = uvicorn.Server(_uvicorn_config(app))
        self._sock = sock
        self._thread = threading.Thread(target=self._run, name="wepwawet-api", daemon=True)
        self._error: BaseException | None = None

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._sock])
        except BaseException as exc:  # reported by start(); a thread cannot raise into its caller
            self._error = exc
            log.exception("API server stopped with an error")

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if self._error is not None or not self._thread.is_alive():
                raise RuntimeError("The local API server failed to start; see the log for details.")
            if time.monotonic() > deadline:
                raise TimeoutError(f"The local API server did not start within {timeout:.0f} s.")
            time.sleep(0.05)

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout)
