"""Data models shared by the pipeline, the API and the CLI.

Coordinates are WGS84 decimal degrees; distances are kilometres; times are milliseconds.
"""

import ipaddress
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Milliseconds = Annotated[float, Field(ge=0)]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GeoPoint(_Model):
    lat: Latitude
    lon: Longitude


class Target(_Model):
    """What the user asked to locate: an address, optionally reached through a hostname."""

    address: IPAddress
    hostname: str | None = None

    @property
    def version(self) -> int:
        return self.address.version


class Probe(_Model):
    """A vantage point of a measurement provider."""

    provider: str
    probe_id: str
    location: GeoPoint
    city: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    asn: int | None = Field(default=None, ge=0)
    network: str | None = None


class MeasurementKind(StrEnum):
    PING = "ping"
    TRACEROUTE = "traceroute"


class Hop(_Model):
    ttl: int = Field(ge=1, le=255)
    address: IPAddress | None = None  # None: no reply at this TTL
    rtts: tuple[Milliseconds, ...] = ()
    hostname: str | None = None
    asn: int | None = Field(default=None, ge=0)

    @property
    def min_rtt(self) -> float | None:
        return min(self.rtts) if self.rtts else None


class Measurement(_Model):
    """One probe's result towards one target."""

    kind: MeasurementKind
    probe: Probe
    target: Target
    rtts: tuple[Milliseconds, ...] = ()
    hops: tuple[Hop, ...] = ()
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def min_rtt(self) -> float | None:
        """Minimum RTT: the sample least inflated by queuing, which is what distance bounds need."""
        return min(self.rtts) if self.rtts else None


class EvidenceKind(StrEnum):
    HARD = "hard"  # physical constraint (RTT); never violated by the estimate
    SOFT = "soft"  # hint that ranks candidates (GeoIP, PTR, PeeringDB, RDAP)
    NEGATIVE = "negative"  # rules a place out
    INFO = "info"  # context without a location (routing, registration, classification)


class Evidence(_Model):
    """One finding of one source. ``details`` keeps the structured facts behind ``summary``."""

    source: str
    kind: EvidenceKind
    summary: str
    label: str | None = None  # short name on the map, e.g. the database or facility name
    point: GeoPoint | None = None
    radius_km: float | None = Field(default=None, gt=0)
    weight: float = Field(default=1.0, ge=0, le=1)
    city: str | None = None
    country: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class NetworkClass(StrEnum):
    RESIDENTIAL = "residential"
    MOBILE = "mobile"
    HOSTING = "hosting"
    CDN = "cdn"
    VPN = "vpn"
    EDUCATION = "education"
    BUSINESS = "business"
    UNKNOWN = "unknown"


class NetworkInfo(_Model):
    """Routing and registration facts about the target's prefix."""

    prefix: str | None = None
    asn: int | None = None
    as_name: str | None = None
    announced: bool | None = None
    rir: str | None = None
    rir_country: str | None = None
    netname: str | None = None
    holder: str | None = None


class ProviderStatus(_Model):
    provider: str
    status: Literal["ok", "empty", "skipped", "error"]
    message: str = ""
    duration_ms: float = 0
    cached: bool = False


class Estimate(_Model):
    location: GeoPoint
    uncertainty_km: float = Field(gt=0)
    confidence: int = Field(ge=0, le=100)
    city: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    explanation: tuple[str, ...] = ()
    anycast: bool = False


class ProbeResult(_Model):
    """One vantage point's ping towards the target, reduced to what the geometry needs."""

    probe: Probe
    phase: str  # "sweep", "zoom", "local", "ssh"
    min_rtt: float | None = None
    avg_rtt: float | None = None
    loss: float | None = Field(default=None, ge=0, le=1)
    packets: int = 0
    max_distance_km: float | None = None
    calibrated: bool = False


class CandidateCity(_Model):
    name: str
    country: str
    point: GeoPoint
    population: int = 0
    distance_km: float = 0
    score: float = 0


class Region(_Model):
    """The feasible region: where every RTT constraint allows the target to be."""

    resolution: int
    cells: int
    area_km2: float
    centroid: GeoPoint
    radius_km: float
    geometry: dict[str, Any]  # GeoJSON MultiPolygon
    binding_probes: tuple[str, ...] = ()
    candidates: tuple[CandidateCity, ...] = ()


class AnycastSite(_Model):
    point: GeoPoint
    max_distance_km: float
    probe: str


class AnycastInfo(_Model):
    """Speed-of-light violations: no single place is close enough to every fast probe."""

    detected: bool
    violations: int
    summary: str
    sites: tuple[AnycastSite, ...] = ()


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TargetReport(BaseModel):
    """Everything known about one address. Mutable: the pipeline fills it phase by phase."""

    model_config = ConfigDict(extra="forbid")

    target: Target
    network: NetworkInfo | None = None
    classification: list[NetworkClass] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    providers: list[ProviderStatus] = Field(default_factory=list)
    probes: list[ProbeResult] = Field(default_factory=list)
    region: Region | None = None
    anycast: AnycastInfo | None = None
    estimate: Estimate | None = None


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    hostname: str | None = None
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    targets: list[TargetReport] = Field(default_factory=list)
    skipped: list[tuple[str, str]] = Field(default_factory=list)
    error: str | None = None
