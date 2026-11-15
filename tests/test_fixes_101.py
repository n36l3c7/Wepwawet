"""1.0.1: software rendering for the desktop window, and a caveat when no measurement can run."""

from pathlib import Path

import pytest

from tests.test_measurement import SimulatedNetwork, run_campaign
from wepwawet.config import load_config
from wepwawet.gui.launcher import chromium_flags, is_virtual_or_headless_gpu
from wepwawet.paths import AppPaths
from wepwawet.providers.base import ProviderContext
from wepwawet.providers.measurement.base import Budget


def machine(tmp_path: Path, *, render_node: bool, vendor: str = "ASUSTeK COMPUTER INC.") -> Path:
    if render_node:
        (tmp_path / "dev" / "dri").mkdir(parents=True)
        (tmp_path / "dev" / "dri" / "renderD128").touch()
    dmi = tmp_path / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "sys_vendor").write_text(vendor + "\n", encoding="utf-8")
    return tmp_path


def test_virtual_machines_and_missing_render_nodes_use_software(tmp_path: Path) -> None:
    assert not is_virtual_or_headless_gpu(machine(tmp_path / "a", render_node=True))
    assert is_virtual_or_headless_gpu(machine(tmp_path / "b", render_node=False))
    assert is_virtual_or_headless_gpu(machine(tmp_path / "c", render_node=True, vendor="innotek GmbH"))
    assert is_virtual_or_headless_gpu(machine(tmp_path / "d", render_node=True, vendor="QEMU"))


def test_chromium_flags_per_mode(tmp_path: Path) -> None:
    real = machine(tmp_path / "real", render_node=True)
    vm = machine(tmp_path / "vm", render_node=True, vendor="VMware, Inc.")
    assert chromium_flags("auto", "", real) == ""
    assert chromium_flags("auto", "", vm) == "--use-angle=swiftshader --enable-unsafe-swiftshader"
    assert chromium_flags("software", "--foo", real) == "--foo --use-angle=swiftshader --enable-unsafe-swiftshader"
    assert chromium_flags("hardware", "", vm) == "--ignore-gpu-blocklist"
    # Flags already set by the user are kept and not repeated.
    kept = chromium_flags("software", "--enable-unsafe-swiftshader", real).split()
    assert kept.count("--enable-unsafe-swiftshader") == 1


def test_rendering_from_the_environment(app_paths: AppPaths) -> None:
    assert load_config(app_paths, {}).gui.rendering == "auto"
    assert load_config(app_paths, {"WEPWAWET_RENDERING": "software"}).gui.rendering == "software"


class Exhausted(SimulatedNetwork):
    async def budget(self, ctx: ProviderContext) -> Budget | None:
        return Budget(self.name, "tests/hour", 250, 0, 1560)


async def test_an_exhausted_quota_is_reported(app_paths: AppPaths) -> None:
    report, _logs, _ = await run_campaign(app_paths, Exhausted())
    assert report.probes == [] and report.tests_used == 0
    (caveat,) = report.caveats
    assert caveat.startswith("No measurement ran") and "resets in 26 min" in caveat


async def test_no_caveat_with_tests_left(app_paths: AppPaths) -> None:
    report, _logs, _ = await run_campaign(app_paths, SimulatedNetwork())
    assert report.caveats == []


@pytest.fixture(autouse=True)
def _icmp() -> None:
    SimulatedNetwork.answers_icmp = True
