# Demonstration runbook

Assignment section 12 prescribes ten demonstration steps and is graded. This is
those ten steps as a script, with the exact commands and tabs to have open, so
the demo does not depend on live-call luck.

**Total: ~15 minutes.**

---

## Before you start

**Tabs to have open:**

| # | Tab | Purpose |
|---|---|---|
| 1 | `http://127.0.0.1:8000/` | Dashboard |
| 2 | `http://127.0.0.1:8000/docs` | Swagger |
| 3 | Agents Console → your agent → Configuration | Steps 2–3 |
| 4 | Agents Console → Conversation logs | Steps 5–6 |
| 5 | Terminal, split: API logs / commands | Steps 4, 6, 9 |

**Bring it up:**

```bash
docker compose --profile mock up --build      # api + mongo + mock console
python -m scripts.seed_scenarios              # 10 scenarios + 3 failure paths
```

Confirm before you present: `/health` returns ok, the dashboard shows 10+ calls
with varied stage codes, and the WebSocket pill in the header reads **live**.

> Have `scripts/seed_scenarios.py` ready to re-run. If anything goes sideways
> mid-demo, it repopulates the dashboard in ~30 seconds through the real code
> path.

---

## Step 1 — Explain the architecture *(2 min)*

Show the mermaid diagram in `README.md` §2. Walk the numbered path: initiate →
two Gnani calls → dial → greeting callback → conversation → disposition →
webhook → dashboard.

**Land this point:** the trigger response returns `{"data": null}` — no call or
conversation id. So the Dynamic Messages callback is the *only* place Gnani
tells us a `conversation_id` for a call we started. It is both the greeting
provider **and** the correlation mechanism. That single constraint shaped the
whole design.

## Step 2 — Show the console configuration *(1 min)*

Configuration tab: System Prompt, Conversation Flow (greeting, ending, ten
pre-call variables), Analytics (Base Instructions + seven typed extraction
fields).

**Mention:** the disposition field *must* be named `DISPOSITION`. Naming it
`stage_code` makes the entire bot config fail to save — while the documentation's
own example uses `STAGE_CODE`. Found by reading the 400 response body.

## Step 3 — Confirm Prisma, Timbre and Evon *(1 min)*

Transcriber → **Prisma**. LLM Model → **Evon v2.0** (not `Fast`, chosen for
instruction adherence over latency). Voice → **Timbre G v1.0**.

**Get ahead of the Timbre 2.5 question before they ask it.** Their docs list
`timbre-v2.5` as supporting ten Indian languages only — no Spanish, no `en-US`.
Section 3.3 requires English (US) and Spanish. So Timbre 2.5 is *technically
unsuitable* for this use case, and separately is not provisioned for this
account. Show the model dropdown with exactly one option. README §9.2.

## Step 4 — Initiate a call from FastAPI *(1 min)*

```bash
curl -X POST http://127.0.0.1:8000/api/Initial_Message \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"CUST900","customer_name":"Rahul Sharma",
       "phone_number":"9123456789","country_code":"+91",
       "loan_account_number":"LAN123456","emi_amount":12500,
       "emi_due_date":"2026-07-30","preferred_language":"en-US","currency":"INR"}'
```

Point at the response: `202`, a `call_id`, a **masked** phone, and a greeting
that names the lender and the account's last four — **and no amount**.

## Step 5 — Demonstrate a multi-turn conversation *(3 min)*

Use the console's **Chat Window** (text, not voice — credits are limited to 20
total). Run four probes in order:

1. *"who is this?"* → must **not** state the amount before you confirm identity
2. confirm you are Rahul → now it speaks the disclosure line
3. *"I can pay on the thirtieth"* → then talk for two more turns; it must
   **never re-ask** the date
4. *"can you give me a discount?"* → must **refuse**

Then: *"can you speak Spanish"* → it switches **and still knows** the thirtieth.

**Explain the mechanism:** the prompt is a ten-stage machine with a slot ledger,
and the standing rule is *before asking anything, check the ledger*. The
language switch carries the ledger across rather than restarting.

## Step 6 — Show the webhook arriving *(1 min)*

Console **Action Logs** → the post-call trigger firing. Then your terminal:

```
webhook.processed  call_id=CALL-... stage_code=PTP_TOMORROW
```

Both sides of the same event.

## Step 7 — Show the record in the database *(1 min)*

```bash
docker compose exec mongo mongosh emi_voice_agent \
  --quiet --eval 'db.calls.find({}, {call_id:1, stage_code:1, ptp_date:1, _id:0}).limit(5)'
```

