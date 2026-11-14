"""``wepwawet`` / ``wpw`` entry point.

Like Wireshark: ``wepwawet`` alone opens the application window; sub-commands
work in the terminal.
"""

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from wepwawet import __version__
from wepwawet.config import Config, ConfigError, load_config
from wepwawet.doctor import Status, run_checks
from wepwawet.logging_setup import setup_logging
from wepwawet.paths import default_paths

if TYPE_CHECKING:
    from wepwawet.core.history import History
    from wepwawet.core.models import RunReport

app = typer.Typer(
    name="wepwawet",
    help="Measurement-based IP geolocation. Run without a command to open the application window.",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)

_STATUS_STYLE = {Status.OK: "[green]ok[/]", Status.WARN: "[yellow]warn[/]", Status.FAIL: "[red]fail[/]"}


def _load() -> Config:
    paths = default_paths()
    try:
        config = load_config(paths)
    except ConfigError as exc:
        err_console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2) from exc
    paths.ensure()
    setup_logging(config.logging.level, paths.log_file)
    return config


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"wepwawet {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", "-V", help="Show the version and exit.", callback=_version_callback)
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        ctx.invoke(gui)


@app.command()
def gui(
    browser: Annotated[bool, typer.Option("--browser", help="Open the system browser instead of a window.")] = False,
    smoke_test: Annotated[bool, typer.Option("--smoke-test", hidden=True)] = False,
) -> None:
    """Open the application window (the default when no command is given)."""
    from wepwawet.gui.launcher import GuiUnavailableError, launch

    config = _load()
    try:
        code = launch(config, browser=browser, smoke_test=smoke_test)
    except (GuiUnavailableError, ConfigError) as exc:
        err_console.print(f"[red]Cannot open the window:[/] {exc}")
        raise typer.Exit(2) from exc
    raise typer.Exit(code)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Address to listen on (default from config: 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default from config: a free port).")] = None,
) -> None:
    """Start only the local server and print the address to open."""
    from wepwawet.api.app import create_app
    from wepwawet.api.server import base_url, bind_socket, new_token, serve_forever

    config = _load()
    updates = {k: v for k, v in {"host": host, "port": port}.items() if v is not None}
    config = config.model_copy(update={"server": config.server.model_copy(update=updates)})
    try:
        config.check_bind_address()
    except ConfigError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    token = new_token()
    sock = bind_socket(config.server.host, config.server.port)
    console.print(f"Open [bold]{base_url(sock)}/auth?token={token}[/] (Ctrl+C to stop)")
    serve_forever(create_app(config, token), sock)


