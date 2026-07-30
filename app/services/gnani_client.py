"""HTTP client for the Gnani Agents Console.

Every outbound call to Gnani goes through here. That matters for two reasons:

* The outbound call-trigger endpoint is not publicly documented. Isolating it
  behind this class means the rest of the system is built and tested against
  ``tests/mock_gnani`` today, and adopting the real endpoint is a config
  change (``GNANI_BASE_URL`` / ``GNANI_TRIGGER_PATH``) rather than a rewrite.
* Assignment section 8 requires API timeout handling and retry handling; both
  live here rather than being sprinkled through the routers.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings, get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)


class GnaniError(RuntimeError):
    """Base class for Gnani integration failures."""


class GnaniTimeout(GnaniError):
    """Gnani did not respond within the configured timeout."""


class GnaniUnavailable(GnaniError):
    """Gnani returned 5xx, or the connection failed, after all retries."""


class GnaniBadRequest(GnaniError):
    """Gnani rejected the request (4xx). Not retried -- retrying won't help."""


#: Only transport failures and 5xx are worth retrying. A 4xx is a bug in our
#: request and retrying it just multiplies the error.
_RETRYABLE = (httpx.TimeoutException, httpx.TransportError, GnaniUnavailable)


class GnaniClient:
    """Thin async wrapper over the Agents Console REST API."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.gnani_base_url,
            timeout=httpx.Timeout(self._settings.gnani_timeout_seconds),
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        key = self._settings.gnani_api_key.get_secret_value()
        headers = {"Content-Type": "application/json"}
        if key:
            # Header name differs between the Agents Console and the raw
            # STT/TTS surface (which uses X-API-Key-ID); both are sent so the
            # same client works against either without a code change.
            headers["Authorization"] = f"Bearer {key}"
            headers["X-API-Key-ID"] = key
        return headers

    async def aclose(self) -> None:
        await self._client.aclose()

    async def add_pre_call_variables(self, variables: dict[str, Any]) -> dict[str, Any]:
        """Register the per-call variables, ahead of triggering the call.

        The console does this as a separate request before the trigger. The
        variables are keyed to the bot, not to a specific call, so this must
        immediately precede its trigger -- two concurrent calls to the same
        agent would race. See ``call_service`` for the lock that serialises it.
        """
        payload = {
            "botId": self._settings.gnani_agent_id,
            "preCallVariables": variables,
        }
        response = await self._client.post(
            self._settings.gnani_precall_path, json=payload
        )
        if response.status_code >= 400:
            raise GnaniBadRequest(
                f"Pre-call variables rejected ({response.status_code}): "
                f"{response.text[:200]}"
            )
        log.info("gnani.pre_call_variables.ok", count=len(variables))
        return response.json()

    async def trigger_call(
        self,
        *,
        call_id: str,
        phone: str,
        country_code: str,
        name: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Place an outbound call.

        Two-step, mirroring the console: register the pre-call variables, then
        trigger. Note the trigger body carries only the destination -- the
        national number and country code are separate fields, and there is no
        field for a correlation id of our own.

        Returns the raw Gnani response, stored verbatim on the call record and
        shown on the dashboard detail page (section 6.1).
        """
        if variables:
            await self.add_pre_call_variables(variables)

        payload = {
            "phone": phone,
            "name": name,
            "countryCode": country_code,
        }

        log.info(
            "gnani.trigger_call.start",
            path=self.trigger_url(),
            phone_suffix=phone[-4:],
            call_id=call_id,
        )

        attempt_number = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._settings.gnani_max_retries),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
                retry=retry_if_exception_type(_RETRYABLE),
                reraise=True,
            ):
                with attempt:
                    attempt_number = attempt.retry_state.attempt_number
                    return await self._post_trigger(payload)
        except httpx.TimeoutException as exc:
            log.error("gnani.trigger_call.timeout", attempts=attempt_number)
            raise GnaniTimeout(
                f"Gnani did not respond within "
                f"{self._settings.gnani_timeout_seconds}s"
            ) from exc
        except httpx.TransportError as exc:
            log.error("gnani.trigger_call.transport_error", error=str(exc))
            raise GnaniUnavailable(f"Could not reach Gnani: {exc}") from exc

        raise GnaniUnavailable("Gnani call trigger exhausted all retries")

    def trigger_url(self) -> str:
        """Resolved trigger path for the configured agent.

        The console calls
        ``POST /genbots/trigger_call/v3/{bot_id}?environment=production``,
        where ``bot_id`` is the same value the UI calls ``agentId``.
        """
        return self._settings.gnani_trigger_path.format(
            agent_id=self._settings.gnani_agent_id
        )

    async def _post_trigger(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            self.trigger_url(),
            json=payload,
            params={"environment": self._settings.gnani_environment},
        )

        if response.status_code >= 500:
            # Raised so tenacity retries; converted to a final error if the
            # retries are exhausted.
            raise GnaniUnavailable(
                f"Gnani returned {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            log.error(
                "gnani.trigger_call.rejected",
                status=response.status_code,
                body=response.text[:200],
            )
            raise GnaniBadRequest(
                f"Gnani rejected the request ({response.status_code}): "
                f"{response.text[:200]}"
            )

        data: dict[str, Any] = response.json()
        log.info("gnani.trigger_call.ok", status=response.status_code)
        return data
