"""Section 5.1 -- initiate an outbound call."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.schemas import InitialMessageRequest, InitialMessageResponse
from app.routers.deps import get_call_service
from app.services.call_service import CallService
from app.services.gnani_client import GnaniBadRequest, GnaniTimeout, GnaniUnavailable
from app.utils.logging import get_logger
from app.utils.masking import mask_phone

log = get_logger(__name__)

router = APIRouter(tags=["calls"])


@router.post(
    "/api/Initial_Message",
    response_model=InitialMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Initiate an outbound EMI collection call",
    responses={
        422: {"description": "Request failed validation"},
        502: {"description": "Gnani rejected the trigger or was unreachable"},
        504: {"description": "Gnani did not respond within the timeout"},
    },
)
async def initial_message(
    request: InitialMessageRequest,
    service: CallService = Depends(get_call_service),
) -> InitialMessageResponse:
    """Validate the customer payload, store the call, and trigger via Gnani.

    Returns ``202 Accepted`` rather than ``201``: the call is queued with the
    telephony provider, and the outcome only becomes known later via the
    post-call webhook.
    """
    try:
        record = await service.initiate(request)
    except GnaniTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)
        ) from exc
    except (GnaniUnavailable, GnaniBadRequest) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return InitialMessageResponse(
        call_id=record["call_id"],
        status=record["call_status"],
        message=record["initial_message"],
        e164_phone_masked=mask_phone(record["customer"]["phone_e164"]),
        gnani_response=record.get("gnani_response"),
    )