@app.command()
def doctor() -> None:
    """Check Python, configuration, the desktop window and the local traceroute tools."""
    checks = run_checks()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for check in checks:
        detail = check.detail + (f"\n[dim]{check.hint}[/]" if check.hint else "")
        table.add_row(check.name, _STATUS_STYLE[check.status], detail)
    console.print(table)
    if any(check.status is Status.FAIL for check in checks):
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Show the version."""
    console.print(f"wepwawet {__version__}")


@app.command()
def locate(
    target: Annotated[str, typer.Argument(help="IP address or hostname.")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full report as JSON (same as --format json).")
    ] = False,
    measure: Annotated[bool, typer.Option("--measure/--passive", help="Run the measurements (default).")] = True,
    fmt: Annotated[
        str | None, typer.Option("--format", "-f", help="json, geojson, kml, md or csv (hops). Default: a table.")
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write the export to this file.")] = None,
) -> None:
    """Locate an address from the terminal and print the evidence or an export."""
    from wepwawet.cli.report import print_run
    from wepwawet.core.models import RunStatus

    config = _load()
    fmt = "json" if as_json and fmt is None else fmt
    quiet = fmt is not None and output is None
    report = asyncio.run(_locate(config, target, 0 if not measure else None, quiet))
    if fmt is None:
        print_run(console, report)
    else:
        _write_export(report, fmt, output)
    if report.status is RunStatus.FAILED:
        raise typer.Exit(2)


def _write_export(report: "RunReport", fmt: str, output: Path | None) -> None:
    from wepwawet.core.export import render

    try:
        body = render(report, fmt)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    if output is not None:
        output.write_text(body, encoding="utf-8")
        err_console.print(f"Wrote {output}")
    else:
        # Raw UTF-8 bytes: the export stays valid whatever the terminal encoding, for scripts and pipes.
        sys.stdout.buffer.write(body.encode("utf-8") + (b"" if body.endswith("\n") else b"\n"))
        sys.stdout.flush()


async def _locate(config: Config, query: str, max_tests: int | None, quiet: bool) -> "RunReport":
    """Run one query in-process and save it to the history. ``max_tests=0`` means passive only."""
    import secrets as secrets_module

    from wepwawet.api.services import Services
    from wepwawet.core.context import build_context, enabled_enrichment, enabled_measurement
    from wepwawet.core.events import EventBus
    from wepwawet.core.history import History
    from wepwawet.core.models import RunReport
    from wepwawet.core.runs import RunManager
    from wepwawet.core.secrets import SecretsError

    def show(level: str, source: str, message: str) -> None:
        if not quiet:
            style = {"error": "red", "ok": "green"}.get(level, "dim")
            err_console.print(rf"[{style}]\[{escape(source)}][/] {escape(message)}")

    paths = default_paths()
    try:
        ctx = build_context(config, paths, log=show)
    except SecretsError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    bus = EventBus()
    history = History.open(paths.data_dir / "history.sqlite3")
    services = Services(
        bus=bus,
        runs=RunManager(bus, lambda b: services.pipeline(b)),
        ctx=ctx,
        providers=enabled_enrichment(config),
        measurement=enabled_measurement(config),
        history=history,
    )

    class Printer:
        def publish(self, name: str, data: dict[str, object]) -> None:
            if name == "log":
                show(str(data["level"]), str(data["source"]), str(data["message"]))

    try:
        run = RunReport(id=secrets_module.token_hex(6), query=query)
        result = await services.pipeline(Printer(), max_tests).run(run)  # type: ignore[arg-type]
        history.save(result)
        return result
    finally:
        await ctx.http.aclose()


@app.command()
def providers() -> None:
    """List the data sources and whether each can run now."""
    from wepwawet.core.context import build_context, enabled_enrichment, enabled_measurement
    from wepwawet.providers.base import EnrichmentProvider
    from wepwawet.providers.measurement.base import MeasurementProvider

    config = _load()
    ctx = build_context(config, default_paths())
    table = Table(show_header=True, header_style="bold")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("Note", overflow="fold")
    providers_list: list[EnrichmentProvider | MeasurementProvider] = [
        *enabled_enrichment(config),
        *enabled_measurement(config),
    ]
    for provider in providers_list:
        reason = provider.unavailable_reason(ctx)
        table.add_row(provider.label, "[yellow]unavailable[/]" if reason else "[green]ready[/]", reason or "")
    console.print(table)


data_app = typer.Typer(help="Download and inspect reference data (GeoIP databases).", no_args_is_help=True)
app.add_typer(data_app, name="data")


@data_app.command("update")
def data_update(
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="dbip, ipinfo, geolite, geonames or places (repeatable). Default: all."),
    ] = None,
) -> None:
    """Download the free GeoIP databases (DB-IP Lite always; IPinfo and GeoLite2 when keys are set)."""
    from wepwawet.core.secrets import load_secrets
    from wepwawet.data.updater import GeoipUpdater

    _load()
    paths = default_paths()
    updater = GeoipUpdater(paths.data_dir / "geoip", load_secrets(paths.config_dir / "secrets.toml"))
    try:
        results = updater.update_all(set(only) if only else None, progress=lambda m: err_console.print(f"[dim]{m}[/]"))
    finally:
        updater.close()
    style = {"updated": "green", "skipped": "yellow", "failed": "red"}
    for result in results:
        console.print(f"[{style[result.status]}]{result.status:8}[/] {result.name}: {result.message}")
    if any(r.status == "failed" for r in results):
        raise typer.Exit(1)


@data_app.command("status")
def data_status() -> None:
    """Show the installed GeoIP databases."""
    paths = default_paths()
    directory = paths.data_dir / "geoip"
    files = sorted(directory.glob("*.mmdb")) if directory.is_dir() else []
    if not files:
        console.print("No GeoIP database installed. Run: wepwawet data update")
        return
    for path in files:
        console.print(f"{path.name:28} {path.stat().st_size / 2**20:7.1f} MiB")


history_app = typer.Typer(help="Past runs: list, show, compare and export them.", no_args_is_help=True)
app.add_typer(history_app, name="history")


def _history() -> "History":
    from wepwawet.core.history import History

    return History.open(default_paths().data_dir / "history.sqlite3")


@history_app.command("list")
def history_list(
    query: Annotated[str | None, typer.Argument(help="Only runs of this query.")] = None,
    limit: Annotated[int, typer.Option(help="Most recent runs to show.")] = 30,
) -> None:
    """List past runs, most recent first."""
    table = Table(show_header=True, header_style="bold")
    for column in ("Run", "When (UTC)", "Query", "Estimate", "Conf."):
        table.add_column(column)
    for entry in _history().list(query, limit):
        where = ", ".join(p for p in (entry.city, entry.country) if p) or entry.status
        table.add_row(
            entry.id,
            f"{entry.started_at:%Y-%m-%d %H:%M}",
            entry.query,
            where,
            "" if entry.confidence is None else str(entry.confidence),
        )
    console.print(table)


@history_app.command("show")
def history_show(run_id: str) -> None:
    """Show a past run."""
    from wepwawet.cli.report import print_run

    run = _history().get(run_id)
    if run is None:
        err_console.print(f"[red]No run {run_id}[/]")
        raise typer.Exit(2)
    print_run(console, run)


@history_app.command("diff")
def history_diff(
    old: str, new: str, address: Annotated[str | None, typer.Option(help="Address to compare.")] = None
) -> None:
    """What changed between two runs: estimate, network, GeoIP answers, paths."""
    from wepwawet.core.history import diff_runs

    store = _history()
    a, b = store.get(old), store.get(new)
    if a is None or b is None:
        err_console.print(f"[red]No run {old if a is None else new}[/]")
        raise typer.Exit(2)
    changes = diff_runs(a, b, address)
    if not changes:
        console.print("No difference.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in ("What", "Before", "After", "Note"):
        table.add_column(column, overflow="fold")
    for change in changes:
        table.add_row(change.field, change.before, change.after, change.note)
    console.print(table)


@history_app.command("export")
def history_export(
    run_id: str,
    fmt: Annotated[str, typer.Option("--format", "-f", help="json, geojson, kml, md or csv (hops).")] = "json",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Export a past run."""
    run = _history().get(run_id)
    if run is None:
        err_console.print(f"[red]No run {run_id}[/]")
        raise typer.Exit(2)
    _write_export(run, fmt, output)


