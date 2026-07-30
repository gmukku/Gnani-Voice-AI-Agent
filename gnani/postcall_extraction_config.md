# Post-call Data Extraction — console configuration

> **Supersedes `disposition_prompt.md`** for console entry. That file was written
> as a single 9-section prompt (the format in `docs.gnani.ai/C01_Disposition`);
> this console build instead uses **Base Instructions + per-field Extraction
> Instructions**. Keep the old file in the repo as the documented stage-code
> logic (submission item #16); enter *this* file into the console.
>
> Location: **Configuration → Analytics → Post-call Data Extraction** (toggle ON).

---

## Base Instructions

Paste into the **Base Instructions** box. This context applies to every field.

```
You are a Call Center Operations Analyst specialising in retail loan EMI collections. You review a completed call transcript between an outbound collections voice agent and a customer, and extract a standardised outcome.

BUSINESS CONTEXT
The agent called the customer about an overdue or upcoming EMI (loan instalment). The goal of the call was to determine the customer's payment intent and capture a payment date if one was offered. Supported languages are English (US) and Spanish; a mixed-language call is normal.

PARTICIPANTS - who is who in the transcript
There are exactly two speakers. The AGENT is the automated collections bot, and its turns are labelled with the agent's name (for example "EMI Payment Collection"). The CUSTOMER is the human who answered, and their turns may be labelled "You", "Customer", "User", or similar.
The agent always speaks first. If a speaker label is ambiguous, use this rule: the party asking questions about payment is the AGENT; the party answering about their own payment is the CUSTOMER.
This distinction is critical. Every rule below that refers to "the customer" means the human, never the bot.

ABSOLUTE ANALYSIS RULES
1. Base every answer only on what was actually said in the transcript. Never infer, assume, or fill gaps with what a customer probably meant.
2. Rely only on the CUSTOMER's words. Never draw a conclusion from what the agent said, asked, or assumed.
3. A commitment requires BOTH intent and specificity. "I'll try", "maybe next month", "as soon as I can" are NOT commitments.
4. If the agent read the outcome back and the customer explicitly agreed, weight that confirmation heavily — it is the most reliable signal in the transcript.
5. When two answers seem equally plausible, choose the more conservative one. An honest "unclear" is correct; a confident guess is a defect.
6. Read the entire transcript before answering. Outcomes frequently change in the final turns.
7. Output only the value requested for each field. No explanations, no JSON wrappers, no code fences.
```

## Data Fields

Add each field below with **Add Data Field**.

> **Available types (confirmed): `Boolean`, `String`, `Number`, `Enum`. There is no
> Date type.**
>
> Use **Enum** wherever the answer is from a closed set — `stage_code`,
> `language_captured`, `customer_sentiment`. Enum constrains the model at the platform
> level, so an invalid value becomes structurally impossible rather than merely
> discouraged. This is the single cheapest accuracy win available.
>
> Use **String** for `ptp_date` and `partial_amount` even though Number looks tempting
> for the latter — both must be able to come back **empty**, and an empty Number is
> likely to be coerced to `0`, which would read as a real commitment of zero. The
> server-side guardrail in `app/services/disposition.py` re-validates everything
> regardless.

---

### Field 1 — `DISPOSITION`

> **The name is not ours to choose.** The Agents Console reserves `DISPOSITION`
> for the disposition enum. Naming this field `stage_code` makes *the whole bot
> config* fail to save with:
> `{"status":"failure","message":"A DISPOSITION field is required with enum type and with valid options."}`
> Note the docs' own example (`C01_Disposition`) uses `STAGE_CODE` — doc drift.
> The webhook model accepts `DISPOSITION`, `disposition`, `stage_code` and
> `STAGE_CODE` via alias, so the backend does not care which one arrives.

**Field Name:** `DISPOSITION` — uppercase, exactly
**Type:** `Enum`
**Description:** `Final call disposition code`

**Enum values** — paste all 19:

```
PTP_TODAY, PTP_TOMORROW, PTP_FUTURE, PTP_PARTIAL, ALREADY_PAID, CALLBACK_SCHEDULED, RTP_FINANCIAL, RTP_MEDICAL, RTP_NO_REASON, DISPUTE_PAID, DISPUTE_CHARGES, NO_LOAN, WRONG_NUMBER, THIRD_PARTY, BUSY, RNR, VM, DSCN, UNCLEAR
```

**Extraction Instruction:**

```
Determine the single stage code that best describes how this call ended.

Evaluate the codes IN THE ORDER LISTED. The first code whose criteria are fully met by an explicit customer statement wins. Output the code only, in uppercase, exactly as written.

WRONG_NUMBER — the person states the borrower is unknown to them, or that this is not their number.
THIRD_PARTY — a different person answered who knows the borrower (family, colleague, household member).
VM — the transcript is a voicemail greeting or automated message with no live human turn.
RNR — the call connected but the customer never gave a substantive verbal response.
NO_LOAN — the confirmed borrower explicitly states they never took this loan.
DISPUTE_PAID — customer claims payment is complete AND contests that a balance is still owed.
ALREADY_PAID — customer states the payment is complete, without contesting a balance.
DISPUTE_CHARGES — customer contests the instalment amount, penalties, interest, or fees.
PTP_PARTIAL — customer commits to paying a specific amount LESS than the full instalment.
PTP_TODAY — customer explicitly commits to paying today.
PTP_TOMORROW — customer explicitly commits to paying tomorrow.
PTP_FUTURE — customer commits AND names a specific future date.
CALLBACK_SCHEDULED — customer requests a callback at a specific stated day or time.
BUSY — customer says they are busy, with no specific callback time given.
RTP_MEDICAL — customer cites illness, hospitalisation, or medical expense as the reason they cannot pay.
RTP_FINANCIAL — customer cites job loss, reduced income, or lack of funds.
RTP_NO_REASON — customer refuses to pay and gives no reason.
DSCN — the call ended abruptly mid-conversation before any outcome was reached.
UNCLEAR — nothing above is satisfied by an explicit customer statement.

TIE-BREAKERS — these confusions are the most common source of error:
- ALREADY_PAID vs DISPUTE_PAID: dispute requires the customer to contest a balance the lender still claims. A plain "I paid it" is ALREADY_PAID.
- WRONG_NUMBER vs THIRD_PARTY: third party means the answerer KNOWS the borrower. Wrong number means they do not.
- RTP_FINANCIAL vs PTP_FUTURE: "I'll try next month" is not a commitment. With a stated reason it is RTP_FINANCIAL; without one it is UNCLEAR.
- PTP_PARTIAL vs PTP_FUTURE: partial requires an explicit amount lower than the full instalment.
- BUSY vs CALLBACK_SCHEDULED: a callback requires a SPECIFIC time. "Call me later" is BUSY.
- DSCN vs RNR: RNR means they answered but stayed silent. DSCN means the call dropped mid-conversation.

Never output a PTP_ code unless the customer named a day, or said "today" or "tomorrow".
```

---

### Field 2 — `ptp_date`

**Type:** `String` — there is no Date type, and that is fine here; see note below
**Description:** `Promised payment date`

**Extraction Instruction:**

```
Extract the date the customer committed to paying.

If the customer named a specific calendar date, output it as YYYY-MM-DD.
If the customer used a relative expression, output that expression verbatim and lowercase: "today", "tomorrow", "next friday", "the thirtieth".
If the customer did not commit to any date, output an empty string.

Do not invent or estimate a date. Do not output a date for a customer who only said they would "try" to pay.
```

> **Why String is the right answer anyway.** Even if a Date type existed we would not
> use it. Relative phrases are how people actually speak, and forcing the model to
> resolve them is exactly what failed in live testing, where the agent read "the
> thirtieth" as today. Passing the raw phrase through lets `resolve_ptp_date()` in
> `app/services/disposition.py` resolve it against the real call date in
> `America/New_York` — deterministic code instead of a model guess.

---

### Field 3 — `partial_amount`

**Type:** `String` — **not** Number; it must be able to return empty, and an empty
Number risks being coerced to `0`, which would read as a committed payment of zero
**Description:** `Committed partial payment amount`

**Extraction Instruction:**

```
If the customer committed to paying an amount LESS than the full instalment, output that amount as digits only, with no currency symbol or separators (for example: 500).
If they committed to the full amount, or to no amount, output an empty string.
```

---

### Field 4 — `disposition_reason`

**Type:** String
**Description:** `One-sentence justification for the stage code`

**Extraction Instruction:**

```
Write ONE sentence explaining why this stage code was assigned, quoting or closely paraphrasing what the customer actually said.

Good: Customer said "I can pay on the thirtieth" and confirmed it when read back.
Bad: The customer seemed willing to pay soon.

Cite the customer, never the agent. If the outcome is UNCLEAR, state what was missing.
```

---

### Field 5 — `disposition_summary`

**Type:** String
**Description:** `Two to three sentence call summary`

**Extraction Instruction:**

```
Summarise the call in two or three sentences: whether identity was confirmed, what the customer said about payment, any reason or objection they raised, and how the call ended. Neutral factual tone. Do not repeat the disposition reason verbatim.
```

---

### Field 6 — `language_captured`

**Type:** `Enum` — values: `English`, `Spanish`, `Mixed`
**Description:** `Language(s) used by the customer`

**Extraction Instruction:**

```
Output exactly one of: English, Spanish, Mixed.
Use "Mixed" only if the customer genuinely used both languages in substantive turns. A single foreign word or greeting does not make a call Mixed.
```

---

### Field 7 — `customer_sentiment`

**Type:** `Enum` — values: `Cooperative`, `Neutral`, `Frustrated`, `Hostile`
**Description:** `Customer's overall tone`

**Extraction Instruction:**

```
Output exactly one of: Cooperative, Neutral, Frustrated, Hostile.
Judge from the customer's words only. Willingness to pay is not the same as tone — a customer who cannot pay but is polite is Cooperative.
```

---

## Validate with Test Extraction

Use the **Test Extraction** button on `stage_code` first — it is the field the grade
turns on. Run these against real or pasted transcripts and confirm each one:

| Transcript gist | Expected |
|---|---|
| "I'll pay on the thirtieth" + confirmed on readback | `PTP_FUTURE`, `ptp_date` = `the thirtieth` or ISO |
| "I'll pay it today" | `PTP_TODAY` |
| "I already paid that last week" | `ALREADY_PAID` |
| "I already paid, why do you keep saying I owe" | `DISPUTE_PAID` |
| "Call me back Thursday at 4" | `CALLBACK_SCHEDULED` |
| "I'm busy, call later" | `BUSY` |
| "Lost my job, I can't pay" | `RTP_FINANCIAL` |
| "I'll try next month" | `RTP_FINANCIAL` or `UNCLEAR` — **never** `PTP_FUTURE` |
| "The amount is wrong, I owe less" | `DISPUTE_CHARGES` |
| "This isn't Rahul, wrong number" | `WRONG_NUMBER` |
| "He's my brother, he's not home" | `THIRD_PARTY` |
| Agent talks, customer silent | `RNR` |

The last two rows of the "I'll try next month" case are the ones to watch — that
single distinction is the difference between an accurate disposition and a
fabricated promise-to-pay.

## Post-Call Trigger

Leave **OFF** until the FastAPI app is live behind a public tunnel. It will point at:

```
POST {PUBLIC_BASE_URL}/api/v1/webhooks/post-call
Header: X-Webhook-Key: <WEBHOOK_API_KEY from .env>
```

Map all seven fields above, plus `conversation_id`, transcript, call status,
duration and timestamps.
