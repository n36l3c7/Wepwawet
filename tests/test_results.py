"""Fusion, history and diff, export, bulk mode."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_ctx, report_for
from tests.test_geo import MILAN, disks_for
from wepwawet.api.app import create_app
from wepwawet.api.services import Services
from wepwawet.config import Config
from wepwawet.core.bulk import new_job, parse_targets, run_bulk
from wepwawet.core.events import EventBus
from wepwawet.core.export import hops_csv, render, summary_csv, to_geojson, to_kml, to_markdown
from wepwawet.core.fusion import fuse
from wepwawet.core.history import History, diff_runs
from wepwawet.core.models import (
    AnycastInfo,
    CandidateCity,
    Estimate,
    Evidence,
    EvidenceKind,
    GeoPoint,
    HopReport,
    NetworkClass,
    NetworkInfo,
    Probe,
    ProbeResult,
    RunReport,
    RunStatus,
    TracerouteReport,
)
from wepwawet.core.multilateration import region_from
from wepwawet.core.runs import RunManager
from wepwawet.paths import AppPaths


def soft(source: str, label: str, city: str, lat: float, lon: float, radius: float | None = 30) -> Evidence:
    return Evidence(
        source=source,
        kind=EvidenceKind.SOFT,
        label=label,
        summary=f"{label}: {city}",
        city=city,
        country="IT",
        point=GeoPoint(lat=lat, lon=lon),
        radius_km=radius,
    )


def measured_report() -> "object":
    report = report_for("193.0.14.129")
    region = region_from(disks_for(MILAN, inflation=1.15, extra_km=30))
    assert region is not None
    region = region.model_copy(
        update={
            "candidates": (
                CandidateCity(name="Milan", country="IT", point=GeoPoint(lat=45.464, lon=9.19), population=1_371_498),
                CandidateCity(name="Monza", country="IT", point=GeoPoint(lat=45.58, lon=9.27), population=122_671),
            )
        }
    )
    report.region = region
    probe = Probe(provider="g", probe_id="1", location=GeoPoint(lat=45.47, lon=9.18), city="Milan", country="IT")
    report.probes = [ProbeResult(probe=probe, phase="zoom", min_rtt=1.2, max_distance_km=120)]
    report.evidence = [
        soft("geoip", "DB-IP Lite", "Milan", 45.46, 9.19, 50),
        soft("ipmap", "RIPE IPmap", "Milan", 45.47, 9.18, None),
        soft("geoip", "GeoLite2", "Rome", 41.90, 12.50, 100),
    ]
    return report


def test_fusion_picks_the_agreeing_city_and_drops_the_impossible_answer() -> None:
    report = measured_report()
    estimate = fuse(report)  # type: ignore[arg-type]
    assert estimate is not None and estimate.city == "Milan"
    assert estimate.uncertainty_km < report.region.radius_km  # type: ignore[attr-defined]
    dropped = [e for e in report.evidence if e.kind is EvidenceKind.NEGATIVE]  # type: ignore[attr-defined]
    assert len(dropped) == 1 and "GeoLite2" in dropped[0].summary
    text = " ".join(estimate.explanation)
    assert "region" in text and "DB-IP Lite" in text and "rule out" in text
    assert 40 <= estimate.confidence <= 100


def test_fusion_penalties_for_anycast_and_mobile() -> None:
    plain = fuse(measured_report())  # type: ignore[arg-type]
    report = measured_report()
    report.anycast = AnycastInfo(detected=True, violations=3, summary="Anycast")  # type: ignore[attr-defined]
    report.classification = [NetworkClass.MOBILE]  # type: ignore[attr-defined]
    worse = fuse(report)  # type: ignore[arg-type]
    assert plain is not None and worse is not None
    assert worse.confidence <= 30 - 15 and worse.anycast and worse.confidence < plain.confidence


def test_fusion_without_measurements_is_humble() -> None:
    report = report_for("193.0.14.129")
    report.evidence = [
        soft("geoip", "DB-IP Lite", "Milan", 45.46, 9.19, 50),
        soft("ipmap", "RIPE IPmap", "Milan", 45.47, 9.18, None),
    ]
    estimate = fuse(report)
    assert estimate is not None and estimate.confidence <= 38
    assert "passive sources only" in estimate.explanation[0]


def test_fusion_with_nothing() -> None:
    assert fuse(report_for("193.0.14.129")) is None


def finished_run(run_id: str, city: str = "Milan", lat: float = 45.46, path: tuple[str, ...] = ("a", "b")) -> RunReport:
    report = report_for("193.0.14.129")
    report.network = NetworkInfo(prefix="193.0.14.0/24", asn=25152)
    report.estimate = Estimate(
        location=GeoPoint(lat=lat, lon=9.19),
        uncertainty_km=30,
        confidence=70,
        city=city,
        country="IT",
        explanation=("Because.",),
    )
    report.evidence = [soft("geoip", "DB-IP Lite", city, lat, 9.19)]
    probe = Probe(provider="g", probe_id="fra", location=GeoPoint(lat=50.1, lon=8.7), city="Frankfurt")
    report.traceroutes = [
        TracerouteReport(
            probe=probe,
            protocol="ICMP",
            reached=True,
            hops=tuple(HopReport(ttl=i + 1, address=a, min_rtt=float(i)) for i, a in enumerate(path)),
        )
    ]
    # Distinct start times (a later id starts later), so the history order never depends on the clock.
    started = datetime(2026, 11, 1, 12, 0, tzinfo=UTC) + timedelta(
        minutes=int(run_id[1:]) if run_id[1:].isdigit() else 0
    )
    return RunReport(id=run_id, query="k.example", status=RunStatus.DONE, targets=[report], started_at=started)


def test_history_round_trip_and_listing() -> None:
    history = History.in_memory()
    history.save(finished_run("r1"))
    history.save(finished_run("r2", city="Monza"))
    assert [e.id for e in history.list("k.example")] == ["r2", "r1"]
    assert history.list("other") == []
    again = history.get("r1")
    assert again is not None and again.targets[0].estimate is not None
    assert again.targets[0].estimate.city == "Milan"
    assert history.delete("r1") and history.get("r1") is None


def test_diff_reports_what_moved() -> None:
    old = finished_run("r1")
    new = finished_run("r2", city="Monza", lat=45.58, path=("a", "c"))
    changes = {c.field: c for c in diff_runs(old, new)}
    assert "moved 13 km" in changes["estimate"].note
    assert changes["DB-IP Lite"].before == "Milan, IT" and changes["DB-IP Lite"].after == "Monza, IT"
    assert changes["path from Frankfurt"].note == "differs from hop 2"
    assert diff_runs(old, finished_run("r3")) == []  # the same result twice: nothing to report


def test_exports() -> None:
    run = finished_run("r1")
    run.targets[0].region = region_from(disks_for(MILAN))
    geo = json.loads(to_geojson(run))
    kinds = {f["properties"]["kind"] for f in geo["features"]}
    assert kinds >= {"estimate", "uncertainty", "region", "evidence", "path"}
    ElementTree.fromstring(to_kml(run))  # noqa: S314 - our own output, checked for well-formedness
    markdown = to_markdown(run)
    assert "# Wepwawet report: k.example" in markdown and "**Estimate:** Milan, IT" in markdown
    csv_text = hops_csv(run)
    assert csv_text.splitlines()[0].startswith("target,probe_city") and len(csv_text.splitlines()) == 3
    assert json.loads(render(run, "json"))["id"] == "r1"
    with pytest.raises(ValueError, match="Unknown format"):
        render(run, "pdf")
    summary = summary_csv([run, RunReport(id="x", query="bad", status=RunStatus.FAILED, error="private")])
    lines = summary.splitlines()
    assert lines[1].startswith("k.example,193.0.14.129,done,Milan,IT") and lines[2].endswith("private")


def test_parse_targets_limit() -> None:
    assert parse_targets("a\nb # c\n\na") == ["a", "b"]
    with pytest.raises(ValueError, match="limit"):
        parse_targets("\n".join(f"10.0.0.{i}-{j}" for i in range(40) for j in range(30)))


async def test_bulk_shares_the_budget() -> None:
    allowed: list[int] = []

    async def run_one(query: str, max_tests: int) -> RunReport:
        allowed.append(max_tests)
        run = finished_run(query)
        run.targets[0].tests_used = 70
        return run

    job = new_job(["a", "b", "c"], budget=150, passive=False)
    updates: list[str] = []
    await run_bulk(job, run_one, per_target_limit=100, on_update=lambda j: updates.append(j.status))
    assert allowed == [100, 80, 10]
    assert job.status == "done" and job.tests_used == 210 and all(i.status == "done" for i in job.items)
    assert job.items[0].summary["city"] == "Milan"


async def test_bulk_passive_and_cancel() -> None:
    allowed: list[int] = []

    async def slow(query: str, max_tests: int) -> RunReport:
        allowed.append(max_tests)
        await asyncio.sleep(10)
        return finished_run(query)

    job = new_job(["a", "b"], budget=100, passive=True)
    task = asyncio.create_task(run_bulk(job, slow, 50))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert allowed == [0] and job.status == "cancelled"
    assert [i.status for i in job.items] == ["cancelled", "cancelled"]


TOKEN = "t" * 24


def test_history_export_and_bulk_routes(app_paths: AppPaths, tmp_path: Path) -> None:
    bus = EventBus()
    history = History.in_memory()
    history.save(finished_run("r1"))
    history.save(finished_run("r2", city="Monza", lat=45.58))
    ctx = make_ctx(app_paths, config=Config.model_validate({"measurements": {"enabled": False}}))
    services = Services(
        bus=bus,
        runs=RunManager(bus, lambda b: services.pipeline(b), history=history),
        ctx=ctx,
        providers=[],
        measurement=[],
        history=history,
    )
    app = create_app(Config(), TOKEN, static_dir=tmp_path, services=services)
    with TestClient(app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        listed = client.get("/api/history", params={"query": "k.example"}).json()
        assert [e["id"] for e in listed] == ["r2", "r1"]
        diff = client.get("/api/history/diff", params={"a": "r1", "b": "r2"}).json()
        assert any(c["field"] == "estimate" for c in diff)
        kml = client.get("/api/runs/r1/export", params={"format": "kml"})
        assert kml.status_code == 200 and "attachment" in kml.headers["content-disposition"]
        assert kml.headers["content-type"].startswith("application/vnd.google-earth.kml+xml")
        assert client.get("/api/runs/r1/export", params={"format": "pdf"}).status_code == 400
        assert client.get("/api/runs/zzz/export").status_code == 404
        started = client.post("/api/bulk", json={"queries": ["10.0.0.1", "192.168.1.1"], "max_tests": 0})
        assert started.status_code == 202
        job_id = started.json()["id"]
        deadline = time.monotonic() + 10
        job = client.get(f"/api/bulk/{job_id}").json()
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(f"/api/bulk/{job_id}").json()
        assert job["status"] == "done" and [i["status"] for i in job["items"]] == ["failed", "failed"]
        summary = client.get(f"/api/bulk/{job_id}/export", params={"format": "csv"})
        assert summary.status_code == 200 and "private" in summary.text
        assert client.post("/api/bulk", json={"queries": ["# only a comment"]}).status_code == 422
        assert client.delete("/api/history/r1").json() == {"deleted": True}


def test_unreadable_history_run_is_a_clear_error() -> None:
    from sqlalchemy import update

    from wepwawet.core.history import HistoryError, _runs

    history = History.in_memory()
    history.save(finished_run("r1"))
    with history._engine.begin() as conn:  # noqa: SLF001 - simulate a run written by another version
        conn.execute(update(_runs).values(report='{"id": "r1", "query": "q", "from_the_future": 1}'))
    with pytest.raises(HistoryError, match="different version"):
        history.get("r1")


def test_facilities_are_counted_not_listed() -> None:
    report = measured_report()
    for i in range(6):
        report.evidence.append(soft("peeringdb", f"DC{i}", "Milan", 45.46, 9.19, None))  # type: ignore[attr-defined]
    estimate = fuse(report)  # type: ignore[arg-type]
    assert estimate is not None
    text = " ".join(estimate.explanation)
    assert "6 data centres of the origin network" in text and "DC3" not in text


def test_far_facilities_are_not_ruled_out_answers() -> None:
    report = measured_report()
    report.evidence.append(soft("peeringdb", "NIKHEF", "Amsterdam", 52.36, 4.95, None))  # type: ignore[attr-defined]
    estimate = fuse(report)  # type: ignore[arg-type]
    assert estimate is not None and "NIKHEF" not in " ".join(estimate.explanation)
    assert not any("NIKHEF" in e.summary for e in report.evidence if e.kind is EvidenceKind.NEGATIVE)  # type: ignore[attr-defined]
