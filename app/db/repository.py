"""Call-record persistence.

Assignment section 7 permits MongoDB, a JSON file, or CSV, and prefers
MongoDB. Both supported backends sit behind ``CallRepository`` so the routers
never know which is in use:

* ``MongoCallRepository`` -- the default, and what ``docker-compose`` runs.
* ``JsonCallRepository``  -- a single-file fallback, selected with
  ``STORAGE_BACKEND=json``. It exists so the whole call loop can be
  demonstrated with no infrastructure at all, which also makes it a safety net
  if Docker misbehaves during a demo.

The idempotency contract is the interesting part. ``record_webhook_event``
must return ``False`` for a duplicate rather than raising, because assignment
section 5.3 requires duplicate webhook deliveries to be absorbed without
creating duplicate records -- and returning a non-2xx to Gnani would just
invite another retry.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

CALLS = "calls"
EVENTS = "webhook_events"
DLQ = "webhook_dlq"
AUDIT = "audit_logs"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value: Any) -> Any:
    """Coerce datetimes/dates to ISO strings so records survive json.dump."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _bson_safe(value: Any) -> Any:
    """Convert ``date`` to an ISO string; leave ``datetime`` native.

    BSON has no date-only type and rejects ``datetime.date`` outright with
    ``InvalidDocument``. Timestamps stay as real BSON datetimes so Mongo can
    sort and range-query them; date-only fields (``emi_due_date``, ``ptp_date``)
    become ISO strings, which is also what the JSON backend stores, so both
    backends agree on those fields.

    The ``datetime`` check must come first: ``datetime`` is a subclass of
    ``date``, so testing for ``date`` alone would stringify every timestamp.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _bson_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(v) for v in value]
    return value


class CallRepository(Protocol):
    """Storage operations the application needs."""

    async def init(self) -> None: ...
    async def ping(self) -> bool: ...
    async def insert_call(self, record: dict[str, Any]) -> None: ...
    async def get_call(self, call_id: str) -> dict[str, Any] | None: ...
    async def update_call(self, call_id: str, changes: dict[str, Any]) -> bool: ...
    async def find_call_by_conversation(
        self, conversation_id: str
    ) -> dict[str, Any] | None: ...
    async def find_pending_call_by_phone(
        self, phone_suffix: str
    ) -> dict[str, Any] | None: ...
    async def list_calls(
        self, filters: dict[str, Any] | None = None, limit: int = 500
    ) -> list[dict[str, Any]]: ...
    async def record_webhook_event(self, event_id: str) -> bool: ...
    async def dead_letter(self, reason: str, payload: dict[str, Any]) -> None: ...
    async def audit(self, call_id: str, action: str, detail: dict[str, Any]) -> None: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------


class MongoCallRepository:
    """Motor-free async Mongo access via pymongo's native AsyncMongoClient."""

    def __init__(self, settings: Settings) -> None:
        from pymongo import AsyncMongoClient

        self._client: Any = AsyncMongoClient(
            settings.mongo_uri, serverSelectionTimeoutMS=3000
        )
        self._db = self._client[settings.mongo_db]

    async def init(self) -> None:
        from pymongo import ASCENDING

        await self._db[CALLS].create_index([("call_id", ASCENDING)], unique=True)
        await self._db[CALLS].create_index([("gnani_conversation_id", ASCENDING)])
        await self._db[CALLS].create_index([("customer.phone_suffix", ASCENDING)])
        await self._db[CALLS].create_index([("created_at", ASCENDING)])
        # Unique index is what actually enforces webhook idempotency; the
        # application-level check below is an optimisation, not the guarantee.
        await self._db[EVENTS].create_index([("event_id", ASCENDING)], unique=True)
        log.info("mongo.indexes_ready", db=self._db.name)

    async def ping(self) -> bool:
        try:
            await self._client.admin.command("ping")
            return True
        except Exception as exc:  # noqa: BLE001 - health check must not raise
            log.warning("mongo.ping_failed", error=str(exc))
            return False

    async def insert_call(self, record: dict[str, Any]) -> None:
        await self._db[CALLS].insert_one(_bson_safe(dict(record)))

    async def get_call(self, call_id: str) -> dict[str, Any] | None:
        return await self._db[CALLS].find_one({"call_id": call_id}, {"_id": 0})

    async def update_call(self, call_id: str, changes: dict[str, Any]) -> bool:
        result = await self._db[CALLS].update_one(
            {"call_id": call_id},
            {"$set": _bson_safe({**changes, "updated_at": utcnow()})},
        )
        return result.matched_count > 0

    async def find_call_by_conversation(
        self, conversation_id: str
    ) -> dict[str, Any] | None:
        return await self._db[CALLS].find_one(
            {"gnani_conversation_id": conversation_id}, {"_id": 0}
        )

    async def find_pending_call_by_phone(
        self, phone_suffix: str
    ) -> dict[str, Any] | None:
        cursor = (
            self._db[CALLS]
            .find({"customer.phone_suffix": phone_suffix}, {"_id": 0})
            .sort("created_at", -1)
            .limit(1)
        )
        async for doc in cursor:
            return doc
        return None

    async def list_calls(
        self, filters: dict[str, Any] | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        cursor = (
            self._db[CALLS]
            .find(filters or {}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def record_webhook_event(self, event_id: str) -> bool:
        from pymongo.errors import DuplicateKeyError

        try:
            await self._db[EVENTS].insert_one(
                {"event_id": event_id, "received_at": utcnow()}
            )
            return True
        except DuplicateKeyError:
            return False

    async def dead_letter(self, reason: str, payload: dict[str, Any]) -> None:
        await self._db[DLQ].insert_one(
            _bson_safe(
                {"reason": reason, "payload": payload, "received_at": utcnow()}
            )
        )

    async def audit(
        self, call_id: str, action: str, detail: dict[str, Any]
    ) -> None:
        await self._db[AUDIT].insert_one(
            _bson_safe(
                {
                    "call_id": call_id,
                    "action": action,
                    "detail": detail,
                    "at": utcnow(),
                }
            )
        )

    async def close(self) -> None:
        await self._client.close()


# ---------------------------------------------------------------------------
# JSON file
# ---------------------------------------------------------------------------


class JsonCallRepository:
    """Single-file backend. Serialised through one lock; not for real load."""

    def __init__(self, settings: Settings) -> None:
        self._path = Path(settings.json_store_path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {
            CALLS: [],
            EVENTS: [],
            DLQ: [],
            AUDIT: [],
        }

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("json_store.corrupt_reinitialising", path=str(self._path))
        for key in (CALLS, EVENTS, DLQ, AUDIT):
            self._data.setdefault(key, [])
        await self._flush()
        log.info("json_store.ready", path=str(self._path))

    async def _flush(self) -> None:
        # Write to a temp file then replace, so a crash mid-write cannot leave
        # a truncated store behind.
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(_jsonable(self._data), indent=2), encoding="utf-8"
        )
        tmp.replace(self._path)

    async def ping(self) -> bool:
        return self._path.parent.exists()

    async def insert_call(self, record: dict[str, Any]) -> None:
        async with self._lock:
            self._data[CALLS].append(_jsonable(record))
            await self._flush()

    async def get_call(self, call_id: str) -> dict[str, Any] | None:
        return next(
            (c for c in self._data[CALLS] if c.get("call_id") == call_id), None
        )

    async def update_call(self, call_id: str, changes: dict[str, Any]) -> bool:
        async with self._lock:
            for call in self._data[CALLS]:
                if call.get("call_id") == call_id:
                    call.update(_jsonable(changes))
                    call["updated_at"] = utcnow().isoformat()
                    await self._flush()
                    return True
            return False

    async def find_call_by_conversation(
        self, conversation_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                c
                for c in self._data[CALLS]
                if c.get("gnani_conversation_id") == conversation_id
            ),
            None,
        )

    async def find_pending_call_by_phone(
        self, phone_suffix: str
    ) -> dict[str, Any] | None:
        matches = [
            c
            for c in self._data[CALLS]
            if c.get("customer", {}).get("phone_suffix") == phone_suffix
        ]
        matches.sort(key=lambda c: c.get("created_at", ""), reverse=True)
        return matches[0] if matches else None

    async def list_calls(
        self, filters: dict[str, Any] | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = sorted(
            self._data[CALLS], key=lambda c: c.get("created_at", ""), reverse=True
        )
        for key, value in (filters or {}).items():
            rows = [r for r in rows if _nested_get(r, key) == value]
        return rows[:limit]

    async def record_webhook_event(self, event_id: str) -> bool:
        async with self._lock:
            if any(e["event_id"] == event_id for e in self._data[EVENTS]):
                return False
            self._data[EVENTS].append(
                {"event_id": event_id, "received_at": utcnow().isoformat()}
            )
            await self._flush()
            return True

    async def dead_letter(self, reason: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._data[DLQ].append(
                {
                    "reason": reason,
                    "payload": _jsonable(payload),
                    "received_at": utcnow().isoformat(),
                }
            )
            await self._flush()

    async def audit(
        self, call_id: str, action: str, detail: dict[str, Any]
    ) -> None:
        async with self._lock:
            self._data[AUDIT].append(
                {
                    "call_id": call_id,
                    "action": action,
                    "detail": _jsonable(detail),
                    "at": utcnow().isoformat(),
                }
            )
            await self._flush()

    async def close(self) -> None:
        async with self._lock:
            await self._flush()


def _nested_get(doc: dict[str, Any], dotted: str) -> Any:
    current: Any = doc
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def build_repository(settings: Settings | None = None) -> CallRepository:
    settings = settings or get_settings()
    if settings.storage_backend == "json":
        return JsonCallRepository(settings)
    return MongoCallRepository(settings)
