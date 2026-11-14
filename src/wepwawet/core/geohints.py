"""Location hints in router hostnames.

Operators often write the city of a router into its name: ``ae1.cr2.fra1.example.net`` (IATA
code FRA, Frankfurt), ``itmil01-rtr`` (UN/LOCODE ITMIL, Milan), ``milano52.mil.seabone.net``
(city name). Hints are candidates only: the traceroute analysis keeps a hint only if the hop's
RTT allows the router to be there.

Order of trust: operator rules from ``geohint_rules.toml``, then UN/LOCODE, city names and
IATA codes. Three-letter tokens are ambiguous (``ae``, ``core``, ``xe``...), so common router
words are ignored and IATA hints get the lowest confidence.
"""

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

from wepwawet.core.models import GeoHint, GeoPoint
from wepwawet.data.gazetteer import City
from wepwawet.data.places import Place

# Words seen in router names that are never place codes.
STOPWORDS = frozenset(
    """
    ae ge xe et te fe hu lo be po eth gig ten hun bundle vlan irb bvi tun loop lag agg
    core edge bdr border gw gate gateway rtr router cr ar br pe ce er dr sr mr cs as ds ss
    bb backbone peer peering transit ix ixp cust customer static dyn dynamic pool dsl ftth
    net com org int ip ipv ip4 ip6 v4 v6 mpls ldp srv server host www mail dns ns
    bng bras lns cpe olt dslam pop dc colo nap cdn cache link wan lan man mgmt oob
    the and new old red blue int ext pub priv inet ipt ipv4 ipv6 rev ptr res arpa in addr
    vie sto may pro tel any all one two six ten atlas cache proxy
    """.split()
)
# IATA tokens that are also frequent English or network words: accept only from operator rules.
AMBIGUOUS_IATA = frozenset({"vie", "sto", "may", "pro", "tel", "any", "all", "one", "two", "six", "ten", "red"})

CONFIDENCE = {"operator": 0.85, "locode": 0.6, "city": 0.6, "iata": 0.45}
LOCODE_WITHOUT_DIGITS = 0.45  # "itmil01" is a code; a bare five-letter word often is not
# IATA metropolitan codes: city codes that are not airports, used by many operators.
METRO_CODES = {
    "mil": "Milan, IT",
    "lon": "London, GB",
    "par": "Paris, FR",
    "rom": "Rome, IT",
    "nyc": "New York City, US",
    "was": "Washington, US",
    "chi": "Chicago, US",
    "tyo": "Tokyo, JP",
    "osa": "Osaka, JP",
    "sel": "Seoul, KR",
    "mow": "Moscow, RU",
    "sao": "Sao Paulo, BR",
    "rio": "Rio de Janeiro, BR",
    "bue": "Buenos Aires, AR",
    "yto": "Toronto, CA",
    "ymq": "Montreal, CA",
    "stk": "Stockholm, SE",
    "ber": "Berlin, DE",
}
MIN_CITY_POPULATION = 50_000
_SPLIT = re.compile(r"[.\-_]+")
_LETTERS = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Rule:
    operator: str
    domain: str
    pattern: re.Pattern[str]
    kind: str
    codes: dict[str, str]


@lru_cache(maxsize=1)
def default_rules() -> tuple[Rule, ...]:
    text = resources.files("wepwawet.data").joinpath("geohint_rules.toml").read_text(encoding="utf-8")
    return parse_rules(text)


def parse_rules(text: str) -> tuple[Rule, ...]:
    data = tomllib.loads(text)
    return tuple(
        Rule(
            operator=r["operator"],
            domain=r["domain"].lower(),
            pattern=re.compile(r["pattern"]),
            kind=r["kind"],
            codes={k.lower(): v for k, v in (r.get("codes") or {}).items()},
        )
        for r in data.get("rule", [])
    )


