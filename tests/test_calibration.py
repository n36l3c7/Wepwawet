"""1.4.0: CBG calibration of the nearest probes against RIPE Atlas anchors around the region."""

from collections.abc import Sequence
from typing import ClassVar

import respx

from tests.test_measurement import COUNTRY, TRUTH, SimulatedNetwork, run_campaign
from wepwawet.core.campaign import CALIBRATION_MARGIN_KM
from wepwawet.core.geo import haversine
from wepwawet.core.landmarks import ATLAS_PROBES_URL, Landmark, spread_by_distance
from wepwawet.core.models import GeoPoint, Probe, Target
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import LocationSpec, PingBatch, ping_result

ANCHORS = (
    Landmark(10, "198.51.100.10", 45.50, 9.35, anchor=True),  # 13 km from the target
    Landmark(11, "198.51.100.11", 45.07, 7.68, anchor=True),  # Turin, 125 km
    Landmark(12, "198.51.100.12", 44.49, 11.34, anchor=True),  # Bologna, 200 km
    Landmark(13, "198.51.100.13", 46.20, 6.15, anchor=True),  # Geneva, 250 km
)


def test_spread_by_distance_keeps_the_nearest_and_the_farthest() -> None:
    centre = GeoPoint(lat=TRUTH[0], lon=TRUTH[1])
    picked = spread_by_distance(list(reversed(ANCHORS)), centre, 3)
    assert picked[0] == ANCHORS[0] and picked[-1] == ANCHORS[3] and len(picked) == 3
    assert spread_by_distance(ANCHORS[:2], centre, 5) == list(ANCHORS[:2])
    assert spread_by_distance(ANCHORS, centre, 1) == [ANCHORS[0]]
    assert spread_by_distance([], centre, 3) == []


def anchor_json() -> dict[str, object]:
    return {
        "results": [
            {
                "id": a.probe_id,
                "address_v4": a.address,
                "geometry": {"type": "Point", "coordinates": [a.lon, a.lat]},
                "is_anchor": True,
            }
            for a in ANCHORS
        ]
    }


class AnchorNetwork(SimulatedNetwork):
    """The simulated fleet: RTT = 1.2 × distance / 100 + 0.5 ms, so 0.5 ms is never distance."""

    zoom: ClassVar[list[tuple[str, str, float, float]]] = []
    anchor_pings: ClassVar[int] = 0

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
                AnchorNetwork.zoom = COUNTRY["IT"][:4]
            return await super().ping(target, locations, packets, ctx, tcp_port)
        AnchorNetwork.anchor_pings += 1
        anchor = next(a for a in ANCHORS if a.address == str(target.address))
        results = []
        for city, country, lat, lon in AnchorNetwork.zoom:
            probe = Probe(
                provider=self.name, probe_id=city, location=GeoPoint(lat=lat, lon=lon), city=city, country=country
            )
            rtt = haversine(lat, lon, anchor.lat, anchor.lon) / 100 * 1.2 + 0.5
            results.append(ping_result(probe, (rtt,), 1))
        return PingBatch(provider=self.name, results=results, reuse_key="sim", tests_used=len(results))


@respx.mock
async def test_calibration_tightens_the_nearest_probes_and_keeps_the_target(app_paths: AppPaths) -> None:
    route = respx.get(url__startswith=ATLAS_PROBES_URL).respond(200, json=anchor_json())
    AnchorNetwork.anchor_pings = 0
    plain, _, _ = await run_campaign(app_paths, AnchorNetwork(), traceroute_probes=0)
    report, logs, _ = await run_campaign(
        app_paths, AnchorNetwork(), traceroute_probes=0, calibrate=True, calibration_anchors=3
    )
    assert route.called and "is_anchor=true" in str(route.calls.last.request.url)
    assert AnchorNetwork.anchor_pings == 3
    assert any(m.startswith("Calibrating ") and "against 3 RIPE Atlas anchors" in m for m in logs)
    calibrated = [r for r in report.probes if r.calibrated]
    assert {r.probe.city for r in calibrated} == {"Milan", "Turin", "Bologna", "Rome"}
    milan = next(r for r in calibrated if r.probe.city == "Milan")
    sol = next(r for r in plain.probes if r.probe.city == "Milan")
    assert milan.max_distance_km is not None and sol.max_distance_km is not None
    assert CALIBRATION_MARGIN_KM <= milan.max_distance_km < sol.max_distance_km
    assert plain.region is not None and report.region is not None
    assert report.region.radius_km < plain.region.radius_km
    assert report.estimate is not None
    assert haversine(report.estimate.location.lat, report.estimate.location.lon, *TRUTH) < report.region.radius_km


@respx.mock
async def test_calibration_respects_the_budget(app_paths: AppPaths) -> None:
    route = respx.get(url__startswith=ATLAS_PROBES_URL).respond(200, json=anchor_json())
    plain, _, _ = await run_campaign(app_paths, AnchorNetwork(), traceroute_probes=0)
    AnchorNetwork.anchor_pings = 0
    _, logs, _ = await run_campaign(
        app_paths, AnchorNetwork(), traceroute_probes=0, calibrate=True, max_tests=plain.tests_used + 5
    )
    assert AnchorNetwork.anchor_pings == 0 and not route.called
    assert any("too few left in the budget" in m for m in logs)
