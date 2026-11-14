"""RDAP: who the address block is registered to, and whether it is a sub-allocation to a customer.

The RIR is found through the IANA bootstrap registry (RFC 9224), cached for a week.
"""

import ipaddress
from typing import Any

from wepwawet.core.http import ProviderError
from wepwawet.core.models import Evidence, EvidenceKind, IPAddress, NetworkInfo, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BOOTSTRAP = {4: "https://data.iana.org/rdap/ipv4.json", 6: "https://data.iana.org/rdap/ipv6.json"}
BOOTSTRAP_TTL = 7 * 86_400
RIR_BY_HOST = {"ripe": "RIPE", "arin": "ARIN", "apnic": "APNIC", "lacnic": "LACNIC", "afrinic": "AFRINIC"}
# Registration types that mean "assigned to an end customer" rather than kept by the provider.
CUSTOMER_TYPES = (
    "ASSIGNED PA",
    "ASSIGNED PI",
    "REASSIGNMENT",
    "REALLOCATION",
    "ASSIGNED NON-PORTABLE",
    "SUB-ALLOCATED",
)


def pick_server(bootstrap: dict[str, Any], address: IPAddress) -> str | None:
    """The RDAP base URL whose registered prefix most specifically contains ``address``."""
    best: tuple[int, str] | None = None
    for prefixes, urls in bootstrap.get("services", []):
        https = [u for u in urls if u.startswith("https://")] or urls
        for prefix in prefixes:
            network = ipaddress.ip_network(prefix, strict=False)
            if (
                network.version == address.version
                and address in network
                and (best is None or network.prefixlen > best[0])
            ):
                best = (network.prefixlen, https[0])
    return best[1] if best else None


def _vcard_value(entity: dict[str, Any], field: str) -> str | None:
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2:
        return None
    for item in vcard[1]:
        if isinstance(item, list) and len(item) >= 4 and item[0] == field:
            value = item[3]
            if isinstance(value, list):
                value = " ".join(str(v) for v in value if v)
            return str(value).strip() or None
    return None


def _descriptions(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for remark in data.get("remarks") or []:
        lines.extend(str(line) for line in remark.get("description") or [] if line)
    return lines


class RdapProvider(EnrichmentProvider):
    name = "rdap"
    label = "RDAP"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = report.target.address
        bootstrap, cached_b = await ctx.http.get_json("rdap-bootstrap", BOOTSTRAP[address.version], ttl=BOOTSTRAP_TTL)
        base = pick_server(bootstrap or {}, address)
        if base is None:
            raise ProviderError(self.name, f"no RDAP server registered for {address}")
        data, cached = await ctx.http.get_json(
            self.name,
            base.rstrip("/") + f"/ip/{address}",
            headers={"Accept": "application/rdap+json"},
            not_found_ok=True,
        )
        result = EnrichmentResult(cached=cached and cached_b)
        if not isinstance(data, dict):
            return result

        rir = next((v for k, v in RIR_BY_HOST.items() if k in base), None)
        # Only the registrant names the holder; administrative contacts are often maintainer objects.
        holder = next(
            (
                _vcard_value(entity, "fn")
                for entity in data.get("entities") or []
                if "registrant" in (entity.get("roles") or [])
            ),
            None,
        )
        reg_type = str(data.get("type") or "").upper()
        customer = any(t in reg_type for t in CUSTOMER_TYPES) and bool(data.get("parentHandle"))
        descriptions = _descriptions(data)
        country = (data.get("country") or "").upper() or None
        result.network = NetworkInfo(rir=rir, netname=data.get("name"), holder=holder, rir_country=country)

        block = f"{data.get('startAddress')} - {data.get('endAddress')}"
        parts = [f"{data.get('name') or data.get('handle')} ({block})"]
        if holder:
            parts.append(f"registered to {holder}")
        if country:
            parts.append(f"country {country}")
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.SOFT if country else EvidenceKind.INFO,
                summary="; ".join(parts),
                country=country,
                weight=0.25,
                details={
                    "rir": rir,
                    "handle": data.get("handle"),
                    "name": data.get("name"),
                    "type": data.get("type"),
                    "start": data.get("startAddress"),
                    "end": data.get("endAddress"),
                    "parent": data.get("parentHandle"),
                    "holder": holder,
                    "country": country,
                    "descriptions": descriptions[:10],
                    "customer_assignment": customer,
                },
            )
        )
        if customer:
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.INFO,
                    summary=f"Sub-allocated to a customer ({data.get('type')}): {holder or data.get('name')}",
                    details={"type": data.get("type"), "holder": holder},
                )
            )
        return result
