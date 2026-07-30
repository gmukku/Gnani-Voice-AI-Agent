"""Stage-code groupings behind the dashboard summary cards.

Assignment section 6.2 asks for eight summary cards. Five of them ("PTP",
"Already paid", "Refusal to pay", "Dispute", "Non-connect") are groupings over
stage codes rather than call statuses, so they are defined once here and reused
by both the cards and the analytics charts.
"""

from __future__ import annotations

from app.models.enums import CallStatus, StageCode

PTP: frozenset[StageCode] = frozenset(
    {
        StageCode.PTP_TODAY,
        StageCode.PTP_TOMORROW,
        StageCode.PTP_FUTURE,
        StageCode.PTP_PARTIAL,
    }
)

ALREADY_PAID: frozenset[StageCode] = frozenset({StageCode.ALREADY_PAID})

REFUSAL_TO_PAY: frozenset[StageCode] = frozenset(
    {
        StageCode.RTP_FINANCIAL,
        StageCode.RTP_MEDICAL,
        StageCode.RTP_NO_REASON,
    }
)

DISPUTE: frozenset[StageCode] = frozenset(
    {
        StageCode.DISPUTE_PAID,
        StageCode.DISPUTE_CHARGES,
        StageCode.NO_LOAN,
    }
)

#: The customer was never meaningfully reached.
NON_CONNECT: frozenset[StageCode] = frozenset(
    {
        StageCode.RNR,
        StageCode.VM,
        StageCode.DSCN,
        StageCode.BUSY,
        StageCode.WRONG_NUMBER,
    }
)

#: "Connected" means a real conversation happened -- i.e. anything that is not
#: a non-connect outcome. Derived rather than hand-listed so that adding a
#: stage code cannot silently desync the two.
CONNECTED: frozenset[StageCode] = frozenset(
    code for code in StageCode if code not in NON_CONNECT
)

#: Card label -> stage codes. Order is the display order on the dashboard.
CARD_GROUPS: dict[str, frozenset[StageCode]] = {
    "PTP Calls": PTP,
    "Already Paid": ALREADY_PAID,
    "Refusal to Pay": REFUSAL_TO_PAY,
    "Dispute": DISPUTE,
    "Non-Connect": NON_CONNECT,
}


def stage_distribution(records: list[dict]) -> list[dict[str, object]]:
    """Stage-code counts for the dashboard chart, highest first.

    Returns only codes actually present, so the chart does not render 19 empty
    bars on a fresh install.
    """
    counts: dict[str, int] = {}
    for record in records:
        code = record.get("stage_code")
        if code:
            counts[str(code)] = counts.get(str(code), 0) + 1

    if not counts:
        return []

    peak = max(counts.values())
    return [
        {"code": code, "count": count, "percent": round(count * 100 / peak)}
        for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def summarise(records: list[dict]) -> dict[str, int]:
    """Build the eight summary-card counts for a set of call records."""
    stage_codes = [r.get("stage_code") for r in records]
    statuses = [r.get("call_status") for r in records]

    counts = {
        "Total Calls": len(records),
        "Completed Calls": sum(1 for s in statuses if s == CallStatus.COMPLETED),
        "Connected Calls": sum(1 for c in stage_codes if c in CONNECTED),
    }
    for label, group in CARD_GROUPS.items():
        counts[label] = sum(1 for c in stage_codes if c in group)
    return counts
