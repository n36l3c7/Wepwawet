"""RIPEstat Data API: routed prefix, origin AS, RIR country and the MaxMind GeoLite answer RIPE hosts.

https://stat.ripe.net/docs/data-api — free, no key; ``sourceapp`` identifies the client.
"""

from typing import Any

from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, NetworkInfo, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BASE_URL = "https://stat.ripe.net/data"
SOURCEAPP = "wepwawet"


async def ripestat_call(ctx: ProviderContext, call: str, resource: str) -> tuple[dict[str, Any], bool]:
    data, cached = await ctx.http.get_json(
        "ripestat", f"{BASE_URL}/{call}/data.json", params={"resource": resource, "sourceapp": SOURCEAPP}
    )
    payload = data.get("data") if isinstance(data, dict) else None
    return (payload if isinstance(payload, dict) else {}), cached


class RipeStatProvider(EnrichmentProvider):
    name = "ripestat"
    label = "RIPEstat"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = str(report.target.address)
        overview, cached_a = await ripestat_call(ctx, "prefix-overview", address)
        rir, cached_b = await ripestat_call(ctx, "rir-geo", address)
        geo, cached_c = await ripestat_call(ctx, "maxmind-geo-lite", address)
        result = EnrichmentResult(cached=cached_a and cached_b and cached_c)

        asns = overview.get("asns") or []
        origin = asns[0] if asns and isinstance(asns[0], dict) else {}
        block = overview.get("block") or {}
        rir_country = _rir_country(rir)
        result.network = NetworkInfo(
            prefix=overview.get("resource") if overview.get("announced") else None,
            asn=origin.get("asn"),
            as_name=origin.get("holder"),
            announced=overview.get("announced"),
            rir=_rir_from_block(block),
            rir_country=rir_country,
        )
        if overview:
            origins = ", ".join(f"AS{a.get('asn')} {a.get('holder', '')}".strip() for a in asns) or "none"
            state = "announced" if overview.get("announced") else "not announced"
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.INFO,
                    summary=f"Prefix {overview.get('resource')} {state}; origin {origins}",
                    details={
                        "prefix": overview.get("resource"),
                        "announced": overview.get("announced"),
                        "origins": asns,
                        "block": block,
                    },
                )
            )
        if rir_country:
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.SOFT,
                    summary=f"Registered in {rir_country} (RIR statistics)",
                    country=rir_country,
                    weight=0.2,
                    details={"rir_country": rir_country},
                )
            )
        result.evidence.extend(_maxmind_lite(geo))
        return result


def _rir_country(data: dict[str, Any]) -> str | None:
    for item in data.get("located_resources") or []:
        location = item.get("location")
        if isinstance(location, str) and len(location) == 2:
            return location.upper()
    return None


def _rir_from_block(block: dict[str, Any]) -> str | None:
    text = f"{block.get('desc', '')} {block.get('name', '')}".upper()
    for rir in ("RIPE", "ARIN", "APNIC", "LACNIC", "AFRINIC"):
        if rir in text:
            return rir
    return None


def _maxmind_lite(data: dict[str, Any]) -> list[Evidence]:
    """RIPEstat serves GeoLite2 City: a GeoIP answer even without a local database."""
    evidence = []
    for resource in data.get("located_resources") or []:
        for loc in resource.get("locations") or []:
            lat, lon = loc.get("latitude"), loc.get("longitude")
            # (0, 0) without a city or country is GeoLite's "unknown", not a place in the Gulf of Guinea.
            if lat is None or lon is None or (lat == 0 and lon == 0 and not loc.get("country")):
                continue
            city = loc.get("city") or None
            country = loc.get("country") or None
            where = ", ".join(p for p in (city, country) if p) or f"{lat:.2f}, {lon:.2f}"
            evidence.append(
                Evidence(
                    source="geoip",
                    kind=EvidenceKind.SOFT,
                    label="GeoLite2 (RIPEstat)",
                    summary=f"GeoLite2 via RIPEstat: {where}",
                    point=GeoPoint(lat=lat, lon=lon),
                    radius_km=float(loc.get("accuracy_radius") or 100) or None,
                    city=city,
                    country=country,
                    weight=0.5,
                    details={"database": "GeoLite2 (RIPEstat)", "covered_percentage": loc.get("covered_percentage")},
                )
            )
        if evidence:
            break
    return evidence
