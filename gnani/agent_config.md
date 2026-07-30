# Agent configuration

Submission item 2 asks for "Gnani Agent s Console bot configuration **or**
export". This is the configuration: every setting needed to rebuild the agent
from scratch.

The console exposes no export button. A machine-readable equivalent can be
captured from the `POST /genbots/dashboard/write/update_bot/v3` request the
console sends when saving — its payload is the complete bot config. If captured,
it is committed alongside this file as `agent_export.json`.

**Agent name:** `EMI Payment Collection`
**Agent / bot id:** `156ed70ef8ce4502b64e7d27e2a2bdea`
**Environment:** `production`

---

## Agent details

| Setting | Value |
|---|---|
| Name | `EMI Payment Collection` |
| Region | America |
| Time zone | **Must match `TIMEZONE` in `.env`** — see note below |
| Knowledge base | *None, deliberately* |

**Time zone matters more than it looks.** "Today" and "tomorrow" are resolved
against it, and the server resolves the same words independently when validating
the promise-to-pay date. A mismatch puts every PTP date off by a day. Whatever is
set here must equal `TIMEZONE` in `.env`.

**No knowledge base by design.** A KB is for document retrieval; every fact this
agent needs — name, amount, due date, account last four — arrives as a pre-call
variable at call time. Attaching one adds latency and a chance of the agent
answering from the wrong source.

---

## Transcriber (ASR)

| Setting | Value | Notes |
|---|---|---|
| Provider | **Gnani** | Gnani's ASR is Prisma v2.5 per their documentation. The dropdown shows the provider, not the model name. |
| Mode | Responsive | Consistent low latency |
| Allow interruptions | on | See below |
| Initial message interruption | **off** | Protects the identity-confirmation opening |
| Background noise filtering | on, 80 | |
| Inverse text normalisation | **should be on** | Formats dates, currencies and numbers — exactly what this agent captures |
| Max speech duration | 30s | |
| Initial silence timeout | 10s | Feeds `RNR` detection |
| Speech segmentation silence | 0.8s | Consider 1.0s; customers pause while working out a date |
| DTMF | off | Not required |
| Custom vocabulary | *optional* | `EMI, instalment, loan account, autopay, due date` reduces mishearing |

**Interruption threshold.** At 1 word, any "um", cough or background noise cuts
the agent off — including mid-way through the identity disclosure. 2–3 is safer.

**Inverse text normalisation is the setting that matters most here.** With it
off, spoken dates and amounts arrive as raw text, which directly harms PTP
capture.

---

## LLM model

| Setting | Value |
|---|---|
| Provider | Gnani |
| Model | **Gnani Evon v2.0** |
| Temperature | 0.3 |
| Max tokens | 300 |
| Knowledge base | none |

**Why plain v2.0 and not `Fast` or `Ultra`.** The dropdown offers
`Evon v2.0` ("strong performance across diverse workloads"), `Evon v2.0 Fast`
("low latency") and `Evon v2.0 Ultra` ("lightning speed"). The system prompt is
demanding — a ten-stage machine, a slot ledger and nine hard prohibitions — so
instruction adherence was chosen over latency. Observed average latency is around
2.3s. `Fast` is the fallback if that proves unworkable, but only if the identity
gate and the no-re-asking behaviour still hold.

`Aion v3.2` is Indic-focused and not applicable.

Temperature 0.3 because this is a compliance script, not creative writing.

---

## Voice (TTS)

| Setting | Value |
|---|---|
| Service | Gnani |
| Model | **Gnani Timbre G v1.0** |
| Voice | Lucia |
| Rate of speech | Normal (1) |
| Caching | on |
| Ambient sound | off |

**On Timbre 2.5.** The assignment specifies it, but Gnani's TTS documentation
lists `timbre-v2.5` as supporting ten Indian languages only — no Spanish, no
`en-US` — so it cannot serve the English (US) + Spanish requirement in section
3.3. It is also not provisioned for this account: the model dropdown offers
exactly one option. Timbre G ("Global") carries the voice catalogue that does
support the required languages. See `docs/engineering_log.md` C2.

---

## Languages

**English (US)** and **Spanish**, English primary.

The agent's language list and the Language Switch Prompt must name exactly the
same languages. If they diverge, switching fails silently at runtime.

---

## Conversation Flow

### Greeting message

```
Hello, this is a call from {{ lender_name }} regarding your loan account ending in {{ loan_last4 }}. May I confirm whether I am speaking with {{ customer_name }}?
```

Names the lender and the account's last four digits, and **nothing else**.
Section 5.2 requires that sensitive information wait for identity confirmation,
so the amount and due date are spoken later via `disclosure_line`.

### Ending message

```
Thank you for your time. Have a good day.
```

Static — this field does not support dynamic variables.

### Pre-call variables

