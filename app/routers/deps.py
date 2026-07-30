"""Shared dependencies: service access and webhook authentication."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings
from app.db.repository import CallRepository
from app.services.call_service import CallService


def get_repo(request: Request) -> CallRepository:
    return request.app.state.repo


def get_call_service(request: Request) -> CallService:
    return request.app.state.call_service


async def require_webhook_key(
    x_webhook_key: str | None = Header(default=None, alias="X-Webhook-Key"),
) -> None:
    """Protect webhook endpoints (assignment section 8).

    Compared with ``secrets.compare_digest`` so the check does not leak the
    key's length or contents through response timing.
    """
    expected = get_settings().webhook_api_key.get_secret_value()
    if not expected:
        # Fail closed: an unset secret must not silently disable auth.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WEBHOOK_API_KEY is not configured on the server",
        )
    if not x_webhook_key or not secrets.compare_digest(x_webhook_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Webhook-Key",
        )
