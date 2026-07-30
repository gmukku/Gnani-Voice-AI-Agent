"""Phone-number normalisation for call correlation.

The Agents Console is inconsistent about phone format: the trigger request
takes ``phone`` (national) and ``countryCode`` as separate fields, while the
Dynamic Messages callback sends a single ``mobile`` value whose format is not
documented -- the docs' own example (``"01234567890"``) carries neither a
country code nor a ``+``.

Correlating on an exact string is therefore fragile. Instead every record
stores a ``phone_suffix`` (the trailing national digits), and lookups compare
that. This matches whether Gnani sends ``9123456789``, ``+919123456789`` or
``0919123456789``.
"""

from __future__ import annotations

#: Long enough to be unique across a customer base, short enough to survive
#: country-code and trunk-prefix differences.
SUFFIX_LENGTH = 9


def digits_only(value: str | None) -> str:
    if not value:
        return ""
    return "".join(c for c in value if c.isdigit())


def phone_suffix(value: str | None, length: int = SUFFIX_LENGTH) -> str:
    """Trailing ``length`` digits, used as the correlation key.

    >>> phone_suffix("9123456789")
    '123456789'
    >>> phone_suffix("+919123456789")
    '123456789'
    >>> phone_suffix("0919123456789")
    '123456789'

    All three forms of the same number collapse to one key.
    """
    digits = digits_only(value)
    return digits[-length:] if len(digits) >= length else digits
