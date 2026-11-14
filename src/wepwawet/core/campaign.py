"""The ping campaign: a global sweep, then a regional zoom, then multilateration.

1. Sweep: probes spread over every continent give a coarse idea (the fastest probe is near).
2. Zoom: probes in the countries of the fastest probes and of the passive answers give tight
   constraints: a probe 2 ms away bounds the target to about 200 km.
3. Optional CBG calibration: the zoom probes also ping RIPE Atlas anchors of known position,
   and each probe gets its own delay-to-distance line.
4. Multilateration intersects the disks; disjoint disks far apart mean anycast.

Each phase checks the test budget first and is skipped, with a log line, when it would exceed it.
"""

import asyncio
import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from wepwawet.core.geo import Bestline, fit_bestline, haversine, sol_max_distance_km
from wepwawet.core.http import ProviderError
from wepwawet.core.models import (
    Estimate,
    Evidence,
    EvidenceKind,
    ProbeResult,
    Region,
    Target,
    TargetReport,
)
from wepwawet.core.multilateration import (
    area_confidence,
    consistent_subset,
    detect_anycast,
    disks_from,
    region_and_cells,
)
from wepwawet.data.gazetteer import FILE_NAME, candidate_cities, load_cities
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import LocationSpec, MeasurementProvider, PingBatch

# Share of the sweep per continent, roughly where probes and networks are.
SWEEP_SHARES = (("EU", 0.27), ("NA", 0.25), ("AS", 0.2), ("SA", 0.1), ("OC", 0.1), ("AF", 0.08))
ANCHORS_URL = "https://atlas.ripe.net/api/v2/anchors/"
MAX_ZOOM_COUNTRIES = 4
MIN_PHASE_TESTS = 5  # below this a phase adds too little to be worth the tests

Log = Callable[[str, str, str], None]
Progress = Callable[[], None]


def sweep_locations(total: int) -> list[LocationSpec]:
    specs = [LocationSpec(continent=c, limit=max(1, round(total * share))) for c, share in SWEEP_SHARES]
    return [s for s in specs if s.limit > 0]


def zoom_countries(report: TargetReport, best: Sequence[ProbeResult]) -> list[str]:
    """The fastest probe's country, then the countries of the passive answers, then the other fast probes.

    The sweep has few probes per country, so the fastest one is often a neighbour of the
    target's country; the passive answers usually name it.
    """
    ordered: list[str] = []

    def add(code: str | None) -> None:
        if code and len(code) == 2 and code.upper() not in ordered:
            ordered.append(code.upper())

    if best:
        add(best[0].probe.country)
    for evidence in report.evidence:
        if evidence.point:
            add(evidence.country)
    for result in best[1:]:
        add(result.probe.country)
    return ordered[:MAX_ZOOM_COUNTRIES]


def zoom_locations(countries: Sequence[str], total: int) -> list[LocationSpec]:
    """Split ``total`` probes over ``countries``, more for the first ones (weights 4:3:2:1)."""
    if not countries or total <= 0:
        return []
    weights = [MAX_ZOOM_COUNTRIES - i for i in range(len(countries))]
    shares = [max(1, round(total * w / sum(weights))) for w in weights]
    return [LocationSpec(country=c, limit=n) for c, n in zip(countries, shares, strict=True)]


def merge_results(existing: list[ProbeResult], new: Sequence[ProbeResult]) -> list[ProbeResult]:
    """Keep one result per probe: the one with the smaller minimum RTT."""
    by_key = {f"{r.probe.provider}:{r.probe.probe_id}": r for r in existing}
    for result in new:
        key = f"{result.probe.provider}:{result.probe.probe_id}"
        old = by_key.get(key)
        if old is None or (result.min_rtt is not None and (old.min_rtt is None or result.min_rtt < old.min_rtt)):
            by_key[key] = result
    return sorted(by_key.values(), key=lambda r: (r.min_rtt is None, r.min_rtt or 0))


