"""Reverse DNS: the PTR of the target and, for IPv4, a PTR-only sweep of its /24.

Neighbour names often reveal the operator's naming scheme (``host-12.ftth.mi.example.net``)
even when the target itself has no PTR. The sweep sends DNS queries only, paced, and is
cached per /24.
"""

import asyncio
import ipaddress
import re
from collections import Counter

from wepwawet.core.models import Evidence, EvidenceKind, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

SWEEP_CONCURRENCY = 16

_IP_DASHED = re.compile(r"\d{1,3}[-.]\d{1,3}[-.]\d{1,3}[-.]\d{1,3}")
_DIGITS = re.compile(r"\d+")
_HEX_RUN = re.compile(r"\b[0-9a-f]{6,}\b")


def name_pattern(name: str) -> str:
    """Generalise a PTR name: embedded addresses become ``{ip}``, number runs ``N``."""
    pattern = _IP_DASHED.sub("{ip}", name.lower())
    pattern = _HEX_RUN.sub("{hex}", pattern)
    return _DIGITS.sub("N", pattern)


def summarise_patterns(names: list[str], top: int = 5) -> list[tuple[str, int]]:
    return Counter(name_pattern(n) for n in names).most_common(top)


class ReverseDnsProvider(EnrichmentProvider):
    name = "rdns"
    label = "Reverse DNS"

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = report.target.address
        cache = ctx.http.cache
        ttl = ctx.http.ttl_for(self.name)
        result = EnrichmentResult()

        names = cache.get(self.name, f"ptr:{address}")
        result.cached = names is not None
        if names is None:
            names = await ctx.dns.ptr(address)
            cache.set(self.name, f"ptr:{address}", names, ttl)
        if names:
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.INFO,
                    summary=f"PTR {', '.join(names)}",
                    details={"ptr": names},
                )
            )

        if address.version == 4 and ctx.config.provider(self.name).option("sweep", True):
            network = ipaddress.IPv4Network(f"{address}/24", strict=False)
            sweep = cache.get(self.name, f"sweep:{network}")
            if sweep is None:
                result.cached = False
                sweep = await self._sweep(network, ctx)
                cache.set(self.name, f"sweep:{network}", sweep, ttl)
            neighbour_names = [n for host, found in sweep.items() if host != str(address) for n in found]
            if neighbour_names:
                patterns = summarise_patterns(neighbour_names)
                answered = sum(1 for found in sweep.values() if found)
                shown = "; ".join(f"{p} ×{c}" for p, c in patterns[:3])
                result.evidence.append(
                    Evidence(
                        source=self.name,
                        kind=EvidenceKind.INFO,
                        summary=f"{answered}/{len(sweep)} neighbours in {network} have a PTR: {shown}",
                        details={
                            "network": str(network),
                            "answered": answered,
                            "queried": len(sweep),
                            "patterns": [{"pattern": p, "count": c} for p, c in patterns],
                            "examples": sorted(neighbour_names)[:12],
                        },
                    )
                )
        return result

    async def _sweep(self, network: ipaddress.IPv4Network, ctx: ProviderContext) -> dict[str, list[str]]:
        slots = asyncio.Semaphore(SWEEP_CONCURRENCY)

        async def one(host: ipaddress.IPv4Address) -> tuple[str, list[str]]:
            async with slots:
                return str(host), await ctx.dns.ptr(host)

        pairs = await asyncio.gather(*(one(h) for h in network.hosts()))
        return dict(pairs)
