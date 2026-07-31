# Sample artefacts

Evidence captured from real calls, not hand-written examples.

## `recordings/`

Call audio downloaded from the Agents Console, named
`call-<STAGE_CODE>-<conversation_id>.mp3` so the outcome each recording
demonstrates is visible without opening anything.

Regenerate after adding files — the stage code is looked up from the stored call
record, never guessed:

```bash
STORAGE_BACKEND=mongo MONGO_URI=mongodb://localhost:27017   python -m scripts.name_recordings --dry-run
```

| Recording | Call | Stage code | Duration |
|---|---|---|---|
| `call-ALREADY_PAID-c5018b6b-0640-4cca-93e5-7a6b2b5d367a.mp3` | `CALL-20260730-671714` | `ALREADY_PAID` | 85s |
| `call-DISPUTE_CHARGES-da4f69d6-ec4b-4a3b-9abc-32713ed1ffe0.mp3` | `CALL-20260730-b63412` | `DISPUTE_CHARGES` | 87s |
| `call-DISPUTE_PAID-0e6287d4-40db-47f6-996c-5dcaf32dbb7d.mp3` | `CALL-20260730-05ff85` | `DISPUTE_PAID` | 102s |
| `call-PTP_FUTURE-f388ca33-ee71-4549-85b1-439801e1719a.mp3` | `CALL-20260730-f712ab` | `PTP_FUTURE` | 126s |
| `call-PTP_PARTIAL-63eb54b0-eb3d-4c0c-bddd-8db801790b5e.mp3` | `CALL-20260730-bbb8f1` | `PTP_PARTIAL` | 90s |

The conversation id in the filename is the join key. For any recording you can
trace the same conversation through:

- the matching payload in `webhooks/`
- the stored call record — `db.calls.find({gnani_conversation_id: "<id>"})`
- the dashboard detail page, which shows the transcript and the raw payload

## `console-analytics/`

Gnani's own post-call analytics for conversation `c5018b6b…`, the `ALREADY_PAID`
call. Three screenshots: the conversation overview, the Post Call Extraction V2
panel beside the transcript, and the extraction fields in full.

**They independently corroborate the stored record.** Every field the console
reports matches what arrived through the webhook and was written to MongoDB:

| Field | Gnani console | Our record |
|---|---|---|
| Duration | 1m 25s | 85s |
| `customerSentiment` | Frustrated | Frustrated |
| `languageCaptured` | English | English |
| `dispositionReason` | *"Customer said, \"I think I already paid this amount\"…"* | identical |

**The transcript also shows the identity gate working under pressure.** Asked
*"uh yes who is this"* before confirming identity, the agent replied:

> *"I am calling on behalf of ICICI Bank regarding the loan account ending in
> 3456. Am I speaking with Rahul Sharma?"*

It named the lender and the account's last four digits, and **withheld the
amount and the due date** until identity was confirmed — the section 5.2 rule,
observed live rather than asserted.

Gnani's console also surfaces per-turn sentiment and emotion (that turn is
tagged *Neutral / Confusion*), which is richer than the seven fields extracted
here.

## `webhooks/`

Post-call payloads exactly as Gnani sent them, one per stage code observed.
Account-identifying values (`user_name`, `user_id`, `bot_id`, ids tied to the
account rather than the call) are redacted; everything else is verbatim.

These are the documents that revealed the real contract — that the seven
extraction fields arrive nested inside `disposition_result`, that transcript
turns are `{role, content, timestamp}` with Unix floats, and that call timings
live in `call_infra.call_status`. See `docs/engineering_log.md` A8.

Regenerate after further testing:

```bash
STORAGE_BACKEND=mongo MONGO_URI=mongodb://localhost:27017 python -m scripts.export_samples
```

## `bulk_calls.csv`

Input for `POST /api/v1/calls/bulk`. The last row is deliberately invalid — an
empty `customer_name` — to demonstrate that rows are validated independently and
one bad row cannot abort the batch.
