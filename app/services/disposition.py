"""Stage-code validation and promise-to-pay date resolution.

This is the server-side half of the disposition guardrail. The Gnani-side
disposition prompt classifies the transcript; this module refuses to trust it
blindly.

Assignment section 2 is explicit: "The bot should not assign a disposition
based on assumptions. The final stage code must be based on explicit statements
made by the customer during the conversation." An LLM asked for a stage code
will always return one, so unsupported classifications are downgraded to
``UNCLEAR`` here rather than stored as fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from app.models.enums import PTP_CODES_REQUIRING_DATE, StageCode

#: Weekday names -> ``date.weekday()`` index, for "next Friday" style phrases.
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    # Spanish -- the agent supports es and the LLM may echo the customer.
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

_TODAY_WORDS = {"today", "hoy"}
_TOMORROW_WORDS = {"tomorrow", "manana", "mañana"}

#: Spoken day-of-month, English ordinals and Spanish cardinals. Live testing
#: showed this is how customers overwhelmingly express a payment date --
#: "I'll pay on the thirtieth" -- and dateutil cannot parse it.
_ORDINAL_WORDS: dict[str, int] = {
    # English
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "twenty-first": 21, "twenty first": 21,
    "twenty-second": 22, "twenty second": 22, "twenty-third": 23,
    "twenty third": 23, "twenty-fourth": 24, "twenty fourth": 24,
    "twenty-fifth": 25, "twenty fifth": 25, "twenty-sixth": 26,
    "twenty sixth": 26, "twenty-seventh": 27, "twenty seventh": 27,
    "twenty-eighth": 28, "twenty eighth": 28, "twenty-ninth": 29,
    "twenty ninth": 29, "thirtieth": 30, "thirty-first": 31,
    "thirty first": 31,
    # Spanish uses cardinals for dates ("el treinta"), except "primero".
    "primero": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintidós": 22,
    "veintitres": 23, "veintitrés": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintiséis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "treinta y uno": 31,
}

#: "30th", "1st", "22nd" -- digits with an ordinal suffix.
_NUMERIC_ORDINAL = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)\b")


def _resolve_day_of_month(day: int, reference: date) -> date | None:
    """Turn a bare day number into a concrete date at or after ``reference``.

    A customer saying "the thirtieth" means the *next* thirtieth: this month if
    it has not passed, otherwise next month. Returns ``None`` for a day that
    does not exist in either candidate month (e.g. the 31st of a 30-day month
    followed by February).
    """
    if not 1 <= day <= 31:
        return None

    for months_ahead in (0, 1):
        year = reference.year + (reference.month - 1 + months_ahead) // 12
        month = (reference.month - 1 + months_ahead) % 12 + 1
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= reference:
            return candidate
    return None


def _parse_spoken_day(text: str, reference: date) -> date | None:
    """Extract a day-of-month from a spoken phrase, if one is present."""
    numeric = _NUMERIC_ORDINAL.search(text)
    if numeric:
        return _resolve_day_of_month(int(numeric.group(1)), reference)

    # Longest key first so "twenty-first" wins over "first".
    for word in sorted(_ORDINAL_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", text):
            return _resolve_day_of_month(_ORDINAL_WORDS[word], reference)
    return None


@dataclass
class DispositionResult:
    """Outcome of validating a raw disposition from the analytics prompt."""

    stage_code: StageCode
    ptp_date: date | None = None
    reason: str = ""
    #: Human-readable notes on anything the guardrail changed. Surfaced on the
    #: dashboard so a downgrade is visible rather than silent.
    adjustments: list[str] = field(default_factory=list)

    @property
    def was_downgraded(self) -> bool:
        return bool(self.adjustments)


def resolve_ptp_date(
    raw: str | None,
    *,
    reference: date,
    tz: str = "UTC",
) -> date | None:
    """Resolve a spoken date expression to a concrete date.

    Handles ISO dates, natural phrases ("today", "tomorrow", "next Friday")
    and free text the LLM passed through verbatim. Returns ``None`` when
    nothing parseable is present -- the caller decides what that means.

    ``reference`` is the call date, so "tomorrow" resolves relative to when the
    conversation happened rather than when the webhook was processed.
    """
    if not raw:
        return None

    text = raw.strip().lower()
    if not text:
        return None

    if text in _TODAY_WORDS:
        return reference
    if text in _TOMORROW_WORDS:
        return reference + timedelta(days=1)

    # "next friday" / "on friday" / "viernes"
    for name, index in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text):
            days_ahead = (index - reference.weekday()) % 7
            # "next <weekday>" always means the coming one, never today.
            days_ahead = days_ahead or 7
            return reference + timedelta(days=days_ahead)

    # ISO and other fully-qualified dates first -- a customer who named a month
    # should not be reinterpreted as a bare day-of-month.
    try:
        parsed = date_parser.parse(
            raw,
            default=datetime.combine(reference, datetime.min.time()),
            fuzzy=True,
        )
    except (ValueError, OverflowError, TypeError):
        # Not a parseable date; fall back to spoken day-of-month
        # ("the thirtieth", "el treinta", "30th").
        return _parse_spoken_day(text, reference)

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(tz))
    return parsed.date()


def validate(
    raw_stage_code: str | None,
    *,
    raw_ptp_date: str | None = None,
    reason: str = "",
    call_date: date | None = None,
    tz: str = "UTC",
    max_days_ahead: int = 90,
) -> DispositionResult:
    """Validate a raw disposition, downgrading anything unsupported.

    Rules, in order:

    1. An unrecognised or missing stage code becomes ``UNCLEAR``.
    2. A ``PTP_*`` code must carry a resolvable date, else ``UNCLEAR`` -- a
       promise to pay with no date is not a promise.
    3. ``PTP_TODAY`` / ``PTP_TOMORROW`` must agree with the resolved date; a
       mismatch is reclassified to ``PTP_FUTURE`` rather than discarded.
    4. A date in the past, or absurdly far ahead, is not a commitment.
    """
    reference = call_date or datetime.now(ZoneInfo(tz)).date()
    adjustments: list[str] = []

    # (1) Unknown code -> UNCLEAR.
    try:
        stage_code = StageCode(str(raw_stage_code).strip().upper())
    except (ValueError, AttributeError):
        return DispositionResult(
            stage_code=StageCode.UNCLEAR,
            reason=reason,
            adjustments=[
                f"Unrecognised stage code {raw_stage_code!r}; downgraded to UNCLEAR."
            ],
        )

    ptp_date = resolve_ptp_date(raw_ptp_date, reference=reference, tz=tz)

    if stage_code not in PTP_CODES_REQUIRING_DATE:
        # A stray date on a non-PTP code is dropped, not stored.
        if ptp_date is not None:
            adjustments.append(
                f"Ignored ptp_date on non-PTP code {stage_code}."
            )
            ptp_date = None
        return DispositionResult(
            stage_code=stage_code,
            ptp_date=None,
            reason=reason,
            adjustments=adjustments,
        )

    # (2) PTP without a date is not a promise.
    if ptp_date is None:
        return DispositionResult(
            stage_code=StageCode.UNCLEAR,
            reason=reason,
            adjustments=[
                f"{stage_code} carried no resolvable ptp_date "
                f"({raw_ptp_date!r}); downgraded to UNCLEAR."
            ],
        )

    # (4) Sanity-check the date before trusting the specific PTP variant.
    if ptp_date < reference:
        return DispositionResult(
            stage_code=StageCode.UNCLEAR,
            reason=reason,
            adjustments=[
                f"ptp_date {ptp_date.isoformat()} precedes the call date "
                f"{reference.isoformat()}; downgraded to UNCLEAR."
            ],
        )
    if (ptp_date - reference).days > max_days_ahead:
        return DispositionResult(
            stage_code=StageCode.UNCLEAR,
            reason=reason,
            adjustments=[
                f"ptp_date {ptp_date.isoformat()} is more than "
                f"{max_days_ahead} days out; downgraded to UNCLEAR."
            ],
        )

    # (3) Reconcile the code against the resolved date.
    offset = (ptp_date - reference).days
    expected = {0: StageCode.PTP_TODAY, 1: StageCode.PTP_TOMORROW}.get(
        offset, StageCode.PTP_FUTURE
    )
    # PTP_PARTIAL is about amount, not timing, so it is left alone.
    if stage_code is not StageCode.PTP_PARTIAL and stage_code is not expected:
        adjustments.append(
            f"{stage_code} disagreed with resolved date "
            f"{ptp_date.isoformat()}; reclassified as {expected}."
        )
        stage_code = expected

    return DispositionResult(
        stage_code=stage_code,
        ptp_date=ptp_date,
        reason=reason,
        adjustments=adjustments,
    )
