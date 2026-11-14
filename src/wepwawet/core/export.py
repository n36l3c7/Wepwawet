"""Export a run: JSON, GeoJSON, KML, Markdown report and CSV (hops, or one row per target for bulk runs)."""

import csv
import io
import json
from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape

from wepwawet import __version__
from wepwawet.core.geo import destination
from wepwawet.core.models import GeoPoint, RunReport, TargetReport

FORMATS = {
    "json": ("application/json", "json"),
    "geojson": ("application/geo+json", "geojson"),
    "kml": ("application/vnd.google-earth.kml+xml", "kml"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "csv": ("text/csv; charset=utf-8", "csv"),
}


def circle(centre: GeoPoint, radius_km: float, steps: int = 64) -> list[list[float]]:
    ring = []
    for i in range(steps + 1):
        point = destination(centre, 360 * i / steps, radius_km)
        ring.append([round(point.lon, 6), round(point.lat, 6)])
    return ring


def _feature(geometry: dict[str, Any], **properties: Any) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def target_features(target: TargetReport) -> list[dict[str, Any]]:
    address = str(target.target.address)
    features: list[dict[str, Any]] = []
    e = target.estimate
    if e is not None:
        features.append(
            _feature(
                {"type": "Point", "coordinates": [e.location.lon, e.location.lat]},
                kind="estimate",
                target=address,
                city=e.city,
                country=e.country,
                uncertainty_km=e.uncertainty_km,
                confidence=e.confidence,
                anycast=e.anycast,
            )
        )
        features.append(
            _feature(
                {"type": "Polygon", "coordinates": [circle(e.location, e.uncertainty_km)]},
                kind="uncertainty",
                target=address,
            )
        )
    if target.region is not None:
        features.append(
            _feature(target.region.geometry, kind="region", target=address, area_km2=target.region.area_km2)
        )
    for item in target.evidence:
        if item.point is not None:
            features.append(
                _feature(
                    {"type": "Point", "coordinates": [item.point.lon, item.point.lat]},
                    kind="evidence",
                    target=address,
                    source=item.source,
                    evidence=item.kind.value,
                    label=item.label,
                    summary=item.summary,
                )
            )
    for probe in target.probes:
        features.append(
            _feature(
                {"type": "Point", "coordinates": [probe.probe.location.lon, probe.probe.location.lat]},
                kind="probe",
                target=address,
                provider=probe.probe.provider,
                city=probe.probe.city,
                min_rtt_ms=probe.min_rtt,
                max_distance_km=probe.max_distance_km,
            )
        )
    for trace in target.traceroutes:
        line = [[trace.probe.location.lon, trace.probe.location.lat]]
        line += [[h.location.lon, h.location.lat] for h in trace.hops if h.location is not None]
        if e is not None:
            line.append([e.location.lon, e.location.lat])
        if len(line) >= 2:
            features.append(
                _feature(
                    {"type": "LineString", "coordinates": line},
                    kind="path",
                    target=address,
                    probe=trace.probe.city or trace.probe.probe_id,
                    protocol=trace.protocol,
                )
            )
    return features


def to_json(run: RunReport) -> str:
    return json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False)


def to_geojson(run: RunReport) -> str:
    features = [f for target in run.targets for f in target_features(target)]
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=1, ensure_ascii=False)


def _kml_placemark(name: str, description: str, geometry: str) -> str:
    return (
        f"<Placemark><name>{escape(name)}</name><description>{escape(description)}</description>{geometry}</Placemark>"
    )


def _kml_ring(coordinates: Sequence[Sequence[float]]) -> str:
    text = " ".join(f"{lon},{lat}" for lon, lat in coordinates)
    return f"<LinearRing><coordinates>{text}</coordinates></LinearRing>"


