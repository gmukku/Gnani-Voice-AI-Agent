"""BSON encoding of call records.

Regression test for a bug the JSON backend hid completely. The call record holds
``date`` objects (``emi_details.emi_due_date`` and ``ptp_date``), which the JSON
backend stringified on the way out via ``_jsonable``. Mongo received them raw
and rejected every insert:

    bson.errors.InvalidDocument: cannot encode object: datetime.date(2026, 7, 30)

Only surfaced by running against a real MongoDB, so these tests assert the
conversion directly rather than relying on a live database.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.db.repository import _bson_safe


class TestBsonSafe:
    def test_date_becomes_an_iso_string(self) -> None:
        assert _bson_safe(date(2026, 7, 30)) == "2026-07-30"

    def test_datetime_is_left_native(self) -> None:
        """BSON stores datetimes natively, so Mongo can sort and range-query."""
        moment = datetime(2026, 7, 30, 4, 15, tzinfo=timezone.utc)
        assert _bson_safe(moment) is moment

    def test_datetime_is_checked_before_date(self) -> None:
        """datetime subclasses date, so order matters.

        Testing for ``date`` first would stringify every timestamp in the
        record, silently destroying Mongo's ability to sort by created_at.
        """
        moment = datetime(2026, 7, 30, 4, 15, tzinfo=timezone.utc)
        assert not isinstance(_bson_safe(moment), str)

    def test_nested_dates_are_converted(self) -> None:
        record = {
            "call_id": "CALL-1",
            "emi_details": {"emi_due_date": date(2026, 7, 30), "emi_amount": 12500},
            "ptp_date": date(2026, 7, 31),
            "created_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
        }
        safe = _bson_safe(record)

        assert safe["emi_details"]["emi_due_date"] == "2026-07-30"
        assert safe["ptp_date"] == "2026-07-31"
        assert isinstance(safe["created_at"], datetime)
        assert safe["emi_details"]["emi_amount"] == 12500

    def test_dates_inside_lists_are_converted(self) -> None:
        safe = _bson_safe({"transcript": [{"at": date(2026, 7, 30)}]})
        assert safe["transcript"][0]["at"] == "2026-07-30"

    def test_no_date_survives_anywhere_in_a_real_record(self) -> None:
        """The property that actually matters: BSON must be able to encode it."""
        from app.models.enums import CallStatus

        record = {
            "call_id": "CALL-20260730-abc123",
            "customer": {"customer_id": "CUST001", "phone_suffix": "123456789"},
            "emi_details": {
                "emi_amount": 12500,
                "emi_due_date": date(2026, 7, 30),
                "currency": "INR",
            },
            "call_status": CallStatus.INITIATED,
            "ptp_date": date(2026, 7, 31),
            "conversation_transcript": [
                {"speaker": "agent", "text": "hello", "day": date(2026, 7, 30)}
            ],
            "created_at": datetime.now(timezone.utc),
        }

        def find_bare_dates(value: object, path: str = "") -> list[str]:
            if isinstance(value, datetime):
                return []
            if isinstance(value, date):
                return [path]
            if isinstance(value, dict):
                return [
                    p
                    for k, v in value.items()
                    for p in find_bare_dates(v, f"{path}.{k}")
                ]
            if isinstance(value, (list, tuple)):
                return [
                    p
                    for i, v in enumerate(value)
                    for p in find_bare_dates(v, f"{path}[{i}]")
                ]
            return []

        assert find_bare_dates(record), "fixture should contain bare dates"
        leftovers = find_bare_dates(_bson_safe(record))
        assert not leftovers, f"un-encodable dates remain at: {leftovers}"
