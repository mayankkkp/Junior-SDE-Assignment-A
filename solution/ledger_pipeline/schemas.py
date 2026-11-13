"""Loads workflow schemas and the disposition catalogue from disk.

Design note: schema files are opened with encoding='utf-8-sig' so a UTF-8 BOM
(which Windows PowerShell's `Set-Content -Encoding utf8` writes) does not break
JSON parsing. Tested against the shipped assignment-a-data files.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Field:
    name: str
    type: str                       # 'string' | 'phone' | 'date' | 'decimal' | 'enum'
    required: bool
    pii: bool = False
    reference: bool = False
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()    # enum allowed values
    default: str | None = None


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    fields: tuple[Field, ...]

    def reference_field(self) -> Field:
        for f in self.fields:
            if f.reference:
                return f
        raise ValueError(f"{self.workflow_id}: no field marked reference=true")


@dataclass(frozen=True)
class Disposition:
    code: str
    is_terminal: bool
    is_success: bool


def load_schema(path: Path) -> WorkflowSchema:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    fields = []
    for f in raw["fields"]:
        fields.append(Field(
            name=f["name"],
            type=f["type"],
            required=bool(f.get("required", False)),
            pii=bool(f.get("pii", False)),
            reference=bool(f.get("reference", False)),
            aliases=tuple(f.get("aliases", ())),
            values=tuple(f.get("values", ())),
            default=f.get("default"),
        ))
    return WorkflowSchema(raw["workflow_id"], tuple(fields))


def load_dispositions(path: Path) -> dict[str, dict[str, Disposition]]:
    """Returns {workflow_id: {code: Disposition}}."""
    out: dict[str, dict[str, Disposition]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            wf = row["workflow_id"].strip()
            code = row["code"].strip()
            out.setdefault(wf, {})[code] = Disposition(
                code=code,
                is_terminal=row["is_terminal"].strip().lower() == "true",
                is_success=row["is_success"].strip().lower() == "true",
            )
    return out


def load_catalog(schemas_dir: Path) -> tuple[dict[str, WorkflowSchema],
                                             dict[str, dict[str, Disposition]]]:
    """
    Load all *.json workflow schemas plus dispositions.csv from schemas_dir.
    Returns (schemas_by_workflow_id, dispositions_by_workflow_id).
    """
    schemas_dir = Path(schemas_dir)
    schemas: dict[str, WorkflowSchema] = {}
    for p in sorted(schemas_dir.glob("*.json")):
        s = load_schema(p)
        schemas[s.workflow_id] = s
    dispositions = load_dispositions(schemas_dir / "dispositions.csv")
    return schemas, dispositions