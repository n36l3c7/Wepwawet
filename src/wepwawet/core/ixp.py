"""Internet exchange peering LANs from PeeringDB.

A router address inside an IXP's peering LAN is at that exchange, a strong location hint. The
full prefix list is downloaded once a day (three requests) and matched locally, which stays far
below PeeringDB's anonymous rate limit.
"""

import ipaddress
from dataclasses import dataclass

from wepwawet.core.geohints import HintData, fold
from wepwawet.core.http import ProviderError
from wepwawet.core.models import GeoPoint
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.enrichment.peeringdb import BASE_URL, peeringdb_headers

LIST_TTL = 86_400


@dataclass(frozen=True)
class Exchange:
    network: ipaddress.IPv4Network | ipaddress.IPv6Network
    name: str
    city: str
    country: str
    point: GeoPoint | None


class IxpIndex:
    def __init__(self, exchanges: list[Exchange]) -> None:
        # Longest prefixes first, so a more specific LAN wins.
        self._exchanges = sorted(exchanges, key=lambda e: e.network.prefixlen, reverse=True)

    def __len__(self) -> int:
        return len(self._exchanges)

    def lookup(self, address: str) -> Exchange | None:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        for exchange in self._exchanges:
            if exchange.network.version == ip.version and ip in exchange.network:
                return exchange
        return None


def build_index(
    prefixes: list[dict[str, object]],
    lans: list[dict[str, object]],
    exchanges: list[dict[str, object]],
    hints: HintData | None,
) -> IxpIndex:
    lan_to_ix = {lan.get("id"): lan.get("ix_id") for lan in lans}
    ix_by_id = {ix.get("id"): ix for ix in exchanges}
    found = []
    for row in prefixes:
        ix = ix_by_id.get(lan_to_ix.get(row.get("ixlan_id")))
        if ix is None or not row.get("prefix"):
            continue
        try:
            network = ipaddress.ip_network(str(row["prefix"]), strict=False)
        except ValueError:
            continue
        city, country = str(ix.get("city") or ""), str(ix.get("country") or "")
        point = None
        if hints is not None:
            match = hints.cities_by_country.get((fold(city.split("/")[0].split(",")[0]), country))
            if match:
                point = GeoPoint(lat=match.lat, lon=match.lon)
        found.append(
            Exchange(network=network, name=str(ix.get("name") or "IXP"), city=city, country=country, point=point)
        )
    return IxpIndex(found)


async def load_ixp_index(ctx: ProviderContext, hints: HintData | None) -> IxpIndex:
    headers = peeringdb_headers(ctx)

    async def rows(path: str) -> list[dict[str, object]]:
        data, _ = await ctx.http.get_json("peeringdb-lists", f"{BASE_URL}/{path}", headers=headers, ttl=LIST_TTL)
        result = data.get("data") if isinstance(data, dict) else None
        return result if isinstance(result, list) else []

    try:
        return build_index(await rows("ixpfx"), await rows("ixlan"), await rows("ix"), hints)
    except ProviderError as exc:
        ctx.log("info", "ixp", f"IXP list unavailable: {exc.message}")
        return IxpIndex([])
