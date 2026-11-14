"""City gazetteer from GeoNames (cities with more than 15 000 inhabitants, CC BY 4.0).

Downloaded by ``wepwawet data update`` and stored as a small tab-separated file; used to name
the candidate cities inside a feasible region.
"""

import csv
import io
import math
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import h3
import httpx

from wepwawet.core.geo import haversine
from wepwawet.core.models import CandidateCity, GeoPoint, Region

GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
FILE_NAME = "cities15000.tsv"


@dataclass(frozen=True)
class City:
    name: str
    country: str
    lat: float
    lon: float
    population: int


def convert_geonames(raw_zip: bytes, target: Path) -> int:
    """Keep name, country, coordinates and population of each city; return how many."""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        text = archive.read("cities15000.txt").decode("utf-8")
    rows = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 15:
            continue
        rows.append((fields[1], fields[8], fields[4], fields[5], fields[14] or "0"))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerows(rows)
    temporary.replace(target)
    return len(rows)


def download_geonames(directory: Path, client: httpx.Client) -> int:
    response = client.get(GEONAMES_URL)
    response.raise_for_status()
    return convert_geonames(response.content, directory / FILE_NAME)


@lru_cache(maxsize=2)
def load_cities(path: Path) -> tuple[City, ...]:
    if not path.is_file():
        return ()
    cities = []
    with path.open(encoding="utf-8", newline="") as handle:
        for name, country, lat, lon, population in csv.reader(handle, delimiter="\t"):
            cities.append(City(name, country, float(lat), float(lon), int(population or 0)))
    return tuple(cities)


def candidate_cities(cities: tuple[City, ...], region: Region, cells: set[str], limit: int = 10) -> list[CandidateCity]:
    """Cities inside the region, ranked by size and closeness to the centroid."""
    inside = [c for c in cities if h3.latlng_to_cell(c.lat, c.lon, region.resolution) in cells]
    scored = []
    for city in inside:
        distance = haversine(region.centroid.lat, region.centroid.lon, city.lat, city.lon)
        score = math.log10(max(city.population, 10)) / (1 + distance / max(region.radius_km, 1))
        scored.append((score, distance, city))
    scored.sort(key=lambda item: item[0], reverse=True)
    total = sum(s for s, _, _ in scored[:limit]) or 1
    return [
        CandidateCity(
            name=city.name,
            country=city.country,
            point=GeoPoint(lat=city.lat, lon=city.lon),
            population=city.population,
            distance_km=round(distance, 1),
            score=round(score / total, 3),
        )
        for score, distance, city in scored[:limit]
    ]


def nearest_city(cities: tuple[City, ...], point: GeoPoint, max_km: float = 50.0) -> City | None:
    best: tuple[float, City] | None = None
    for city in cities:
        if abs(city.lat - point.lat) > 1.0:
            continue
        distance = haversine(point.lat, point.lon, city.lat, city.lon)
        if distance <= max_km and (best is None or distance < best[0]):
            best = (distance, city)
    return best[1] if best else None
