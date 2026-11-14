"""PeeringDB: where the origin network says it is present (facilities, exchange points).

Facilities with coordinates become candidate locations on the map. The anonymous API is rate
limited, so requests are paced and every answer is cached; an API key raises the limit.
"""

from typing import Any

from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

BASE_URL = "https://www.peeringdb.com/api"
MAX_IDS_PER_QUERY = 100


def peeringdb_headers(ctx: ProviderContext) -> dict[str, str]:
    key = ctx.secrets.get("peeringdb")
    return {"Authorization": f"Api-Key {key}"} if key else {}


async def peeringdb_get(ctx: ProviderContext, path: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    # Anonymous use allows a few requests per minute; keep well under it.
    ctx.http.configure_pacing("peeringdb", 1.0 if ctx.secrets.has("peeringdb") else 3.0)
    data, cached = await ctx.http.get_json(
        "peeringdb", f"{BASE_URL}/{path}", params=params, headers=peeringdb_headers(ctx)
    )
    rows = data.get("data") if isinstance(data, dict) else None
    return (rows if isinstance(rows, list) else []), cached


async def peeringdb_by_ids(ctx: ProviderContext, path: str, ids: list[int]) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    cached_all = True
    for start in range(0, len(ids), MAX_IDS_PER_QUERY):
        chunk = ",".join(str(i) for i in sorted(ids)[start : start + MAX_IDS_PER_QUERY])
        part, cached = await peeringdb_get(ctx, path, {"id__in": chunk})
        rows.extend(part)
        cached_all = cached_all and cached
    return rows, cached_all


class PeeringDbProvider(EnrichmentProvider):
    name = "peeringdb"
    label = "PeeringDB"
    stage = 2  # needs the origin ASN

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        asn = report.network.asn if report.network else None
        result = EnrichmentResult(cached=True)
        if asn is None:
            result.evidence.append(
                Evidence(source=self.name, kind=EvidenceKind.INFO, summary="Origin AS unknown; PeeringDB not queried")
            )
            return result
        nets, cached = await peeringdb_get(ctx, "net", {"asn": asn})
        result.cached &= cached
        if not nets:
            result.evidence.append(
                Evidence(source=self.name, kind=EvidenceKind.INFO, summary=f"AS{asn} has no PeeringDB record")
            )
            return result
        net = nets[0]
        info_types = net.get("info_types") or ([net["info_type"]] if net.get("info_type") else [])
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.INFO,
                summary=f"AS{asn} {net.get('name')}: {', '.join(info_types) or 'type not given'}"
                + (f", scope {net['info_scope']}" if net.get("info_scope") else ""),
                details={
                    "net_id": net.get("id"),
                    "name": net.get("name"),
                    "info_types": info_types,
                    "info_scope": net.get("info_scope"),
                    "website": net.get("website"),
                },
            )
        )

        netfac, cached = await peeringdb_get(ctx, "netfac", {"net_id": net["id"]})
        result.cached &= cached
        facilities, cached = await peeringdb_by_ids(ctx, "fac", [row["fac_id"] for row in netfac if "fac_id" in row])
        result.cached &= cached
        for fac in facilities:
            lat, lon = fac.get("latitude"), fac.get("longitude")
            where = ", ".join(p for p in (fac.get("city"), fac.get("country")) if p)
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.SOFT,
                    label=fac.get("name"),
                    summary=f"Present at {fac.get('name')} ({where})",
                    point=GeoPoint(lat=lat, lon=lon) if lat is not None and lon is not None else None,
                    city=fac.get("city"),
                    country=fac.get("country"),
                    weight=0.15,
                    details={"type": "facility", "fac_id": fac.get("id"), "name": fac.get("name")},
                )
            )

        netixlan, cached = await peeringdb_get(ctx, "netixlan", {"net_id": net["id"]})
        result.cached &= cached
        exchanges, cached = await peeringdb_by_ids(
            ctx, "ix", sorted({row["ix_id"] for row in netixlan if "ix_id" in row})
        )
        result.cached &= cached
        if exchanges:
            listed = [
                {"ix_id": ix.get("id"), "name": ix.get("name"), "city": ix.get("city"), "country": ix.get("country")}
                for ix in sorted(exchanges, key=lambda ix: str(ix.get("name")))
            ]
            names = ", ".join(f"{ix['name']} ({ix['city']})" for ix in listed[:5])
            more = f" and {len(listed) - 5} more" if len(listed) > 5 else ""
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.INFO,
                    summary=f"Peers at {len(listed)} exchange{'s' if len(listed) != 1 else ''}: {names}{more}",
                    details={"type": "ixps", "exchanges": listed},
                )
            )
        return result
