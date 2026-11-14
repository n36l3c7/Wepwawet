"""Enrichment providers against canned API answers (no network)."""

import httpx
import pytest
import respx

from tests.conftest import FakeDns, make_ctx, report_for
from wepwawet.core.http import ProviderError
from wepwawet.core.models import EvidenceKind, NetworkClass, NetworkInfo
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.enrichment.censys import CensysProvider
from wepwawet.providers.enrichment.classify import ClassifyProvider, _decide, match_ranges, ptr_classes
from wepwawet.providers.enrichment.geoip import GeoipProvider, database_label, haversine_km, parse_record
from wepwawet.providers.enrichment.internetdb import InternetDbProvider
from wepwawet.providers.enrichment.ipmap import IpmapProvider
from wepwawet.providers.enrichment.peeringdb import PeeringDbProvider
from wepwawet.providers.enrichment.rdap import RdapProvider, pick_server
from wepwawet.providers.enrichment.rdns import ReverseDnsProvider, name_pattern, summarise_patterns
from wepwawet.providers.enrichment.ripestat import RipeStatProvider
from wepwawet.providers.enrichment.shodan import ShodanProvider

RIPESTAT = "https://stat.ripe.net/data"


def ripestat_routes(mock: respx.MockRouter, ip: str) -> None:
    mock.get(f"{RIPESTAT}/prefix-overview/data.json", params={"resource": ip}).respond(
        json={
            "data": {
                "resource": "193.0.14.0/24",
                "announced": True,
                "asns": [{"asn": 25152, "holder": "K-ROOT-SERVER"}],
                "block": {"resource": "193.0.0.0/8", "desc": "RIPE NCC (Status: ALLOCATED)", "name": "IANA IPv4"},
            }
        }
    )
    mock.get(f"{RIPESTAT}/rir-geo/data.json").respond(
        json={"data": {"located_resources": [{"resource": "193.0.14.0/24", "location": "NL"}]}}
    )
    mock.get(f"{RIPESTAT}/maxmind-geo-lite/data.json").respond(
        json={
            "data": {
                "located_resources": [
                    {
                        "resource": "193.0.14.0/24",
                        "locations": [
                            {
                                "country": "NL",
                                "city": "Amsterdam",
                                "latitude": 52.37,
                                "longitude": 4.89,
                                "covered_percentage": 100,
                            }
                        ],
                    }
                ]
            }
        }
    )


@respx.mock
async def test_ripestat(ctx: ProviderContext) -> None:
    ripestat_routes(respx.mock, "193.0.14.129")
    result = await RipeStatProvider().enrich(report_for("193.0.14.129"), ctx)
    assert result.network == NetworkInfo(
        prefix="193.0.14.0/24", asn=25152, as_name="K-ROOT-SERVER", announced=True, rir="RIPE", rir_country="NL"
    )
    geo = [e for e in result.evidence if e.source == "geoip"]
    assert geo and geo[0].city == "Amsterdam" and geo[0].point is not None
    assert not result.cached


@respx.mock
async def test_ripestat_answers_are_cached(ctx: ProviderContext) -> None:
    ripestat_routes(respx.mock, "193.0.14.129")
    await RipeStatProvider().enrich(report_for("193.0.14.129"), ctx)
    calls = respx.mock.calls.call_count
    again = await RipeStatProvider().enrich(report_for("193.0.14.129"), ctx)
    assert respx.mock.calls.call_count == calls
    assert again.cached


@respx.mock
async def test_ripestat_server_error(ctx: ProviderContext) -> None:
    respx.mock.get(url__startswith=RIPESTAT).respond(503, json={"message": "maintenance"})
    with pytest.raises(ProviderError, match="HTTP 503: maintenance"):
        await RipeStatProvider().enrich(report_for("193.0.14.129"), ctx)


BOOTSTRAP = {
    "services": [
        [["193.0.0.0/8", "194.0.0.0/7"], ["https://rdap.db.ripe.net/"]],
        [["193.0.14.0/23"], ["http://insecure.example/", "https://specific.example/rdap/"]],
        [["1.0.0.0/8"], ["https://rdap.apnic.net/"]],
    ]
}


