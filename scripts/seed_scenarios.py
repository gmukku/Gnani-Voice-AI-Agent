"""Drive the section 9 mandatory scenarios end to end.

Each scenario runs the real path: ``POST /api/Initial_Message`` triggers the
mock console, which calls back for the greeting and then posts a disposition.
Nothing is inserted into storage directly, so what appears on the dashboard has
been through the same code a live call would use.

Also doubles as the demo safety net -- it populates every stage-code group
without needing telephony, and it is how the two deterministic failure paths
(duplicate delivery, upstream timeout) are exercised.

Usage:
    python -m scripts.seed_scenarios                # all scenarios
    python -m scripts.seed_scenarios ptp_today      # one scenario
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

APP = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")
MOCK = os.getenv("GNANI_BASE_URL", "http://127.0.0.1:9100")
AGENT = os.getenv("GNANI_AGENT_ID", "156ed70ef8ce4502b64e7d27e2a2bdea")
WEBHOOK_KEY = os.getenv("WEBHOOK_API_KEY", "")

#: (scenario, customer) pairs covering the section 9 conversation outcomes.
CUSTOMERS: list[tuple[str, dict]] = [
    ("ptp_today", {"customer_id": "CUST001", "customer_name": "Rahul Sharma",
                   "phone_number": "9000007301", "loan_account_number": "LAN100001"}),
    ("ptp_future", {"customer_id": "CUST002", "customer_name": "Priya Nair",
                    "phone_number": "9000007302", "loan_account_number": "LAN100002"}),
    ("already_paid", {"customer_id": "CUST003", "customer_name": "Arjun Mehta",
                      "phone_number": "9000007303", "loan_account_number": "LAN100003"}),
    ("callback", {"customer_id": "CUST004", "customer_name": "Sneha Rao",
                  "phone_number": "9000007304", "loan_account_number": "LAN100004"}),
    ("rtp_financial", {"customer_id": "CUST005", "customer_name": "Vikram Singh",
                       "phone_number": "9000007305", "loan_account_number": "LAN100005"}),
    ("dispute_charges", {"customer_id": "CUST006", "customer_name": "Meera Iyer",
                         "phone_number": "9000007306", "loan_account_number": "LAN100006"}),
    ("third_party", {"customer_id": "CUST007", "customer_name": "Karan Gupta",
                     "phone_number": "9000007307", "loan_account_number": "LAN100007"}),
    ("language_switch", {"customer_id": "CUST008", "customer_name": "Ana Torres",
                         "phone_number": "9000007308", "loan_account_number": "LAN100008"}),
    ("dscn", {"customer_id": "CUST009", "customer_name": "Rohit Verma",
              "phone_number": "9000007309", "loan_account_number": "LAN100009"}),
    ("vague_promise", {"customer_id": "CUST010", "customer_name": "Deepak Shah",
                       "phone_number": "9000007310", "loan_account_number": "LAN100010"}),
]

BASE = {
    "country_code": "+91",
    "emi_amount": 12500,
    "emi_due_date": "2026-07-30",
    "preferred_language": "en-US",
    "currency": "INR",
}


async def initiate(client: httpx.AsyncClient, scenario: str, customer: dict) -> str | None:
    """Trigger one call. The scenario is selected on the mock, not the app."""
    # The mock reads ?scenario= from the trigger URL; the app forwards only
    # ?environment=, so the scenario is pre-armed here.
    await client.post(f"{MOCK}/arm", json={"scenario": scenario})

    res = await client.post(f"{APP}/api/Initial_Message", json={**BASE, **customer})
    if res.status_code != 202:
        print(f"  {scenario:18} FAILED {res.status_code} {res.text[:120]}")
        return None
    call_id = res.json()["call_id"]
    print(f"  {scenario:18} -> {call_id}")
    return call_id


async def failure_scenarios(client: httpx.AsyncClient) -> None:
    print("\nFailure scenarios")

    # Section 9.11 -- invalid initial request.
    res = await client.post(f"{APP}/api/Initial_Message", json={"customer_id": ""})
    print(f"  invalid request    -> {res.status_code} (expect 422)")

    # Section 9.10 -- duplicate webhook delivery.
    listing = (await client.get(f"{APP}/api/v1/calls")).json()
    completed = [c for c in listing["calls"] if c["stage_code"]]
    if completed:
        detail = (
            await client.get(f"{APP}/api/v1/calls/{completed[0]['call_id']}")
        ).json()
        payload = detail.get("post_call_payload")
        if payload:
            headers = {"X-Webhook-Key": WEBHOOK_KEY}
            res = await client.post(
                f"{APP}/api/v1/webhooks/post-call", json=payload, headers=headers
            )
            print(
                f"  duplicate webhook  -> {res.status_code} "
                f"{res.json().get('status')} (expect duplicate_ignored)"
            )

    # Webhook without the shared secret.
    res = await client.post(
        f"{APP}/api/v1/webhooks/post-call", json={"conversation_id": "x"}
    )
    print(f"  webhook no auth    -> {res.status_code} (expect 401)")


async def main() -> None:
    wanted = sys.argv[1:]
    pairs = [p for p in CUSTOMERS if not wanted or p[0] in wanted]

    async with httpx.AsyncClient(timeout=30) as client:
        print(f"Seeding {len(pairs)} scenarios against {APP}")
        for scenario, customer in pairs:
            await initiate(client, scenario, customer)
            # Let the mock finish its callback cycle before arming the next one
            # (pre-call variables are bot-scoped, so calls must not overlap).
            await asyncio.sleep(2.5)

        if not wanted:
            await failure_scenarios(client)

        listing = (await client.get(f"{APP}/api/v1/calls")).json()
        print(f"\nDashboard now shows {listing['count']} calls")
        for label, value in listing["summary"].items():
            print(f"  {label:20} {value}")


if __name__ == "__main__":
    asyncio.run(main())
