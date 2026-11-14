"""DNS lookups (dnspython, asynchronous): A/AAAA for hostnames and PTR for addresses."""

import asyncio
import ipaddress

import dns.asyncresolver
import dns.exception
import dns.resolver
import dns.reversename

from wepwawet.core.models import IPAddress


class DnsClient:
    def __init__(self, resolver: dns.asyncresolver.Resolver | None = None, timeout: float = 4.0) -> None:
        self._resolver = resolver or dns.asyncresolver.Resolver()
        self._resolver.lifetime = timeout

    async def resolve_host(self, hostname: str) -> list[IPAddress]:
        """Every A and AAAA record of ``hostname`` (empty when it does not exist)."""
        results = await asyncio.gather(self._query(hostname, "A"), self._query(hostname, "AAAA"))
        return [ipaddress.ip_address(rdata) for answer in results for rdata in answer]

    async def ptr(self, address: IPAddress) -> list[str]:
        """PTR names of ``address`` without the trailing dot (empty when there are none)."""
        name = dns.reversename.from_address(str(address)).to_text()
        return [rdata.rstrip(".").lower() for rdata in await self._query(name, "PTR")]

    async def _query(self, name: str, rdtype: str) -> list[str]:
        try:
            answer = await self._resolver.resolve(name, rdtype, raise_on_no_answer=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.resolver.NoAnswer):
            return []
        except dns.exception.Timeout:
            return []
        return [rdata.to_text() for rdata in answer] if answer.rrset is not None else []
