# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-11-15

One answer, with its reasons, kept and shareable.

### Added

- Evidence fusion: the estimate is the best place inside the RTT region, ranked by the soft evidence
  that agrees (GeoIP, RIPE IPmap, convergence and last located router, facilities); answers outside
  the region are ruled out and listed as negative evidence. Confidence from the region size, the
  number of independent agreeing sources and the network class (anycast, mobile, hosting, VPN lower
  it); a plain-language explanation. Passive-only estimates stay at or below 38.
- History: every finished run (window, terminal, bulk) is kept in `history.sqlite3`; diff of two
  runs (estimate movement, confidence, network, GeoIP answers, convergence, first changed hop).
- Export: JSON, GeoJSON, KML, Markdown report, CSV of traceroute hops, PNG of the map.
- Bulk mode: a list of targets under one test budget, in the window (file mode, queue, pins coloured
  by confidence) and with `wepwawet bulk`; summary CSV and GeoJSON.
- `wepwawet locate --format/--output`, `wepwawet history list|show|diff|export`; API routes for
  history, diff, export and bulk.
- The desktop window allows downloads, for the exports.

## [0.4.0] - 2026-11-15

Paths: Wepwawet now traces the routes to the address and reads the routers on the way.

### Added

- Traceroutes from the fastest probes: Globalping `mtr` (per-hop RTT and ASN), local `mtr --json`
  or `traceroute`; TCP to the fallback port when the target only answered TCP.
- Location hints in router hostnames: operator rules in `geohint_rules.toml` (Arelion, NTT,
  Cogent, Hurricane Electric, Lumen, GTT, Telecom Italia Sparkle, Deutsche Telekom, Google), then
  UN/LOCODE, city names, airport and IATA metro codes. A hint is kept only when the hop's RTT allows
  the router to be there.
- Exchange points: hop addresses inside PeeringDB peering LANs are placed at the exchange (the
  prefix list is downloaded daily and matched locally).
- Path convergence (the router nearest the target shared by most paths), access delay with the
  likely access technology, and multilateration corrected for 80 % of the access delay.
- Path flags: MPLS or hidden hops, RTT falling along the path, unresponsive tail.
- Interface: traceroute paths and located routers on the map, convergence point, Hops tab with
  per-hop names, ASNs, exchanges and places, path summary in the Summary tab.
- Airports (OurAirports) and UN/LOCODE in `wepwawet data update` (`--only places`).

## [0.3.0] - 2026-11-15

Measurements: Wepwawet now pings the address and works out where it can be.

### Added

- Measurement sources: Globalping (free; a token doubles the hourly limit), RIPE Atlas (one-off pings,
  only with `providers.ripe_atlas.max_credits`, cost logged before spending), this machine and your
  own servers over SSH.
- Two-stage ping campaign: a global sweep over every continent, then a regional zoom in the country
  of the fastest probe and of the passive answers. Phases shrink or are skipped to stay within
  `measurements.max_tests` and what Globalping has left this hour.
- TCP fallback: when no probe gets an ICMP reply, the sweep measures the TCP handshake to port 443.
- Multilateration on an H3 grid: feasible region, centre, uncertainty radius and area; the GeoNames
  cities inside it; a provisional estimate with a confidence from the region size.
- Optional CBG calibration: the zoom probes ping RIPE Atlas anchors and get a delay-to-distance
  line each (`measurements.calibrate`).
- Anycast detection from speed-of-light violations, with a caveat and the nearest site.
- Interface: probes coloured by minimum RTT, RTT disks, feasible region, estimate with uncertainty
  and confidence, cities in the region, layer toggles, Probes tab and a budget meter.
- `wepwawet locate --passive`, `GET /api/budget`, GeoNames in `wepwawet data update`.

### Fixed

- `wepwawet locate --json` always writes UTF-8, whatever the terminal encoding.

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

[Unreleased]: https://github.com/n36l3c7/Wepwawet/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/n36l3c7/Wepwawet/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/n36l3c7/Wepwawet/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/n36l3c7/Wepwawet/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/n36l3c7/Wepwawet/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/n36l3c7/Wepwawet/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/n36l3c7/Wepwawet/releases/tag/v0.1.0
