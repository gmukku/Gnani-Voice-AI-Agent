# Demonstration runbook

The ten steps of assignment section 12, as a script you can follow on a shared
screen. **About 15 minutes**, plus 30 minutes of setup beforehand.

Written against the system as it actually stands: five real calls in MongoDB,
Docker running, no voice credits remaining.

> **Do not promise a live phone call.** Credits are exhausted, and outbound PSTN
> never worked anyway (`engineering_log.md` D1). The live moment in this demo is
> the **API-to-dashboard loop**, driven through `mock_gnani` — which is real code
> on both ends, not a video. Say that plainly and it lands fine.

---

## T-30 — cold start

Run these in order. If the machine rebooted, all of it is required.

```powershell
$env:Path = "C:\Program Files\Docker\Docker\resources\bin;$env:Path"
cd "F:\Interviews\Gnani AI Interview"

# 1. Start the stack (Docker Desktop must be running first)
docker compose --profile mock up -d

# 2. Wait ~20s, then confirm all three are up
docker compose --profile mock ps
```

**Verify before you present** — if any of these is wrong, fix it now:

```powershell
# must say "storage":"mongo"  -- not "json"
(Invoke-WebRequest "http://localhost:8000/ready" -UseBasicParsing).Content

# must be 5
((Invoke-WebRequest "http://localhost:8000/api/v1/calls" -UseBasicParsing).Content | ConvertFrom-Json).calls.Count
```

Then open the dashboard and check the pill top-right reads **LIVE** in green.
If it says *reconnecting*, the WebSocket is blocked — the demo still works, you
just refresh manually instead of rows updating live.

### Tabs to have open, in this order

| # | Tab | Used in |
|---|---|---|
| 1 | `http://localhost:8000/` — dashboard | 4, 5, 8 |
| 2 | `http://localhost:8000/docs` — Swagger | 4 |
| 3 | Agents Console → your agent → Configuration | 2, 3 |
| 4 | Agents Console → Conversations → `c5018b6b…` | 3, 6 |
| 5 | GitHub repo | 1, 10 |
| 6 | Terminal — logs | 4, 6, 9 |
| 7 | Terminal — commands | 4, 9 |

In tab 6, start the log tail **before** you begin:

```powershell
docker compose logs api -f
```

### One command to have ready but not yet run

This arms the mock with the scenario used in step 4. Run it during setup:

```powershell
Invoke-WebRequest -Uri "http://localhost:9100/arm" -Method POST `
  -ContentType "application/json" -Body '{"scenario":"vague_promise"}' -UseBasicParsing
