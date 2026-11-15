"""Landmark refinement (experimental): which hosts of known position does the target look like?

RIPE Atlas probes publish their position and public address, so the ones inside the region are
landmarks. The probes that measured the target ping each landmark too; a landmark next to the
target, on the same network, should show about the same RTT from every probe as the target
does (the delay-space idea of GeoPing, Padmanabhan and Subramanian, 2001, used here only
inside the region the RTT disks already allow). The landmarks with the closest RTT pattern
give a soft answer, which the fusion weighs like any other and drops when it falls outside the
region.

It is experimental: Atlas positions are set by their hosts (and rounded), many home probes do not
answer pings, and two places in one city can have very different paths. It is off by default.
"""

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from wepwawet.core.geo import haversine, spherical_mean
from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint

ATLAS_PROBES_URL = "https://atlas.ripe.net/api/v2/probes/"
# Landmarks are searched within the region's radius, between these bounds (the API wants km).
MIN_SEARCH_KM = 10
MAX_SEARCH_KM = 100
# A landmark compared over fewer probes than this tells nothing.
MIN_SHARED_PROBES = 3
# The answer averages this many best landmarks, weighted by how close their pattern is.
BEST_LANDMARKS = 3
# Floor of the answer's accuracy: Atlas positions are rounded and set by hand.
MIN_RADIUS_KM = 5.0
# RTT-pattern difference added to every score so a perfect match does not get infinite weight.
SCORE_FLOOR_MS = 0.2


@dataclass(frozen=True)
class Landmark:
    probe_id: int
    address: str
    lat: float
    lon: float
    asn: int | None = None
    anchor: bool = False


@dataclass(frozen=True)
class Match:
    landmark: Landmark
    score_ms: float  # median |RTT to the target - RTT to the landmark| over the shared probes
    shared: int


def search_radius_km(region_radius_km: float) -> int:
    return int(min(MAX_SEARCH_KM, max(MIN_SEARCH_KM, math.ceil(region_radius_km))))


def parse_landmarks(data: Mapping[str, object] | None, version: int, exclude: str) -> list[Landmark]:
    """Connected public Atlas probes with an address of the target's IP version, except ``exclude``."""
    found: list[Landmark] = []
    results = (data or {}).get("results")
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        address = item.get("address_v4" if version == 4 else "address_v6")
        geometry = item.get("geometry") or {}
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if not address or address == exclude or not coordinates or not isinstance(item.get("id"), int):
            continue
        lon, lat = coordinates
        asn = item.get("asn_v4" if version == 4 else "asn_v6")
        found.append(
            Landmark(
                probe_id=item["id"],
                address=str(address),
                lat=float(lat),
                lon=float(lon),
                asn=asn if isinstance(asn, int) else None,
                anchor=bool(item.get("is_anchor")),
            )
        )
    return found


def choose(landmarks: Sequence[Landmark], centre: GeoPoint, count: int, asn: int | None) -> list[Landmark]:
    """Up to ``count`` landmarks: half on the target's own network, the rest spread over the region.

    A landmark of the same operator shares the target's paths, so its RTT pattern is the most
    comparable; the others are picked farthest-first so the region is covered, not one street.
    """
    if count <= 0 or not landmarks:
        return []
    by_distance = sorted(landmarks, key=lambda lm: haversine(centre.lat, centre.lon, lm.lat, lm.lon))
    chosen = [lm for lm in by_distance if asn is not None and lm.asn == asn][: max(1, count // 2)]
    rest = [lm for lm in by_distance if lm not in chosen]
    if not chosen and rest:
        chosen.append(rest.pop(0))
    while rest and len(chosen) < count:
        farthest = max(rest, key=lambda lm: min(haversine(lm.lat, lm.lon, c.lat, c.lon) for c in chosen))
        rest.remove(farthest)
        chosen.append(farthest)
    return chosen


def spread_by_distance(landmarks: Sequence[Landmark], centre: GeoPoint, count: int) -> list[Landmark]:
    """``count`` landmarks at evenly spaced distance ranks from ``centre``, the nearest and farthest included.

    A delay-distance line needs short and long distances: the nearest points fix its intercept
    (the delay that is not distance), the farthest its slope.
    """
    ordered = sorted(landmarks, key=lambda lm: haversine(centre.lat, centre.lon, lm.lat, lm.lon))
    if count <= 0 or not ordered:
        return []
    if count == 1 or len(ordered) <= count:
        return ordered[:count]
    picks = sorted({round(i * (len(ordered) - 1) / (count - 1)) for i in range(count)})
    return [ordered[i] for i in picks]


def match(target: Mapping[str, float], landmarks: Mapping[Landmark, Mapping[str, float]]) -> list[Match]:
    """Landmarks ranked by how closely their RTTs, probe by probe, follow the target's."""
    matches = []
    for landmark, rtts in landmarks.items():
        shared = [key for key in target if key in rtts]
        if len(shared) < MIN_SHARED_PROBES:
            continue
        score = statistics.median(abs(target[key] - rtts[key]) for key in shared)
        matches.append(Match(landmark=landmark, score_ms=round(score, 3), shared=len(shared)))
    return sorted(matches, key=lambda m: (m.score_ms, -m.shared))


def landmark_evidence(matches: Sequence[Match], measured: int) -> Evidence | None:
    """A soft answer from the best-matching landmarks, or None when none could be compared."""
    best = list(matches[:BEST_LANDMARKS])
    if not best:
        return None
    weights = [1 / (m.score_ms + SCORE_FLOOR_MS) for m in best]
    points = [GeoPoint(lat=m.landmark.lat, lon=m.landmark.lon) for m in best]
    centre = spherical_mean(points, weights)
    spread = max(haversine(centre.lat, centre.lon, p.lat, p.lon) for p in points)
    top = best[0]
    return Evidence(
        source="landmark",
        kind=EvidenceKind.SOFT,
        label="Landmarks",
        summary=f"Closest RTT pattern: RIPE Atlas probe {top.landmark.probe_id} "
        f"({top.score_ms:.2f} ms apart over {top.shared} probes); {len(matches)} of {measured} landmarks compared",
        point=centre,
        radius_km=round(max(MIN_RADIUS_KM, spread), 1),
        weight=0.5,
        details={
            "experimental": True,
            "landmarks": [
                {
                    "probe_id": m.landmark.probe_id,
                    "address": m.landmark.address,
                    "lat": m.landmark.lat,
                    "lon": m.landmark.lon,
                    "asn": m.landmark.asn,
                    "score_ms": m.score_ms,
                    "shared_probes": m.shared,
                }
                for m in matches
            ],
        },
    )