def test_rdap_picks_longest_prefix_and_https() -> None:
    import ipaddress

    assert pick_server(BOOTSTRAP, ipaddress.ip_address("193.0.14.129")) == "https://specific.example/rdap/"
    assert pick_server(BOOTSTRAP, ipaddress.ip_address("193.1.1.1")) == "https://rdap.db.ripe.net/"
    assert pick_server(BOOTSTRAP, ipaddress.ip_address("8.8.8.8")) is None


@respx.mock
async def test_rdap(ctx: ProviderContext) -> None:
    respx.mock.get("https://data.iana.org/rdap/ipv4.json").respond(
        json={"services": [[["193.0.0.0/8"], ["https://rdap.db.ripe.net/"]]]}
    )
    respx.mock.get("https://rdap.db.ripe.net/ip/193.0.14.129").respond(
        json={
            "handle": "193.0.14.0 - 193.0.15.255",
            "startAddress": "193.0.14.0",
            "endAddress": "193.0.15.255",
            "name": "EXAMPLE-FTTH-MI",
            "type": "ASSIGNED PA",
            "country": "it",
            "parentHandle": "193.0.0.0 - 193.0.63.255",
            "remarks": [{"description": ["Residential FTTH customers, Milano"]}],
            "entities": [
                {"roles": ["administrative"], "vcardArray": ["vcard", [["fn", {}, "text", "MAINT-OBJECT"]]]},
                {"roles": ["registrant"], "vcardArray": ["vcard", [["fn", {}, "text", "Example Access SpA"]]]},
            ],
        }
    )
    result = await RdapProvider().enrich(report_for("193.0.14.129"), ctx)
    main = result.evidence[0]
    assert main.country == "IT" and main.kind is EvidenceKind.SOFT
    assert main.details["holder"] == "Example Access SpA"
    assert main.details["descriptions"] == ["Residential FTTH customers, Milano"]
    assert main.details["customer_assignment"] is True
    assert result.network is not None and result.network.netname == "EXAMPLE-FTTH-MI"
    assert result.network.rir == "RIPE"


@respx.mock
async def test_rdap_without_server(ctx: ProviderContext) -> None:
    respx.mock.get("https://data.iana.org/rdap/ipv4.json").respond(json={"services": []})
    with pytest.raises(ProviderError, match="no RDAP server"):
        await RdapProvider().enrich(report_for("193.0.14.129"), ctx)


def test_name_patterns() -> None:
    assert name_pattern("host-12.ftth.mi.example.net") == "host-N.ftth.mi.example.net"
    assert name_pattern("93-41-12-7.ip.example.it") == "{ip}.ip.example.it"
    names = [f"host-{i}.ftth.mi.example.net" for i in range(10)] + ["router.example.net"]
    assert summarise_patterns(names)[0] == ("host-N.ftth.mi.example.net", 10)


async def test_rdns_target_and_sweep(app_paths: AppPaths) -> None:
    ptrs = {"193.0.14.129": ["k.root-servers.net"]}
    ptrs.update({f"193.0.14.{i}": [f"host-{i}.ftth.mi.example.net"] for i in range(1, 40)})
    dns = FakeDns(ptrs=ptrs)
    ctx = make_ctx(app_paths, dns=dns)
    result = await ReverseDnsProvider().enrich(report_for("193.0.14.129"), ctx)
    assert result.evidence[0].details["ptr"] == ["k.root-servers.net"]
    sweep = result.evidence[1].details
    assert sweep["network"] == "193.0.14.0/24" and sweep["queried"] == 254
    assert sweep["patterns"][0] == {"pattern": "host-N.ftth.mi.example.net", "count": 39}
    first = dns.ptr_queries
    again = await ReverseDnsProvider().enrich(report_for("193.0.14.129"), ctx)
    assert dns.ptr_queries == first and again.cached


async def test_rdns_sweep_can_be_disabled(app_paths: AppPaths) -> None:
    from wepwawet.config import Config

    config = Config.model_validate({"providers": {"rdns": {"sweep": False}}})
    dns = FakeDns(ptrs={"193.0.14.129": ["k.root-servers.net"]})
    await ReverseDnsProvider().enrich(report_for("193.0.14.129"), make_ctx(app_paths, dns=dns, config=config))
    assert dns.ptr_queries == 1


