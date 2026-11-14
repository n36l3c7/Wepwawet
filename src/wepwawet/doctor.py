"""``wepwawet doctor``: check that this machine can run every part of Wepwawet."""

import importlib
import os
import shutil
import subprocess  # noqa: S404 - fixed argv lists, no shell
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from wepwawet.config import ConfigError, load_config
from wepwawet.gui.launcher import has_display
from wepwawet.paths import AppPaths, default_paths


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""


def check_python() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 12):  # noqa: UP036 - reports an interpreter started outside the package
        return Check("Python", Status.OK, version)
    return Check("Python", Status.FAIL, version, "Wepwawet needs Python 3.12 or newer.")


def check_config(paths: AppPaths) -> Check:
    try:
        load_config(paths)
    except ConfigError as exc:
        return Check("Configuration", Status.FAIL, str(exc), "Fix or remove the file.")
    where = paths.config_file if paths.config_file.is_file() else "defaults (no config.toml)"
    return Check("Configuration", Status.OK, str(where))


def check_directories(paths: AppPaths) -> Check:
    try:
        paths.ensure()
    except OSError as exc:
        return Check("Directories", Status.FAIL, str(exc))
    unwritable = [str(d) for d in (paths.config_dir, paths.data_dir, paths.cache_dir) if not os.access(d, os.W_OK)]
    if unwritable:
        return Check("Directories", Status.FAIL, "not writable: " + ", ".join(unwritable))
    return Check("Directories", Status.OK, f"{paths.data_dir}, {paths.cache_dir}")


def check_gui() -> Check:
    os.environ.setdefault("QT_API", "pyside6")
    try:
        importlib.import_module("webview")
        importlib.import_module("PySide6.QtWebEngineWidgets")
    except ImportError as exc:
        return Check(
            "Desktop window",
            Status.WARN,
            f"Qt WebEngine cannot load: {exc}",
            "Install the system Qt libraries listed in the docs, or use 'wepwawet gui --browser'.",
        )
    if not has_display():
        return Check("Desktop window", Status.WARN, "no DISPLAY or WAYLAND_DISPLAY", "Use 'wepwawet serve'.")
    return Check("Desktop window", Status.OK, "Qt WebEngine available")


def _has_raw_socket_capability(binary: str) -> bool:
    getcap = shutil.which("getcap")
    if getcap is None:
        return False
    result = subprocess.run([getcap, binary], capture_output=True, text=True, check=False, timeout=5)  # noqa: S603
    return "cap_net_raw" in result.stdout


def check_traceroute_tools(which: Callable[[str], str | None] = shutil.which) -> Check:
    found = {name: which(name) for name in ("mtr", "scamper", "traceroute", "tracepath")}
    available = [name for name, path in found.items() if path]
    if not available:
        return Check(
            "Local traceroute",
            Status.WARN,
            "none of mtr, scamper, traceroute, tracepath found",
            "Install mtr (Debian/Ubuntu/Kali: mtr-tiny, Fedora/Arch: mtr) to measure from this machine.",
        )
    detail = "found: " + ", ".join(available)
    scamper = found["scamper"]
    if scamper and not _has_raw_socket_capability(scamper):
        return Check(
            "Local traceroute",
            Status.WARN,
            detail + " (scamper lacks CAP_NET_RAW)",
            f"sudo setcap cap_net_raw+ep {scamper}  (never run Wepwawet itself as root)",
        )
    return Check("Local traceroute", Status.OK, detail)


def run_checks(paths: AppPaths | None = None) -> list[Check]:
    paths = paths or default_paths()
    return [
        check_python(),
        check_config(paths),
        check_directories(paths),
        check_gui(),
        check_traceroute_tools(),
    ]
