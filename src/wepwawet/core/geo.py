"""Geodesy and delay-to-distance conversion.

Distances use the haversine formula on a sphere of mean Earth radius: at the scale of RTT
constraints (tens to thousands of kilometres) its 0.5 % error is far below the measurement noise.

Delay to distance: a packet cannot travel faster than light in fibre, about 2/3 c, which is
roughly 200 km per millisecond one way, so a round-trip time of ``rtt`` ms bounds the distance
to ``rtt / 2 * 200`` km. CBG (Gueye et al., 2006) tightens the bound per probe with a "bestline"
fitted on landmarks of known position; without calibration the speed-of-light bound is used.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from wepwawet.core.models import GeoPoint

EARTH_RADIUS_KM = 6371.0088
DEFAULT_SPEED_KM_PER_MS = 200.0  # one way, about 2/3 of the speed of light in vacuum
MAX_SURFACE_DISTANCE_KM = math.pi * EARTH_RADIUS_KM


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    return haversine(a.lat, a.lon, b.lat, b.lon)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def destination(origin: GeoPoint, bearing_deg: float, distance_km: float) -> GeoPoint:
    """The point ``distance_km`` from ``origin`` along ``bearing_deg`` (great circle)."""
    d = distance_km / EARTH_RADIUS_KM
    p1, l1, b = math.radians(origin.lat), math.radians(origin.lon), math.radians(bearing_deg)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return GeoPoint(lat=math.degrees(p2), lon=(math.degrees(l2) + 540) % 360 - 180)


def spherical_mean(points: Sequence[GeoPoint]) -> GeoPoint:
    """Centroid on the sphere (average of unit vectors); correct across the antimeridian."""
    x = y = z = 0.0
    for p in points:
        lat, lon = math.radians(p.lat), math.radians(p.lon)
        x += math.cos(lat) * math.cos(lon)
        y += math.cos(lat) * math.sin(lon)
        z += math.sin(lat)
    n = len(points)
    x, y, z = x / n, y / n, z / n
    return GeoPoint(lat=math.degrees(math.atan2(z, math.hypot(x, y))), lon=math.degrees(math.atan2(y, x)))


def sol_max_distance_km(rtt_ms: float, speed_km_per_ms: float = DEFAULT_SPEED_KM_PER_MS) -> float:
    """Speed-of-light bound: the farthest the target can be for a round trip of ``rtt_ms``."""
    return min(MAX_SURFACE_DISTANCE_KM, max(0.0, rtt_ms) / 2 * speed_km_per_ms)


@dataclass(frozen=True)
class Bestline:
    """CBG bestline ``rtt = slope * distance + intercept``: the tightest line below no calibration point.

    It is used inverted: ``distance <= (rtt - intercept) / slope``.
    """

    slope: float  # ms per km
    intercept: float  # ms

    def max_distance_km(self, rtt_ms: float) -> float:
        return min(MAX_SURFACE_DISTANCE_KM, max(0.0, (rtt_ms - self.intercept) / self.slope))


def fit_bestline(points: Iterable[tuple[float, float]], speed_km_per_ms: float = DEFAULT_SPEED_KM_PER_MS) -> Bestline:
    """Fit the CBG bestline on ``(distance_km, rtt_ms)`` calibration points.

    Among lines with slope at least the physical minimum (``2 / speed``) and non-negative
    intercept that stay below every point, pick the one closest to the points (least total
    vertical gap). The optimum passes through a point of the lower convex hull, so trying the
    lines through pairs of points, and through single points at the minimum slope, is enough.
    """
    data = [(d, r) for d, r in points if d >= 0 and r >= 0]
    min_slope = 2.0 / speed_km_per_ms
    if not data:
        return Bestline(slope=min_slope, intercept=0.0)

    def feasible(m: float, b: float) -> bool:
        return m >= min_slope - 1e-12 and b >= -1e-9 and all(r >= m * d + b - 1e-9 for d, r in data)

    def gap(m: float, b: float) -> float:
        return sum(r - (m * d + b) for d, r in data)

    candidates: list[tuple[float, float]] = [(min_slope, max(0.0, min(r - min_slope * d for d, r in data)))]
    for i, (d1, r1) in enumerate(data):
        candidates.append((min_slope, r1 - min_slope * d1))
        for d2, r2 in data[i + 1 :]:
            if abs(d2 - d1) > 1e-9:
                m = (r2 - r1) / (d2 - d1)
                candidates.append((m, r1 - m * d1))
    best = min(((m, b) for m, b in candidates if feasible(m, b)), key=lambda mb: gap(*mb))
    return Bestline(slope=best[0], intercept=max(0.0, best[1]))
