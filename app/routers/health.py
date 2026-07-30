"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.config import get_settings
from app.db.repository import CallRepository
from app.routers.deps import get_repo

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
    }


@router.get("/ready", summary="Readiness (checks storage)")
async def ready(
    response: Response, repo: CallRepository = Depends(get_repo)
) -> dict[str, object]:
    storage_ok = await repo.ping()
    if not storage_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if storage_ok else "degraded",
        "storage": get_settings().storage_backend,
        "storage_ok": storage_ok,
    }
