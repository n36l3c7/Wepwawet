from pathlib import Path

import pytest
from typer.testing import CliRunner

from wepwawet import __version__
from wepwawet.cli import main as cli
from wepwawet.config import Config
from wepwawet.doctor import Status, check_traceroute_tools

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for variable, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(variable, str(tmp_path / sub))
    monkeypatch.delenv("WEPWAWET_CONFIG", raising=False)


def test_version_flag() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_arguments_opens_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, bool]] = []

    def fake_launch(config: Config, *, browser: bool, smoke_test: bool) -> int:
        calls.append({"browser": browser, "smoke_test": smoke_test})
        return 0

    monkeypatch.setattr("wepwawet.gui.launcher.launch", fake_launch)
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0
    assert calls == [{"browser": False, "smoke_test": False}]


def test_gui_without_display_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    result = runner.invoke(cli.app, ["gui"])
    assert result.exit_code == 2
    assert "No graphical display" in result.output


def test_invalid_config_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[server]\nport = -1\n", encoding="utf-8")
    monkeypatch.setenv("WEPWAWET_CONFIG", str(bad))
    result = runner.invoke(cli.app, ["serve"])
    assert result.exit_code == 2
    assert "server.port" in result.output


def test_serve_refuses_remote_host() -> None:
    result = runner.invoke(cli.app, ["serve", "--host", "0.0.0.0"])  # noqa: S104 - the case under test
    assert result.exit_code == 2
    assert "only loopback" in result.output


def test_doctor_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(cli.app, ["doctor"])
    assert "Python" in result.output
    assert "Local traceroute" in result.output
    assert result.exit_code in (0, 1)


def test_traceroute_check_without_tools() -> None:
    check = check_traceroute_tools(which=lambda _name: None)
    assert check.status is Status.WARN
    assert "mtr" in check.hint


def test_traceroute_check_with_mtr() -> None:
    check = check_traceroute_tools(which=lambda name: "/usr/bin/mtr" if name == "mtr" else None)
    assert check.status is Status.OK
    assert "mtr" in check.detail
