"""Export a real Gnani post-call payload as a repository sample.

Submission item 14 asks for sample post-call webhook payloads. A hand-written
example would be a guess; this exports one captured from an actual call, which
is also the document that revealed the real contract (nested extraction fields,
role/content transcript turns, Unix timestamps).

Account-identifying values are redacted, since the file is committed.

    python -m scripts.export_samples
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.db.repository import build_repository

OUT_DIR = Path("samples/webhooks")

#: Values tied to the Gnani account rather than to the call itself.
REDACT = {
    "user_name",
    "user_id",
    "kb_owner_user_id",
    "aura_org_id",
    "organization_id",
    "bot_id",
    "sender_id",
    "customerCRTId",
    "call_uid",
}


def redact(payload: dict) -> dict:
    cleaned = dict(payload)
    for key in REDACT:
        if key in cleaned and cleaned[key]:
            cleaned[key] = "<redacted>"
    infra = cleaned.get("call_infra", {}).get("call_status")
    if isinstance(infra, dict):
        for key in REDACT:
            if key in infra and infra[key]:
                infra[key] = "<redacted>"
    return cleaned


async def main() -> None:
    settings = get_settings()
    repo = build_repository(settings)
    await repo.init()

    records = await repo.list_calls()
    real = [
        r
        for r in records
        if r.get("origin") == "gnani_console" and r.get("post_call_payload")
    ]

    if not real:
        print("No real Gnani call found. Make a call first, then re-run.")
        await repo.close()
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for record in real:
        payload = redact(record["post_call_payload"])
        code = str(record.get("stage_code") or "unknown").lower()
        path = OUT_DIR / f"real_post_call_{code}.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"  {path}")
        print(f"    stage code      {record.get('stage_code')}")
        print(f"    top-level keys  {len(payload)}")
        print(f"    transcript      {len(payload.get('transcript') or [])} turns")

    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
