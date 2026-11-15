"""Ping from this machine and from your own servers over SSH.

Both need to know where the vantage point is: set ``providers.local.lat`` / ``lon`` for this
machine, and ``lat`` / ``lon`` for each host in ``providers.ssh.hosts``. The target is always a
validated IP literal, so it never reaches a shell as anything else.
"""

import asyncio
import json
import shutil
import sys
from collections.abc import Sequence
from typing import Any

from wepwawet.core.http import ProviderError
from wepwawet.core.models import GeoPoint, Probe, Target
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import (
    LocationSpec,
    MeasurementProvider,
    PingBatch,
    TraceBatch,
    TraceResult,
    parse_mtr_json,
    parse_ping_output,
    parse_traceroute_output,
    ping_result,
)

COMMAND_TIMEOUT = 30.0


def ping_argv(target: Target, packets: int) -> list[str]:
    if sys.platform == "win32":
        return ["ping", "-n", str(packets), "-w", "2000", str(target.address)]
    flag = "-6" if target.address.version == 6 else "-4"
    return ["ping", flag, "-n", "-c", str(packets), "-W", "2", "-i", "0.3", str(target.address)]


async def run_command(argv: list[str], timeout: float = COMMAND_TIMEOUT) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        process.kill()
        raise ProviderError(argv[0], f"no answer within {timeout:.0f} s") from exc
    return stdout.decode(errors="replace")


def traceroute_argv(target: Target, use_mtr: bool) -> list[str]:
    flag = "-6" if target.address.version == 6 else "-4"
    if use_mtr:
        return ["mtr", flag, "--json", "--no-dns", "--report-cycles", "3", str(target.address)]
    return ["traceroute", flag, "-n", "-q", "3", "-w", "2", str(target.address)]


def parse_trace(output: str, use_mtr: bool) -> list[Any]:
    if use_mtr:
        try:
            return parse_mtr_json(json.loads(output))
        except ValueError:
            return []
    return parse_traceroute_output(output)


def _location(settings: dict[str, Any]) -> GeoPoint | None:
    lat, lon = settings.get("lat"), settings.get("lon")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return GeoPoint(lat=lat, lon=lon)
    return None


class LocalPingProvider(MeasurementProvider):
    name = "local"
    label = "This machine"
    selectable = False
    supports_traceroute = True
    max_probes = 1

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        settings = ctx.config.provider(self.name).model_extra or {}
        if _location(settings) is None:
            return "set providers.local.lat and lon (where this machine is) to use it"
        if shutil.which("ping") is None:
            return "no ping command"
        return None

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        settings = ctx.config.provider(self.name).model_extra or {}
        location = _location(settings)
        if location is None:
            return PingBatch(provider=self.name)
        output = await run_command(ping_argv(target, packets))
        rtts, sent, _ = parse_ping_output(output)
        probe = Probe(provider=self.name, probe_id="local", location=location, city=settings.get("city"))
        return PingBatch(provider=self.name, results=[ping_result(probe, rtts, sent or packets)])

    async def traceroute(
        self,
        target: Target,
        locations: Sequence[LocationSpec],
        ctx: ProviderContext,
        protocol: str = "ICMP",
        port: int | None = None,
    ) -> TraceBatch:
        settings = ctx.config.provider(self.name).model_extra or {}
        location = _location(settings)
        use_mtr = shutil.which("mtr") is not None
        if location is None or (not use_mtr and shutil.which("traceroute") is None) or sys.platform == "win32":
            return TraceBatch(provider=self.name)
        hops = parse_trace(await run_command(traceroute_argv(target, use_mtr), timeout=90), use_mtr)
        probe = Probe(provider=self.name, probe_id="local", location=location, city=settings.get("city"))
        reached = any(h.address == str(target.address) and h.rtts for h in hops)
        return TraceBatch(
            provider=self.name, results=[TraceResult(probe=probe, protocol="ICMP", hops=tuple(hops), reached=reached)]
        )


class SshPingProvider(MeasurementProvider):
    """``[[providers.ssh.hosts]]`` entries: ``name``, ``host``, optional ``user`` and ``port``, ``lat``, ``lon``."""

    name = "ssh"
    label = "SSH probes"
    selectable = False
    max_probes = 50

    def hosts(self, ctx: ProviderContext) -> list[dict[str, Any]]:
        raw = ctx.config.provider(self.name).option("hosts", [])
        return (
            [h for h in raw if isinstance(h, dict) and h.get("host") and _location(h)] if isinstance(raw, list) else []
        )

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        if not self.hosts(ctx):
            return "no [[providers.ssh.hosts]] with host, lat and lon configured"
        if shutil.which("ssh") is None:
            return "no ssh command"
        return None

    @staticmethod
    def argv(host: dict[str, Any], target: Target, packets: int) -> list[str]:
        destination = f"{host['user']}@{host['host']}" if host.get("user") else str(host["host"])
        flag = "-6" if target.address.version == 6 else "-4"
        remote = f"ping {flag} -n -c {int(packets)} -W 2 -i 0.3 {target.address}"
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        if host.get("port"):
            argv += ["-p", str(int(host["port"]))]
        # "--" ends the options: a host name can never be read as one.
        return [*argv, "--", destination, remote]

    async def ping(
        self,
        target: Target,
        locations: Sequence[LocationSpec] | str,
        packets: int,
        ctx: ProviderContext,
        tcp_port: int | None = None,
    ) -> PingBatch:
        hosts = self.hosts(ctx)

        async def one(host: dict[str, Any]) -> Any:
            try:
                output = await run_command(self.argv(host, target, packets))
            except ProviderError as exc:
                ctx.log("error", self.name, f"{host.get('name', host['host'])}: {exc.message}")
                return None
            rtts, sent, _ = parse_ping_output(output)
            location = _location(host)
            if location is None:  # hosts() already filters these out
                return None
            probe = Probe(
                provider=self.name,
                probe_id=str(host.get("name") or host["host"]),
                location=location,
                city=host.get("city"),
            )
            return ping_result(probe, rtts, sent or packets)

        results = [r for r in await asyncio.gather(*(one(h) for h in hosts)) if r is not None]
        return PingBatch(provider=self.name, results=results)
