"""Call lifecycle orchestration.

Holds the three flows that make up a call:

1. ``initiate``          -- section 5.1, operator triggers an outbound call
2. ``resolve_greeting``  -- section 5.2, Gnani asks us for the opening message
3. ``apply_post_call``   -- section 5.3, Gnani reports the outcome

Two constraints from the real Gnani API shape drive the design here:

* ``preCallVariables`` are keyed to the **bot**, not to a call, and the trigger
  response returns ``{"data": null}`` -- no call or conversation id. So two
  overlapping triggers on one agent would clobber each other's variables.
  ``_trigger_lock`` serialises variables+trigger into one critical section.
* Because the trigger returns no id, correlation depends entirely on the
  Dynamic Messages callback binding ``conversation_id`` to our ``call_id``,
  with a most-recent-pending-call-by-phone fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.db.repository import CallRepository, utcnow
from app.models.enums import TERMINAL_STATUSES, CallStatus
from app.models.schemas import InitialMessageRequest, PostCallWebhookPayload
from app.services import disposition as disposition_service
from app.services.gnani_client import GnaniClient, GnaniError
from app.services.greeting import build_greeting, build_user_context
from app.utils.logging import bind_call_context, get_logger
from app.utils.phone import phone_suffix
from app.ws.hub import hub

log = get_logger(__name__)


class CallNotFound(LookupError):
    """No call record matches the identifiers supplied."""


def new_call_id(now: datetime | None = None) -> str:
    """``CALL-20260730-a1b2c3`` -- the section 7 shape, with a random suffix.

    A per-day counter would read more like the assignment's example but needs a
    read-modify-write per call; a random suffix is collision-safe under
    concurrency and still sorts by day.
    """
    stamp = (now or utcnow()).strftime("%Y%m%d")
    return f"CALL-{stamp}-{secrets.token_hex(3)}"


class CallService:
    def __init__(
        self,
        repo: CallRepository,
        client: GnaniClient,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repo
        self._client = client
        self._settings = settings or get_settings()
        self._trigger_lock = asyncio.Lock()

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self._settings.timezone)

    def _today(self) -> date:
        return datetime.now(self._tz).date()

    # -- 5.1 ------------------------------------------------------------

    async def initiate(self, request: InitialMessageRequest) -> dict[str, Any]:
        """Validate, persist, then trigger. Returns the stored call record."""
        call_id = new_call_id()
        bind_call_context(call_id=call_id)

        language = request.preferred_language
        variables = build_user_context(
            customer_name=request.customer_name,
            loan_account_number=request.loan_account_number,
            emi_amount=request.emi_amount,
            emi_due_date=request.emi_due_date,
            currency=request.currency,
            language=language,
            lender=self._settings.lender_name,
            today=self._today(),
        )
        greeting = build_greeting(
            customer_name=request.customer_name,
            loan_account_number=request.loan_account_number,
            emi_amount=request.emi_amount,
            emi_due_date=request.emi_due_date,
            currency=request.currency,
            language=language,
            lender=self._settings.lender_name,
            override=request.initial_message,
        )

        record: dict[str, Any] = {
            "call_id": call_id,
            "gnani_conversation_id": None,
            "customer": {
                "customer_id": request.customer_id,
                "customer_name": request.customer_name,
                "phone_number": request.phone_number,
                "country_code": request.country_code,
                "phone_e164": request.e164,
                #: Correlation key -- see app/utils/phone.py for why an exact
                #: match on phone_e164 is not reliable.
                "phone_suffix": phone_suffix(request.e164),
                "loan_account_number": request.loan_account_number,
            },
            "emi_details": {
                "emi_amount": request.emi_amount,
                "emi_due_date": request.emi_due_date,
                "currency": request.currency,
            },
            "call_request": request.model_dump(mode="json"),
            "pre_call_variables": variables,
            "initial_message": greeting,
            "gnani_response": None,
            "post_call_payload": None,
            "call_status": CallStatus.INITIATED,
            "stage_code": None,
            "disposition_reason": None,
            "disposition_summary": None,
            "disposition_adjustments": [],
            "ptp_date": None,
            "partial_amount": None,
            "language_captured": None,
            "customer_sentiment": None,
            "conversation_transcript": [],
            "call_duration_seconds": None,
            "recording_url": None,
            "call_started_at": None,
            "call_ended_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await self._repo.insert_call(record)
        await self._repo.audit(call_id, "call.created", {"phone": request.e164})
        await hub.broadcast("call.created", {"call_id": call_id})

        # Variables are bot-scoped and the trigger returns no id, so these two
        # requests must not interleave with another call's pair.
        try:
            async with self._trigger_lock:
                gnani_response = await self._client.trigger_call(
                    call_id=call_id,
                    phone=request.phone_number,
                    country_code=request.country_code,
                    name=request.customer_name,
                    variables=variables,
                )
        except GnaniError as exc:
            log.error("call.trigger_failed", error=str(exc))
            await self._repo.update_call(
                call_id,
                {
                    "call_status": CallStatus.TRIGGER_FAILED,
                    "gnani_response": {"error": str(exc)},
                },
            )
            await self._repo.audit(call_id, "call.trigger_failed", {"error": str(exc)})
            await hub.broadcast(
                "call.updated",
                {"call_id": call_id, "call_status": CallStatus.TRIGGER_FAILED},
            )
            raise

        await self._repo.update_call(call_id, {"gnani_response": gnani_response})
        await self._repo.audit(call_id, "call.triggered", gnani_response)
        await hub.broadcast("call.updated", {"call_id": call_id})

        updated = await self._repo.get_call(call_id)
        return updated or record

    # -- 5.2 ------------------------------------------------------------

    async def resolve_greeting(
        self, *, conversation_id: str, mobile: str
    ) -> tuple[str, dict[str, Any]]:
        """Answer Gnani's Dynamic Messages callback.

        This is also where correlation happens: it is the only point at which
        Gnani tells us a ``conversation_id`` for a call we started, so the
        binding is written here.
        """
        suffix = phone_suffix(mobile)
        call = await self._repo.find_call_by_conversation(conversation_id)
        if call is None:
            call = await self._repo.find_pending_call_by_phone(suffix)

        if call is None:
            # Not ours -- an inbound call, or a test from the console. Answer
            # with something safe rather than failing the call.
            log.warning(
                "dynamic_message.unmatched",
                conversation_id=conversation_id,
                phone_suffix=suffix[-4:],
            )
            await self._repo.dead_letter(
                "dynamic_message_unmatched",
                {"conversation_id": conversation_id, "mobile": mobile},
            )
            return (
                "Hello, this is a call regarding your loan account. "
                "May I confirm who I am speaking with?",
                {},
            )

        call_id = call["call_id"]
        bind_call_context(call_id=call_id, conversation_id=conversation_id)
        await self._repo.update_call(
            call_id,
            {
                "gnani_conversation_id": conversation_id,
                "call_status": CallStatus.IN_PROGRESS,
                "call_started_at": utcnow(),
            },
        )
        await self._repo.audit(
            call_id, "call.bound", {"conversation_id": conversation_id}
        )
        await hub.broadcast(
            "call.updated",
            {"call_id": call_id, "call_status": CallStatus.IN_PROGRESS},
        )
        log.info("dynamic_message.bound", call_id=call_id)
        return call["initial_message"], call.get("pre_call_variables", {})

    # -- 5.3 ------------------------------------------------------------

    @staticmethod
    def derive_event_id(payload: dict[str, Any]) -> str:
        """Stable idempotency key for payloads that carry no event id."""
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def apply_post_call(
        self, payload: PostCallWebhookPayload, raw: dict[str, Any]
    ) -> dict[str, Any]:
        """Process a post-call webhook. Idempotent.

        Returns ``{"status": "processed"|"duplicate_ignored", "call_id": ...}``.
        """
        event_id = payload.event_id or self.derive_event_id(raw)

        if not await self._repo.record_webhook_event(event_id):
            log.info("webhook.duplicate_ignored", event_id=event_id[:12])
            call = await self._repo.find_call_by_conversation(payload.conversation_id)
            return {
                "status": "duplicate_ignored",
                "call_id": call["call_id"] if call else None,
            }

        call = await self._repo.find_call_by_conversation(payload.conversation_id)
        if call is None and payload.call_id:
            call = await self._repo.get_call(payload.call_id)

        if call is None:
            log.warning("webhook.unmatched", conversation_id=payload.conversation_id)
            await self._repo.dead_letter("post_call_unmatched", raw)
            raise CallNotFound(
                f"No call matches conversation_id={payload.conversation_id!r}"
            )

        call_id = call["call_id"]
        bind_call_context(call_id=call_id, conversation_id=payload.conversation_id)

        call_day = self._today()
        if payload.call_started_at:
            call_day = payload.call_started_at.astimezone(self._tz).date()

        result = disposition_service.validate(
            payload.stage_code,
            raw_ptp_date=payload.ptp_date,
            reason=payload.disposition_reason or "",
            call_date=call_day,
            tz=self._settings.timezone,
            max_days_ahead=self._settings.max_ptp_days_ahead,
        )
        if result.adjustments:
            log.info("disposition.adjusted", adjustments=result.adjustments)

        changes: dict[str, Any] = {
            "post_call_payload": raw,
            "stage_code": result.stage_code,
            "disposition_reason": result.reason or payload.disposition_reason,
            "disposition_summary": payload.disposition_summary,
            "disposition_adjustments": result.adjustments,
            "ptp_date": result.ptp_date,
            "partial_amount": payload.partial_amount,
            "language_captured": payload.language_captured,
            "customer_sentiment": payload.customer_sentiment,
            "conversation_transcript": [
                turn.model_dump(mode="json") for turn in payload.transcript
            ],
            "call_duration_seconds": payload.call_duration_seconds,
            "recording_url": payload.recording_url,
            "call_ended_at": payload.call_ended_at or utcnow(),
        }
        if payload.call_started_at:
            changes["call_started_at"] = payload.call_started_at

        # A late or out-of-order webhook must not resurrect a finished call,
        # but its payload is still worth keeping.
        current = call.get("call_status")
        if current in TERMINAL_STATUSES:
            log.warning("webhook.out_of_order", current_status=current)
            changes.pop("call_ended_at", None)
        else:
            changes["call_status"] = CallStatus.COMPLETED

        await self._repo.update_call(call_id, changes)
        await self._repo.audit(
            call_id,
            "disposition.applied",
            {
                "stage_code": str(result.stage_code),
                "ptp_date": result.ptp_date.isoformat() if result.ptp_date else None,
                "adjustments": result.adjustments,
            },
        )
        await hub.broadcast(
            "call.completed",
            {
                "call_id": call_id,
                "stage_code": str(result.stage_code),
                "ptp_date": result.ptp_date.isoformat() if result.ptp_date else None,
            },
        )
        log.info("webhook.processed", stage_code=str(result.stage_code))
        return {"status": "processed", "call_id": call_id}
