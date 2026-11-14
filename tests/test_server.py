import httpx

from wepwawet.api.app import create_app
from wepwawet.api.server import BackgroundServer, base_url, bind_socket, new_token
from wepwawet.config import Config


def test_background_server_serves_and_stops() -> None:
    sock = bind_socket("127.0.0.1", 0)
    url = base_url(sock)
    assert url.startswith("http://127.0.0.1:") and not url.endswith(":0")
    server = BackgroundServer(create_app(Config(), new_token(), static_dir=None), sock)
    server.start()
    try:
        assert httpx.get(f"{url}/api/health", timeout=5).json() == {"status": "ok"}
    finally:
        server.stop()


def test_tokens_are_unique_and_long() -> None:
    tokens = {new_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(token) >= 40 for token in tokens)
