"""Stage codes, call statuses and their groupings.

The stage-code enum is the single source of truth: the disposition guardrail
rejects anything outside it, and the dashboard summary cards are defined as
groupings over it (see ``app/services/metrics.py``).
"""

from __future__ import annotations

from enum import StrEnum


class StageCode(StrEnum):
    """The 19 dispositions from assignment section 3.4."""

    PTP_TODAY = "PTP_TODAY"
    PTP_TOMORROW = "PTP_TOMORROW"
    PTP_FUTURE = "PTP_FUTURE"
    PTP_PARTIAL = "PTP_PARTIAL"
    ALREADY_PAID = "ALREADY_PAID"
    CALLBACK_SCHEDULED = "CALLBACK_SCHEDULED"
    RTP_FINANCIAL = "RTP_FINANCIAL"
    RTP_MEDICAL = "RTP_MEDICAL"
    RTP_NO_REASON = "RTP_NO_REASON"
    DISPUTE_PAID = "DISPUTE_PAID"
    DISPUTE_CHARGES = "DISPUTE_CHARGES"
    NO_LOAN = "NO_LOAN"
    WRONG_NUMBER = "WRONG_NUMBER"
    THIRD_PARTY = "THIRD_PARTY"
    BUSY = "BUSY"
    RNR = "RNR"
    VM = "VM"
    DSCN = "DSCN"
    UNCLEAR = "UNCLEAR"


#: Codes that require a resolved ``ptp_date`` to be considered valid.
#: Without one the guardrail downgrades to ``UNCLEAR`` rather than let an
#: unsupported promise-to-pay through (assignment section 2: dispositions must
#: rest on explicit customer statements, never inference).
PTP_CODES_REQUIRING_DATE: frozenset[StageCode] = frozenset(
    {
        StageCode.PTP_TODAY,
        StageCode.PTP_TOMORROW,
        StageCode.PTP_FUTURE,
        StageCode.PTP_PARTIAL,
    }
)


class CallStatus(StrEnum):
    """Lifecycle of a call record."""

    INITIATED = "INITIATED"
    TRIGGER_FAILED = "TRIGGER_FAILED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Terminal states. A late or out-of-order webhook must not drag a call back
#: out of one of these (see ``app/services/call_service.py``).
TERMINAL_STATUSES: frozenset[CallStatus] = frozenset(
    {CallStatus.COMPLETED, CallStatus.TRIGGER_FAILED, CallStatus.FAILED}
)


class Language(StrEnum):
    """Assignment section 3.3 mandates English (US) and Spanish."""

    EN_US = "en-US"
    ES = "es"
    MIXED = "Mixed"
