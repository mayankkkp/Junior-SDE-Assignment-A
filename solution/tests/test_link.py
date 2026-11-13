import json
import sqlite3
from pathlib import Path

import pytest

from ledger_pipeline.db import get_conn
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


def _lead_csv(tmp_path, name="batch_001.csv", phone="9876543210"):
    p = tmp_path / name
    p.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        f"Aarav,{phone},MOT/2026/1,15-10-2026,₹1000,en\n",
        encoding="utf-8",
    )
    return p


def _ev_jsonl(tmp_path, events):
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


def test_event_with_matching_lead_is_linked(conn, tmp_path):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    ingest_lead_file(conn, rid, _lead_csv(tmp_path), schemas["motor_renewal"])
    ingest_event_file(conn, rid, _ev_jsonl(tmp_path, [_ev()]), dispositions)
    newly, orphan = relink_orphans(conn)
    assert newly == 1
    assert orphan == 0
    row = conn.execute("SELECT lead_id, is_orphan FROM events_ledger").fetchone()
    assert row["lead_id"] is not None
    assert row["is_orphan"] == 0


def test_event_with_no_matching_lead_stays_orphan(conn, tmp_path):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    # no lead ingested
    ingest_event_file(conn, rid, _ev_jsonl(tmp_path, [_ev()]), dispositions)
    newly, orphan = relink_orphans(conn)
    assert newly == 0
    assert orphan == 1


def test_late_lead_rescues_orphan(conn, tmp_path):
    """The spec's headline requirement: drop_2 lead rescues drop_1 orphan."""
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)

    # drop_1: event arrives, no lead
    ingest_event_file(conn, rid, _ev_jsonl(tmp_path, [_ev()]), dispositions)
    relink_orphans(conn)
    assert conn.execute("SELECT is_orphan FROM events_ledger").fetchone()[0] == 1

    # drop_2: lead arrives
    ingest_lead_file(conn, rid, _lead_csv(tmp_path, "batch_002.csv"), schemas["motor_renewal"])
    newly, orphan = relink_orphans(conn)
    assert newly == 1
    assert orphan == 0
    assert conn.execute("SELECT is_orphan FROM events_ledger").fetchone()[0] == 0


def test_override_links_via_call_id(conn, tmp_path):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    ingest_lead_file(conn, rid, _lead_csv(tmp_path), schemas["motor_renewal"])
    events = [
        _ev(event_id="evt_1", event_type="call.initiated"),
        _ev(event_id="evt_2", event_type="disposition.override",
            phone=None, payload={"disposition_code": "renewed",
                                 "overridden_by": "sup_1", "reason": "qa"}),
    ]
    # strip phone from override
    events[1].pop("phone")
    ingest_event_file(conn, rid, _ev_jsonl(tmp_path, events), dispositions)
    newly, orphan = relink_orphans(conn)
    assert newly == 2
    assert orphan == 0


def test_override_without_any_linked_call_stays_orphan(conn, tmp_path):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    override = _ev(event_id="evt_2", event_type="disposition.override",
                   call_id="call_unknown", payload={"disposition_code": "renewed",
                                                    "overridden_by": "x", "reason": "y"})
    override.pop("phone")
    ingest_event_file(conn, rid, _ev_jsonl(tmp_path, [override]), dispositions)
    newly, orphan = relink_orphans(conn)
    assert newly == 0
    assert orphan == 1


def test_relink_is_idempotent(conn, tmp_path):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    ingest_lead_file(conn, rid, _lead_csv(tmp_path), schemas["motor_renewal"])
    ingest_event_file(conn, rid, _ev_jsonl(tmp_path, [_ev()]), dispositions)
    relink_orphans(conn)
    newly2, orphan2 = relink_orphans(conn)
    assert newly2 == 0
    assert orphan2 == 0