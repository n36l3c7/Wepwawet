"""Traceroute analysis: locate hops, find where paths converge, estimate the access delay, flag oddities.

- A hop is placed with, in order: its IXP (address in a peering LAN), then the best hint in its
  hostname that its own RTT allows: a router ``rtt`` ms from the probe cannot be farther than
  ``rtt / 2 × 200`` km from it.
- The convergence point is the router closest to the target that most paths share. Routers
  are shared by address, so a path that enters the target's network elsewhere does not count.
- The access delay is the RTT step from the last router to the target (median over paths): it
  is the access line (fibre, xDSL, mobile...) and is part of every probe's RTT.
- Flags: MPLS or hidden hops (big RTT jump across silent hops), RTT that falls along the path
  (asymmetric return path), and an unresponsive tail (the target never answered).
"""

import ipaddress
import statistics
from collections.abc import Sequence

from wepwawet.core.geo import haversine, sol_max_distance_km
from wepwawet.core.geohints import HintData, extract_hints
from wepwawet.core.ixp import IxpIndex
from wepwawet.core.models import AccessDelay, Convergence, HopReport, NetworkClass, TracerouteReport
from wepwawet.providers.measurement.base import RawHop, TraceResult

HINT_SLACK_KM = 50.0


def is_public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


MPLS_JUMP_MS = 15.0
ASYMMETRY_MS = 5.0


def locate_hop(
    hop: RawHop,
    probe_lat: float,
    probe_lon: float,
    hints: HintData | None,
    ixps: IxpIndex | None,
    speed: float,
) -> HopReport:
    found_hints = tuple(extract_hints(hop.hostname, hints)) if hints and hop.hostname else ()
    report = HopReport(
        ttl=hop.ttl,
        address=hop.address,
        hostname=hop.hostname,
        asn=hop.asn,
        min_rtt=hop.min_rtt,
        hints=found_hints,
    )
    exchange = ixps.lookup(hop.address) if ixps and hop.address else None
    if exchange is not None:
        label = f"{exchange.name} ({exchange.city})"
        if exchange.point is not None:
            return report.model_copy(
                update={
                    "ixp": exchange.name,
                    "location": exchange.point,
                    "location_source": "ixp",
                    "location_label": label,
                }
            )
        report = report.model_copy(update={"ixp": exchange.name})
    limit = sol_max_distance_km(hop.min_rtt, speed) + HINT_SLACK_KM if hop.min_rtt is not None else None
    for hint in found_hints:  # most trusted first
        distance = haversine(probe_lat, probe_lon, hint.point.lat, hint.point.lon)
        if limit is None and hint.confidence < 0.8:
            continue
        if limit is None or distance <= limit:
            return report.model_copy(
                update={
                    "location": hint.point,
                    "location_source": "hint",
                    "location_label": f"{hint.name}, {hint.country}" if hint.country else hint.name,
                }
            )
    return report


def flag_path(hops: Sequence[HopReport], reached: bool) -> list[str]:
    flags: list[str] = []
    answered = [h for h in hops if h.min_rtt is not None]
    for previous, current in zip(answered, answered[1:], strict=False):
        gap = current.ttl - previous.ttl
        jump = (current.min_rtt or 0) - (previous.min_rtt or 0)
        if jump >= MPLS_JUMP_MS and gap > 1 and "mpls_or_hidden_hops" not in flags:
            flags.append("mpls_or_hidden_hops")
        if jump <= -ASYMMETRY_MS and "rtt_decreases" not in flags:
            flags.append("rtt_decreases")
    if not reached:
        tail = 0
        for hop in reversed(hops):
            if hop.address is not None:
                break
            tail += 1
        flags.append("unresponsive_tail" if tail >= 2 else "target_not_reached")
    return flags


