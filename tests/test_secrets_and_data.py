import gzip
import io
import os
import tarfile
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from wepwawet.core.secrets import Secrets, SecretsError, load_secrets
from wepwawet.data.updater import GeoipUpdater, _months_back

MMDB_TAIL = b"\xab\xcd\xefMaxMind.com" + b"metadata"


def fake_mmdb() -> bytes:
    return b"\x00" * 64 + MMDB_TAIL


def test_secrets_from_env_override_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.toml"
    path.write_text('[keys]\nshodan = "from-file"\nipinfo = "tok"\n', encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    secrets = load_secrets(path, {"WEPWAWET_SHODAN": "from-env"})
    assert secrets.get("shodan") == "from-env" and secrets.get("ipinfo") == "tok"
    assert "from-env" not in repr(secrets)


def test_unknown_secret_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "secrets.toml"
    path.write_text('[keys]\nshodna = "typo"\n', encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    with pytest.raises(SecretsError, match="unknown keys shodna"):
        load_secrets(path, {})


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX only")
def test_world_readable_secrets_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "secrets.toml"
    path.write_text('[keys]\nshodan = "x"\n', encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(SecretsError, match="chmod 600"):
        load_secrets(path, {})


def test_missing_file_means_env_only(tmp_path: Path) -> None:
    assert load_secrets(tmp_path / "none.toml", {}).names() == []


def test_months_back() -> None:
    assert _months_back(date(2026, 1, 15), 2) == ["2026-01", "2025-12"]


@respx.mock
def test_dbip_falls_back_to_last_month(tmp_path: Path) -> None:
    respx.get("https://download.db-ip.com/free/dbip-city-lite-2026-11.mmdb.gz").respond(404)
    respx.get("https://download.db-ip.com/free/dbip-city-lite-2026-10.mmdb.gz").respond(
        content=gzip.compress(fake_mmdb())
    )
    updater = GeoipUpdater(tmp_path, Secrets({}), client=httpx.Client())
    result = updater.update_dbip(today=date(2026, 11, 2))
    assert result.status == "updated" and "2026-10" in result.message
    assert (tmp_path / "dbip-city-lite.mmdb").read_bytes() == fake_mmdb()


@respx.mock
def test_corrupt_download_never_replaces_a_database(tmp_path: Path) -> None:
    (tmp_path / "dbip-city-lite.mmdb").write_bytes(b"old")
    respx.get(url__startswith="https://download.db-ip.com/").respond(content=gzip.compress(b"<html>error</html>"))
    updater = GeoipUpdater(tmp_path, Secrets({}), client=httpx.Client())
    results = updater.update_all({"dbip"})
    assert results[0].status == "failed"
    assert (tmp_path / "dbip-city-lite.mmdb").read_bytes() == b"old"


def test_keyed_sources_are_skipped_without_keys(tmp_path: Path) -> None:
    updater = GeoipUpdater(tmp_path, Secrets({}), client=httpx.Client())
    assert updater.update_ipinfo().status == "skipped"
    assert updater.update_geolite().status == "skipped"


@respx.mock
def test_geolite_extracts_from_tarball(tmp_path: Path) -> None:
    def tarball(edition: str) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            data = fake_mmdb()
            info = tarfile.TarInfo(f"{edition}_20261110/{edition}.mmdb")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    route = respx.get(url__regex=r"https://download\.maxmind\.com/geoip/databases/(GeoLite2-\w+)/download").mock(
        side_effect=lambda request: httpx.Response(200, content=tarball(request.url.path.split("/")[3]))
    )
    secrets = Secrets({"maxmind_account_id": "123", "maxmind_license_key": "abc"})
    result = GeoipUpdater(tmp_path, secrets, client=httpx.Client()).update_geolite()
    assert result.status == "updated"
    assert (tmp_path / "GeoLite2-City.mmdb").exists() and (tmp_path / "GeoLite2-ASN.mmdb").exists()
    assert route.calls.last.request.headers["Authorization"].startswith("Basic ")
