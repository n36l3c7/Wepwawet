# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-15

Passive evidence: Wepwawet now looks addresses up.

### Added

- Input validation: IPv4, IPv6 and hostnames (every A and AAAA record); private, documentation,
  CGNAT and other special-purpose ranges are refused with the reason.
- Sources: local GeoIP databases (DB-IP Lite, IPinfo Lite, GeoLite2) with a disagreement check,
  RIPE IPmap, RIPEstat (prefix, origin AS, RIR country, GeoLite2), RDAP with the IANA bootstrap,
  reverse DNS with a paced PTR sweep of the /24, PeeringDB facilities and exchanges, Shodan
  InternetDB, Shodan and Censys (Platform API).
- Network classification (residential, mobile, hosting, CDN, VPN, education) from published cloud
  ranges, known ASNs, PeeringDB types, AS names, PTR patterns and InternetDB tags, with caveats.
- `wepwawet locate`, `wepwawet providers`, `wepwawet data update` and `wepwawet data status`.
- API: `/api/providers` and `/api/runs` (start, read, cancel); live `run`, `target` and `log` events.
- Interface: target input with Run and Stop, data source status, Summary, Evidence and Raw tabs,
  GeoIP answers with accuracy circles, located answers and PeeringDB facilities on the map.
- API keys from `WEPWAWET_*` variables or a `secrets.toml` readable by its owner only.
- Shared HTTP client with a TTL cache, per-source pacing and retries with back-off.

### Changed

- HTTP library loggers are limited to warnings, so request URLs never reach the log.

## [0.1.1] - 2026-11-14

### Fixed

- arm64 packages: declare the system libraries that Qt WebEngine links on aarch64 (lcms2, minizip,
  opus, snappy, webp); without them the window could not start on Debian, Ubuntu, Kali and Fedora.

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

[Unreleased]: https://github.com/n36l3c7/Wepwawet/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/n36l3c7/Wepwawet/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/n36l3c7/Wepwawet/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/n36l3c7/Wepwawet/releases/tag/v0.1.0
