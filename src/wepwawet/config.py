"""Configuration: defaults, overridden by ``config.toml``, then by ``settings.toml`` (written by the
settings panel), then by environment variables.

Environment variables:

- ``WEPWAWET_CONFIG``: path of the TOML file (default: ``$XDG_CONFIG_HOME/wepwawet/config.toml``).
- ``WEPWAWET_HOST`` / ``WEPWAWET_PORT``: address of the local server.
- ``WEPWAWET_LOG_LEVEL``: ``DEBUG``, ``INFO``, ``WARNING`` or ``ERROR``.
- ``WEPWAWET_BASEMAP``: ``carto`` or ``offline``.
- ``WEPWAWET_RENDERING``: ``auto``, ``software`` or ``hardware`` (desktop window graphics).
"""

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from wepwawet.paths import AppPaths, default_paths

SETTINGS_FILE = "settings.toml"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class ConfigError(Exception):
    """The configuration file or an environment variable holds an invalid value."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Section):
    host: str = "127.0.0.1"
    # 0 picks a free port at start-up.
    port: int = Field(default=0, ge=0, le=65535)
    # Binding outside loopback is refused unless this is set explicitly.
    allow_remote: bool = False


class MapConfig(_Section):
    basemap: Literal["carto", "offline"] = "carto"


class GuiConfig(_Section):
    # How the desktop window draws the map: "auto" uses software rendering (SwiftShader) in virtual
    # machines and without a GPU render node, "software" always, "hardware" also on blocklisted drivers.
    rendering: Literal["auto", "software", "hardware"] = "auto"


class CacheConfig(_Section):
    default_ttl_seconds: int = Field(default=86_400, gt=0)
    # Per-source overrides, e.g. {"rdap" = 604800}.
    ttl_seconds: dict[str, int] = Field(default_factory=dict)

    @field_validator("ttl_seconds")
    @classmethod
    def _positive(cls, value: dict[str, int]) -> dict[str, int]:
        bad = [name for name, ttl in value.items() if ttl <= 0]
        if bad:
            raise ValueError(f"TTL must be positive for: {', '.join(sorted(bad))}")
        return value

    def ttl_for(self, source: str) -> int:
        return self.ttl_seconds.get(source, self.default_ttl_seconds)


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class MeasurementsConfig(_Section):
    enabled: bool = True
    # One-way speed of light in fibre used for the distance bound (km per ms); 200 is about 2/3 c.
    speed_km_per_ms: float = Field(default=200.0, gt=50, le=300)
    sweep_probes: int = Field(default=48, ge=0, le=500)
    zoom_probes: int = Field(default=30, ge=0, le=500)
    packets: int = Field(default=3, ge=1, le=16)
    # When no probe gets an ICMP reply, measure the TCP handshake to this port instead (0 disables it).
    tcp_fallback_port: int = Field(default=443, ge=0, le=65535)
    # Most tests (Globalping) or credits (RIPE Atlas, per provider max_credits) one target may use.
    max_tests: int = Field(default=150, ge=0)
    # CBG calibration: ping RIPE Atlas anchors from the zoom probes and fit a delay-distance line per probe.
    calibrate: bool = False
    # Traceroutes from the fastest probes, after the pings (0 disables them).
    traceroute_probes: int = Field(default=5, ge=0, le=20)
    # Subtract most of the estimated access delay from every RTT before multilateration.
    access_correction: bool = True
    calibration_anchors: int = Field(default=4, ge=1, le=20)


class ProviderSettings(BaseModel):
    """``[providers.<name>]``: ``enabled`` plus options specific to that provider."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True

    def option(self, key: str, default: Any) -> Any:
        return (self.model_extra or {}).get(key, default)


class Config(_Section):
    server: ServerConfig = Field(default_factory=ServerConfig)
    map: MapConfig = Field(default_factory=MapConfig)
    gui: GuiConfig = Field(default_factory=GuiConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    measurements: MeasurementsConfig = Field(default_factory=MeasurementsConfig)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)

    def provider(self, name: str) -> ProviderSettings:
        return self.providers.get(name) or ProviderSettings()

    def check_bind_address(self) -> None:
        if self.server.host not in LOOPBACK_HOSTS and not self.server.allow_remote:
            raise ConfigError(
                f"Refusing to listen on {self.server.host}: only loopback addresses are allowed. "
                "Set server.allow_remote = true in config.toml to override."
            )


def _env_overrides(environ: dict[str, str]) -> dict[str, Any]:
    overrides: dict[str, dict[str, Any]] = {}
    mapping = {
        "WEPWAWET_HOST": ("server", "host"),
        "WEPWAWET_PORT": ("server", "port"),
        "WEPWAWET_LOG_LEVEL": ("logging", "level"),
        "WEPWAWET_BASEMAP": ("map", "basemap"),
        "WEPWAWET_RENDERING": ("gui", "rendering"),
    }
    for variable, (section, key) in mapping.items():
        if variable in environ:
            value = environ[variable].strip()
            overrides.setdefault(section, {})[key] = value.upper() if key == "level" else value
    return overrides


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_file_path(paths: AppPaths, environ: dict[str, str]) -> Path:
    explicit = environ.get("WEPWAWET_CONFIG", "").strip()
    return Path(explicit) if explicit else paths.config_file


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def load_config(
    paths: AppPaths | None = None, environ: dict[str, str] | None = None, settings_file: Path | None = None
) -> Config:
    """Load the configuration. A missing file means defaults; an invalid one raises ``ConfigError``.

    ``settings_file`` replaces ``settings.toml`` in the configuration directory (used to check a
    change before it is saved).
    """
    paths = paths or default_paths()
    environ = dict(os.environ) if environ is None else environ
    path = config_file_path(paths, environ)

    data: dict[str, Any] = {}
    if path.is_file():
        data = _read(path)
    elif "WEPWAWET_CONFIG" in environ:
        raise ConfigError(f"WEPWAWET_CONFIG points to {path}, which does not exist.")
    sources = str(path)
    settings = settings_file or paths.config_dir / SETTINGS_FILE
    if settings.is_file():
        data = _merge(data, _read(settings))
        sources += f" + {SETTINGS_FILE}"

    try:
        return Config.model_validate(_merge(data, _env_overrides(environ)))
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors())
        raise ConfigError(f"Invalid configuration ({sources}): {details}") from exc
