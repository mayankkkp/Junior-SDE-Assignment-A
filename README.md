# Assignment A — Interaction Ledger & Reporting Pipeline

My submission for **Agentic Universe's Junior SDE take-home, Assignment A**.

Takes two drops of lead CSVs and call-event JSONLs, validates and normalises them,
builds an append-only interaction ledger, resolves an effective disposition per
call, buckets each lead, and produces the five report files exactly as the spec
requires.

- **Language:** Python 3.11+ (tested on 3.15)
- **Storage:** SQLite (no external services)
- **Tests:** `pytest -q` → 131 passing
- **Dependencies:** `pytest` only (everything else is stdlib)

---

## Repo layout
.
├── README.md ← you are here
├── SUBMISSION.md ← design write-up: decisions, trade-offs, next steps
├── AI_USAGE.md ← honest account of AI-assisted work
├── .gitignore ← excludes generated data, DB, venv
└── solution/ ← the pipeline
├── README.md ← setup and run commands
├── requirements.txt
├── setup.py
├── pytest.ini
├── ledger_pipeline/ ← the package
│ ├── cli.py ← entrypoint: ingest / report
│ ├── db.py ← schema + connection
│ ├── schemas.py ← load workflow schemas + dispositions
│ ├── headers.py ← header normalisation + alias matching
│ ├── phone.py ← Indian mobile normaliser
│ ├── dates.py ← 4-format date parser, IST timestamps
│ ├── decimals.py ← ₹ / Rs / INR parser
│ ├── pii.py ← HMAC-SHA256 of canonical phone
│ ├── leads.py ← lead CSV ingestion + merge
│ ├── events.py ← event JSONL ingestion + classification
│ ├── link.py ← event-to-lead linking (orphan rescue)
│ ├── derived.py ← calls, call_dispositions, lead_state
│ ├── drop.py ← one-drop orchestration
│ └── reports.py ← the five report writers
├── tests/ ← 131 tests across 8 files
├── assignment-a-data/ ← provided schemas + data generator
└── GLOSSARY.md ← provided terminology

text

---

## Quick start (2 minutes)

```bash
# from the repository root
cd solution

# create an isolated environment (Python 3.11+)
python -m venv .venv
source .venv/bin/activate            # macOS / Linux
# .\.venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
pip install -e .

# PII salt (required; no default)
export PII_SALT="any-random-value"       # bash
# $env:PII_SALT = "any-random-value"     # PowerShell

# generate the synthetic data
python assignment-a-data/tools/generate_data.py \
    --seed 42 --scale small --out data

# ingest both drops
python -m ledger_pipeline.cli ingest data/drop_1
python -m ledger_pipeline.cli ingest data/drop_2

# produce the five report files
python -m ledger_pipeline.cli report --out reports

# verify
python -m pytest -q
Full details — including the idempotent re-run behaviour, expected
counters, and the reset command — are in solution/README.md.

What the pipeline does (one paragraph)
Reads lead CSVs against per-workflow schemas (motor_renewal, policy_feedback),
normalises headers via aliases, validates phone/date/decimal/enum values, rejects
bad rows with a single reason code, and merges duplicate leads by
(workflow_id, canonical phone). Reads call-event JSONLs, classifies each line
as malformed / duplicate / orphan / unknown-disposition / valid, and appends to
an append-only ledger. Links events to leads after every drop, so an event
orphaned in drop_1 links automatically when its lead arrives in drop_2. Derives
one effective disposition per call (latest override, else latest set), one bucket
per lead (yet_to_reach_out → completed → in_progress_telephony_failed →
in_progress_dialed), and the five report files. PII is never stored in
plaintext: the name is discarded, and the phone is stored as
HMAC-SHA256(canonical +91XXXXXXXXXX) plus a masked display +91******XXXX.

Design highlights
Concern	Approach
Idempotency	Drop-level via SHA-256 of the drop tree; row-level via UNIQUE on leads (workflow_id, phone_hash) and events_ledger.event_id; report-level via deterministic rebuild (byte-identical CSV/JSON on re-run, asserted by test)
PII at rest	Name discarded; phone as HMAC-SHA256 with salt from env (PII_SALT, never in DB); masked display only
Late-lead rescue	relink_orphans runs after every drop; orphan_events in data_quality.json reflects final state
Effective disposition	Latest disposition.override by occurred_at, else latest disposition.set — one window-function query, deterministic tiebreak by id
Bucket precedence	yet_to_reach_out → completed → in_progress_telephony_failed → in_progress_dialed, first match
Report aggregation	All five files computed entirely in SQL (CTEs + window functions) — no Python aggregation
Accounting identity	events_total = malformed + duplicate + unknown_disposition + COUNT(events_ledger) — asserted by test
See SUBMISSION.md for the full design write-up,
deliberate decisions on ambiguous rules, known limitations, and what
I'd do next in production.

Requirements
Python 3.11+ (declared in setup.py as python_requires=">=3.11")

No external services. SQLite ships with Python.

On Windows, use PowerShell. The pipeline handles UTF-8 with or without
BOM in all CSV/JSON inputs.

Tests
bash
cd solution
python -m pytest -q
Expected: 131 passed across 8 test files covering:

test_schemas.py — schema loading

test_normalizers.py — phone/date/decimal/header rules (40+ cases)

test_leads.py — validation order, rejection reasons, merge, defaults

test_events.py — malformed/duplicate/unknown/blank classification

test_link.py — phone/call_id linking, orphan rescue

test_derived.py — effective disposition, bucket precedence, override-wins

test_reports.py — exact CSV headers, 4-decimal rates, JSON keys, boolean format

test_integration.py — drop-order independence, byte-identical re-report, PII non-leakage, events accounting identity

Environment tested
Python 3.15.2 on Windows 11 (PowerShell 5.1)

pytest 9.2.0

Generated data: python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

License / data
Synthetic data only. No real customer data. The assignment-a-data/ folder
(schemas, generator, glossary) is provided by Agentic Universe and reproduced
unchanged. solution/ is my work.

text

**Save it.** Then verify the file:

```powershell
cd C:\Users\mayan\OneDrive\Desktop\Task
Get-Content README.md -TotalCount 5
Should print the first five lines of the Markdown.

