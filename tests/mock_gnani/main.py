"""A stand-in for the Gnani Agents Console.

Mirrors the endpoint shapes captured from the real console's own network
traffic:

* ``POST /analytics/add_pre_call_variables``          -> ``{"status":"success"}``
* ``POST /genbots/trigger_call/v3/{bot_id}``          -> ``{"status":"success",
  "message":"Call is being triggered to ...","response":{"data":null}}``

...and then does what the real platform does *after* a successful trigger,
which is the part that is currently broken on the account: it calls back into
the application. First the Dynamic Messages callback, then, after a short
delay, the post-call webhook.

Why this exists: the real trigger returns 200 but no call is ever delivered, so
the loop after the trigger cannot be exercised against the live platform. This
lets the full lifecycle -- trigger, greeting, disposition, dashboard update --
be demonstrated and tested end to end, and it is also how the timeout,
duplicate-delivery and failure scenarios are driven deterministically.

Run with:  uvicorn tests.mock_gnani.main:app --port 9100
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")
WEBHOOK_KEY = os.getenv("WEBHOOK_API_KEY", "")

#: Seconds between trigger and the simulated call finishing.
CALL_DURATION = float(os.getenv("MOCK_CALL_SECONDS", "3"))

app = FastAPI(title="Mock Gnani Agents Console", version="1.0.0")

#: Last variables registered, mirroring the real API's bot-scoped behaviour.
_pre_call_variables: dict[str, Any] = {}

#: Scenario transcripts and dispositions, keyed by the name passed as
#: ?scenario=. Covers the section 9 mandatory scenarios.
SCENARIOS: dict[str, dict[str, Any]] = {
    "ptp_today": {
        "DISPOSITION": "PTP_TODAY",
        "ptp_date": "today",
        "disposition_reason": 'Customer said "I will pay it today" and confirmed on readback.',
        "customer_sentiment": "Cooperative",
        "turns": [("customer", "yes speaking"), ("customer", "I will pay it today")],
    },
    "ptp_future": {
        "DISPOSITION": "PTP_FUTURE",
        "ptp_date": "the thirtieth",
        "disposition_reason": 'Customer said "I can pay on the thirtieth" and confirmed on readback.',
        "customer_sentiment": "Cooperative",
        "turns": [("customer", "yes"), ("customer", "I can pay on the thirtieth")],
    },
    "already_paid": {
        "DISPOSITION": "ALREADY_PAID",
        "ptp_date": "",
        "disposition_reason": 'Customer stated "I already paid that last week".',
        "customer_sentiment": "Neutral",
        "turns": [("customer", "I already paid that last week")],
    },
    "callback": {
        "DISPOSITION": "CALLBACK_SCHEDULED",
        "ptp_date": "",
        "disposition_reason": 'Customer asked to be called back "Thursday at four".',
        "customer_sentiment": "Cooperative",
        "turns": [("customer", "call me back thursday at four")],
    },
    "rtp_financial": {
        "DISPOSITION": "RTP_FINANCIAL",
        "ptp_date": "",
        "disposition_reason": 'Customer said "I lost my job, I cannot pay right now".',
        "customer_sentiment": "Frustrated",
        "turns": [("customer", "I lost my job, I cannot pay right now")],
    },
    "dispute_charges": {
        "DISPOSITION": "DISPUTE_CHARGES",
        "ptp_date": "",
        "disposition_reason": 'Customer contested the amount: "the amount is wrong, I owe less".',
        "customer_sentiment": "Frustrated",
        "turns": [("customer", "the amount is wrong, I owe less than that")],
    },
    "third_party": {
        "DISPOSITION": "THIRD_PARTY",
        "ptp_date": "",
        "disposition_reason": "Customer's brother answered; borrower was unavailable.",
        "customer_sentiment": "Neutral",
        "turns": [("customer", "he is my brother, he is not home")],
    },
    "language_switch": {
        "DISPOSITION": "PTP_FUTURE",
        "ptp_date": "the thirtieth",
        "disposition_reason": 'Customer switched to Spanish and committed to "el treinta".',
        "language_captured": "Mixed",
        "customer_sentiment": "Cooperative",
        "turns": [
            ("customer", "can you speak spanish"),
            ("customer", "puedo pagar el treinta"),
        ],
    },
    "dscn": {
        "DISPOSITION": "DSCN",
        "ptp_date": "",
        "disposition_reason": "Call dropped mid-conversation before any outcome.",
        "customer_sentiment": "Neutral",
        "turns": [("customer", "hold on a second")],
    },
    # Deliberately unsupported: the customer never commits. The server-side
    # guardrail must refuse to store this as a promise to pay.
    "vague_promise": {
        "DISPOSITION": "PTP_FUTURE",
        "ptp_date": "",
        "disposition_reason": 'Customer said "I will try next month" -- no specific date.',
        "customer_sentiment": "Neutral",
        "turns": [("customer", "I will try next month")],
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@app.post("/analytics/add_pre_call_variables")
async def add_pre_call_variables(request: Request) -> dict[str, Any]:
    body = await request.json()
    _pre_call_variables.clear()
    _pre_call_variables.update(body.get("preCallVariables", {}))
    return {
        "status": "success",
        "message": "Pre call variables added successfully",
        "response": {"data": None},
    }


@app.post("/genbots/trigger_call/v3/{bot_id}")
async def trigger_call(
    bot_id: str,
    request: Request,
    environment: str = Query(default="production"),
    scenario: str | None = Query(default=None),
    fail: str | None = Query(default=None),
    duplicate: bool = Query(default=False),
) -> dict[str, Any]:
    """Accept a trigger, then drive the callbacks the real platform would.

    ``fail=timeout`` sleeps past the client timeout, ``fail=500`` returns a
    server error, and ``duplicate=true`` delivers the post-call webhook twice --
    the three failure scenarios required by section 9.
    """
    body = await request.json()
    phone = body.get("phone", "")

    if fail == "timeout":
        await asyncio.sleep(30)
    if fail == "500":
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Simulated upstream failure")

    conversation_id = str(uuid.uuid4())
    chosen = scenario or _armed["scenario"]
    asyncio.create_task(
        _run_call(conversation_id, phone, chosen, duplicate)  # noqa: RUF006
    )

    return {
        "status": "success",
        "message": f"Call is being triggered to {phone}",
        "response": {"data": None},
    }


async def _run_call(
    conversation_id: str, phone: str, scenario: str, duplicate: bool
) -> None:
    """Simulate the call: greeting callback, wait, then post-call webhook."""
    spec = SCENARIOS.get(scenario, SCENARIOS["ptp_future"])
    started = _now()

    async with httpx.AsyncClient(base_url=APP_BASE_URL, timeout=15) as client:
        greeting = ""
        try:
            res = await client.post(
                "/api/v1/gnani/dynamic-message",
                json={"conversation_id": conversation_id, "mobile": phone},
            )
            greeting = (
                res.json()
                .get("additional_info", {})
                .get("inya_data", {})
                .get("text", "")
            )
            print(f"[mock] greeting resolved: {greeting[:70]}...")
        except Exception as exc:  # noqa: BLE001
            print(f"[mock] dynamic-message failed: {exc}")

        await asyncio.sleep(CALL_DURATION)
        ended = _now()

        transcript = [
            {
                "speaker": "agent",
                "text": greeting or "Hello, this is a call regarding your loan account.",
                "timestamp": started.isoformat(),
            }
        ]
        for offset, (speaker, text) in enumerate(spec["turns"], start=1):
            transcript.append(
                {
                    "speaker": speaker,
                    "text": text,
                    "timestamp": (started + timedelta(seconds=offset * 5)).isoformat(),
                }
            )

        payload = {
            "conversation_id": conversation_id,
            "event_id": f"evt-{conversation_id}",
            "DISPOSITION": spec["DISPOSITION"],
            "ptp_date": spec["ptp_date"],
            "partial_amount": spec.get("partial_amount", ""),
            "disposition_reason": spec["disposition_reason"],
            "disposition_summary": (
                "Identity confirmed. " + spec["disposition_reason"]
            ),
            "language_captured": spec.get("language_captured", "English"),
            "customer_sentiment": spec.get("customer_sentiment", "Neutral"),
            "call_status": "completed",
            "call_duration_seconds": int((ended - started).total_seconds()),
            "call_started_at": started.isoformat(),
            "call_ended_at": ended.isoformat(),
            "recording_url": f"https://example.invalid/recordings/{conversation_id}.wav",
            "transcript": transcript,
        }

        headers = {"X-Webhook-Key": WEBHOOK_KEY} if WEBHOOK_KEY else {}
        deliveries = 2 if duplicate else 1
        for attempt in range(deliveries):
            try:
                res = await client.post(
                    "/api/v1/webhooks/post-call", json=payload, headers=headers
                )
                print(
                    f"[mock] webhook delivery {attempt + 1}/{deliveries} "
                    f"-> {res.status_code} {res.text[:120]}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[mock] webhook delivery failed: {exc}")


#: Scenario used by the next trigger. The application forwards only
#: ?environment= on the trigger URL, so the scenario is armed out of band --
#: this lets the seeding script drive different outcomes without the app
#: needing to know that scenarios exist at all.
_armed = {"scenario": "ptp_future"}


@app.post("/arm")
async def arm(request: Request) -> dict[str, Any]:
    body = await request.json()
    scenario = body.get("scenario", "ptp_future")
    if scenario not in SCENARIOS:
        return {"status": "unknown_scenario", "known": sorted(SCENARIOS)}
    _armed["scenario"] = scenario
    return {"status": "armed", "scenario": scenario}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "armed": _armed["scenario"], "scenarios": sorted(SCENARIOS)}
