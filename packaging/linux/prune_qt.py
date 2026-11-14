"""Remove the parts of PySide6 that the Wepwawet window never loads.

PySide6 ships all of Qt (3D, Multimedia, Charts, QML tooling, Designer...). The window
needs Qt WebEngine and what it links to. The set of Qt libraries to keep is computed,
not listed: it is the ``ldd`` closure of the Python modules pywebview imports, of the
WebEngine helper process and of the platform plugins. Everything else is deleted.

Usage: prune_qt.py <site-packages directory>
"""

import re
import shutil
import subprocess  # noqa: S404 - fixed argv lists, no shell
import sys
from pathlib import Path

# Python bindings imported by pywebview's Qt backend (webview/platforms/qt.py).
KEEP_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets"}
# Plugin families needed to open a window on X11 or Wayland, with TLS, images and input methods.
KEEP_PLUGIN_DIRS = {
    "platforms",
    "xcbglintegrations",
    "wayland-decoration-client",
    "wayland-graphics-integration-client",
    "wayland-shell-integration",
    "platforminputcontexts",
    "platformthemes",
    "imageformats",
    "iconengines",
    "tls",
    "egldeviceintegrations",
}
# Build-time or tooling content.
DROP_DIRS = ["include", "typesystems", "glue", "scripts", "support", "Qt/metatypes", "Qt/qml"]
WEBENGINE_LOCALE_KEEP = {"en-US.pak"}

_LDD_LINE = re.compile(r"^\s*(\S+)\s+=>\s+(\S+)")


def ldd(paths: list[Path], qt_lib: Path) -> set[str]:
    """Names of the Qt libraries that ``paths`` load, directly or indirectly."""
    needed: set[str] = set()
    env = {"LD_LIBRARY_PATH": str(qt_lib), "PATH": "/usr/bin:/bin"}
    for path in paths:
        result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, env=env, check=False)  # noqa: S603, S607
        for line in result.stdout.splitlines():
            match = _LDD_LINE.match(line)
            if match and Path(match.group(2)).parent == qt_lib:
                needed.add(match.group(1))
    return needed


def size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def remove(path: Path) -> int:
    freed = size(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return freed


def prune(site_packages: Path) -> int:
    pyside = site_packages / "PySide6"
    qt = pyside / "Qt"
    qt_lib = qt / "lib"
    freed = 0

    # 1. Plugins: keep the needed families only.
    for directory in (qt / "plugins").iterdir():
        if directory.name not in KEEP_PLUGIN_DIRS:
            freed += remove(directory)

    # 2. Qt libraries: keep the ldd closure of what remains.
    roots = [pyside / f"{name}.abi3.so" for name in KEEP_MODULES]
    roots += list((qt / "plugins").rglob("*.so"))
    roots += list((qt / "libexec").glob("QtWebEngineProcess"))
    needed = ldd(roots, qt_lib)
    if "libQt6WebEngineCore.so.6" not in needed:
        sys.exit("error: the closure does not include Qt WebEngine; refusing to prune")
    stems = {name.split(".so")[0] for name in needed}
    for lib in qt_lib.iterdir():
        if lib.name.split(".so")[0] not in stems:
            freed += remove(lib)

    # 3. Python bindings: keep a module when its Qt library survived.
    for module in pyside.glob("Qt*.abi3.so"):
        name = module.name.removesuffix(".abi3.so")
        if name not in KEEP_MODULES and f"libQt6{name.removeprefix('Qt')}" not in stems:
            freed += remove(module)
    for stub in pyside.glob("*.pyi"):
        freed += remove(stub)

    # 4. Qt tools shipped as executables (designer, assistant, qmlls...) and build-time folders.
    for entry in pyside.iterdir():
        if entry.is_file() and entry.suffix == "" and entry.read_bytes()[:4] == b"\x7fELF":
            freed += remove(entry)
    for relative in DROP_DIRS:
        target = pyside / relative
        if target.exists():
            freed += remove(target)

    # 5. Translations: the interface is in English.
    translations = qt / "translations"
    for entry in translations.iterdir():
        if entry.name == "qtwebengine_locales":
            for locale in entry.iterdir():
                if locale.name not in WEBENGINE_LOCALE_KEEP:
                    freed += remove(locale)
        else:
            freed += remove(entry)

    # 6. pip is never used inside the packaged interpreter.
    for entry in site_packages.glob("pip*"):
        freed += remove(entry)
    return freed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    total = prune(Path(sys.argv[1]))
    print(f"pruned {total / 2**20:.0f} MiB")
