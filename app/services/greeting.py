"""Initial-message generation (assignment section 5.2).

There is a genuine tension in the assignment here. Section 5.2 states:

    "Sensitive information should not be disclosed until the customer's
     identity has been reasonably confirmed."

...but the worked example immediately above it discloses the amount and due
date *before* asking "May I confirm whether I am speaking with Rahul Sharma?".

We follow the stated rule rather than the example, because it is also how real
collections calls must work (you cannot disclose a debt to whoever answers the
phone). The greeting is therefore two-stage:

* ``build_greeting``  -- pre-confirmation: name and purpose only, no amount,
  no due date, no full account number. Just enough for the customer to know
  why you are calling.
* ``build_disclosure`` -- post-confirmation: the full EMI detail, spoken by the
  agent only once ``identity_confirmed`` is true.

The README documents the deviation.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from jinja2 import Environment, StrictUndefined

from app.models.enums import Language
from app.utils.masking import last4

_env = Environment(undefined=StrictUndefined, autoescape=False)

#: Pre-confirmation. Names the customer and the purpose, discloses nothing.
_GREETING = {
    Language.EN_US: (
        "Hello, this is a call from {{ lender }} regarding your loan account "
        "ending in {{ loan_last4 }}. "
        "May I confirm whether I am speaking with {{ customer_name }}?"
    ),
    Language.ES: (
        "Hola, le llamamos de {{ lender }} en relación con su cuenta de "
        "préstamo terminada en {{ loan_last4 }}. "
        "¿Puedo confirmar si hablo con {{ customer_name }}?"
    ),
}

#: Post-confirmation. Safe to state amounts now.
#: Currency is spoken as a word after the amount ("1,200 dollars") rather than
#: as an ISO code before it ("USD 1,200") -- live testing showed Timbre reads
#: the code letter by letter. "EMI" is likewise avoided in favour of "monthly
#: instalment" for the same reason.
_DISCLOSURE = {
    Language.EN_US: (
        "Thank you, {{ customer_name }}. Your monthly instalment of "
        "{{ emi_amount }} {{ currency_word }} was due on "
        "{{ emi_due_date_spoken }}."
    ),
    Language.ES: (
        "Gracias, {{ customer_name }}. Su cuota mensual de "
        "{{ emi_amount }} {{ currency_word }} venció el "
        "{{ emi_due_date_spoken }}."
    ),
}

_MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

#: Spoken currency names. Passing the ISO code straight to TTS makes Timbre
#: spell it out letter by letter ("U S D"), which is what live testing showed.
_CURRENCY_WORDS = {
    "USD": {Language.EN_US: "dollars", Language.ES: "dólares"},
    "EUR": {Language.EN_US: "euros", Language.ES: "euros"},
    "INR": {Language.EN_US: "rupees", Language.ES: "rupias"},
    "GBP": {Language.EN_US: "pounds", Language.ES: "libras"},
}


def currency_word(code: str, language: Language) -> str:
    """Spoken name for a currency code, falling back to the code itself."""
    return _CURRENCY_WORDS.get(code.upper(), {}).get(language, code)


def spoken_date(value: date, language: Language) -> str:
    """Render a date the way it should be read aloud.

    TTS reads "2026-07-25" as digits, which sounds wrong on a voice call, so
    dates are expanded before they reach Timbre.
    """
    if language is Language.ES:
        return f"{value.day} de {_MONTHS_ES[value.month - 1]} de {value.year}"
    return value.strftime("%d %B %Y").lstrip("0")


def _context(
    *,
    customer_name: str,
    loan_account_number: str,
    emi_amount: float,
    emi_due_date: date,
    currency: str,
    language: Language,
    lender: str,
) -> dict[str, Any]:
    return {
        "customer_name": customer_name,
        "loan_last4": last4(loan_account_number),
        "emi_amount": f"{emi_amount:,.0f}",
        "emi_due_date_spoken": spoken_date(emi_due_date, language),
        "currency": currency,
        "currency_word": currency_word(currency, language),
        "lender": lender,
    }


def build_greeting(
    *,
    customer_name: str,
    loan_account_number: str,
    emi_amount: float,
    emi_due_date: date,
    currency: str = "USD",
    language: Language = Language.EN_US,
    lender: str = "your lender",
    override: str | None = None,
) -> str:
    """Pre-confirmation greeting. Discloses no amount or due date."""
    if override:
        return override.strip()

    language = language if language in _GREETING else Language.EN_US
    return _env.from_string(_GREETING[language]).render(
        **_context(
            customer_name=customer_name,
            loan_account_number=loan_account_number,
            emi_amount=emi_amount,
            emi_due_date=emi_due_date,
            currency=currency,
            language=language,
            lender=lender,
        )
    )


def build_disclosure(
    *,
    customer_name: str,
    loan_account_number: str,
    emi_amount: float,
    emi_due_date: date,
    currency: str = "USD",
    language: Language = Language.EN_US,
    lender: str = "your lender",
) -> str:
    """Post-confirmation EMI detail. Only spoken after identity is confirmed."""
    language = language if language in _DISCLOSURE else Language.EN_US
    return _env.from_string(_DISCLOSURE[language]).render(
        **_context(
            customer_name=customer_name,
            loan_account_number=loan_account_number,
            emi_amount=emi_amount,
            emi_due_date=emi_due_date,
            currency=currency,
            language=language,
            lender=lender,
        )
    )


def build_user_context(
    *,
    customer_name: str,
    loan_account_number: str,
    emi_amount: float,
    emi_due_date: date,
    currency: str,
    language: Language,
    lender: str = "your lender",
    today: date | None = None,
) -> dict[str, str]:
    """Variables handed to Gnani for use in the system prompt.

    These become the Jinja context inside the agent's prompt, so every name
    here must also be declared as a Pre-Call Variable in the console -- an
    undeclared variable fails at runtime mid-call.

    ``current_date`` / ``current_date_spoken`` are not decoration. An LLM has
    no clock, and live testing showed the agent interpreting a bare "the
    thirtieth" as *today*. Since every PTP stage code is an offset from today
    (``PTP_TODAY`` vs ``PTP_TOMORROW`` vs ``PTP_FUTURE``), the agent must be
    told the date explicitly or the disposition is guesswork.
    """
    today = today or date.today()
    return {
        "customer_name": customer_name,
        "loan_last4": last4(loan_account_number),
        "emi_amount": f"{emi_amount:,.0f}",
        "emi_due_date": emi_due_date.isoformat(),
        "emi_due_date_spoken": spoken_date(emi_due_date, language),
        "currency": currency,
        "currency_word": currency_word(currency, language),
        "current_date": today.isoformat(),
        "current_date_spoken": spoken_date(today, language),
        "lender_name": lender,
        "preferred_language": str(language),
        "disclosure_line": build_disclosure(
            customer_name=customer_name,
            loan_account_number=loan_account_number,
            emi_amount=emi_amount,
            emi_due_date=emi_due_date,
            currency=currency,
            language=language,
            lender=lender,
        ),
    }
