"""Greeting generation, including the identity gate (section 5.2).

The identity-gate tests are the important ones: they assert that the opening
message discloses no financial detail. That requirement outranks the
assignment's own worked example, which states the amount before confirming
identity -- see README section 6.1.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.enums import Language
from app.services.greeting import (
    build_disclosure,
    build_greeting,
    build_user_context,
    currency_word,
    spoken_date,
)

CUSTOMER = {
    "customer_name": "Rahul Sharma",
    "loan_account_number": "LAN123456",
    "emi_amount": 12500,
    "emi_due_date": date(2026, 7, 30),
    "currency": "INR",
    "lender": "ICICI Bank",
}


class TestIdentityGate:
    @pytest.mark.parametrize("language", [Language.EN_US, Language.ES])
    def test_greeting_discloses_no_amount(self, language: Language) -> None:
        greeting = build_greeting(language=language, **CUSTOMER)
        assert "12,500" not in greeting
        assert "12500" not in greeting
        assert "rupees" not in greeting.lower()
        assert "rupias" not in greeting.lower()

    @pytest.mark.parametrize("language", [Language.EN_US, Language.ES])
    def test_greeting_discloses_no_due_date(self, language: Language) -> None:
        greeting = build_greeting(language=language, **CUSTOMER)
        assert "30 July" not in greeting
        assert "julio" not in greeting

    def test_greeting_never_exposes_the_full_account_number(self) -> None:
        greeting = build_greeting(**CUSTOMER)
        assert "LAN123456" not in greeting
        assert "3456" in greeting  # last four only

    def test_greeting_identifies_the_lender_and_asks_to_confirm(self) -> None:
        # Both were missing in the first live call.
        greeting = build_greeting(**CUSTOMER)
        assert "ICICI Bank" in greeting
        assert "Rahul Sharma" in greeting
        assert "?" in greeting

    def test_disclosure_states_the_amount_once_confirmed(self) -> None:
        line = build_disclosure(**CUSTOMER)
        assert "12,500" in line
        assert "rupees" in line


class TestTtsSafety:
    """Live testing showed Timbre reading ISO codes letter by letter."""

    @pytest.mark.parametrize(
        ("code", "language", "expected"),
        [
            ("INR", Language.EN_US, "rupees"),
            ("INR", Language.ES, "rupias"),
            ("USD", Language.EN_US, "dollars"),
            ("USD", Language.ES, "dólares"),
        ],
    )
    def test_currency_is_spoken_as_a_word(
        self, code: str, language: Language, expected: str
    ) -> None:
        assert currency_word(code, language) == expected

    def test_unknown_currency_falls_back_to_the_code(self) -> None:
        assert currency_word("XYZ", Language.EN_US) == "XYZ"

    def test_disclosure_contains_no_iso_code_or_abbreviation(self) -> None:
        line = build_disclosure(**CUSTOMER)
        assert "INR" not in line
        assert "EMI" not in line  # "monthly instalment" instead

    def test_dates_are_expanded_for_speech(self) -> None:
        # "2026-07-30" would be read as digits.
        assert spoken_date(date(2026, 7, 30), Language.EN_US) == "30 July 2026"
        assert spoken_date(date(2026, 7, 30), Language.ES) == "30 de julio de 2026"


class TestUserContext:
    def test_includes_todays_date(self) -> None:
        """Without this the agent guessed, and read 'the thirtieth' as today."""
        ctx = build_user_context(
            language=Language.EN_US, today=date(2026, 7, 29), **CUSTOMER
        )
        assert ctx["current_date"] == "2026-07-29"
        assert ctx["current_date_spoken"] == "29 July 2026"

    def test_declares_every_variable_the_prompt_uses(self) -> None:
        """A variable the console has not declared fails at runtime mid-call."""
        ctx = build_user_context(
            language=Language.EN_US, today=date(2026, 7, 29), **CUSTOMER
        )
        required = {
            "customer_name", "loan_last4", "emi_amount", "emi_due_date_spoken",
            "currency_word", "current_date_spoken", "lender_name",
            "preferred_language", "disclosure_line",
        }
        assert required <= set(ctx)

    def test_all_values_are_strings(self) -> None:
        # The console sends pre-call variables as strings.
        ctx = build_user_context(
            language=Language.EN_US, today=date(2026, 7, 29), **CUSTOMER
        )
        assert all(isinstance(v, str) for v in ctx.values())

    def test_override_replaces_the_generated_greeting(self) -> None:
        custom = "Hi, am I speaking to Rahul?"
        assert build_greeting(override=custom, **CUSTOMER) == custom

    def test_unsupported_language_falls_back_to_english(self) -> None:
        greeting = build_greeting(language=Language.MIXED, **CUSTOMER)
        assert "May I confirm" in greeting
