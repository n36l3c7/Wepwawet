"""Evidence fusion: one estimate, a confidence and a plain-language explanation.

Hard evidence (the RTT region) decides where the target can be. Soft evidence (GeoIP answers,
RIPE IPmap, router names, convergence, facilities, registration) ranks the places inside it.
Soft answers that fall outside the region are contradicted by physics and are dropped, with a
note saying so.

When the region is small (radius up to 50 km), its centre is kept as the position and the best
candidate only names the place: GeoIP answers are city centres, coarser than such a measurement.

Scoring: every candidate place (the cities in the region, and each soft answer that lies in it)
gets ``sum(weight × exp(-d² / 2σ²))`` over the soft evidence, where ``d`` is the distance from
the candidate to the answer and ``σ`` the answer's own accuracy (at least 25 km). The best
candidate is the estimate. Confidence starts from the size of the region and rises with
independent sources agreeing; anycast, mobile networks and hosting lower it.
"""

import math
from dataclasses import dataclass

from wepwawet.core.geo import haversine, spherical_mean
from wepwawet.core.models import (
    Estimate,
    Evidence,
    EvidenceKind,
    GeoPoint,
    NetworkClass,
    Region,
    TargetReport,
)
from wepwawet.core.multilateration import area_confidence

MIN_SIGMA_KM = 25.0
# Below this radius the RTT region is more precise than GeoIP-style answers, which are city centres.
PRECISE_REGION_KM = 50.0
AGREEMENT_KM = 60.0
# Weight of each kind of soft answer when the provider gave none of its own.
SOURCE_WEIGHT = {
    "geoip": 0.5,
    "ipmap": 0.6,
    "shodan": 0.3,
    "censys": 0.3,
    "peeringdb": 0.12,
    "traceroute": 0.7,
}
CLASS_PENALTY = {
    NetworkClass.MOBILE: 15,
    NetworkClass.HOSTING: 5,
    NetworkClass.VPN: 10,
    NetworkClass.CDN: 10,
}


@dataclass(frozen=True)
class Candidate:
    name: str | None
    country: str | None
    point: GeoPoint
    origin: str  # "city", "evidence", "centroid"


def _sigma(evidence: Evidence) -> float:
    return max(MIN_SIGMA_KM, evidence.radius_km or 0.0)


def _inside(point: GeoPoint, region: Region | None) -> bool:
    if region is None:
        return True
    return haversine(point.lat, point.lon, region.centroid.lat, region.centroid.lon) <= region.radius_km


def soft_points(report: TargetReport) -> list[Evidence]:
    return [e for e in report.evidence if e.kind is EvidenceKind.SOFT and e.point is not None]


def _score(candidate: Candidate, evidence: list[Evidence]) -> float:
    total = 0.0
    for item in evidence:
        if item.point is None:
            continue
        d = haversine(candidate.point.lat, candidate.point.lon, item.point.lat, item.point.lon)
        sigma = _sigma(item)
        total += (item.weight or SOURCE_WEIGHT.get(item.source, 0.3)) * math.exp(-(d * d) / (2 * sigma * sigma))
    return total


def _candidates(report: TargetReport, evidence: list[Evidence]) -> list[Candidate]:
    region = report.region
    found: list[Candidate] = []
    if region is not None:
        found += [Candidate(c.name, c.country, c.point, "city") for c in region.candidates]
        found.append(Candidate(None, None, region.centroid, "centroid"))
    for item in evidence:
        if item.point is not None and _inside(item.point, region):
            found.append(Candidate(item.city, item.country, item.point, "evidence"))
    return found


