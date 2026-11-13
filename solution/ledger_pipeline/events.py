"""Event JSONL parsing and classification.

Per-line processing order (spec):
  1. malformed       -- not JSON, or missing event_id            -> count, skip
  2. duplicate       -- event_id already seen                    -> count, skip
  3. orphan          -- can't link to a lead                     -> count (in Stage 5, re-link later)
  4. unknown disp    -- disposition.* code not in catalogue      -> count, skip
  5. valid           -- insert into events_ledger
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .dates import parse_timestamp, ist_date_str
from .phone import parse_phone
from .pii import phone_hash
from .schemas import Disposition, WorkflowSchema


EVENT_TYPES = {
    "call.initiated",
    "call.telephony_failed",
    "call.connected",
    "call.ended",
    "disposition.set",
    "disposition.override",
}

_OVERRIDE_TYPES = {"disposition.override"}
_DISPOSITION_TYPES = {"disposition.set", "disposition.override"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ingest_one_line(
    conn: sqlite3.Connection,
    line: str,
    ingest_run_id: int,
    dispositions_by_wf: dict[str, dict[str, Disposition]],
) -> str:
    """
    Returns one of: 'malformed', 'duplicate', 'unknown_disposition', 'inserted'.
    Updates run_counters accordingly.
    """
    stripped = line.strip()
    if not stripped:
        return "blank"   # blank lines aren't counted per spec ("non-empty line")

    # 1. Malformed?
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        conn.execute(
            "UPDATE run_counters SET malformed_events = malformed_events + 1 "
            "WHERE ingest_run_id = ?", (ingest_run_id,),
        )
        return "malformed"
    if not isinstance(obj, dict) or "event_id" not in obj:
        conn.execute(
            "UPDATE run_counters SET malformed_events = malformed_events + 1 "
            "WHERE ingest_run_id = ?", (ingest_run_id,),
        )
        return "malformed"

    event_id = str(obj["event_id"])
    event_type = obj.get("event_type", "")
    workflow_id = obj.get("workflow_id", "")

    if event_type not in EVENT_TYPES:
        # Unknown event type: treat as malformed (spec doesn't name it separately)
        conn.execute(
            "UPDATE run_counters SET malformed_events = malformed_events + 1 "
            "WHERE ingest_run_id = ?", (ingest_run_id,),
        )
        return "malformed"

    # 2. Duplicate? (UNIQUE on event_id)
    already = conn.execute(
        "SELECT 1 FROM events_ledger WHERE event_id = ?", (event_id,),
    ).fetchone()
    if already is not None:
        conn.execute(
            "UPDATE run_counters SET duplicate_events_dropped = duplicate_events_dropped + 1 "
            "WHERE ingest_run_id = ?", (ingest_run_id,),
        )
        return "duplicate"

    # Parse timestamp; malformed if unparseable
    ts = parse_timestamp(obj.get("occurred_at", ""))
    if ts is None:
        conn.execute(
            "UPDATE run_counters SET malformed_events = malformed_events + 1 "
            "WHERE ingest_run_id = ?", (ingest_run_id,),
        )
        return "malformed"

    # 4. Unknown disposition?
    payload = obj.get("payload") or {}
    dropped_reason = None
    if event_type in _DISPOSITION_TYPES:
        code = payload.get("disposition_code")
        catalogue = dispositions_by_wf.get(workflow_id, {})
        if code is None or code not in catalogue:
            conn.execute(
                "UPDATE run_counters SET unknown_disposition_events = unknown_disposition_events + 1 "
                "WHERE ingest_run_id = ?", (ingest_run_id,),
            )
            return "unknown_disposition"

    # Extract phone (not present for overrides)
    phone_hash_val = None
    if event_type not in _OVERRIDE_TYPES:
        raw_phone = obj.get("phone")
        if raw_phone:
            canonical = parse_phone(raw_phone)
            if canonical:
                phone_hash_val = phone_hash(canonical)
            # if phone present but invalid -> treat as orphan (can't link), keep in ledger
            # This is consistent with spec: "An event links via (workflow_id, normalised phone)"
            # If phone can't be normalised, the event can't link -> orphan (is_orphan=1)

    # Insert valid event as orphan (Stage 5 re-links)
    occurred_utc = ts.astimezone(timezone.utc).isoformat()
    occurred_ist = ts.isoformat()
    ist_date = ist_date_str(ts)

    try:
        conn.execute(
            """INSERT INTO events_ledger
               (event_id, event_type, workflow_id, call_id,
                occurred_at, occurred_at_ist, ist_date,
                phone_hash, payload_json, lead_id, is_orphan,
                dropped_reason, ingest_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, NULL, ?)""",
            (event_id, event_type, workflow_id, obj.get("call_id"),
             occurred_utc, occurred_ist, ist_date,
             phone_hash_val, json.dumps(payload, ensure_ascii=False),
             ingest_run_id),
        )
    except sqlite3.IntegrityError:
        # Race condition: another line inserted the same event_id.
        # Count as duplicate — correct behavior.
        conn.execute(
            "UPDATE run_counters SET duplicate_events_dropped = duplicate_events_dropped + 1 "
            "WHERE ingest_run_id = ?", (ingest_run_id,),
        )
        return "duplicate"

    return "inserted"


def ingest_event_file(
    conn: sqlite3.Connection,
    ingest_run_id: int,
    file_path: Path,
    dispositions_by_wf: dict[str, dict[str, Disposition]],
) -> None:
    """Ingest one .jsonl event file. Called for each file in drop/events/."""
    with open(file_path, encoding="utf-8-sig") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue  # blank line: not counted
            conn.execute(
                "UPDATE run_counters SET events_total = events_total + 1 "
                "WHERE ingest_run_id = ?", (ingest_run_id,),
            )
            _ingest_one_line(conn, stripped, ingest_run_id, dispositions_by_wf)

    conn.commit()