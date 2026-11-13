import json
from pathlib import Path

import pytest

from ledger_pipeline.db import get_conn
from ledger_pipeline.derived import rebuild_catalogue, rebuild_derived
from ledger_pipeline.events import ingest_event_file
from ledger_pipeline.leads import ingest_lead_file
from ledger_pipeline.link import relink_orphans
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


def _lead(tmp_path, phone="9876543210", name="batch_001.csv"):
    p = tmp_path / name
    p.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        f"Aarav,{phone},MOT/2026/1,15-10-2026,₹1000,en\n",
        encoding="utf-8",
    )
    return p


def _evts(tmp_path, events):
    p = tmp_path / "events_0001.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def _ev(**kw):
    base = {
        "event_id": "evt_1", "event_type": "call.initiated",
        "workflow_id": "motor_renewal", "call_id": "call_1",
        "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210",
        "payload": {},
    }
    base.update(kw)
    return base


def _setup(tmp_path, conn, events):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    ingest_lead_file(conn, rid, _lead(tmp_path), schemas["motor_renewal"])
    ingest_event_file(conn, rid, _evts(tmp_path, events), dispositions)
    relink_orphans(conn)
    rebuild_catalogue(conn, dispositions)
    rebuild_derived(conn)
    return rid


def test_call_requires_initiated(conn, tmp_path):
    """A connected event without call.initiated does not create a call."""
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.connected"),
        _ev(event_id="e2", event_type="call.ended",
            payload={"duration_sec": 60, "end_reason": "completed"}),
    ])
    assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
    row = conn.execute("SELECT bucket, total_calls FROM lead_state").fetchone()
    assert row["bucket"] == "yet_to_reach_out"
    assert row["total_calls"] == 0


def test_connected_and_initiated_is_connected(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
    ])
    row = conn.execute("SELECT total_calls, connected_calls FROM lead_state").fetchone()
    assert row["total_calls"] == 1
    assert row["connected_calls"] == 1


def test_telephony_failed_only_bucket(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.telephony_failed",
            payload={"reason": "carrier_rejected"}),
    ])
    row = conn.execute("SELECT bucket FROM lead_state").fetchone()
    assert row["bucket"] == "in_progress_telephony_failed"


def test_terminal_disposition_is_completed(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
        _ev(event_id="e3", event_type="disposition.set",
            payload={"disposition_code": "renewed"}),
    ])
    row = conn.execute("SELECT bucket, ever_success, current_disposition FROM lead_state").fetchone()
    assert row["bucket"] == "completed"
    assert row["ever_success"] == 1
    assert row["current_disposition"] == "renewed"


def test_non_terminal_disposition_in_progress_dialed(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
        _ev(event_id="e3", event_type="disposition.set",
            payload={"disposition_code": "callback_requested"}),
    ])
    row = conn.execute("SELECT bucket, ever_success FROM lead_state").fetchone()
    assert row["bucket"] == "in_progress_dialed"
    assert row["ever_success"] == 0


def test_override_wins_over_set(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
        _ev(event_id="e3", event_type="disposition.set",
            occurred_at="2026-10-05T14:00:00Z",
            payload={"disposition_code": "not_interested"}),
        _ev(event_id="e4", event_type="disposition.override",
            occurred_at="2026-10-05T15:00:00Z",
            payload={"disposition_code": "renewed",
                     "overridden_by": "sup_1", "reason": "qa"}),
    ])
    # the override event has no phone in the source; but our test helper includes one,
    # so let's strip it from the second override event
    conn.execute(
        "UPDATE events_ledger SET phone_hash = NULL "
        "WHERE event_id = 'e4'"
    )
    relink_orphans(conn)
    rebuild_derived(conn)
    row = conn.execute(
        "SELECT effective_code FROM call_dispositions WHERE call_id = 'call_1'"
    ).fetchone()
    assert row["effective_code"] == "renewed"


def test_override_respects_occurred_at_order(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
        _ev(event_id="e3", event_type="disposition.set",
            occurred_at="2026-10-05T14:00:00Z",
            payload={"disposition_code": "callback_requested"}),
        _ev(event_id="e4", event_type="disposition.override",
            occurred_at="2026-10-06T08:00:00Z",
            payload={"disposition_code": "renewed", "overridden_by": "s1", "reason": "r"}),
        _ev(event_id="e5", event_type="disposition.override",
            occurred_at="2026-10-05T20:00:00Z",
            payload={"disposition_code": "not_interested", "overridden_by": "s2", "reason": "r"}),
    ])
    row = conn.execute(
        "SELECT effective_code FROM call_dispositions WHERE call_id = 'call_1'"
    ).fetchone()
    # Latest override by occurred_at wins: 2026-10-06 (renewed), not 2026-10-05 (not_interested)
    assert row["effective_code"] == "renewed"


def test_rebuild_is_idempotent(conn, tmp_path):
    _setup(tmp_path, conn, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
        _ev(event_id="e3", event_type="disposition.set",
            payload={"disposition_code": "renewed"}),
    ])
    before = conn.execute("SELECT COUNT(*) FROM lead_state").fetchone()[0]
    rebuild_derived(conn)
    after = conn.execute("SELECT COUNT(*) FROM lead_state").fetchone()[0]
    assert before == after == 1