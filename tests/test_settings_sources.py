"""1.5.0: source options (RIPE Atlas credits, this machine, SSH hosts) from the settings panel."""

import ipaddress
import tomllib

import pytest
from fastapi.testclient import TestClient

from tests.test_settings import SOURCES, client  # noqa: F401 - pytest fixture
from wepwawet.config import load_config
from wepwawet.core.models import Target
from wepwawet.core.settings import SettingsError, SettingsStore, dump_toml
from wepwawet.paths import AppPaths
from wepwawet.providers.measurement.local import SshPingProvider

NAMES = SOURCES | {"ripe_atlas", "local", "ssh"}
HOST = {"name": "lab", "host": "192.0.2.7", "user": "probe", "port": 2222, "lat": 45.4, "lon": 9.2}


def test_lists_of_tables_round_trip() -> None:
    data = {"providers": {"ssh": {"enabled": True, "hosts": [HOST, {"host": "vps.example", "lat": 1.5, "lon": 2}]}}}
    text = dump_toml(data)
    assert "[[providers.ssh.hosts]]" in text and tomllib.loads(text) == data


def test_source_options_are_saved_and_loaded(app_paths: AppPaths) -> None:
    store = SettingsStore(app_paths, {})
    config = store.update(
        {
            "providers": {
                "ripe_atlas": {"max_credits": 600},
                "rdns": {"sweep": False},
                "local": {"lat": 45.46, "lon": 9.19},
                "ssh": {"hosts": [HOST]},
            },
            "measurements": {"speed_km_per_ms": 180.0},
            "cache": {"default_ttl_seconds": 3600},
        },
        NAMES,
    )
    assert config.provider("ripe_atlas").option("max_credits", 0) == 600
    assert config.provider("rdns").option("sweep", True) is False
    assert config.provider("local").option("lat", None) == 45.46
    assert config.provider("ssh").option("hosts", []) == [HOST]
    assert config.measurements.speed_km_per_ms == 180.0 and config.cache.default_ttl_seconds == 3600
    assert load_config(app_paths, {}).provider("ripe_atlas").option("max_credits", 0) == 600


def test_clearing_an_option_falls_back_to_config_toml(app_paths: AppPaths) -> None:
    app_paths.ensure()
    app_paths.config_file.write_text("[providers.local]\nlat = 41.9\nlon = 12.5\n", encoding="utf-8")
    store = SettingsStore(app_paths, {})
    assert (
        store.update({"providers": {"local": {"lat": 45.0, "lon": 9.0}}}, NAMES).provider("local").option("lat", None)
        == 45.0
    )
    config = store.update({"providers": {"local": {"lat": None, "lon": None}}}, NAMES)
    assert config.provider("local").option("lat", None) == 41.9
    assert "lat" not in store.settings_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"ripe_atlas": {"max_credits": -1}}, "between"),
        ({"ripe_atlas": {"max_credits": 1.5}}, "whole number"),
        ({"ripe_atlas": {"max_credits": True}}, "whole number"),
        ({"rdns": {"sweep": "yes"}}, "true or false"),
        ({"local": {"lat": 91}}, "between"),
        ({"local": {"lat": float("nan")}}, "number"),
        ({"ripe_atlas": {"key": "x"}}, "can be changed"),
        ({"ssh": {"hosts": [{**HOST, "host": "-oProxyCommand=evil"}]}}, "host is not valid"),
        ({"ssh": {"hosts": [{**HOST, "host": "a b"}]}}, "host is not valid"),
        ({"ssh": {"hosts": [{**HOST, "user": "-l root"}]}}, "user is not valid"),
        ({"ssh": {"hosts": [{**HOST, "port": 70000}]}}, "between"),
        ({"ssh": {"hosts": [{**HOST, "lat": None}]}}, "lat must be a number"),
        ({"ssh": {"hosts": [{**HOST, "command": "rm"}]}}, "unknown fields"),
        ({"ssh": {"hosts": [HOST] * 21}}, "at most 20"),
    ],
)
def test_invalid_source_options_are_refused(app_paths: AppPaths, update: dict, message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        SettingsStore(app_paths, {}).update({"providers": update}, NAMES)


def test_the_server_section_stays_in_config_toml(app_paths: AppPaths) -> None:
    with pytest.raises(SettingsError, match=r"\[server\]"):
        SettingsStore(app_paths, {}).update({"server": {"allow_remote": True}}, NAMES)


def test_ssh_argv_ends_options_before_the_destination() -> None:
    argv = SshPingProvider.argv(HOST, Target(address=ipaddress.ip_address("192.0.2.1")), 3)
    assert argv[-3:-1] == ["--", "probe@192.0.2.7"]


def test_settings_api_shows_and_saves_source_options(client: TestClient) -> None:  # noqa: F811
    values = client.get("/api/settings").json()["values"]["providers"]
    assert values["ripe_atlas"]["max_credits"] == 0 and values["local"]["lat"] is None
    assert values["ssh"]["hosts"] == [] and values["rdns"]["sweep"] is True
    saved = client.put(
        "/api/settings", json={"values": {"providers": {"ripe_atlas": {"enabled": True, "max_credits": 300}}}}
    )
    assert saved.status_code == 200 and saved.json()["values"]["providers"]["ripe_atlas"]["max_credits"] == 300
    bad = client.put("/api/settings", json={"values": {"providers": {"ssh": {"hosts": [{"host": "-x"}]}}}})
    assert bad.status_code == 422
