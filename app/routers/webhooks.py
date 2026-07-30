"""Section 5.3 -- post-call webhook.

Design notes, all driven by the requirement to absorb duplicate deliveries:

* The raw body is read and stored **before** validation, so a payload whose
  shape we did not anticipate is captured for inspection rather than lost. The
  Agents Console offers no body mapping, so the exact shape is Gnani's to
  choose.
* A duplicate returns ``200 duplicate_ignored``, never ``409``. A non-2xx would
  simply invite Gnani to retry, manufacturing more duplicates.
* An unmatched ``conversation_id`` is dead-lettered and answered ``202``, again
  to stop retry storms for something a retry cannot fix.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response, status

from app.models.schemas import PostCallWebhookPayload, WebhookAck
from app.routers.deps import get_call_service, require_webhook_key
from app.services.call_service import CallNotFound, CallService
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post(
    "/post-call",
    response_model=WebhookAck,
    dependencies=[Depends(require_webhook_key)],
    summary="Receive the post-call disposition from Gnani",
    responses={
        200: {"description": "Processed, or a duplicate that was ignored"},
        202: {"description": "Accepted but unmatched; stored in the dead-letter queue"},
        401: {"description": "Missing or invalid X-Webhook-Key"},
        422: {"description": "Body was not valid JSON or failed validation"},
    },
)
async def post_call(
    request: Request,
    response: Response,
    service: CallService = Depends(get_call_service),
) -> WebhookAck:
    body = await request.body()
    try:
        raw = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        log.warning("webhook.invalid_json", error=str(exc))
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return WebhookAck(status="rejected", detail="Body is not valid JSON")

    if not isinstance(raw, dict):
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return WebhookAck(status="rejected", detail="Body must be a JSON object")

    try:
        payload = PostCallWebhookPayload.model_validate(raw)
    except ValueError as exc:
        # Keep it: an unparseable payload is a contract mismatch worth seeing,
        # not something to drop on the floor.
        await service._repo.dead_letter("post_call_validation_failed", raw)  # noqa: SLF001
        log.warning("webhook.validation_failed", error=str(exc)[:300])
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return WebhookAck(status="rejected", detail="Payload failed validation")

    try:
        result = await service.apply_post_call(payload, raw)
    except CallNotFound as exc:
        response.status_code = status.HTTP_202_ACCEPTED
        return WebhookAck(status="unmatched", detail=str(exc))

    return WebhookAck(status=result["status"], call_id=result.get("call_id"))
