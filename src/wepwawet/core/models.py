"""Data models shared by the pipeline, the API and the CLI.

Coordinates are WGS84 decimal degrees; distances are kilometres; times are milliseconds.
"""

import ipaddress
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

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


class Evidence(_Model):
    source: str
    kind: EvidenceKind
    summary: str
    point: GeoPoint | None = None
    radius_km: float | None = Field(default=None, gt=0)
    weight: float = Field(default=1.0, ge=0, le=1)


class Estimate(_Model):
    location: GeoPoint
    uncertainty_km: float = Field(gt=0)
    confidence: int = Field(ge=0, le=100)
    city: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    explanation: tuple[str, ...] = ()
    anycast: bool = False
