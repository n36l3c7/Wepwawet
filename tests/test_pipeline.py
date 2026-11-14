import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeDns, make_ctx
from wepwawet.api.app import create_app
from wepwawet.api.services import Services
from wepwawet.config import Config
from wepwawet.core.events import EventBus
from wepwawet.core.http import ProviderError
from wepwawet.core.models import (
    Evidence,
    EvidenceKind,
    NetworkClass,
    NetworkInfo,
    RunReport,
    RunStatus,
    TargetReport,
)
from wepwawet.core.pipeline import Pipeline, merge_network
from wepwawet.core.runs import RunManager
from wepwawet.paths import AppPaths
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext


class Routing(EnrichmentProvider):
    name = "routing"
    label = "Routing"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        return EnrichmentResult(
            network=NetworkInfo(asn=64500, as_name="EXAMPLE"),
            evidence=[Evidence(source=self.name, kind=EvidenceKind.INFO, summary="AS64500")],
        )


class NeedsAsn(EnrichmentProvider):
    name = "needs-asn"
    label = "Needs ASN"
    stage = 2
    seen: ClassVar[list[int | None]] = []

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        NeedsAsn.seen.append(report.network.asn if report.network else None)
        return EnrichmentResult(classes=[NetworkClass.RESIDENTIAL], caveats=["Residential"])


class Failing(EnrichmentProvider):
    name = "failing"
    label = "Failing"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        raise ProviderError(self.name, "HTTP 503")


class Crashing(EnrichmentProvider):
    name = "crashing"
    label = "Crashing"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        raise KeyError("bug")


class Keyed(EnrichmentProvider):
    name = "keyed"
    label = "Keyed"
    required_secrets = ("shodan",)

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        raise AssertionError("must not run without a key")


class Slow(EnrichmentProvider):
    name = "slow"
    label = "Slow"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        await asyncio.sleep(30)
        return EnrichmentResult()


def collect() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []
    return events, lambda name, data: events.append((name, data))


async def test_pipeline_runs_stages_in_order_and_isolates_failures(app_paths: AppPaths) -> None:
    NeedsAsn.seen.clear()
    events, emit = collect()
    ctx = make_ctx(app_paths, dns=FakeDns(hosts={"k.example.net": ["193.0.14.129", "10.0.0.1"]}))
    providers = [NeedsAsn(), Routing(), Failing(), Crashing(), Keyed()]
    run = await Pipeline(ctx, providers, emit).run(RunReport(id="r1", query="k.example.net"))

    assert run.status is RunStatus.DONE and run.hostname == "k.example.net"
    assert run.skipped == [("10.0.0.1", "a private address (RFC 1918)")]
    target = run.targets[0]
    assert NeedsAsn.seen == [64500]  # stage 2 saw what stage 1 found
    statuses = {s.provider: s.status for s in target.providers}
    assert statuses == {
        "routing": "ok",
        "failing": "error",
        "crashing": "error",
        "keyed": "skipped",
        "needs-asn": "empty",
    }
    assert target.classification == [NetworkClass.RESIDENTIAL] and target.caveats == ["Residential"]
    names = [name for name, _ in events]
    assert names[0] == "run" and names[-1] == "run" and "target" in names
    assert any(d.get("source") == "crashing" and "internal error" in d["message"] for n, d in events if n == "log")


async def test_pipeline_input_error(app_paths: AppPaths) -> None:
    events, emit = collect()
    run = await Pipeline(make_ctx(app_paths), [Routing()], emit).run(RunReport(id="r2", query="192.168.1.1"))
    assert run.status is RunStatus.FAILED and "private" in (run.error or "")
    assert run.targets == []


def test_merge_network_keeps_first_answer() -> None:
    a = NetworkInfo(asn=1, as_name=None)
    b = NetworkInfo(asn=2, as_name="B", prefix="1.0.0.0/24")
    assert merge_network(a, b) == NetworkInfo(asn=1, as_name="B", prefix="1.0.0.0/24")
    assert merge_network(None, b) == b
    assert merge_network(a, None) == a


