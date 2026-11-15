"""Multilateration: intersect the RTT disks of the probes on an H3 grid, and detect anycast.

Every probe with a minimum RTT says "the target is within ``d_max`` km of me". The feasible
region is the set of places inside every disk. It is computed on Uber's H3 hexagonal grid,
coarse to fine: start from all resolution-1 cells, keep the cells that can touch every disk,
replace them with their children, and repeat until the region is described finely enough or
would need too many cells. Cell area gives the region's area in km².

If two disks do not overlap at all, no single place is consistent with both probes: the
address is served from several places (anycast) or a measurement is wrong.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import h3

from wepwawet.core.geo import haversine, spherical_mean
from wepwawet.core.models import AnycastInfo, AnycastSite, GeoPoint, ProbeResult, Region

START_RESOLUTION = 1
MAX_RESOLUTION = 8
MAX_CELLS = 15_000
# A disk smaller than this (an RTT under 10 ms) is trusted even when it conflicts with others.
TRUSTED_RADIUS_KM = 1000.0
# A cell is kept if its centre lies within d_max plus this share of the cell's size.
CELL_MARGIN = 1.05


@dataclass(frozen=True)
class Disk:
    probe: str
    lat: float
    lon: float
    radius_km: float


def disks_from(probes: Sequence[ProbeResult]) -> list[Disk]:
    return [
        Disk(
            probe=f"{p.probe.provider}:{p.probe.probe_id}",
            lat=p.probe.location.lat,
            lon=p.probe.location.lon,
            radius_km=p.max_distance_km,
        )
        for p in probes
        if p.max_distance_km is not None
    ]


def _cell_reach_km(resolution: int) -> float:
    """Distance from a cell's centre to its farthest corner (an upper bound, with margin)."""
    return float(h3.average_hexagon_edge_length(resolution, unit="km")) * CELL_MARGIN


def _inside_all(cell: str, disks: Sequence[Disk], slack_km: float) -> bool:
    lat, lon = h3.cell_to_latlng(cell)
    return all(haversine(lat, lon, d.lat, d.lon) <= d.radius_km + slack_km for d in disks)


def feasible_cells(disks: Sequence[Disk]) -> tuple[set[str], int]:
    """Cells consistent with every disk, at the finest resolution reached, and that resolution."""
    if not disks:
        return set(), START_RESOLUTION
    ordered = sorted(disks, key=lambda d: d.radius_km)  # the tightest disks reject most cells first
    resolution = START_RESOLUTION
    cells = {c for base in h3.get_res0_cells() for c in h3.cell_to_children(base, resolution)}
    cells = {c for c in cells if _inside_all(c, ordered, _cell_reach_km(resolution))}
    while cells and resolution < MAX_RESOLUTION:
        children = {child for c in cells for child in h3.cell_to_children(c, resolution + 1)}
        refined = {c for c in children if _inside_all(c, ordered, _cell_reach_km(resolution + 1))}
        if not refined or len(refined) > MAX_CELLS:
            break
        cells, resolution = refined, resolution + 1
    # Final pass at the reached resolution: centre strictly inside every disk when possible.
    strict = {c for c in cells if _inside_all(c, ordered, 0.0)}
    return (strict or cells), resolution


def region_from(disks: Sequence[Disk]) -> Region | None:
    result = region_and_cells(disks)
    return result[0] if result else None


def region_and_cells(disks: Sequence[Disk]) -> tuple[Region, set[str]] | None:
    cells, resolution = feasible_cells(disks)
    if not cells:
        return None
    centres = [GeoPoint(lat=lat, lon=lon) for lat, lon in (h3.cell_to_latlng(c) for c in cells)]
    centroid = spherical_mean(centres)
    distances = sorted(haversine(centroid.lat, centroid.lon, p.lat, p.lon) for p in centres)
    radius = distances[min(len(distances) - 1, int(0.9 * len(distances)))] + _cell_reach_km(resolution)
    area = sum(h3.cell_area(c, unit="km^2") for c in cells)
    shape = h3.cells_to_h3shape(list(cells))
    geometry = shape.__geo_interface__
    if geometry["type"] == "Polygon":
        geometry = {"type": "MultiPolygon", "coordinates": [geometry["coordinates"]]}
    binding = tuple(d.probe for d in sorted(disks, key=lambda d: d.radius_km)[:3])
    region = Region(
        resolution=resolution,
        cells=len(cells),
        area_km2=round(area, 1),
        centroid=centroid,
        radius_km=round(radius, 1),
        geometry=_json_safe(geometry),
        binding_probes=binding,
    )
    return region, cells


