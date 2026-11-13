"""Header normalization per Assignment A spec.

Rule: lower-case, trim, collapse every run of '_' or whitespace to a single
space. Match against field name or aliases (normalized the same way).
"""

import re

_WS_UND = re.compile(r"[_\s]+")


def normalize_header(raw: str | None) -> str:
    if raw is None:
        return ""
    return _WS_UND.sub(" ", str(raw).strip().lower()).strip()


def build_header_map(fieldnames: list[str], schema_fields) -> dict[int, str]:
    """
    Map column index -> canonical field name.
    Columns matching nothing are excluded (per spec: ignore them).
    """
    lookup: dict[str, str] = {}
    for f in schema_fields:
        lookup[normalize_header(f.name)] = f.name
        for alias in f.aliases:
            lookup[normalize_header(alias)] = f.name
    out: dict[int, str] = {}
    for idx, raw in enumerate(fieldnames):
        key = normalize_header(raw)
        if key in lookup:
            out[idx] = lookup[key]
    return out