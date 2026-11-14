#!/usr/bin/env bash
# Run build.sh inside a clean Debian container (Docker or Podman), for hosts that are not Linux
# or lack the tools. The wheel must already exist in dist/. Packages land in dist/packages/.
#
# Usage: packaging/linux/build-in-container.sh [--platform linux/arm64]
set -euo pipefail

UV_VERSION="0.13.5"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE="$(command -v docker || command -v podman || true)"
[ -n "$ENGINE" ] || { echo "error: docker or podman is required" >&2; exit 1; }

mkdir -p "$ROOT/dist/packages"
"$ENGINE" run --rm "$@" \
  -v "$ROOT:/src:ro" -v "$ROOT/dist/packages:/out" \
  -e UV_VERSION="$UV_VERSION" \
  debian:trixie bash -euc '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends curl ca-certificates python3 file >/dev/null
    arch="$(uname -m)"
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${arch}-unknown-linux-gnu.tar.gz" \
      | tar -xz --strip-components=1 -C /usr/local/bin
    mkdir /work
    tar -C /src --exclude=./frontend/node_modules --exclude=./.venv --exclude=./build --exclude=./.git \
      --exclude=./dist/packages -cf - . | tar -xf - -C /work
    cd /work
    bash packaging/linux/build.sh
    cp dist/packages/* /out/
  '
