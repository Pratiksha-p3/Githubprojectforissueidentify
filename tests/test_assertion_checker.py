from src.analyzers.assertion_checker import detect_guaranteed_assertion_failures


def test_flags_assert_false():
    findings = detect_guaranteed_assertion_failures("assert False\n", "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only -- correct fix isn't derivable
    assert findings[0].severity.value == "critical"


def test_flags_assert_zero():
    findings = detect_guaranteed_assertion_failures("assert 0\n", "app.py")
    assert len(findings) == 1


def test_flags_assert_none():
    findings = detect_guaranteed_assertion_failures("assert None\n", "app.py")
    assert len(findings) == 1


def test_flags_assert_empty_string():
    findings = detect_guaranteed_assertion_failures('assert ""\n', "app.py")
    assert len(findings) == 1


def test_flags_assert_empty_list():
    findings = detect_guaranteed_assertion_failures("assert []\n", "app.py")
    assert len(findings) == 1


def test_flags_assert_empty_dict():
    findings = detect_guaranteed_assertion_failures("assert {}\n", "app.py")
    assert len(findings) == 1


def test_flags_assert_empty_tuple():
    findings = detect_guaranteed_assertion_failures("assert ()\n", "app.py")
    assert len(findings) == 1


def test_flags_assert_false_with_a_message():
    findings = detect_guaranteed_assertion_failures('assert False, "should not happen"\n', "app.py")
    assert len(findings) == 1


def test_skips_assert_true():
    assert detect_guaranteed_assertion_failures("assert True\n", "app.py") == []


def test_skips_assert_on_a_non_empty_literal():
    assert detect_guaranteed_assertion_failures("assert 1\n", "app.py") == []
    assert detect_guaranteed_assertion_failures('assert "x"\n', "app.py") == []
    assert detect_guaranteed_assertion_failures("assert [1]\n", "app.py") == []


def test_skips_assert_on_a_non_empty_tuple():
    """The classic `assert (x, "msg")` typo -- always truthy, so it's the
    OPPOSITE bug (silently disabled), out of scope for this checker."""
    assert detect_guaranteed_assertion_failures('assert (1, "msg")\n', "app.py") == []


def test_skips_assert_on_a_name():
    """Only literal-truthiness is reasoned about -- a Name's actual value
    isn't knowable without real dataflow analysis."""
    code = "def f(x):\n    assert x\n"
    assert detect_guaranteed_assertion_failures(code, "app.py") == []


def test_skips_assert_on_a_comparison():
    assert detect_guaranteed_assertion_failures("assert 1 == 2\n", "app.py") == []


def test_returns_empty_for_a_syntax_error_file():
    assert detect_guaranteed_assertion_failures("def broken(:\n    pass\n", "app.py") == []
