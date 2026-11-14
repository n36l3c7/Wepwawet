"""TTL cache for provider answers, stored in SQLite.

Every external lookup goes through this cache, keyed by ``(source, key)``, so a
repeated run costs no API credits and respects the providers' rate limits.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Float, MetaData, String, Table, Text, create_engine, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

_metadata = MetaData()
_entries = Table(
    "cache_entries",
    _metadata,
    Column("source", String(64), primary_key=True),
    Column("key", String(512), primary_key=True),
    Column("value", Text, nullable=False),
    Column("expires_at", Float, nullable=False, index=True),
)

Clock = Callable[[], float]


class TTLCache:
    def __init__(self, engine: Engine, clock: Clock = time.time) -> None:
        self._engine = engine
        self._clock = clock
        _metadata.create_all(engine)

    @classmethod
    def open(cls, path: Path, clock: Clock = time.time) -> "TTLCache":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(create_engine(f"sqlite:///{path}"), clock)

    @classmethod
    def in_memory(cls, clock: Clock = time.time) -> "TTLCache":
        return cls(create_engine("sqlite://"), clock)

    def get(self, source: str, key: str) -> Any | None:
        """Return the cached JSON value, or ``None`` when missing or expired."""
        query = select(_entries.c.value).where(
            _entries.c.source == source,
            _entries.c.key == key,
            _entries.c.expires_at > self._clock(),
        )
        with self._engine.connect() as conn:
            raw = conn.execute(query).scalar_one_or_none()
        return None if raw is None else json.loads(raw)

    def set(self, source: str, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        row = {
            "source": source,
            "key": key,
            "value": json.dumps(value, separators=(",", ":"), sort_keys=True),
            "expires_at": self._clock() + ttl_seconds,
        }
        stmt = sqlite_insert(_entries).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[_entries.c.source, _entries.c.key],
            set_={"value": stmt.excluded.value, "expires_at": stmt.excluded.expires_at},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def purge_expired(self) -> int:
        """Delete expired rows; return how many were removed."""
        with self._engine.begin() as conn:
            result = conn.execute(delete(_entries).where(_entries.c.expires_at <= self._clock()))
        return result.rowcount

    def clear(self, source: str | None = None) -> int:
        stmt = delete(_entries) if source is None else delete(_entries).where(_entries.c.source == source)
        with self._engine.begin() as conn:
            result = conn.execute(stmt)
        return result.rowcount