def _json_safe(geometry: dict[str, object]) -> dict[str, object]:
    """Tuples to lists, so the geometry round-trips through JSON unchanged."""

    def convert(value: object) -> object:
        if isinstance(value, tuple | list):
            return [convert(v) for v in value]
        return value

    return {k: convert(v) for k, v in geometry.items()}


def _conflicts(disks: Sequence[Disk], min_separation_km: float) -> dict[str, int]:
    counts = {d.probe: 0 for d in disks}
    for i, a in enumerate(disks):
        for b in disks[i + 1 :]:
            distance = haversine(a.lat, a.lon, b.lat, b.lon)
            if distance > a.radius_km + b.radius_km and distance > min_separation_km:
                counts[a.probe] += 1
                counts[b.probe] += 1
    return counts


def split_outliers(
    disks: Sequence[Disk], min_separation_km: float = 500.0, share: float = 0.1
) -> tuple[list[Disk], list[Disk]]:
    """Separate probes whose declared position is wrong from the rest.

    A probe placed in the wrong country conflicts with most other probes. Removing the worst
    probes, at most ``share`` of them, and finding that every conflict disappears means they were
    misplaced, not that the address is anycast. A probe whose bound is under ``TRUSTED_RADIUS_KM``
    (an RTT under 10 ms) is never discarded: so fast an answer is strong local evidence, and it
    is exactly what a real anycast site produces.
    """
    kept = list(disks)
    removed: list[Disk] = []
    budget = max(1, int(len(disks) * share))
    while len(removed) < budget:
        counts = _conflicts(kept, min_separation_km)
        candidates = [d for d in kept if counts[d.probe] > 0 and d.radius_km >= TRUSTED_RADIUS_KM]
        if not any(counts.values()) or not candidates:
            break
        worst = max(candidates, key=lambda d: counts[d.probe])
        kept.remove(worst)
        removed.append(worst)
    if removed and not any(_conflicts(kept, min_separation_km).values()):
        return kept, removed
    return list(disks), []


def detect_anycast(disks: Sequence[Disk], min_separation_km: float = 500.0) -> AnycastInfo:
    """Pairs of disks that cannot both contain one point, far enough apart to be distinct sites.

    The sites reported are the tightest disks that are mutually disjoint: each marks one
    place the address is served from.
    """
    violations = 0
    for i, a in enumerate(disks):
        for b in disks[i + 1 :]:
            gap = haversine(a.lat, a.lon, b.lat, b.lon) - (a.radius_km + b.radius_km)
            if gap > 0 and haversine(a.lat, a.lon, b.lat, b.lon) > min_separation_km:
                violations += 1
    if not violations:
        return AnycastInfo(detected=False, violations=0, summary="RTTs are consistent with a single location")
    sites: list[Disk] = []
    for disk in sorted(disks, key=lambda d: d.radius_km):
        if all(haversine(disk.lat, disk.lon, s.lat, s.lon) > disk.radius_km + s.radius_km for s in sites):
            sites.append(disk)
    return AnycastInfo(
        detected=True,
        violations=violations,
        summary=f"Anycast: {violations} pair{'s' if violations != 1 else ''} of probes are too fast for one "
        f"location; at least {len(sites)} sites",
        sites=tuple(
            AnycastSite(point=GeoPoint(lat=s.lat, lon=s.lon), max_distance_km=round(s.radius_km, 1), probe=s.probe)
            for s in sites[:20]
        ),
    )


def consistent_subset(disks: Sequence[Disk]) -> list[Disk]:
    """For an anycast target: the disks around the tightest one that agree with it, i.e. one site."""
    if not disks:
        return []
    anchor = min(disks, key=lambda d: d.radius_km)
    return [d for d in disks if haversine(anchor.lat, anchor.lon, d.lat, d.lon) <= anchor.radius_km + d.radius_km]


def area_confidence(area_km2: float) -> int:
    """A rough 0–100 confidence from the region size alone (fusion refines it from v0.5.0)."""
    if area_km2 <= 0:
        return 0
    # 100 km² -> ~85, 10 000 km² -> ~55, 1 000 000 km² -> ~25
    return max(5, min(90, round(115 - 15 * math.log10(area_km2))))
