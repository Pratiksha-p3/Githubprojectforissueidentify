from src.core.remediation_guide import REMEDIATION_GUIDE, get_remediation


def test_every_entry_has_non_empty_guidance():
    assert len(REMEDIATION_GUIDE) > 40
    for name, guidance in REMEDIATION_GUIDE.items():
        assert guidance.strip(), f"{name} has empty guidance"


def test_lookup_is_exact():
    assert get_remediation("KeyError") == REMEDIATION_GUIDE["KeyError"]


def test_lookup_is_case_insensitive():
    assert get_remediation("keyerror") == REMEDIATION_GUIDE["KeyError"]
    assert get_remediation("SQL INJECTION") == REMEDIATION_GUIDE["SQL Injection"]


def test_lookup_returns_none_for_unknown_name():
    assert get_remediation("not a real category") is None


def test_covers_categories_no_checker_currently_detects():
    """The whole point of this module: guidance for categories this
    project's deterministic checkers don't (and in some cases can't)
    cover -- confirm a representative sample is actually present."""
    for name in ("NameError", "TypeError", "Cross-Site Scripting (XSS)", "Race Condition"):
        assert get_remediation(name) is not None
