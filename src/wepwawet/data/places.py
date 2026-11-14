"""Place codes found in router names: airports (IATA) and UN/LOCODE.

- OurAirports (public domain): airports with an IATA code and scheduled service, or large ones.
- UN/LOCODE (United Nations, free to use): locations with coordinates.

Both are downloaded by ``wepwawet data update`` and kept as small tab-separated files.
"""

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
LOCODE_URL = "https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv"
AIRPORTS_FILE = "airports.tsv"
LOCODE_FILE = "locode.tsv"

_COORDINATE = re.compile(r"^(\d{2})(\d{2})([NS])\s+(\d{3})(\d{2})([EW])$")


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    country: str
    lat: float
    lon: float


def parse_locode_coordinates(text: str) -> tuple[float, float] | None:
    """UN/LOCODE coordinates look like ``4528N 00911E`` (degrees and minutes)."""
    match = _COORDINATE.match(text.strip())
    if not match:
        return None
    lat = int(match.group(1)) + int(match.group(2)) / 60
    lon = int(match.group(4)) + int(match.group(5)) / 60
    if match.group(3) == "S":
        lat = -lat
    if match.group(6) == "W":
        lon = -lon
    return lat, lon


def _write(rows: Sequence[Sequence[str]], target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, delimiter="\t", lineterminator="\n").writerows(rows)
    temporary.replace(target)
    return len(rows)


def convert_airports(text: str, target: Path) -> int:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        code = (row.get("iata_code") or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            continue
        if row.get("type") not in ("large_airport", "medium_airport") and row.get("scheduled_service") != "yes":
            continue
        name = row.get("municipality") or row.get("name") or code
        rows.append((code, name, row.get("iso_country", ""), row["latitude_deg"], row["longitude_deg"]))
    return _write(rows, target)


def convert_locode(text: str, target: Path) -> int:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        coordinates = parse_locode_coordinates(row.get("Coordinates") or "")
        if coordinates is None or not row.get("Country") or not row.get("Location"):
            continue
        code = f"{row['Country']}{row['Location']}".upper()
        name = row.get("NameWoDiacritics") or row.get("Name") or code
        rows.append((code, name, row["Country"], f"{coordinates[0]:.4f}", f"{coordinates[1]:.4f}"))
    return _write(rows, target)


def download_places(directory: Path, client: httpx.Client) -> tuple[int, int]:
    airports = client.get(AIRPORTS_URL)
    airports.raise_for_status()
    locode = client.get(LOCODE_URL)
    locode.raise_for_status()
    return (
        convert_airports(airports.text, directory / AIRPORTS_FILE),
        convert_locode(locode.text, directory / LOCODE_FILE),
    )


@lru_cache(maxsize=4)
def load_places(path: Path) -> dict[str, Place]:
    """Code to place. For duplicate codes the first row wins (OurAirports lists large airports first)."""
    places: dict[str, Place] = {}
    if not path.is_file():
        return places
    with path.open(encoding="utf-8", newline="") as handle:
        for code, name, country, lat, lon in csv.reader(handle, delimiter="\t"):
            places.setdefault(code, Place(code, name, country, float(lat), float(lon)))
    return places