def to_kml(run: RunReport) -> str:
    marks: list[str] = []
    for target in run.targets:
        address = str(target.target.address)
        e = target.estimate
        if e is not None:
            where = ", ".join(p for p in (e.city, e.country) if p) or address
            marks.append(
                _kml_placemark(
                    f"{address}: {where}",
                    f"±{e.uncertainty_km:.0f} km, confidence {e.confidence}. " + " ".join(e.explanation),
                    f"<Point><coordinates>{e.location.lon},{e.location.lat}</coordinates></Point>",
                )
            )
        if target.region is not None:
            polygons = "".join(
                f"<Polygon><outerBoundaryIs>{_kml_ring(polygon[0])}</outerBoundaryIs></Polygon>"
                for polygon in target.region.geometry.get("coordinates", [])
            )
            marks.append(
                _kml_placemark(
                    f"{address}: RTT region",
                    f"{target.region.area_km2:,.0f} km²",
                    f"<MultiGeometry>{polygons}</MultiGeometry>",
                )
            )
        for item in target.evidence:
            if item.point is not None:
                marks.append(
                    _kml_placemark(
                        item.label or item.source,
                        item.summary,
                        f"<Point><coordinates>{item.point.lon},{item.point.lat}</coordinates></Point>",
                    )
                )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>Wepwawet {escape(run.query)}</name>{''.join(marks)}</Document></kml>\n"
    )


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(run: RunReport) -> str:
    lines = [f"# Wepwawet report: {run.query}", ""]
    lines.append(f"- Run `{run.id}`, started {run.started_at:%Y-%m-%d %H:%M} UTC, status **{run.status.value}**")
    lines.append(f"- Wepwawet {__version__}")
    if run.error:
        lines += ["", f"**Error:** {run.error}"]
    for target in run.targets:
        address = str(target.target.address)
        lines += ["", f"## {address}" + (f" ({target.target.hostname})" if target.target.hostname else ""), ""]
        e = target.estimate
        if e is not None:
            where = ", ".join(p for p in (e.city, e.country) if p) or "unnamed place"
            lines.append(
                f"**Estimate:** {where} at {e.location.lat:.4f}, {e.location.lon:.4f}, "
                f"±{e.uncertainty_km:.0f} km, confidence {e.confidence}/100"
                + (" (anycast: nearest site)" if e.anycast else "")
            )
            lines += [""] + [f"- {line}" for line in e.explanation]
        else:
            lines.append("No estimate.")
        if target.caveats:
            lines += ["", "**Caveats**", ""] + [f"- {c}" for c in target.caveats]
        if target.network:
            n = target.network
            lines += [
                "",
                f"**Network:** {n.prefix or '?'}, AS{n.asn or '?'} {n.as_name or ''}, "
                f"{n.rir or ''} {n.rir_country or ''}".rstrip(),
            ]
        if target.classification:
            lines.append(f"**Class:** {', '.join(c.value for c in target.classification)}")
        lines += ["", "| Source | Kind | Finding |", "|---|---|---|"]
        lines += [f"| {e.source} | {e.kind.value} | {_md_escape(e.summary)} |" for e in target.evidence]
        answered = [p for p in target.probes if p.min_rtt is not None]
        if answered:
            lines += ["", f"**Probes:** {len(answered)} answered of {len(target.probes)}; fastest:", ""]
            lines += ["| Probe | min RTT | d max |", "|---|---|---|"]
            for p in answered[:10]:
                where = ", ".join(x for x in (p.probe.city, p.probe.country) if x) or p.probe.probe_id
                lines.append(f"| {where} | {p.min_rtt:.1f} ms | {p.max_distance_km or 0:,.0f} km |")
    lines += ["", "---", "Generated by Wepwawet. Locations are estimates with the uncertainty stated above.", ""]
    return "\n".join(lines)


def hops_csv(run: RunReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "target",
            "probe_city",
            "probe_country",
            "protocol",
            "ttl",
            "address",
            "hostname",
            "asn",
            "min_rtt_ms",
            "ixp",
            "location",
            "location_source",
        ]
    )
    for target in run.targets:
        for trace in target.traceroutes:
            for hop in trace.hops:
                writer.writerow(
                    [
                        str(target.target.address),
                        trace.probe.city or "",
                        trace.probe.country or "",
                        trace.protocol,
                        hop.ttl,
                        hop.address or "",
                        hop.hostname or "",
                        hop.asn or "",
                        "" if hop.min_rtt is None else f"{hop.min_rtt:.3f}",
                        hop.ixp or "",
                        hop.location_label or "",
                        hop.location_source or "",
                    ]
                )
    return buffer.getvalue()


SUMMARY_HEADER = [
    "query",
    "address",
    "status",
    "city",
    "country",
    "lat",
    "lon",
    "uncertainty_km",
    "confidence",
    "anycast",
    "network_class",
    "asn",
    "error",
]


def summary_rows(runs: Sequence[RunReport]) -> list[list[str]]:
    """One row per address, for bulk runs."""
    rows = []
    for run in runs:
        if not run.targets:
            rows.append([run.query, "", run.status.value] + [""] * 9 + [run.error or ""])
        for target in run.targets:
            e = target.estimate
            rows.append(
                [
                    run.query,
                    str(target.target.address),
                    run.status.value,
                    e.city or "" if e else "",
                    e.country or "" if e else "",
                    f"{e.location.lat:.5f}" if e else "",
                    f"{e.location.lon:.5f}" if e else "",
                    f"{e.uncertainty_km:.1f}" if e else "",
                    str(e.confidence) if e else "",
                    str(e.anycast).lower() if e else "",
                    "+".join(c.value for c in target.classification),
                    str(target.network.asn) if target.network and target.network.asn else "",
                    run.error or "",
                ]
            )
    return rows


def summary_csv(runs: Sequence[RunReport]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SUMMARY_HEADER)
    writer.writerows(summary_rows(runs))
    return buffer.getvalue()


def render(run: RunReport, fmt: str) -> str:
    renderers = {"json": to_json, "geojson": to_geojson, "kml": to_kml, "md": to_markdown, "csv": hops_csv}
    if fmt not in renderers:
        raise ValueError(f"Unknown format {fmt!r}; use one of {', '.join(renderers)}")
    return renderers[fmt](run)