def test_geoip_parses_every_layout() -> None:
    city = parse_record(
        "GeoLite2 City",
        {
            "city": {"names": {"en": "Milan"}},
            "country": {"iso_code": "IT"},
            "location": {"latitude": 45.46, "longitude": 9.19, "accuracy_radius": 20},
        },
    )
    assert city.city == "Milan" and city.point is not None and city.accuracy_km == 20
    ipinfo = parse_record("IPinfo Lite", {"country_code": "IT", "asn": "AS3269", "as_name": "Telecom Italia"})
    assert ipinfo.asn == 3269 and ipinfo.point is None and ipinfo.country == "IT"
    asn = parse_record("GeoLite2 ASN", {"autonomous_system_number": 3269, "autonomous_system_organization": "TIM"})
    assert asn.asn == 3269


def test_geoip_labels() -> None:
    from pathlib import Path

    assert database_label(Path("dbip-city-lite.mmdb"), "DBIP-City-Lite") == "DB-IP Lite"
    assert database_label(Path("ipinfo-lite.mmdb"), "ipinfo_lite.mmdb") == "IPinfo Lite"
    assert database_label(Path("GeoLite2-City.mmdb"), "GeoLite2-City") == "GeoLite2 City"


def test_haversine() -> None:
    from wepwawet.core.models import GeoPoint

    milan, rome = GeoPoint(lat=45.4642, lon=9.19), GeoPoint(lat=41.9028, lon=12.4964)
    assert 470 < haversine_km(milan, rome) < 485


