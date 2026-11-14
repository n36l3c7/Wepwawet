#!/usr/bin/env bash
# Install a built package inside a clean distribution and check that it works, window included.
# Runs as root inside a container:  test-install.sh <deb|rpm|archlinux|appimage> <package file>
#
# Checks: the package installs with its declared dependencies, no bundled library has an
# unresolved dependency, the CLI answers, and the window opens under Xvfb and loads the UI.
set -euo pipefail

kind="$1"
package="$2"
log() { printf '==> %s\n' "$*"; }

log "Installing $package"
case "$kind" in
  deb)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends "$package" xvfb xauth ${EXTRA_PACKAGES:-} >/dev/null
    ;;
  rpm)
    dnf install -y -q "$package" xorg-x11-server-Xvfb xorg-x11-xauth which >/dev/null
    ;;
  archlinux)
    pacman -Sy --noconfirm --needed xorg-server-xvfb xorg-xauth which >/dev/null
    pacman -U --noconfirm "$package" >/dev/null
    ;;
  appimage)
    # AppImage: no package manager; the host must provide the same system libraries.
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends xvfb xauth libgl1 libegl1 libfontconfig1 libdbus-1-3 \
      libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
      libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libx11-xcb1 libnss3 \
      libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libxfixes3 libgssapi-krb5-2 >/dev/null
    apt-get install -y -qq --no-install-recommends libglib2.0-0t64 >/dev/null 2>&1 \
      || apt-get install -y -qq --no-install-recommends libglib2.0-0 >/dev/null
    apt-get install -y -qq --no-install-recommends libasound2t64 >/dev/null 2>&1 \
      || apt-get install -y -qq --no-install-recommends libasound2 >/dev/null
    cp "$package" /tmp/Wepwawet.AppImage
    chmod +x /tmp/Wepwawet.AppImage
    (cd /opt && /tmp/Wepwawet.AppImage --appimage-extract >/dev/null && mv squashfs-root wepwawet-appimage)
    ln -sf /opt/wepwawet-appimage/AppRun /usr/local/bin/wepwawet
    ;;
  *) echo "unknown kind $kind" >&2; exit 2 ;;
esac

root=/opt/wepwawet
[ "$kind" = appimage ] && root=/opt/wepwawet-appimage/opt/wepwawet

log "Looking for unresolved shared libraries in Qt WebEngine"
pyside="$(find "$root/python/lib" -maxdepth 3 -type d -name PySide6 | head -n1)"
missing="$(
  { find "$pyside/Qt/lib" -name 'libQt6WebEngine*.so*' -o -name 'libQt6Gui.so*' -o -name 'libQt6XcbQpa.so*'
    find "$pyside/Qt/plugins/platforms" -name 'libqxcb.so'
    find "$pyside/Qt/plugins/xcbglintegrations" -name '*.so' 2>/dev/null
    find "$pyside/Qt/libexec" -name 'QtWebEngineProcess'
  } | while read -r f; do LD_LIBRARY_PATH="$pyside/Qt/lib" ldd "$f" | grep 'not found' || true; done | sort -u
)"
if [ -n "$missing" ]; then
  echo "$missing"
  echo "FAIL: unresolved libraries (add them to the package dependencies)" >&2
  exit 1
fi

log "CLI"
wepwawet --version
wpw --version 2>/dev/null || wepwawet --version
wepwawet doctor || true

log "Window smoke test under Xvfb"
export QTWEBENGINE_DISABLE_SANDBOX=1  # containers run as root; the Chromium sandbox refuses root
export QT_QUICK_BACKEND=software
export QTWEBENGINE_CHROMIUM_FLAGS="--use-angle=swiftshader --enable-unsafe-swiftshader"
timeout 120 xvfb-run -a -s "-screen 0 1440x900x24" wepwawet gui --smoke-test
log "PASS"
