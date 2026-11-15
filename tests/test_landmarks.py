"""1.3.0: experimental landmark refinement with RIPE Atlas probes of known position."""

from collections.abc import Sequence
from typing import ClassVar

import respx

from tests.test_measurement import COUNTRY, TRUTH, SimulatedNetwork, run_campaign
from wepwawet.core.geo import haversine
from wepwawet.core.landmarks import (
    ATLAS_PROBES_URL,
    Landmark,
    choose,
    landmark_evidence,
    match,
    parse_landmarks,
    search_radius_km,
)
from wepwawet.core.models import EvidenceKind, GeoPoint, Probe, Target
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import LocationSpec, PingBatch, ping_result

NEAR = Landmark(1, "198.51.100.1", 45.47, 9.20, asn=64500)  # next to the target, in Milan
TURIN = Landmark(2, "198.51.100.2", 45.07, 7.68)
BOLOGNA = Landmark(3, "198.51.100.3", 44.49, 11.34)
LANDMARKS = (NEAR, TURIN, BOLOGNA)


def atlas_item(landmark: Landmark, **extra: object) -> dict[str, object]:
    return {
        "id": landmark.probe_id,
        "address_v4": landmark.address,
        "asn_v4": landmark.asn,
        "geometry": {"type": "Point", "coordinates": [landmark.lon, landmark.lat]},
        "is_anchor": False,
        **extra,
    }


def test_parse_keeps_reachable_landmarks_of_the_right_version() -> None:
    data = {
        "results": [
            atlas_item(NEAR),
            atlas_item(TURIN, address_v4=None),
            {"id": 9, "address_v4": "192.0.2.9", "geometry": None},
            atlas_item(BOLOGNA, address_v4="203.0.113.7"),
        ]
    }
    found = parse_landmarks(data, 4, exclude="203.0.113.7")
    assert found == [NEAR]
    assert parse_landmarks(data, 6, exclude="") == []
    assert parse_landmarks(None, 4, exclude="") == []


def test_choose_starts_with_the_target_network_then_spreads() -> None:
    centre = GeoPoint(lat=TRUTH[0], lon=TRUTH[1])
    close = Landmark(4, "198.51.100.4", 45.465, 9.19)
    picked = choose([close, TURIN, BOLOGNA, NEAR], centre, 3, asn=64500)
    assert picked[0] == NEAR and set(picked[1:]) == {TURIN, BOLOGNA}  # spread, not the next street
    assert choose([close, TURIN], centre, 1, asn=None) == [close]
    assert choose([], centre, 3, asn=None) == []
    assert search_radius_km(3) == 10 and search_radius_km(42.2) == 43 and search_radius_km(900) == 100


def test_match_ranks_by_rtt_pattern_and_needs_three_probes() -> None:
    target = {"a": 1.0, "b": 4.0, "c": 6.0}
    ranked = match(
        target,
        {
            NEAR: {"a": 1.1, "b": 4.2, "c": 5.9},
            TURIN: {"a": 3.0, "b": 1.0, "c": 9.0},
            BOLOGNA: {"a": 1.0, "b": 4.0},  # only two probes in common
        },
    )
    assert [m.landmark for m in ranked] == [NEAR, TURIN]
    assert ranked[0].score_ms == 0.1 and ranked[0].shared == 3


def test_evidence_leans_to_the_best_match() -> None:
    ranked = match({"a": 1.0, "b": 2.0, "c": 3.0}, {NEAR: {"a": 1, "b": 2, "c": 3}, TURIN: {"a": 3, "b": 5, "c": 1}})
    evidence = landmark_evidence(ranked, 3)
    assert evidence is not None and evidence.kind is EvidenceKind.SOFT and evidence.point is not None
    to_near = haversine(evidence.point.lat, evidence.point.lon, NEAR.lat, NEAR.lon)
    to_turin = haversine(evidence.point.lat, evidence.point.lon, TURIN.lat, TURIN.lon)
    assert to_near < to_turin and evidence.radius_km is not None and evidence.radius_km >= to_turin - 0.5
    assert evidence.details["experimental"] is True and "2 of 3 landmarks" in evidence.summary
    assert landmark_evidence([], 3) is None


class LandmarkNetwork(SimulatedNetwork):
    """The simulated fleet; a reuse key pings a landmark from the probes of the regional zoom."""

    zoom: ClassVar[list[tuple[str, str, float, float]]] = []
    landmark_pings: ClassVar[int] = 0

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        if not isinstance(locations, str):
            if any(spec.country == "IT" for spec in locations):
                LandmarkNetwork.zoom = COUNTRY["IT"][:4]
            return await super().ping(target, locations, packets, ctx, tcp_port)
        LandmarkNetwork.landmark_pings += 1
        spot = next(lm for lm in LANDMARKS if lm.address == str(target.address))
        results = []
        for city, country, lat, lon in LandmarkNetwork.zoom:
            probe = Probe(
                provider=self.name, probe_id=city, location=GeoPoint(lat=lat, lon=lon), city=city, country=country
            )
            rtt = haversine(lat, lon, spot.lat, spot.lon) / 100 * 1.2 + 0.5
            results.append(ping_result(probe, (rtt,), 1))
        return PingBatch(provider=self.name, results=results, reuse_key="sim", tests_used=len(results))


@respx.mock
async def test_campaign_matches_landmarks_when_enabled(app_paths: AppPaths) -> None:
    respx.get(url__startswith=ATLAS_PROBES_URL).respond(200, json={"results": [atlas_item(lm) for lm in LANDMARKS]})
    LandmarkNetwork.landmark_pings = 0
    plain, _, _ = await run_campaign(app_paths, LandmarkNetwork(), traceroute_probes=0)
    assert LandmarkNetwork.landmark_pings == 0 and not any(e.source == "landmark" for e in plain.evidence)

    report, logs, _ = await run_campaign(app_paths, LandmarkNetwork(), traceroute_probes=0, landmarks=3)
    assert LandmarkNetwork.landmark_pings == 3
    assert any(m.startswith("Experimental: pinging 3 RIPE Atlas probes") for m in logs)
    found = next(e for e in report.evidence if e.source == "landmark")
    assert found.details["landmarks"][0]["probe_id"] == NEAR.probe_id
    assert found.point is not None and haversine(found.point.lat, found.point.lon, *TRUTH) < 60
    assert "landmarks" in {s.stage for s in report.stages}
    assert report.tests_used == plain.tests_used + 3 * len(LandmarkNetwork.zoom)


@respx.mock
async def test_landmarks_respect_the_budget(app_paths: AppPaths) -> None:
    route = respx.get(url__startswith=ATLAS_PROBES_URL).respond(200, json={"results": []})
    LandmarkNetwork.landmark_pings = 0
    plain, _, _ = await run_campaign(app_paths, LandmarkNetwork(), traceroute_probes=0)
    _, logs, _ = await run_campaign(
        app_paths, LandmarkNetwork(), traceroute_probes=0, landmarks=5, max_tests=plain.tests_used + 4
    )
    assert LandmarkNetwork.landmark_pings == 0 and not route.called
    assert any("too few left in the budget" in m for m in logs)
