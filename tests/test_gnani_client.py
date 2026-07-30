"""Gnani client: URL construction, retry policy and timeout handling.

Assignment section 8 requires API timeout handling and retry handling. The
policy that matters is *what is not retried*: a 4xx means our request is wrong,
so retrying only multiplies the error.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services.gnani_client import (
    GnaniBadRequest,
    GnaniClient,
    GnaniTimeout,
    GnaniUnavailable,
)

TRIGGER_OK = {
    "status": "success",
    "message": "Call is being triggered to 9123456789",
    "response": {"data": None},
}


def client_with(settings: Settings, handler) -> GnaniClient:
    return GnaniClient(
        settings,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=settings.gnani_base_url,
            timeout=settings.gnani_timeout_seconds,
        ),
    )


async def trigger(client: GnaniClient):
    return await client.trigger_call(
        call_id="CALL-test",
        phone="9123456789",
        country_code="+91",
        name="Rahul Sharma",
    )


class TestUrlConstruction:
    def test_matches_the_endpoint_captured_from_the_console(
        self, settings: Settings
    ) -> None:
        client = client_with(settings, lambda r: httpx.Response(200, json={}))
        assert client.trigger_url() == "/genbots/trigger_call/v3/test-bot-id"

    async def test_environment_is_sent_as_a_query_parameter(
        self, settings: Settings
    ) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=TRIGGER_OK)

        await trigger(client_with(settings, handler))
        assert "environment=production" in seen["url"]

    async def test_trigger_body_carries_only_the_destination(
        self, settings: Settings
    ) -> None:
        """The real API takes phone and countryCode separately, with no ref id."""
        import json

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=TRIGGER_OK)

        await trigger(client_with(settings, handler))
        assert seen["body"] == {
            "phone": "9123456789",
            "name": "Rahul Sharma",
            "countryCode": "+91",
        }


class TestRetryPolicy:
    async def test_server_error_is_retried_then_raises(
        self, settings: Settings
    ) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(503, text="upstream down")

        with pytest.raises(GnaniUnavailable):
            await trigger(client_with(settings, handler))

        # gnani_max_retries=2 in the test settings.
        assert attempts["n"] == 2

    async def test_transient_error_then_success(self, settings: Settings) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(500, text="blip")
            return httpx.Response(200, json=TRIGGER_OK)

        result = await trigger(client_with(settings, handler))
        assert result == TRIGGER_OK
        assert attempts["n"] == 2

    async def test_client_error_is_not_retried(self, settings: Settings) -> None:
        """A 4xx is our bug; retrying cannot fix it."""
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(422, text="bad phone number")

        with pytest.raises(GnaniBadRequest):
            await trigger(client_with(settings, handler))

        assert attempts["n"] == 1

    async def test_timeout_raises_gnani_timeout(self, settings: Settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("too slow", request=request)

        with pytest.raises(GnaniTimeout) as exc:
            await trigger(client_with(settings, handler))

        assert "did not respond" in str(exc.value)

    async def test_connection_failure_raises_unavailable(
        self, settings: Settings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        with pytest.raises(GnaniUnavailable):
            await trigger(client_with(settings, handler))


class TestPreCallVariables:
    async def test_payload_shape(self, settings: Settings) -> None:
        import json

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "success"})

        client = client_with(settings, handler)
        await client.add_pre_call_variables({"customer_name": "Rahul Sharma"})

        assert seen["body"] == {
            "botId": "test-bot-id",
            "preCallVariables": {"customer_name": "Rahul Sharma"},
        }

    async def test_rejection_raises(self, settings: Settings) -> None:
        client = client_with(
            settings, lambda r: httpx.Response(400, text="undeclared variable")
        )
        with pytest.raises(GnaniBadRequest):
            await client.add_pre_call_variables({"nope": "x"})
