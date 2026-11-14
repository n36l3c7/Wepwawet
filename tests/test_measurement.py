"""Measurement providers, gazetteer and the ping campaign, with canned answers or a simulated network."""

import io
import ipaddress
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest
import respx

from tests.conftest import make_ctx, report_for
from wepwawet.config import Config
from wepwawet.core.campaign import Campaign, merge_results, sweep_locations, zoom_countries, zoom_locations
from wepwawet.core.geo import haversine
from wepwawet.core.http import ProviderError
from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, Probe, ProbeResult, Target
from wepwawet.data.gazetteer import FILE_NAME, City, candidate_cities, convert_geonames, nearest_city
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement import globalping as gp
from wepwawet.providers.measurement import ripe_atlas as atlas
from wepwawet.providers.measurement.base import (
    Budget,
    LocationSpec,
    MeasurementProvider,
    PingBatch,
    parse_ping_output,
    ping_result,
)
from wepwawet.providers.measurement.local import LocalPingProvider, SshPingProvider

IPUTILS = """PING 193.0.14.129 (193.0.14.129) 56(84) bytes of data.
64 bytes from 193.0.14.129: icmp_seq=1 ttl=57 time=12.4 ms
64 bytes from 193.0.14.129: icmp_seq=2 ttl=57 time=11.9 ms

--- 193.0.14.129 ping statistics ---
3 packets transmitted, 2 received, 33.3333% packet loss, time 602ms
rtt min/avg/max/mdev = 11.900/12.150/12.400/0.250 ms
"""
BSD = """64 bytes from 1.1.1.1: icmp_seq=0 ttl=58 time=3.245 ms
64 bytes from 1.1.1.1: icmp_seq=1 ttl=58 time=3.101 ms
--- 1.1.1.1 ping statistics ---
2 packets transmitted, 2 packets received, 0.0% packet loss
"""


def test_parse_ping_output() -> None:
    assert parse_ping_output(IPUTILS) == ((12.4, 11.9), 3, 2)
    assert parse_ping_output(BSD) == ((3.245, 3.101), 2, 2)
    assert parse_ping_output("3 packets transmitted, 0 received, 100% packet loss") == ((), 3, 0)


def test_ping_result_statistics() -> None:
    probe = Probe(provider="x", probe_id="1", location=GeoPoint(lat=0, lon=0))
    result = ping_result(probe, (12.4, 11.9), 3)
    assert result.min_rtt == 11.9 and result.avg_rtt == 12.15 and result.loss == pytest.approx(1 / 3)
    empty = ping_result(probe, (), 3)
    assert empty.min_rtt is None and empty.loss == 1


GP = "https://api.globalping.io/v1"


def gp_result(city: str, country: str, lat: float, lon: float, rtts: list[float]) -> dict[str, object]:
    return {
        "probe": {
            "city": city,
            "country": country,
            "asn": 3320,
            "network": "Example",
            "latitude": lat,
            "longitude": lon,
        },
        "result": {
            "status": "finished",
            "timings": [{"rtt": r, "ttl": 58} for r in rtts],
            "stats": {"min": min(rtts) if rtts else None, "total": 3, "loss": 0 if rtts else 100},
        },
    }


@respx.mock
async def test_globalping_ping(ctx: ProviderContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gp, "POLL_INTERVAL", 0)
    create = respx.post(f"{GP}/measurements").respond(202, json={"id": "m1", "probesCount": 2})
    respx.get(f"{GP}/measurements/m1").mock(
        side_effect=[
            respx.MockResponse(200, json={"status": "in-progress", "results": []}),
            respx.MockResponse(
                200,
                json={
                    "status": "finished",
                    "results": [
                        gp_result("Berlin", "DE", 52.52, 13.40, [4.1, 4.6, 5.0]),
                        gp_result("Lost", "FR", 48.8, 2.3, []),
                    ],
                },
            ),
        ]
    )
    target = Target(address=ipaddress.ip_address("193.0.14.129"))
    batch = await gp.GlobalpingProvider().ping(target, [LocationSpec(continent="EU", limit=2)], 3, ctx, tcp_port=443)
    body = create.calls.last.request.read().decode()
    assert '"protocol":"TCP"' in body.replace(" ", "") and '"continent":"EU"' in body.replace(" ", "")
    assert batch.reuse_key == "m1" and batch.tests_used == 2
    berlin, lost = batch.results
    assert berlin.min_rtt == 4.1 and berlin.probe.city == "Berlin" and lost.min_rtt is None


