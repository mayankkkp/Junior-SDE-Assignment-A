"""Command-line entrypoint.

Usage:
    python -m ledger_pipeline.cli ingest <drop_dir>
    python -m ledger_pipeline.cli report --out <dir>

Env:
    PII_SALT must be set (any secret string).
"""

import argparse
import sys
from pathlib import Path

from .db import get_conn
from .drop import ingest_drop
from .reports import write_all_reports
from .schemas import load_catalog


SCHEMAS_DIR = Path(__file__).parent.parent / "assignment-a-data" / "schemas"
DEFAULT_DB = "pipeline.db"


def cmd_ingest(args: argparse.Namespace) -> int:
    schemas, dispositions = load_catalog(SCHEMAS_DIR)
    drop_path = Path(args.drop_dir)
    if not drop_path.is_dir():
        sys.exit(f"ERROR: not a directory: {drop_path}")

    conn = get_conn(args.db)
    run_id = ingest_drop(conn, drop_path, schemas, dispositions)

    if run_id is None:
        print(f"Drop already ingested (idempotent no-op): {drop_path}")
    else:
        counters = conn.execute(
            "SELECT * FROM run_counters WHERE ingest_run_id = ?", (run_id,),
        ).fetchone()
        print(f"Ingested drop: {drop_path}")
        print(f"  run_id={run_id}")
        for k in counters.keys():
            if k != "ingest_run_id":
                print(f"  {k} = {counters[k]}")

    conn.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = get_conn(args.db)
    out_dir = Path(args.out)
    write_all_reports(conn, out_dir)
    print(f"Reports written to: {out_dir.resolve()}")
    for name in [
        "batch_summary.csv", "daily_overview.csv", "lead_report.csv",
        "rejections.csv", "data_quality.json",
    ]:
        p = out_dir / name
        size = p.stat().st_size if p.exists() else 0
        print(f"  {name:<22} {size:>8} bytes")
    conn.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="ledger_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="Ingest a drop directory")
    pi.add_argument("drop_dir")
    pi.add_argument("--db", default=DEFAULT_DB)
    pi.set_defaults(func=cmd_ingest)

    pr = sub.add_parser("report", help="Write the five report files")
    pr.add_argument("--out", required=True)
    pr.add_argument("--db", default=DEFAULT_DB)
    pr.set_defaults(func=cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())