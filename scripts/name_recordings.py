"""Rename call recordings to carry their stage code.

Recordings download from the console with opaque names. Any filename containing
a conversation id can be matched against the stored call record, so the stage
code is looked up rather than guessed, and the file renamed to:

    call-<STAGE_CODE>-<conversation_id>.mp3

That makes the directory self-describing: which outcomes have audio evidence is
visible from `ls`, and each file still joins to its payload, its record and its
dashboard row through the conversation id.

Also rewrites the recordings table in `samples/README.md`.

    python -m scripts.name_recordings --dry-run
    python -m scripts.name_recordings
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from app.config import get_settings
from app.db.repository import build_repository

RECORDINGS = Path("samples/recordings")
INDEX = Path("samples/README.md")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
AUDIO = {".mp3", ".wav", ".m4a"}


def render_table(rows: list[dict]) -> str:
    lines = [
        "| Recording | Call | Stage code | Duration |",
        "|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: str(r["stage_code"])):
        lines.append(
            f"| `{r['filename']}` | `{r['call_id']}` | `{r['stage_code']}` "
            f"| {r['duration']}s |"
        )
    return "\n".join(lines)


def update_index(rows: list[dict]) -> bool:
    if not INDEX.exists() or not rows:
        return False
    text = INDEX.read_text(encoding="utf-8")
    # Replace the first markdown table in the recordings section.
    pattern = re.compile(r"\| Recording[^\n]*\|\n\|[-| ]+\|\n(?:\|[^\n]*\|\n)+")
    if not pattern.search(text):
        return False
    INDEX.write_text(
        pattern.sub(render_table(rows) + "\n", text, count=1), encoding="utf-8"
    )
    return True


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    settings = get_settings()
    repo = build_repository(settings)
    await repo.init()
    records = await repo.list_calls()

    by_conversation = {
        str(r.get("gnani_conversation_id")): r
        for r in records
        if r.get("gnani_conversation_id")
    }

    rows: list[dict] = []
    unmatched: list[str] = []

    for path in sorted(RECORDINGS.glob("*")):
        if path.suffix.lower() not in AUDIO:
            continue

        found = UUID_RE.search(path.name)
        if not found:
            unmatched.append(f"{path.name} (no conversation id in filename)")
            continue

        conversation_id = found.group(0).lower()
        record = by_conversation.get(conversation_id)
        if record is None:
            unmatched.append(f"{path.name} (no call record for {conversation_id})")
            continue

        stage = str(record.get("stage_code") or "UNKNOWN")
        target = path.with_name(f"call-{stage}-{conversation_id}{path.suffix.lower()}")

        if target != path:
            print(f"  {path.name}\n    -> {target.name}")
            if not dry_run:
                path.rename(target)
        else:
            print(f"  {path.name}  (already named correctly)")

        rows.append(
            {
                "filename": target.name,
                "call_id": record["call_id"],
                "stage_code": stage,
                "duration": record.get("call_duration_seconds") or "?",
            }
        )

    for problem in unmatched:
        print(f"  SKIPPED {problem}")

    if dry_run:
        print("\ndry run -- nothing renamed\n")
        print(render_table(rows))
    elif update_index(rows):
        print(f"\nupdated {INDEX} with {len(rows)} recordings")

    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
