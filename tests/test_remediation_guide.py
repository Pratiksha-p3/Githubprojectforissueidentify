from src.core.remediation_guide import (
    REMEDIATION_GUIDE,
    get_remediation,
    get_remediation_for_finding,
)


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


def test_unbound_local_error_entry_exists_and_is_distinct_from_name_error():
    assert get_remediation("UnboundLocalError") is not None
    assert get_remediation("UnboundLocalError") != get_remediation("NameError")


def test_get_remediation_for_finding_maps_known_checker_sources():
    assert get_remediation_for_finding("sql_injection_checker", "x") == get_remediation(
        "SQL Injection"
    )
    assert get_remediation_for_finding("index_guard_checker", "x") == get_remediation(
        "IndexError"
    )
    assert get_remediation_for_finding("type_mismatch_checker", "x") == get_remediation(
        "TypeError"
    )
    assert get_remediation_for_finding("invalid_method_checker", "x") == get_remediation(
        "AttributeError"
    )


def test_get_remediation_for_finding_returns_none_for_an_unmapped_source():
    assert get_remediation_for_finding("some_future_checker", "x") is None


def test_get_remediation_for_finding_distinguishes_undefined_name_checkers_two_exceptions():
    unbound = get_remediation_for_finding(
        "undefined_name_checker", "undefined name 'x' — raises UnboundLocalError: ..."
    )
    plain = get_remediation_for_finding(
        "undefined_name_checker", "undefined name 'x' — raises NameError the moment..."
    )
    assert unbound == get_remediation("UnboundLocalError")
    assert plain == get_remediation("NameError")
    assert unbound != plain