def fold(text: str) -> str:
    """Lowercase ASCII without spaces or accents: ``Düsseldorf`` -> ``dusseldorf``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if c.isalnum() and c.isascii()).lower()


@dataclass(frozen=True)
class HintData:
    airports: dict[str, Place]
    locodes: dict[str, Place]
    cities: dict[str, City]  # folded name -> most populous city
    cities_by_country: dict[tuple[str, str], City]
    rules: tuple[Rule, ...]

    @classmethod
    def build(
        cls,
        airports: dict[str, Place],
        locodes: dict[str, Place],
        cities: tuple[City, ...],
        rules: tuple[Rule, ...] | None = None,
    ) -> "HintData":
        by_name: dict[str, City] = {}
        by_country: dict[tuple[str, str], City] = {}
        for city in cities:
            key = fold(city.name)
            big = city.population >= MIN_CITY_POPULATION and len(key) >= 4
            if big and (key not in by_name or city.population > by_name[key].population):
                by_name[key] = city
            country_key = (key, city.country)
            if country_key not in by_country or city.population > by_country[country_key].population:
                by_country[country_key] = city
        return cls(airports, locodes, by_name, by_country, rules if rules is not None else default_rules())


def _hint(token: str, kind: str, name: str, country: str | None, lat: float, lon: float, confidence: float) -> GeoHint:
    return GeoHint(
        token=token,
        kind=kind,
        name=name,
        country=country or None,
        point=GeoPoint(lat=lat, lon=lon),
        confidence=confidence,
    )


def _resolve(code: str, kind: str, data: HintData, rule: Rule | None) -> GeoHint | None:
    confidence = CONFIDENCE["operator"] if rule else CONFIDENCE[kind]
    hint_kind = "operator" if rule else kind
    if kind == "iata":
        airport = data.airports.get(code.upper())
        if airport:
            return _hint(code, hint_kind, airport.name, airport.country, airport.lat, airport.lon, confidence)
        metro = METRO_CODES.get(code.lower())
        if metro:
            name, _, country = metro.rpartition(",")
            city = data.cities_by_country.get((fold(name), country.strip().upper()))
            if city:
                return _hint(code, hint_kind, city.name, city.country, city.lat, city.lon, confidence)
    elif kind == "city":
        city = data.cities.get(fold(code))
        if city:
            return _hint(code, hint_kind, city.name, city.country, city.lat, city.lon, confidence)
    elif kind == "map" and rule is not None:
        target = rule.codes.get(code)
        if target:
            name, _, country = target.rpartition(",")
            city = data.cities_by_country.get((fold(name), country.strip().upper()))
            if city:
                return _hint(code, "operator", city.name, city.country, city.lat, city.lon, confidence)
    return None


def extract_hints(hostname: str | None, data: HintData) -> list[GeoHint]:
    """Candidate places named in ``hostname``, most trusted first, one per place."""
    if not hostname:
        return []
    name = hostname.lower().rstrip(".")
    hints: list[GeoHint] = []
    for rule in data.rules:
        if name == rule.domain or name.endswith("." + rule.domain):
            match = rule.pattern.search(name)
            if match:
                hint = _resolve(match.group("code"), rule.kind, data, rule)
                if hint:
                    hints.append(hint)
    if hints:
        # The operator's own convention named the place: the generic guesses would only add noise.
        return hints
    labels = name.split(".")
    # The last two labels are the operator's domain, not a place.
    for raw in _SPLIT.split(".".join(labels[:-2]) if len(labels) > 2 else ""):
        for found in _LETTERS.finditer(raw):
            token = found.group(0)
            if token in STOPWORDS or len(token) < 3:
                continue
            numbered = found.end() < len(raw) and raw[found.end()].isdigit()
            candidates: list[tuple[str, str]] = []
            if len(token) == 5 and token.upper() in data.locodes:
                candidates.append((token, "locode"))
            if len(token) >= 4:
                candidates.append((token, "city"))
            if len(token) == 3 and token not in AMBIGUOUS_IATA:
                candidates.append((token, "iata"))
            for code, kind in candidates:
                hint = _locode_hint(code, data, numbered) if kind == "locode" else _resolve(code, kind, data, None)
                if hint:
                    hints.append(hint)
                    break
    unique: dict[tuple[str, str | None], GeoHint] = {}
    for hint in sorted(hints, key=lambda h: h.confidence, reverse=True):
        unique.setdefault((fold(hint.name), hint.country), hint)
    return list(unique.values())


def _locode_hint(code: str, data: HintData, numbered: bool) -> GeoHint | None:
    place = data.locodes.get(code.upper())
    if place is None:
        return None
    confidence = CONFIDENCE["locode"] if numbered else LOCODE_WITHOUT_DIGITS
    return _hint(code, "locode", place.name, place.country, place.lat, place.lon, confidence)


def load_hint_data(gazetteer_dir: Path) -> HintData:
    from wepwawet.data.gazetteer import FILE_NAME, load_cities
    from wepwawet.data.places import AIRPORTS_FILE, LOCODE_FILE, load_places

    return HintData.build(
        load_places(gazetteer_dir / AIRPORTS_FILE),
        load_places(gazetteer_dir / LOCODE_FILE),
        load_cities(gazetteer_dir / FILE_NAME),
    )
