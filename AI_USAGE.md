# AI Usage

## Tools used
- Claude (chat) — scaffolding, Windows-specific debugging, test generation.

## What was AI-assisted
- Initial SQLite schema sketch and report format code.
- Some test file skeletons (fixtures, parametrized cases).
- Debugging support on Windows-specific failures (BOM in JSON/CSV, path
  resolution, `CREATE TABLE IF NOT EXISTS` staleness, circular imports).

## What was not AI-assisted (or was AI-assisted and reviewed line by line)
- Phone normalization rules — reviewed against spec examples and
  hand-verified for the 6 formats the generator emits.
- Date parsing — implemented day-first logic, verified against
  `31/02/2026` (invalid), `15-OCT-2026` (case-insensitive), etc.
- Decimal parsing — verified `₹`/`Rs.`/`INR` prefix handling and ROUND_HALF_UP.
- Effective disposition — reviewed the "latest override by occurred_at,
  else latest set" rule against the spec and tested with conflict cases.
- Bucket precedence — chose ordering (yet_to_reach_out → completed →
  in_progress_telephony_failed → in_progress_dialed), tested each transition.
- PII design — chose HMAC-SHA256 with env-held salt and "discard name, keep
  masked phone". Defended in SUBMISSION.md.
- Idempotency at drop/row/report level — designed and tested independently.

## Can I defend every line live?
Yes. Every design decision is documented in SUBMISSION.md and has an
associated test. If asked to add a metric (e.g. average handle time per
campaign), it's one SQL query on `calls`/`call_dispositions` — no schema
change.