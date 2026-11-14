"""``wepwawet`` / ``wpw`` entry point.

Like Wireshark: ``wepwawet`` alone opens the application window; sub-commands
work in the terminal.
"""

import asyncio
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from wepwawet import __version__
from wepwawet.config import Config, ConfigError, load_config
from wepwawet.doctor import Status, run_checks
from wepwawet.logging_setup import setup_logging
from wepwawet.paths import default_paths

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
    as_json: Annotated[bool, typer.Option("--json", help="Print the full report as JSON.")] = False,
    measure: Annotated[bool, typer.Option("--measure/--passive", help="Run the ping campaign (default).")] = True,
) -> None:
    """Locate an address from the terminal and print the evidence."""
    from wepwawet.cli.report import print_run
    from wepwawet.core.campaign import Campaign
    from wepwawet.core.context import build_context, enabled_enrichment, enabled_measurement
    from wepwawet.core.models import RunReport, RunStatus
    from wepwawet.core.pipeline import Pipeline
    from wepwawet.core.secrets import SecretsError

    config = _load()

    def show(level: str, source: str, message: str) -> None:
        if not as_json:
            style = {"error": "red", "ok": "green"}.get(level, "dim")
            err_console.print(rf"[{style}]\[{escape(source)}][/] {escape(message)}")

    try:
        ctx = build_context(config, default_paths(), log=show)
    except SecretsError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    def emit(name: str, data: dict[str, object]) -> None:
        if name == "log":
            show(str(data["level"]), str(data["source"]), str(data["message"]))

    async def go() -> RunReport:
        try:
            campaign = Campaign(ctx, enabled_measurement(config), show) if measure else None
            pipeline = Pipeline(ctx, enabled_enrichment(config), emit, campaign)
            return await pipeline.run(RunReport(id="cli", query=target))
        finally:
            await ctx.http.aclose()

    report = asyncio.run(go())
    if as_json:
        # Raw UTF-8 bytes: the JSON stays valid whatever the terminal encoding, for scripts and pipes.
        sys.stdout.buffer.write(report.model_dump_json(indent=2).encode("utf-8") + b"\n")
        sys.stdout.flush()
    else:
        print_run(console, report)
    if report.status is RunStatus.FAILED:
        raise typer.Exit(2)


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
        list[str] | None, typer.Option("--only", help="dbip, ipinfo, geolite or geonames (repeatable). Default: all.")
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


def run() -> None:
    app()
