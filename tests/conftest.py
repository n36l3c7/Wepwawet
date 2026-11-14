import ipaddress
from collections.abc import Iterator
from pathlib import Path

import pytest

from wepwawet.config import Config
from wepwawet.core.cache import TTLCache
from wepwawet.core.http import HttpClient
from wepwawet.core.models import IPAddress, Target, TargetReport
from wepwawet.core.secrets import Secrets
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext


@pytest.fixture(autouse=True)
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """No test ever reads or writes the developer's real configuration, data or cache."""
    home = tmp_path_factory.mktemp("home")
    for variable, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(variable, str(home / sub))
    for name in list(__import__("os").environ):
        if name.startswith("WEPWAWET_"):
            monkeypatch.delenv(name)
    yield


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


class FakeDns:
    """DNS answers from dictionaries; unknown names have no records."""

    def __init__(self, hosts: dict[str, list[str]] | None = None, ptrs: dict[str, list[str]] | None = None) -> None:
        self.hosts = hosts or {}
        self.ptrs = ptrs or {}
        self.ptr_queries = 0

    async def resolve_host(self, hostname: str) -> list[IPAddress]:
        return [ipaddress.ip_address(a) for a in self.hosts.get(hostname, [])]

    async def ptr(self, address: IPAddress) -> list[str]:
        self.ptr_queries += 1
        return self.ptrs.get(str(address), [])


def make_ctx(
    paths: AppPaths,
    *,
    secrets: dict[str, str] | None = None,
    dns: FakeDns | None = None,
    config: Config | None = None,
) -> ProviderContext:
    config = config or Config()
    return ProviderContext(
        config=config,
        paths=paths,
        http=HttpClient(TTLCache.in_memory(), config.cache.ttl_for, retries=0, backoff=0),
        dns=dns or FakeDns(),  # type: ignore[arg-type]
        secrets=Secrets(secrets or {}),
    )


@pytest.fixture
def ctx(app_paths: AppPaths) -> ProviderContext:
    return make_ctx(app_paths)


def report_for(address: str) -> TargetReport:
    return TargetReport(target=Target(address=ipaddress.ip_address(address)))
