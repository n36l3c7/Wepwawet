"""Hostname hints, IXP matching and traceroute analysis (no network)."""

import ipaddress
from collections.abc import Sequence
from typing import ClassVar

import pytest
import respx

from tests.conftest import make_ctx, report_for
from tests.test_measurement import COUNTRY, FLEET, TRUTH, SimulatedNetwork
from wepwawet.config import Config
from wepwawet.core.campaign import Campaign, traceroute_locations
from wepwawet.core.geo import haversine
from wepwawet.core.geohints import HintData, extract_hints, fold, parse_rules
from wepwawet.core.ixp import build_index
from wepwawet.core.models import GeoPoint, NetworkClass, Probe, ProbeResult, Target
from wepwawet.core.traceroutes import (
    access_technology,
    analyse_traces,
    estimate_access,
    find_convergence,
    flag_path,
    locate_hop,
)
from wepwawet.data.gazetteer import City
from wepwawet.data.places import Place, convert_airports, convert_locode, load_places, parse_locode_coordinates
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import (
    LocationSpec,
    RawHop,
    TraceBatch,
    TraceResult,
    parse_mtr_json,
    parse_traceroute_output,
)
from wepwawet.providers.measurement.globalping import parse_mtr_results

CITIES = (
    City("Milan", "IT", 45.46427, 9.18951, 1_371_498),
    City("Frankfurt am Main", "DE", 50.11552, 8.68417, 650_000),
    City("Amsterdam", "NL", 52.37403, 4.88969, 741_636),
    City("Zurich", "CH", 47.36667, 8.55, 341_730),
    City("Kearney", "US", 40.7, -99.08, 30_000),
)
AIRPORTS = {
    "FRA": Place("FRA", "Frankfurt am Main", "DE", 50.0333, 8.5706),
    "LAX": Place("LAX", "Los Angeles", "US", 33.94, -118.41),
    "ZRH": Place("ZRH", "Zurich", "CH", 47.46, 8.55),
}
LOCODES = {"ITMIL": Place("ITMIL", "Milano", "IT", 45.4667, 9.2), "ATLAS": Place("ATLAS", "Lassing", "AT", 47.5, 14.6)}
RULES = parse_rules(
    """
[[rule]]
operator = "Example backbone"
domain = "bb.example"
pattern = '^(?P<code>[a-z]{3})-'
kind = "map"
[rule.codes]
ffm = "Frankfurt am Main, DE"
"""
)


@pytest.fixture
def hints() -> HintData:
    return HintData.build(AIRPORTS, LOCODES, CITIES, RULES)


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("ffm-bb2.bb.example", ("operator", "Frankfurt am Main")),
        ("xe-0-0-1.core.itmil01.example.it", ("locode", "Milano")),
        ("ae1.cr2.fra1.example.net", ("iata", "Frankfurt am Main")),
        ("bundle1.edge2.amsterdam.example.com", ("city", "Amsterdam")),
        ("lag-3.rtr.mil.example.net", ("iata", "Milan")),  # IATA metro code, not an airport
    ],
)
def test_hints(hints: HintData, hostname: str, expected: tuple[str, str]) -> None:
    found = extract_hints(hostname, hints)
    assert (found[0].kind, found[0].name) == expected


def test_operator_rule_silences_the_generic_guesses(hints: HintData) -> None:
    found = extract_hints("ffm-bb2.zrh.bb.example", hints)
    assert [h.name for h in found] == ["Frankfurt am Main"]


def test_router_words_and_unnumbered_locodes(hints: HintData) -> None:
    assert extract_hints("ae1.core.edge.example.net", hints) == []
    assert extract_hints("k.root-servers.net", hints) == []
    assert extract_hints(None, hints) == []
    numbered = extract_hints("itmil01.example.net", hints)[0]
    bare = extract_hints("itmil.example.net", hints)[0]
    assert numbered.confidence > bare.confidence


def test_fold() -> None:
    assert fold("Düsseldorf") == "dusseldorf" and fold("New York City") == "newyorkcity"


def test_shipped_rules_parse() -> None:
    from wepwawet.core.geohints import default_rules

    rules = default_rules()
    assert {r.domain for r in rules} >= {"he.net", "twelve99.net", "1e100.net", "gin.ntt.net"}
    assert all(r.kind in {"iata", "city", "map"} for r in rules)


