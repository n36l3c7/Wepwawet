"""Shodan InternetDB: Shodan's free, keyless summary of an address (open ports, hostnames, tags).

Updated weekly, for non-commercial use. Its tags (``vpn``, ``proxy``, ``tor``, ``cloud``...)
feed the network classification.
"""

from wepwawet.core.models import Evidence, EvidenceKind, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BASE_URL = "https://internetdb.shodan.io"


class InternetDbProvider(EnrichmentProvider):
    name = "internetdb"
    label = "Shodan InternetDB"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = str(report.target.address)
        data, cached = await ctx.http.get_json(self.name, f"{BASE_URL}/{address}", not_found_ok=True)
        result = EnrichmentResult(cached=cached)
        if not isinstance(data, dict) or not (data.get("ports") or data.get("hostnames") or data.get("tags")):
            return result
        ports = sorted(p for p in data.get("ports") or [] if isinstance(p, int))
        tags = [str(t) for t in data.get("tags") or []]
        hostnames = [str(h) for h in data.get("hostnames") or []]
        parts = []
        if ports:
            parts.append(f"open ports {', '.join(map(str, ports[:12]))}")
        if tags:
            parts.append(f"tags {', '.join(tags)}")
        if hostnames:
            parts.append(f"names {', '.join(hostnames[:3])}")
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.INFO,
                summary="InternetDB: " + "; ".join(parts),
                details={"ports": ports, "tags": tags, "hostnames": hostnames, "cpes": data.get("cpes") or []},
            )
        )
        return result
