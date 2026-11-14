"""Build the shared provider context and the list of enabled providers from the configuration."""

from wepwawet.config import Config
from wepwawet.core.cache import TTLCache
from wepwawet.core.dns import DnsClient
from wepwawet.core.http import HttpClient
from wepwawet.core.secrets import load_secrets
from wepwawet.paths import AppPaths
from wepwawet.providers.base import EnrichmentProvider, LogFn, ProviderContext
from wepwawet.providers.enrichment.censys import CensysProvider
from wepwawet.providers.enrichment.classify import ClassifyProvider
from wepwawet.providers.enrichment.geoip import GeoipProvider
from wepwawet.providers.enrichment.internetdb import InternetDbProvider
from wepwawet.providers.enrichment.ipmap import IpmapProvider
from wepwawet.providers.enrichment.peeringdb import PeeringDbProvider
from wepwawet.providers.enrichment.rdap import RdapProvider
from wepwawet.providers.enrichment.rdns import ReverseDnsProvider
from wepwawet.providers.enrichment.ripestat import RipeStatProvider
from wepwawet.providers.enrichment.shodan import ShodanProvider
from wepwawet.providers.measurement.base import MeasurementProvider
from wepwawet.providers.measurement.globalping import GlobalpingProvider
from wepwawet.providers.measurement.local import LocalPingProvider, SshPingProvider
from wepwawet.providers.measurement.ripe_atlas import RipeAtlasProvider

ALL_ENRICHMENT: list[type[EnrichmentProvider]] = [
    GeoipProvider,
    IpmapProvider,
    RipeStatProvider,
    RdapProvider,
    ReverseDnsProvider,
    PeeringDbProvider,
    InternetDbProvider,
    ShodanProvider,
    CensysProvider,
    ClassifyProvider,
]


# The first available selectable provider leads the campaign: Globalping is free, so it comes first.
ALL_MEASUREMENT: list[type[MeasurementProvider]] = [
    GlobalpingProvider,
    RipeAtlasProvider,
    LocalPingProvider,
    SshPingProvider,
]


def enabled_measurement(config: Config) -> list[MeasurementProvider]:
    return [cls() for cls in ALL_MEASUREMENT if config.provider(cls.name).enabled]


def build_context(config: Config, paths: AppPaths, log: LogFn | None = None) -> ProviderContext:
    paths.ensure()
    cache = TTLCache.open(paths.cache_db)
    ctx = ProviderContext(
        config=config,
        paths=paths,
        http=HttpClient(cache, config.cache.ttl_for),
        dns=DnsClient(),
        secrets=load_secrets(paths.config_dir / "secrets.toml"),
    )
    if log is not None:
        ctx.log = log
    return ctx


def enabled_enrichment(config: Config) -> list[EnrichmentProvider]:
    return [cls() for cls in ALL_ENRICHMENT if config.provider(cls.name).enabled]
