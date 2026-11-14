import ipaddress

import pytest
from pydantic import ValidationError

from wepwawet.core.models import (
    Estimate,
    GeoPoint,
    Hop,
    Measurement,
    MeasurementKind,
    Probe,
    Target,
)

PROBE = Probe(provider="globalping", probe_id="p1", location=GeoPoint(lat=45.46, lon=9.19), city="Milan", country="IT")
TARGET = Target(address=ipaddress.ip_address("198.51.100.23"))


def test_min_rtt_uses_the_smallest_sample() -> None:
    m = Measurement(kind=MeasurementKind.PING, probe=PROBE, target=TARGET, rtts=(3.4, 1.9, 2.2))
    assert m.min_rtt == 1.9


def test_min_rtt_none_without_replies() -> None:
    assert Measurement(kind=MeasurementKind.PING, probe=PROBE, target=TARGET).min_rtt is None
    assert Hop(ttl=4).min_rtt is None


def test_target_version() -> None:
    assert TARGET.version == 4
    assert Target(address=ipaddress.ip_address("2001:db8::1")).version == 6


@pytest.mark.parametrize(("lat", "lon"), [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_coordinates_are_bounded(lat: float, lon: float) -> None:
    with pytest.raises(ValidationError):
        GeoPoint(lat=lat, lon=lon)


def test_negative_rtt_rejected() -> None:
    with pytest.raises(ValidationError):
        Measurement(kind=MeasurementKind.PING, probe=PROBE, target=TARGET, rtts=(-1.0,))


def test_confidence_range() -> None:
    with pytest.raises(ValidationError):
        Estimate(location=GeoPoint(lat=0, lon=0), uncertainty_km=10, confidence=101)


def test_models_round_trip_through_json() -> None:
    m = Measurement(
        kind=MeasurementKind.TRACEROUTE,
        probe=PROBE,
        target=TARGET,
        hops=(Hop(ttl=1, address=ipaddress.ip_address("192.0.2.1"), rtts=(0.4,)), Hop(ttl=2)),
    )
    again = Measurement.model_validate_json(m.model_dump_json())
    assert again.hops == m.hops
    assert again.probe == PROBE
