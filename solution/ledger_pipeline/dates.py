"""Date and timestamp parsing per Assignment A spec.

Dates: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY (month case-insensitive,
day-first). Impossible dates (31/02) invalid. Return ISO 'YYYY-MM-DD' or None.

Timestamps: 'Z', '+05:30', naive (interpreted as IST). Return IST-aware
datetime or None. All report dates are IST dates.
"""

import re
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MON_DATE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str | None) -> str | None:
    """Parse a lead-file date. Return ISO 'YYYY-MM-DD' or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    m = _MON_DATE.match(s)
    if m:
        day = int(m.group(1))
        mon = m.group(2).lower()
        year = int(m.group(3))
        if mon in _MONTHS:
            try:
                return datetime(year, _MONTHS[mon], day).date().isoformat()
            except ValueError:
                return None
    return None


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse an event timestamp into an IST-aware datetime, or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return dt.astimezone(IST)
        if "T" in s and ("+" in s or s.endswith("-00:00")):
            return datetime.fromisoformat(s).astimezone(IST)
        if "T" in s:
            # naive ISO
            return datetime.fromisoformat(s).replace(tzinfo=IST)
        # naive space-separated
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def ist_date_str(dt: datetime) -> str:
    """'YYYY-MM-DD' in IST for an already-IST-aware datetime."""
    return dt.astimezone(IST).date().isoformat()