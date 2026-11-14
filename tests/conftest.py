from pathlib import Path

import pytest

from wepwawet.paths import AppPaths


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
