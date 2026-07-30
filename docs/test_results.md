# Test results

Submission item 17. Results of the twelve mandatory scenarios in section 9, plus
the automated suites.

Every result below was observed on a running system. Where a scenario was
exercised against `tests/mock_gnani` rather than the live platform, that is
stated — the distinction matters, and telephony limits (see
`docs/engineering_log.md` D1–D3) determined which was possible.

---

## Summary

| Evidence | Count |
|---|---|
| Real calls through the live Gnani platform | **5** |
| Scenarios exercised end to end via `mock_gnani` | 10 |
| Failure paths verified against the running API | 3 |
| Automated tests | **135**, all passing |
| Stage-code accuracy on a labelled corpus | **100%** (27/27) |
| Call recordings captured | 5 |
| Real webhook payloads captured | 5 |
| Console and dashboard screenshots | `Screenshots-Console_Dashboard.pdf` |

`ruff` and `mypy` clean across 27 source files.

---

## Real calls through the live platform

Five conversations held through the Agents Console — Prisma transcribing, Evon
running the stage machine, Timbre speaking — with the disposition produced by the
seven configured extraction fields and delivered to
`POST /hook` over a public tunnel.

Audio in `samples/recordings/`, raw payloads in `samples/webhooks/`, both named
by conversation id.

### 1. `PTP_FUTURE` — commits to a future date

| | |
|---|---|
| Call | `CALL-20260730-f712ab` |
| Conversation | `f388ca33-ee71-4549-85b1-439801e1719a` |
| Duration | 126s, 17 turns |
| `ptp_date` | **2026-08-05** |
| Sentiment | Cooperative |

> *The customer said, "is it possible to pay on fifth august" and confirmed with
> "yes" when the payment date was read back.*

**This is the section 2 requirement working end to end.** The customer spoke a
date informally; stage 9 read it back; the customer confirmed; the extraction
cited the confirmation. The disposition rests on an explicit statement, not an
inference — and "fifth august" resolved to a correct ISO date.

### 2. `PTP_PARTIAL` — partial commitment, with a date

| | |
|---|---|
| Call | `CALL-20260730-bbb8f1` |
| Duration | 90s, 11 turns |
| `partial_amount` | **5000** |
| `ptp_date` | 2026-08-02 |

> *The customer said, "I can only manage five thousand right now i'll pay the
> rest tomorrow" and later confirmed paying the remaining amount on "second
> august."*

Captured **both** the partial amount and a date for the remainder, and kept the
code as `PTP_PARTIAL` rather than reclassifying by date — partial is about
amount, not timing.

### 3. `DISPUTE_PAID` — claims payment and contests the balance

| | |
|---|---|
| Call | `CALL-20260730-05ff85` |
| Duration | 102s, 13 turns |
| Sentiment | Frustrated |

> *Customer said "I already paid this" and "I definitely paid it," indicating the
> payment is complete and disputing the lender's balance claim.*

### 4. `ALREADY_PAID` — claims payment, no dispute

| | |
|---|---|
| Call | `CALL-20260730-671714` |
| Duration | 85s, 11 turns |
| Sentiment | Frustrated |

> *Customer said, "I think I already paid this amount" and expressed
> dissatisfaction with being asked again.*

**Cases 3 and 4 are the hardest collision in the stage-code set** and were
separated correctly on live conversations. Both customers said they had paid;
only one contested the balance the lender still claims. The tie-breaker in
`gnani/postcall_extraction_config.md` made the distinction without a
server-side correction.

### 5. `DISPUTE_CHARGES` — contests the amount

| | |
|---|---|
| Call | `CALL-20260730-b63412` |
| Duration | 87s, 13 turns |

> *The customer stated, "i think the amount is wrong i owe less than that"...*

Three dispute-adjacent outcomes — `DISPUTE_PAID`, `ALREADY_PAID`,
`DISPUTE_CHARGES` — resolved to three different codes.

### What the live calls did *not* show

**No guardrail corrections were needed.** `disposition_adjustments` was empty on
all five: the extraction returned defensible codes and the server-side validation
confirmed rather than overrode them.

That is the desirable outcome, but it means the guardrail's *corrective*
behaviour is evidenced by the accuracy harness below, not by these calls. Both
matter: the harness proves it refuses bad dispositions, the live calls prove it
does not interfere with good ones.

