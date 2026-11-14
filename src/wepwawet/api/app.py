"""FastAPI application: JSON API under ``/api``, server-sent events, and the built frontend.

Security model (personal tool, local only):

- The server listens on loopback by default (see ``Config.check_bind_address``).
- ``Host`` must be a loopback name, which blocks DNS-rebinding pages from reaching the API.
- Every ``/api`` route except ``/api/health`` needs the session token, sent either as the
  ``wepwawet_session`` cookie (set by ``/auth?token=...``) or as ``Authorization: Bearer``.
"""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wepwawet import __version__
from wepwawet.api.services import Services, default_services
from wepwawet.config import LOOPBACK_HOSTS, Config
from wepwawet.core.events import EventBus


class RunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)


SESSION_COOKIE = "wepwawet_session"
CARTO_STYLE_URL = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"

_CARTO = "https://basemaps.cartocdn.com https://*.basemaps.cartocdn.com"
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # MapLibre sets inline style attributes on its controls and canvas.
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: blob: {_CARTO}",
        f"connect-src 'self' {_CARTO}",
        # MapLibre runs its tile workers from a blob: URL.
        "worker-src 'self' blob:",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

_FALLBACK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Wepwawet</title></head>
<body style="background:#100e0a;color:#eee7d5;font-family:monospace;padding:2rem">
<p>/\\-&gt; The web interface is not built.</p>
<p>Run <code>npm run build</code> in <code>frontend/</code>, then restart Wepwawet.</p></body></html>"""


def _host_allowed(host_header: str | None, allow_remote: bool) -> bool:
    if allow_remote:
        return True
    if not host_header:
        return False
    host = host_header.strip()
    if host.startswith("["):  # [::1]:port
        host = host[1 : host.find("]")] if "]" in host else host
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    # TestClient uses "testserver"; it never leaves the process.
    return host in LOOPBACK_HOSTS or host == "testserver"


def _authorized(request: Request, token: str | None) -> bool:
    if token is None:
        return True
    supplied = request.cookies.get(SESSION_COOKIE, "")
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        supplied = header[7:].strip()
    return bool(supplied) and secrets.compare_digest(supplied.encode(), token.encode())


def create_app(
    config: Config,
    token: str | None,
    static_dir: Path | None = DEFAULT_STATIC_DIR,
    sse_heartbeat_seconds: float = 15.0,
    sse_max_heartbeats: int | None = None,
    on_ui_connected: Callable[[], None] | None = None,
    services: Services | None = None,
) -> FastAPI:
    """Build the application. ``token=None`` disables authentication (tests and explicit opt-out only).

    ``on_ui_connected`` runs each time the interface opens the event stream (used by the smoke test).
    ``services`` defaults to the real providers built from ``config``.
    """
    svc = services or default_services(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await svc.aclose()

    app = FastAPI(
        title="Wepwawet",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.services = svc

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not _host_allowed(request.headers.get("host"), config.server.allow_remote):
            return JSONResponse({"detail": "Host not allowed"}, status_code=400)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health" and not _authorized(request, token):
            return JSONResponse({"detail": "Missing or invalid session token"}, status_code=401)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.get("/auth", include_in_schema=False)
    def auth(token_param: str = Query(alias="token")) -> RedirectResponse:
        if token is None or not secrets.compare_digest(token_param.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="Invalid session token")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta() -> dict[str, object]:
        return {
            "version": __version__,
            "basemap": config.map.basemap,
            "basemaps": {"carto": {"style": CARTO_STYLE_URL}, "offline": {}},
        }

    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        return svc.provider_status()

    @app.get("/api/budget")
    async def budget() -> list[dict[str, Any]]:
        return await svc.budgets()

    @app.post("/api/runs", status_code=202)
    async def start_run(body: RunRequest) -> dict[str, Any]:
        try:
            run = svc.runs.start(body.query)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return [
            {"id": r.id, "query": r.query, "status": r.status, "started_at": r.started_at.isoformat()}
            for r in svc.runs.list()
        ]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = svc.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="No such run")
        return run.model_dump(mode="json")

    @app.delete("/api/runs/{run_id}")
    def cancel_run(run_id: str) -> dict[str, bool]:
        if svc.runs.get(run_id) is None:
            raise HTTPException(status_code=404, detail="No such run")
        return {"cancelled": svc.runs.cancel(run_id)}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        if on_ui_connected is not None:
            on_ui_connected()
        return StreamingResponse(
            _event_stream(request, svc.bus, sse_heartbeat_seconds, sse_max_heartbeats),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def fallback() -> HTMLResponse:
            return HTMLResponse(_FALLBACK_PAGE)

    return app


def format_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _event_stream(
    request: Request, bus: EventBus, heartbeat: float, max_heartbeats: int | None
) -> AsyncIterator[str]:
    async with bus.subscribe() as queue:
        yield format_event("ready", {"version": __version__})
        idle = 0
        while max_heartbeats is None or idle < max_heartbeats:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                if await request.is_disconnected():
                    return
                yield ": heartbeat\n\n"
                idle += 1
                continue
            yield format_event(event.name, event.data)
