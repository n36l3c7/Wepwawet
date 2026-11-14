from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wepwawet import __version__
from wepwawet.api.app import CONTENT_SECURITY_POLICY, SESSION_COOKIE, create_app, format_event
from wepwawet.config import Config

TOKEN = "test-token-123"  # noqa: S105 - test fixture, not a secret


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>Wepwawet</title>", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(static_dir: Path) -> TestClient:
    app = create_app(Config(), TOKEN, static_dir=static_dir, sse_heartbeat_seconds=0.01, sse_max_heartbeats=1)
    return TestClient(app, base_url="http://127.0.0.1:8650")


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_api_needs_token(client: TestClient) -> None:
    assert client.get("/api/meta").status_code == 401


def test_bearer_token(client: TestClient) -> None:
    response = client.get("/api/meta", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json()["version"] == __version__
    assert response.json()["basemap"] == "carto"


def test_wrong_token(client: TestClient) -> None:
    assert client.get("/api/meta", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_auth_sets_cookie_and_redirects(client: TestClient) -> None:
    response = client.get(f"/auth?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/meta").status_code == 200  # the client kept the cookie


def test_auth_rejects_wrong_token(client: TestClient) -> None:
    assert client.get("/auth?token=nope", follow_redirects=False).status_code == 401


def test_foreign_host_is_rejected(client: TestClient) -> None:
    # A DNS-rebinding page would arrive with its own host name.
    response = client.get("/api/health", headers={"Host": "evil.example:8650"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost:8650", "[::1]:8650", "127.0.0.1"])
def test_loopback_hosts_are_accepted(client: TestClient, host: str) -> None:
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_security_headers(client: TestClient) -> None:
    response = client.get("/")
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "<title>Wepwawet</title>" in response.text


def test_fallback_page_when_frontend_missing(tmp_path: Path) -> None:
    client = TestClient(create_app(Config(), TOKEN, static_dir=tmp_path / "missing"), base_url="http://127.0.0.1")
    assert "not built" in client.get("/").text


def test_event_stream(client: TestClient) -> None:
    with client.stream("GET", "/api/events", headers={"Authorization": f"Bearer {TOKEN}"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert body.startswith(format_event("ready", {"version": __version__}))
    assert ": heartbeat" in body


def test_format_event() -> None:
    assert format_event("probe", {"rtt": 1.5}) == 'event: probe\ndata: {"rtt":1.5}\n\n'


def test_ui_connected_callback(static_dir: Path) -> None:
    calls: list[bool] = []
    app = create_app(
        Config(), TOKEN, static_dir=static_dir, sse_max_heartbeats=0, on_ui_connected=lambda: calls.append(True)
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    assert calls == []
    with client.stream("GET", "/api/events", headers={"Authorization": f"Bearer {TOKEN}"}) as response:
        "".join(response.iter_text())
    assert calls == [True]


def test_ui_connected_needs_the_token(static_dir: Path) -> None:
    calls: list[bool] = []
    app = create_app(Config(), TOKEN, static_dir=static_dir, on_ui_connected=lambda: calls.append(True))
    assert TestClient(app, base_url="http://127.0.0.1").get("/api/events").status_code == 401
    assert calls == []
