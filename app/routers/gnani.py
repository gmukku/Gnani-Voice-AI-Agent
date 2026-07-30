"""Section 5.2 -- Gnani's Dynamic Messages callback.

Gnani calls this at the start of every call with ``{conversation_id, mobile}``
and expects ``additional_info.inya_data`` with a mandatory ``text`` and
``user_context``. The platform allows 10 seconds, so this endpoint must stay
cheap -- two indexed reads and one update, no outbound calls.

It is also the only place Gnani hands us a ``conversation_id`` for a call we
started, so it doubles as the correlation point for the post-call webhook.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.schemas import (
    AdditionalInfo,
    DynamicMessageRequest,
    DynamicMessageResponse,
    InyaData,
)
from app.routers.deps import get_call_service
from app.services.call_service import CallService
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/gnani", tags=["gnani"])


@router.post(
    "/dynamic-message",
    response_model=DynamicMessageResponse,
    summary="Return the personalised opening message for a call",
)
async def dynamic_message(
    payload: DynamicMessageRequest,
    service: CallService = Depends(get_call_service),
) -> DynamicMessageResponse:
    """Always answers 200.

    A non-2xx here would break the call itself, so an unmatched conversation
    returns a safe generic greeting (and is dead-lettered) rather than an error.
    """
    text, user_context = await service.resolve_greeting(
        conversation_id=payload.conversation_id, mobile=payload.mobile
    )
    return DynamicMessageResponse(
        additional_info=AdditionalInfo(
            inya_data=InyaData(text=text, user_context=user_context)
        )
    )
