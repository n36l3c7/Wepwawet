import json

import pytest

from wepwawet.core.geo import (
    Bestline,
    destination,
    fit_bestline,
    haversine,
    sol_max_distance_km,
    spherical_mean,
)
from wepwawet.core.models import GeoPoint
from wepwawet.core.multilateration import (
    Disk,
    area_confidence,
    consistent_subset,
    detect_anycast,
    region_and_cells,
    region_from,
)

MILAN = (45.4642, 9.19)
PROBES = {
    "zrh": (47.37, 8.54),
    "fra": (50.11, 8.68),
    "par": (48.86, 2.35),
    "rom": (41.90, 12.50),
    "vie": (48.21, 16.37),
}


def disks_for(target: tuple[float, float], inflation: float = 1.3, extra_km: float = 50) -> list[Disk]:
    return [
        Disk(name, lat, lon, haversine(target[0], target[1], lat, lon) * inflation + extra_km)
        for name, (lat, lon) in PROBES.items()
    ]


def test_haversine_known_distance() -> None:
    assert haversine(45.4642, 9.19, 41.9028, 12.4964) == pytest.approx(477, abs=5)
    assert haversine(10, 10, 10, 10) == 0


def test_destination_round_trip() -> None:
    start = GeoPoint(lat=45.0, lon=9.0)
    end = destination(start, 90, 100)
    assert haversine(start.lat, start.lon, end.lat, end.lon) == pytest.approx(100, rel=1e-6)
    assert end.lon > start.lon


def test_spherical_mean_across_antimeridian() -> None:
    mean = spherical_mean([GeoPoint(lat=0, lon=179), GeoPoint(lat=0, lon=-179)])
    assert abs(abs(mean.lon) - 180) < 1e-6


def test_speed_of_light_bound() -> None:
    assert sol_max_distance_km(10) == pytest.approx(1000)
    assert sol_max_distance_km(10, speed_km_per_ms=150) == pytest.approx(750)
    assert sol_max_distance_km(-1) == 0
    assert sol_max_distance_km(10_000) == pytest.approx(20_015, rel=1e-3)  # capped at half the Earth


def test_bestline_stays_below_points_and_tightens_the_bound() -> None:
    points = [(100, 3.0), (500, 8.0), (1000, 14.0), (300, 9.0)]
    line = fit_bestline(points)
    assert line.slope >= 2 / 200 - 1e-12 and line.intercept >= 0
    assert all(rtt >= line.slope * d + line.intercept - 1e-9 for d, rtt in points)
    # Tighter than light in fibre for the same RTT.
    assert line.max_distance_km(8.0) < sol_max_distance_km(8.0)


def test_bestline_without_points_is_the_physical_line() -> None:
    assert fit_bestline([]) == Bestline(slope=0.01, intercept=0.0)


def test_region_contains_the_target() -> None:
    result = region_and_cells(disks_for(MILAN))
    assert result is not None
    region, cells = result
    import h3

    assert h3.latlng_to_cell(MILAN[0], MILAN[1], region.resolution) in cells
    assert region.area_km2 > 0 and region.radius_km > 0
    assert haversine(region.centroid.lat, region.centroid.lon, *MILAN) < region.radius_km
    assert region.geometry["type"] == "MultiPolygon"
    assert json.loads(json.dumps(region.geometry)) == region.geometry


def test_tighter_disks_give_a_smaller_region() -> None:
    loose = region_from(disks_for(MILAN, inflation=1.6, extra_km=150))
    tight = region_from(disks_for(MILAN, inflation=1.1, extra_km=20))
    assert loose is not None and tight is not None
    assert tight.area_km2 < loose.area_km2


def test_no_disks_no_region() -> None:
    assert region_from([]) is None


def test_anycast_is_detected_and_sites_listed() -> None:
    disks = [*disks_for(MILAN), Disk("syd", -33.87, 151.21, 300)]
    info = detect_anycast(disks)
    assert info.detected and info.violations == 5
    assert {s.probe for s in info.sites} >= {"syd"}
    assert region_from(disks) is None  # no single place fits
    # The nearest site is the one around the tightest disk: here Sydney.
    assert {d.probe for d in consistent_subset(disks)} == {"syd"}


def test_consistent_disks_are_not_anycast() -> None:
    assert not detect_anycast(disks_for(MILAN)).detected


def test_area_confidence_decreases_with_area() -> None:
    values = [area_confidence(a) for a in (100, 10_000, 1_000_000)]
    assert values == sorted(values, reverse=True)
    assert area_confidence(0) == 0


def test_a_misplaced_probe_is_not_anycast() -> None:
    from wepwawet.core.multilateration import split_outliers

    # A probe declared in Pretoria whose 42 ms RTT only fits a European position.
    disks = [*disks_for(MILAN), Disk("pretoria", -25.86, 28.19, 4230)]
    kept, outliers = split_outliers(disks)
    assert [d.probe for d in outliers] == ["pretoria"]
    assert not detect_anycast(kept).detected


def test_fast_far_probes_stay_anycast() -> None:
    from wepwawet.core.multilateration import split_outliers

    # Sydney answers in 3 ms: too fast to be a misplaced probe, so this is a second site.
    disks = [*disks_for(MILAN), Disk("syd", -33.87, 151.21, 300)]
    kept, outliers = split_outliers(disks)
    assert outliers == [] and detect_anycast(kept).detected
