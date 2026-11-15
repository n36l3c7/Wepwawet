"""What the API needs at run time: the event bus, the run manager and the provider context."""

import asyncio
import dataclasses
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from wepwawet.config import Config
from wepwawet.core.bulk import BulkJob, new_job, run_bulk
from wepwawet.core.campaign import Campaign
from wepwawet.core.context import (
    ALL_ENRICHMENT,
    ALL_MEASUREMENT,
    build_context,
    enabled_enrichment,
    enabled_measurement,
)
from wepwawet.core.events import EventBus
from wepwawet.core.history import History
from wepwawet.core.models import RunReport
from wepwawet.core.pipeline import Pipeline
from wepwawet.core.runs import RunManager
from wepwawet.core.secrets import SecretsError, load_secrets
from wepwawet.core.settings import SettingsError, SettingsStore
from wepwawet.paths import AppPaths, default_paths
from wepwawet.providers.base import EnrichmentProvider, ProviderContext
from wepwawet.providers.measurement.base import MeasurementProvider

log = logging.getLogger(__name__)


@dataclass
class Services:
    bus: EventBus
    runs: RunManager
    ctx: ProviderContext | None = None
    providers: list[EnrichmentProvider] | None = None
    measurement: list[MeasurementProvider] | None = None
    history: History | None = None
    bulk_jobs: dict[str, BulkJob] = field(default_factory=dict)
    bulk_tasks: dict[str, "asyncio.Task[BulkJob]"] = field(default_factory=dict)
    settings: SettingsStore | None = None

    def pipeline(self, bus: EventBus, max_tests: int | None = None) -> Pipeline:
        """A pipeline; ``max_tests`` caps the measurements (0 = passive only)."""
        if self.ctx is None:
            raise RuntimeError("no provider context")
        ctx = self.ctx
        if max_tests is not None:
            measurements = ctx.config.measurements.model_copy(update={"max_tests": max_tests})
            ctx = dataclasses.replace(ctx, config=ctx.config.model_copy(update={"measurements": measurements}))

        def log(level: str, source: str, message: str) -> None:
            bus.publish("log", {"level": level, "source": source, "message": message})

        campaign = Campaign(ctx, self.measurement or [], log) if max_tests != 0 else None
        return Pipeline(ctx, self.providers or [], bus.publish, campaign)

    def bulk_running(self) -> bool:
        return any(not task.done() for task in self.bulk_tasks.values())

    def start_bulk(self, queries: list[str], budget: int, passive: bool) -> BulkJob:
        if self.runs.active() or self.bulk_running():
            raise RuntimeError("Another run is in progress. Wait for it to finish or cancel it.")
        job = new_job(queries, budget, passive)
        self.bulk_jobs[job.id] = job

        async def run_one(query: str, max_tests: int) -> RunReport:
            run = RunReport(id=secrets.token_hex(6), query=query)
            self.bus.publish("log", {"level": "info", "source": "bulk", "message": f"Locating {query}"})
            result = await self.pipeline(self.bus, max_tests).run(run)
            if self.history is not None:
                self.history.save(result)
            return result

        def update(changed: BulkJob) -> None:
            self.bus.publish("bulk", changed.as_dict())

        limit = self.ctx.config.measurements.max_tests if self.ctx else 0
        self.bulk_tasks[job.id] = asyncio.create_task(run_bulk(job, run_one, limit, update), name=f"bulk-{job.id}")
        return job

    def provider_status(self) -> list[dict[str, Any]]:
        if self.ctx is None:
            return []
        rows = [
            {"name": p.name, "label": p.label, "kind": "enrichment", "reason": p.unavailable_reason(self.ctx)}
            for p in self.providers or []
        ]
        rows += [
            {"name": p.name, "label": p.label, "kind": "measurement", "reason": p.unavailable_reason(self.ctx)}
            for p in self.measurement or []
        ]
        return rows

    async def budgets(self) -> list[dict[str, Any]]:
        if self.ctx is None:
            return []
        rows = []
        for provider in self.measurement or []:
            if provider.unavailable_reason(self.ctx) is None:
                budget = await provider.budget(self.ctx)
                if budget is not None:
                    rows.append(dict(budget.__dict__))
        return rows

    def busy(self) -> bool:
        return bool(self.runs.active()) or self.bulk_running()

    def reconfigure(self, config: Config) -> None:
        """Use ``config`` and the current keys from now on; refused while a run is in progress."""
        if self.ctx is None or self.settings is None:
            raise SettingsError("settings are not available in this mode")
        if self.busy():
            raise RuntimeError("A run is in progress. Wait for it to finish or cancel it.")
        try:
            secrets_ = load_secrets(self.settings.secrets_file, self.settings.environ)
        except SecretsError as exc:
            raise SettingsError(str(exc)) from exc
        self.ctx = dataclasses.replace(self.ctx, config=config, secrets=secrets_)
        self.providers = enabled_enrichment(config)
        self.measurement = enabled_measurement(config)
        logging.getLogger().setLevel(config.logging.level)

    def all_sources(self) -> list[dict[str, str]]:
        """Every source, enabled or not, for the settings panel."""
        rows = [{"name": c.name, "label": c.label, "kind": "enrichment"} for c in ALL_ENRICHMENT]
        return rows + [{"name": c.name, "label": c.label, "kind": "measurement"} for c in ALL_MEASUREMENT]

    async def aclose(self) -> None:
        if self.ctx is not None:
            await self.ctx.http.aclose()


def default_services(config: Config, paths: AppPaths | None = None) -> Services:
    bus = EventBus()

    def emit_log(level: str, source: str, message: str) -> None:
        bus.publish("log", {"level": level, "source": source, "message": message})

    ctx = build_context(config, paths or default_paths(), log=emit_log)
    resolved = paths or default_paths()
    history = History.open(resolved.data_dir / "history.sqlite3")
    services = Services(
        bus=bus,
        runs=RunManager(bus, lambda b: services.pipeline(b), history=history),
        ctx=ctx,
        providers=enabled_enrichment(config),
        measurement=enabled_measurement(config),
        history=history,
        settings=SettingsStore(resolved),
    )
    services.runs.busy_elsewhere = services.bulk_running
    return services
