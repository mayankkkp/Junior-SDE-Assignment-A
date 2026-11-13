"""Derived facts:
  calls                -- one row per (lead_id, call_id) with initiated time
  call_dispositions    -- effective disposition per call (override > set)
  lead_state           -- one row per lead: bucket, current_disposition, etc.

All materialised tables. Rebuild via `rebuild_derived(conn)`. Deterministic,
idempotent (DELETE + INSERT every call). Report layer reads from these.
"""

import sqlite3


def _rebuild_calls(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM calls")
    conn.execute(
        """
        INSERT INTO calls (lead_id, workflow_id, call_id, initiated_at, ist_date)
        SELECT
            e.lead_id,
            e.workflow_id,
            e.call_id,
            MIN(e.occurred_at_ist),
            MIN(e.ist_date)
        FROM events_ledger e
        WHERE e.event_type = 'call.initiated'
          AND e.lead_id IS NOT NULL
          AND e.call_id IS NOT NULL
        GROUP BY e.lead_id, e.workflow_id, e.call_id
        """
    )


def _rebuild_call_dispositions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM call_dispositions")
    conn.execute(
        """
        INSERT INTO call_dispositions
            (call_id, workflow_id, effective_code, source_event_id, effective_at)
        WITH ranked AS (
            SELECT
                call_id,
                workflow_id,
                json_extract(payload_json, '$.disposition_code') AS code,
                occurred_at,
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY call_id
                    ORDER BY
                        CASE event_type
                             WHEN 'disposition.override' THEN 0
                             WHEN 'disposition.set'      THEN 1
                        END ASC,
                        occurred_at DESC,
                        id DESC
                ) AS rn
            FROM events_ledger
            WHERE event_type IN ('disposition.override', 'disposition.set')
              AND lead_id IS NOT NULL
              AND call_id IS NOT NULL
        )
        SELECT call_id, workflow_id, code, id, occurred_at
        FROM ranked
        WHERE rn = 1
        """
    )


def _rebuild_lead_state(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM lead_state")
    conn.execute(
        """
        INSERT INTO lead_state
            (lead_id, workflow_id, total_calls, connected_calls,
             telephony_failed_calls, last_call_at, last_call_ist_date,
             current_disposition, ever_success, bucket)
        WITH per_lead_calls AS (
            SELECT
                c.lead_id, c.workflow_id, c.call_id, c.initiated_at, c.ist_date,
                CASE WHEN EXISTS (
                    SELECT 1 FROM events_ledger e
                    WHERE e.call_id = c.call_id
                      AND e.event_type = 'call.connected'
                      AND e.lead_id IS NOT NULL
                ) THEN 1 ELSE 0 END AS is_connected,
                CASE WHEN EXISTS (
                    SELECT 1 FROM events_ledger e
                    WHERE e.call_id = c.call_id
                      AND e.event_type = 'call.telephony_failed'
                      AND e.lead_id IS NOT NULL
                ) THEN 1 ELSE 0 END AS is_telephony_failed
            FROM calls c
        ),
        per_lead_agg AS (
            SELECT
                lead_id, workflow_id,
                COUNT(*) AS total_calls,
                SUM(is_connected) AS connected_calls,
                SUM(is_telephony_failed) AS telephony_failed_calls,
                MAX(initiated_at) AS last_call_at,
                MAX(ist_date) AS last_call_ist_date
            FROM per_lead_calls
            GROUP BY lead_id, workflow_id
        ),
        latest_call_per_lead AS (
            SELECT lead_id, call_id, initiated_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY lead_id ORDER BY initiated_at DESC, call_id DESC
                   ) AS rn
            FROM calls
        ),
        current_disp AS (
            SELECT lcl.lead_id, cd.effective_code
            FROM latest_call_per_lead lcl
            JOIN call_dispositions cd ON cd.call_id = lcl.call_id
            WHERE lcl.rn = 1
        ),
        ever_success AS (
            SELECT c.lead_id, 1 AS s
            FROM calls c
            JOIN call_dispositions cd ON cd.call_id = c.call_id
            JOIN dispositions_catalogue d
              ON d.workflow_id = cd.workflow_id
             AND d.code = cd.effective_code
             AND d.is_success = 1
            GROUP BY c.lead_id
        ),
        terminal_per_lead AS (
            SELECT DISTINCT c.lead_id
            FROM calls c
            JOIN call_dispositions cd ON cd.call_id = c.call_id
            JOIN dispositions_catalogue d
              ON d.workflow_id = cd.workflow_id
             AND d.code = cd.effective_code
             AND d.is_terminal = 1
        )
        SELECT
            l.id,
            l.workflow_id,
            COALESCE(agg.total_calls, 0),
            COALESCE(agg.connected_calls, 0),
            COALESCE(agg.telephony_failed_calls, 0),
            agg.last_call_at,
            agg.last_call_ist_date,
            cd.effective_code,
            COALESCE(es.s, 0),
            CASE
                WHEN agg.total_calls IS NULL OR agg.total_calls = 0 THEN 'yet_to_reach_out'
                WHEN tp.lead_id IS NOT NULL THEN 'completed'
                WHEN agg.telephony_failed_calls = agg.total_calls THEN 'in_progress_telephony_failed'
                ELSE 'in_progress_dialed'
            END
        FROM leads l
        LEFT JOIN per_lead_agg agg   ON agg.lead_id = l.id
        LEFT JOIN current_disp cd    ON cd.lead_id = l.id
        LEFT JOIN ever_success es    ON es.lead_id = l.id
        LEFT JOIN terminal_per_lead tp ON tp.lead_id = l.id
        """
    )


def rebuild_derived(conn: sqlite3.Connection) -> None:
    """Rebuild calls, call_dispositions, lead_state from events_ledger."""
    _rebuild_calls(conn)
    _rebuild_call_dispositions(conn)
    _rebuild_lead_state(conn)
    conn.commit()


def rebuild_catalogue(conn: sqlite3.Connection, dispositions: dict) -> None:
    """Upsert dispositions_catalogue from the loaded Python dict."""
    rows = []
    for wf, codes in dispositions.items():
        for code, disp in codes.items():
            rows.append((wf, code, 1 if disp.is_terminal else 0,
                         1 if disp.is_success else 0))
    conn.executemany(
        "INSERT OR REPLACE INTO dispositions_catalogue "
        "(workflow_id, code, is_terminal, is_success) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()