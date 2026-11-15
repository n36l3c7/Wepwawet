"""1.2.0: the local zoom, and the operator's last router measured in place of a silent address."""

from collections.abc import Sequence
from typing import ClassVar

import pytest

from tests.conftest import make_ctx, report_for
from tests.test_measurement import TRUTH, SimulatedNetwork, run_campaign
from wepwawet.config import Config
from wepwawet.core import campaign
from wepwawet.core.campaign import Campaign, local_locations, pick_proxy
from wepwawet.core.fusion import fuse
from wepwawet.core.geo import haversine
from wepwawet.core.models import CandidateCity, Evidence, EvidenceKind, GeoPoint, NetworkInfo, Probe, Region, Target
from wepwawet.data.gazetteer import City
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import (
    LocationSpec,
    PingBatch,
    RawHop,
    TraceBatch,
    TraceResult,
    ping_result,
)

MILAN = CandidateCity(name="Milan", country="IT", point=GeoPoint(lat=45.46, lon=9.19))
CITIES = (
    City("Milan", "IT", 45.4642, 9.19, 1_350_000),
    City("Monza", "IT", 45.58, 9.27, 120_000),
    City("Turin", "IT", 45.07, 7.68, 870_000),
    City("Bologna", "IT", 44.49, 11.34, 390_000),
    City("Lugano", "CH", 46.0, 8.95, 63_000),
)


def region(radius: float, candidates: tuple[CandidateCity, ...] = (MILAN,)) -> Region:
    return Region(
        resolution=5,
        cells=10,
        area_km2=3.14 * radius**2,
        centroid=MILAN.point,
        radius_km=radius,
        geometry={},
        candidates=candidates,
    )


def test_local_locations_split_between_network_and_city() -> None:
    report = report_for("151.0.0.1")
    report.region = region(80)
    report.network = NetworkInfo(asn=1267)
    specs, where = local_locations(report, 20)
    assert specs == [
        LocationSpec(country="IT", asn=1267, limit=10),
        LocationSpec(country="IT", city="Milan", limit=10),
    ]
    assert where == "Milan, IT and AS1267"
    report.network = None
    assert local_locations(report, 20)[0] == [LocationSpec(country="IT", city="Milan", limit=20)]


@pytest.mark.parametrize(
    ("radius", "candidates", "total"),
    [(5, (MILAN,), 20), (80, (), 20), (80, (MILAN,), 0)],
)
def test_no_local_zoom_when_sharp_unnamed_or_off(radius: float, candidates: tuple, total: int) -> None:
    report = report_for("151.0.0.1")
    report.region = region(radius, candidates)
    assert local_locations(report, total) == ([], "")


class CityNetwork(SimulatedNetwork):
    """The simulated fleet, plus probes in Milan's suburbs for city and network requests."""

    local_specs: list[LocationSpec] = []  # noqa: RUF012 - reset by the test

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        local = (
            [s for s in locations if not isinstance(s, str) and (s.city or s.asn)]
            if not isinstance(locations, str)
            else []
        )
        if not local:
            return await super().ping(target, locations, packets, ctx, tcp_port)
        CityNetwork.local_specs = local
        results = []
        for name, lat, lon in (("Sesto", 45.54, 9.23), ("Rho", 45.53, 9.04), ("Lodi", 45.31, 9.50)):
            probe = Probe(
                provider=self.name, probe_id=name, location=GeoPoint(lat=lat, lon=lon), city=name, country="IT"
            )
            rtt = haversine(lat, lon, *TRUTH) / 100 * 1.2 + 0.2
            results.append(ping_result(probe, (rtt,), 1))
        return PingBatch(provider=self.name, results=results, tests_used=len(results))


