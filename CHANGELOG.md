# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-14

First release: the application, without geolocation yet.

### Added

- Desktop window (pywebview on Qt WebEngine): `wepwawet` with no arguments opens it, like Wireshark;
  `wepwawet gui --browser` opens the system browser instead.
- Interface with the "Sand & turquoise" terminal theme and the animated ASCII jackal for empty, busy,
  error and result states.
- MapLibre map with two base maps: CARTO dark (online, recoloured with the theme) and an offline map
  from bundled Natural Earth outlines.
- Local API (FastAPI) bound to loopback, protected by a per-session token, a `Host` check against DNS
  rebinding and a strict Content Security Policy; live log over server-sent events.
- Command line: `gui`, `serve`, `doctor`, `version`; `wpw` alias.
- Configuration from `~/.config/wepwawet/config.toml` and `WEPWAWET_*` environment variables, XDG
  directories, structured logging with secret redaction, SQLite TTL cache, data models.
- Linux packages for x86_64 and arm64: `.deb` (Debian, Ubuntu, Kali), `.rpm` (Fedora), Arch Linux
  package and AppImage, with a bundled Python 3.12 and Qt WebEngine.
- CI: lint, type check and tests; package build and install tests with a window smoke test on every
  supported distribution.
- Documentation site in `docs/`.

[Unreleased]: https://github.com/n36l3c7/Wepwawet/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/n36l3c7/Wepwawet/releases/tag/v0.1.0
