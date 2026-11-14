"""What the API needs at run time: the event bus, the run manager and the provider context."""

import logging
from dataclasses import dataclass
from typing import Any

from wepwawet.config import Config
from wepwawet.core.context import build_context, enabled_enrichment
from wepwawet.core.events import EventBus
from wepwawet.core.pipeline import Pipeline
from wepwawet.core.runs import RunManager
from wepwawet.paths import AppPaths, default_paths
from wepwawet.providers.base import EnrichmentProvider, ProviderContext

log = logging.getLogger(__name__)


@dataclass
class Services:
    bus: EventBus
    runs: RunManager
    ctx: ProviderContext | None = None
    providers: list[EnrichmentProvider] | None = None

    def provider_status(self) -> list[dict[str, Any]]:
        if self.ctx is None or self.providers is None:
            return []
        return [
            {
                "name": p.name,
                "label": p.label,
                "kind": "enrichment",
                "reason": p.unavailable_reason(self.ctx),
            }
            for p in self.providers
        ]

    async def aclose(self) -> None:
        if self.ctx is not None:
            await self.ctx.http.aclose()


def default_services(config: Config, paths: AppPaths | None = None) -> Services:
    bus = EventBus()

    def emit_log(level: str, source: str, message: str) -> None:
        bus.publish("log", {"level": level, "source": source, "message": message})

    ctx = build_context(config, paths or default_paths(), log=emit_log)
    providers = enabled_enrichment(config)
    runs = RunManager(bus, lambda b: Pipeline(ctx, providers, b.publish))
    return Services(bus=bus, runs=runs, ctx=ctx, providers=providers)
