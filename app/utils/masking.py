"""PII masking.

Assignment section 6.1 requires the customer's phone number to be masked on the
user interface, e.g. ``******3210``. Masking happens at the serialisation
boundary so the stored record keeps the real number (needed to place the call)
while nothing unmasked reaches a template or API response.
"""

from __future__ import annotations

MASK_CHAR = "*"
VISIBLE_DIGITS = 4


def mask_phone(phone: str | None, visible: int = VISIBLE_DIGITS) -> str:
    """Mask all but the trailing ``visible`` digits.

    >>> mask_phone("9876543210")
    '******3210'
    >>> mask_phone("+15551234567")
    '********4567'

    Non-digit characters (``+``, spaces, hyphens) are dropped so the mask
    length reflects the actual digits. Short or missing values mask entirely
    rather than leaking a partial number.
    """
    if not phone:
        return ""

    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    if len(digits) <= visible:
        return MASK_CHAR * len(digits)

    return MASK_CHAR * (len(digits) - visible) + digits[-visible:]


def mask_account(account: str | None, visible: int = VISIBLE_DIGITS) -> str:
    """Mask a loan account number, keeping the trailing characters.

    >>> mask_account("LAN123456")
    '*****3456'
    """
    if not account:
        return ""
    if len(account) <= visible:
        return MASK_CHAR * len(account)
    return MASK_CHAR * (len(account) - visible) + account[-visible:]


def last4(value: str | None) -> str:
    """Trailing four characters, used in the greeting ("account ending 3456").

    The greeting may reference the last four digits before identity is
    confirmed; the full number may not be disclosed (assignment section 5.2).
    """
    if not value:
        return ""
    return value[-VISIBLE_DIGITS:]
