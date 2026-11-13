import csv
import json
from pathlib import Path

import pytest

from ledger_pipeline.db import get_conn
from ledger_pipeline.derived import rebuild_catalogue, rebuild_derived
from ledger_pipeline.events import ingest_event_file
from ledger_pipeline.leads import ingest_lead_file
from ledger_pipeline.link import relink_orphans
from ledger_pipeline.reports import write_all_reports
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


def _lead(tmp_path, phone="9876543210", name="batch_001.csv", ref="MOT/2026/1"):
    p = tmp_path / name
    p.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        f"Aarav,{phone},{ref},15-10-2026,₹1000,en\n",
        encoding="utf-8",
    )
    return p


def _evts(tmp_path, events, name="events_0001.jsonl"):
    p = tmp_path / name
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


def _setup_full(conn, tmp_path):
    """One lead with one call, one connected, one renewed disposition."""
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    ingest_lead_file(conn, rid, _lead(tmp_path), schemas["motor_renewal"])
    ingest_event_file(conn, rid, _evts(tmp_path, [
        _ev(event_id="e1", event_type="call.initiated"),
        _ev(event_id="e2", event_type="call.connected"),
        _ev(event_id="e3", event_type="disposition.set",
            payload={"disposition_code": "renewed"}),
    ]), dispositions)
    relink_orphans(conn)
    rebuild_catalogue(conn, dispositions)
    rebuild_derived(conn)


def test_all_five_files_created(conn, tmp_path):
    _setup_full(conn, tmp_path)
    out = tmp_path / "reports"
    write_all_reports(conn, out)
    for name in [
        "batch_summary.csv", "daily_overview.csv", "lead_report.csv",
        "rejections.csv", "data_quality.json",
    ]:
        assert (out / name).exists(), f"missing {name}"


def test_batch_summary_headers_and_row(conn, tmp_path):
    _setup_full(conn, tmp_path)
    out = tmp_path / "reports"
    write_all_reports(conn, out)
    with open(out / "batch_summary.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "workflow_id", "batch_id", "total_leads",
        "yet_to_reach_out", "in_progress_telephony_failed",
        "in_progress_dialed", "completed",
    ]
    # 1 lead, 1 completed
    assert rows[1] == ["motor_renewal", "batch_001", "1", "0", "0", "0", "1"]


def test_daily_overview_format(conn, tmp_path):
    _setup_full(conn, tmp_path)
    out = tmp_path / "reports"
    write_all_reports(conn, out)
    with open(out / "daily_overview.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "workflow_id", "date", "total_calls",
        "connected_calls", "connectivity_rate", "success_leads",
    ]
    # 2026-10-05 in IST for the event at 13:20:21 UTC (= 18:50:21 IST)
    assert rows[1][0] == "motor_renewal"
    assert rows[1][1] == "2026-10-05"
    assert rows[1][2] == "1"          # total_calls
    assert rows[1][3] == "1"          # connected_calls
    assert rows[1][4] == "1.0000"     # connectivity_rate 4 decimals
    assert rows[1][5] == "1"          # success_leads


def test_lead_report_format(conn, tmp_path):
    _setup_full(conn, tmp_path)
    out = tmp_path / "reports"
    write_all_reports(conn, out)
    with open(out / "lead_report.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "workflow_id", "external_ref", "batch_id", "phone_masked",
        "bucket", "total_calls", "connected_calls",
        "last_call_at", "current_disposition", "ever_success",
    ]
    r = rows[1]
    assert r[0] == "motor_renewal"
    assert r[1] == "MOT/2026/1"
    assert r[2] == "batch_001"
    assert r[3] == "+91******3210"
    assert r[4] == "completed"
    assert r[5] == "1"                   # total_calls
    assert r[6] == "1"                   # connected_calls
    assert r[7].startswith("2026-10-05T18:50:21")   # last_call_at ISO IST
    assert r[8] == "renewed"             # current_disposition
    assert r[9] == "true"                # ever_success boolean


def test_rejections_header(conn, tmp_path):
    _setup_full(conn, tmp_path)
    out = tmp_path / "reports"
    write_all_reports(conn, out)
    with open(out / "rejections.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["workflow_id", "batch_id", "row_number", "reason"]


def test_data_quality_keys(conn, tmp_path):
    _setup_full(conn, tmp_path)
    out = tmp_path / "reports"
    write_all_reports(conn, out)
    with open(out / "data_quality.json", encoding="utf-8") as f:
        d = json.load(f)
    expected_keys = {
        "lead_rows_total", "lead_rows_rejected",
        "rejections_by_reason", "duplicate_lead_rows_merged",
        "field_warnings", "events_total", "malformed_events",
        "duplicate_events_dropped", "orphan_events",
        "unknown_disposition_events",
    }
    assert set(d.keys()) == expected_keys
    assert d["lead_rows_total"] == 1
    assert d["events_total"] == 3
    assert d["orphan_events"] == 0
    assert isinstance(d["rejections_by_reason"], dict)


def test_multi_lead_multi_call_daily_overview(conn, tmp_path):
    """Two leads, three calls on same day, only 2 connected → rate 2/3."""
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    rid = _prep_run(conn)
    ingest_lead_file(conn, rid, _lead(tmp_path, phone="9876543210", ref="MOT/1"), schemas["motor_renewal"])
    ingest_lead_file(conn, rid, _lead(tmp_path, phone="9876543211", ref="MOT/2", name="batch_002.csv"),
                     schemas["motor_renewal"])
    events = [
        # call_1: connected + renewed (lead1)
        _ev(event_id="e1", event_type="call.initiated", call_id="call_1", phone="9876543210"),
        _ev(event_id="e2", event_type="call.connected", call_id="call_1", phone="9876543210"),
        _ev(event_id="e3", event_type="disposition.set", call_id="call_1", phone="9876543210",
            payload={"disposition_code": "renewed"}),
        # call_2: telephony failed (lead2)
        _ev(event_id="e4", event_type="call.initiated", call_id="call_2", phone="9876543211",
            occurred_at="2026-10-05T14:00:00Z"),
        _ev(event_id="e5", event_type="call.telephony_failed", call_id="call_2", phone="9876543211",
            occurred_at="2026-10-05T14:00:01Z", payload={"reason": "carrier"}),
        # call_3: connected no disposition (lead2)
        _ev(event_id="e6", event_type="call.initiated", call_id="call_3", phone="9876543211",
            occurred_at="2026-10-05T15:00:00Z"),
        _ev(event_id="e7", event_type="call.connected", call_id="call_3", phone="9876543211",
            occurred_at="2026-10-05T15:00:05Z"),
    ]
    ingest_event_file(conn, rid, _evts(tmp_path, events), dispositions)
    relink_orphans(conn)
    rebuild_catalogue(conn, dispositions)
    rebuild_derived(conn)

    out = tmp_path / "reports"
    write_all_reports(conn, out)
    with open(out / "daily_overview.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    # total_calls=3, connected_calls=2 → rate 0.6667, success_leads=1
    r = rows[1]
    assert r[2] == "3"
    assert r[3] == "2"
    assert r[4] == "0.6667"
    assert r[5] == "1"