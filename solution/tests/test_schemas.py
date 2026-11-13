from pathlib import Path
from ledger_pipeline.schemas import load_catalog

SCHEMAS_DIR = Path(__file__).parent.parent / "assignment-a-data" / "schemas"


def test_loads_both_workflows():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    assert set(schemas.keys()) == {"motor_renewal", "policy_feedback"}


def test_motor_renewal_reference_field():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    assert schemas["motor_renewal"].reference_field().name == "policy_number"


def test_policy_feedback_reference_field():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    assert schemas["policy_feedback"].reference_field().name == "order_id"


def test_motor_renewal_fields_in_schema_order():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    names = [f.name for f in schemas["motor_renewal"].fields]
    assert names == [
        "customer_name", "phone", "policy_number",
        "policy_expiry", "premium_amount", "preferred_language",
    ]


def test_pii_flags_motor_renewal():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    pii = {f.name for f in schemas["motor_renewal"].fields if f.pii}
    assert pii == {"customer_name", "phone"}


def test_required_flags():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    req = {f.name for f in schemas["motor_renewal"].fields if f.required}
    assert req == {"customer_name", "phone", "policy_number"}


def test_preferred_language_default():
    schemas, _ = load_catalog(SCHEMAS_DIR)
    lang = next(f for f in schemas["motor_renewal"].fields
                if f.name == "preferred_language")
    assert lang.default == "en"
    assert "hi" in lang.values and "ta" in lang.values


def test_dispositions_catalog_shape():
    _, dispositions = load_catalog(SCHEMAS_DIR)
    assert set(dispositions.keys()) == {"motor_renewal", "policy_feedback"}
    mr = dispositions["motor_renewal"]
    assert "renewed" in mr and "callback_requested" in mr
    assert mr["renewed"].is_terminal is True
    assert mr["renewed"].is_success is True
    assert mr["callback_requested"].is_terminal is False
    assert mr["callback_requested"].is_success is False
    assert mr["dnd"].is_terminal is True and mr["dnd"].is_success is False


def test_policy_feedback_dispositions():
    _, dispositions = load_catalog(SCHEMAS_DIR)
    pf = dispositions["policy_feedback"]
    assert pf["feedback_captured"].is_success is True
    assert pf["refused"].is_terminal is True
    assert "renewed" not in pf  # not a policy_feedback code


def test_all_disposition_codes_have_valid_bool():
    _, dispositions = load_catalog(SCHEMAS_DIR)
    for wf, codes in dispositions.items():
        for code, disp in codes.items():
            assert isinstance(disp.is_terminal, bool)
            assert isinstance(disp.is_success, bool)