import json
import sqlite3
from pathlib import Path

import pytest

from ledger_pipeline.db import get_conn
from ledger_pipeline.events import ingest_event_file
from ledger_pipeline.schemas import load_catalog

SCHEMAS_DIR = Path(__file__).parent.parent / "assignment-a-data" / "schemas"


@pytest.fixture(autouse=True)
def _set_salt(monkeypatch):
    monkeypatch.setenv("PII_SALT", "test-salt")


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    yield c
    c.close()


def _prep_run(conn):
    cur = conn.execute(
        "INSERT INTO ingest_runs (drop_path, content_sha256, ingested_at) "
        "VALUES ('x', 'y', 'z')"
    )
    rid = cur.lastrowid
    conn.execute("INSERT INTO run_counters (ingest_run_id) VALUES (?)", (rid,))
    conn.commit()
    return rid


def _write(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "events_0001.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _counters(conn, rid):
    return conn.execute("SELECT * FROM run_counters WHERE ingest_run_id = ?", (rid,)).fetchone()


def _ev(**kw):
    base = {
        "event_id": "evt_1",
        "event_type": "call.initiated",
        "workflow_id": "motor_renewal",
        "call_id": "call_1",
        "occurred_at": "2026-10-05T13:20:21Z",
        "phone": "9876543210",
        "payload": {},
    }
    base.update(kw)
    return json.dumps(base)


def test_valid_event_inserted(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, [_ev()])
    ingest_event_file(conn, rid, f, dispositions)
    assert conn.execute("SELECT COUNT(*) FROM events_ledger").fetchone()[0] == 1
    c = _counters(conn, rid)
    assert c["events_total"] == 1
    assert c["malformed_events"] == 0


def test_malformed_json_counted(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, ["not json", _ev()])
    ingest_event_file(conn, rid, f, dispositions)
    c = _counters(conn, rid)
    assert c["events_total"] == 2
    assert c["malformed_events"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events_ledger").fetchone()[0] == 1


def test_missing_event_id_malformed(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    obj = json.loads(_ev()); del obj["event_id"]
    f = _write(tmp_path, [json.dumps(obj)])
    ingest_event_file(conn, rid, f, dispositions)
    assert _counters(conn, rid)["malformed_events"] == 1


def test_duplicate_event_dropped(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, [_ev(), _ev()])   # same event_id twice
    ingest_event_file(conn, rid, f, dispositions)
    assert conn.execute("SELECT COUNT(*) FROM events_ledger").fetchone()[0] == 1
    c = _counters(conn, rid)
    assert c["events_total"] == 2
    assert c["duplicate_events_dropped"] == 1


def test_unknown_disposition_counted(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, [_ev(
        event_type="disposition.set",
        payload={"disposition_code": "nonexistent_code"},
    )])
    ingest_event_file(conn, rid, f, dispositions)
    assert conn.execute("SELECT COUNT(*) FROM events_ledger").fetchone()[0] == 0
    assert _counters(conn, rid)["unknown_disposition_events"] == 1


def test_valid_disposition_inserted(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, [_ev(
        event_type="disposition.set",
        payload={"disposition_code": "renewed"},
    )])
    ingest_event_file(conn, rid, f, dispositions)
    assert conn.execute("SELECT COUNT(*) FROM events_ledger").fetchone()[0] == 1
    assert _counters(conn, rid)["unknown_disposition_events"] == 0


def test_disposition_from_wrong_workflow_is_unknown(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    # 'renewed' is a motor_renewal code, not policy_feedback
    f = _write(tmp_path, [_ev(
        workflow_id="policy_feedback",
        event_type="disposition.set",
        payload={"disposition_code": "renewed"},
    )])
    ingest_event_file(conn, rid, f, dispositions)
    assert _counters(conn, rid)["unknown_disposition_events"] == 1


def test_override_without_phone_inserted(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    obj = json.loads(_ev(
        event_type="disposition.override",
        payload={"disposition_code": "renewed", "overridden_by": "sup_1", "reason": "qa"},
    ))
    del obj["phone"]   # overrides have no phone
    f = _write(tmp_path, [json.dumps(obj)])
    ingest_event_file(conn, rid, f, dispositions)
    row = conn.execute("SELECT phone_hash, event_type FROM events_ledger").fetchone()
    assert row["event_type"] == "disposition.override"
    assert row["phone_hash"] is None


def test_ist_date_computed(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    # 22:30 UTC on Oct 5 = 04:00 IST on Oct 6
    f = _write(tmp_path, [_ev(occurred_at="2026-10-05T22:30:00Z")])
    ingest_event_file(conn, rid, f, dispositions)
    row = conn.execute("SELECT ist_date, occurred_at_ist FROM events_ledger").fetchone()
    assert row["ist_date"] == "2026-10-06"
    assert "2026-10-06" in row["occurred_at_ist"]


def test_all_events_start_as_orphans(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, [_ev()])
    ingest_event_file(conn, rid, f, dispositions)
    row = conn.execute("SELECT is_orphan, lead_id FROM events_ledger").fetchone()
    assert row["is_orphan"] == 1
    assert row["lead_id"] is None


def test_blank_lines_not_counted(conn, tmp_path):
    _, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    f = _write(tmp_path, ["", _ev(), "   "])
    ingest_event_file(conn, rid, f, dispositions)
    c = _counters(conn, rid)
    assert c["events_total"] == 1