```

---

## Step 1 — Architecture *(2 min)* · tab 5

Open the repo. Scroll to the mermaid diagram in `README.md` §2.

Walk the numbered path: initiate → two Gnani calls → dial → greeting callback →
conversation → disposition → webhook → dashboard.

**Land one point:**

> "The trigger response returns `data: null` — no call id, nothing to correlate
> on. So the Dynamic Messages callback is the only place the platform tells me a
> conversation id for a call I started. That one constraint shaped the whole
> correlation design."

Then scroll to the **engineering log callout** at the top of the README and say
you'll come back to it.

## Step 2 — Console configuration *(1 min)* · tab 3

Click through: System Prompt → Conversation Flow → Analytics.

Point at the System Prompt header: **1101 words, "Excellent", "Validated"** —
the platform's own rating, not a self-assessment.

**Mention one finding:**

> "The disposition field has to be named `DISPOSITION`. Naming it `stage_code`
> makes the entire bot config fail to save — and their documentation's own
> example uses `STAGE_CODE`. I found that by reading the 400 response body."

## Step 3 — Prisma, Timbre, Evon *(1.5 min)* · tabs 3 → 4

Transcriber → **Gnani/Prisma**. LLM Model → **Evon v2.0**. Voice → **Timbre G**.

Then switch to tab 4 and show a real conversation with its transcript and
latency — the components in use, not just configured.

**Get ahead of the Timbre 2.5 question before they ask it:**

> "The assignment specifies Timbre 2.5. Their TTS docs list it as ten Indian
> languages — no Spanish, no `en-US` — so it can't serve the English plus
> Spanish requirement in 3.3. It's also not provisioned on my account; the
> dropdown has exactly one option. I used Timbre G, which is the global voice
> family, and documented the reasoning."

## Step 4 — Initiate a call, and watch it land ⭐ *(3 min)* · tabs 2, 1, 6

**This is the centrepiece.** Have the dashboard visible alongside Swagger.

In Swagger → `POST /api/Initial_Message` → **Try it out** → Execute with:

```json
{
  "customer_id": "CUST900",
  "customer_name": "Rahul Sharma",
  "phone_number": "9123456789",
  "country_code": "+91",
  "loan_account_number": "LAN123456",
  "emi_amount": 12500,
  "emi_due_date": "2026-08-15",
  "preferred_language": "en-US",
  "currency": "INR"
}
```

Point at the `202` response: a `call_id`, a **masked** phone, and a greeting that
names the lender and the account's last four — **and no amount**.

Switch to the dashboard. Within ~2s a new row appears as `INITIATED`; ~3s later
it flips to `COMPLETED` **without a reload**. Then in tab 6, the log shows:

```
gnani.pre_call_variables.ok → gnani.trigger_call.ok →
dynamic_message.bound → disposition.adjusted → webhook.processed
```

**Say what is real here:**

> "The Gnani side is a local mock, because outbound calling never worked on my
> account. Everything else is the production path — validation, the two-request
> trigger, the greeting callback, correlation, the guardrail, storage, the
> WebSocket push."

**Now open that row.** It will show `UNCLEAR` with an amber notice:

> "The extraction reported `PTP_FUTURE`. The customer only said *'I'll try next
> month'* — intent without a date. A promise with no date isn't a promise, so
> the server refused it and recorded why. Section 2 says the disposition must
> rest on explicit customer statements; an LLM asked for a stage code always
> returns one, so the refusal has to live in code."

## Step 5 — Multi-turn conversation *(2 min)* · tab 1

Go back and open **`CALL-20260730-f712ab`** — the real `PTP_FUTURE` call.

Scroll the transcript. Then read the disposition reason aloud:

> *"The customer said, 'is it possible to pay on fifth august' and confirmed with
> 'yes' when the payment date was read back."*

> "That's the design working end to end. The customer spoke a date informally,
> stage 9 read it back, they confirmed, and the extraction cited the
> confirmation. Stage 9 exists precisely to manufacture the explicit statement
> the disposition has to rest on."

**If you have time, play 20 seconds** of `samples/recordings/call-PTP_FUTURE-…mp3`.

## Step 6 — The webhook arriving *(1 min)* · tabs 4, 6

Console → **Action Logs** shows the Post-Call Trigger firing. Terminal shows
`webhook.processed` with the same conversation id. Both ends of one event.

## Step 7 — The record in MongoDB *(1 min)* · tab 7

```powershell
docker compose exec mongo mongosh emi_voice_agent --quiet `
  --eval "db.calls.find({},{call_id:1,stage_code:1,ptp_date:1,_id:0}).limit(6)"

docker compose exec mongo mongosh emi_voice_agent --quiet `
  --eval "db.webhook_events.getIndexes()"
```

> "`event_id` is a unique index. That, not application logic, is what actually
> enforces webhook idempotency under concurrent delivery."

## Step 8 — The dashboard ⭐ *(2 min)* · tab 1

Eight summary cards, the stage-code chart, seven filters, thirteen columns.
Filter by `stage_code=DISPUTE_PAID`, then open the row.

**Two things to point at on the detail page:**

- The **raw `gnani_response` and `post_call_payload`** panels — proof the
  integration is real, not mocked at the boundary
- Then open `samples/console-analytics/` and show the console screenshot beside
  it:

> "Gnani's own analytics for this call and my stored record agree field for
> field — duration, sentiment, language, and the disposition reason verbatim.
> Nothing was lost or altered between their platform and my dashboard."

Also worth showing from that screenshot: asked *"who is this"* before confirming
identity, the agent named ICICI Bank and the account's last four **and withheld
the amount** — section 5.2 enforced on a live call.

## Step 9 — Failure handling *(2 min)* · tab 7

```powershell
# invalid request -> 422
curl.exe -s -o NUL -w "%{http_code}`n" -X POST http://localhost:8000/api/Initial_Message `
  -H "Content-Type: application/json" -d "{\"customer_id\":\"\"}"