def test_locode_coordinates() -> None:
    assert parse_locode_coordinates("4528N 00911E") == pytest.approx((45.4667, 9.1833), abs=1e-3)
    assert parse_locode_coordinates("3352S 15112E") == pytest.approx((-33.8667, 151.2), abs=1e-3)
    assert parse_locode_coordinates("") is None


def test_place_conversions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    airports = (
        '"id","ident","type","name","latitude_deg","longitude_deg","elevation_ft","continent","iso_country",'
        '"iso_region","municipality","scheduled_service","icao_code","iata_code"\n'
        '1,"LIML","medium_airport","Linate",45.445,9.276,353,"EU","IT","IT-25","Milan","yes","LIML","LIN"\n'
        '2,"XX","heliport","Pad",1,1,1,"EU","IT","IT-25","Nowhere","no",,""\n'
    )
    assert convert_airports(airports, tmp_path / "a.tsv") == 1
    assert load_places(tmp_path / "a.tsv")["LIN"].name == "Milan"
    locode = "Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,Date,IATA,Coordinates,Remarks\n"
    locode += ",IT,MIL,Milano,Milano,MI,AI,1234----,0101,,4528N 00911E,\n,IT,XXX,Nowhere,Nowhere,,,,,,,\n"
    assert convert_locode(locode, tmp_path / "l.tsv") == 1
    assert load_places(tmp_path / "l.tsv")["ITMIL"].country == "IT"


MTR_JSON = {
    "report": {
        "hubs": [
            {"count": 1, "host": "192.168.1.1", "Loss%": 0.0, "Best": 0.4},
            {"count": 2, "host": "???", "Loss%": 100.0, "Best": 0.0},
            {"count": 3, "host": "193.0.14.129", "Loss%": 0.0, "Best": 12.1},
        ]
    }
}
TRACEROUTE_TEXT = """traceroute to 193.0.14.129 (193.0.14.129), 30 hops max, 60 byte packets
 1  192.168.1.1  0.512 ms  0.401 ms  0.390 ms
 2  * * *
 3  193.0.14.129  12.3 ms  12.1 ms *
"""


def test_parse_local_traces() -> None:
    mtr = parse_mtr_json(MTR_JSON)
    assert [(h.ttl, h.address, h.min_rtt) for h in mtr] == [
        (1, "192.168.1.1", 0.4),
        (2, None, None),
        (3, "193.0.14.129", 12.1),
    ]
    text = parse_traceroute_output(TRACEROUTE_TEXT)
    assert [(h.ttl, h.address, h.min_rtt) for h in text] == [
        (1, "192.168.1.1", 0.39),
        (2, None, None),
        (3, "193.0.14.129", 12.1),
    ]


def test_parse_globalping_mtr() -> None:
    data = {
        "results": [
            {
                "probe": {"city": "Zurich", "country": "CH", "asn": 1, "latitude": 47.37, "longitude": 8.54},
                "result": {
                    "status": "finished",
                    "hops": [
                        {
                            "resolvedAddress": "10.0.0.1",
                            "resolvedHostname": "10.0.0.1",
                            "asn": [],
                            "timings": [{"rtt": 0.5}],
                        },
                        {"resolvedAddress": None, "timings": []},
                        {
                            "resolvedAddress": "193.0.14.129",
                            "resolvedHostname": "k.root-servers.net",
                            "asn": [25152],
                            "timings": [{"rtt": 3.1}, {"rtt": 2.9}],
                        },
                    ],
                },
            }
        ]
    }
    (trace,) = parse_mtr_results(data, "193.0.14.129", "ICMP")
    assert trace.reached and trace.hops[0].hostname is None
    assert trace.hops[2].asn == 25152 and trace.hops[2].min_rtt == 2.9 and trace.hops[1].address is None


