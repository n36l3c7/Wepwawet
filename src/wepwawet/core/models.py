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
    phase: str  # "sweep", "zoom", "city", or the provider name ("local", "ssh")
    min_rtt: float | None = None
    avg_rtt: float | None = None
    loss: float | None = Field(default=None, ge=0, le=1)
    packets: int = 0
    max_distance_km: float | None = None
    calibrated: bool = False
    # The RTT is impossible from the probe's declared position: the probe is misplaced and ignored.
    outlier: bool = False


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


class GeoHint(_Model):
    """A place named inside a router's hostname (IATA code, UN/LOCODE, city name, operator code)."""

    token: str
    kind: str  # "iata", "locode", "city", "operator"
    name: str
    country: str | None = None
    point: GeoPoint
    confidence: float = Field(ge=0, le=1)


class HopReport(_Model):
    ttl: int
    address: str | None = None  # None: no reply at this TTL
    hostname: str | None = None
    asn: int | None = None
    min_rtt: float | None = None
    hints: tuple[GeoHint, ...] = ()
    ixp: str | None = None
    location: GeoPoint | None = None
    location_source: str | None = None  # "hint", "ixp", "probe"
    location_label: str | None = None


class TracerouteReport(_Model):
    probe: Probe
    protocol: str
    reached: bool
    hops: tuple[HopReport, ...]
    flags: tuple[str, ...] = ()


class Convergence(_Model):
    """The last router shared by several paths: the strongest anchor near the target."""

    address: str
    hostname: str | None = None
    paths: int
    of: int
    location: GeoPoint | None = None
    label: str | None = None
    rtt_to_target_ms: float | None = None  # target RTT minus this hop's RTT, smallest over paths
    max_distance_km: float | None = None


class AccessDelay(_Model):
    """Delay between the last network router and the target: the access line."""

    delay_ms: float
    technology: str  # "datacentre", "fibre", "cable/xDSL", "mobile or fixed wireless", "unknown"
    paths: int


class ProxyTarget(_Model):
    """A router measured in place of an address that answers nothing: the operator's last one before it."""

    address: str
    hostname: str | None = None
    asn: int
    paths: int  # traceroutes whose last router of the operator was this one
    of: int
    margin_km: float  # added to the uncertainty: the line goes on beyond the router


class StageEstimate(_Model):
    """The estimate as it stood after one stage, so the benchmark can show what each stage adds."""

    stage: str  # "passive", "pings", "traceroutes", "landmarks", "final"
    location: GeoPoint
    uncertainty_km: float
    confidence: int


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
    traceroutes: list[TracerouteReport] = Field(default_factory=list)
    convergence: Convergence | None = None
    access: AccessDelay | None = None
    proxy: ProxyTarget | None = None
    tests_used: int = 0  # measurement tests (or credits) this address cost
    stages: list[StageEstimate] = Field(default_factory=list)
    estimate: Estimate | None = None

    def record_stage(self, stage: str, estimate: Estimate | None) -> None:
        if estimate is None:
            return
        self.stages = [s for s in self.stages if s.stage != stage]
        self.stages.append(
            StageEstimate(
                stage=stage,
                location=estimate.location,
                uncertainty_km=estimate.uncertainty_km,
                confidence=estimate.confidence,
            )
        )


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