async def test_campaign_runs_the_local_zoom(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(campaign, "load_cities", lambda _path: CITIES)
    plain, _, _ = await run_campaign(app_paths, SimulatedNetwork(), local_probes=0)
    report, logs, _ = await run_campaign(app_paths, CityNetwork())
    assert any(m.startswith("Local zoom: 20 probes in Milan, IT") for m in logs)
    assert {r.phase for r in report.probes} == {"sweep", "zoom", "city"}
    assert plain.region is not None and report.region is not None
    assert report.region.radius_km < plain.region.radius_km
    assert haversine(report.estimate.location.lat, report.estimate.location.lon, *TRUTH) < report.region.radius_km  # type: ignore[union-attr]


PROXY = "151.6.200.1"


def hop(ttl: int, address: str | None, asn: int | None, rtt: float | None = 5.0) -> RawHop:
    return RawHop(ttl=ttl, address=address, asn=asn, rtts=(rtt,) if rtt is not None and address else ())


def trace(*hops: RawHop) -> TraceResult:
    probe = Probe(provider="globalping", probe_id="t", location=GeoPoint(lat=45.4, lon=9.2), country="IT")
    return TraceResult(probe=probe, protocol="ICMP", hops=hops, reached=False)


def test_pick_proxy_takes_the_last_public_router_of_the_operator() -> None:
    target = "151.99.0.10"
    paths = [
        trace(hop(1, "62.101.124.1", 8968), hop(2, PROXY, 1267), hop(3, "10.0.0.1", None), hop(4, None, None)),
        trace(hop(1, "151.6.1.1", 1267), hop(2, PROXY, 1267), hop(3, target, 1267)),
        trace(hop(1, "151.6.9.9", 1267), hop(2, None, None)),
    ]
    found = pick_proxy(paths, 1267, target)
    assert found is not None and found[0].address == PROXY and found[1] == 2
    assert pick_proxy(paths, 3269, target) is None
    assert pick_proxy([trace(hop(1, "10.1.1.1", 1267))], 1267, target) is None  # private: unreachable


class SilentLine(SimulatedNetwork):
    """The address answers nothing; the operator's last router answers like a host in Milan."""

    supports_traceroute = True
    tcp_batches: ClassVar[list[int]] = []

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        batch = await super().ping(target, locations, packets, ctx, tcp_port)
        if tcp_port:
            SilentLine.tcp_batches.append(len(batch.results))
        if str(target.address) != PROXY:
            batch.results = [ping_result(r.probe, (), packets) for r in batch.results]
        return batch

    async def traceroute(
        self,
        target: Target,
        locations: Sequence[LocationSpec],
        ctx: ProviderContext,
        protocol: str = "ICMP",
        port: int | None = None,
    ) -> TraceBatch:
        if str(target.address) == PROXY:
            return TraceBatch(provider=self.name)
        paths = [trace(hop(1, "151.6.1.1", 1267), hop(2, PROXY, 1267), hop(3, None, None)) for _ in range(3)]
        return TraceBatch(provider=self.name, results=paths, tests_used=3)


async def test_a_silent_address_is_measured_through_the_router_where_paths_meet(app_paths: AppPaths) -> None:
    SilentLine.tcp_batches = []
    config = Config.model_validate({"measurements": {"traceroute_probes": 0}})
    ctx = make_ctx(app_paths, config=config)
    report = report_for("151.99.0.10")
    report.network = NetworkInfo(asn=1267)
    report.evidence.append(
        Evidence(source="geoip", kind=EvidenceKind.SOFT, summary="x", point=GeoPoint(lat=45.5, lon=9.2), country="IT")
    )
    logs: list[str] = []
    await Campaign(ctx, [SilentLine()], lambda _l, _s, m: logs.append(m)).run(report, lambda: None)
    assert SilentLine.tcp_batches and max(SilentLine.tcp_batches) <= 9  # only the TCP check, not a full sweep
    assert report.proxy is not None and report.proxy.address == PROXY and report.proxy.paths == 3
    assert report.region is not None and any("router of AS1267 where the paths" in c for c in report.caveats)
    estimate = fuse(report)
    assert estimate is not None and estimate.uncertainty_km >= report.region.radius_km + report.proxy.margin_km - 0.1
    assert any("answers nothing" in line for line in estimate.explanation)
