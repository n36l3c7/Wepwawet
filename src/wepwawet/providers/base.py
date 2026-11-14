"""Provider interfaces.

An enrichment provider reads passive sources about one address and returns evidence. Each
declares its name, the keys it needs, and a stage: stage 1 providers only need the address;
stage 2 providers also read what stage 1 found (for example the origin ASN).
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from wepwawet.config import Config
from wepwawet.core.dns import DnsClient
from wepwawet.core.http import HttpClient
from wepwawet.core.models import Evidence, NetworkClass, NetworkInfo, TargetReport
from wepwawet.core.secrets import Secrets
from wepwawet.paths import AppPaths

LogFn = Callable[[str, str, str], None]  # (level, source, message)


@dataclass
class ProviderContext:
    config: Config
    paths: AppPaths
    http: HttpClient
    dns: DnsClient
    secrets: Secrets
    log: LogFn = field(default=lambda _level, _source, _message: None)


@dataclass
class EnrichmentResult:
    evidence: list[Evidence] = field(default_factory=list)
    network: NetworkInfo | None = None
    classes: list[NetworkClass] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    cached: bool = False


class EnrichmentProvider(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    stage: ClassVar[int] = 1
    # Secret names that must all be present; empty means the provider works without keys.
    required_secrets: ClassVar[tuple[str, ...]] = ()

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        """Why the provider cannot run now (missing key, missing database), or ``None``."""
        missing = [name for name in self.required_secrets if not ctx.secrets.has(name)]
        if missing:
            variables = ", ".join(f"WEPWAWET_{name.upper()}" for name in missing)
            return f"no API key ({variables} or secrets.toml)"
        return None

    @abstractmethod
    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        """Look the target up. Raise ``ProviderError`` when the source fails."""