def test_hop_location_respects_the_rtt(hints: HintData) -> None:
    # A router 0.6 ms from a Zurich probe cannot be in Frankfurt (300 km needs 3 ms of round trip).
    near = locate_hop(
        RawHop(ttl=3, address="203.0.113.1", hostname="ae1.cr2.fra1.example.net", rtts=(0.6,)),
        47.37,
        8.54,
        hints,
        None,
        200,
    )
    assert near.hints and near.location is None
    far = locate_hop(
        RawHop(ttl=3, address="203.0.113.1", hostname="ae1.cr2.fra1.example.net", rtts=(4.0,)),
        47.37,
        8.54,
        hints,
        None,
        200,
    )
    assert far.location_label == "Frankfurt am Main, DE" and far.location_source == "hint"


def test_ixp_index_wins_over_hints(hints: HintData) -> None:
    index = build_index(
        [{"ixlan_id": 10, "prefix": "185.1.0.0/24"}, {"ixlan_id": 99, "prefix": "bad"}],
        [{"id": 10, "ix_id": 7}],
        [{"id": 7, "name": "MIX-IT", "city": "Milan", "country": "IT"}],
        hints,
    )
    assert len(index) == 1 and index.lookup("185.1.0.20").name == "MIX-IT"
    assert index.lookup("185.2.0.1") is None and index.lookup("nonsense") is None
    hop = locate_hop(
        RawHop(ttl=5, address="185.1.0.20", hostname="ae1.cr2.fra1.example.net", rtts=(9.0,)),
        47.37,
        8.54,
        hints,
        index,
        200,
    )
    assert hop.ixp == "MIX-IT" and hop.location_source == "ixp" and hop.location_label == "MIX-IT (Milan)"


def make_trace(
    city: str, lat: float, lon: float, hops: Sequence[tuple[str | None, str | None, float | None]]
) -> TraceResult:
    probe = Probe(provider="globalping", probe_id=city, location=GeoPoint(lat=lat, lon=lon), city=city, country="XX")
    raw = tuple(
        RawHop(ttl=i, address=a, hostname=h, rtts=(r,) if r is not None else ()) for i, (a, h, r) in enumerate(hops, 1)
    )
    return TraceResult(probe=probe, protocol="ICMP", hops=raw, reached=any(h.address == "193.0.14.129" for h in raw))


TRACES = [
    make_trace(
        "Zurich",
        47.37,
        8.54,
        [
            ("10.0.0.1", None, 0.4),
            ("62.115.10.9", "itmil01.example.it", 3.0),
            ("62.115.10.77", "bng-7.example.it", 3.6),
            ("193.0.14.129", None, 6.1),
        ],
    ),
    make_trace(
        "Frankfurt",
        50.11,
        8.68,
        [
            ("10.1.0.1", None, 0.3),
            ("62.115.10.20", None, 6.0),
            ("62.115.10.77", "bng-7.example.it", 7.2),
            ("193.0.14.129", None, 9.4),
        ],
    ),
    make_trace(
        "Paris",
        48.86,
        2.35,
        [
            ("10.2.0.1", None, 0.5),
            (None, None, None),
            (None, None, None),
            ("62.115.10.30", None, 30.0),
            ("62.115.10.31", None, 9.0),
        ],
    ),
]


def test_convergence_and_access(hints: HintData) -> None:
    reports = analyse_traces(TRACES, "193.0.14.129", hints, None, 200)
    conv = find_convergence(reports, "193.0.14.129", 200)
    assert conv is not None and conv.address == "62.115.10.77" and (conv.paths, conv.of) == (2, 3)
    assert conv.rtt_to_target_ms == pytest.approx(2.2) and conv.max_distance_km == pytest.approx(240)
    access = estimate_access(reports, "193.0.14.129", [])
    assert access is not None and access.paths == 2 and access.delay_ms == pytest.approx(2.35)
    assert access.technology.startswith("fibre")
    paris = reports[2]
    assert set(paris.flags) == {"mpls_or_hidden_hops", "rtt_decreases", "target_not_reached"}


def test_no_convergence_with_one_path(hints: HintData) -> None:
    assert find_convergence(analyse_traces(TRACES[:1], "193.0.14.129", hints, None, 200), "193.0.14.129", 200) is None


def test_access_technology() -> None:
    assert access_technology(0.2, []) == "datacentre or LAN"
    assert access_technology(8, []) == "cable or xDSL"
    assert access_technology(1, [NetworkClass.MOBILE]) == "mobile"


