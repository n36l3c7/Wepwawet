"""v1.0.0: per-stage estimates, benchmark scoring, persistent rate limits, provider timeouts, fixed token."""

import asyncio

import pytest
import respx
from typer.testing import CliRunner

from tests.conftest import make_ctx, report_for
from wepwawet.cli import main as cli
from wepwawet.core.benchmark import Anchor, AnchorResult, pick_anchors, results_csv, score, summarise
from wepwawet.core.cache import TTLCache
from wepwawet.core.http import HttpClient, ProviderError
from wepwawet.core.models import Estimate, GeoPoint, RunReport, RunStatus, TargetReport
from wepwawet.core.pipeline import Pipeline
from wepwawet.paths import AppPaths
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

MILAN = Anchor("it-mil-as1.anchors.atlas.ripe.net", "193.0.14.129", "IT", "Milan", 45.4642, 9.19)


def staged_run(address: str = "193.0.14.129") -> RunReport:
    target = report_for(address)
    for stage, lat, radius in (("passive", 41.9, 100), ("pings", 45.6, 60), ("final", 45.47, 20)):
        target.record_stage(stage, Estimate(location=GeoPoint(lat=lat, lon=9.19), uncertainty_km=radius, confidence=50))
    target.tests_used = 83
    return RunReport(id="r", query=address, status=RunStatus.DONE, targets=[target])


def test_record_stage_replaces_and_ignores_missing() -> None:
    target = TargetReport(target=report_for("193.0.14.129").target)
    estimate = Estimate(location=GeoPoint(lat=1, lon=1), uncertainty_km=10, confidence=5)
    target.record_stage("final", estimate)
    target.record_stage("final", estimate.model_copy(update={"confidence": 9}))
    target.record_stage("pings", None)
    assert [(s.stage, s.confidence) for s in target.stages] == [("final", 9)]


def test_benchmark_scoring_per_stage() -> None:
    result = score(MILAN, staged_run())
    assert result.errors_km["passive"] == pytest.approx(395, abs=3)
    assert result.covered == {"passive": False, "pings": True, "final": True}
    assert result.tests_used == 83
    missing = score(MILAN, RunReport(id="x", query="q", status=RunStatus.FAILED, error="private"))
    assert missing.error == "private"


def test_benchmark_summary_and_csv() -> None:
    results = [score(MILAN, staged_run()), AnchorResult(anchor=MILAN, error="timed out")]
    by_stage = {s.stage: s for s in summarise(results)}
    assert by_stage["final"].count == 1 and by_stage["final"].coverage == 1.0
    assert by_stage["traceroutes"].count == 0 and by_stage["traceroutes"].median_km is None
    lines = results_csv(results).splitlines()
    assert lines[0].startswith("anchor,address,country,city,tests,passive_error_km")
    assert lines[2].endswith("timed out")


def test_pick_anchors_spreads_over_countries_and_repeats() -> None:
    pool = [Anchor(f"a{i}", f"192.0.2.{i}", "IT" if i < 5 else f"C{i}", "", 0, 0) for i in range(10)]
    first = pick_anchors(pool, 6, seed=3)
    assert first == pick_anchors(pool, 6, seed=3)
    assert len(first) == 6 and len({a.country for a in first}) == 6
    assert len(pick_anchors(pool, 9, seed=3)) == 9  # fills up once every country is used


@respx.mock
async def test_rate_limits_persist_across_clients(app_paths: AppPaths) -> None:
    cache = TTLCache.in_memory()
    route = respx.get("https://example.test/x").respond(429, headers={"Retry-After": "120"})
    first = HttpClient(cache, lambda _s: 60, retries=0)
    with pytest.raises(ProviderError):
        await first.get_json("demo", "https://example.test/x")
    second = HttpClient(cache, lambda _s: 60, retries=0)  # e.g. the next run of the program
    with pytest.raises(ProviderError, match="rate limited: retry in 2 min") as info:
        await second.get_json("demo", "https://example.test/x")
    assert info.value.status == 429 and route.call_count == 1
    respx.get("https://example.test/y").respond(json={"ok": True})
    assert (await second.get_json("other", "https://example.test/y"))[0] == {"ok": True}


class Hanging(EnrichmentProvider):
    name = "hanging"
    label = "Hanging"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        await asyncio.sleep(60)
        return EnrichmentResult()


async def test_provider_timeout_does_not_stop_the_run(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    from wepwawet.core import pipeline

    monkeypatch.setattr(pipeline, "PROVIDER_TIMEOUT_SECONDS", 0.05)
    run = await Pipeline(make_ctx(app_paths), [Hanging()], lambda _n, _d: None).run(
        RunReport(id="t", query="193.0.14.129")
    )
    status = run.targets[0].providers[0]
    assert run.status is RunStatus.DONE and status.status == "error" and "no answer within" in status.message


def test_serve_rejects_a_short_fixed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEPWAWET_TOKEN", "short")
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 2 and "at least 24" in result.output


def test_fusion_keeps_a_precise_region_centre() -> None:
    from tests.test_results import measured_report
    from wepwawet.core.fusion import PRECISE_REGION_KM, fuse

    report = measured_report()
    region = report.region  # type: ignore[attr-defined]
    centre = GeoPoint(lat=45.40, lon=9.30)  # a measured centre away from every city centre
    report.region = region.model_copy(update={"radius_km": PRECISE_REGION_KM - 1, "centroid": centre})  # type: ignore[attr-defined]
    estimate = fuse(report)  # type: ignore[arg-type]
    assert estimate is not None and estimate.location == centre and estimate.city == "Milan"


def test_init7_rule() -> None:
    from wepwawet.core.geohints import HintData, default_rules, extract_hints
    from wepwawet.data.gazetteer import City

    data = HintData.build({}, {}, (City("Winterthur", "CH", 47.5, 8.72, 114_000),), default_rules())
    (hint,) = extract_hints("s1-sec.win1.edge.init7.net", data)
    assert (hint.kind, hint.name) == ("operator", "Winterthur")
