"""Accuracy benchmark against RIPE Atlas anchors, whose exact positions RIPE NCC publishes.

Each anchor is located like any target; the error of every stage (passive, after the pings,
after the traceroutes, final) is the distance from that stage's estimate to the anchor. The
report gives the median, the 90th percentile and how often the truth fell inside the stated
uncertainty, per stage, so you can see what each technique adds.

Bias to keep in mind: anchors sit in data centres and well-connected networks, where GeoIP is
good and probes are near; residential and mobile addresses are harder than this benchmark.
"""

import csv
import io
import random
import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from wepwawet.core.geo import haversine
from wepwawet.core.http import ProviderError
from wepwawet.core.models import RunReport
from wepwawet.providers.base import ProviderContext

ANCHORS_URL = "https://atlas.ripe.net/api/v2/anchors/"
STAGES = ("passive", "pings", "traceroutes", "final")


@dataclass(frozen=True)
class Anchor:
    fqdn: str
    address: str
    country: str
    city: str
    lat: float
    lon: float


@dataclass
class AnchorResult:
    anchor: Anchor
    run_id: str | None = None
    errors_km: dict[str, float] = field(default_factory=dict)
    covered: dict[str, bool] = field(default_factory=dict)
    tests_used: int = 0
    error: str | None = None


@dataclass(frozen=True)
class StageSummary:
    stage: str
    count: int
    median_km: float | None
    p90_km: float | None
    coverage: float | None  # share of anchors inside the stated uncertainty


async def fetch_anchors(ctx: ProviderContext, version: int = 4) -> list[Anchor]:
    anchors: list[Anchor] = []
    url: str | None = ANCHORS_URL
    params: dict[str, Any] | None = {"is_disabled": "false", "page_size": 500}
    while url:
        data, _ = await ctx.http.get_json("ripe-anchors", url, params=params, ttl=7 * 86_400)
        params = None  # the "next" link already carries them
        for row in (data or {}).get("results", []):
            address = row.get("ip_v4" if version == 4 else "ip_v6")
            geometry = row.get("geometry") or {}
            if not address or row.get("is_disabled") or not geometry.get("coordinates"):
                continue
            lon, lat = geometry["coordinates"]
            anchors.append(
                Anchor(row.get("fqdn") or address, address, row.get("country") or "", row.get("city") or "", lat, lon)
            )
        url = (data or {}).get("next")
    return anchors


def pick_anchors(anchors: Sequence[Anchor], count: int, seed: int) -> list[Anchor]:
    """A reproducible sample, one anchor per country first, so the set is spread out."""
    shuffled = list(anchors)
    random.Random(seed).shuffle(shuffled)  # noqa: S311 - sampling, not security
    chosen: list[Anchor] = []
    countries: set[str] = set()
    for anchor in shuffled:
        if anchor.country not in countries:
            chosen.append(anchor)
            countries.add(anchor.country)
        if len(chosen) >= count:
            return chosen
    for anchor in shuffled:
        if anchor not in chosen:
            chosen.append(anchor)
        if len(chosen) >= count:
            break
    return chosen


def score(anchor: Anchor, run: RunReport) -> AnchorResult:
    result = AnchorResult(anchor=anchor, run_id=run.id)
    target = next((t for t in run.targets if str(t.target.address) == anchor.address), None)
    if target is None:
        result.error = run.error or "no result"
        return result
    result.tests_used = target.tests_used
    for stage in target.stages:
        distance = haversine(anchor.lat, anchor.lon, stage.location.lat, stage.location.lon)
        result.errors_km[stage.stage] = round(distance, 1)
        result.covered[stage.stage] = distance <= stage.uncertainty_km
    return result


def summarise(results: Sequence[AnchorResult]) -> list[StageSummary]:
    summaries = []
    for stage in STAGES:
        errors = sorted(r.errors_km[stage] for r in results if stage in r.errors_km)
        covered = [r.covered[stage] for r in results if stage in r.covered]
        if not errors:
            summaries.append(StageSummary(stage, 0, None, None, None))
            continue
        p90 = errors[min(len(errors) - 1, max(0, round(0.9 * len(errors)) - 1))]
        summaries.append(
            StageSummary(
                stage=stage,
                count=len(errors),
                median_km=round(statistics.median(errors), 1),
                p90_km=round(p90, 1),
                coverage=round(sum(covered) / len(covered), 2),
            )
        )
    return summaries


async def run_benchmark(
    anchors: Sequence[Anchor], locate: Callable[[str], Awaitable[RunReport]], progress: Callable[[str], None]
) -> list[AnchorResult]:
    results = []
    for index, anchor in enumerate(anchors, start=1):
        progress(f"{index}/{len(anchors)} {anchor.fqdn} ({anchor.city}, {anchor.country})")
        try:
            run = await locate(anchor.address)
        except ProviderError as exc:
            results.append(AnchorResult(anchor=anchor, error=exc.message))
            continue
        results.append(score(anchor, run))
    return results


def results_csv(results: Sequence[AnchorResult]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "anchor",
            "address",
            "country",
            "city",
            "tests",
            *[f"{s}_error_km" for s in STAGES],
            *[f"{s}_covered" for s in STAGES],
            "error",
        ]
    )
    for r in results:
        writer.writerow(
            [
                r.anchor.fqdn,
                r.anchor.address,
                r.anchor.country,
                r.anchor.city,
                r.tests_used,
                *[r.errors_km.get(s, "") for s in STAGES],
                *[("" if s not in r.covered else str(r.covered[s]).lower()) for s in STAGES],
                r.error or "",
            ]
        )
    return buffer.getvalue()
