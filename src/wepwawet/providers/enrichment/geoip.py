"""Local GeoIP databases (.mmdb): every database in the data directory answers, and they are compared.

Supported layouts: MaxMind GeoLite2 City / Country / ASN, DB-IP Lite (same layout as MaxMind)
and IPinfo Lite (country and ASN only). Disagreement between databases is itself evidence: it
is reported with the distance between their answers.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import maxminddb

from wepwawet.core.models import Evidence, EvidenceKind, GeoPoint, IPAddress, NetworkInfo, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True)
class GeoipAnswer:
    database: str
    city: str | None = None
    country: str | None = None
    point: GeoPoint | None = None
    accuracy_km: float | None = None
    asn: int | None = None
    as_name: str | None = None


def database_label(path: Path, metadata_type: str) -> str:
    name = metadata_type or path.stem
    lowered = (name + path.stem).lower()
    if "ipinfo" in lowered:
        return "IPinfo Lite"
    if "dbip" in lowered or "db-ip" in lowered:
        return "DB-IP Lite"
    if "geolite2" in lowered:
        return name.replace("GeoLite2-", "GeoLite2 ")
    return name


def parse_record(database: str, record: dict[str, Any]) -> GeoipAnswer:
    """Normalise one record of any supported layout."""
    if "country_code" in record or "as_name" in record:  # IPinfo Lite
        asn_text = str(record.get("asn") or "").upper().removeprefix("AS")
        return GeoipAnswer(
            database=database,
            country=(record.get("country_code") or None),
            asn=int(asn_text) if asn_text.isdigit() else None,
            as_name=record.get("as_name"),
        )
    if "autonomous_system_number" in record:  # GeoLite2 ASN
        return GeoipAnswer(
            database=database,
            asn=record.get("autonomous_system_number"),
            as_name=record.get("autonomous_system_organization"),
        )
    location = record.get("location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    names = (record.get("city") or {}).get("names") or {}
    country = (record.get("country") or record.get("registered_country") or {}).get("iso_code")
    return GeoipAnswer(
        database=database,
        city=names.get("en"),
        country=country,
        point=GeoPoint(lat=lat, lon=lon) if lat is not None and lon is not None else None,
        accuracy_km=location.get("accuracy_radius"),
    )


def lookup_all(directory: Path, address: IPAddress) -> list[GeoipAnswer]:
    answers = []
    for path in sorted(directory.glob("*.mmdb")):
        with maxminddb.open_database(str(path)) as reader:
            record = reader.get(str(address))
            label = database_label(path, reader.metadata().database_type)
        if isinstance(record, dict):
            answers.append(parse_record(label, record))
    return answers


def geoip_dir(ctx: ProviderContext) -> Path:
    return ctx.paths.data_dir / "geoip"


class GeoipProvider(EnrichmentProvider):
    name = "geoip"
    label = "GeoIP databases"

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        if not any(geoip_dir(ctx).glob("*.mmdb")):
            return "no database installed; run 'wepwawet data update'"
        return None

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        answers = lookup_all(geoip_dir(ctx), report.target.address)
        result = EnrichmentResult()
        located = [a for a in answers if a.point is not None]
        for answer in answers:
            if answer.asn is not None and result.network is None:
                result.network = NetworkInfo(asn=answer.asn, as_name=answer.as_name)
            if answer.point is None and answer.country is None:
                continue
            where = ", ".join(p for p in (answer.city, answer.country) if p) or "?"
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.SOFT,
                    label=answer.database,
                    summary=f"{answer.database}: {where}"
                    + (f" (±{answer.accuracy_km:.0f} km)" if answer.accuracy_km else ""),
                    point=answer.point,
                    radius_km=answer.accuracy_km or None,
                    city=answer.city,
                    country=answer.country,
                    weight=0.5 if answer.point else 0.3,
                    details={"database": answer.database, "asn": answer.asn, "as_name": answer.as_name},
                )
            )
        if len(located) >= 2:
            spread = max(
                haversine_km(a.point, b.point)  # type: ignore[arg-type]
                for i, a in enumerate(located)
                for b in located[i + 1 :]
            )
            verdict = "agree" if spread < 50 else "disagree"
            result.evidence.append(
                Evidence(
                    source=self.name,
                    kind=EvidenceKind.INFO,
                    summary=f"{len(located)} databases {verdict}: answers up to {spread:.0f} km apart",
                    details={"spread_km": round(spread, 1), "databases": [a.database for a in located]},
                )
            )
        return result
