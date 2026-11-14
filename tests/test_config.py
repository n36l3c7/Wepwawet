from pathlib import Path

import pytest

from wepwawet.config import Config, ConfigError, load_config
from wepwawet.paths import AppPaths, default_paths


def write_config(paths: AppPaths, text: str) -> None:
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(text, encoding="utf-8")


def test_defaults_without_file(app_paths: AppPaths) -> None:
    config = load_config(app_paths, environ={})
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 0
    assert config.map.basemap == "carto"
    assert config.cache.ttl_for("rdap") == 86_400


def test_file_values_are_read(app_paths: AppPaths) -> None:
    write_config(app_paths, '[map]\nbasemap = "offline"\n[cache]\nttl_seconds = { rdap = 604800 }\n')
    config = load_config(app_paths, environ={})
    assert config.map.basemap == "offline"
    assert config.cache.ttl_for("rdap") == 604_800
    assert config.cache.ttl_for("ripestat") == 86_400


def test_environment_overrides_file(app_paths: AppPaths) -> None:
    write_config(app_paths, "[server]\nport = 9000\n")
    config = load_config(app_paths, environ={"WEPWAWET_PORT": "9100", "WEPWAWET_LOG_LEVEL": "debug"})
    assert config.server.port == 9100
    assert config.logging.level == "DEBUG"


def test_explicit_config_path(tmp_path: Path, app_paths: AppPaths) -> None:
    other = tmp_path / "other.toml"
    other.write_text('[map]\nbasemap = "offline"\n', encoding="utf-8")
    assert load_config(app_paths, environ={"WEPWAWET_CONFIG": str(other)}).map.basemap == "offline"


def test_missing_explicit_config_path_is_an_error(tmp_path: Path, app_paths: AppPaths) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(app_paths, environ={"WEPWAWET_CONFIG": str(tmp_path / "nope.toml")})


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[server\n", "not valid TOML"),
        ("[server]\nport = 70000\n", "server.port"),
        ('[map]\nbasemap = "osm"\n', "map.basemap"),
        ("[unknown]\nx = 1\n", "unknown"),
        ("[cache]\nttl_seconds = { rdap = 0 }\n", "TTL must be positive"),
    ],
)
def test_invalid_values_raise(app_paths: AppPaths, text: str, message: str) -> None:
    write_config(app_paths, text)
    with pytest.raises(ConfigError, match=message):
        load_config(app_paths, environ={})


def test_remote_bind_is_refused_by_default() -> None:
    config = Config.model_validate({"server": {"host": "0.0.0.0"}})  # noqa: S104 - the case under test
    with pytest.raises(ConfigError, match="only loopback"):
        config.check_bind_address()
    Config.model_validate({"server": {"host": "0.0.0.0", "allow_remote": True}}).check_bind_address()  # noqa: S104


def test_xdg_variables_are_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/ignored")
    paths = default_paths()
    assert paths.config_dir == tmp_path / "cfg" / "wepwawet"
    assert paths.cache_dir == Path.home() / ".cache" / "wepwawet"