def manager(app_paths: AppPaths, providers: list[EnrichmentProvider]) -> tuple[RunManager, EventBus]:
    bus = EventBus()
    ctx = make_ctx(app_paths)
    return RunManager(bus, lambda b: Pipeline(ctx, providers, b.publish)), bus


async def test_run_manager_lifecycle(app_paths: AppPaths) -> None:
    runs, bus = manager(app_paths, [Routing()])
    async with bus.subscribe() as queue:
        run = runs.start("193.0.14.129")
        done = await runs.wait(run.id)
        assert done is not None and done.status is RunStatus.DONE
        assert runs.list()[0].id == run.id
        names = []
        while not queue.empty():
            names.append(queue.get_nowait().name)
        assert names[0] == "run" and "target" in names


async def test_run_manager_allows_one_run_and_cancels(app_paths: AppPaths) -> None:
    runs, _bus = manager(app_paths, [Slow()])
    run = runs.start("193.0.14.129")
    await asyncio.sleep(0.05)
    with pytest.raises(RuntimeError, match="in progress"):
        runs.start("1.1.1.1")
    assert runs.cancel(run.id)
    finished = await runs.wait(run.id)
    assert finished is not None and finished.status is RunStatus.CANCELLED
    assert not runs.cancel(run.id)


async def test_event_bus_drops_oldest_when_full() -> None:
    bus = EventBus(queue_size=2)
    async with bus.subscribe() as queue:
        for i in range(3):
            bus.publish("e", {"i": i})
        assert [queue.get_nowait().data["i"] for _ in range(2)] == [1, 2]
    assert bus.subscriber_count == 0


TOKEN = "t" * 20


def api_client(app_paths: AppPaths, providers: list[EnrichmentProvider], static: Path) -> TestClient:
    runs, bus = manager(app_paths, providers)
    ctx = make_ctx(app_paths)
    services = Services(bus=bus, runs=runs, ctx=ctx, providers=providers)
    app = create_app(
        Config(), TOKEN, static_dir=static, services=services, sse_heartbeat_seconds=0.05, sse_max_heartbeats=2
    )
    return TestClient(app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {TOKEN}"})


def test_api_runs(app_paths: AppPaths, tmp_path: Path) -> None:
    with api_client(app_paths, [Routing(), Keyed()], tmp_path) as client:
        providers = client.get("/api/providers").json()
        assert {p["name"]: p["reason"] for p in providers}["keyed"].startswith("no API key")
        started = client.post("/api/runs", json={"query": "193.0.14.129"})
        assert started.status_code == 202
        run_id = started.json()["id"]
        for _ in range(50):
            body = client.get(f"/api/runs/{run_id}").json()
            if body["status"] != "running":
                break
        assert body["status"] == "done"
        assert body["targets"][0]["network"]["asn"] == 64500
        assert client.get("/api/runs").json()[0]["id"] == run_id
        assert client.get("/api/runs/nope").status_code == 404
        assert client.delete(f"/api/runs/{run_id}").json() == {"cancelled": False}


def test_api_rejects_empty_query(app_paths: AppPaths, tmp_path: Path) -> None:
    with api_client(app_paths, [Routing()], tmp_path) as client:
        assert client.post("/api/runs", json={"query": ""}).status_code == 422


def test_api_streams_run_events(app_paths: AppPaths, tmp_path: Path) -> None:
    with api_client(app_paths, [Routing()], tmp_path) as client:
        services: Services = client.app.state.services  # type: ignore[attr-defined]
        services.bus.publish("log", {"level": "info", "source": "x", "message": "queued"})
        with client.stream("GET", "/api/events") as response:
            body, sent = "", False
            for chunk in response.iter_text():
                body += chunk
                if "event: ready" in body and not sent:
                    services.bus.publish("log", {"level": "info", "source": "x", "message": "hello"})
                    sent = True
        assert "event: ready" in body and '"message":"hello"' in body