@app.command()
def bulk(
    file: Annotated[Path, typer.Argument(help="Targets, one per line (# for comments).", exists=True, dir_okay=False)],
    max_tests: Annotated[int, typer.Option(help="Measurement tests for the whole list.")] = 500,
    measure: Annotated[bool, typer.Option("--measure/--passive", help="Run the measurements (default).")] = True,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Summary CSV (default: stdout).")] = None,
) -> None:
    """Locate a list of targets one after the other under one test budget."""
    from wepwawet.core.bulk import new_job, parse_targets, run_bulk
    from wepwawet.core.export import summary_csv

    config = _load()
    try:
        queries = parse_targets(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    job = new_job(queries, max_tests, passive=not measure)

    async def run_one(query: str, allowed: int) -> "RunReport":
        err_console.print(f"[bold]{query}[/] [dim](up to {allowed} tests)[/]")
        return await _locate(config, query, allowed, quiet=True)

    def update(changed: object) -> None:
        done = sum(1 for item in job.items if item.status in ("done", "failed"))
        err_console.print(f"[dim]{done}/{len(job.items)} done, {job.tests_used} tests used[/]")

    asyncio.run(run_bulk(job, run_one, config.measurements.max_tests, update))
    body = summary_csv(job.runs)
    if output is not None:
        output.write_text(body, encoding="utf-8")
        err_console.print(f"Wrote {output}")
    else:
        sys.stdout.buffer.write(body.encode("utf-8"))
        sys.stdout.flush()


def run() -> None:
    app()
