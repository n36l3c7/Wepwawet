"""XDG base directories used by Wepwawet.

Configuration lives in ``$XDG_CONFIG_HOME/wepwawet``, persistent data (history,
downloaded databases) in ``$XDG_DATA_HOME/wepwawet`` and disposable data (the TTL
cache) in ``$XDG_CACHE_HOME/wepwawet``.
"""

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "wepwawet"


def _xdg(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "").strip()
    # The XDG spec says relative paths must be ignored.
    if value and Path(value).is_absolute():
        return Path(value)
    return Path.home() / fallback


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def cache_db(self) -> Path:
        return self.cache_dir / "cache.sqlite3"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "wepwawet.log"

    def ensure(self) -> None:
        """Create the directories, readable by the owner only."""
        for directory in (self.config_dir, self.data_dir, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)


def default_paths() -> AppPaths:
    return AppPaths(
        config_dir=_xdg("XDG_CONFIG_HOME", ".config") / APP_NAME,
        data_dir=_xdg("XDG_DATA_HOME", ".local/share") / APP_NAME,
        cache_dir=_xdg("XDG_CACHE_HOME", ".cache") / APP_NAME,
    )