Mention the collections: `calls`, `webhook_events` (unique index on `event_id` —
this is what actually enforces idempotency), `webhook_dlq`, `audit_logs`.

## Step 8 — Show the outcome on the dashboard *(2 min)*

Eight summary cards, stage-code chart, seven filters, thirteen columns. Filter by
`stage_code=UNCLEAR`, then open **View Details**.

**Two things to point at on the detail page:**

- The **raw `gnani_response` and `post_call_payload`** panels — proof the
  integration is real, not mocked at the boundary.
- The **yellow adjustment notice**: *"PTP_FUTURE disagreed with resolved date
  2026-07-30; reclassified as PTP_TOMORROW."* The model said one thing, the
  server resolved the actual date and corrected it — and said so.

## Step 9 — Demonstrate failure handling *(2 min)*

Four, all live:

```bash
# 9.11 invalid request -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8000/api/Initial_Message \
  -H 'Content-Type: application/json' -d '{"customer_id":""}'

# webhook without the shared secret -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8000/api/v1/webhooks/post-call \
  -H 'Content-Type: application/json' -d '{"conversation_id":"x"}'
```

Then in Postman, send **"9.10 Duplicate delivery"** twice → `200 processed`, then
`200 duplicate_ignored`, and **the dashboard row count does not change**.

**Explain why 200 and not 409:** a non-2xx would invite Gnani to retry, which
manufactures the very duplicates we are asked to prevent.

Then the guardrail, which is the strongest single moment in the demo — send
**"Guardrail: vague promise"**: the model reports `PTP_FUTURE`, the customer only
said *"I'll try next month"*, and the system stores **`UNCLEAR`**. Section 2
forbids assumption-based dispositions; an LLM asked for a stage code always
returns one, so the refusal has to live in code.

Finish with the harness:

```bash
python -m tests.test_disposition_accuracy
# accuracy: 100.0% (27/27) | corrections: 9/9
```

**Say the second number, not the first.** 100% alone is unimpressive; *"nine of
twenty-seven cases required the server to override the model, and all nine were
handled correctly"* is the real claim. Show `docs/stage_code_accuracy.md`.

## Step 10 — Production readiness *(2 min)*

README §13, then the honest limitations:

**The blocker.** `add_pre_call_variables` → 200. `trigger_call` → 200
`"Call is being triggered to 9123456789"`. **No call is delivered**, to either
whitelisted number. Since their API reports success, the failure is downstream —
telephony entitlement, plan (`totalCredit: 20.0`), or the Indian DND registry.
Escalated with the conversation id and full request/response trace. README §10.1.

**Say plainly:** this blocks the section 15 acceptance line *"the customer
receives a call"*. Everything downstream of the trigger is exercised through
`tests/mock_gnani`, which mirrors the real endpoint shapes.

**The scaling constraint worth raising unprompted:** `preCallVariables` are
keyed to the *bot*, not to a call. Two concurrent calls on one agent would
clobber each other's variables, so trigger+variables are serialised behind a
lock. Production needs per-call variable scoping from Gnani, or one agent per
concurrent stream. This is a platform limitation, not a design choice.

Then: stateless API scales horizontally · queue between webhook and enrichment ·
Mongo replica set · secrets manager over `.env` · `webhook_dlq` depth as the key
alert, because a rising DLQ means correlation is failing.

---

## Questions to have answers ready for

| Question | Answer |
|---|---|
| Why is `ptp_date` a string, not a date? | Customers say "the thirtieth". Forcing the model to resolve it is what failed in live testing. Raw phrase in, resolved in code against the real call date. README §6.2 |
| Why does the guardrail overrule the model? | An LLM asked for a stage code always returns one. Section 2 forbids assumptions, so refusal must be deterministic. README §6.3 |
| Why does stage 9 exist? | The readback manufactures the explicit customer statement the disposition rests on. Without it, extraction is inferring. README §6.4 |
| Why not exact-match the phone? | The trigger takes national + country code separately; the callback sends one undocumented `mobile` field. Suffix matching collapses all formats. README §6.5 |
| Why two storage backends? | Section 7 permits JSON; it makes the loop demonstrable with no infrastructure and is a demo safety net. Mongo is the default. README §6.10 |
| Why is the prompt ordered that way? | The console's own analyser flagged that variables at line 11 defeat KV cache. Restructured to an 89% static prefix. README §6.11 |
| Did you use the STT/TTS API keys? | They are a separate surface (`api.vachana.ai`) and authenticate nothing on the Agents Console. Useful only as supplementary component evidence. README §7.4 |
| What would you do differently? | Ask about telephony entitlement on day one — it was the one blocker no amount of code could route around. |
