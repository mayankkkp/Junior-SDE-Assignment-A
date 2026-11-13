import sqlite3
import tempfile
from pathlib import Path

import pytest

from ledger_pipeline.db import get_conn
from ledger_pipeline.leads import ingest_lead_file
from ledger_pipeline.schemas import load_catalog

SCHEMAS_DIR = Path(__file__).parent.parent / "assignment-a-data" / "schemas"
SALT = "test-salt"


@pytest.fixture(autouse=True)
def _set_salt(monkeypatch):
    monkeypatch.setenv("PII_SALT", SALT)


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
    run_id = cur.lastrowid
    conn.execute("INSERT INTO run_counters (ingest_run_id) VALUES (?)", (run_id,))
    conn.commit()
    return run_id


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


MR_HEADER = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"


def test_happy_path_inserts_lead(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 "Aarav Sharma,9876543210,MOT/2026/1,15-10-2026,₹1234,en\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])

    row = conn.execute("SELECT * FROM leads").fetchone()
    assert row["workflow_id"] == "motor_renewal"
    assert row["phone_masked"] == "+91******3210"
    assert row["batch_id"] == "batch_001"
    assert row["external_ref"] == "MOT/2026/1"
    # name must NOT appear in payload
    import json
    payload = json.loads(row["payload_json"])
    assert "customer_name" not in payload
    assert "phone" not in payload


def test_missing_required_first_in_schema_order(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    # no name AND no phone — reason must be missing_required:customer_name
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 ",,MOT/2026/1,,,\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    r = conn.execute("SELECT reason FROM rejections").fetchone()
    assert r["reason"] == "missing_required:customer_name"


def test_missing_phone_only_gets_phone_reason(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 "Aarav,,MOT/2026/1,,,\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    r = conn.execute("SELECT reason FROM rejections").fetchone()
    assert r["reason"] == "missing_required:phone"


def test_invalid_phone_rejected(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 "Aarav,5123456789,MOT/2026/1,,,\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    r = conn.execute("SELECT reason FROM rejections").fetchone()
    assert r["reason"] == "invalid_phone"


def test_optional_invalid_dates_warn_but_insert(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 "Aarav,9876543210,MOT/2026/1,31/02/2026,N/A,xx\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    assert _count(conn, "leads") == 1
    warns = conn.execute(
        "SELECT field_name FROM field_warnings ORDER BY field_name"
    ).fetchall()
    assert [w["field_name"] for w in warns] == ["policy_expiry", "preferred_language", "premium_amount"]


def test_blank_rows_skipped_not_counted(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 ",,,\n"
                 "Aarav,9876543210,MOT/2026/1,,,\n"
                 "\n"
                 ",,,\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    c = conn.execute("SELECT * FROM run_counters WHERE ingest_run_id = ?", (run_id,)).fetchone()
    assert c["lead_rows_total"] == 1
    assert c["lead_rows_rejected"] == 0


def test_duplicate_merges_and_counts(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv1 = _write(tmp_path, "batch_001.csv", MR_HEADER +
                  "Aarav,9876543210,MOT/2026/1,,₹1000,en\n")
    csv2 = _write(tmp_path, "batch_002.csv", MR_HEADER +
                  "Aarav,9876543210,MOT/2026/1,15-10-2026,₹2000,hi\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv1, schemas["motor_renewal"])
    ingest_lead_file(conn, run_id, csv2, schemas["motor_renewal"])

    assert _count(conn, "leads") == 1
    c = conn.execute(
        "SELECT duplicate_lead_rows_merged FROM run_counters WHERE ingest_run_id = ?",
        (run_id,),
    ).fetchone()
    assert c["duplicate_lead_rows_merged"] == 1

    row = conn.execute("SELECT batch_id, payload_json FROM leads").fetchone()
    assert row["batch_id"] == "batch_001"       # first batch wins
    import json
    payload = json.loads(row["payload_json"])
    assert payload["premium_amount"] == "2000.00"   # second (non-null) wins
    assert payload["preferred_language"] == "hi"


def test_default_language_applied(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    csv = _write(tmp_path, "batch_001.csv", MR_HEADER +
                 "Aarav,9876543210,MOT/2026/1,,,\n")
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    import json
    row = conn.execute("SELECT payload_json FROM leads").fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["preferred_language"] == "en"


def test_alias_headers_work(conn, tmp_path):
    schemas, _ = load_catalog(SCHEMAS_DIR)
    content = ("Insured Name,Mobile No,Policy No,Expiry Date,Premium,Language,agent_notes\n"
               "Aarav,+91 98765 43210,MOT/2026/1,15-Oct-2026,Rs. 500,en,note\n")
    csv = _write(tmp_path, "batch_003.csv", content)
    run_id = _prep_run(conn)
    ingest_lead_file(conn, run_id, csv, schemas["motor_renewal"])
    row = conn.execute("SELECT phone_masked, external_ref FROM leads").fetchone()
    assert row["phone_masked"] == "+91******3210"
    assert row["external_ref"] == "MOT/2026/1"