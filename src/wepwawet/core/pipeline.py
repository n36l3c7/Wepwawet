"""The pipeline: resolve the input, then run every phase for every target, emitting progress events.

Phases: passive enrichment (providers by stage), the measurement campaign (pings, multilateration,
traceroutes), then fusion of all the evidence into one estimate.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from wepwawet.core.campaign import Campaign
from wepwawet.core.fusion import fuse
from wepwawet.core.http import ProviderError
from wepwawet.core.models import NetworkInfo, ProviderStatus, RunReport, RunStatus, TargetReport
from wepwawet.core.targets import TargetError, resolve_input
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]
# A source that takes longer is reported as failed; the run goes on with the others.
PROVIDER_TIMEOUT_SECONDS = 90.0
ANYCAST_CAVEAT = (
    "Anycast: the address answers from several places at once. The region shown is the site nearest to "
    "the fastest probe, not a single location."
)


def merge_network(current: NetworkInfo | None, new: NetworkInfo | None) -> NetworkInfo | None:
    """Fill the missing fields of ``current`` with ``new`` (first source to answer wins)."""
    if new is None:
        return current
    if current is None:
        return new
    updates = {k: v for k, v in new.model_dump().items() if v is not None and getattr(current, k) is None}
    return current.model_copy(update=updates)


class Pipeline:
    def __init__(
        self,
        ctx: ProviderContext,
        providers: Sequence[EnrichmentProvider],
        emit: Emit,
        campaign: Campaign | None = None,
    ) -> None:
        self._ctx = ctx
        self._providers = list(providers)
        self._emit = emit
        self._campaign = campaign

    def _log(self, run: RunReport, level: str, source: str, message: str) -> None:
        self._emit("log", {"run": run.id, "level": level, "source": source, "message": message})

    async def run(self, run: RunReport) -> RunReport:
        started = time.monotonic()
        self._emit("run", run.model_dump(mode="json"))
        try:
            resolved = await resolve_input(run.query, self._ctx.dns.resolve_host)
        except TargetError as exc:
            run.status, run.error = RunStatus.FAILED, str(exc)
            run.finished_at = datetime.now(UTC)
            self._log(run, "error", "input", str(exc))
            self._emit("run", run.model_dump(mode="json"))
            return run

        run.hostname = resolved.hostname
        run.skipped = list(resolved.skipped)
        run.targets = [TargetReport(target=t) for t in resolved.targets]
        if resolved.hostname:
            addresses = ", ".join(str(t.address) for t in resolved.targets)
            self._log(run, "info", "dns", f"{resolved.hostname} resolves to {addresses}")
        for address, reason in resolved.skipped:
            self._log(run, "info", "dns", f"Skipping {address}: {reason}")
        self._emit("run", run.model_dump(mode="json"))

        try:
            for index, report in enumerate(run.targets):
                await self._enrich(run, index, report)
                passive = fuse(report)
                report.record_stage("passive", passive)
                report.estimate = passive
                if self._campaign is not None:
                    await self._measure(run, index, report, self._campaign)
                self._finalise(run, index, report)
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            run.finished_at = datetime.now(UTC)
            self._emit("run", run.model_dump(mode="json"))
            raise

        run.status = RunStatus.DONE
        run.finished_at = datetime.now(UTC)
        self._log(run, "ok", "pipeline", f"Run finished in {time.monotonic() - started:.1f} s")
        self._emit("run", run.model_dump(mode="json"))
        return run

    async def _enrich(self, run: RunReport, index: int, report: TargetReport) -> None:
        for stage in sorted({p.stage for p in self._providers}):
            providers = [p for p in self._providers if p.stage == stage]
            results = await asyncio.gather(*(self._call(run, report, p) for p in providers))
            for provider, (status, result) in zip(providers, results, strict=True):
                report.providers.append(status)
                if result is None:
                    continue
                report.evidence.extend(result.evidence)
                report.network = merge_network(report.network, result.network)
                for cls in result.classes:
                    if cls not in report.classification:
                        report.classification.append(cls)
                for caveat in result.caveats:
                    if caveat not in report.caveats:
                        report.caveats.append(caveat)
                self._log(run, "info", provider.name, status.message or "ok")
            self._emit("target", {"run": run.id, "index": index, "report": report.model_dump(mode="json")})

    def _finalise(self, run: RunReport, index: int, report: TargetReport) -> None:
        """Fuse every piece of evidence into the final estimate."""
        estimate = fuse(report)
        report.record_stage("final", estimate)
        if estimate is not None:
            report.estimate = estimate
            where = ", ".join(p for p in (estimate.city, estimate.country) if p) or "the region centre"
            self._log(
                run,
                "ok",
                "fusion",
                f"{report.target.address}: {where} ±{estimate.uncertainty_km:.0f} km, confidence {estimate.confidence}",
            )
        self._emit("target", {"run": run.id, "index": index, "report": report.model_dump(mode="json")})

    async def _measure(self, run: RunReport, index: int, report: TargetReport, campaign: Campaign) -> None:
        def progress() -> None:
            self._emit("target", {"run": run.id, "index": index, "report": report.model_dump(mode="json")})

        await campaign.run(report, progress)
        if report.anycast and report.anycast.detected and ANYCAST_CAVEAT not in report.caveats:
            report.caveats.append(ANYCAST_CAVEAT)
        progress()

    async def _call(
        self, run: RunReport, report: TargetReport, provider: EnrichmentProvider
    ) -> tuple[ProviderStatus, EnrichmentResult | None]:
        reason = provider.unavailable_reason(self._ctx)
        if reason is not None:
            return ProviderStatus(provider=provider.name, status="skipped", message=reason), None
        started = time.monotonic()
        try:
            try:
                result = await asyncio.wait_for(provider.enrich(report, self._ctx), PROVIDER_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise ProviderError(provider.name, f"no answer within {PROVIDER_TIMEOUT_SECONDS:.0f} s") from exc
        except ProviderError as exc:
            elapsed = (time.monotonic() - started) * 1000
            self._log(run, "error", provider.name, exc.message)
            return ProviderStatus(
                provider=provider.name, status="error", message=exc.message, duration_ms=elapsed
            ), None
        except Exception as exc:  # a bug in one provider must not stop the others; it is logged in full
            log.exception("Provider %s crashed", provider.name)
            elapsed = (time.monotonic() - started) * 1000
            message = f"internal error ({exc.__class__.__name__}); see the log file"
            self._log(run, "error", provider.name, message)
            return ProviderStatus(provider=provider.name, status="error", message=message, duration_ms=elapsed), None
        elapsed = (time.monotonic() - started) * 1000
        found = len(result.evidence)
        status = ProviderStatus(
            provider=provider.name,
            status="ok" if found or result.network else "empty",
            message=f"{found} finding{'s' if found != 1 else ''}" + (" (cached)" if result.cached else ""),
            duration_ms=round(elapsed, 1),
            cached=result.cached,
        )
        return status, result
