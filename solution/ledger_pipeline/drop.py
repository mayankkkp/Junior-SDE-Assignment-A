"""Ingest one drop directory.

Order:
  1. Register ingest_run (content-hash idempotency).
  2. Ingest leads for all workflows (leads/*.csv).
  3. Ingest events (events/*.jsonl).
  4. Re-link orphans (so late leads rescue earlier orphans).
  5. Refresh dispositions catalogue + derived facts.
"""

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .derived import rebuild_catalogue, rebuild_derived
from .events import ingest_event_file
from .leads import ingest_lead_file
from .link import relink_orphans
from .schemas import Disposition, WorkflowSchema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _drop_sha256(drop_path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(drop_path.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(drop_path).as_posix().encode())
            h.update(b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()


def ingest_drop(
    conn: sqlite3.Connection,
    drop_path: Path,
    schemas: dict[str, WorkflowSchema],
    dispositions: dict[str, dict[str, Disposition]],
) -> int | None:
    """Idempotent drop ingestion. Returns run_id, or None if already ingested."""
    drop_path = Path(drop_path).resolve()
    drop_key = str(drop_path)
    digest = _drop_sha256(drop_path)

    existing = conn.execute(
        "SELECT id, content_sha256 FROM ingest_runs WHERE drop_path = ?",
        (drop_key,),
    ).fetchone()
    if existing is not None:
        if existing["content_sha256"] == digest:
            return None
        raise RuntimeError(
            f"Drop {drop_key} was ingested earlier but content changed. "
            "Start a fresh DB or use a new drop path."
        )

    cur = conn.execute(
        "INSERT INTO ingest_runs (drop_path, content_sha256, ingested_at) "
        "VALUES (?, ?, ?)", (drop_key, digest, _now()),
    )
    ingest_run_id = cur.lastrowid
    conn.execute("INSERT INTO run_counters (ingest_run_id) VALUES (?)", (ingest_run_id,))
    conn.commit()

    # 1. Leads (schema-mapped CSVs)
    leads_root = drop_path / "leads"
    if leads_root.is_dir():
        for wf_dir in sorted(leads_root.iterdir()):
            if not wf_dir.is_dir():
                continue
            if wf_dir.name not in schemas:
                continue
            schema = schemas[wf_dir.name]
            for csv_file in sorted(wf_dir.glob("*.csv")):
                ingest_lead_file(conn, ingest_run_id, csv_file, schema)

    # 2. Events (jsonl, one file at a time)
    events_root = drop_path / "events"
    if events_root.is_dir():
        for jsonl in sorted(events_root.glob("*.jsonl")):
            ingest_event_file(conn, ingest_run_id, jsonl, dispositions)

    # 3. Link events to leads. Re-link after every drop so late-arriving
    #    leads rescue events that were orphans in earlier drops.
    relink_orphans(conn)

    # 4. Refresh dispositions catalogue + derived facts (calls, call_dispositions,
    #    lead_state) — rebuilt deterministically from events_ledger.
    rebuild_catalogue(conn, dispositions)
    rebuild_derived(conn)

    conn.commit()
    return ingest_run_id