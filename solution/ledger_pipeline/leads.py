"""Lead CSV ingestion: validate, normalise, merge, default."""

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .decimals import parse_decimal
from .dates import parse_date
from .headers import build_header_map
from .phone import mask_phone, parse_phone
from .pii import phone_hash
from .schemas import Field, WorkflowSchema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_row(
    row: list[str],
    mapping: dict[int, str],
    schema: WorkflowSchema,
) -> tuple[dict, list[str], str | None]:
    """
    Returns (normalised, warnings, rejection_reason).
    Rejection order: walk schema.fields in order; first failure wins.
    Phone invalidity is checked in the same pass (even for optional),
    per spec: "the phone is present but invalid -> invalid_phone".
    """
    # 1. Extract raw values by field name
    raw_by_field: dict[str, str] = {}
    for idx, fname in mapping.items():
        cell = row[idx] if idx < len(row) and row[idx] is not None else ""
        raw_by_field[fname] = cell.strip()

    normalised: dict = {}
    warnings: list[str] = []

    # 2. Rejection pass (walk schema order, first failure wins)
    for f in schema.fields:
        raw = raw_by_field.get(f.name, "")
        if raw == "":
            if f.required:
                return {}, [], f"missing_required:{f.name}"
            continue
        if f.type == "phone":
            canonical = parse_phone(raw)
            if canonical is None:
                return {}, [], "invalid_phone"
            normalised[f.name] = canonical
        elif f.type == "string":
            # present and non-empty; kept only for validation. PII discarded later.
            normalised[f.name] = raw
        elif f.type == "date":
            d = parse_date(raw)
            if d is None and f.required:
                return {}, [], f"invalid_{f.name}"
            if d is not None:
                normalised[f.name] = d
        elif f.type == "decimal":
            d = parse_decimal(raw)
            if d is None and f.required:
                return {}, [], f"invalid_{f.name}"
            if d is not None:
                normalised[f.name] = str(d)
        elif f.type == "enum":
            lower = raw.lower()
            if lower in f.values:
                normalised[f.name] = lower
            # if not in values, deferred to warning pass below

    # 3. Warning pass: optional present-but-invalid
    for f in schema.fields:
        if f.name in normalised:
            continue
        raw = raw_by_field.get(f.name, "")
        if raw == "":
            continue
        # Only optional fields that failed type parsing reach here
        if f.type in ("date", "decimal", "enum"):
            warnings.append(f.name)

    # 4. Apply defaults for still-missing fields
    for f in schema.fields:
        if f.name not in normalised and f.default is not None:
            normalised[f.name] = f.default

    return normalised, warnings, None


def ingest_lead_file(
    conn: sqlite3.Connection,
    ingest_run_id: int,
    file_path: Path,
    schema: WorkflowSchema,
) -> None:
    """Ingest one lead CSV. batch_id = file stem, permanent."""
    batch_id = file_path.stem

    with open(file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return
        mapping = build_header_map(header, schema.fields)

        row_number = 0
        for row in reader:
            # skip blank rows (every cell empty/whitespace)
            if all((c or "").strip() == "" for c in row):
                continue
            row_number += 1

            conn.execute(
                "UPDATE run_counters SET lead_rows_total = lead_rows_total + 1 "
                "WHERE ingest_run_id = ?", (ingest_run_id,),
            )

            normalised, warnings, reason = _validate_row(row, mapping, schema)

            if reason is not None:
                conn.execute(
                    "INSERT INTO rejections "
                    "(workflow_id, batch_id, row_number, reason, ingest_run_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (schema.workflow_id, batch_id, row_number, reason, ingest_run_id),
                )
                conn.execute(
                    "UPDATE run_counters "
                    "SET lead_rows_rejected = lead_rows_rejected + 1 "
                    "WHERE ingest_run_id = ?", (ingest_run_id,),
                )
                continue

            if warnings:
                conn.executemany(
                    "INSERT INTO field_warnings "
                    "(workflow_id, batch_id, row_number, field_name, ingest_run_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(schema.workflow_id, batch_id, row_number, w, ingest_run_id)
                     for w in warnings],
                )
                conn.execute(
                    "UPDATE run_counters SET field_warnings = field_warnings + ? "
                    "WHERE ingest_run_id = ?", (len(warnings), ingest_run_id),
                )

            # PII: drop name, keep phone hash + masked
            canonical_phone = normalised.pop("phone")
            normalised.pop("customer_name", None)
            ph = phone_hash(canonical_phone)

            ref_field = schema.reference_field()
            external_ref = normalised.get(ref_field.name, "")

            existing = conn.execute(
                "SELECT id, payload_json FROM leads "
                "WHERE workflow_id = ? AND phone_hash = ?",
                (schema.workflow_id, ph),
            ).fetchone()

            if existing is None:
                conn.execute(
                    "INSERT INTO leads "
                    "(workflow_id, phone_hash, phone_masked, batch_id, "
                    " external_ref, payload_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (schema.workflow_id, ph, mask_phone(canonical_phone),
                     batch_id, external_ref,
                     json.dumps(normalised, ensure_ascii=False),
                     _now(), _now()),
                )
            else:
                conn.execute(
                    "UPDATE run_counters "
                    "SET duplicate_lead_rows_merged = duplicate_lead_rows_merged + 1 "
                    "WHERE ingest_run_id = ?", (ingest_run_id,),
                )
                merged = json.loads(existing["payload_json"])
                for k, v in normalised.items():
                    if v is not None and v != "":
                        merged[k] = v
                conn.execute(
                    "UPDATE leads SET payload_json = ?, external_ref = ?, updated_at = ? "
                    "WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), external_ref,
                     _now(), existing["id"]),
                )

    conn.commit()