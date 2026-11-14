"""Bulk mode: locate a list of targets one after the other under one test budget.

Each target may use at most what is left of the budget (and no more than ``measurements.max_tests``);
when nothing is left, the remaining targets get the passive sources only. Every run is saved to
the history like a single one.
"""

import asyncio
import dataclasses
import secrets
import string
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from wepwawet.core.models import RunReport, RunStatus

MAX_TARGETS = 1000

# Runs one query with at most ``max_tests`` measurement tests (0: passive only).
RunOne = Callable[[str, int], "asyncio.Future[RunReport] | Any"]


def parse_targets(text: str) -> list[str]:
    """One target per line; ``#`` starts a comment; blank lines and duplicates are skipped."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        value = line.split("#", 1)[0].strip(string.whitespace + ",;")
        if value:
            seen.setdefault(value, None)
    targets = list(seen)
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"{len(targets)} targets; the limit is {MAX_TARGETS} per bulk run")
    return targets


@dataclass
class BulkItem:
    query: str
    status: str = "queued"  # queued, running, done, failed, cancelled
    run_id: str | None = None
    tests_used: int = 0
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class BulkJob:
    id: str
    budget: int
    passive: bool
    items: list[BulkItem]
    status: str = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    runs: list[RunReport] = field(default_factory=list)

    @property
    def tests_used(self) -> int:
        return sum(item.tests_used for item in self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "budget": self.budget,
            "passive": self.passive,
            "tests_used": self.tests_used,
            "started_at": self.started_at.isoformat(),
            "items": [dataclasses.asdict(item) for item in self.items],
        }


def item_summary(run: RunReport) -> dict[str, Any]:
    """The first address's result, for the queue table and the map."""
    if run.status is RunStatus.FAILED or not run.targets:
        return {"error": run.error}
    target = run.targets[0]
    e = target.estimate
    summary: dict[str, Any] = {
        "address": str(target.target.address),
        "addresses": len(run.targets),
        "classes": [c.value for c in target.classification],
    }
    if e is not None:
        summary.update(
            city=e.city,
            country=e.country,
            lat=e.location.lat,
            lon=e.location.lon,
            uncertainty_km=e.uncertainty_km,
            confidence=e.confidence,
            anycast=e.anycast,
        )
    return summary


async def run_bulk(
    job: BulkJob,
    run_one: Callable[[str, int], Any],
    per_target_limit: int,
    on_update: Callable[[BulkJob], None] = lambda _job: None,
) -> BulkJob:
    """Run every queued item in order; ``run_one(query, max_tests)`` returns a finished ``RunReport``."""
    try:
        for item in job.items:
            if item.status != "queued":
                continue
            left = max(0, job.budget - job.tests_used)
            allowed = 0 if job.passive else min(per_target_limit, left)
            item.status = "running"
            on_update(job)
            run: RunReport = await run_one(item.query, allowed)
            item.run_id = run.id
            item.tests_used = sum(t.tests_used for t in run.targets)
            item.status = "failed" if run.status is RunStatus.FAILED else "done"
            item.summary = item_summary(run)
            job.runs.append(run)
            on_update(job)
        job.status = "done"
    except asyncio.CancelledError:
        job.status = "cancelled"
        for item in job.items:
            if item.status in ("queued", "running"):
                item.status = "cancelled"
        on_update(job)
        raise
    on_update(job)
    return job


def new_job(queries: Iterable[str], budget: int, passive: bool) -> BulkJob:
    return BulkJob(id=secrets.token_hex(6), budget=budget, passive=passive, items=[BulkItem(q) for q in queries])
