"""Call lifecycle and post-call webhook behaviour (sections 5.1-5.3).

Covers the four properties a webhook needs beyond simply working: idempotency,
correlation, out-of-order tolerance, and never dropping an unmatched event.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.db.repository import JsonCallRepository
from app.models.enums import CallStatus, StageCode
from app.models.schemas import InitialMessageRequest, PostCallWebhookPayload
from app.services.call_service import CallNotFound, CallService, new_call_id
from app.services.gnani_client import GnaniClient

TRIGGER_OK = {
    "status": "success",
    "message": "Call is being triggered to 9123456789",
    "response": {"data": None},
}


def make_service(
    repo: JsonCallRepository, settings: Settings, handler=None
) -> CallService:
    """A CallService whose Gnani calls are served by a mock transport."""

    def default_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TRIGGER_OK)

    transport = httpx.MockTransport(handler or default_handler)
    client = GnaniClient(
        settings,
        client=httpx.AsyncClient(
            transport=transport, base_url=settings.gnani_base_url, timeout=1.0
        ),
    )
    return CallService(repo, client, settings)


def post_call_payload(conversation_id: str, **overrides) -> PostCallWebhookPayload:
    body = {
        "conversation_id": conversation_id,
        "event_id": f"evt-{conversation_id}",
        # Note the reserved console field name, not "stage_code".
        "DISPOSITION": "PTP_FUTURE",
        "ptp_date": "the thirtieth",
        "disposition_reason": 'Customer said "I can pay on the thirtieth".',
        "language_captured": "English",
        "customer_sentiment": "Cooperative",
        "call_duration_seconds": 68,
        "call_started_at": datetime(2026, 7, 29, 18, 12, tzinfo=timezone.utc).isoformat(),
        "transcript": [{"speaker": "customer", "text": "I can pay on the thirtieth"}],
    }
    body.update(overrides)
    return PostCallWebhookPayload.model_validate(body)


class TestCallId:
    def test_shape_follows_the_assignment(self) -> None:
        call_id = new_call_id(datetime(2026, 7, 30, tzinfo=timezone.utc))
        assert call_id.startswith("CALL-20260730-")

    def test_ids_are_unique(self) -> None:
        ids = {new_call_id() for _ in range(200)}
        assert len(ids) == 200


class TestInitiate:
    async def test_persists_and_triggers(
        self, repo: JsonCallRepository, settings: Settings,
        initial_request: InitialMessageRequest,
    ) -> None:
        service = make_service(repo, settings)
        record = await service.initiate(initial_request)

        assert record["call_status"] == CallStatus.INITIATED
        assert record["gnani_response"] == TRIGGER_OK
        assert record["customer"]["phone_e164"] == "+919123456789"
        # Correlation key must be stored at insert time.
        assert record["customer"]["phone_suffix"] == "123456789"

    async def test_greeting_is_identity_gated(
        self, repo: JsonCallRepository, settings: Settings,
        initial_request: InitialMessageRequest,
    ) -> None:
        service = make_service(repo, settings)
        record = await service.initiate(initial_request)
        assert "12,500" not in record["initial_message"]

    async def test_two_requests_are_sent_variables_then_trigger(
        self, repo: JsonCallRepository, settings: Settings,
        initial_request: InitialMessageRequest,
    ) -> None:
        """The console does this as two calls; order matters."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=TRIGGER_OK)

        service = make_service(repo, settings, handler)
        await service.initiate(initial_request)

        assert seen == [
            "/analytics/add_pre_call_variables",
            "/genbots/trigger_call/v3/test-bot-id",
        ]

    async def test_trigger_failure_marks_the_call(
        self, repo: JsonCallRepository, settings: Settings,
        initial_request: InitialMessageRequest,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "trigger_call" in request.url.path:
                return httpx.Response(400, text="bad agent")
            return httpx.Response(200, json={"status": "success"})

        service = make_service(repo, settings, handler)
        with pytest.raises(Exception):
            await service.initiate(initial_request)

        calls = await repo.list_calls()
        assert calls[0]["call_status"] == CallStatus.TRIGGER_FAILED
        # The record survives the failure -- it is not rolled back.
        assert "error" in calls[0]["gnani_response"]


class TestCorrelation:
    async def test_dynamic_message_binds_conversation_by_phone(
        self, repo: JsonCallRepository, settings: Settings,
        initial_request: InitialMessageRequest,
    ) -> None:
        """Gnani sends the national number; we stored E.164."""
        service = make_service(repo, settings)
        record = await service.initiate(initial_request)

        text, ctx = await service.resolve_greeting(
            conversation_id="conv-123", mobile="9123456789"
        )

        assert text == record["initial_message"]
        assert ctx["customer_name"] == "Rahul Sharma"
        stored = await repo.get_call(record["call_id"])
        assert stored["gnani_conversation_id"] == "conv-123"
        assert stored["call_status"] == CallStatus.IN_PROGRESS

    @pytest.mark.parametrize(
        "mobile", ["9123456789", "+919123456789", "0919123456789"]
    )
    async def test_any_phone_format_correlates(
        self, repo: JsonCallRepository, settings: Settings,
        initial_request: InitialMessageRequest, mobile: str,
    ) -> None:
        service = make_service(repo, settings)
        await service.initiate(initial_request)
        _, ctx = await service.resolve_greeting(
            conversation_id="conv-x", mobile=mobile
        )
        assert ctx, f"failed to correlate {mobile}"

    async def test_unmatched_call_gets_a_safe_greeting(
        self, repo: JsonCallRepository, settings: Settings
    ) -> None:
        """Never fail this endpoint: a non-2xx would break the live call."""
        service = make_service(repo, settings)
        text, ctx = await service.resolve_greeting(
            conversation_id="ghost", mobile="9999999999"
        )
        assert text  # a usable greeting
        assert ctx == {}
        assert len(repo._data["webhook_dlq"]) == 1  # noqa: SLF001


class TestPostCallWebhook:
    async def _completed_call(self, repo, settings, initial_request):
        service = make_service(repo, settings)
        record = await service.initiate(initial_request)
        await service.resolve_greeting(
            conversation_id="conv-abc", mobile="9123456789"
        )
        return service, record["call_id"]

    async def test_disposition_is_stored_and_corrected(
        self, repo, settings, initial_request
    ) -> None:
        service, call_id = await self._completed_call(repo, settings, initial_request)
        payload = post_call_payload("conv-abc")

        result = await service.apply_post_call(payload, payload.model_dump(mode="json"))

        assert result == {"status": "processed", "call_id": call_id}
        stored = await repo.get_call(call_id)
        assert stored["call_status"] == CallStatus.COMPLETED
        # 30 July is the day after the 29 July call date.
        assert stored["stage_code"] == StageCode.PTP_TOMORROW
        assert stored["ptp_date"] == "2026-07-30"
        assert stored["disposition_adjustments"]  # the correction is recorded
        assert len(stored["conversation_transcript"]) == 1

    async def test_duplicate_delivery_is_absorbed(
        self, repo, settings, initial_request
    ) -> None:
        """Section 5.3: duplicates must not create duplicate records."""
        service, call_id = await self._completed_call(repo, settings, initial_request)
        payload = post_call_payload("conv-abc")
        raw = payload.model_dump(mode="json")

        first = await service.apply_post_call(payload, raw)
        second = await service.apply_post_call(payload, raw)

        assert first["status"] == "processed"
        assert second["status"] == "duplicate_ignored"
        assert second["call_id"] == call_id
        assert len(await repo.list_calls()) == 1

    async def test_event_id_is_derived_when_absent(
        self, repo, settings, initial_request
    ) -> None:
        """Gnani may send no event id; dedupe must still work."""
        service, _ = await self._completed_call(repo, settings, initial_request)
        payload = post_call_payload("conv-abc", event_id=None)
        raw = payload.model_dump(mode="json")

        assert (await service.apply_post_call(payload, raw))["status"] == "processed"
        assert (await service.apply_post_call(payload, raw))[
            "status"
        ] == "duplicate_ignored"

    async def test_unmatched_event_is_dead_lettered(
        self, repo, settings
    ) -> None:
        service = make_service(repo, settings)
        payload = post_call_payload("nobody-knows-me")

        with pytest.raises(CallNotFound):
            await service.apply_post_call(payload, payload.model_dump(mode="json"))

        assert len(repo._data["webhook_dlq"]) == 1  # noqa: SLF001

    async def test_late_webhook_cannot_reopen_a_finished_call(
        self, repo, settings, initial_request
    ) -> None:
        service, call_id = await self._completed_call(repo, settings, initial_request)
        first = post_call_payload("conv-abc")
        await service.apply_post_call(first, first.model_dump(mode="json"))

        # A different event for the same call, arriving late.
        late = post_call_payload("conv-abc", event_id="evt-late", DISPOSITION="RNR")
        await service.apply_post_call(late, late.model_dump(mode="json"))

        stored = await repo.get_call(call_id)
        assert stored["call_status"] == CallStatus.COMPLETED

    async def test_vague_promise_is_not_stored_as_a_ptp(
        self, repo, settings, initial_request
    ) -> None:
        """The end-to-end anti-fabrication path."""
        service, call_id = await self._completed_call(repo, settings, initial_request)
        payload = post_call_payload(
            "conv-abc", DISPOSITION="PTP_FUTURE", ptp_date=""
        )

        await service.apply_post_call(payload, payload.model_dump(mode="json"))

        stored = await repo.get_call(call_id)
        assert stored["stage_code"] == StageCode.UNCLEAR
        assert stored["ptp_date"] is None


class TestPayloadAliases:
    @pytest.mark.parametrize(
        "key", ["DISPOSITION", "disposition", "stage_code", "STAGE_CODE"]
    )
    def test_all_disposition_spellings_are_accepted(self, key: str) -> None:
        """The console requires DISPOSITION; the docs use STAGE_CODE."""
        payload = PostCallWebhookPayload.model_validate(
            {"conversation_id": "c", key: "ALREADY_PAID"}
        )
        assert payload.stage_code == "ALREADY_PAID"

    def test_unknown_fields_are_tolerated(self) -> None:
        """We do not control the payload shape, so it must not fail validation."""
        payload = PostCallWebhookPayload.model_validate(
            {"conversation_id": "c", "some_future_gnani_field": {"nested": 1}}
        )
        assert payload.model_extra["some_future_gnani_field"] == {"nested": 1}

    def test_conversation_id_is_required(self) -> None:
        with pytest.raises(ValueError):
            PostCallWebhookPayload.model_validate({"DISPOSITION": "RNR"})