@respx.mock
async def test_globalping_reuses_probes_by_measurement_id(
    ctx: ProviderContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gp, "POLL_INTERVAL", 0)
    create = respx.post(f"{GP}/measurements").respond(202, json={"id": "m2"})
    respx.get(f"{GP}/measurements/m2").respond(json={"status": "finished", "results": []})
    await gp.GlobalpingProvider().ping(Target(address=ipaddress.ip_address("1.1.1.1")), "m1", 3, ctx)
    assert '"locations":"m1"' in create.calls.last.request.read().decode().replace(" ", "")


@respx.mock
async def test_globalping_no_matching_probes(ctx: ProviderContext) -> None:
    respx.post(f"{GP}/measurements").respond(422, json={"error": {"message": "No suitable probes"}})
    batch = await gp.GlobalpingProvider().ping(Target(address=ipaddress.ip_address("1.1.1.1")), [], 3, ctx)
    assert batch.results == []


@respx.mock
async def test_globalping_rate_limited(ctx: ProviderContext) -> None:
    respx.post(f"{GP}/measurements").respond(429, headers={"Retry-After": "120"}, json={"error": {"message": "slow"}})
    with pytest.raises(ProviderError) as info:
        await gp.GlobalpingProvider().ping(Target(address=ipaddress.ip_address("1.1.1.1")), [], 3, ctx)
    assert info.value.status == 429 and info.value.retry_after == 120


@respx.mock
async def test_globalping_budget_and_token(app_paths: AppPaths) -> None:
    ctx = make_ctx(app_paths, secrets={"globalping": "tok"})
    route = respx.get(f"{GP}/limits").respond(
        json={"rateLimit": {"measurements": {"create": {"limit": 500, "remaining": 321, "reset": 600}}}}
    )
    budget = await gp.GlobalpingProvider().budget(ctx)
    assert budget == Budget("globalping", "tests/hour", 500, 321, 600)
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


def test_location_json() -> None:
    assert gp.location_json(LocationSpec(country="IT", limit=5)) == {"country": "IT", "limit": 5}
    assert gp.location_json(LocationSpec(asn=3269, limit=2)) == {"asn": 3269, "limit": 2}


ATLAS = "https://atlas.ripe.net/api/v2"


def test_ripe_atlas_is_off_without_a_credit_budget(app_paths: AppPaths) -> None:
    provider = atlas.RipeAtlasProvider()
    assert "API key" in (provider.unavailable_reason(make_ctx(app_paths)) or "")
    keyed = make_ctx(app_paths, secrets={"ripe_atlas": "k"})
    assert "max_credits" in (provider.unavailable_reason(keyed) or "")
    funded = make_ctx(
        app_paths,
        secrets={"ripe_atlas": "k"},
        config=Config.model_validate({"providers": {"ripe_atlas": {"max_credits": 500}}}),
    )
    assert provider.unavailable_reason(funded) is None


def test_ripe_atlas_costs() -> None:
    assert atlas.ping_cost(10, 3) == 60
    assert atlas.probe_spec(LocationSpec(country="it", limit=4)) == {"type": "country", "value": "IT", "requested": 4}
    assert atlas.probe_spec(LocationSpec(continent="EU", limit=3))["type"] == "area"


async def test_ripe_atlas_refuses_to_overspend(app_paths: AppPaths) -> None:
    ctx = make_ctx(
        app_paths,
        secrets={"ripe_atlas": "k"},
        config=Config.model_validate({"providers": {"ripe_atlas": {"max_credits": 50}}}),
    )
    with pytest.raises(ProviderError, match="would cost 60 credits"):
        await atlas.RipeAtlasProvider().ping(
            Target(address=ipaddress.ip_address("1.1.1.1")), [LocationSpec(country="IT", limit=10)], 3, ctx
        )


@respx.mock
async def test_ripe_atlas_flow(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "POLL_INTERVAL", 0)
    ctx = make_ctx(
        app_paths,
        secrets={"ripe_atlas": "k"},
        config=Config.model_validate({"providers": {"ripe_atlas": {"max_credits": 100}}}),
    )
    create = respx.post(f"{ATLAS}/measurements/").respond(201, json={"measurements": [777]})
    respx.get(f"{ATLAS}/measurements/777/results/").respond(
        json=[
            {"prb_id": 6001, "min": 0.79, "sent": 3, "result": [{"rtt": 0.82}, {"rtt": 0.79}, {"x": "*"}]},
            {"prb_id": 6002, "min": -1, "sent": 3, "result": [{"x": "*"}, {"x": "*"}, {"x": "*"}]},
        ]
    )
    respx.get(f"{ATLAS}/probes/").respond(
        json={
            "results": [
                {"id": 6001, "geometry": {"type": "Point", "coordinates": [4.89, 52.37]}, "country_code": "NL"},
                {"id": 6002, "geometry": {"type": "Point", "coordinates": [2.35, 48.86]}, "country_code": "FR"},
            ]
        }
    )
    batch = await atlas.RipeAtlasProvider().ping(
        Target(address=ipaddress.ip_address("193.0.14.129")), [LocationSpec(country="NL", limit=2)], 3, ctx
    )
    assert create.calls.last.request.headers["Authorization"] == "Key k"
    first, second = batch.results
    assert first.min_rtt == 0.79 and first.probe.location.lat == 52.37 and second.min_rtt is None
    assert batch.tests_used == 12


