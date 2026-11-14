"""Shodan host lookup (passive): what Shodan already knows about the address. Needs an API key.

Wepwawet only reads Shodan's existing record; it never asks Shodan to scan.
"""

from typing import Any

from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BASE_URL = "https://api.shodan.io/shodan/host"


def certificate_names(services: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for service in services:
        cert = (service.get("ssl") or {}).get("cert") or {}
        common_name = (cert.get("subject") or {}).get("CN")
        if common_name and common_name not in names:
            names.append(common_name)
    return names


class ShodanProvider(EnrichmentProvider):
    name = "shodan"
    label = "Shodan"
    required_secrets = ("shodan",)

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = str(report.target.address)
        data, cached = await ctx.http.get_json(
            self.name,
            f"{BASE_URL}/{address}",
            params={"key": ctx.secrets.get("shodan"), "minify": "false"},
            cache_key=f"host:{address}",  # never put the key in the cache key
            not_found_ok=True,
        )
        result = EnrichmentResult(cached=cached)
        if not isinstance(data, dict):
            result.evidence.append(Evidence(source=self.name, kind=EvidenceKind.INFO, summary="No Shodan record"))
            return result
        lat, lon = data.get("latitude"), data.get("longitude")
        city, country = data.get("city"), data.get("country_code")
        services = data.get("data") or []
        ports = sorted({s.get("port") for s in services if isinstance(s.get("port"), int)})
        certs = certificate_names(services)
        hostnames = data.get("hostnames") or []
        parts = [
            f"org {data.get('org')}" if data.get("org") else None,
            f"ports {', '.join(map(str, ports))}" if ports else None,
        ]
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.SOFT if lat is not None else EvidenceKind.INFO,
                label="Shodan",
                summary="Shodan: " + "; ".join(p for p in parts if p) if any(parts) else "Shodan record",
                point=GeoPoint(lat=lat, lon=lon) if lat is not None and lon is not None else None,
                city=city,
                country=country,
                weight=0.3,
                details={
                    "org": data.get("org"),
                    "isp": data.get("isp"),
                    "asn": data.get("asn"),
                    "hostnames": hostnames,
                    "domains": data.get("domains") or [],
                    "ports": ports,
                    "certificate_names": certs,
                    "last_update": data.get("last_update"),
                },
            )
        )
        return result
