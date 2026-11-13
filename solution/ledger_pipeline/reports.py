"""Report writers. Five files per the Assignment A output contract.

All CSVs use \n line endings, no index column, exact column names and order.
data_quality.json reflects the final state of all ingested drops.
"""

import csv
import json
import sqlite3
from pathlib import Path


def _open_csv(path: Path):
    f = open(path, "w", newline="", encoding="utf-8")
    w = csv.writer(f, lineterminator="\n")
    return f, w


# ---------- 1. batch_summary.csv ----------
def write_batch_summary(conn: sqlite3.Connection, out_dir: Path) -> None:
    """
    One row per (workflow_id, batch_id).
    Buckets: yet_to_reach_out / in_progress_telephony_failed /
             in_progress_dialed / completed.
    """
    path = out_dir / "batch_summary.csv"
    f, w = _open_csv(path)
    try:
        w.writerow([
            "workflow_id", "batch_id", "total_leads",
            "yet_to_reach_out", "in_progress_telephony_failed",
            "in_progress_dialed", "completed",
        ])
        rows = conn.execute(
            """
            SELECT
                l.workflow_id,
                l.batch_id,
                COUNT(*) AS total_leads,
                SUM(CASE WHEN ls.bucket = 'yet_to_reach_out'            THEN 1 ELSE 0 END) AS yet_to_reach_out,
                SUM(CASE WHEN ls.bucket = 'in_progress_telephony_failed' THEN 1 ELSE 0 END) AS in_progress_telephony_failed,
                SUM(CASE WHEN ls.bucket = 'in_progress_dialed'           THEN 1 ELSE 0 END) AS in_progress_dialed,
                SUM(CASE WHEN ls.bucket = 'completed'                    THEN 1 ELSE 0 END) AS completed
            FROM leads l
            JOIN lead_state ls ON ls.lead_id = l.id
            GROUP BY l.workflow_id, l.batch_id
            ORDER BY l.workflow_id, l.batch_id
            """
        ).fetchall()
        for r in rows:
            w.writerow([
                r["workflow_id"], r["batch_id"], r["total_leads"],
                r["yet_to_reach_out"], r["in_progress_telephony_failed"],
                r["in_progress_dialed"], r["completed"],
            ])
    finally:
        f.close()


# ---------- 2. daily_overview.csv ----------
def write_daily_overview(conn: sqlite3.Connection, out_dir: Path) -> None:
    """
    One row per (workflow_id, IST date with >= 1 call).
    connectivity_rate = connected_calls / total_calls, 4 decimals.
    success_leads = distinct leads with >= 1 call that date whose
                    effective disposition is_success=true.
    """
    path = out_dir / "daily_overview.csv"
    f, w = _open_csv(path)
    try:
        w.writerow([
            "workflow_id", "date", "total_calls",
            "connected_calls", "connectivity_rate", "success_leads",
        ])
        # Step 1: per (workflow, date) totals + connects
        base = conn.execute(
            """
            SELECT
                c.workflow_id,
                c.ist_date AS date,
                COUNT(*) AS total_calls,
                SUM(CASE WHEN EXISTS (
                    SELECT 1 FROM events_ledger e
                    WHERE e.call_id = c.call_id
                      AND e.event_type = 'call.connected'
                      AND e.lead_id IS NOT NULL
                ) THEN 1 ELSE 0 END) AS connected_calls
            FROM calls c
            GROUP BY c.workflow_id, c.ist_date
            ORDER BY c.workflow_id, c.ist_date
            """
        ).fetchall()

        # Step 2: for each (workflow, date), count distinct success leads
        # (leads that had a call that date AND any of that day's calls had
        # a success disposition)
        for r in base:
            wf, date = r["workflow_id"], r["date"]
            rate = (r["connected_calls"] / r["total_calls"]) if r["total_calls"] else 0.0
            rate_s = f"{rate:.4f}"

            success_n = conn.execute(
                """
                SELECT COUNT(DISTINCT c.lead_id)
                FROM calls c
                JOIN call_dispositions cd ON cd.call_id = c.call_id
                JOIN dispositions_catalogue d
                  ON d.workflow_id = cd.workflow_id
                 AND d.code = cd.effective_code
                 AND d.is_success = 1
                WHERE c.workflow_id = ? AND c.ist_date = ?
                """,
                (wf, date),
            ).fetchone()[0]

            w.writerow([wf, date, r["total_calls"], r["connected_calls"], rate_s, success_n])
    finally:
        f.close()


