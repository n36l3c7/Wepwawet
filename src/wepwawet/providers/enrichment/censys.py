"""Censys host lookup (passive, Platform API v3). Needs a personal access token.

Each lookup costs one Censys credit (the free plan has 100 a month), so answers are cached.
"""

from typing import Any

from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BASE_URL = "https://api.platform.censys.io/v3/global/asset/host"
CACHE_TTL = 7 * 86_400  # credits are scarce


def subject_names(services: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for service in services:
        parsed = ((service.get("cert") or {}).get("parsed")) or {}
        subject = parsed.get("subject_dn")
        if subject and subject not in names:
            names.append(subject)
    return names


class CensysProvider(EnrichmentProvider):
    name = "censys"
    label = "Censys"
    required_secrets = ("censys",)

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = str(report.target.address)
        ctx.http.configure_pacing(self.name, interval=1.0, concurrency=1)
        headers = {"Authorization": f"Bearer {ctx.secrets.get('censys')}", "Accept": "application/json"}
        org = ctx.secrets.get("censys_org_id")
        if org:
            headers["X-Organization-ID"] = org
        data, cached = await ctx.http.get_json(
            self.name,
            f"{BASE_URL}/{address}",
            headers=headers,
            cache_key=f"host:{address}",
            ttl=CACHE_TTL,
            not_found_ok=True,
        )
        result = EnrichmentResult(cached=cached)
        resource = ((data or {}).get("result") or {}).get("resource") if isinstance(data, dict) else None
        if not isinstance(resource, dict):
            result.evidence.append(Evidence(source=self.name, kind=EvidenceKind.INFO, summary="No Censys record"))
            return result
        location = resource.get("location") or {}
        coords = location.get("coordinates") or {}
        lat, lon = coords.get("latitude"), coords.get("longitude")
        system = resource.get("autonomous_system") or {}
        services = resource.get("services") or []
        ports = sorted({s.get("port") for s in services if isinstance(s.get("port"), int)})
        dns_names = ((resource.get("dns") or {}).get("names")) or []
        where = ", ".join(p for p in (location.get("city"), location.get("country_code")) if p)
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.SOFT if lat is not None else EvidenceKind.INFO,
                label="Censys",
                summary=f"Censys: {where or 'no location'}"
                + (f"; ports {', '.join(map(str, ports))}" if ports else ""),
                point=GeoPoint(lat=lat, lon=lon) if lat is not None and lon is not None else None,
                city=location.get("city"),
                country=location.get("country_code"),
                weight=0.3,
                details={
                    "asn": system.get("asn"),
                    "as_name": system.get("name"),
                    "bgp_prefix": system.get("bgp_prefix"),
                    "dns_names": dns_names[:20],
                    "ports": ports,
                    "certificate_subjects": subject_names(services)[:10],
                    "labels": [
                        label.get("value") if isinstance(label, dict) else label
                        for label in resource.get("labels") or []
                    ],
                },
            )
        )
        return result