@dataclass
class CampaignState:
    tests_used: int = 0
    limit: int = 0  # most tests this target may use: max_tests, or what the provider has left
    tcp_port: int | None = None  # set when the target only answers TCP
    reuse: dict[str, str] = field(default_factory=dict)  # provider -> reuse key of the zoom measurement


class Campaign:
    def __init__(self, ctx: ProviderContext, providers: Sequence[MeasurementProvider], log: Log) -> None:
        self._ctx = ctx
        self._providers = list(providers)
        self._log = log

    def available(self) -> list[MeasurementProvider]:
        return [p for p in self._providers if p.unavailable_reason(self._ctx) is None]

    def _to_results(
        self, batch: PingBatch, phase: str, bestlines: dict[str, Bestline] | None = None
    ) -> list[ProbeResult]:
        speed = self._ctx.config.measurements.speed_km_per_ms
        results = []
        for ping in batch.results:
            key = f"{ping.probe.provider}:{ping.probe.probe_id}"
            line = (bestlines or {}).get(key)
            distance = None
            if ping.min_rtt is not None:
                distance = line.max_distance_km(ping.min_rtt) if line else sol_max_distance_km(ping.min_rtt, speed)
            results.append(
                ProbeResult(
                    probe=ping.probe,
                    phase=phase,
                    min_rtt=ping.min_rtt,
                    avg_rtt=ping.avg_rtt,
                    loss=ping.loss,
                    packets=ping.packets,
                    max_distance_km=round(distance, 1) if distance is not None else None,
                    calibrated=line is not None,
                )
            )
        return results

    async def _ping(
        self, provider: MeasurementProvider, target: Target, where: Sequence[LocationSpec] | str, state: CampaignState
    ) -> PingBatch | None:
        cfg = self._ctx.config.measurements
        try:
            batch = await provider.ping(target, where, cfg.packets, self._ctx, tcp_port=state.tcp_port)
        except ProviderError as exc:
            wait = f" (retry in {exc.retry_after:.0f} s)" if exc.retry_after else ""
            self._log("error", provider.name, f"{exc.message}{wait}")
            return None
        state.tests_used += batch.tests_used
        return batch

    async def run(self, report: TargetReport, progress: Progress) -> None:
        cfg = self._ctx.config.measurements
        usable = self.available()
        if not cfg.enabled or not usable:
            reason = "disabled in the configuration" if not cfg.enabled else "no measurement source is available"
            self._log("info", "measure", f"No measurements: {reason}")
            return
        target = report.target
        state = CampaignState()
        selectable = [p for p in usable if p.selectable]
        fixed = [p for p in usable if not p.selectable]
        primary = selectable[0] if selectable else None
        state.limit = cfg.max_tests
        if primary is not None:
            budget = await primary.budget(self._ctx)
            if budget is not None and budget.unit == "tests/hour" and budget.remaining is not None:
                state.limit = min(state.limit, budget.remaining)
                if budget.remaining < cfg.sweep_probes:
                    wait = f"; resets in {budget.reset_seconds // 60} min" if budget.reset_seconds else ""
                    self._log("info", primary.name, f"Only {budget.remaining} tests left this hour{wait}")

        # 1. Sweep (plus every fixed vantage point, which costs nothing).
        jobs = [self._ping(p, target, [], state) for p in fixed]
        sweep = self._allowance(state, min(cfg.sweep_probes, primary.max_probes) if primary else 0)
        if primary and sweep:
            self._log("info", primary.name, f"Global sweep: {sweep} probes on every continent")
            jobs.append(self._ping(primary, target, sweep_locations(sweep), state))
        for batch in await asyncio.gather(*jobs):
            if batch:
                phase = "sweep" if batch.provider == (primary.name if primary else None) else batch.provider
                report.probes = merge_results(report.probes, self._to_results(batch, phase))
        self._finish_geometry(report)
        progress()
        answered = [r for r in report.probes if r.min_rtt is not None]
        if answered:
            best = answered[0]
            where = ", ".join(p for p in (best.probe.city, best.probe.country) if p)
            self._log("info", "measure", f"Fastest so far: {where} {best.min_rtt:.1f} ms")
        elif report.probes and not await self._tcp_sweep(report, primary, state, progress):
            return
        answered = [r for r in report.probes if r.min_rtt is not None]

        # 2. Zoom near the fastest probes and the passive answers.
        countries = zoom_countries(report, answered[:5])
        zoom = self._allowance(state, min(cfg.zoom_probes, primary.max_probes) if primary and countries else 0)
        if primary and zoom:
            self._log("info", primary.name, f"Regional zoom: {zoom} probes in {', '.join(countries)}")
            batch = await self._ping(primary, target, zoom_locations(countries, zoom), state)
            if batch:
                if batch.reuse_key:
                    state.reuse[primary.name] = batch.reuse_key
                report.probes = merge_results(report.probes, self._to_results(batch, "zoom"))
                self._finish_geometry(report)
                progress()

        # 3. Optional calibration of the zoom probes against RIPE Atlas anchors.
        if cfg.calibrate and primary and primary.name in state.reuse:
            await self._calibrate(report, primary, state, countries)
            self._finish_geometry(report)
            progress()
        self._log("info", "measure", f"Measurements used {state.tests_used} tests")

    async def _tcp_sweep(
        self, report: TargetReport, primary: MeasurementProvider | None, state: CampaignState, progress: Progress
    ) -> bool:
        """Repeat the sweep as TCP pings when the target drops ICMP. True if any probe got an answer."""
        cfg = self._ctx.config.measurements
        port = cfg.tcp_fallback_port
        if not port or primary is None or not primary.supports_tcp:
            self._log("info", "measure", "No probe got a reply: the target filters ICMP")
            return False
        sweep = self._allowance(state, min(cfg.sweep_probes, primary.max_probes))
        if not sweep:
            return False
        self._log("info", primary.name, f"No ICMP replies: measuring the TCP handshake to port {port} instead")
        state.tcp_port = port
        report.probes = []
        locations = sweep_locations(sweep)
        batch = await self._ping(primary, report.target, locations, state)
        if batch:
            report.probes = merge_results([], self._to_results(batch, "sweep"))
            self._finish_geometry(report)
            progress()
        if not any(r.min_rtt is not None for r in report.probes):
            self._log("info", "measure", f"No reply over TCP port {port} either: the target cannot be measured")
            return False
        return True

    def _allowance(self, state: CampaignState, wanted: int) -> int:
        """How many tests a phase may use: ``wanted``, shrunk to the budget left; 0 when too few remain."""
        if wanted <= 0:
            return 0
        left = max(0, state.limit - state.tests_used)
        allowed = min(wanted, left)
        if allowed < MIN_PHASE_TESTS:
            self._log("info", "measure", f"Skipping a phase: {left} tests left in the budget")
            return 0
        if allowed < wanted:
            self._log("info", "measure", f"Budget: using {allowed} of the {wanted} probes planned")
        return allowed

    def _within_budget(self, state: CampaignState, needed: int) -> bool:
        if state.tests_used + needed > state.limit:
            left = max(0, state.limit - state.tests_used)
            self._log("info", "measure", f"Skipping a phase: it needs {needed} tests, {left} left in the budget")
            return False
        return True

    async def _calibrate(
        self, report: TargetReport, provider: MeasurementProvider, state: CampaignState, countries: Sequence[str]
    ) -> None:
        cfg = self._ctx.config.measurements
        anchors = await self._anchors(countries, report.target.address.version, cfg.calibration_anchors)
        zoom_size = len([r for r in report.probes if r.phase == "zoom"])
        if not anchors or not self._within_budget(state, zoom_size * len(anchors)):
            return
        self._log("info", "calibrate", f"Calibrating {zoom_size} probes against {len(anchors)} RIPE Atlas anchors")
        points: dict[str, list[tuple[float, float]]] = {}
        for address, lat, lon in anchors:
            batch = await self._ping(
                provider, Target(address=ipaddress.ip_address(address)), state.reuse[provider.name], state
            )
            for ping in batch.results if batch else []:
                if ping.min_rtt is None:
                    continue
                key = f"{ping.probe.provider}:{ping.probe.probe_id}"
                distance = haversine(ping.probe.location.lat, ping.probe.location.lon, lat, lon)
                points.setdefault(key, []).append((distance, ping.min_rtt))
        lines = {key: fit_bestline(pts, cfg.speed_km_per_ms) for key, pts in points.items() if len(pts) >= 2}
        updated = []
        for result in report.probes:
            key = f"{result.probe.provider}:{result.probe.probe_id}"
            if key in lines and result.min_rtt is not None:
                distance = lines[key].max_distance_km(result.min_rtt)
                result = result.model_copy(update={"max_distance_km": round(distance, 1), "calibrated": True})
            updated.append(result)
        report.probes = updated
        self._log("info", "calibrate", f"{len(lines)} probes calibrated")

    async def _anchors(self, countries: Sequence[str], version: int, count: int) -> list[tuple[str, float, float]]:
        found: list[tuple[str, float, float]] = []
        for country in countries:
            try:
                data, _ = await self._ctx.http.get_json(
                    "ripe-anchors", ANCHORS_URL, params={"country": country, "page_size": 50}, ttl=7 * 86_400
                )
            except ProviderError as exc:
                self._log("error", "calibrate", f"Anchors unavailable: {exc.message}")
                return found
            for anchor in (data or {}).get("results", []):
                address = anchor.get("ip_v4" if version == 4 else "ip_v6")
                geometry = anchor.get("geometry") or {}
                if address and not anchor.get("is_disabled") and geometry.get("coordinates"):
                    lon, lat = geometry["coordinates"]
                    found.append((address, lat, lon))
                if len(found) >= count:
                    return found
        return found

    def _finish_geometry(self, report: TargetReport) -> None:
        """Region, anycast verdict, hard evidence and provisional estimate from the probes so far."""
        disks = disks_from([r for r in report.probes if r.min_rtt is not None])
        if not disks:
            return
        anycast = detect_anycast(disks)
        report.anycast = anycast
        region_input = consistent_subset(disks) if anycast.detected else disks
        result = region_and_cells(region_input)
        report.evidence = [e for e in report.evidence if e.source != "rtt"]
        if result is None:
            report.region = None
            return
        region, cells = result
        cities = load_cities(self._ctx.paths.data_dir / "gazetteer" / FILE_NAME)
        if cities:
            region = region.model_copy(update={"candidates": tuple(candidate_cities(cities, region, cells))})
        report.region = region
        self._add_evidence(report, region, anycast.detected, len(disks))

    def _add_evidence(self, report: TargetReport, region: Region, anycast: bool, probes: int) -> None:
        best = next((r for r in report.probes if r.min_rtt is not None), None)
        top = region.candidates[0] if region.candidates else None
        where = f"{top.name}, {top.country}" if top else f"{region.centroid.lat:.2f}, {region.centroid.lon:.2f}"
        scope = "the nearest anycast site" if anycast else "the target"
        fastest = (
            f"; fastest {best.probe.city or best.probe.country} {best.min_rtt:.1f} ms" if best and best.min_rtt else ""
        )
        report.evidence.append(
            Evidence(
                source="rtt",
                kind=EvidenceKind.HARD,
                label="RTT region",
                summary=f"{probes} probes leave a {region.area_km2:,.0f} km² region for {scope} around {where}"
                f"{fastest}",
                point=region.centroid,
                radius_km=region.radius_km,
                city=top.name if top else None,
                country=top.country if top else None,
                details={
                    "area_km2": region.area_km2,
                    "cells": region.cells,
                    "resolution": region.resolution,
                    "probes": probes,
                    "binding_probes": list(region.binding_probes),
                },
            )
        )
        confidence = area_confidence(region.area_km2)
        if anycast:
            confidence = min(confidence, 30)
        report.estimate = Estimate(
            location=region.centroid,
            uncertainty_km=max(region.radius_km, 1.0),
            confidence=confidence,
            city=top.name if top else None,
            country=top.country if top else None,
            explanation=(f"Centre of the {region.area_km2:,.0f} km² region allowed by {probes} RTT measurements.",),
            anycast=anycast,
        )