# ---------- 3. lead_report.csv ----------
def write_lead_report(conn: sqlite3.Connection, out_dir: Path) -> None:
    """
    One row per lead, sorted by workflow_id, external_ref.
    phone_masked from leads.phone_masked.
    last_call_at is ISO8601 IST (already stored in that form).
    current_disposition = effective disposition of most recent call.
    """
    path = out_dir / "lead_report.csv"
    f, w = _open_csv(path)
    try:
        w.writerow([
            "workflow_id", "external_ref", "batch_id", "phone_masked",
            "bucket", "total_calls", "connected_calls",
            "last_call_at", "current_disposition", "ever_success",
        ])
        rows = conn.execute(
            """
            SELECT
                l.workflow_id,
                l.external_ref,
                l.batch_id,
                l.phone_masked,
                ls.bucket,
                ls.total_calls,
                ls.connected_calls,
                ls.last_call_at,
                ls.current_disposition,
                ls.ever_success
            FROM leads l
            JOIN lead_state ls ON ls.lead_id = l.id
            ORDER BY l.workflow_id, l.external_ref
            """
        ).fetchall()
        for r in rows:
            w.writerow([
                r["workflow_id"], r["external_ref"], r["batch_id"], r["phone_masked"],
                r["bucket"], r["total_calls"], r["connected_calls"],
                r["last_call_at"] or "",
                r["current_disposition"] or "",
                "true" if r["ever_success"] else "false",
            ])
    finally:
        f.close()


# ---------- 4. rejections.csv ----------
def write_rejections(conn: sqlite3.Connection, out_dir: Path) -> None:
    path = out_dir / "rejections.csv"
    f, w = _open_csv(path)
    try:
        w.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        rows = conn.execute(
            """
            SELECT workflow_id, batch_id, row_number, reason
            FROM rejections
            ORDER BY workflow_id, batch_id, row_number
            """
        ).fetchall()
        for r in rows:
            w.writerow([r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]])
    finally:
        f.close()


# ---------- 5. data_quality.json ----------
def write_data_quality(conn: sqlite3.Connection, out_dir: Path) -> None:
    path = out_dir / "data_quality.json"

    # Sum run_counters across all drops
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(lead_rows_total), 0)            AS lead_rows_total,
            COALESCE(SUM(lead_rows_rejected), 0)         AS lead_rows_rejected,
            COALESCE(SUM(duplicate_lead_rows_merged), 0) AS duplicate_lead_rows_merged,
            COALESCE(SUM(field_warnings), 0)             AS field_warnings,
            COALESCE(SUM(events_total), 0)               AS events_total,
            COALESCE(SUM(malformed_events), 0)           AS malformed_events,
            COALESCE(SUM(duplicate_events_dropped), 0)   AS duplicate_events_dropped,
            COALESCE(SUM(unknown_disposition_events), 0) AS unknown_disposition_events
        FROM run_counters
        """
    ).fetchone()

    # Rejections-by-reason: map reason -> count
    reasons = {}
    for r in conn.execute(
        "SELECT reason, COUNT(*) c FROM rejections GROUP BY reason ORDER BY reason"
    ):
        reasons[r["reason"]] = r["c"]

    # Orphan events = current state (final) from events_ledger
    orphan_events = conn.execute(
        "SELECT COUNT(*) FROM events_ledger WHERE is_orphan = 1"
    ).fetchone()[0]

    data = {
        "lead_rows_total": row["lead_rows_total"],
        "lead_rows_rejected": row["lead_rows_rejected"],
        "rejections_by_reason": reasons,
        "duplicate_lead_rows_merged": row["duplicate_lead_rows_merged"],
        "field_warnings": row["field_warnings"],
        "events_total": row["events_total"],
        "malformed_events": row["malformed_events"],
        "duplicate_events_dropped": row["duplicate_events_dropped"],
        "orphan_events": orphan_events,
        "unknown_disposition_events": row["unknown_disposition_events"],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")


def write_all_reports(conn: sqlite3.Connection, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_batch_summary(conn, out_dir)
    write_daily_overview(conn, out_dir)
    write_lead_report(conn, out_dir)
    write_rejections(conn, out_dir)
    write_data_quality(conn, out_dir)