from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_pipeline.phone import parse_phone, mask_phone
from ledger_pipeline.dates import parse_date, parse_timestamp, ist_date_str, IST
from ledger_pipeline.decimals import parse_decimal
from ledger_pipeline.headers import normalize_header, build_header_map
from ledger_pipeline.schemas import load_catalog

SCHEMAS_DIR = Path(__file__).parent.parent / "assignment-a-data" / "schemas"


# ---------- phone ----------
@pytest.mark.parametrize("raw,expected", [
    ("9876543210", "+919876543210"),
    ("+919876543210", "+919876543210"),
    ("+91 98765 43210", "+919876543210"),
    ("09876543210", "+919876543210"),
    ("91-98765-43210", "+919876543210"),
    ("(+91) 9876543210", "+919876543210"),
    ("  98765 43210  ", "+919876543210"),
    ("+91 (98765) 43210", "+919876543210"),
    ("+91-098765-43210", None),   # 12 digits after strip? 91 098765 43210 -> 12, starts with 91 -> drop -> 0987654321 (10 digits, starts 0, invalid)
    ("6789012345", "+916789012345"),
    ("7890123456", "+917890123456"),
])
def test_phone_accepts(raw, expected):
    assert parse_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "N/A", "12345",
    "5123456789",        # starts with 5
    "022-2345-6789",     # landline
    "+1 415 555 0134",   # US
    "987654321",         # 9 digits
    "98765432101",       # 11 digits, doesn't start 0
])
def test_phone_rejects(raw):
    assert parse_phone(raw) is None


def test_phone_mask():
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("+916789012345") == "+91******2345"


# ---------- date ----------
@pytest.mark.parametrize("raw,expected", [
    ("2026-10-15", "2026-10-15"),
    ("15/10/2026", "2026-10-15"),
    ("15-10-2026", "2026-10-15"),
    ("15-Oct-2026", "2026-10-15"),
    ("15-OCT-2026", "2026-10-15"),
    ("01-Dec-2026", "2026-12-01"),
    ("01-dec-2026", "2026-12-01"),
    ("  2026-10-15  ", "2026-10-15"),
])
def test_date_accepts(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ",
    "31/02/2026", "2026-13-01", "2026-02-30",
    "soon", "TBD", "2026/10/15",
])
def test_date_rejects(raw):
    assert parse_date(raw) is None


# ---------- decimal ----------
@pytest.mark.parametrize("raw,expected", [
    ("1200", Decimal("1200.00")),
    ("1200.50", Decimal("1200.50")),
    ("1200.505", Decimal("1200.51")),   # ROUND_HALF_UP
    ("₹12,345", Decimal("12345.00")),
    ("Rs. 500", Decimal("500.00")),
    ("Rs 500", Decimal("500.00")),
    ("INR 500.5", Decimal("500.50")),
    ("₹ 1,00,000.99", Decimal("100000.99")),
    ("rs.1000", Decimal("1000.00")),
    ("inr500", Decimal("500.00")),
    ("  1234  ", Decimal("1234.00")),
    ("0", Decimal("0.00")),
])
def test_decimal_accepts(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "-100", "-0.01",
    "N/A", "twelve thousand", "abc", "1.2.3",
])
def test_decimal_rejects(raw):
    assert parse_decimal(raw) is None


# ---------- header normalization ----------
@pytest.mark.parametrize("raw,expected", [
    ("customer_name", "customer name"),
    ("  Customer_Name  ", "customer name"),
    ("Customer Name", "customer name"),
    ("Policy__No", "policy no"),
    ("Policy  No", "policy no"),
    ("_phone_", "phone"),                # leading/trailing underscores trimmed
    ("MOBILE_NO", "mobile no"),
    ("", ""),
    (None, ""),
])

def test_normalize_header(raw: str | None, expected: str):
    assert normalize_header(raw) == expected


def test_header_map_matches_canonical_headers():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    headers = ["customer_name", "phone", "policy_number",
               "policy_expiry", "premium_amount", "preferred_language"]
    mapping = build_header_map(headers, schemas["motor_renewal"].fields)
    assert mapping == {
        0: "customer_name", 1: "phone", 2: "policy_number",
        3: "policy_expiry", 4: "premium_amount", 5: "preferred_language",
    }


def test_header_map_matches_aliases():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    # batch_003 uses alias headers
    headers = ["Insured Name", "Mobile No", "Policy No",
               "Expiry Date", "Premium", "Language", "agent_notes"]
    mapping = build_header_map(headers, schemas["motor_renewal"].fields)
    assert mapping == {
        0: "customer_name", 1: "phone", 2: "policy_number",
        3: "policy_expiry", 4: "premium_amount", 5: "preferred_language",
    }
    # agent_notes is not a known field -> excluded


def test_header_map_policy_feedback_aliases():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    headers = ["Order ID", "Name", "Contact", "Product",
               "Order Date", "Language"]
    mapping = build_header_map(headers, schemas["policy_feedback"].fields)
    assert mapping == {
        0: "order_id", 1: "customer_name", 2: "phone",
        3: "product", 4: "order_date", 5: "preferred_language",
    }


# ---------- timestamp ----------
def test_ts_utc_to_ist():
    dt = parse_timestamp("2026-10-05T13:20:21Z")
    assert dt.utcoffset() == timedelta(hours=5, minutes=30)
    assert (dt.hour, dt.minute, dt.second) == (18, 50, 21)


def test_ts_offset_preserved():
    dt = parse_timestamp("2026-10-05T18:50:21+05:30")
    assert dt.utcoffset() == timedelta(hours=5, minutes=30)
    assert (dt.hour, dt.minute, dt.second) == (18, 50, 21)


def test_ts_naive_is_ist():
    dt = parse_timestamp("2026-10-05 18:50:21")
    assert dt.tzinfo is IST
    assert (dt.hour, dt.minute, dt.second) == (18, 50, 21)


def test_ts_invalid():
    assert parse_timestamp("garbage") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None


def test_ist_date_str():
    dt = parse_timestamp("2026-10-05T22:30:00Z")  # 22:30 UTC = 04:00 IST next day
    assert ist_date_str(dt) == "2026-10-06"