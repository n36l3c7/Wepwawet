"""Globalping (jsDelivr): free measurements from thousands of community probes.

Anonymous use allows 250 tests an hour and 50 probes per measurement; a free token doubles the
hourly limit. A test is one probe's result, so a 48-probe ping costs 48 tests.
"""

import asyncio
import hashlib
import time
from collections.abc import Sequence
from typing import Any

from wepwawet.core.http import ProviderError
from wepwawet.core.models import GeoPoint, Probe, Target
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import (
    Budget,
    LocationSpec,
    MeasurementProvider,
    PingBatch,
    PingResult,
    ping_result,
)

BASE_URL = "https://api.globalping.io/v1"
POLL_INTERVAL = 0.6  # the API allows 2 polls per second per measurement
TIMEOUT_SECONDS = 45.0


def location_json(spec: LocationSpec) -> dict[str, Any]:
    data: dict[str, Any] = {"limit": spec.limit}
    for key in ("continent", "country", "city", "magic"):
        value = getattr(spec, key)
        if value:
            data[key] = value
    if spec.asn:
        data["asn"] = spec.asn
    return data


def probe_from(raw: dict[str, Any]) -> Probe:
    """Globalping results carry no probe id; derive a stable one from what identifies the probe."""
    key = f"{raw.get('city')}|{raw.get('asn')}|{raw.get('network')}|{raw.get('latitude')}|{raw.get('longitude')}"
    country = raw.get("country")
    return Probe(
        provider="globalping",
        probe_id=hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:10],
        location=GeoPoint(lat=raw["latitude"], lon=raw["longitude"]),
        city=raw.get("city"),
        country=country if isinstance(country, str) and len(country) == 2 else None,
        asn=raw.get("asn"),
        network=raw.get("network"),
    )


def parse_results(data: dict[str, Any]) -> list[PingResult]:
    results = []
    for item in data.get("results") or []:
        raw_probe, result = item.get("probe") or {}, item.get("result") or {}
        if raw_probe.get("latitude") is None or raw_probe.get("longitude") is None:
            continue
        rtts = [t["rtt"] for t in result.get("timings") or [] if isinstance(t.get("rtt"), int | float)]
        stats = result.get("stats") or {}
        sent = int(stats.get("total") or len(result.get("timings") or []) or 0)
        if result.get("status") != "finished":
            rtts = []
        results.append(ping_result(probe_from(raw_probe), rtts, sent))
    return results


class GlobalpingProvider(MeasurementProvider):
    name = "globalping"
    label = "Globalping"
    max_probes = 50
    supports_tcp = True

    def _headers(self, ctx: ProviderContext) -> dict[str, str]:
        headers = {"Accept-Encoding": "gzip"}
        token = ctx.secrets.get("globalping")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        options: dict[str, Any] = {"packets": max(1, min(16, packets))}
        if tcp_port:
            options.update(protocol="TCP", port=tcp_port)
        body: dict[str, Any] = {"type": "ping", "target": str(target.address), "measurementOptions": options}
        if isinstance(locations, str):
            body["locations"] = locations  # reuse the probes of an earlier measurement
        else:
            body["locations"] = [location_json(spec) for spec in locations]
        status, created = await ctx.http.request_json(
            self.name, "POST", f"{BASE_URL}/measurements", json=body, headers=self._headers(ctx), ok_statuses=(202, 422)
        )
        if status == 422:  # no probe matched the locations
            return PingBatch(provider=self.name)
        measurement_id = str(created["id"])
        data = await self._wait(measurement_id, ctx)
        results = parse_results(data)
        return PingBatch(provider=self.name, results=results, reuse_key=measurement_id, tests_used=len(results))

    async def _wait(self, measurement_id: str, ctx: ProviderContext) -> dict[str, Any]:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            _, data = await ctx.http.request_json(
                self.name, "GET", f"{BASE_URL}/measurements/{measurement_id}", headers=self._headers(ctx)
            )
            if data.get("status") == "finished" or time.monotonic() > deadline:
                return data if isinstance(data, dict) else {}

    async def budget(self, ctx: ProviderContext) -> Budget | None:
        try:
            _, data = await ctx.http.request_json(self.name, "GET", f"{BASE_URL}/limits", headers=self._headers(ctx))
        except ProviderError:
            return None
        create = ((data or {}).get("rateLimit") or {}).get("measurements", {}).get("create", {})
        return Budget(
            provider=self.name,
            unit="tests/hour",
            limit=create.get("limit"),
            remaining=create.get("remaining"),
            reset_seconds=create.get("reset"),
        )
