"""1.2.1: the proxy is where the paths meet, no access correction for routers, software Qt compositing."""

from pathlib import Path

from tests.conftest import make_ctx, report_for
from tests.test_fixes_101 import machine
from tests.test_local_zoom import hop, trace
from wepwawet.core.campaign import Campaign, pick_proxy
from wepwawet.core.models import AccessDelay, GeoPoint, ProbeResult, ProxyTarget
from wepwawet.core.models import Probe as ProbeInfo
from wepwawet.gui.launcher import qt_environment
from wepwawet.paths import AppPaths

TARGET = "151.99.0.10"
BACKBONE = "151.6.50.1"
MEETING = "151.6.200.1"


def test_the_proxy_is_where_most_paths_meet_not_the_most_common_last_router() -> None:
    paths = [
        # Two paths go silent after a backbone router in another city.
        trace(hop(1, "151.6.1.1", 1267), hop(2, BACKBONE, 1267), hop(3, None, None)),
        trace(hop(1, "151.6.1.2", 1267), hop(2, BACKBONE, 1267), hop(3, None, None)),
        # Three paths meet near the target, then end on different access routers.
        trace(hop(1, "151.6.9.1", 1267), hop(2, MEETING, 1267), hop(3, "151.6.201.1", 1267)),
        trace(hop(1, "151.6.9.2", 1267), hop(2, MEETING, 1267), hop(3, "151.6.201.2", 1267)),
        trace(hop(1, "151.6.9.3", 1267), hop(2, MEETING, 1267), hop(3, "151.6.201.3", 1267)),
    ]
    found = pick_proxy(paths, 1267, TARGET)
    assert found is not None and found[0].address == MEETING and found[1] == 3


def test_among_routers_every_path_shares_the_nearest_to_the_target_wins() -> None:
    paths = [trace(hop(1, "151.6.1.1", 1267), hop(2, MEETING, 1267), hop(3, None, None)) for _ in range(2)]
    paths += [trace(hop(1, "151.6.1.1", 1267), hop(2, MEETING, 1267), hop(3, TARGET, 1267))]
    found = pick_proxy(paths, 1267, TARGET)
    assert found is not None and found[0].address == MEETING and found[1] == 3


def test_no_proxy_when_the_paths_share_no_router() -> None:
    paths = [trace(hop(1, f"151.6.{i}.1", 1267), hop(2, None, None)) for i in range(3)]
    assert pick_proxy(paths, 1267, TARGET) is None
    assert pick_proxy(paths[:1], 1267, TARGET) is not None  # a single path is all there is


def probe(rtt: float) -> ProbeResult:
    info = ProbeInfo(provider="globalping", probe_id=str(rtt), location=GeoPoint(lat=45.4, lon=9.2), country="IT")
    return ProbeResult(probe=info, phase="zoom", min_rtt=rtt, max_distance_km=rtt * 100)


def test_access_correction_is_capped_and_never_applied_to_a_router(app_paths: AppPaths) -> None:
    campaign = Campaign(make_ctx(app_paths), [], lambda _l, _s, _m: None)
    report = report_for(TARGET)
    report.probes = [probe(1.5), probe(4.0)]
    report.access = AccessDelay(delay_ms=1.0, technology="fibre", paths=3)
    assert campaign._access_correction(report) == 0.8
    # A step larger than the fastest RTT is a slow reply, not a line: at most 80 % of that RTT.
    report.access = AccessDelay(delay_ms=6.0, technology="unknown", paths=3)
    assert campaign._access_correction(report) == 1.2
    report.proxy = ProxyTarget(address=MEETING, asn=1267, paths=3, of=5, margin_km=30.0)
    assert campaign._access_correction(report) == 0.0


def test_software_rendering_composes_the_qt_window_in_software(tmp_path: Path) -> None:
    real = machine(tmp_path / "real", render_node=True)
    vm = machine(tmp_path / "vm", render_node=True, vendor="QEMU")
    assert qt_environment("auto", {}, real) == {}
    assert qt_environment("auto", {}, vm) == {"QT_QUICK_BACKEND": "software"}
    assert qt_environment("software", {}, real) == {"QT_QUICK_BACKEND": "software"}
    assert qt_environment("hardware", {}, vm) == {}
    assert qt_environment("software", {"QT_QUICK_BACKEND": "rhi"}, real) == {}  # the user's choice wins
