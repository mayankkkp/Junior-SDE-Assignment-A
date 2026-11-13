import sqlite3
from pathlib import Path

import pytest

from ledger_pipeline.db import get_conn
from ledger_pipeline.drop import ingest_drop
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


@pytest.fixture
def drop_dir(tmp_path: Path) -> Path:
    d = tmp_path / "drop_1"
    (d / "leads" / "motor_renewal").mkdir(parents=True)
    (d / "leads" / "motor_renewal" / "batch_001.csv").write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav,9876543210,MOT/2026/1,15-10-2026,₹1000,en\n",
        encoding="utf-8",
    )
    return d


def test_ingest_populates(conn, drop_dir):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    run_id = ingest_drop(conn, drop_dir, schemas, dispositions)
    assert run_id is not None
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1


def test_reingest_identical_drop_is_noop(conn, drop_dir):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    ingest_drop(conn, drop_dir, schemas, dispositions)
    again = ingest_drop(conn, drop_dir, schemas, dispositions)
    assert again is None
    assert conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1


def test_reingest_changed_drop_raises(conn, drop_dir):
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    ingest_drop(conn, drop_dir, schemas, dispositions)
    (drop_dir / "leads" / "motor_renewal" / "batch_001.csv").write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav,9876543210,MOT/2026/1,15-10-2026,₹9999,en\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="content changed"):
        ingest_drop(conn, drop_dir, schemas, dispositions)
    