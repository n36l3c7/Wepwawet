"""Shared HTTP client for providers: one connection pool, a polite User-Agent, the TTL cache and per-source pacing."""

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import httpx

from wepwawet import __version__
from wepwawet.core.cache import TTLCache

USER_AGENT = f"Wepwawet/{__version__} (+https://github.com/n36l3c7/Wepwawet)"
RATE_LIMIT_SOURCE = "rate-limits"
DEFAULT_RATE_LIMIT_SECONDS = 60.0
MAX_RATE_LIMIT_SECONDS = 3600.0


class ProviderError(Exception):
    """A provider could not answer. ``retry_after`` (seconds) is set when the provider asked us to wait."""

    def __init__(self, source: str, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
        self.status = status
        self.retry_after = retry_after


class Pacer:
    """At most one request every ``interval`` seconds per source, and ``concurrency`` in flight."""

    def __init__(self, interval: float = 0.0, concurrency: int = 4) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._slots = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> None:
        await self._slots.acquire()
        async with self._lock:
            wait = self._next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next = time.monotonic() + self._interval

    async def __aexit__(self, *_exc: object) -> None:
        self._slots.release()


class HttpClient:
    def __init__(
        self,
        cache: TTLCache,
        ttl_for: Any,
        client: httpx.AsyncClient | None = None,
        pacing: Mapping[str, Pacer] | None = None,
        retries: int = 2,
        backoff: float = 1.0,
    ) -> None:
        self._retries = retries
        self._backoff = backoff
        self._cache = cache
        self._ttl_for = ttl_for
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._pacing = dict(pacing or {})

    @property
    def cache(self) -> TTLCache:
        return self._cache

    def ttl_for(self, source: str) -> int:
        return int(self._ttl_for(source))

    @property
    def raw(self) -> httpx.AsyncClient:
        return self._client

    def _check_rate_limit(self, source: str) -> None:
        """Refuse early while a provider's last 429 asked us to wait, even across runs."""
        entry = self._cache.get(RATE_LIMIT_SOURCE, source)
        if entry and entry.get("until", 0) > time.time():
            wait = entry["until"] - time.time()
            raise ProviderError(source, f"rate limited: retry in {wait / 60:.0f} min", status=429, retry_after=wait)

    def _remember_rate_limit(self, source: str, retry_after: float | None) -> None:
        wait = retry_after if retry_after and retry_after > 0 else DEFAULT_RATE_LIMIT_SECONDS
        wait = min(wait, MAX_RATE_LIMIT_SECONDS)
        self._cache.set(RATE_LIMIT_SOURCE, source, {"until": time.time() + wait}, max(1, int(wait)))

    def configure_pacing(self, source: str, interval: float, concurrency: int = 1) -> None:
        """Set the pace of ``source`` once; later calls keep the first setting."""
        self._pacing.setdefault(source, Pacer(interval=interval, concurrency=concurrency))

    def pacer(self, source: str) -> Pacer:
        if source not in self._pacing:
            self._pacing[source] = Pacer()
        return self._pacing[source]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request_json(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        """Uncached request; returns ``(status, parsed JSON or None)``. Raises ``ProviderError`` on failure."""
        self._check_rate_limit(source)
        retries = self._retries if method == "GET" else 0
        for attempt in range(retries + 1):
            async with self.pacer(source):
                try:
                    response = await self._client.request(method, url, params=params, headers=headers, json=json)
                except httpx.TimeoutException as exc:
                    if attempt < retries:
                        await asyncio.sleep(self._backoff * 2**attempt)
                        continue
                    raise ProviderError(source, "timed out") from exc
                except httpx.HTTPError as exc:
                    if attempt < retries:
                        await asyncio.sleep(self._backoff * 2**attempt)
                        continue
                    raise ProviderError(source, f"network error: {exc.__class__.__name__}") from exc
            if response.status_code >= 500 and attempt < retries:
                await asyncio.sleep(self._backoff * 2**attempt)
                continue
            break
        if response.status_code not in ok_statuses:
            retry_after = _retry_after(response)
            if response.status_code == 429:
                self._remember_rate_limit(source, retry_after)
            detail = _error_detail(response)
            raise ProviderError(
                source,
                f"HTTP {response.status_code}{': ' + detail if detail else ''}",
                status=response.status_code,
                retry_after=retry_after,
            )
        if not response.content:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise ProviderError(source, "answered with something that is not JSON") from exc

    async def get_json(
        self,
        source: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_key: str | None = None,
        ttl: int | None = None,
        not_found_ok: bool = False,
    ) -> tuple[Any, bool]:
        """Cached GET. Returns ``(data, from_cache)``; with ``not_found_ok`` a 404 gives ``None``."""
        key = cache_key or url + ("?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else "")
        cached = self._cache.get(source, key)
        if cached is not None:
            return cached.get("data"), True
        statuses = (200, 404) if not_found_ok else (200,)
        status, data = await self.request_json(source, "GET", url, params=params, headers=headers, ok_statuses=statuses)
        if status == 404:
            data = None
        self._cache.set(source, key, {"data": data}, ttl or self._ttl_for(source))
        return data, False


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    if isinstance(data, dict):
        for key in ("error", "message", "detail", "title"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("type")
            if isinstance(value, str):
                return value[:200]
    return ""
