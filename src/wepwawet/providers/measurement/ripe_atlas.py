"""RIPE Atlas one-off pings. Costs credits: off until ``providers.ripe_atlas.max_credits`` is set.

A one-off ping with 3 packets costs 6 credits per probe (3 packets, doubled for one-offs). The
estimated cost is logged before anything is spent, and a measurement that would exceed the
budget is not started.
"""

import asyncio
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

BASE_URL = "https://atlas.ripe.net/api/v2"
POLL_INTERVAL = 5.0
TIMEOUT_SECONDS = 240.0
CONTINENT_AREAS = {
    "EU": "West",
    "AF": "South-Central",
    "NA": "North-Central",
    "SA": "South-Central",
    "AS": "South-East",
    "OC": "South-East",
}


def max_credits(ctx: ProviderContext) -> int:
    value = ctx.config.provider("ripe_atlas").option("max_credits", 0)
    text = str(value).strip()
    return int(text) if text.isdigit() else 0


def ping_cost(probes: int, packets: int) -> int:
    """Credits for a one-off ping: ``packets`` per result (packet size under 1500 bytes), doubled."""
    return probes * packets * 2


def probe_spec(spec: LocationSpec) -> dict[str, Any] | None:
    if spec.country:
        return {"type": "country", "value": spec.country.upper(), "requested": spec.limit}
    if spec.asn:
        return {"type": "asn", "value": spec.asn, "requested": spec.limit}
    if spec.continent:
        return {"type": "area", "value": CONTINENT_AREAS.get(spec.continent.upper(), "WW"), "requested": spec.limit}
    return {"type": "area", "value": "WW", "requested": spec.limit}


class RipeAtlasProvider(MeasurementProvider):
    name = "ripe_atlas"
    label = "RIPE Atlas"
    required_secrets = ("ripe_atlas",)
    max_probes = 100

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        reason = super().unavailable_reason(ctx)
        if reason:
            return reason
        if max_credits(ctx) <= 0:
            return "spends credits: set providers.ripe_atlas.max_credits to allow it"
        return None

    def _headers(self, ctx: ProviderContext) -> dict[str, str]:
        return {"Authorization": f"Key {ctx.secrets.get('ripe_atlas')}"}

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        if isinstance(locations, str):
            probes: list[dict[str, Any]] = [{"type": "msm", "value": int(locations), "requested": self.max_probes}]
        else:
            probes = [p for p in (probe_spec(s) for s in locations) if p]
        requested = sum(int(str(p["requested"])) for p in probes)
        cost = ping_cost(requested, packets)
        budget = max_credits(ctx)
        if cost > budget:
            raise ProviderError(self.name, f"would cost {cost} credits, above max_credits = {budget}")
        ctx.log("info", self.name, f"One-off ping from {requested} probes: up to {cost} credits")
        body = {
            "definitions": [
                {
                    "type": "ping",
                    "af": target.address.version,
                    "target": str(target.address),
                    "packets": packets,
                    "description": "Wepwawet geolocation",
                    "is_public": True,
                }
            ],
            "probes": probes,
            "is_oneoff": True,
        }
        _, created = await ctx.http.request_json(
            self.name,
            "POST",
            f"{BASE_URL}/measurements/",
            json=body,
            headers=self._headers(ctx),
            ok_statuses=(201, 202),
        )
        measurement_id = int((created or {}).get("measurements", [0])[0])
        raw = await self._wait(measurement_id, requested, ctx)
        locations_by_id = await self._probe_locations([r["prb_id"] for r in raw if "prb_id" in r], ctx)
        results = [r for r in (self._result(item, locations_by_id) for item in raw) if r]
        return PingBatch(provider=self.name, results=results, reuse_key=str(measurement_id), tests_used=cost)

    async def _wait(self, measurement_id: int, requested: int, ctx: ProviderContext) -> list[dict[str, Any]]:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        results: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            _, data = await ctx.http.request_json(
                self.name, "GET", f"{BASE_URL}/measurements/{measurement_id}/results/", headers=self._headers(ctx)
            )
            results = data if isinstance(data, list) else []
            if len(results) >= requested:
                break
        return results

    async def _probe_locations(self, ids: list[int], ctx: ProviderContext) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        _, data = await ctx.http.request_json(
            self.name,
            "GET",
            f"{BASE_URL}/probes/",
            params={
                "id__in": ",".join(map(str, sorted(set(ids)))),
                "fields": "id,geometry,country_code,asn_v4",
                "page_size": 500,
            },
        )
        return {p["id"]: p for p in (data or {}).get("results", []) if p.get("geometry")}

    def _result(self, item: dict[str, Any], probes: dict[int, dict[str, Any]]) -> PingResult | None:
        info = probes.get(item.get("prb_id", -1))
        if info is None:
            return None
        lon, lat = info["geometry"]["coordinates"]
        probe = Probe(
            provider=self.name,
            probe_id=str(info["id"]),
            location=GeoPoint(lat=lat, lon=lon),
            country=info.get("country_code"),
            asn=info.get("asn_v4"),
        )
        rtts = [r["rtt"] for r in item.get("result") or [] if isinstance(r.get("rtt"), int | float)]
        return ping_result(probe, rtts, int(item.get("sent") or len(item.get("result") or [])))

    async def budget(self, ctx: ProviderContext) -> Budget | None:
        limit = max_credits(ctx)
        try:
            _, data = await ctx.http.request_json(self.name, "GET", f"{BASE_URL}/credits/", headers=self._headers(ctx))
        except ProviderError:
            return Budget(provider=self.name, unit="credits", limit=limit, remaining=None)
        return Budget(provider=self.name, unit="credits", limit=limit, remaining=(data or {}).get("current_balance"))
