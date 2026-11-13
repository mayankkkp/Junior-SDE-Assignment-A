"""Decimal parsing per Assignment A spec.

Optional currency prefix: ₹, Rs, Rs., INR (case-insensitive, optional space).
Commas are thousands separators. Must be >= 0. Round to 2 places.
"""

import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

_PREFIX = re.compile(r"^(?:₹|Rs\.?|INR)\s*", re.IGNORECASE)
_COMMAS = re.compile(r",")


def parse_decimal(raw: str | None) -> Decimal | None:
    """Return Decimal rounded to 2 places, or None if invalid/negative."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = _PREFIX.sub("", s)
    s = _COMMAS.sub("", s).strip()
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if d < 0:
        return None
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)