#!/usr/bin/env bash
# Build the Linux packages (.deb, .rpm, Arch .pkg.tar.zst, AppImage) for this machine's architecture.
#
# Input:  dist/wepwawet-<version>-py3-none-any.whl, built by `npm run build` (frontend) then `uv build`.
# Output: dist/packages/
#
# Layout of the installed software:
#   /opt/wepwawet/python/   a standalone CPython 3.12 (python-build-standalone) with Wepwawet and PySide6
#   /usr/bin/wepwawet       launcher; /usr/bin/wpw is a symlink to it
#   /usr/share/applications/wepwawet.desktop, hicolor icon, licence
#
# Needs: bash, curl, tar, uv. Downloads pinned nfpm and appimagetool into build/tools.
set -euo pipefail

NFPM_VERSION="2.48.0"
APPIMAGETOOL_VERSION="1.9.1"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -n1)"
WHEEL="$ROOT/dist/wepwawet-${VERSION}-py3-none-any.whl"

case "$(uname -m)" in
  x86_64) PKG_ARCH="amd64"; NFPM_ARCH="x86_64"; APPIMAGE_ARCH="x86_64" ;;
  aarch64 | arm64) PKG_ARCH="arm64"; NFPM_ARCH="arm64"; APPIMAGE_ARCH="aarch64" ;;
  *) echo "error: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

BUILD="$ROOT/build/linux-$PKG_ARCH"
STAGE="$BUILD/root"
TOOLS="$ROOT/build/tools"
OUT="$ROOT/dist/packages"
PREFIX="/opt/wepwawet"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -n "$VERSION" ] || die "cannot read the version from pyproject.toml"
[ -f "$WHEEL" ] || die "missing $WHEEL; run 'npm run build' in frontend/ and then 'uv build'"
python3 -c "import sys, zipfile; sys.exit('wepwawet/web/dist/index.html' not in zipfile.ZipFile(sys.argv[1]).namelist())" "$WHEEL" \
  || die "the wheel has no frontend; run 'npm run build' before 'uv build'"
command -v uv >/dev/null || die "uv is required"

fetch_tools() {
  mkdir -p "$TOOLS"
  if [ ! -x "$TOOLS/nfpm" ]; then
    log "Downloading nfpm $NFPM_VERSION"
    curl -fsSL "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_${NFPM_ARCH}.tar.gz" \
      | tar -xz -C "$TOOLS" nfpm
  fi
  if [ ! -x "$TOOLS/appimagetool" ]; then
    log "Downloading appimagetool $APPIMAGETOOL_VERSION"
    curl -fsSL -o "$TOOLS/appimagetool" \
      "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-${APPIMAGE_ARCH}.AppImage"
    chmod +x "$TOOLS/appimagetool"
  fi
}

stage_python() {
  log "Installing a standalone Python $PYTHON_VERSION"
  local installs="$BUILD/pythons"
  UV_PYTHON_INSTALL_DIR="$installs" uv python install --no-bin "$PYTHON_VERSION" >/dev/null
  local src
  src="$(find "$installs" -maxdepth 1 -type d -name "cpython-${PYTHON_VERSION}*-linux-*" | sort | tail -n1)"
  [ -n "$src" ] || die "uv did not install Python $PYTHON_VERSION"
  mkdir -p "$STAGE$PREFIX"
  cp -a "$src" "$STAGE$PREFIX/python"
  # uv marks its interpreters as externally managed; this copy belongs to the package.
  find "$STAGE$PREFIX/python/lib" -maxdepth 2 -name EXTERNALLY-MANAGED -delete
}

stage_wepwawet() {
  log "Installing Wepwawet $VERSION with the desktop window (PySide6)"
  uv pip install --quiet --python "$STAGE$PREFIX/python/bin/python3" --compile-bytecode \
    "wepwawet[gui] @ file://$WHEEL"
  # Test suites and headers are not needed at run time.
  rm -rf "$STAGE$PREFIX/python/lib/python${PYTHON_VERSION}/test" \
         "$STAGE$PREFIX/python/lib/python${PYTHON_VERSION}/idlelib" \
         "$STAGE$PREFIX/python/include"
  log "Pruning unused Qt modules"
  "$STAGE$PREFIX/python/bin/python3" -I "$ROOT/packaging/linux/prune_qt.py" \
    "$STAGE$PREFIX/python/lib/python${PYTHON_VERSION}/site-packages"
}

stage_desktop_files() {
  log "Adding launcher, desktop entry and icon"
  install -Dm755 "$ROOT/packaging/linux/wepwawet.sh" "$STAGE/usr/bin/wepwawet"
  install -Dm644 "$ROOT/packaging/linux/wepwawet.desktop" "$STAGE/usr/share/applications/wepwawet.desktop"
  install -Dm644 "$ROOT/frontend/public/wepwawet.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/wepwawet.svg"
  install -Dm644 "$ROOT/LICENSE" "$STAGE/usr/share/licenses/wepwawet/LICENSE"
}

build_native_packages() {
  mkdir -p "$OUT"
  # nfpm does not expand variables in `contents`, so resolve the placeholders here.
  local config="$BUILD/nfpm.yaml"
  sed -e "s|\${VERSION}|$VERSION|g" -e "s|\${PKG_ARCH}|$PKG_ARCH|g" -e "s|\${STAGE}|$STAGE|g" \
    "$ROOT/packaging/linux/nfpm.yaml" >"$config"
  for packager in deb rpm archlinux; do
    log "Packaging $packager"
    "$TOOLS/nfpm" package --config "$config" --packager "$packager" --target "$OUT/" >/dev/null
  done
}

build_appimage() {
  log "Building the AppImage"
  local appdir="$BUILD/Wepwawet.AppDir"
  rm -rf "$appdir"
  cp -a "$STAGE" "$appdir"
  install -Dm755 "$ROOT/packaging/linux/AppRun" "$appdir/AppRun"
  cp "$ROOT/packaging/linux/wepwawet.desktop" "$appdir/wepwawet.desktop"
  cp "$ROOT/frontend/public/wepwawet.svg" "$appdir/wepwawet.svg"
  ln -sf wepwawet.svg "$appdir/.DirIcon"
  # --appimage-extract-and-run: works in containers and CI without FUSE.
  ARCH="$APPIMAGE_ARCH" "$TOOLS/appimagetool" --appimage-extract-and-run --no-appstream \
    "$appdir" "$OUT/Wepwawet-${VERSION}-${APPIMAGE_ARCH}.AppImage" >/dev/null
}

main() {
  rm -rf "$BUILD"
  mkdir -p "$BUILD"
  fetch_tools
  stage_python
  stage_wepwawet
  stage_desktop_files
  "$STAGE$PREFIX/python/bin/python3" -I -m wepwawet --version
  build_native_packages
  build_appimage
  log "Done:"
  ls -lh "$OUT"
}

main "$@"
