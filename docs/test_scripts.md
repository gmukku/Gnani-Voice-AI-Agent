# Voice agent test scripts

One script per scenario. Each lists what **you** say as the customer, the stage
code the system should record, and what to check afterwards.

**Sample values assumed:** Rahul Sharma · account ending 3456 · 12,500 rupees ·
due 30 July 2026 · ICICI Bank.

---

## Read this first — do it cheaply

You have roughly 15 credits left, and a voice call costs about 2.5. That is
only five or six voice calls, which is fewer than the scenarios below.

**So start with one Chat Window conversation** (`Test → Chat Window`) and check
whether a row appears on the dashboard afterwards. The Post-Call Trigger fires
after any call, so text conversations should produce a real disposition too — at
a fraction of the cost.

- **If a row appears:** run every script below in the Chat Window. You get real
  Gnani dispositions across all stage codes for almost nothing, then spend your
  voice credits only on the 3–4 calls you want as audio evidence.
- **If no row appears:** the trigger is voice-only. Use the priority order in the
  last section and accept partial coverage.

Either way, watch it land with `docker compose logs api -f`.

---

## The scripts

Each turn is one thing you say. Wait for the agent to reply between turns.

### 1 — Promise to pay today → `PTP_TODAY`

```
yes this is Rahul Sharma
I can pay it today
yes that's correct
```
**Check:** `ptp_date` equals today's date.

### 2 — Promise to pay on a future date → `PTP_FUTURE`

```
yes speaking
I can pay on the fifteenth of August
yes that's right
```
**Check:** `ptp_date` = `2026-08-15`. The agent should read the date back with
the weekday before you confirm.

### 3 — Spoken ordinal, no month → `PTP_TOMORROW` or `PTP_FUTURE`

```
yes this is Rahul
I'll pay on the thirty-first
yes
```
**Check:** this is the bug you found live. The agent must **not** say "today, the
thirty-first". The stored code depends on the real date — the guardrail decides,
not the model.

### 4 — Partial payment → `PTP_PARTIAL`

```
yes speaking
I can only manage five thousand right now
I'll pay the five thousand tomorrow
yes
```
**Check:** `partial_amount` = `5000`. The code must stay `PTP_PARTIAL` even
though the date is tomorrow.

### 5 — Already paid → `ALREADY_PAID`

```
yes this is Rahul Sharma
I already paid that last week
no, I'm sure it went through
```
**Check:** the agent must **not** confirm the payment as received — only note it
for verification.

### 6 — Disputes the balance → `DISPUTE_PAID`

```
yes speaking
I already paid this, why do you keep saying I owe money
no I definitely paid it, your records are wrong
```
**Check:** `DISPUTE_PAID`, not `ALREADY_PAID`. The difference is contesting a
balance the lender still claims.

### 7 — Disputes the amount → `DISPUTE_CHARGES`

```
yes this is Rahul
the amount is wrong, I owe less than that
there are extra charges I never agreed to
```
**Check:** the agent acknowledges and promises review. It must **not** argue or
attempt to justify the charges.

### 8 — Callback at a specific time → `CALLBACK_SCHEDULED`

```
yes speaking
can you call me back on Thursday at four in the afternoon
yes that works
```
**Check:** `CALLBACK_SCHEDULED`, and the specific time appears in the reason.

### 9 — Busy, no specific time → `BUSY`

```
yes this is Rahul
I'm busy right now, call me later
no, just call later
```
**Check:** `BUSY`, **not** `CALLBACK_SCHEDULED`. "Later" is not a time.

### 10 — Cannot pay, financial → `RTP_FINANCIAL`

```
yes speaking
I lost my job last month, I can't pay right now
no, I really don't know when
```
**Check:** `RTP_FINANCIAL` with no `ptp_date`.

### 11 — Cannot pay, medical → `RTP_MEDICAL`

```
yes this is Rahul
I've been in hospital, I can't manage it right now
```
**Check:** `RTP_MEDICAL`, not `RTP_FINANCIAL`.

### 12 — Refuses, no reason → `RTP_NO_REASON`

```
yes speaking
I'm not paying
I don't want to discuss it
```
**Check:** the agent must not threaten or press beyond two attempts.

### 13 — Denies the loan → `NO_LOAN`

```
yes this is Rahul Sharma
I never took any loan from you
no, I've never had an account with you
```

### 14 — Third party answers → `THIRD_PARTY`

```
no, this is his brother
he's not home right now
he'll be back this evening
```
**Check — this is a compliance moment.** The agent must **never** state the
amount, the due date, or that the call concerns a debt. It should ask when Rahul
is reachable and close.

### 15 — Wrong number → `WRONG_NUMBER`

```
there's no Rahul here
no, you have the wrong number
```
**Check:** `WRONG_NUMBER`, not `THIRD_PARTY`. Third party means the answerer
knows the borrower.

### 16 — Language switch → `PTP_FUTURE`, language `Mixed`

```
yes speaking
can you speak Spanish
puedo pagar el treinta
sí
```
**Check — the important one.** After switching, the agent must **not** re-ask
anything you already answered, and must not restart from identity confirmation.
`language_captured` should be `Mixed`.

### 17 — Vague promise → `UNCLEAR` *(guardrail test)*

```
yes this is Rahul
I'll try to pay next month
no, I can't give you a date
```
**Check — the most important row on your dashboard.** The extraction may report
`PTP_FUTURE`; the stored code must be **`UNCLEAR`**, with a
`disposition_adjustments` note on the detail page. A promise with no date is not
a promise.

### 18 — No clear outcome → `UNCLEAR` or `DSCN`

```
yes speaking
hold on a second
```
Then stop responding, and end the call.

---

## Behaviour to probe on any call

Slip these into a conversation you are running anyway:

| Say this | The agent must |
|---|---|
| *"who is this?"* before confirming identity | Name ICICI Bank, and **not** state the amount |
| *"can you give me a discount?"* | Refuse — no waiver, no settlement, no extension |
| *"how much do I owe?"* before confirming identity | Withhold the amount |
| *"take me off your list"* | Apologise and close immediately |
| Give a date, then chat for two more turns | **Never re-ask** the date |

Also listen for: **"twelve thousand five hundred rupees"** — never "I N R" — and
**"monthly instalment"**, never "E M I".

---

## If you only have credits for five voice calls

Prioritised by what each proves:

1. **#17 vague promise** — the guardrail refusing a fabricated promise. Nothing
   else demonstrates section 2 as directly.
2. **#16 language switch** — covers section 3.3, and the memory-across-switch
   behaviour is the hardest thing to get right.
3. **#14 third party** — the identity gate, which is both a grading item and a
   real compliance rule.
4. **#2 or #3 promise to pay** — the happy path, and it populates a PTP card.
5. **#10 financial hardship** — a refusal branch, for stage-code spread.

That covers five of the eight summary cards and every high-value behaviour. Run
the rest in the Chat Window.

---

## After each call

1. Wait ~90 seconds — analytics generation is not instant.
2. Refresh the dashboard; a row should appear on its own.
3. Open **View Details** and confirm the stage code, the reason quoting your
   actual words, and any amber adjustment notice.
4. Screenshot anything that surprises you — that is submission item #17.
