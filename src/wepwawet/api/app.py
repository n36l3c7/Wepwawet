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
from wepwawet.core.bulk import BulkJob, parse_targets
from wepwawet.core.events import EventBus
from wepwawet.core.export import FORMATS, render, summary_csv, target_features
from wepwawet.core.history import HistoryError, diff_runs
from wepwawet.core.models import RunReport
from wepwawet.core.settings import RESTART_REQUIRED, SettingsError, SettingsStore, editable_values


class RunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)


class BulkRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=1000)
    max_tests: int = Field(default=500, ge=0, le=100_000)
    passive: bool = False


class SettingsRequest(BaseModel):
    values: dict[str, dict[str, Any]]


class KeyRequest(BaseModel):
    value: str = Field(min_length=1, max_length=1024)


def _home_relative(path: Path) -> str:
    """``~/.config/...`` instead of the full home directory, which names the user."""
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def _safe(text: str) -> str:
    """A file-name-safe version of a query."""
    return "".join(c if c.isalnum() or c in ".-" else "_" for c in text)[:60] or "run"


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

    @app.exception_handler(HistoryError)
    async def unreadable_run(_request: Request, exc: HistoryError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=410)

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
            "basemap": (svc.ctx.config if svc.ctx else config).map.basemap,
            "basemaps": {"carto": {"style": CARTO_STYLE_URL}, "offline": {}},
        }

    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        return svc.provider_status()

    def _store() -> SettingsStore:
        if svc.settings is None or svc.ctx is None:
            raise HTTPException(status_code=503, detail="Settings are not available in this mode")
        return svc.settings

    def _current() -> Config:
        _store()
        return svc.ctx.config if svc.ctx else config

    def _settings_view() -> dict[str, Any]:
        store = _store()
        sources = svc.all_sources()
        return {
            "values": editable_values(_current(), [s["name"] for s in sources]),
            "sources": sources,
            "keys": store.key_status(),
            "env_overrides": store.env_overrides(),
            "restart_required": list(RESTART_REQUIRED),
            "files": {"settings": _home_relative(store.settings_file), "secrets": _home_relative(store.secrets_file)},
        }

    def _apply(change: Callable[[SettingsStore], Config]) -> dict[str, Any]:
        store = _store()
        if svc.busy():
            raise HTTPException(status_code=409, detail="A run is in progress. Wait for it to finish or cancel it.")
        try:
            svc.reconfigure(change(store))
        except SettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _settings_view()

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return _settings_view()

    @app.put("/api/settings")
    def put_settings(body: SettingsRequest) -> dict[str, Any]:
        names = {s["name"] for s in svc.all_sources()}
        return _apply(lambda store: store.update(body.values, names))

    def _key_change(name: str, value: str | None) -> Callable[[SettingsStore], Config]:
        def change(store: SettingsStore) -> Config:
            store.set_key(name, value)
            return _current()

        return change

    @app.put("/api/settings/keys/{name}")
    def put_key(name: str, body: KeyRequest) -> dict[str, Any]:
        return _apply(_key_change(name, body.value))

    @app.delete("/api/settings/keys/{name}")
    def delete_key(name: str) -> dict[str, Any]:
        return _apply(_key_change(name, None))

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

    def _report(run_id: str) -> RunReport:
        run = svc.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="No such run")
        return run

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str, format: str = Query("json")) -> Response:  # noqa: A002 - the query name
        run = _report(run_id)
        if format not in FORMATS:
            raise HTTPException(status_code=400, detail=f"Unknown format; use one of {', '.join(FORMATS)}")
        media, extension = FORMATS[format]
        name = f"wepwawet-{_safe(run.query)}-{run.id}.{'hops.' if format == 'csv' else ''}{extension}"
        return Response(
            render(run, format), media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    @app.get("/api/history")
    def history(query: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        if svc.history is None:
            return []
        return [entry.as_dict() for entry in svc.history.list(query, limit)]

    @app.get("/api/history/diff")
    def history_diff(a: str, b: str, address: str | None = None) -> list[dict[str, str]]:
        return [change.as_dict() for change in diff_runs(_report(a), _report(b), address)]

    @app.delete("/api/history/{run_id}")
    def history_delete(run_id: str) -> dict[str, bool]:
        return {"deleted": bool(svc.history and svc.history.delete(run_id))}

    @app.post("/api/bulk", status_code=202)
    async def start_bulk(body: BulkRequest) -> dict[str, Any]:
        try:
            queries = parse_targets("\n".join(body.queries))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not queries:
            raise HTTPException(status_code=422, detail="No targets in the list")
        try:
            job = svc.start_bulk(queries, body.max_tests, body.passive)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.as_dict()

    def _job(job_id: str) -> BulkJob:
        job = svc.bulk_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such bulk run")
        return job

    @app.get("/api/bulk/{job_id}")
    def get_bulk(job_id: str) -> dict[str, Any]:
        return _job(job_id).as_dict()

    @app.delete("/api/bulk/{job_id}")
    def cancel_bulk(job_id: str) -> dict[str, bool]:
        _job(job_id)
        task = svc.bulk_tasks.get(job_id)
        if task is None or task.done():
            return {"cancelled": False}
        task.cancel()
        return {"cancelled": True}

    @app.get("/api/bulk/{job_id}/export")
    def export_bulk(job_id: str, format: str = Query("csv")) -> Response:  # noqa: A002 - the query name
        job = _job(job_id)
        if format == "csv":
            body, media, extension = summary_csv(job.runs), "text/csv; charset=utf-8", "csv"
        elif format == "geojson":
            features = [
                f
                for run in job.runs
                for t in run.targets
                for f in target_features(t)
                if f["properties"]["kind"] in ("estimate", "uncertainty")
            ]
            body = json.dumps({"type": "FeatureCollection", "features": features}, indent=1)
            media, extension = "application/geo+json", "geojson"
        else:
            raise HTTPException(status_code=400, detail="Use format=csv or format=geojson")
        name = f"wepwawet-bulk-{job.id}.{extension}"
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

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