---

## Section 9 — the twelve mandatory scenarios

| # | Scenario | Result | Evidence |
|---|---|---|---|
| 1 | Commits to paying today | ✅ `PTP_TODAY` | mock |
| 2 | Provides a future PTP date | ✅ **`PTP_FUTURE`** | **live call**, recording, payload |
| 3 | States payment already complete | ✅ **`ALREADY_PAID`** | **live call**, recording, payload |
| 4 | Requests a callback | ✅ `CALLBACK_SCHEDULED` | mock |
| 5 | Refuses — financial difficulty | ✅ `RTP_FINANCIAL` | mock |
| 6 | Disputes the EMI amount | ✅ **`DISPUTE_CHARGES`** | **live call**, recording, payload |
| 7 | Third party answers | ⚠️ `THIRD_PARTY` | mock only — credits exhausted |
| 8 | Changes language mid-call | ⚠️ `PTP_FUTURE` / Mixed | mock only — credits exhausted |
| 9 | Disconnects without a clear outcome | ✅ `DSCN` | mock |
| 10 | Duplicate post-call webhook | ✅ `200 duplicate_ignored` | **live API** |
| 11 | Invalid initial call request | ✅ `422` | **live API** |
| 12 | Gnani API fails or times out | ✅ `502` / `504` | unit tests + mock |

Two additional outcomes were captured live beyond the required list:
**`PTP_PARTIAL`** and **`DISPUTE_PAID`**.

### Scenarios 7 and 8 — not exercised live

Credits were exhausted before third-party and language-switch calls could be
made (`totalCredit: 20.0`, roughly 2.5 per voice call). Both are implemented and
pass through `mock_gnani`, and the behaviour is specified in
`gnani/system_prompt.md` and `gnani/conversation_flow.md`, but **neither has been
observed on a live conversation.** Stated plainly rather than implied.

Spanish was added to the agent after these five calls were captured, so all
existing audio is English. Section 3.3 is configured — both languages on the
agent, and a Language Switch Prompt naming exactly those two — but the switch
itself has not been demonstrated live.

The identity gate that scenario 7 depends on *was* exercised live — every real
call withheld the amount until identity was confirmed.

---

## Failure paths — verified against the running container

Re-run at the time of writing, against MongoDB, with five real calls in the
database.

### Scenario 10 — duplicate webhook delivery

```
replay a real payload  -> 200 {"status":"duplicate_ignored","call_id":"CALL-20260730-f712ab"}
replay again           -> 200 {"status":"duplicate_ignored","call_id":"CALL-20260730-f712ab"}
rows before / after    -> 5 / 5
```

`200`, never `409` — a non-2xx would invite Gnani to retry and manufacture the
duplicates the requirement asks us to prevent. Enforced by a unique index on
`webhook_events.event_id`, not by application logic.

> **Observed during this run:** the first replay returned `processed`, not
> `duplicate_ignored`, because `clear_demo_data` had wiped the event ledger while
> keeping the call. The event was no longer known, so it was reprocessed — and
> correctly *updated the existing call rather than creating a second one*, since
> correlation is by `conversation_id`. Subsequent replays absorbed as expected.
> Idempotency and correlation are independent mechanisms, and this showed the
> second working when the first had been reset.

### Scenario 11 — invalid initial request

| Input | Result |
|---|---|
| Empty `customer_id` | `422` |
| Malformed phone (`abc`) | `422` |
| Negative `emi_amount` | `422` |

No call record is created in any case.

### Scenario 12 — Gnani unreachable or slow

Covered by `tests/test_gnani_client.py` with a mocked transport, because a real
timeout cannot be produced on demand:

- 5xx → retried, then `GnaniUnavailable` → **`502`**
- Timeout → `GnaniTimeout` → **`504`**
- 4xx → **not retried** (retrying a bad request only multiplies the error)
- Transport failure → `GnaniUnavailable`

On failure the call record is retained and marked `TRIGGER_FAILED`, not rolled
back.

### Webhook authentication

| Request | Result |
|---|---|
| No `X-Webhook-Key` | `401` |
| Wrong key | `401` |
| Correct key | processed |

Auth **fails closed**: if `WEBHOOK_API_KEY` is unset the endpoint returns `500`
rather than accepting anything.

---

## Automated tests

```
135 passed
ruff: All checks passed
mypy: no issues found in 27 source files
```

