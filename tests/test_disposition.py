"""The disposition guardrail -- the highest-risk business logic in the system.

Assignment section 2: "The bot should not assign a disposition based on
assumptions. The final stage code must be based on explicit statements made by
the customer." An LLM asked for a stage code always returns one, so the refusal
has to be tested here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.enums import StageCode
from app.services.disposition import resolve_ptp_date, validate
from tests.conftest import REFERENCE_DATE

REF = REFERENCE_DATE  # Wednesday 2026-07-29


class TestResolvePtpDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("today", date(2026, 7, 29)),
            ("hoy", date(2026, 7, 29)),
            ("tomorrow", date(2026, 7, 30)),
            ("mañana", date(2026, 7, 30)),
            ("2026-08-15", date(2026, 8, 15)),
            ("15 August 2026", date(2026, 8, 15)),
        ],
    )
    def test_absolute_and_relative_forms(self, raw: str, expected: date) -> None:
        assert resolve_ptp_date(raw, reference=REF) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # English ordinals -- how customers actually speak.
            ("the thirtieth", date(2026, 7, 30)),
            ("thirtieth", date(2026, 7, 30)),
            ("the thirty-first", date(2026, 7, 31)),
            # Numeric ordinals.
            ("the 30th", date(2026, 7, 30)),
            ("30th", date(2026, 7, 30)),
            # Spanish uses cardinals for dates.
            ("el treinta", date(2026, 7, 30)),
            ("treinta", date(2026, 7, 30)),
        ],
    )
    def test_spoken_day_of_month(self, raw: str, expected: date) -> None:
        assert resolve_ptp_date(raw, reference=REF) == expected

    def test_day_already_passed_rolls_to_next_month(self) -> None:
        # The 1st has passed on the 29th, so "the first" means next month.
        assert resolve_ptp_date("the first", reference=REF) == date(2026, 8, 1)

    def test_longest_ordinal_wins(self) -> None:
        # "twenty-first" must not be matched as "first".
        assert resolve_ptp_date("the twenty-first", reference=REF) == date(2026, 8, 21)

    def test_weekday_never_resolves_to_today(self) -> None:
        # REF is a Wednesday; "wednesday" means the next one, not today.
        assert resolve_ptp_date("wednesday", reference=REF) == date(2026, 8, 5)

    def test_invalid_day_for_month_is_skipped(self) -> None:
        # No 31st in September, so from 15 Sep "the thirty-first" -> 31 Oct.
        assert resolve_ptp_date(
            "the thirty-first", reference=date(2026, 9, 15)
        ) == date(2026, 10, 31)

    @pytest.mark.parametrize("raw", ["", None, "soon", "next month", "as soon as I can"])
    def test_vague_expressions_do_not_resolve(self, raw: str | None) -> None:
        assert resolve_ptp_date(raw, reference=REF) is None


class TestGuardrail:
    def test_unknown_code_becomes_unclear(self) -> None:
        result = validate("TOTALLY_MADE_UP", call_date=REF)
        assert result.stage_code is StageCode.UNCLEAR
        assert result.was_downgraded

    def test_ptp_without_date_is_not_a_promise(self) -> None:
        """The core anti-fabrication rule."""
        result = validate("PTP_FUTURE", raw_ptp_date=None, call_date=REF)
        assert result.stage_code is StageCode.UNCLEAR
        assert result.ptp_date is None

    def test_vague_promise_is_refused(self) -> None:
        # "I'll try next month" must never become a promise to pay.
        result = validate(
            "PTP_FUTURE", raw_ptp_date="I will try next month", call_date=REF
        )
        assert result.stage_code is StageCode.UNCLEAR

    def test_past_date_is_refused(self) -> None:
        result = validate("PTP_FUTURE", raw_ptp_date="2026-07-01", call_date=REF)
        assert result.stage_code is StageCode.UNCLEAR
        assert "precedes" in result.adjustments[0]

    def test_absurdly_far_date_is_refused(self) -> None:
        far = (REF + timedelta(days=400)).isoformat()
        result = validate(
            "PTP_FUTURE", raw_ptp_date=far, call_date=REF, max_days_ahead=90
        )
        assert result.stage_code is StageCode.UNCLEAR

    @pytest.mark.parametrize(
        ("reported", "raw_date", "expected"),
        [
            # The model's label is corrected against the resolved date.
            ("PTP_FUTURE", "today", StageCode.PTP_TODAY),
            ("PTP_FUTURE", "the thirtieth", StageCode.PTP_TOMORROW),
            ("PTP_TODAY", "2026-08-15", StageCode.PTP_FUTURE),
            ("PTP_TOMORROW", "today", StageCode.PTP_TODAY),
            # Already correct -- left alone.
            ("PTP_TODAY", "today", StageCode.PTP_TODAY),
        ],
    )
    def test_code_is_reconciled_with_resolved_date(
        self, reported: str, raw_date: str, expected: StageCode
    ) -> None:
        result = validate(reported, raw_ptp_date=raw_date, call_date=REF)
        assert result.stage_code is expected

    def test_partial_is_about_amount_not_timing(self) -> None:
        """PTP_PARTIAL must survive a date that would otherwise reclassify it."""
        result = validate("PTP_PARTIAL", raw_ptp_date="today", call_date=REF)
        assert result.stage_code is StageCode.PTP_PARTIAL
        assert result.ptp_date == REF

    def test_non_ptp_code_drops_a_stray_date(self) -> None:
        result = validate("ALREADY_PAID", raw_ptp_date="the thirtieth", call_date=REF)
        assert result.stage_code is StageCode.ALREADY_PAID
        assert result.ptp_date is None
        assert result.adjustments  # the drop is recorded, not silent

    @pytest.mark.parametrize(
        "code",
        [
            "ALREADY_PAID", "CALLBACK_SCHEDULED", "RTP_FINANCIAL", "RTP_MEDICAL",
            "RTP_NO_REASON", "DISPUTE_PAID", "DISPUTE_CHARGES", "NO_LOAN",
            "WRONG_NUMBER", "THIRD_PARTY", "BUSY", "RNR", "VM", "DSCN", "UNCLEAR",
        ],
    )
    def test_non_ptp_codes_pass_through_untouched(self, code: str) -> None:
        result = validate(code, call_date=REF)
        assert result.stage_code == StageCode(code)
        assert not result.adjustments

    def test_case_and_whitespace_are_tolerated(self) -> None:
        assert validate("  ptp_today  ", raw_ptp_date="today", call_date=REF).stage_code \
            is StageCode.PTP_TODAY

    def test_every_adjustment_is_explained(self) -> None:
        """A downgrade must always carry a human-readable reason."""
        result = validate("PTP_FUTURE", raw_ptp_date=None, call_date=REF)
        assert result.adjustments
        assert all(len(a) > 20 for a in result.adjustments)