def test_local_needs_a_location(ctx: ProviderContext) -> None:
    assert "providers.local.lat" in (LocalPingProvider().unavailable_reason(ctx) or "")


def test_ssh_argv_quotes_nothing_from_the_user(app_paths: AppPaths) -> None:
    host = {"name": "vps1", "host": "vps.example", "user": "probe", "port": 2222, "lat": 50.1, "lon": 8.7}
    argv = SshPingProvider.argv(host, Target(address=ipaddress.ip_address("2001:7fd::1")), 3)
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    assert argv[-2:] == ["probe@vps.example", "ping -6 -n -c 3 -W 2 -i 0.3 2001:7fd::1"]
    config = Config.model_validate({"providers": {"ssh": {"hosts": [host, {"host": "no-location"}]}}})
    assert len(SshPingProvider().hosts(make_ctx(app_paths, config=config))) == 1


def test_geonames_conversion_and_ranking(tmp_path: Path) -> None:
    rows = [
        ["3173435", "Milan", "Milano", "", "45.46427", "9.18951", "P", "PPLA", "IT", "", "", "", "", "", "1371498"],
        ["3172394", "Monza", "Monza", "", "45.58005", "9.27246", "P", "PPLA2", "IT", "", "", "", "", "", "122671"],
        ["3169070", "Rome", "Roma", "", "41.89193", "12.51133", "P", "PPLC", "IT", "", "", "", "", "", "2318895"],
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("cities15000.txt", "\n".join("\t".join(r) for r in rows))
    target = tmp_path / FILE_NAME
    assert convert_geonames(buffer.getvalue(), target) == 3
    from wepwawet.data.gazetteer import load_cities

    cities = load_cities(target)
    assert cities[0] == City("Milan", "IT", 45.46427, 9.18951, 1371498)

    from tests.test_geo import MILAN, disks_for
    from wepwawet.core.multilateration import region_and_cells

    result = region_and_cells(disks_for(MILAN, inflation=1.1, extra_km=20))
    assert result is not None
    region, cells = result
    names = [c.name for c in candidate_cities(cities, region, cells)]
    assert names[0] == "Milan" and "Rome" not in names
    near = nearest_city(cities, GeoPoint(lat=45.47, lon=9.2))
    assert near is not None and near.name == "Milan"


# --- campaign against a simulated network --------------------------------------------------------

TRUTH = (45.4642, 9.19)  # Milan
FLEET = {
    "EU": [
        ("Zurich", "CH", 47.37, 8.54),
        ("Frankfurt", "DE", 50.11, 8.68),
        ("Paris", "FR", 48.86, 2.35),
        ("London", "GB", 51.51, -0.13),
    ],
    "NA": [("New York", "US", 40.71, -74.0)],
    "AS": [("Tokyo", "JP", 35.68, 139.69)],
    "SA": [("Sao Paulo", "BR", -23.55, -46.63)],
    "OC": [("Sydney", "AU", -33.87, 151.21)],
    "AF": [("Lagos", "NG", 6.52, 3.38)],
}
COUNTRY = {
    "IT": [
        ("Milan", "IT", 45.47, 9.18),
        ("Turin", "IT", 45.07, 7.68),
        ("Bologna", "IT", 44.49, 11.34),
        ("Rome", "IT", 41.90, 12.50),
    ],
    "CH": [("Lugano", "CH", 46.0, 8.95), ("Geneva", "CH", 46.2, 6.14)],
    "DE": [("Munich", "DE", 48.14, 11.58)],
}


class SimulatedNetwork(MeasurementProvider):
    """RTT = 1.2 × light-in-fibre time for the real distance + 0.5 ms, from a fixed fleet."""

    name = "globalping"
    label = "Simulated"
    supports_tcp = True
    answers_icmp: ClassVar[bool] = True
    calls: ClassVar[list[str]] = []

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        SimulatedNetwork.calls.append("tcp" if tcp_port else "icmp")
        chosen = []
        for spec in [] if isinstance(locations, str) else locations:
            pool = FLEET.get(spec.continent or "", []) + COUNTRY.get(spec.country or "", [])
            chosen += pool[: spec.limit]
        results = []
        for city, country, lat, lon in chosen:
            probe = Probe(
                provider=self.name, probe_id=city, location=GeoPoint(lat=lat, lon=lon), city=city, country=country
            )
            rtt = haversine(lat, lon, *TRUTH) / 100 * 1.2 + 0.5
            replies = (rtt, rtt + 0.3) if (self.answers_icmp or tcp_port) else ()
            results.append(ping_result(probe, replies, 2))
        return PingBatch(provider=self.name, results=results, reuse_key="sim", tests_used=len(results))


async def run_campaign(app_paths: AppPaths, provider: MeasurementProvider, **measurements: object) -> tuple:
    config = Config.model_validate({"measurements": measurements})
    ctx = make_ctx(app_paths, config=config)
    logs: list[str] = []
    report = report_for("193.0.14.129")
    report.evidence.append(
        Evidence(source="geoip", kind=EvidenceKind.SOFT, summary="x", point=GeoPoint(lat=45.5, lon=9.2), country="IT")
    )
    progress: list[int] = []
    await Campaign(ctx, [provider], lambda _l, _s, m: logs.append(m)).run(report, lambda: progress.append(1))
    return report, logs, progress


async def test_campaign_finds_the_region(app_paths: AppPaths) -> None:
    SimulatedNetwork.answers_icmp = True
    report, logs, progress = await run_campaign(app_paths, SimulatedNetwork())
    phases = {r.phase for r in report.probes}
    assert phases == {"sweep", "zoom"} and progress
    assert report.probes[0].probe.city == "Milan"  # fastest first
    assert report.region is not None and report.estimate is not None
    assert haversine(report.estimate.location.lat, report.estimate.location.lon, *TRUTH) < report.region.radius_km
    assert report.anycast is not None and not report.anycast.detected
    rtt = [e for e in report.evidence if e.source == "rtt"]
    assert len(rtt) == 1 and rtt[0].kind is EvidenceKind.HARD
    assert any("Regional zoom" in m and "IT" in m for m in logs)


async def test_campaign_falls_back_to_tcp(app_paths: AppPaths) -> None:
    SimulatedNetwork.answers_icmp = False
    SimulatedNetwork.calls = []
    report, logs, _ = await run_campaign(app_paths, SimulatedNetwork())
    SimulatedNetwork.answers_icmp = True
    assert SimulatedNetwork.calls[:2] == ["icmp", "tcp"]
    assert report.region is not None
    assert any("TCP handshake" in m for m in logs)


async def test_campaign_respects_the_test_budget(app_paths: AppPaths) -> None:
    report, logs, _ = await run_campaign(app_paths, SimulatedNetwork(), max_tests=12)
    assert {r.phase for r in report.probes} == {"sweep"}  # 9 used by the sweep, 3 left: too few to zoom
    assert any("using 12 of the 48" in m for m in logs) and any("Skipping a phase" in m for m in logs)


async def test_campaign_disabled(app_paths: AppPaths) -> None:
    report, logs, _ = await run_campaign(app_paths, SimulatedNetwork(), enabled=False)
    assert report.probes == [] and "disabled" in logs[0]


def test_planning_helpers() -> None:
    assert sum(s.limit for s in sweep_locations(48)) in range(46, 51)
    assert [s.limit for s in zoom_locations(["IT", "CH"], 30)] == [17, 13]
    report = report_for("193.0.14.129")
    report.evidence.append(
        Evidence(source="geoip", kind=EvidenceKind.SOFT, summary="x", point=GeoPoint(lat=1, lon=1), country="it")
    )
    fast = ProbeResult(
        probe=Probe(provider="g", probe_id="1", location=GeoPoint(lat=0, lon=0), country="CH"), phase="sweep", min_rtt=3
    )
    other = fast.model_copy(update={"probe": fast.probe.model_copy(update={"country": "DE"})})
    assert zoom_countries(report, [fast, other]) == ["CH", "IT", "DE"]


def test_merge_keeps_the_faster_answer() -> None:
    probe = Probe(provider="g", probe_id="1", location=GeoPoint(lat=0, lon=0))
    slow = ProbeResult(probe=probe, phase="sweep", min_rtt=9)
    fast = ProbeResult(probe=probe, phase="zoom", min_rtt=4)
    assert merge_results([slow], [fast]) == [fast]
    assert merge_results([fast], [slow]) == [fast]
