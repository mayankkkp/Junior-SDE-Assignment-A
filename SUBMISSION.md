# SUBMISSION — Assignment A: Interaction Ledger & Reporting Pipeline

## What I built

An idempotent pipeline that ingests two drops of lead CSVs and call event
JSONLs, resolves one effective disposition per call, buckets each lead, and
produces the five report files. SQLite storage, Python 3.15, no external
services. 131 tests.

## How to run

```bash
cd solution
python -m venv .venv
source .venv/bin/activate            # macOS/Linux
# .\.venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -r requirements.txt
pip install -e .

# PII hash salt -- required, no default, must be in env
export PII_SALT="some-random-value"  # or $env:PII_SALT = "..." on Windows

# Generate the synthetic data (once)
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# Ingest both drops
python -m ledger_pipeline.cli ingest data/drop_1
python -m ledger_pipeline.cli ingest data/drop_2

# Report
python -m ledger_pipeline.cli report --out reports/

# Tests
python -m pytest -q
Re-run ingest on the same drop: no-op ("already ingested").
Re-run report: byte-identical output.

Architecture
text
CLI  ──> drop.ingest_drop  ──> leads.ingest_lead_file  (CSV → leads)
                            ──> events.ingest_event_file (JSONL → events_ledger)
                            ──> link.relink_orphans       (link by phone_hash / call_id)
                            ──> derived.rebuild_catalogue (dispositions_catalogue)
                            ──> derived.rebuild_derived   (calls, call_dispositions, lead_state)

CLI  ──> reports.write_all_reports   (5 SQL queries → CSV/JSON)
Import graph is acyclic. cli.py imports everything; every other module
imports only stdlib + its direct dependencies. Each layer is independently
testable.

Data model
Layer	Table	What	Why
raw	ingest_runs	per-drop content hash	idempotency
raw	run_counters	per-drop DQ counters	data_quality.json sums from these
raw	leads	one row per (workflow, phone_hash)	dedupe by canonical phone
raw	rejections / field_warnings	per-row DQ	no PII, no lead record
raw	events_ledger	append-only events	UNIQUE(event_id) for dedupe
derived	calls	one row per linked call with call.initiated	deterministic rebuild
derived	call_dispositions	effective disposition per call	override > set, by occurred_at
derived	lead_state	one row per lead, bucket etc.	report input
All derived tables are rebuilt from raw on every ingest. Deleting the DB
and re-ingesting reconstructs everything — the raw layer is the source of
truth.

Key design decisions
Dedup scoping
Leads: dedupe by (workflow_id, canonical_phone_hash). Merge rule is
last-write-wins only on non-null fields of later rows; batch_id is
permanent from the first file. Defaults (e.g. preferred_language=en) apply
after merging, to fields still null.

Events: dedupe by event_id (UNIQUE constraint). Scope is all drops, so
a duplicate line in drop_2 is dropped even if drop_1 had the same event_id.

Effective disposition
Per spec: latest disposition.override by occurred_at if any, else latest
disposition.set. Ranked in a single window function so ties (same
occurred_at) break by latest insertion id. Overrides never delete the
original event; the ledger stays append-only.

Bucket precedence
yet_to_reach_out → completed → in_progress_telephony_failed →
in_progress_dialed, first match wins. completed means any of the
lead's calls had a terminal effective disposition (not just the latest), so a
lead whose first call renewed and second call was callback_requested is
still completed. in_progress_telephony_failed requires every call to
have a call.telephony_failed event.

PII at rest
customer_name: never stored. Required by schema, validated during
ingestion, then discarded. Reports don't need it.

phone: stored as HMAC-SHA256 of the canonical +91XXXXXXXXXX using a
salt from the PII_SALT env var (never in the DB), plus a masked display
form +91******XXXX. Joins link events to leads via this hash, so PII
never sits at rest in plaintext and is reversible only with the salt.

rejections.csv and rejections table contain no name/phone — only
workflow_id, batch_id, row_number, reason.

Idempotency at three levels
Drop level: _drop_sha256 over the whole directory. Same directory,
same bytes → no-op. Different bytes at the same path → error (not silent
corruption).

Row level: leads UNIQUE (workflow_id, phone_hash); events_ledger
UNIQUE event_id.

Report level: write_all_reports produces byte-identical output on
repeated runs from the same DB (proven by test_report_rebuild_is_byte_identical).

Late-lead rescue
relink_orphans runs after every drop. Events orphaned in drop_1 link
automatically when their lead arrives in drop_2. orphan_events in
data_quality.json reflects the final state, not per-drop counts.

Assumptions (deliberate calls where the spec was ambiguous)
Name not stored at all. The spec says PII "must not be stored in
plaintext"; discarding is stronger than encrypting, and reports never need
the name.

Override may need a call_id that links to an existing event. If the
call_id has no linked event yet (its call.initiated is itself an orphan),
the override stays orphan until the call is linked. This matches the spec's
"override links through its call_id to the lead of that call's other
events."

events_total includes blank lines? No — I interpreted "non-empty line
read" as literally non-empty after .strip(). Blank lines are not counted.

malformed_events includes unparseable timestamps, missing
event_id, and unknown event_type. The spec names malformed as "not
valid JSON, or has no event_id"; I extended to unknown event types because
they cannot be processed correctly and counting them keeps the accounting
identity intact.

unknown_disposition_events are dropped (not inserted into
events_ledger) so they don't pollute the disposition chain — matches
"ignore it."

connectivity_rate formatted as f"{rate:.4f}" on the raw ratio
(e.g. 27/48 → 0.5625). Not multiplied by 100.

Accounting identity (verifiable)
events_total == malformed_events

duplicate_events_dropped

unknown_disposition_events

COUNT(events_ledger)

For the seed-42 small run: 8745 == 8 + 235 + 21 + 8481 = 8745. This identity
is asserted in test_events_accounting_identity.

What I'd do next (in priority order)
Incremental relink + rebuild. relink_orphans and rebuild_derived
are full-scan O(n) per drop. At 100k leads/day, that's minutes per run.
Fix: track dirty_lead_ids from ingest, rebuild only those, and run
relink on the new drop's orphans + any phone_hash that just arrived.

Schema migrations. CREATE TABLE IF NOT EXISTS can't add columns.
Add a schema_version table + migration runner, so production deploys
don't require a DB rebuild.

_drop_sha256 is O(filesize) — hashing the entire drop on every
ingest. For huge drops, hash file metadata + first/last N bytes to get a
cheap pre-check, then full hash only for new drops.

Encrypt customer_name at rest if a future report needs it —
AES-GCM with a KMS-held key, not just discard. Document as a future
requirement, not a current gap.

Sanitize reason strings (currently pass through) — reject if any
contain PII-shaped substrings, so future reason fields can't leak.

Environment tested
Python 3.15.2 on Windows 11 (PowerShell 5.1)

pytest 9.2.0, pytest -q → 131+ tests, all pass

Data: python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

Sample invariants verified:

lead_state count == leads count

sum(buckets) == lead_state count

Events accounting identity (above)

No 10-digit phone strings in any report file

No customer names in any report file