async def test_geoip_compares_databases(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    from wepwawet.core.models import GeoPoint
    from wepwawet.providers.enrichment import geoip

    answers = [
        geoip.GeoipAnswer("DB-IP Lite", "Milan", "IT", GeoPoint(lat=45.46, lon=9.19), 50),
        geoip.GeoipAnswer("GeoLite2 City", "Rome", "IT", GeoPoint(lat=41.90, lon=12.50), 100),
        geoip.GeoipAnswer("IPinfo Lite", None, "IT", None, None, 3269, "Telecom Italia"),
    ]
    monkeypatch.setattr(geoip, "lookup_all", lambda _d, _a: answers)
    ctx = make_ctx(app_paths)
    (app_paths.data_dir / "geoip").mkdir(parents=True)
    (app_paths.data_dir / "geoip" / "x.mmdb").write_bytes(b"")
    assert GeoipProvider().unavailable_reason(ctx) is None
    result = await GeoipProvider().enrich(report_for("193.0.14.129"), ctx)
    assert [e.label for e in result.evidence[:3]] == ["DB-IP Lite", "GeoLite2 City", "IPinfo Lite"]
    assert "disagree" in result.evidence[-1].summary
    assert result.network is not None and result.network.asn == 3269


def test_geoip_unavailable_without_databases(ctx: ProviderContext) -> None:
    assert "data update" in (GeoipProvider().unavailable_reason(ctx) or "")


PDB = "https://www.peeringdb.com/api"


@respx.mock
async def test_peeringdb(ctx: ProviderContext) -> None:
    ctx.http.configure_pacing("peeringdb", 0.0)
    respx.mock.get(f"{PDB}/net", params={"asn": "3269"}).respond(
        json={"data": [{"id": 42, "name": "Example Access", "info_type": "Cable/DSL/ISP", "info_scope": "Regional"}]}
    )
    respx.mock.get(f"{PDB}/netfac").respond(json={"data": [{"fac_id": 1}, {"fac_id": 2}]})
    respx.mock.get(f"{PDB}/fac").respond(
        json={
            "data": [
                {"id": 1, "name": "MIL1", "city": "Milan", "country": "IT", "latitude": 45.47, "longitude": 9.1},
                {"id": 2, "name": "ROM1", "city": "Rome", "country": "IT", "latitude": None, "longitude": None},
            ]
        }
    )
    respx.mock.get(f"{PDB}/netixlan").respond(json={"data": [{"ix_id": 7}]})
    respx.mock.get(f"{PDB}/ix").respond(json={"data": [{"id": 7, "name": "MIX", "city": "Milan", "country": "IT"}]})
    report = report_for("193.0.14.129")
    report.network = NetworkInfo(asn=3269)
    result = await PeeringDbProvider().enrich(report, ctx)
    kinds = [(e.label, e.point is not None) for e in result.evidence if e.details.get("type") == "facility"]
    assert kinds == [("MIL1", True), ("ROM1", False)]
    assert result.evidence[0].details["info_types"] == ["Cable/DSL/ISP"]
    assert "MIX (Milan)" in result.evidence[-1].summary


async def test_peeringdb_without_asn(ctx: ProviderContext) -> None:
    result = await PeeringDbProvider().enrich(report_for("193.0.14.129"), ctx)
    assert "not queried" in result.evidence[0].summary


@respx.mock
async def test_peeringdb_sends_the_key(app_paths: AppPaths) -> None:
    ctx = make_ctx(app_paths, secrets={"peeringdb": "pdb-key"})
    route = respx.mock.get(f"{PDB}/net").respond(json={"data": []})
    report = report_for("193.0.14.129")
    report.network = NetworkInfo(asn=1)
    await PeeringDbProvider().enrich(report, ctx)
    assert route.calls.last.request.headers["Authorization"] == "Api-Key pdb-key"


def test_ptr_classes() -> None:
    found = dict(ptr_classes(["host-1.ftth.example.net", "mob-5g-12.example.net", "vps123.cloud.example"]))
    assert set(found) == {NetworkClass.RESIDENTIAL, NetworkClass.MOBILE, NetworkClass.HOSTING}


def test_match_ranges() -> None:
    import ipaddress

    assert match_ranges(ipaddress.ip_address("3.5.1.1"), ["3.5.0.0/16", "2600::/16"]) == "3.5.0.0/16"
    assert match_ranges(ipaddress.ip_address("2600::1"), ["3.5.0.0/16", "2600::/16"]) == "2600::/16"
    assert match_ranges(ipaddress.ip_address("8.8.8.8"), ["3.5.0.0/16"]) is None


def test_decide_prefers_strong_signals_and_keeps_mobile() -> None:
    votes = [
        (NetworkClass.RESIDENTIAL, "PTR contains 'dsl'"),
        (NetworkClass.RESIDENTIAL, "PeeringDB type 'Cable/DSL/ISP'"),
        (NetworkClass.HOSTING, "inside Amazon Web Services's published range 3.5.0.0/16"),
        (NetworkClass.MOBILE, "AS name 'Mobile'"),
    ]
    assert _decide(votes) == [NetworkClass.HOSTING, NetworkClass.MOBILE]
    assert _decide([]) == []


@respx.mock
async def test_classify_cloud_range_and_caveat(ctx: ProviderContext) -> None:
    respx.mock.get("https://ip-ranges.amazonaws.com/ip-ranges.json").respond(
        json={"prefixes": [{"ip_prefix": "3.5.0.0/16"}], "ipv6_prefixes": []}
    )
    respx.mock.get(url__regex=r"https://(www\.gstatic|docs\.oracle|api\.cloudflare).*").respond(404)
    result = await ClassifyProvider().enrich(report_for("3.5.1.1"), ctx)
    assert result.classes == [NetworkClass.HOSTING]
    assert "exit" in result.caveats[0]
    assert "Amazon Web Services" in result.evidence[0].summary


@respx.mock
async def test_shodan(app_paths: AppPaths) -> None:
    ctx = make_ctx(app_paths, secrets={"shodan": "sh-key"})
    route = respx.mock.get("https://api.shodan.io/shodan/host/193.0.14.129").respond(
        json={
            "org": "RIPE NCC",
            "city": "Amsterdam",
            "country_code": "NL",
            "latitude": 52.37,
            "longitude": 4.89,
            "hostnames": ["k.root-servers.net"],
            "data": [{"port": 53}, {"port": 443, "ssl": {"cert": {"subject": {"CN": "k.example"}}}}],
        }
    )
    result = await ShodanProvider().enrich(report_for("193.0.14.129"), ctx)
    item = result.evidence[0]
    assert item.details["ports"] == [53, 443] and item.details["certificate_names"] == ["k.example"]
    assert route.calls.last.request.url.params["key"] == "sh-key"
    assert ctx.http.cache.get("shodan", "host:193.0.14.129") is not None  # cache key never holds the API key


def test_shodan_and_censys_need_keys(ctx: ProviderContext) -> None:
    assert "WEPWAWET_SHODAN" in (ShodanProvider().unavailable_reason(ctx) or "")
    assert "WEPWAWET_CENSYS" in (CensysProvider().unavailable_reason(ctx) or "")


@respx.mock
async def test_censys(app_paths: AppPaths) -> None:
    ctx = make_ctx(app_paths, secrets={"censys": "pat", "censys_org_id": "org-1"})
    route = respx.mock.get("https://api.platform.censys.io/v3/global/asset/host/193.0.14.129").respond(
        json={
            "result": {
                "resource": {
                    "location": {
                        "city": "Amsterdam",
                        "country_code": "NL",
                        "coordinates": {"latitude": 52.37, "longitude": 4.89},
                    },
                    "autonomous_system": {"asn": 25152, "name": "K-ROOT"},
                    "dns": {"names": ["k.root-servers.net"]},
                    "services": [{"port": 53}, {"port": 443, "cert": {"parsed": {"subject_dn": "CN=k.example"}}}],
                }
            }
        }
    )
    result = await CensysProvider().enrich(report_for("193.0.14.129"), ctx)
    assert result.evidence[0].city == "Amsterdam"
    assert result.evidence[0].details["certificate_subjects"] == ["CN=k.example"]
    headers = route.calls.last.request.headers
    assert headers["Authorization"] == "Bearer pat" and headers["X-Organization-ID"] == "org-1"


@respx.mock
async def test_internetdb(ctx: ProviderContext) -> None:
    respx.mock.get("https://internetdb.shodan.io/193.0.14.129").respond(
        json={"ports": [53], "hostnames": ["k.root-servers.net"], "tags": ["vpn"], "cpes": []}
    )
    result = await InternetDbProvider().enrich(report_for("193.0.14.129"), ctx)
    assert result.evidence[0].details["tags"] == ["vpn"]


@respx.mock
async def test_internetdb_unknown_address(ctx: ProviderContext) -> None:
    respx.mock.get("https://internetdb.shodan.io/193.0.14.129").respond(404, json={"detail": "No information"})
    assert (await InternetDbProvider().enrich(report_for("193.0.14.129"), ctx)).evidence == []


@respx.mock
async def test_ipmap(ctx: ProviderContext) -> None:
    respx.mock.get("https://ipmap-api.ripe.net/v1/locate/193.0.14.129/best").respond(
        json={
            "location": {
                "cityName": "Amsterdam",
                "countryCodeAlpha2": "NL",
                "latitude": 52.37,
                "longitude": 4.89,
                "score": 14.2,
                "iataCode": "AMS",
            },
            "alternatives": [],
        }
    )
    result = await IpmapProvider().enrich(report_for("193.0.14.129"), ctx)
    assert result.evidence[0].summary == "RIPE IPmap: Amsterdam, NL (score 14)"
    assert result.evidence[0].details["iata"] == "AMS"


@respx.mock
async def test_http_retries_timeouts(app_paths: AppPaths) -> None:
    from wepwawet.core.cache import TTLCache
    from wepwawet.core.http import HttpClient

    http = HttpClient(TTLCache.in_memory(), lambda _s: 60, retries=2, backoff=0)
    route = respx.mock.get("https://example.test/x").mock(
        side_effect=[httpx.ConnectTimeout("slow"), httpx.Response(502), httpx.Response(200, json={"ok": 1})]
    )
    data, cached = await http.get_json("test", "https://example.test/x")
    assert data == {"ok": 1} and not cached and route.call_count == 3


@respx.mock
async def test_http_reports_retry_after() -> None:
    from wepwawet.core.cache import TTLCache
    from wepwawet.core.http import HttpClient

    http = HttpClient(TTLCache.in_memory(), lambda _s: 60, retries=0)
    respx.mock.get("https://example.test/x").respond(429, headers={"Retry-After": "30"}, json={"error": "slow down"})
    with pytest.raises(ProviderError) as info:
        await http.get_json("test", "https://example.test/x")
    assert info.value.status == 429 and info.value.retry_after == 30 and "slow down" in info.value.message


def test_ripestat_ignores_null_island() -> None:
    from wepwawet.providers.enrichment.ripestat import _maxmind_lite

    data = {"located_resources": [{"locations": [{"latitude": 0, "longitude": 0, "city": "", "country": ""}]}]}
    assert _maxmind_lite(data) == []
