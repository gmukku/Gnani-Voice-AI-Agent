# Engineering log

Every problem encountered building this, how it was diagnosed, and what was done
about it. Grouped by where the problem lived.

Most were not visible from reading documentation. They surfaced from running the
system, reading response bodies, and inspecting the console's own network
traffic.

| Category | Count |
|---|---|
| [A. Undocumented platform behaviour](#a-undocumented-platform-behaviour) | 8 |
| [B. Defects found by running it](#b-defects-found-by-running-it) | 11 |
| [C. Contradictions in the specification](#c-contradictions-in-the-specification) | 4 |
| [D. Blockers outside our control](#d-blockers-outside-our-control) | 3 |

---

## A. Undocumented platform behaviour

### A1 — The Agents Console API is not documented anywhere

**Symptom.** Assignment section 5.1 requires calls to be initiated "through the
Gnani Agent s Console call-trigger API". No such API appears in the public
documentation, which covers only STT/TTS/Voice-Cloning, nor anywhere in the
console UI.

**Diagnosis.** Opened the browser network tab and used the console's own
`Trigger Agent Call` button.

**Finding.** Two requests, not one:

```http
POST https://api.inya.ai/analytics/add_pre_call_variables
{ "botId": "<bot_id>", "preCallVariables": { ... } }

POST https://api.inya.ai/genbots/trigger_call/v3/<bot_id>?environment=production
{ "phone": "9123456789", "name": "Rahul Sharma", "countryCode": "+91" }
```

`agentId` and `bot_id` are the same value. The national number and country code
are separate fields.

**Resolution.** `app/services/gnani_client.py` performs both, in order. Paths are
configuration, not constants, since they are undocumented and may move.

### A2 — The trigger returns no correlation identifier

**Symptom.** Nothing in the trigger response ties it to the later webhook.

```json
{"status":"success","message":"Call is being triggered to 9123456789","response":{"data":null}}
```

`data: null`, and the request body has no field for a reference id of our own.

**Consequence.** Correlation cannot come from the trigger. The Dynamic Messages
callback — which sends `{conversation_id, mobile}` at call start — becomes the
only place the platform reveals a conversation id for a call we started, with a
phone-suffix fallback behind it.

**Resolution.** `CallService.resolve_greeting` binds `conversation_id` to
`call_id`. This constraint shaped the whole correlation design.

### A3 — `DISPOSITION` is a reserved field name

**Symptom.** The entire bot configuration refused to save. The UI said only
"Save changes to test".

**Diagnosis.** Network tab → the failing request's **response** body:

```json
{"status":"failure","message":"A DISPOSITION field is required with enum type and with valid options."}
```

**Finding.** The extraction field must be named `DISPOSITION`. Naming it
`stage_code` invalidates the whole agent config. The documentation's own example
(`C01_Disposition`) uses `STAGE_CODE`.

**Resolution.** Renamed in the console; `PostCallWebhookPayload` accepts
`DISPOSITION`, `disposition`, `stage_code` and `STAGE_CODE` via alias rather than
betting on one.

### A4 — Two API surfaces, easily conflated

| | Gnani APIs | Gnani Agents |
|---|---|---|
| Host | `api.vachana.ai` | `api.inya.ai` |
| Auth | `X-API-Key-ID`, per-scope keys | console session |
| Covers | STT, TTS, Voice Cloning | agents, calls, analytics |
| Documented | yes | no |

The API-key page issues only STT- and TTS-scoped keys. **Neither authenticates
against the Agents Console.** Creating them does not help place calls.

### A5 — The console rejects long webhook URLs

**Symptom.** Entering
`https://<tunnel>.trycloudflare.com/api/v1/webhooks/post-call` produced "enter a
valid URL". No length was stated.

**Diagnosis.** Tested progressively shorter URLs; the same host with a short path
was accepted.

**Resolution.** Added `/hook`, an alias for the same handler with identical
authentication, purely to stay under the limit.

### A6 — The disposition prompt structure differs from the documentation

`C01_Disposition` describes one nine-section prompt returning JSON. The console
instead provides **Base Instructions + typed Data Fields** with per-field
extraction instructions and a Test Extraction button.

The console approach is stronger — an `Enum` field makes an invalid stage code
structurally impossible rather than merely discouraged — so the configuration was
built for it, and the prose version kept as documented stage-code logic.

### A7 — Transcript speaker labels are ambiguous

Turns appear as the agent's name (`EMI Payment Collection`) versus `You`. The
extraction rules depend on "rely only on the customer's words", so the analyst
must know which speaker is which.

**Resolution.** The Base Instructions carry an explicit PARTICIPANTS section, with
a fallback rule: the party asking about payment is the agent.

### A8 — The real post-call payload differs structurally from any documented shape

**Symptom.** The first real webhooks returned `422`, five times — Gnani retried.

```
34 validation errors for PostCallWebhookPayload
transcript.0.speaker  Field required
  input_value={'role': 'assistant', 'content': '...', 'timestamp': 1785436832.7}
```

**Diagnosis.** Because the handler stores the raw body *before* validating, all
five failed deliveries were sitting in `webhook_dlq`. The true contract was read
off a real call rather than guessed.

**Findings.** ~70 top-level keys, of which:

- the seven configured extraction fields are **nested** inside
  `disposition_result` (duplicated as `post_call_extraction_v2`), not top-level
- only the stage code also appears top-level, as `STAGE_CODE`
- transcript turns are `{role, content, timestamp}` with **Unix float**
  timestamps, not `{speaker, text}`
- call timings live in `call_infra.call_status` as `2026/07/31 00:10:32 +0000`
- `rec_path` carries the recording

**Resolution.** A single `model_validator(mode="before")` on
`PostCallWebhookPayload` normalises all of it, so the translation lives in one
place. Explicit top-level values still win, leaving hand-built payloads
unaffected.

**This is the clearest payoff of storing the raw body first.** Had the handler
rejected unparseable payloads outright, the contract would have been invisible.

---

## B. Defects found by running it

### B1 — The agent did not know today's date

From a live transcript:

```
Customer: i can pay on the thirtieth
Agent:    did you mean you will pay today, the thirtieth of July?
Customer: today is not thirty yet today is twenty ninth
```

An LLM has no clock. Since every PTP code is an offset from today, the
disposition was guesswork.

**Fix.** `current_date` / `current_date_spoken` added to the pre-call variables,
plus a stage-5 rule: a bare day number never means today unless it equals today's
date, and dates are read back with the weekday so a misunderstanding surfaces in
one turn.

### B2 — TTS spelled out currency codes and abbreviations

The agent said "Your **E M I** of **U S D** 1,200" — letter by letter.

**Fix.** Currency codes are mapped to spoken words before reaching TTS (`INR` →
"rupees" / "rupias"), the word follows the amount rather than preceding it, and
"EMI" became "monthly instalment". A prompt rule forbids speaking any code aloud.

### B3 — The agent could not identify the caller

Asked "who is this?", it answered "I am calling from the lender" — unconvincing,
and wrong for collections.

**Fix.** `lender_name` added as a variable and surfaced in the greeting.

### B4 — Correlation failed on phone-number format

The Dynamic Messages callback sends `mobile` in national format (`9123456789`)
while records stored E.164 (`+919123456789`). Exact matching failed, the
conversation was never bound, and the post-call webhook was dead-lettered.

**Fix.** Every record stores `phone_suffix`, the trailing nine national digits, so
`9123456789`, `+919123456789` and `0919123456789` collapse to one key.

### B5 — Spoken ordinal dates were unparseable

`resolve_ptp_date("the thirtieth")` returned `None`, so a legitimate commitment
was downgraded to `UNCLEAR`. The guardrail was behaving correctly but rejecting
real promises — and "the thirtieth" is exactly how the customer phrased it.

**Fix.** English ordinals, numeric ordinals (`30th`) and Spanish cardinals
(`el treinta`) now resolve, with next-occurrence logic and invalid-date guarding
(the 31st of a 30-day month rolls forward correctly).

### B6 — `tzdata` missing

`ZoneInfo("America/New_York")` raised `ZoneInfoNotFoundError`. Windows ships no
IANA database, and neither do slim Linux images — it would have failed
identically in Docker.

**Fix.** `tzdata` pinned in `requirements.txt`.

### B7 — Python 3.14 dependency incompatibility

`pydantic-core` has no `cp314` wheel below 2.12, and the source build fails
because PyO3 ≤ 0.22 supports 3.13 at most.

**Fix.** Version floors chosen for 3.14 wheel availability, documented inline so
nobody lowers them.

### B8 — BSON cannot encode `datetime.date`

**Symptom.** Every insert failed against MongoDB:

```
bson.errors.InvalidDocument: cannot encode object: datetime.date(2026, 7, 30)
```

**Why it was hidden.** The JSON backend stringified dates on write, so the raw
`date` objects never reached a strict encoder. Only running against a real
MongoDB exposed it.

**Fix.** `_bson_safe` converts `date` to an ISO string while leaving `datetime`
native, so Mongo can still sort and range-query timestamps. The `datetime` check
must come first — `datetime` subclasses `date`, so the naive order would
stringify every timestamp. Regression test in `tests/test_bson_encoding.py`,
including a property check that no bare date survives anywhere in a full record.

### B9 — The container dialled itself

**Symptom.** Every trigger failed with `httpx.ConnectError` inside Docker, having
worked locally.

**Cause.** `docker-compose.yml` loads `.env` into the container **and** uses it
for variable substitution, so `${GNANI_BASE_URL}` resolved to `127.0.0.1:9100`
from `.env` — which, inside a container, is the container itself.

**Fix.** A distinct `GNANI_BASE_URL_DOCKER` variable, defaulting to the mock
service name.

### B10 — The same shadowing bug, silently, for storage

**Symptom.** MongoDB held 12 calls; the dashboard showed 1. Both were "correct".

**Cause.** Identical to B9: `.env` had `STORAGE_BACKEND=json`, which won the
compose substitution, so the container quietly ran on the **file** backend while
MongoDB sat idle. `/ready` had reported `"storage":"mongo"` earlier only because
a shell variable happened to be set in that session.

**Fix.** `STORAGE_BACKEND_DOCKER`. This one is worth noting because nothing
errored — the system simply wrote to the wrong place.

### B11 — Interaction defects found by clicking through

- **CSV upload dumped raw JSON.** The form posted to the API endpoint, so the
  browser navigated to `{"accepted": 4, ...}`. Now content-negotiated: API clients
  get JSON, browsers get a redirect and a result banner.
- **A dead recording link.** The mock emitted `https://example.invalid/...`, which
  failed on click. It now emits none, and the detail page renders an `<audio>`
  player only when a real recording exists — which is what section 14's playback
  bonus actually asks for.
- **A variable shadowing the HTTP request** (caught by `mypy`): the CSV loop bound
  `request`, shadowing the FastAPI `Request` parameter. It worked only because the
  content-negotiation check ran before the loop.
- **The webhook body was undocumented in Swagger.** Reading the raw body means
  FastAPI cannot derive a request schema, so the one integration surface Gnani
  posts to was invisible on `/docs`. Declared explicitly via `openapi_extra`.
- **`ruff` and `mypy` were documented as passing but were not.** Three unused
  imports, an untyped comprehension, a missing annotation, absent stubs. Fixed
  rather than softening the claim.

---

## C. Contradictions in the specification

Documented rather than silently resolved.

### C1 — Section 5.2 contradicts its own example

The rule: "Sensitive information should not be disclosed until the customer's
identity has been reasonably confirmed." The example immediately below states the
amount and due date *before* asking to confirm identity.

**Decision.** Followed the rule. The greeting names only the lender and the
account's last four digits; the amount waits for confirmation. This is also how
collections must legally work — a debt cannot be disclosed to whoever answers.

### C2 — Sections 3.1 and 3.3 are mutually incompatible

3.1 mandates **Timbre 2.5**. 3.3 mandates **English (US) and Spanish**. But
Gnani's TTS documentation lists `timbre-v2.5` as supporting ten **Indian**
languages only — `hi-IN, en-IN, ta-IN, te-IN, kn-IN, ml-IN, mr-IN, pa-IN, bn-IN,
gu-IN` — with no Spanish and no `en-US`. Its 41 voices are Hindi, Tamil, Bengali,
Gujarati, Marathi, Kannada, Punjabi, Telugu, Malayalam and Hinglish.

Timbre 2.5 is therefore technically unsuitable for this use case, and separately
is not provisioned for this account.

**Decision.** Timbre G v1.0, whose voice catalogue supports the required
languages. Screenshot evidence shows the model dropdown offering exactly one
option.

### C3 — The assignment appears adapted from an Indian use case

3.3 asks for English (US) and Spanish, but the 5.1 sample payload uses
`"country_code": "+91"`, `"preferred_language": "Hindi"`, `LAN123456` and
`12500` — and 3.1's Timbre 2.5 requirement only makes sense for Indian languages.

**Decision.** Implemented 3.3 as written, since it is the explicit language
requirement, while using INR to stay coherent with the Indian test number.

### C4 — Stage-code naming

Section 3.4 and the documentation use `STAGE_CODE`; the console requires
`DISPOSITION` (A3). Both are accepted.

---

## D. Blockers outside our control

### D1 — Outbound PSTN calls are never delivered

Both API calls succeed:

```
POST /analytics/add_pre_call_variables            -> 200
POST /genbots/trigger_call/v3/<bot>?environment=production
  -> 200 {"status":"success","message":"Call is being triggered to 9123456789"}
```

**No call arrives**, at either whitelisted number. Since the API reports success,
the failure is downstream of the Agents Console. Candidates, in order:

1. **No outbound caller number provisioned.** The console exposes *Inbound
   Numbers* only; whitelisting authorises a destination but does not supply an
   origin.
2. **Plan or credit limits.** `user_info` reports `totalCredit: 20.0`. Web-based
   voice works (no telephony); PSTN does not.
3. **Indian DND registry** blocking automated calls to the `+91` number.

Escalated with the conversation id and full request/response trace. This blocks
the section 15 acceptance line "the customer receives a call" — but it is an
account-side issue, not a code defect, and everything downstream of the trigger is
exercised through `tests/mock_gnani` and through real console-initiated calls.

### D2 — Whitelisting OTP does not reach US numbers

`+1 878…` received no OTP and shows 0 test calls; `+91…` received one and
verified. The SMS route appears to be India-only. Consequence: all live testing
uses the Indian number, which is why sample recordings dial `+91`.

### D3 — Credit ceiling

20 credits total, roughly 2.5 per voice call. Conversation iteration was
therefore done in the text Chat Window, with voice calls reserved for evidence
capture.

---

## What this changed about the design

Three decisions exist *because* of what was found here, not by preference:

1. **The webhook stores the raw body before validating** (A8). Without it the real
   payload shape would have been unknowable — the failed deliveries in
   `webhook_dlq` were the only source of truth.
2. **Correlation uses a phone suffix and a Dynamic Messages binding** (A2, B4),
   because the trigger returns no identifier and the platform is inconsistent
   about phone format.
3. **The disposition guardrail can overrule the model** (B1, B5). An LLM asked for
   a stage code always returns one; refusal has to be deterministic, which is why
   `services/disposition.py` re-validates every field and records what it changed.
