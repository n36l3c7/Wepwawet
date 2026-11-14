"""RIPE IPmap: RIPE NCC's own geolocation, built from crowdsourced data and latency from RIPE Atlas.

Free and keyless. Its ``score`` grows with the strength of the engines that agree.
"""

from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BASE_URL = "https://ipmap-api.ripe.net/v1/locate"


class IpmapProvider(EnrichmentProvider):
    name = "ipmap"
    label = "RIPE IPmap"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = str(report.target.address)
        data, cached = await ctx.http.get_json(
            self.name, f"{BASE_URL}/{address}/best", params={"client": "wepwawet"}, not_found_ok=True
        )
        result = EnrichmentResult(cached=cached)
        location = (data or {}).get("location") if isinstance(data, dict) else None
        if not isinstance(location, dict) or location.get("latitude") is None:
            return result
        city, country = location.get("cityName"), location.get("countryCodeAlpha2")
        score = location.get("score")
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.SOFT,
                label="RIPE IPmap",
                summary=f"RIPE IPmap: {', '.join(p for p in (city, country) if p)}"
                + (f" (score {score:.0f})" if isinstance(score, int | float) else ""),
                point=GeoPoint(lat=location["latitude"], lon=location["longitude"]),
                city=city,
                country=country,
                weight=0.6,
                details={
                    "iata": location.get("iataCode"),
                    "score": score,
                    "type": location.get("type"),
                    "alternatives": len((data or {}).get("alternatives") or []),
                },
            )
        )
        return result
