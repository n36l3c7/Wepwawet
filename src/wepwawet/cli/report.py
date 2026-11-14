"""Render a run report in the terminal with Rich."""

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from wepwawet.core.models import EvidenceKind, RunReport, RunStatus, TargetReport

KIND_STYLE = {
    EvidenceKind.HARD: "bold yellow",
    EvidenceKind.SOFT: "cyan",
    EvidenceKind.NEGATIVE: "red",
    EvidenceKind.INFO: "dim",
}
MAX_ROWS_PER_SOURCE = 8
STATUS_STYLE = {"ok": "green", "empty": "dim", "skipped": "yellow", "error": "red"}


def target_panel(report: TargetReport) -> Panel:
    parts: list[Table | Text] = []
    if report.estimate:
        e = report.estimate
        where = ", ".join(p for p in (e.city, e.country) if p) or f"{e.location.lat:.4f}, {e.location.lon:.4f}"
        parts.append(
            Text.assemble(
                ("Estimate: ", "bold"),
                (where, "bold yellow"),
                f"  ({e.location.lat:.4f}, {e.location.lon:.4f}) ±{e.uncertainty_km:.0f} km, confidence {e.confidence}",
            )
        )
    net = report.network
    if net:
        line = Text.assemble(
            ("Network: ", "bold"),
            f"{net.prefix or '?'}  AS{net.asn or '?'} {net.as_name or ''}",
            f"  {net.rir or ''} {net.rir_country or ''}".rstrip(),
        )
        parts.append(line)
    if report.classification:
        parts.append(Text.assemble(("Class: ", "bold"), ", ".join(c.value for c in report.classification)))
    for caveat in report.caveats:
        parts.append(Text(f"! {caveat}", style="yellow"))

    evidence = Table(show_header=True, header_style="bold", box=None, pad_edge=False, expand=True)
    evidence.add_column("Source", no_wrap=True)
    evidence.add_column("Kind", no_wrap=True)
    evidence.add_column("Finding", overflow="fold")
    shown: dict[str, int] = {}
    for item in report.evidence:
        shown[item.source] = shown.get(item.source, 0) + 1
        if shown[item.source] <= MAX_ROWS_PER_SOURCE:
            evidence.add_row(item.source, Text(item.kind.value, style=KIND_STYLE[item.kind]), item.summary)
    for source, count in shown.items():
        if count > MAX_ROWS_PER_SOURCE:
            hidden = count - MAX_ROWS_PER_SOURCE
            evidence.add_row(source, "", Text(f"... {hidden} more (use --json for all)", style="dim"))
    parts.append(evidence)

    providers = Text("Sources: ")
    for status in report.providers:
        providers.append(f"{status.provider}", style=STATUS_STYLE.get(status.status, ""))
        providers.append(f"({status.status}) ")
    parts.append(providers)
    return Panel(Group(*parts), title=str(report.target.address), title_align="left", border_style="yellow")


def print_run(console: Console, run: RunReport) -> None:
    if run.status is RunStatus.FAILED:
        console.print(f"[red]Cannot locate {run.query}:[/] {run.error}")
        return
    if run.hostname:
        console.print(f"[bold]{run.hostname}[/] → {', '.join(str(t.target.address) for t in run.targets)}")
    for address, reason in run.skipped:
        console.print(f"[dim]Skipped {address}: {reason}[/]")
    for target in run.targets:
        console.print(target_panel(target))