| Suite | Tests | Covers |
|---|---|---|
| `test_disposition.py` | 51 | Guardrail rules, spoken-date resolution |
| `test_masking_and_phone.py` | 40 | Section 6.1 masking, phone-format correlation |
| `test_greeting.py` | 25 | Identity gate, TTS safety, variable completeness |
| `test_webhook_lifecycle.py` | 25 | Sections 5.1–5.3, idempotency, adoption, ordering |
| `test_gnani_client.py` | 10 | Retry policy, timeouts, real payload shapes |
| `test_bson_encoding.py` | 6 | Regression: BSON date encoding |
| `test_disposition_accuracy.py` | 5 | Labelled corpus, confusion matrix |

Two suites encode defects found by running the system, so they cannot regress:
spoken-ordinal dates (`test_disposition.py`) and phone-format correlation
(`test_masking_and_phone.py`).

---

## Stage-code accuracy

`docs/stage_code_accuracy.md`, generated from
`tests/fixtures/disposition_cases.json`.

**100% (27/27). Nine cases required the guardrail to override what the model
reported, and all nine were handled correctly.**

The second number is the meaningful one. A corpus where nothing is corrected
proves nothing, so a test asserts the corpus contains at least eight correction
cases.

Corrections exercised:

| Model reported | Stored | Why |
|---|---|---|
| `PTP_FUTURE` + "the thirtieth" | `PTP_TOMORROW` | Date resolved; the code disagreed with it |
| `PTP_FUTURE` + "as soon as I can" | `UNCLEAR` | No resolvable date — not a promise |
| `PTP_FUTURE` + "" | `UNCLEAR` | Same |
| `PTP_FUTURE` + a past date | `UNCLEAR` | Cannot promise backwards |
| `PTP_FUTURE` + a date 3 years out | `UNCLEAR` | Beyond `MAX_PTP_DAYS_AHEAD` |
| `PTP_TODAY` + a date 17 days out | `PTP_FUTURE` | Reconciled |
| `MAYBE_WILL_PAY` | `UNCLEAR` | Outside the enum |
| `ALREADY_PAID` + a stray date | `ALREADY_PAID`, date dropped | Non-PTP codes carry no date |
| Spanish `el treinta` | `PTP_TOMORROW` | Resolved, then reconciled |

A cross-corpus invariant is also asserted: **no case can store a `PTP_*` code
without a resolved date.** That is section 2's requirement as a property rather
than a per-case check.

---

## Verified interactively

| Behaviour | Result |
|---|---|
| Identity gate | Amount and due date withheld until identity confirmed — on every live call |
| Lender identification | "ICICI Bank" named when asked "who is this?" |
| TTS pronunciation | "twelve thousand five hundred rupees", not "I N R"; "monthly instalment", not "E M I" |
| Date awareness | A bare day number is no longer read as today |
| Dashboard live update | Rows update on webhook arrival without a reload |
| Dashboard filters | All seven filter correctly, individually and combined |
| Phone masking | `*******7373` everywhere; `phone_e164` absent from all API responses |
| CSV bulk upload | 4 accepted, 1 rejected, per-row errors reported |

---

## A note on the customer panel

All five live calls were started from the Agents Console rather than through
`POST /api/Initial_Message`, because outbound PSTN never worked (D1). The
console does not send customer details for a call it initiates, so those records
carry a disposition, a transcript and a recording but no customer name, phone or
loan account.

The dashboard states this explicitly on the detail page rather than rendering a
panel of blanks, and the records are tagged `origin: gnani_console` to
distinguish them from API-initiated calls. Calls started through the API do
carry the full customer record — visible by running
`python -m scripts.seed_scenarios`.

---

## Known gaps

1. **No PSTN call was ever delivered.** The trigger API returns
   `200 "Call is being triggered"`; no call arrives at either whitelisted number.
   Account-side, escalated, evidenced in `docs/engineering_log.md` D1. All live
   calls above were made through the console's Web-based (Voice) mode, which
   exercises Prisma, Evon, Timbre and the full post-call pipeline — everything
   except the last telephony mile.
2. **Scenarios 7 and 8 have no live evidence.** Credits exhausted.
3. **Timbre 2.5 was not used.** Not provisioned, and documented as unsuitable for
   the required languages — `docs/engineering_log.md` C2.
