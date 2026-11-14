"""What the API needs at run time: the event bus, the run manager and the provider context."""

import logging
from dataclasses import dataclass
from typing import Any

from wepwawet.config import Config
from wepwawet.core.campaign import Campaign
from wepwawet.core.context import build_context, enabled_enrichment, enabled_measurement
from wepwawet.core.events import EventBus
from wepwawet.core.pipeline import Pipeline
from wepwawet.core.runs import RunManager
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

    async def aclose(self) -> None:
        if self.ctx is not None:
            await self.ctx.http.aclose()


def default_services(config: Config, paths: AppPaths | None = None) -> Services:
    bus = EventBus()

    def emit_log(level: str, source: str, message: str) -> None:
        bus.publish("log", {"level": level, "source": source, "message": message})

    ctx = build_context(config, paths or default_paths(), log=emit_log)
    providers = enabled_enrichment(config)
    measurement = enabled_measurement(config)

    def pipeline(b: EventBus) -> Pipeline:
        def log(level: str, source: str, message: str) -> None:
            b.publish("log", {"level": level, "source": source, "message": message})

        return Pipeline(ctx, providers, b.publish, Campaign(ctx, measurement, log))

    runs = RunManager(bus, pipeline)
    return Services(bus=bus, runs=runs, ctx=ctx, providers=providers, measurement=measurement)