# webhook with no key -> 401
curl.exe -s -o NUL -w "%{http_code}`n" -X POST http://localhost:8000/api/v1/webhooks/post-call `
  -H "Content-Type: application/json" -d "{\"conversation_id\":\"x\"}"
```

Then the accuracy harness:

```powershell
docker compose exec api python -m tests.test_disposition_accuracy
```

**Quote the second number, not the first:**

> "100% on 27 labelled cases — but the number that matters is that nine of them
> required the guardrail to override what the model reported, and all nine were
> handled correctly. A corpus where nothing gets corrected proves nothing, so
> there's a test asserting it contains at least eight correction cases."

## Step 10 — Production readiness *(2 min)* · tab 5

Open `docs/engineering_log.md`. Scroll the four section headings.

> "Twenty-six problems, each with how I diagnosed it. The call-trigger API isn't
> documented anywhere — I recovered it from the console's own network traffic.
> The real webhook payload shape came out of my own dead-letter queue, because
> the handler stores the raw body before validating."

**Then name your own limitation before they find it:**

> "`preCallVariables` are keyed to the bot, not the call. Two concurrent calls on
> one agent would clobber each other's variables, so I serialise them behind a
> lock — which caps throughput. Production needs per-call scoping from Gnani, or
> one agent per concurrent stream."

Close with: stateless API scales horizontally · a queue between webhook and
enrichment · Mongo replica set · secrets manager over `.env` · **`webhook_dlq`
depth as the alert that matters**, because a rising queue means correlation is
failing and that's invisible from the dashboard.

---

## Afterwards

Remove the demo row so the dashboard is real calls only:

```powershell
docker compose exec api python -m scripts.clear_demo_data --dry-run
docker compose exec api python -m scripts.clear_demo_data
```

---

## If something breaks

| Problem | Do this |
|---|---|
| Containers down | `docker compose --profile mock up -d`, wait 20s |
| `/ready` says `json` | `docker compose --profile mock up -d --force-recreate api` |
| Dashboard empty | `docker compose exec api python -m scripts.seed_scenarios` — 10 rows in ~30s |
| Row doesn't appear live | Refresh. The pill shows `reconnecting`; say the WebSocket is blocked and move on |
| Swagger call fails | Fall back to the five existing calls — the story is the same |
| Docker won't start at all | Talk through `docs/test_results.md` and the console analytics screenshots. Every claim has an artefact |

**The demo does not depend on anything outside this machine.** No tunnel, no
Gnani API, no credits.

---

## Questions to have answers ready for

| Question | Answer |
|---|---|
| Why is `ptp_date` a string, not a date? | Customers say "the thirtieth". Forcing the model to resolve it is exactly what failed in live testing. Raw phrase in, resolved in code against the real call date. README §6.2 |
| Why can the guardrail overrule the model? | An LLM asked for a stage code always returns one. §2 forbids assumptions, so the refusal has to be deterministic. §6.3 |
| Why does stage 9 exist? | The readback manufactures the explicit statement the disposition rests on. Without it, extraction is inferring. §6.4 |
| Why not exact-match the phone? | The trigger takes national + country code separately; the callback sends one undocumented `mobile` field. Suffix matching collapses all formats. §6.5 |
| Why two storage backends? | §7 permits JSON. It makes the loop runnable with no infrastructure and is a demo safety net. Mongo is the default. §6.10 |
| Why is the customer panel empty on those calls? | They were started from the console, not the API — the platform sends no customer data for calls it initiates. Tagged `origin: gnani_console`, and the page says so |
| Did you use the STT/TTS API keys? | Different surface (`api.vachana.ai`); neither authenticates against the Agents Console. §A4 |
| Why didn't outbound calling work? | Their API returns `200 "Call is being triggered"` and nothing is delivered. Most likely no outbound caller number, or plan limits — `totalCredit: 20.0`. Raised during the assignment. §D1 |
| Scenarios 7 and 8? | Implemented and passing through the mock, but never observed on a live call — credits ran out. Said plainly in `test_results.md` |
| What would you do differently? | Ask about telephony entitlement on day one. It was the one blocker no amount of code could route around |
