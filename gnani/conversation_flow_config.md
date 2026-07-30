# Conversation Flow tab — configuration

Everything for **Configuration → Conversation Flow** in the Gnani Agents Console.

---

## 1. Greeting Message  *(mandatory)*

Supports dynamic variables. Paste exactly:

```
Hello, this is a call from {{ lender_name }} regarding your loan account ending in {{ loan_last4 }}. May I confirm whether I am speaking with {{ customer_name }}?
```

**Why it is worded this way.** It names the lender, the customer, and the
account's last four digits only. It deliberately does **not** state the EMI
amount or the due date, because assignment section 5.2 requires that sensitive
information is withheld until identity is reasonably confirmed. The amount is
spoken later, at stage 2 of the system prompt, via `disclosure_line`.

`lender_name` is in the opening line because an earlier version omitted it, and
live testing showed the agent answering "who is this?" with "I am calling from
the lender" — unconvincing, and wrong for collections, where the caller must
identify itself. The recordings in `samples/recordings/` predate this change and
still carry the older opening.

> The assignment's own worked example discloses the amount *before* confirming identity. We follow the stated rule instead of the example — see README.

## 2. Ending Message  *(mandatory)*

Keep this **static**. Per `docs.gnani.ai/B04_Dynamic_Variables`, the Ending Message does **not** support dynamic variables — a `{{ ... }}` here will be read aloud literally or fail.

```
Thank you for your time. Have a good day.
```

## 3. Pre-call Variables  *(toggle ON)*

Add all eight, type **String**. Names must match `app/services/greeting.py:build_user_context()`
character-for-character — an undeclared variable fails at runtime mid-call.

| Variable | Example value | Used in |
|---|---|---|
| `customer_name` | `Rahul Sharma` | greeting, system prompt |
| `loan_last4` | `3456` | greeting, system prompt |
| `emi_amount` | `1,200` | system prompt |
| `emi_due_date` | `2026-07-25` | reference / extraction |
| `emi_due_date_spoken` | `25 July 2026` | system prompt (TTS-safe form) |
| `currency` | `USD` | system prompt |
| `preferred_language` | `en-US` | system prompt |
| `disclosure_line` | `Thank you, Rahul Sharma. Your EMI of USD 1,200 was due on 25 July 2026.` | system prompt stage 2 |

`emi_due_date_spoken` exists separately from `emi_due_date` because TTS reads
`2026-07-25` as digits, which sounds wrong on a call.

## 4. Dynamic Messages  *(leave OFF for now)*

Turn on later, once the FastAPI app is running behind a public tunnel. It will
point at:

```
POST {PUBLIC_BASE_URL}/api/v1/gnani/dynamic-message
```

Gnani sends `{conversation_id, mobile}`; we reply with
`additional_info.inya_data.{text, user_context}`. This is what binds Gnani's
`conversation_id` to our `call_id`, so the post-call webhook can be correlated.

## 5. Call transfer  *(leave OFF)*

Not required by the assignment. There is no human agent to transfer to.

---

## Order of operations

1. Toggle **Pre-call Variables** ON and add the eight above.
2. Paste **Greeting Message** and **Ending Message**.
3. Paste the system prompt into **System Prompt** (see `gnani/system_prompt.md`).
4. `Save` should now be enabled.
