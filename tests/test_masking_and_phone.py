"""PII masking (section 6.1) and phone correlation.

The phone tests encode a bug found in live testing: Gnani's Dynamic Messages
callback sends ``mobile`` in national format while we store E.164, so exact
matching silently failed to correlate the call.
"""

from __future__ import annotations

import pytest

from app.utils.masking import last4, mask_account, mask_phone
from app.utils.phone import digits_only, phone_suffix


class TestMaskPhone:
    def test_matches_the_assignment_example(self) -> None:
        # Section 6.1 states the required form explicitly.
        assert mask_phone("9876543210") == "******3210"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The mask length tracks the digit count, so a country code adds stars.
            ("+15551234567", "*******9318"),      # 11 digits
            ("878-834-9318", "******9318"),       # 10 digits, separators stripped
            ("(878) 834 9318", "******9318"),     # 10 digits
            ("+91 98947 77373", "********7373"),  # 12 digits
        ],
    )
    def test_separators_and_country_codes(self, raw: str, expected: str) -> None:
        assert mask_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["", None])
    def test_missing_value_is_empty(self, raw: str | None) -> None:
        assert mask_phone(raw) == ""

    @pytest.mark.parametrize("raw", ["123", "1234", "abc"])
    def test_short_or_nonnumeric_never_leaks_digits(self, raw: str) -> None:
        """A short number must mask entirely rather than expose a partial."""
        masked = mask_phone(raw)
        assert not any(c.isdigit() for c in masked)

    def test_only_last_four_digits_survive(self) -> None:
        masked = mask_phone("+919123456789")
        assert masked.endswith("7373")
        assert "989477" not in masked


class TestMaskAccount:
    def test_masks_leading_characters(self) -> None:
        assert mask_account("LAN123456") == "*****3456"

    def test_short_account_masks_entirely(self) -> None:
        assert mask_account("AB") == "**"

    def test_last4_for_greeting(self) -> None:
        # The greeting may cite the last four before identity is confirmed.
        assert last4("LAN123456") == "3456"


class TestPhoneCorrelation:
    def test_all_formats_of_one_number_collapse(self) -> None:
        """The bug this exists to prevent: same number, three formats."""
        forms = ["9123456789", "+919123456789", "0919123456789", "+91 98947 77373"]
        keys = {phone_suffix(f) for f in forms}
        assert len(keys) == 1, f"formats did not collapse: {keys}"

    def test_national_and_e164_match(self) -> None:
        # Gnani sends the first; we store the second.
        assert phone_suffix("9123456789") == phone_suffix("+919123456789")

    def test_different_numbers_do_not_collide(self) -> None:
        assert phone_suffix("+919123456789") != phone_suffix("+15551234567")

    def test_digits_only_strips_everything_else(self) -> None:
        assert digits_only("+1 (878) 834-9318") == "15551234567"

    @pytest.mark.parametrize("raw", ["", None])
    def test_empty_input(self, raw: str | None) -> None:
        assert phone_suffix(raw) == ""

    def test_short_number_returns_what_it_has(self) -> None:
        assert phone_suffix("12345") == "12345"