Ten, all String. Names must match `app/services/greeting.py::build_user_context`
exactly; an undeclared variable fails at runtime mid-call.

| Variable | Sample value |
|---|---|
| `customer_name` | `Rahul Sharma` |
| `loan_last4` | `3456` |
| `emi_amount` | `12,500` |
| `emi_due_date_spoken` | `30 July 2026` |
| `currency_word` | `rupees` |
| `current_date_spoken` | `29 July 2026` |
| `lender_name` | `ICICI Bank` |
| `preferred_language` | `en-US` |
| `disclosure_line` | `Thank you, Rahul Sharma. Your monthly instalment of 12,500 rupees was due on 30 July 2026.` |
| `currency` | `INR` |

`current_date_spoken` is not decoration. An LLM has no clock, and live testing
showed the agent reading "the thirtieth" as *today*. Every PTP stage code is an
offset from today, so the date must be supplied explicitly.

`emi_due_date_spoken` exists separately from any ISO form because TTS reads
`2026-07-30` as digits.

### Dynamic Messages

**Off** for console testing — it would replace the configured greeting with one
served by the API. Enable it only when calls are initiated through
`POST /api/Initial_Message`, pointing at:

```
POST {PUBLIC_BASE_URL}/api/v1/gnani/dynamic-message
```

### Call transfer

Off. Not required — there is no human agent to transfer to.

---

## System prompt

Pasted from [`system_prompt.md`](system_prompt.md). A ten-stage machine with a
slot ledger; the flow is documented in
[`conversation_flow.md`](conversation_flow.md).

**Ordered for prompt caching.** The console's own Prompt Efficiency analyser
flagged the first draft: dynamic variables at line 11 of 98 defeat the KV cache,
because nothing after them can be reused across calls. Restructured so every
instruction comes first and all variables sit in one trailing `CUSTOMER CONTEXT`
block:

| Metric | Before | After |
|---|---|---|
| First variable | line 11 of 98 | line 74 of 82 |
| Cacheable static prefix | ~10% | **89%** |
| Blank-line ratio | 30.6% | 10.8% |

---

## Analytics

### Post-call Data Extraction — **on**

Base Instructions and all seven field definitions are in
[`postcall_extraction_config.md`](postcall_extraction_config.md).

| Field | Type |
|---|---|
| `DISPOSITION` | **Enum**, 19 values |
| `ptp_date` | String |
| `partial_amount` | String |
| `disposition_reason` | String |
| `disposition_summary` | String |
| `language_captured` | Enum — English, Spanish, Mixed |
| `customer_sentiment` | Enum — Cooperative, Neutral, Frustrated, Hostile |

**The field must be named `DISPOSITION`.** The console reserves that name and
rejects the entire bot config otherwise, with
`"A DISPOSITION field is required with enum type and with valid options."` —
while the documentation's own example uses `STAGE_CODE`.

**`ptp_date` is String, not Number or Enum**, and would remain String even if a
Date type existed. Customers say "the thirtieth"; forcing the model to resolve
that is what failed in live testing. The raw phrase passes through and
`app/services/disposition.py` resolves it against the real call date.

**`partial_amount` is String, not Number**, because it must be able to return
empty — an empty Number risks coercion to `0`, which would read as a committed
payment of zero.

### Post-Call Trigger — **on**

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `https://<tunnel>/hook` |
| Header key | `X-Webhook-Key` |
| Header value | `WEBHOOK_API_KEY` from `.env` |

**Use `/hook`, not the full path.** The console rejects long URLs with only
"enter a valid URL". `/hook` is an alias for the same handler with identical
authentication.

---

## Other tabs

| Tab | State |
|---|---|
| FAQ Answers | Empty — not needed |
| Voicemail detection | Recommended on (3 attempts, 3s delay) for the `VM` stage code |

---

## Rebuilding this agent from scratch

1. Create an agent named `EMI Payment Collection`; set the time zone to match `.env`.
2. **Transcriber** → Gnani, Responsive. Turn on inverse text normalisation; raise the interruption threshold to 2–3.
3. **LLM Model** → Gnani Evon v2.0, temperature 0.3.
4. **Voice** → Gnani, Timbre G v1.0, a bilingual voice.
5. **Languages** → English (US) + Spanish.
6. **Conversation Flow** → paste the greeting and ending messages; turn on Pre-call Variables and add all ten.
7. **System Prompt** → paste `system_prompt.md`.
8. **Overview → Language Switch Prompt** → paste `language_switch_prompt.md`.
9. **Analytics** → turn on Post-call Data Extraction; paste the Base Instructions; add all seven fields with the types above.
10. **Analytics → Post-Call Trigger** → point at `https://<tunnel>/hook` with the `X-Webhook-Key` header.
11. Whitelist a test number (OTP verification required).
12. Save, then test in the Chat Window before spending voice credits.
