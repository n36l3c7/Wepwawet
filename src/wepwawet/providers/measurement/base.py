"""Measurement provider interface: ping (and, from v0.4.0, traceroute) from many vantage points."""

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from wepwawet.core.models import Probe, Target
from wepwawet.providers.base import ProviderContext


@dataclass(frozen=True)
class LocationSpec:
    """Where to pick probes. Providers use the fields they understand."""

    limit: int = 1
    continent: str | None = None
    country: str | None = None
    city: str | None = None
    asn: int | None = None
    magic: str | None = None


@dataclass(frozen=True)
class PingResult:
    probe: Probe
    rtts: tuple[float, ...]
    packets: int
    min_rtt: float | None
    avg_rtt: float | None
    loss: float | None  # 0..1


@dataclass
class PingBatch:
    """The answers of one measurement. ``reuse_key`` lets a provider target the same probes again."""

    provider: str
    results: list[PingResult] = field(default_factory=list)
    reuse_key: str | None = None
    tests_used: int = 0


@dataclass(frozen=True)
class RawHop:
    ttl: int
    address: str | None  # None: no reply at this TTL
    hostname: str | None = None
    asn: int | None = None
    rtts: tuple[float, ...] = ()

    @property
    def min_rtt(self) -> float | None:
        return min(self.rtts) if self.rtts else None


@dataclass(frozen=True)
class TraceResult:
    probe: Probe
    protocol: str
    hops: tuple[RawHop, ...]
    reached: bool


@dataclass
class TraceBatch:
    provider: str
    results: list[TraceResult] = field(default_factory=list)
    tests_used: int = 0


@dataclass(frozen=True)
class Budget:
    provider: str
    unit: str  # "tests/hour" or "credits"
    limit: int | None
    remaining: int | None
    reset_seconds: int | None = None


class MeasurementProvider(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    required_secrets: ClassVar[tuple[str, ...]] = ()
    max_probes: ClassVar[int] = 50
    # True when probes can be chosen by location (Globalping, RIPE Atlas); False for fixed ones (local, SSH).
    selectable: ClassVar[bool] = True
    # True when ``ping`` can measure a TCP handshake instead of ICMP echo (for targets that drop ICMP).
    supports_tcp: ClassVar[bool] = False

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        missing = [n for n in self.required_secrets if not ctx.secrets.has(n)]
        if missing:
            return f"no API key ({', '.join('WEPWAWET_' + n.upper() for n in missing)})"
        return None

    @abstractmethod
    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        """Ping ``target`` from probes matching ``locations`` (or from the probes of a reuse key).

        With ``tcp_port``, measure the TCP handshake to that port instead of ICMP echo.
        """

    async def traceroute(
        self,
        target: Target,
        locations: Sequence[LocationSpec],
        ctx: ProviderContext,
        protocol: str = "ICMP",
        port: int | None = None,
    ) -> TraceBatch:
        """Trace the path to ``target`` from probes matching ``locations``. Not every provider can."""
        return TraceBatch(provider=self.name)

    supports_traceroute: ClassVar[bool] = False

    async def budget(self, ctx: ProviderContext) -> Budget | None:
        return None


_SUMMARY = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)")
_REPLY = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
_TRANSMITTED = re.compile(r"(\d+)\s+packets transmitted,\s*(\d+)\s+(?:packets )?received", re.IGNORECASE)


def parse_ping_output(text: str) -> tuple[tuple[float, ...], int, int]:
    """RTTs, packets sent and received from the output of iputils/BSD ``ping``."""
    rtts = tuple(float(m) for m in _REPLY.findall(text))
    sent = received = len(rtts)
    totals = _TRANSMITTED.search(text)
    if totals:
        sent, received = int(totals.group(1)), int(totals.group(2))
    return rtts, sent, received


_TRACEROUTE_LINE = re.compile(r"^\s*(\d+)\s+(.*)$")
_HOP_ADDRESS = re.compile(r"^(\S+)")
_HOP_RTT = re.compile(r"([\d.]+)\s*ms")


def parse_traceroute_output(text: str) -> list[RawHop]:
    """Hops from ``traceroute -n`` output: ``2  192.0.2.1  1.2 ms  1.1 ms  1.3 ms`` or ``3  * * *``."""
    hops = []
    for line in text.splitlines():
        match = _TRACEROUTE_LINE.match(line)
        if not match:
            continue
        ttl, rest = int(match.group(1)), match.group(2).strip()
        if rest.startswith("*") and "ms" not in rest:
            hops.append(RawHop(ttl=ttl, address=None))
            continue
        address_match = _HOP_ADDRESS.match(rest.replace("*", "").strip())
        rtts = tuple(float(v) for v in _HOP_RTT.findall(rest))
        hops.append(RawHop(ttl=ttl, address=address_match.group(1) if address_match else None, rtts=rtts))
    return hops


def parse_mtr_json(data: dict[str, object]) -> list[RawHop]:
    """Hops from ``mtr --json`` (``report.hubs``): ``Best`` is the minimum RTT, ``???`` a silent hop."""
    report = data.get("report") if isinstance(data, dict) else None
    hubs = report.get("hubs", []) if isinstance(report, dict) else []
    hops = []
    for index, hub in enumerate(hubs if isinstance(hubs, list) else [], start=1):
        if not isinstance(hub, dict):
            continue
        host = str(hub.get("host") or "")
        best = hub.get("Best")
        received = isinstance(best, int | float) and float(hub.get("Loss%") or 0) < 100
        address = None if host in ("???", "") else host
        hops.append(
            RawHop(
                ttl=int(hub.get("count") or index),
                address=address,
                rtts=(float(best),) if received and address and isinstance(best, int | float) else (),
            )
        )
    return hops


def ping_result(probe: Probe, rtts: Sequence[float], sent: int) -> PingResult:
    values = tuple(rtts)
    return PingResult(
        probe=probe,
        rtts=values,
        packets=sent,
        min_rtt=min(values) if values else None,
        avg_rtt=round(sum(values) / len(values), 3) if values else None,
        loss=(1 - len(values) / sent) if sent else None,
    )
