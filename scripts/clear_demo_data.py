"""Remove mock-generated calls, keeping only real Gnani ones.

`scripts/seed_scenarios.py` populates the dashboard from `tests/mock_gnani` so
the stage-code groupings can be demonstrated without telephony. Once real calls
start arriving, those synthetic rows are noise -- and worse, they are
indistinguishable from real ones at a glance during a demo.

A call is treated as real when it was adopted from a Gnani post-call webhook
(``origin == "gnani_console"``) **and** carries a UUID conversation id, which is
the format the platform issues. That second condition matters: hand-built test
payloads also arrive as adopted calls, but with readable ids like
``console-webtest-demo-1``.

    python -m scripts.clear_demo_data            # keep real calls only
    python -m scripts.clear_demo_data --all      # wipe everything
    python -m scripts.clear_demo_data --dry-run  # show what would go
"""

from __future__ import annotations

import asyncio
import re
import sys

from app.config import get_settings
from app.db.repository import AUDIT, CALLS, DLQ, EVENTS, build_repository

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def is_real_gnani_call(record: dict) -> bool:
    if record.get("origin") != "gnani_console":
        return False
    return bool(UUID_RE.match(str(record.get("gnani_conversation_id") or "")))


async def main() -> None:
    wipe_all = "--all" in sys.argv
    dry_run = "--dry-run" in sys.argv

    settings = get_settings()
    repo = build_repository(settings)
    await repo.init()

    records = await repo.list_calls()
    keep = [] if wipe_all else [r for r in records if is_real_gnani_call(r)]
    keep_ids = {r["call_id"] for r in keep}
    drop = [r for r in records if r["call_id"] not in keep_ids]

    print(f"backend: {settings.storage_backend}")
    print(f"  total    {len(records)}")
    print(f"  keeping  {len(keep)}")
    print(f"  removing {len(drop)}")

    for r in keep:
        print(
            f"    keep  {r['call_id']}  {r.get('stage_code')}  "
            f"conv={r.get('gnani_conversation_id')}"
        )

    if dry_run:
        print("\ndry run -- nothing deleted")
        await repo.close()
        return

    if settings.storage_backend == "json":
        data = repo._data  # noqa: SLF001 - maintenance script, not app code
        data[CALLS] = keep
        data[EVENTS] = []
        data[AUDIT] = [a for a in data[AUDIT] if a["call_id"] in keep_ids]
        data[DLQ] = []
        await repo._flush()  # noqa: SLF001
    else:
        db = repo._db  # noqa: SLF001
        drop_ids = [r["call_id"] for r in drop]
        await db[CALLS].delete_many({"call_id": {"$in": drop_ids}})
        await db[AUDIT].delete_many({"call_id": {"$in": drop_ids}})
        # Event ids are not stored on the call, so the whole ledger goes. That
        # is safe: a webhook for a deleted call has nothing left to update, and
        # a redelivery for a surviving call would simply be reprocessed rather
        # than corrupting anything.
        await db[EVENTS].delete_many({})
        await db[DLQ].delete_many({})

    print(f"\nremoved {len(drop)} calls; {len(keep)} remain")
    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