def analyse_traces(
    traces: Sequence[TraceResult],
    target: str,
    hints: HintData | None,
    ixps: IxpIndex | None,
    speed: float,
) -> list[TracerouteReport]:
    reports = []
    for trace in traces:
        lat, lon = trace.probe.location.lat, trace.probe.location.lon
        hops = tuple(locate_hop(h, lat, lon, hints, ixps, speed) for h in trace.hops)
        reports.append(
            TracerouteReport(
                probe=trace.probe,
                protocol=trace.protocol,
                reached=trace.reached,
                hops=hops,
                flags=tuple(flag_path(hops, trace.reached)),
            )
        )
    return reports


def _target_rtt(trace: TracerouteReport, target: str) -> float | None:
    rtts = [h.min_rtt for h in trace.hops if h.address == target and h.min_rtt is not None]
    return min(rtts) if rtts else None


def find_convergence(traces: Sequence[TracerouteReport], target: str, speed: float) -> Convergence | None:
    """The router nearest the target that at least two paths (and most of them, if possible) share."""
    paths = [t for t in traces if any(h.address and h.address != target for h in t.hops)]
    if len(paths) < 2:
        return None
    seen: dict[str, list[tuple[TracerouteReport, HopReport]]] = {}
    for trace in paths:
        for hop in trace.hops:
            # Private addresses repeat across unrelated networks: two 10.0.0.1 are not one router.
            if hop.address and hop.address != target and is_public(hop.address):
                seen.setdefault(hop.address, []).append((trace, hop))
    shared = {address: entries for address, entries in seen.items() if len({id(t) for t, _ in entries}) >= 2}
    if not shared:
        return None

    def closeness(entries: list[tuple[TracerouteReport, HopReport]]) -> float:
        # Hops before the target: fewer is closer. Average over the paths sharing the router.
        distances = []
        for trace, hop in entries:
            last = max((h.ttl for h in trace.hops if h.address), default=hop.ttl)
            distances.append(last - hop.ttl)
        return statistics.mean(distances)

    best_count = max(len({id(t) for t, _ in e}) for e in shared.values())
    candidates = {a: e for a, e in shared.items() if len({id(t) for t, _ in e}) == best_count}
    address, entries = min(candidates.items(), key=lambda item: closeness(item[1]))
    gaps = []
    for trace, hop in entries:
        target_rtt = _target_rtt(trace, target)
        if target_rtt is not None and hop.min_rtt is not None:
            gaps.append(max(0.0, target_rtt - hop.min_rtt))
    hop = next((h for _, h in entries if h.location is not None), entries[0][1])
    rtt_gap = round(min(gaps), 3) if gaps else None
    return Convergence(
        address=address,
        hostname=hop.hostname,
        paths=best_count,
        of=len(paths),
        location=hop.location,
        label=hop.location_label,
        rtt_to_target_ms=rtt_gap,
        max_distance_km=round(sol_max_distance_km(rtt_gap, speed) + 20, 1) if rtt_gap is not None else None,
    )


def access_technology(delay_ms: float, classes: Sequence[NetworkClass]) -> str:
    if NetworkClass.MOBILE in classes:
        return "mobile"
    if delay_ms < 0.5:
        return "datacentre or LAN"
    if delay_ms < 3:
        return "fibre (FTTH) or Ethernet"
    if delay_ms < 15:
        return "cable or xDSL"
    return "mobile or fixed wireless, or a congested line"


def estimate_access(
    traces: Sequence[TracerouteReport], target: str, classes: Sequence[NetworkClass]
) -> AccessDelay | None:
    steps = []
    for trace in traces:
        target_rtt = _target_rtt(trace, target)
        before = [h for h in trace.hops if h.address and h.address != target and h.min_rtt is not None]
        # The step only measures the access line when the last router is in the public network:
        # a private last hop (home gateway, container bridge) says nothing about it.
        if target_rtt is not None and len(before) >= 2 and is_public(before[-1].address or ""):
            steps.append(max(0.0, target_rtt - before[-1].min_rtt))  # type: ignore[operator]
    if not steps:
        return None
    delay = round(statistics.median(steps), 2)
    return AccessDelay(delay_ms=delay, technology=access_technology(delay, classes), paths=len(steps))