2. Create C:\Users\mayan\OneDrive\Desktop\Task\solution\README.md
This one is more detailed — it's the "run it from clean clone" reference.

powershell
cd C:\Users\mayan\OneDrive\Desktop\Task\solution
Paste this content:

markdown
# ledger_pipeline

The Assignment A interaction-ledger pipeline. See the [repo
README](../README.md) for the overview and [SUBMISSION.md](../SUBMISSION.md)
for the design write-up.

This file is the **operational reference**: exact commands that work from
a clean clone, expected outputs, and how to reset.

---

## Setup (from a clean clone)

```bash
cd solution
python -m venv .venv
source .venv/bin/activate            # macOS / Linux
# .\.venv\Scripts\Activate.ps1       # Windows PowerShell

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
Verify installation:

bash
python -c "import ledger_pipeline; print('OK')"
Required environment variable
PII_SALT must be set before running ingest. It is used as the HMAC
key for phone hashing. The salt is never stored in the DB, never committed,
and never emitted in any report.

bash
# bash / zsh
export PII_SALT="$(openssl rand -hex 32)"

# PowerShell
$env:PII_SALT = "any-random-value-change-me"
If PII_SALT is unset when ingest runs, you'll get:

text
RuntimeError: PII_SALT environment variable must be set.
That's intentional — a missing salt would mean phone hashes are unkeyed, which
is a PII weakness. Fail-closed is the right behaviour.

Generate the synthetic data
bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
Expected:

text
Generated seed=42 scale=small into data/
  drop_1: 4247 event lines
  drop_2: 4498 event lines
For the larger benchmark (~140k leads, ~600k events):

bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale large --out data_large
Ingest
bash
python -m ledger_pipeline.cli ingest data/drop_1
python -m ledger_pipeline.cli ingest data/drop_2
Expected — drop_1:

text
Ingested drop: data\drop_1
  run_id=1
  lead_rows_total = 1515
  lead_rows_rejected = 71
  duplicate_lead_rows_merged = 29
  field_warnings = 83
  events_total = 4247
  malformed_events = 4
  duplicate_events_dropped = 82
  unknown_disposition_events = 11
Expected — drop_2:

text
Ingested drop: data\drop_2
  run_id=2
  lead_rows_total = 542
  lead_rows_rejected = 37
  duplicate_lead_rows_merged = 28
  field_warnings = 39
  events_total = 4498
  malformed_events = 4
  duplicate_events_dropped = 153
  unknown_disposition_events = 10
Exact numbers will differ if you use a different --seed. The important
point is that both drops run without error and produce non-zero counters.

Idempotency check (mandatory)
Re-run the same drop — this must be a no-op:

bash
python -m ledger_pipeline.cli ingest data/drop_1
Expected:

text
Drop already ingested (idempotent no-op): data\drop_1
This is the drop-level idempotency proof. The DB stores the SHA-256 of
the whole drop directory; a re-ingest with the same content short-circuits
before touching any table.

To force a fresh ingest of a drop whose contents changed (or to reset the
whole DB), see Reset below.

Report
bash
python -m ledger_pipeline.cli report --out reports
Expected:

text
Reports written to: C:\...\solution\reports
  batch_summary.csv           358 bytes
  daily_overview.csv         1452 bytes
  lead_report.csv          197617 bytes
  rejections.csv             5610 bytes
  data_quality.json           494 bytes
The five files match the output contract in the assignment spec exactly:

batch_summary.csv — one row per (workflow_id, batch_id), four bucket counts per row

daily_overview.csv — one row per (workflow_id, IST date with ≥1 call), connectivity_rate at 4 decimals

lead_report.csv — one row per lead, sorted by (workflow_id, external_ref)

rejections.csv — one row per rejected lead row, no PII, sorted by (workflow_id, batch_id, row_number)

data_quality.json — 10 keys, all counters summed across drops

All CSV files use \n line endings, no index column, exact header names.

Re-run report — must be byte-identical:

bash
python -m ledger_pipeline.cli report --out reports2
diff reports/batch_summary.csv reports2/batch_summary.csv     # no output
# Windows: Compare-Object (Get-Content reports/batch_summary.csv) (Get-Content reports2/batch_summary.csv)
Tests
bash
python -m pytest -q
Expected: 131 passed. Two of the tests (test_drop_order_independence
and test_reingest_same_drop_is_noop) re-ingest both drops end-to-end,
so expect 10-30 seconds total runtime.

To skip the slow integration tests:

bash
python -m pytest -q -k "not integration"
Reset
The pipeline is fully derivable from raw files + leads + events.
Delete the DB and re-ingest to rebuild everything:

bash
rm pipeline.db pipeline.db-wal pipeline.db-shm
# Windows: Remove-Item pipeline.db, pipeline.db-wal, pipeline.db-shm -ErrorAction SilentlyContinue
This is safe: ingest reconstructs all derived tables (calls,
call_dispositions, lead_state) from events_ledger. There's no
persistent state that the DB loses on delete.

Schema changes also require a DB delete — CREATE TABLE IF NOT EXISTS
won't migrate existing tables. See "Schema changes" below.

Schema changes
The take-home uses CREATE TABLE IF NOT EXISTS for idempotent setup,
which means adding a column to an existing table is a no-op. If you
change db.py's SCHEMA string, you must:

bash
rm pipeline.db
python -m ledger_pipeline.cli ingest data/drop_1
python -m ledger_pipeline.cli ingest data/drop_2
In production this would be a schema_version table + migration runner
(Alembic or hand-rolled). Documented in SUBMISSION.md as a next step.

Inspecting the DB (for debugging / interview demos)
bash
# quick counts
python -c "
import sqlite3
c = sqlite3.connect('pipeline.db')
for t in ['leads', 'lead_state', 'calls', 'call_dispositions',
          'events_ledger', 'rejections']:
    print(f'{t:<22} {c.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]}')
c.close()
"
Invariants to check:

lead_state == leads (one row per lead, always)

call_dispositions ≤ calls (a call may have no disposition)

sum(buckets in lead_state) == lead_state (every lead in exactly one bucket)

events_total == malformed + duplicate_events_dropped + unknown_disposition + COUNT(events_ledger)

All four are asserted in tests/test_integration.py.

Common Windows pitfalls (and how this repo handles them)
UTF-8 BOM in CSVs written by PowerShell — every file reader uses
encoding="utf-8-sig", which strips the BOM if present. Tested against
Set-Content -Encoding utf8 output.

/tmp paths — use relative paths (., pipeline.db) or
$env:TEMP. The repo uses relative paths everywhere.

git LF → CRLF warnings — harmless. Committed files use LF; Git
rewrites on checkout. Add a .gitattributes with * text=auto eol=lf
if you want to silence the warnings.

OneDrive-synced folders — SQLite can occasionally see database is locked if OneDrive is actively uploading the DB file. Running from a
non-OneDrive path avoids this; the DB is disposable so deleting and
re-ingesting is always safe.

Files and what they do
File	Role
cli.py	Entrypoint: ingest and report subcommands
db.py	SQLite schema (7 raw + 4 derived + 1 catalogue) + connection
schemas.py	Loads assignment-a-data/schemas/*.json and dispositions.csv
headers.py	Header normalisation + alias→field mapping
phone.py	Indian mobile normaliser (+91XXXXXXXXXX canonical form)
dates.py	Lead date parser (4 formats), event timestamp parser (IST), ist_date_str
decimals.py	Currency decimal parser (₹ / Rs / Rs. / INR)
pii.py	HMAC-SHA256 of canonical phone using PII_SALT
leads.py	Lead CSV ingestion: validation, rejection, merge, defaults, batch_id
events.py	Event JSONL ingestion: malformed / duplicate / unknown / valid
link.py	Event-to-lead linking by phone_hash, override linking by call_id
derived.py	calls, call_dispositions, lead_state (all rebuilt from ledger)
drop.py	One-drop orchestration (idempotent, calls link + rebuild at the end)
reports.py	The five report writers (pure SQL → CSV/JSON)
