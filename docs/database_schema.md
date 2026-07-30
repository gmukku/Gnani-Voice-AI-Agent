# Database schema

Submission item 6. MongoDB is the default backend (section 7 prefers it); a
JSON-file backend stores the same documents and is selected with
`STORAGE_BACKEND=json`.

Four collections: `calls`, `webhook_events`, `webhook_dlq`, `audit_logs`.

---

## `calls`

One document per call. Follows the structure suggested in section 7, with
additions noted below.

```json
{
  "call_id": "CALL-20260730-f712ab",
  "gnani_conversation_id": "f388ca33-ee71-4549-85b1-439801e1719a",
  "origin": "api",

  "customer": {
    "customer_id": "CUST001",
    "customer_name": "Rahul Sharma",
    "phone_number": "9123456789",
    "country_code": "+91",
    "phone_e164": "+919123456789",
    "phone_suffix": "123456789",
    "loan_account_number": "LAN123456"
  },

  "emi_details": {
    "emi_amount": 12500,
    "emi_due_date": "2026-07-30",
    "currency": "INR"
  },

  "call_request": { "...": "the validated POST /api/Initial_Message body" },
  "pre_call_variables": { "...": "the 12 variables sent to Gnani" },
  "initial_message": "Hello, this is a call from ICICI Bank regarding...",

  "gnani_response": { "...": "raw trigger response, stored verbatim" },
  "post_call_payload": { "...": "raw webhook body, stored verbatim" },

  "call_status": "COMPLETED",
  "stage_code": "PTP_FUTURE",
  "disposition_reason": "The customer said, \"is it possible to pay on fifth august\"...",
  "disposition_summary": "The customer's identity was confirmed...",
  "disposition_adjustments": [],
  "ptp_date": "2026-08-05",
  "partial_amount": "",
  "language_captured": "English",
  "customer_sentiment": "Cooperative",

  "conversation_transcript": [
    { "speaker": "agent", "text": "...", "timestamp": "2026-07-31T00:10:32Z" }
  ],
  "call_duration_seconds": 126,
  "recording_url": "prod-18c30c506b63/2026_07_31/...",

  "call_started_at": "2026-07-31T00:10:32Z",
  "call_ended_at": "2026-07-31T00:12:38Z",
  "created_at": "2026-07-30T18:42:00Z",
  "updated_at": "2026-07-30T18:44:10Z"
}
```

### Fields beyond the suggested structure

| Field | Why it exists |
|---|---|
| `gnani_conversation_id` | The trigger response returns `{"data": null}` — no id — so this is bound later, from the Dynamic Messages callback or the post-call webhook. It is the only join key between our record and Gnani's. |
| `customer.phone_suffix` | Correlation key. Gnani's callback sends `mobile` in national format while we store E.164; matching on trailing digits collapses both. See `app/utils/phone.py`. |
| `origin` | `api` for calls started via `/api/Initial_Message`, `gnani_console` for calls adopted from a webhook (Web-based Voice tests, Trigger Agent Call). Console calls have no customer fields — the platform never sends them. |
| `disposition_adjustments` | Human-readable record of every change the guardrail made to the model's disposition. Surfaced on the dashboard so a downgrade is visible rather than silent. |
| `pre_call_variables` | What was actually sent to Gnani, for debugging a call that behaved unexpectedly. |
| `partial_amount`, `customer_sentiment` | Extraction fields configured in the console. |

### Indexes

| Index | Purpose |
|---|---|
| `call_id` **unique** | Primary lookup; prevents duplicate creation |
| `gnani_conversation_id` | Webhook correlation — the hot path |
| `customer.phone_suffix` | Fallback correlation when no conversation id is bound |
| `created_at` | Dashboard ordering |

### Type note

`emi_due_date` and `ptp_date` are stored as **ISO strings**, not BSON dates.
BSON has no date-only type and rejects `datetime.date` outright, while
timestamps (`created_at`, `call_started_at`) stay native so Mongo can sort and
range-query them. `app/db/repository.py::_bson_safe` enforces the split.

---

## `webhook_events`

The idempotency ledger. One document per processed webhook.

```json
{ "event_id": "evt-f388ca33...", "received_at": "2026-07-30T18:42:42Z" }
```

| Index | Purpose |
|---|---|
| `event_id` **unique** | **This is what actually enforces idempotency.** |

The application also checks before inserting, but that check is an optimisation.
Under concurrent delivery, only the unique index guarantees correctness — the
second insert raises `DuplicateKeyError` and the handler answers
`200 duplicate_ignored`.

Gnani sends no `event_id`, so one is derived as a SHA-256 of the canonicalised
payload.

---

## `webhook_dlq`

Webhooks that could not be applied, stored rather than dropped.

```json
{
  "reason": "post_call_validation_failed",
  "payload": { "...": "the raw body, exactly as received" },
  "received_at": "2026-07-30T18:42:42Z"
}
```

Reasons: `post_call_validation_failed`, `post_call_unmatched`,
`dynamic_message_unmatched`.

This collection earned its place during integration. The first real Gnani
webhooks failed validation because the payload shape differed from what had been
assumed — and because the raw body was stored before parsing, the actual
contract could be read off a real call instead of guessed. The parser was fixed
from these documents.

**Operationally, DLQ depth is the key alert.** A rising queue means correlation
is failing, which is invisible from the dashboard alone.

---

## `audit_logs`

Append-only state-transition trail.

```json
{
  "call_id": "CALL-20260730-f712ab",
  "action": "disposition.applied",
  "detail": { "stage_code": "PTP_FUTURE", "ptp_date": "2026-08-05" },
  "at": "2026-07-30T18:44:10Z"
}
```

Actions: `call.created`, `call.triggered`, `call.trigger_failed`, `call.bound`,
`call.adopted`, `disposition.applied`.

---

## Lifecycle

```
INITIATED ──trigger fails──> TRIGGER_FAILED
    │
    └──greeting callback──> IN_PROGRESS ──post-call webhook──> COMPLETED
```

`TRIGGER_FAILED`, `COMPLETED` and `FAILED` are terminal: a late or out-of-order
webhook cannot move a call back out of one, though its payload is still stored.

Calls adopted from the console skip `INITIATED` and are created directly at
`IN_PROGRESS`, then completed by the same webhook.

---

## Inspecting it

```bash
docker compose exec mongo mongosh emi_voice_agent

db.calls.countDocuments({})
db.calls.find({}, {call_id:1, stage_code:1, ptp_date:1, _id:0})
db.calls.find({origin:"gnani_console"})          # real console calls
db.webhook_dlq.find().sort({received_at:-1})     # anything that failed
db.audit_logs.find({call_id:"CALL-..."})         # one call's history
```
