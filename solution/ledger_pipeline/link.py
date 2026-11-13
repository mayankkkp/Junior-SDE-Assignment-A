"""Event-to-lead linking.

Rules (per spec):
- call.* and disposition.set link via (workflow_id, phone_hash).
- disposition.override links via call_id → the lead of that call's other events.
  (Overrides have no phone field.)

This pass is idempotent and safe to run after every ingest. It:
  1. Links events whose phone_hash matches a lead.
  2. Links overrides whose call_id already has a linked event.
  3. Clears is_orphan for linked rows.
  4. Re-scans in a small fixpoint loop, because a fresh lead can unlock a
     chain: lead arrives -> its non-override events link -> those unlock
     their call_id for overrides.

An event with no phone_hash and no call_id match stays orphan. That is the
correct behaviour per spec.
"""

import sqlite3


def relink_orphans(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Run the linking pass until no new links appear.
    Returns (newly_linked, still_orphan).
    """
    total_new = 0
    # Cap iterations to avoid pathological loops; 4 is more than enough.
    for _ in range(4):
        # 1. Link by (workflow_id, phone_hash)
        cur = conn.execute(
            """
            UPDATE events_ledger
               SET lead_id = (
                    SELECT l.id FROM leads l
                    WHERE l.workflow_id = events_ledger.workflow_id
                      AND l.phone_hash = events_ledger.phone_hash
                   ),
                   is_orphan = 0
             WHERE is_orphan = 1
               AND phone_hash IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM leads l
                    WHERE l.workflow_id = events_ledger.workflow_id
                      AND l.phone_hash = events_ledger.phone_hash
                   )
            """
        )
        step_new = cur.rowcount

        # 2. Link overrides by call_id → any linked event on the same call
        cur = conn.execute(
            """
            UPDATE events_ledger
               SET lead_id = (
                    SELECT e2.lead_id FROM events_ledger e2
                    WHERE e2.call_id = events_ledger.call_id
                      AND e2.lead_id IS NOT NULL
                    LIMIT 1
                   ),
                   is_orphan = 0
             WHERE is_orphan = 1
               AND event_type = 'disposition.override'
               AND call_id IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM events_ledger e2
                    WHERE e2.call_id = events_ledger.call_id
                      AND e2.lead_id IS NOT NULL
                   )
            """
        )
        step_new += cur.rowcount

        if step_new == 0:
            break
        total_new += step_new

    conn.commit()
    still_orphan = conn.execute(
        "SELECT COUNT(*) FROM events_ledger WHERE is_orphan = 1"
    ).fetchone()[0]
    return total_new, still_orphan