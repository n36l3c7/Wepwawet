"""1.1.0: settings and API keys changed from the window."""

import os
import stat
import tomllib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from wepwawet.api.app import create_app
from wepwawet.api.services import default_services
from wepwawet.config import Config, load_config
from wepwawet.core.settings import SettingsError, SettingsStore, dump_toml
from wepwawet.paths import AppPaths

TOKEN = "settings-test-token"  # noqa: S105 - test fixture, not a secret
SOURCES = {"globalping", "shodan", "rdns"}


@pytest.fixture
def client(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in list(os.environ):
        if name.startswith("WEPWAWET_"):
            monkeypatch.delenv(name)
    app_paths.ensure()
    services = default_services(Config(), app_paths)
    app = create_app(Config(), TOKEN, static_dir=None, services=services)
    with TestClient(app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {TOKEN}"}) as test_client:
        yield test_client


def test_dump_toml_round_trips() -> None:
    data = {"map": {"basemap": "offline"}, "measurements": {"max_tests": 80, "calibrate": True}}
    data["providers"] = {"shodan": {"enabled": False}}
    text = dump_toml(data, "# header")
    assert text.startswith("# header\n") and tomllib.loads(text) == data
    assert tomllib.loads(dump_toml({"keys": {"x": 'a"b\\c'}})) == {"keys": {"x": 'a"b\\c'}}
    with pytest.raises(SettingsError):
        dump_toml({"keys": {"x": "line\nbreak"}})


def test_settings_layer_over_config_and_under_the_environment(app_paths: AppPaths) -> None:
    app_paths.ensure()
    app_paths.config_file.write_text('[map]\nbasemap = "offline"\n[measurements]\nmax_tests = 90\n', encoding="utf-8")
    store = SettingsStore(app_paths, {})
    config = store.update({"measurements": {"max_tests": 40}}, SOURCES)
    assert config.measurements.max_tests == 40 and config.map.basemap == "offline"
    assert load_config(app_paths, {}).measurements.max_tests == 40
    assert "max_tests = 90" in app_paths.config_file.read_text(encoding="utf-8")  # never rewritten
    assert load_config(app_paths, {"WEPWAWET_BASEMAP": "carto"}).map.basemap == "carto"


def test_invalid_settings_leave_the_file_alone(app_paths: AppPaths) -> None:
    store = SettingsStore(app_paths, {})
    store.update({"map": {"basemap": "offline"}}, SOURCES)
    before = store.settings_file.read_text(encoding="utf-8")
    with pytest.raises(SettingsError, match="max_tests"):
        store.update({"measurements": {"max_tests": -1}}, SOURCES)
    for bad in ({"server": {"allow_remote": True}}, {"providers": {"nope": {"enabled": False}}}, {"map": {"x": 1}}):
        with pytest.raises(SettingsError):
            store.update(bad, SOURCES)
    assert store.settings_file.read_text(encoding="utf-8") == before
    assert [p.name for p in app_paths.config_dir.iterdir() if p.name.startswith(".")] == []


def test_keys_are_written_privately_and_never_read_back(app_paths: AppPaths) -> None:
    store = SettingsStore(app_paths, {"WEPWAWET_SHODAN": "from-env"})
    store.set_key("globalping", "  tok123  ")
    assert tomllib.loads(store.secrets_file.read_text(encoding="utf-8")) == {"keys": {"globalping": "tok123"}}
    if os.name == "posix":
        assert stat.S_IMODE(store.secrets_file.stat().st_mode) == 0o600
    status = {row["name"]: row for row in store.key_status()}
    assert status["globalping"]["source"] == "file" and status["shodan"]["source"] == "env"
    assert status["censys"]["source"] is None and "tok123" not in repr(status)
    store.set_key("globalping", None)
    assert store.key_status()[0]["source"] is None
    for name, value in (("unknown", "x"), ("globalping", "has space"), ("globalping", "")):
        with pytest.raises(SettingsError):
            store.set_key(name, value)


def test_settings_api(client: TestClient) -> None:
    view = client.get("/api/settings").json()
    assert view["values"]["map"]["basemap"] == "carto" and view["values"]["providers"]["shodan"]["enabled"]
    assert {s["name"] for s in view["sources"]} >= SOURCES and "gui.rendering" in view["restart_required"]

    changed = client.put(
        "/api/settings", json={"values": {"map": {"basemap": "offline"}, "providers": {"rdns": {"enabled": False}}}}
    )
    assert changed.status_code == 200 and changed.json()["values"]["map"]["basemap"] == "offline"
    assert client.get("/api/meta").json()["basemap"] == "offline"
    assert "rdns" not in {p["name"] for p in client.get("/api/providers").json()}

    bad = client.put("/api/settings", json={"values": {"measurements": {"sweep_probes": 9999}}})
    assert bad.status_code == 422 and "sweep_probes" in bad.json()["detail"]
    assert client.put("/api/settings", json={"values": {"server": {"port": 1}}}).status_code == 422


def test_key_api_makes_a_source_available(client: TestClient) -> None:
    def shodan_reason() -> str | None:
        return next(p["reason"] for p in client.get("/api/providers").json() if p["name"] == "shodan")

    assert shodan_reason() is not None
    response = client.put("/api/settings/keys/shodan", json={"value": "abc123"})
    assert response.status_code == 200 and "abc123" not in response.text
    assert shodan_reason() is None
    assert next(k for k in response.json()["keys"] if k["name"] == "shodan")["source"] == "file"
    assert client.delete("/api/settings/keys/shodan").status_code == 200
    assert shodan_reason() is not None
    assert client.put("/api/settings/keys/bogus", json={"value": "x"}).status_code == 422


def test_settings_need_the_token(client: TestClient) -> None:
    assert client.get("/api/settings", headers={"Authorization": ""}).status_code == 401
    assert (
        client.put("/api/settings/keys/shodan", headers={"Authorization": ""}, json={"value": "x"}).status_code == 401
    )
