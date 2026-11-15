# Wepwawet

[![CI](https://github.com/n36l3c7/Wepwawet/actions/workflows/ci.yml/badge.svg)](https://github.com/n36l3c7/Wepwawet/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/n36l3c7/Wepwawet)](https://github.com/n36l3c7/Wepwawet/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Measurement-based IP geolocation for Linux: passive evidence plus ping and traceroute from many vantage points, on a map.

**Documentation: <https://n36l3c7.github.io/Wepwawet/>**

![Wepwawet locating 77.109.180.62, a RIPE Atlas anchor in Winterthur: the RTT region over Zurich, the probes, the traceroutes and the estimate with its reasons](docs/img/main-window.png)

> **v1.0.1.** Passive sources, about 80 probes, a feasible region, traceroutes read router by router,
> and one fused estimate with its uncertainty, confidence and reasons; history with diff, export, bulk mode
> and an accuracy benchmark. How it works, and where it stops: [Methodology](https://n36l3c7.github.io/Wepwawet/methodology.html).

## What it does

Wepwawet estimates where an IP address is by combining two kinds of evidence:

- **Passive evidence**: GeoIP databases (and how much they disagree), RIR registration data (RDAP),
  routing data (RIPEstat), reverse DNS of the target and of its neighbours, PeeringDB facilities.
- **Active measurements**: ping and traceroute from many probes around the world (Globalping, RIPE Atlas,
  this machine, your own servers). Round-trip times give hard physical limits on where the target can be.

Every estimate comes with an uncertainty radius, a confidence score and the evidence behind it, shown on
the map. It is for network engineers, SOC analysts and researchers who need to know where an address
really is on the network (city, PoP, facility), not who uses it.

## Ethical use

- Wepwawet uses only public data sources and measurement platforms, within their terms of service.
- It never scans ports or interacts with the target beyond what the measurement platforms do (ping,
  traceroute) and DNS lookups.
- Its goal is the network location of an address. The realistic limit for a home connection is the
  provider's point of presence; the subscriber's address is only in the provider's records, which this
  tool does not try to obtain. Do not use it to locate or follow people.

## Requirements

- Linux on **x86_64** or **arm64**. Tested distributions: Debian 13, Ubuntu 22.04 and 24.04, Kali Rolling,
  Fedora 43, Arch Linux.
- On **arm64**, glibc 2.39 or newer (Ubuntu 24.04+, Debian 13+), required by Qt.
- About 190 MB to download and 515 MB of disk space. The packages include their own Python 3.12 and Qt
  WebEngine, so they do not depend on the system Python.
- A graphical session (X11 or Wayland) for the window. Without one, use `wepwawet serve` and a browser.

## Installation

Download the package for your distribution from the
[releases page](https://github.com/n36l3c7/Wepwawet/releases), then:

```sh
# Debian, Ubuntu, Kali
sudo apt install ./wepwawet_1.0.1-1_amd64.deb

# Fedora
sudo dnf install ./wepwawet-1.0.1-1.x86_64.rpm

# Arch Linux
sudo pacman -U wepwawet-1.0.1-1-x86_64.pkg.tar.zst

# Any distribution: AppImage (needs libfuse2, or run it with --appimage-extract-and-run)
chmod +x Wepwawet-1.0.1-x86_64.AppImage
./Wepwawet-1.0.1-x86_64.AppImage
```

The packages install the system libraries Qt needs and recommend `mtr` and `traceroute` for measurements
from this machine. The AppImage expects the same libraries on the host; the list is in the
[installation guide](https://n36l3c7.github.io/Wepwawet/installation.html).

## Quickstart

```sh
wepwawet data update        # download DB-IP Lite, GeoNames cities, airport and UN/LOCODE codes (monthly)
wepwawet                    # open the window (also in the desktop menu)
wepwawet locate ripe.net    # or locate from the terminal
wepwawet doctor             # check the window, the configuration and the traceroute tools
```

`wpw` is a short alias of `wepwawet`.

## Sources

| Source | Key | What it adds |
|---|---|---|
| GeoIP databases | no (download once) | DB-IP Lite, IPinfo Lite, GeoLite2 answers and how far apart they are |
| RIPE IPmap | no | RIPE NCC's geolocation from crowdsourced and latency data |
| RIPEstat | no | Routed prefix, origin AS, RIR country, GeoLite2 answer |
| RDAP | no | Registration: block, holder, country, customer assignments |
| Reverse DNS | no | PTR of the target and naming patterns of its /24 neighbours (DNS only) |
| PeeringDB | optional | Facilities and exchanges of the origin network |
| Shodan InternetDB | no | Open ports, names, tags such as `vpn` or `cloud` (non-commercial use) |
| Shodan | yes (paid) | Shodan's host record and location |
| Censys | yes (free plan: 100 lookups a month) | Location, DNS names, certificate subjects |

## Measurements

| Source | Key | What it adds |
|---|---|---|
| Globalping | optional (free token doubles the limit) | Pings from thousands of community probes: 250 tests an hour anonymously |
| RIPE Atlas | yes, plus `providers.ripe_atlas.max_credits` | One-off pings (6 credits per probe); off until you set a budget |
| This machine / SSH probes | no (their location in the configuration) | Pings from your computer and your own servers |

A campaign sweeps about 48 probes on every continent, then zooms with about 30 probes in the country of the
fastest one and of the passive answers. Each minimum RTT bounds the distance (light in fibre covers about
100 km per millisecond of round trip); the places inside every bound form the feasible region, computed on
an H3 grid, with its centre, an uncertainty radius and the GeoNames cities inside it. Probes too fast for a
single place reveal anycast. When the target drops ICMP, the sweep is repeated as a TCP handshake to port 443.

Then traceroutes from the five fastest probes read the routers on the way. Router names often contain a
place (an airport or UN/LOCODE code, a city, an operator's own code); a name is believed only if the
router's RTT allows it. Routers inside an exchange's peering LAN (PeeringDB) are placed at that exchange.
The router where most paths meet is the convergence point, and the RTT step from the last router to the
target estimates the access line (fibre, xDSL, mobile), most of which is removed from every RTT before
the region is recomputed. Operator naming rules live in
[`geohint_rules.toml`](src/wepwawet/data/geohint_rules.toml) and are easy to extend.

## The estimate

Every finding becomes weighted evidence. The RTT region is a hard limit; the answers that agree inside it
(GeoIP databases, RIPE IPmap, the convergence router, the last located router) rank the places in it, and
answers outside it are ruled out and listed. The result has an uncertainty radius, a confidence from 0 to
100 (from the size of the region, independent sources agreeing, and the network class) and a plain-language
explanation. Without measurements the confidence never exceeds 38.

Every run is kept in a history: compare two runs to see whether the estimate moved, a GeoIP database
changed its answer or a path changed. Runs export to JSON, GeoJSON, KML, Markdown, a CSV of hops, or a PNG
of the map. Lists of targets run in bulk under one test budget.

Answers are cached (one day by default). The network is classified as residential, mobile, hosting, CDN,
VPN or education from cloud ranges, PeeringDB, the AS name and PTR patterns, and the window explains
what that means for the answer.

## Command line

| Command | What it does |
|---|---|
| `wepwawet` | Open the window. Same as `wepwawet gui`. |
| `wepwawet gui --browser` | Open the interface in the system browser instead of a window, for machines where Qt WebEngine cannot run. |
| `wepwawet locate TARGET [-f FORMAT] [-o FILE] [--passive]` | Locate an IP address or hostname; print the evidence, or export it (`json`, `geojson`, `kml`, `md`, `csv`). |
| `wepwawet history list\|show\|diff\|export` | Past runs: list them, show one, compare two, export one. |
| `wepwawet bulk FILE [--max-tests N] [-o results.csv]` | Locate a list of targets under one test budget; print a summary CSV. |
| `wepwawet benchmark [--anchors N] [--max-tests N]` | Accuracy on RIPE Atlas anchors: error per stage. |
| `wepwawet providers` | List the sources and whether each can run now. |
| `wepwawet data update [--only dbip\|ipinfo\|geolite\|geonames\|places]` | Download the GeoIP databases, the GeoNames cities and the place codes. `data status` lists the GeoIP databases. |
| `wepwawet serve [--host H] [--port P]` | Start only the local server and print the address to open. |
| `wepwawet doctor` | Check Python, configuration, directories, the window and the local traceroute tools. |
| `wepwawet version` / `--version` | Show the version. |

## Configuration

Wepwawet works without configuration. To change a setting, create `~/.config/wepwawet/config.toml`:

```toml
[server]
host = "127.0.0.1"   # loopback only, unless allow_remote = true
port = 0             # 0 picks a free port at start-up
allow_remote = false

[map]
basemap = "carto"    # "carto" (online, CARTO dark) or "offline" (bundled Natural Earth outlines)

[gui]
rendering = "auto"   # "software" draws the map on the CPU; "auto" does so in VMs and without a GPU

[cache]
default_ttl_seconds = 86400
ttl_seconds = { rdap = 604800 }   # per-source overrides

[logging]
level = "INFO"       # DEBUG, INFO, WARNING, ERROR

[measurements]
sweep_probes = 48    # global sweep, then
zoom_probes = 30     # regional zoom
max_tests = 150      # per target
calibrate = false    # CBG calibration against RIPE Atlas anchors
traceroute_probes = 5
access_correction = true  # remove most of the access delay before multilateration

[providers.rdns]
sweep = true         # PTR lookups of the /24 neighbours

[providers.local]    # ping from this machine too
lat = 45.46
lon = 9.19

[[providers.ssh.hosts]]   # and from your servers (key authentication)
name = "vps-fra"
host = "vps.example.net"
lat = 50.11
lon = 8.68

[providers.ripe_atlas]
max_credits = 300    # RIPE Atlas spends credits only with a budget

[providers.shodan]
enabled = false      # any source can be turned off
```

API keys go in environment variables (`WEPWAWET_GLOBALPING`, `WEPWAWET_RIPE_ATLAS`, `WEPWAWET_IPINFO`,
`WEPWAWET_MAXMIND_ACCOUNT_ID`, `WEPWAWET_MAXMIND_LICENSE_KEY`, `WEPWAWET_PEERINGDB`, `WEPWAWET_SHODAN`,
`WEPWAWET_CENSYS`) or in
`~/.config/wepwawet/secrets.toml`, which must be readable by you only (`chmod 600`):

```toml
[keys]
ipinfo = "..."
censys = "..."
```

| Environment variable | Overrides |
|---|---|
| `WEPWAWET_CONFIG` | Path of the configuration file |
| `WEPWAWET_HOST`, `WEPWAWET_PORT` | `server.host`, `server.port` |
| `WEPWAWET_BASEMAP` | `map.basemap` |
| `WEPWAWET_RENDERING` | `gui.rendering` |
| `WEPWAWET_LOG_LEVEL` | `logging.level` |
| `WEPWAWET_TOKEN` | Fixed session token for `wepwawet serve` (24+ characters) |

Files: configuration in `~/.config/wepwawet/`, history and logs in `~/.local/share/wepwawet/`, the cache in
`~/.cache/wepwawet/` (the `XDG_*` variables are honoured). Secrets are never written to the log.

The online base map uses [CARTO](https://carto.com/basemaps) tiles built from OpenStreetMap data, under
CARTO's terms. Switch to the offline map (button on the map, or `basemap = "offline"`) to make no tile
requests at all.

## Accuracy

`wepwawet benchmark` locates RIPE Atlas anchors, whose positions are public, and reports the error of each
stage. Passive sources only, 20 anchors (seed 1), November 2026:

| Sources | Median error | 90th percentile | Truth inside the stated uncertainty |
|---|---|---|---|
| All passive sources | 3.6 km | 13 km | 95 % |
| Without RIPE IPmap | 5.8 km | 180 km | 75 % |

Read these as a best case. Anchors sit in data centres where GeoIP is good, and RIPE IPmap is built partly
on RIPE Atlas data, so it knows the anchors well; the second row is the fairer passive figure. Residential
and mobile addresses are harder (see below).

With measurements (two anchors, 84 Globalping tests each): the anchor in Singapore was located 0.6 km from
its published position (fastest probe 0.5 ms away), the one in Winterthur 14 km away; the true position
was inside the stated uncertainty at every stage. Two anchors are an example, not a statistic: run the
benchmark yourself, with a Globalping token for larger samples:

```sh
wepwawet benchmark --anchors 10 --max-tests 100 -o benchmark.csv
```

## Known limits

- **Residential**: the realistic ceiling is the city of the provider's PoP or BNG.
- **Mobile and CGNAT**: the gateway can be hundreds of kilometres from the user.
- **VPN, Tor, proxies, hosting**: the result is the exit server.
- **Anycast**: there is no single location; Wepwawet flags it instead of guessing.
- MPLS tunnels, ICMP filtering and asymmetric routing reduce precision.

## Roadmap

| Version | Content |
|---|---|
| 0.1.0 | Application, map, local server, CLI, Linux packages (done) |
| 0.2.0 | Passive enrichment: GeoIP, RIPE IPmap, RIPEstat, RDAP, reverse DNS, PeeringDB, Shodan, Censys (done) |
| 0.3.0 | Measurement providers, two-stage ping campaign, multilateration, anycast detection (done) |
| 0.4.0 | Traceroutes, location hints from router names, IXPs, path convergence (done) |
| 0.5.0 | Evidence fusion and confidence, history and diff, export, bulk mode (done) |
| 1.0.0 | Accuracy benchmark, hardening, complete documentation (done) |
| 1.0.1 | Map overlays on CARTO, software rendering for VMs, exhausted-quota caveat (done) |
| 1.1.0 | Settings panel: API keys and options from the window |
| 1.2.0 | City and same-network zoom for residential addresses |
| later | Landmark-based refinement (street-level, experimental) |

## Development

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 22.

```sh
git clone https://github.com/n36l3c7/Wepwawet.git && cd Wepwawet
uv sync --extra gui                       # Python environment with the desktop window
(cd frontend && npm ci && npm run build)  # interface, built into src/wepwawet/web/dist
uv run wepwawet                           # open the window from the source tree
```

For interface work, run `uv run wepwawet serve --port 8650` and `npm run dev` in `frontend/`, then open the
`/auth?token=...` address printed by `serve` on the Vite port (5173).

Checks (CI runs the same):

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd frontend && npm run lint && npm run format:check && npm test
```

Linux packages: `npm run build`, `uv build --wheel`, then `bash packaging/linux/build.sh` on Linux, or
`bash packaging/linux/build-in-container.sh` anywhere Docker or Podman runs. `packaging/linux/test-install.sh`
installs a package in a clean container and opens the window under Xvfb.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [SECURITY.md](SECURITY.md) to report a
vulnerability.

## Licence

[MIT](LICENSE). The packages bundle third-party software under its own licences: CPython (PSF), Qt and
PySide6 (LGPLv3), MapLibre GL JS (BSD-3-Clause), Natural Earth data (public domain), Inter and JetBrains
Mono fonts (OFL-1.1). IP geolocation by [DB-IP](https://db-ip.com) (CC BY 4.0) when the DB-IP Lite
database is installed. City names from [GeoNames](https://www.geonames.org) (CC BY 4.0); airport codes from
[OurAirports](https://ourairports.com) (public domain); place codes from UN/LOCODE.
