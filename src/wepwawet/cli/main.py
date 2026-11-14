"""``wepwawet`` / ``wpw`` entry point.

Like Wireshark: ``wepwawet`` alone opens the application window; sub-commands
work in the terminal.
"""

from typing import Annotated

import typer
from rich.console import Console
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


def run() -> None:
    app()
