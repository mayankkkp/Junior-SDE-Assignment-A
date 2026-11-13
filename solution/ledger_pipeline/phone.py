"""Indian mobile normalization per Assignment A spec.

Rules:
- Strip every non-digit.
- If 12 digits and starts with '91', drop the '91'.
- If 11 digits and starts with '0', drop the '0'.
- Valid iff exactly 10 digits and first digit in {6,7,8,9}.
- Canonical form: '+91XXXXXXXXXX'.
"""

import re

_NON_DIGIT = re.compile(r"\D")


def parse_phone(raw: str | None) -> str | None:
    """Return canonical '+91XXXXXXXXXX', or None if invalid."""
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


def mask_phone(canonical: str) -> str:
    """'+91XXXXXXXXXX' -> '+91******XXXX'. Assumes canonical input."""
    assert canonical.startswith("+91") and len(canonical) == 13, canonical
    return "+91******" + canonical[-4:]