def test_unresponsive_tail_flag() -> None:
    from wepwawet.core.models import HopReport

    hops = [HopReport(ttl=1, address="10.0.0.1", min_rtt=1.0), HopReport(ttl=2), HopReport(ttl=3)]
    assert flag_path(hops, reached=False) == ["unresponsive_tail"]


def test_traceroute_locations_pick_distinct_cities() -> None:
    def result(city: str, rtt: float | None, provider: str = "globalping") -> ProbeResult:
        probe = Probe(
            provider=provider, probe_id=city + str(rtt), location=GeoPoint(lat=0, lon=0), city=city, country="IT", asn=1
        )
        return ProbeResult(probe=probe, phase="zoom", min_rtt=rtt)

    probes = [result("Milan", 1), result("Milan", 2), result("Turin", 3), result("Rome", None), result("Bari", 4, "x")]
    specs = traceroute_locations(probes, "globalping", 5)
    assert [s.city for s in specs] == ["Milan", "Turin"]


class TracingNetwork(SimulatedNetwork):
    """The simulated network, plus traceroutes that converge on one Milan router."""

    supports_traceroute = True
    traced: ClassVar[list[str]] = []

    async def traceroute(
        self,
        target: Target,
        locations: Sequence[LocationSpec],
        ctx: ProviderContext,
        protocol: str = "ICMP",
        port: int | None = None,
    ) -> TraceBatch:
        TracingNetwork.traced.append(protocol)
        pool = {
            city: (country, lat, lon)
            for group in [*FLEET.values(), *COUNTRY.values()]
            for city, country, lat, lon in group
        }
        results = []
        for spec in locations:
            country, lat, lon = pool[spec.city or ""]
            rtt = haversine(lat, lon, *TRUTH) / 100 * 1.2 + 0.5
            probe = Probe(
                provider=self.name,
                probe_id=spec.city or "",
                location=GeoPoint(lat=lat, lon=lon),
                city=spec.city,
                country=country,
            )
            hops = (
                RawHop(ttl=1, address="10.0.0.1", rtts=(0.3,)),
                RawHop(ttl=2, address="62.115.10.77", hostname="bng-7.mil.example.net", rtts=(max(0.4, rtt - 2.5),)),
                RawHop(ttl=3, address=str(target.address), rtts=(rtt + 0.5,)),
            )
            results.append(TraceResult(probe=probe, protocol=protocol, hops=hops, reached=True))
        return TraceBatch(provider=self.name, results=results, tests_used=len(results))


@respx.mock
async def test_campaign_traceroute_phase(app_paths: AppPaths) -> None:
    respx.get(url__startswith="https://www.peeringdb.com/api/").respond(json={"data": []})
    respx.get(url__startswith="http://127.0.0.1").respond(json={})
    config = Config.model_validate({"measurements": {"traceroute_probes": 3}})
    ctx = make_ctx(app_paths, config=config)
    report = report_for("193.0.14.129")
    logs: list[str] = []
    TracingNetwork.traced = []
    await Campaign(ctx, [TracingNetwork()], lambda _l, _s, m: logs.append(m)).run(report, lambda: None)
    assert len(report.traceroutes) == 3 and TracingNetwork.traced == ["ICMP"]
    assert report.convergence is not None and report.convergence.address == "62.115.10.77"
    assert report.access is not None and report.access.delay_ms == pytest.approx(3.0, abs=0.01)
    assert any(e.source == "traceroute" and "converge" in e.summary for e in report.evidence)
    assert report.estimate is not None and "access delay" in report.estimate.explanation[0]
    assert ipaddress.ip_address("193.0.14.129")


def test_private_hops_are_neither_shared_nor_access_routers(hints: HintData) -> None:
    traces = [
        make_trace("A", 47.37, 8.54, [("10.0.0.1", None, 0.4), ("193.0.14.129", None, 50.0)]),
        make_trace("B", 50.11, 8.68, [("10.0.0.1", None, 0.3), ("193.0.14.129", None, 40.0)]),
    ]
    reports = analyse_traces(traces, "193.0.14.129", hints, None, 200)
    assert find_convergence(reports, "193.0.14.129", 200) is None
    assert estimate_access(reports, "193.0.14.129", []) is None