def fuse(report: TargetReport) -> Estimate | None:
    """The final estimate for ``report``, replacing the provisional one of the measurements."""
    region = report.region
    located = soft_points(report)
    # Physics overrules databases: soft answers outside the RTT region are dropped.
    kept = [e for e in located if e.point is not None and _inside(e.point, region)]
    # A facility of the origin network far away is not a claim about this address: ignore it quietly.
    dropped = [e for e in located if e not in kept and e.source != "peeringdb"]
    report.evidence = [e for e in report.evidence if e.source != "fusion"]
    for item in dropped:
        where = ", ".join(p for p in (item.city, item.country) if p) or "elsewhere"
        report.evidence.append(
            Evidence(
                source="fusion",
                kind=EvidenceKind.NEGATIVE,
                summary=f"{item.label or item.source} says {where}: outside the RTT region, ignored",
                point=item.point,
                details={"source": item.source, "label": item.label},
            )
        )

    candidates = _candidates(report, kept)
    if not candidates:
        return None
    scored = sorted(((c, _score(c, kept)) for c in candidates), key=lambda cs: cs[1], reverse=True)
    best, best_score = scored[0]
    if region is not None and best_score == 0:
        best = next((c for c, _ in scored if c.origin == "city"), Candidate(None, None, region.centroid, "centroid"))
    if region is not None and region.radius_km <= PRECISE_REGION_KM:
        # The measurements are sharper than any soft answer (often a city centre): keep their centre,
        # and let the best candidate only name the place.
        best = Candidate(best.name, best.country, region.centroid, "centroid")

    supporting = [
        e
        for e in kept
        if e.point and haversine(best.point.lat, best.point.lon, e.point.lat, e.point.lon) <= AGREEMENT_KM
    ]
    sources = sorted({e.source for e in supporting})
    if region is not None:
        uncertainty = region.radius_km
        if supporting:
            # Several independent answers next to each other narrow it, never below 10 km.
            spread = max(
                (haversine(best.point.lat, best.point.lon, e.point.lat, e.point.lon) for e in supporting if e.point),
                default=0.0,
            )
            uncertainty = max(10.0, min(region.radius_km, spread + MIN_SIGMA_KM * (1 + 1 / len(sources))))
        confidence = area_confidence(region.area_km2) + 6 * min(len(sources), 4)
    else:
        if not kept:
            return None
        points = [e.point for e in supporting or kept if e.point]
        centre = spherical_mean(points)
        best = Candidate(best.name, best.country, centre, "evidence")
        uncertainty = max(MIN_SIGMA_KM, *(_sigma(e) for e in supporting or kept))
        confidence = 10 + 7 * min(len(sources), 4)  # passive only: never more than 38

    anycast = bool(report.anycast and report.anycast.detected)
    if anycast:
        confidence = min(confidence, 30)
    confidence -= sum(CLASS_PENALTY.get(c, 0) for c in report.classification)
    city, country = best.name, best.country
    if city is None and region is not None and region.candidates:
        nearest = min(
            region.candidates,
            key=lambda c: haversine(best.point.lat, best.point.lon, c.point.lat, c.point.lon),
        )
        city, country = nearest.name, nearest.country
    return Estimate(
        location=best.point,
        uncertainty_km=round(uncertainty, 1),
        confidence=max(0, min(100, round(confidence))),
        city=city,
        country=country,
        explanation=tuple(explain(report, supporting, dropped, region is not None)),
        anycast=anycast,
    )


def explain(report: TargetReport, supporting: list[Evidence], dropped: list[Evidence], measured: bool) -> list[str]:
    lines: list[str] = []
    region = report.region
    answered = [p for p in report.probes if p.min_rtt is not None]
    if region is not None:
        best = answered[0] if answered else None
        fastest = (
            f"; the fastest probe, in {best.probe.city or best.probe.country}, answered in {best.min_rtt:.1f} ms"
            if best and best.min_rtt is not None
            else ""
        )
        lines.append(f"{len(answered)} probes leave a {region.area_km2:,.0f} km² region{fastest}.")
    elif not measured:
        lines.append("No measurement: this estimate rests on passive sources only.")
    if report.anycast and report.anycast.detected:
        lines.append(f"{report.anycast.summary}; the estimate is the site nearest the fastest probe.")
    conv = report.convergence
    if conv is not None:
        where = f" in {conv.label}" if conv.label else ""
        lines.append(f"{conv.paths} of {conv.of} traceroutes meet at {conv.hostname or conv.address}{where}.")
    if report.access is not None:
        lines.append(f"The last mile adds about {report.access.delay_ms:.1f} ms ({report.access.technology}).")
    agreeing = [e.label or e.source for e in supporting if e.source not in ("traceroute", "peeringdb")]
    facilities = sum(1 for e in supporting if e.source == "peeringdb")
    if facilities:
        agreeing.append(f"{facilities} data centre{'s' if facilities != 1 else ''} of the origin network (PeeringDB)")
    if agreeing:
        lines.append(f"Agreeing nearby: {', '.join(dict.fromkeys(agreeing))}.")
    for item in dropped[:3]:
        where = ", ".join(p for p in (item.city, item.country) if p) or "elsewhere"
        lines.append(f"{item.label or item.source} says {where}, which the RTTs rule out.")
    return lines
