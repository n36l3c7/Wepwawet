"""Run history: every finished run is kept in SQLite with its full report, and two runs can be compared."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, desc, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from wepwawet.core.geo import haversine
from wepwawet.core.models import RunReport, TargetReport

_metadata = MetaData()
_runs = Table(
    "runs",
    _metadata,
    Column("id", String(32), primary_key=True),
    Column("query", String(300), nullable=False, index=True),
    Column("status", String(16), nullable=False),
    Column("started_at", Float, nullable=False, index=True),
    Column("targets", Integer, nullable=False),
    Column("address", String(64)),
    Column("city", String(200)),
    Column("country", String(8)),
    Column("confidence", Integer),
    Column("report", Text, nullable=False),
)


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    query: str
    status: str
    started_at: datetime
    targets: int
    address: str | None
    city: str | None
    country: str | None
    confidence: int | None

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "started_at": self.started_at.isoformat()}


class History:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        _metadata.create_all(engine)

    @classmethod
    def open(cls, path: Path) -> "History":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(create_engine(f"sqlite:///{path}"))

    @classmethod
    def in_memory(cls) -> "History":
        return cls(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))

    def save(self, run: RunReport) -> None:
        first = run.targets[0] if run.targets else None
        estimate = first.estimate if first else None
        row = {
            "id": run.id,
            "query": run.query,
            "status": run.status.value,
            "started_at": run.started_at.timestamp(),
            "targets": len(run.targets),
            "address": str(first.target.address) if first else None,
            "city": estimate.city if estimate else None,
            "country": estimate.country if estimate else None,
            "confidence": estimate.confidence if estimate else None,
            "report": run.model_dump_json(),
        }
        with self._engine.begin() as conn:
            conn.execute(delete(_runs).where(_runs.c.id == run.id))
            conn.execute(_runs.insert().values(row))

    def list(self, query: str | None = None, limit: int = 100) -> list[HistoryEntry]:
        statement = select(_runs).order_by(desc(_runs.c.started_at)).limit(limit)
        if query:
            statement = statement.where(_runs.c.query == query.strip())
        with self._engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
        return [
            HistoryEntry(
                id=r["id"],
                query=r["query"],
                status=r["status"],
                started_at=datetime.fromtimestamp(r["started_at"], UTC),
                targets=r["targets"],
                address=r["address"],
                city=r["city"],
                country=r["country"],
                confidence=r["confidence"],
            )
            for r in rows
        ]

    def get(self, run_id: str) -> RunReport | None:
        with self._engine.connect() as conn:
            raw = conn.execute(select(_runs.c.report).where(_runs.c.id == run_id)).scalar_one_or_none()
        return RunReport.model_validate_json(raw) if raw else None

    def delete(self, run_id: str) -> bool:
        with self._engine.begin() as conn:
            return conn.execute(delete(_runs).where(_runs.c.id == run_id)).rowcount > 0


@dataclass(frozen=True)
class Change:
    field: str
    before: str
    after: str
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


def _pick(run: RunReport, address: str | None) -> TargetReport | None:
    for target in run.targets:
        if address is None or str(target.target.address) == address:
            return target
    return run.targets[0] if run.targets else None


def _place(target: TargetReport) -> str:
    e = target.estimate
    if e is None:
        return "no estimate"
    where = ", ".join(p for p in (e.city, e.country) if p) or f"{e.location.lat:.3f}, {e.location.lon:.3f}"
    return f"{where} ±{e.uncertainty_km:.0f} km"


def _paths(target: TargetReport) -> dict[str, list[str]]:
    return {f"{t.probe.city or t.probe.probe_id}": [h.address or "*" for h in t.hops] for t in target.traceroutes}


def diff_runs(old: RunReport, new: RunReport, address: str | None = None) -> list[Change]:
    """What changed for one address between two runs (the first address when not given)."""
    a, b = _pick(old, address), _pick(new, address or (str(old.targets[0].target.address) if old.targets else None))
    if a is None or b is None:
        return [Change("targets", str(len(old.targets)), str(len(new.targets)), "nothing to compare")]
    changes: list[Change] = []
    if a.estimate and b.estimate:
        moved = haversine(
            a.estimate.location.lat, a.estimate.location.lon, b.estimate.location.lat, b.estimate.location.lon
        )
        if moved >= 1 or _place(a) != _place(b):
            changes.append(Change("estimate", _place(a), _place(b), f"moved {moved:,.0f} km"))
        if a.estimate.confidence != b.estimate.confidence:
            changes.append(Change("confidence", str(a.estimate.confidence), str(b.estimate.confidence)))
    elif bool(a.estimate) != bool(b.estimate):
        changes.append(Change("estimate", _place(a), _place(b)))
    if [c.value for c in a.classification] != [c.value for c in b.classification]:
        changes.append(
            Change(
                "network class",
                " + ".join(c.value for c in a.classification) or "-",
                " + ".join(c.value for c in b.classification) or "-",
            )
        )
    for field in ("prefix", "asn", "as_name", "netname"):
        before = getattr(a.network, field, None) if a.network else None
        after = getattr(b.network, field, None) if b.network else None
        if before != after:
            changes.append(Change(field.replace("_", " "), str(before or "-"), str(after or "-")))
    geo_a = {
        e.label: ", ".join(p for p in (e.city, e.country) if p) for e in a.evidence if e.source in ("geoip", "ipmap")
    }
    geo_b = {
        e.label: ", ".join(p for p in (e.city, e.country) if p) for e in b.evidence if e.source in ("geoip", "ipmap")
    }
    for label in sorted(set(geo_a) | set(geo_b), key=str):
        if geo_a.get(label) != geo_b.get(label):
            changes.append(Change(str(label), geo_a.get(label) or "-", geo_b.get(label) or "-"))
    conv_a = a.convergence.hostname or a.convergence.address if a.convergence else "-"
    conv_b = b.convergence.hostname or b.convergence.address if b.convergence else "-"
    if conv_a != conv_b:
        changes.append(Change("convergence", conv_a, conv_b))
    paths_a, paths_b = _paths(a), _paths(b)
    for probe in sorted(set(paths_a) & set(paths_b)):
        hops_a, hops_b = paths_a[probe], paths_b[probe]
        if hops_a != hops_b:
            index = next(
                (i for i, (x, y) in enumerate(zip(hops_a, hops_b, strict=False)) if x != y),
                min(len(hops_a), len(hops_b)),
            )
            before = hops_a[index] if index < len(hops_a) else "(end)"
            after = hops_b[index] if index < len(hops_b) else "(end)"
            changes.append(Change(f"path from {probe}", before, after, f"differs from hop {index + 1}"))
    if a.region and b.region and abs(a.region.area_km2 - b.region.area_km2) > 0.05 * a.region.area_km2:
        changes.append(Change("region", f"{a.region.area_km2:,.0f} km²", f"{b.region.area_km2:,.0f} km²"))
    return changes


def report_to_json(run: RunReport) -> str:
    return json.dumps(run.model_dump(mode="json"), indent=2)
