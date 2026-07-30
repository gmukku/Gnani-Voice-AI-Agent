"""Application factory.

Swagger is served at ``/docs`` (assignment section 8).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import json
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.repository import build_repository
from app.routers import calls, dashboard, gnani, health, webhooks
from app.services.call_service import CallService
from app.services.gnani_client import GnaniClient
from app.utils.logging import (
    bind_call_context,
    clear_call_context,
    configure_logging,
    get_logger,
)
from app.ws.hub import hub

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()

    repo = build_repository(settings)
    await repo.init()
    client = GnaniClient(settings)

    app.state.repo = repo
    app.state.gnani_client = client
    app.state.call_service = CallService(repo, client, settings)

    log.info(
        "app.started",
        environment=settings.environment,
        storage=settings.storage_backend,
        gnani_base_url=settings.gnani_base_url,
        agent_configured=bool(settings.gnani_agent_id),
    )
    try:
        yield
    finally:
        await client.aclose()
        await repo.close()
        log.info("app.stopped")


def _fmt_datetime(value: Any) -> str:
    """Render a timestamp for the dashboard; tolerate strings from JSON storage."""
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M:%S")
    return str(value)


def _fmt_dateonly(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    return str(value)[:10]


def _fmt_duration(seconds: Any) -> str:
    if seconds is None:
        return "—"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _fmt_pretty(value: Any) -> str:
    """Pretty-print the raw Gnani payloads shown on the detail page."""
    if value in (None, {}, []):
        return "— not received —"
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Outbound EMI collection voice agent on the Gnani Agents Console. "
            "Initiates calls, serves the personalised opening message, and "
            "records post-call dispositions."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Bind a request id to every log line emitted while handling it."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        bind_call_context(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            clear_call_context()
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        log.warning("request.validation_failed", path=request.url.path)
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "detail": "Request failed validation",
                "errors": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak an internal message to the caller; the log has the detail.
        log.exception("request.unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Internal server error"},
        )

    app.mount("/static", StaticFiles(directory="static"), name="static")

    for name, fn in {
        "datetime": _fmt_datetime,
        "dateonly": _fmt_dateonly,
        "duration": _fmt_duration,
        "pretty": _fmt_pretty,
    }.items():
        dashboard.templates.env.filters[name] = fn

    app.include_router(health.router)
    app.include_router(calls.router)
    app.include_router(gnani.router)
    app.include_router(webhooks.router)
    app.include_router(dashboard.router)

    @app.websocket("/ws/calls")
    async def calls_socket(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        try:
            while True:
                # No inbound protocol; this just keeps the socket open and
                # detects disconnects.
                await websocket.receive_text()
        except WebSocketDisconnect:
            await hub.disconnect(websocket)
        except Exception:  # noqa: BLE001
            await hub.disconnect(websocket)

    return app


app = create_app()
