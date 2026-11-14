"""``wepwawet data update``: download the free GeoIP databases into the data directory.

- DB-IP Lite City: monthly, no key, CC BY 4.0 (attribution: "IP geolocation by DB-IP").
- IPinfo Lite: country and ASN, needs a free token, CC BY-SA 4.0.
- MaxMind GeoLite2 City and ASN: need a free account ID and licence key (GeoLite EULA).

Files are written atomically: a failed download never replaces a working database.
"""

import gzip
import io
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from wepwawet.core.http import USER_AGENT
from wepwawet.core.secrets import Secrets

DBIP_URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
IPINFO_URL = "https://ipinfo.io/data/ipinfo_lite.mmdb"
MAXMIND_URL = "https://download.maxmind.com/geoip/databases/{edition}/download"


@dataclass(frozen=True)
class UpdateResult:
    name: str
    status: str  # "updated", "skipped" or "failed"
    message: str


Progress = Callable[[str], None]


def _months_back(today: date, count: int) -> list[str]:
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".part") as tmp:
        tmp.write(data)
        temp_path = Path(tmp.name)
    temp_path.replace(target)


def _check_mmdb(data: bytes, name: str) -> None:
    # Every MaxMind DB file ends with a metadata section introduced by this marker.
    if b"\xab\xcd\xefMaxMind.com" not in data[-128 * 1024 :]:
        raise ValueError(f"{name}: the download is not a MaxMind DB file")


class GeoipUpdater:
    def __init__(self, directory: Path, secrets: Secrets, client: httpx.Client | None = None) -> None:
        self.directory = directory
        self._secrets = secrets
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(120.0, connect=15.0), headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    def update_all(self, only: set[str] | None = None, progress: Progress = lambda _m: None) -> list[UpdateResult]:
        steps = {"dbip": self.update_dbip, "ipinfo": self.update_ipinfo, "geolite": self.update_geolite}
        results = []
        for name, step in steps.items():
            if only and name not in only:
                continue
            progress(f"Updating {name}...")
            try:
                results.append(step())
            except (httpx.HTTPError, ValueError, tarfile.TarError, OSError, EOFError) as exc:
                results.append(UpdateResult(name, "failed", str(exc)[:300]))
        return results

    def update_dbip(self, today: date | None = None) -> UpdateResult:
        for month in _months_back(today or datetime.now(UTC).date(), 2):
            response = self._client.get(DBIP_URL.format(month=month))
            if response.status_code == 404:
                continue
            response.raise_for_status()
            data = gzip.decompress(response.content)
            _check_mmdb(data, "DB-IP")
            _write_atomic(self.directory / "dbip-city-lite.mmdb", data)
            return UpdateResult("dbip", "updated", f"DB-IP Lite City {month}")
        return UpdateResult("dbip", "failed", "no DB-IP release found for this or last month")

    def update_ipinfo(self) -> UpdateResult:
        token = self._secrets.get("ipinfo")
        if not token:
            return UpdateResult("ipinfo", "skipped", "no token (WEPWAWET_IPINFO); free at ipinfo.io")
        response = self._client.get(IPINFO_URL, params={"token": token})
        if response.status_code in (401, 403):
            return UpdateResult("ipinfo", "failed", "IPinfo refused the token")
        response.raise_for_status()
        _check_mmdb(response.content, "IPinfo")
        _write_atomic(self.directory / "ipinfo-lite.mmdb", response.content)
        return UpdateResult("ipinfo", "updated", "IPinfo Lite (country, ASN)")

    def update_geolite(self) -> UpdateResult:
        account, key = self._secrets.get("maxmind_account_id"), self._secrets.get("maxmind_license_key")
        if not account or not key:
            return UpdateResult(
                "geolite",
                "skipped",
                "no MaxMind account (WEPWAWET_MAXMIND_ACCOUNT_ID / _LICENSE_KEY); free at maxmind.com",
            )
        names = []
        for edition in ("GeoLite2-City", "GeoLite2-ASN"):
            response = self._client.get(
                MAXMIND_URL.format(edition=edition), params={"suffix": "tar.gz"}, auth=(account, key)
            )
            if response.status_code in (401, 403):
                return UpdateResult("geolite", "failed", "MaxMind refused the account ID or licence key")
            response.raise_for_status()
            with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
                member = next((m for m in archive.getmembers() if m.name.endswith(".mmdb") and m.isfile()), None)
                if member is None:
                    raise ValueError(f"{edition}: no .mmdb in the archive")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"{edition}: cannot read {member.name}")
                data = extracted.read()
            _check_mmdb(data, edition)
            _write_atomic(self.directory / f"{edition}.mmdb", data)
            names.append(edition)
        return UpdateResult("geolite", "updated", " and ".join(names))

    def installed(self) -> list[tuple[str, int, datetime]]:
        rows = []
        for path in sorted(self.directory.glob("*.mmdb")):
            stat = path.stat()
            rows.append((path.name, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC)))
        return rows

    def close(self) -> None:
        self._client.close()


def copy_database(source: Path, directory: Path) -> Path:
    """Install a database the user downloaded by hand."""
    with source.open("rb") as handle:
        data = handle.read()
    _check_mmdb(data, source.name)
    target = directory / source.name
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target
