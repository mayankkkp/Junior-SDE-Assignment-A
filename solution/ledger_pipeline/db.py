"""SQLite schema and connection factory for the ingestion pipeline.

Layers:
  ingest_runs / run_counters — per-drop bookkeeping (idempotency, DQ counters)
  leads                      — one row per (workflow_id, phone_hash); PII-safe
  rejections / field_warnings — per-row DQ records, no PII
  events_ledger              — append-only; dedupe via UNIQUE(event_id)

All tables are created if absent. `get_conn` is idempotent (safe on every run).
"""

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS ingest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_path       TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    UNIQUE (drop_path)
);

CREATE TABLE IF NOT EXISTS run_counters (
    ingest_run_id               INTEGER PRIMARY KEY
                                REFERENCES ingest_runs(id) ON DELETE CASCADE,
    lead_rows_total             INTEGER NOT NULL DEFAULT 0,
    lead_rows_rejected          INTEGER NOT NULL DEFAULT 0,
    duplicate_lead_rows_merged  INTEGER NOT NULL DEFAULT 0,
    field_warnings              INTEGER NOT NULL DEFAULT 0,
    events_total                INTEGER NOT NULL DEFAULT 0,
    malformed_events            INTEGER NOT NULL DEFAULT 0,
    duplicate_events_dropped    INTEGER NOT NULL DEFAULT 0,
    unknown_disposition_events  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id     TEXT NOT NULL,
    phone_hash      TEXT NOT NULL,
    phone_masked    TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    external_ref    TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (workflow_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS rejections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id     TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    row_number      INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    ingest_run_id   INTEGER NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rej_wf ON rejections(workflow_id, batch_id, row_number);

CREATE TABLE IF NOT EXISTS field_warnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id     TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    row_number      INTEGER NOT NULL,
    field_name      TEXT NOT NULL,
    ingest_run_id   INTEGER NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_warn_wf ON field_warnings(workflow_id, batch_id, row_number);

CREATE TABLE IF NOT EXISTS events_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    event_type      TEXT NOT NULL,
    workflow_id     TEXT NOT NULL,
    call_id         TEXT,
    occurred_at     TEXT NOT NULL,          -- ISO8601 UTC
    occurred_at_ist TEXT NOT NULL,          -- ISO8601 IST
    ist_date        TEXT NOT NULL,          -- YYYY-MM-DD in IST
    phone_hash      TEXT,                   -- NULL for overrides
    payload_json    TEXT NOT NULL,
    lead_id         INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    is_orphan       INTEGER NOT NULL DEFAULT 1,
    dropped_reason  TEXT,                   -- 'unknown_disposition' | NULL
    ingest_run_id   INTEGER NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dispositions_catalogue (
    workflow_id  TEXT NOT NULL,
    code         TEXT NOT NULL,
    is_terminal  INTEGER NOT NULL,
    is_success   INTEGER NOT NULL,
    PRIMARY KEY (workflow_id, code)
);

CREATE TABLE IF NOT EXISTS calls (
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    workflow_id  TEXT NOT NULL,
    call_id      TEXT NOT NULL,
    initiated_at TEXT NOT NULL,           -- ISO8601 IST
    ist_date     TEXT NOT NULL,           -- YYYY-MM-DD IST
    PRIMARY KEY (lead_id, call_id)
);
CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(workflow_id, ist_date);
CREATE INDEX IF NOT EXISTS idx_calls_lead ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_call ON calls(call_id);

CREATE TABLE IF NOT EXISTS call_dispositions (
    call_id          TEXT PRIMARY KEY,
    workflow_id      TEXT NOT NULL,
    effective_code   TEXT NOT NULL,
    source_event_id  INTEGER NOT NULL,
    effective_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cd_wf ON call_dispositions(workflow_id);

CREATE TABLE IF NOT EXISTS lead_state (
    lead_id                 INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
    workflow_id             TEXT NOT NULL,
    total_calls             INTEGER NOT NULL DEFAULT 0,
    connected_calls         INTEGER NOT NULL DEFAULT 0,
    telephony_failed_calls  INTEGER NOT NULL DEFAULT 0,
    last_call_at            TEXT,
    last_call_ist_date      TEXT,
    current_disposition     TEXT,
    ever_success            INTEGER NOT NULL DEFAULT 0,
    bucket                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ls_bucket ON lead_state(workflow_id, bucket);
CREATE INDEX IF NOT EXISTS idx_ev_call ON events_ledger(call_id);
CREATE INDEX IF NOT EXISTS idx_ev_lead ON events_ledger(lead_id);
CREATE INDEX IF NOT EXISTS idx_ev_orphan ON events_ledger(is_orphan);
CREATE INDEX IF NOT EXISTS idx_ev_phone ON events_ledger(workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS idx_ev_type ON events_ledger(event_type);
"""


def get_conn(db_path: str) -> sqlite3.Connection:
    """Open or create the DB. Safe to call every run."""
    if db_path != ":memory:":
        Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn