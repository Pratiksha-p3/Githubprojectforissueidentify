from src.analyzers.type_mismatch_checker import detect_type_mismatched_addition


def test_flags_string_plus_int():
    code = 'print("count: " + 5)\n'
    findings = detect_type_mismatched_addition(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only -- correct fix isn't derivable
    assert findings[0].severity.value == "critical"


def test_flags_int_plus_string():
    code = 'print(5 + "count")\n'
    findings = detect_type_mismatched_addition(code, "app.py")
    assert len(findings) == 1


def test_skips_string_plus_string():
    assert detect_type_mismatched_addition('print("a" + "b")\n', "app.py") == []


def test_skips_numeric_plus_numeric():
    assert detect_type_mismatched_addition("print(1 + 2)\n", "app.py") == []
    assert detect_type_mismatched_addition("print(1 + 2.5)\n", "app.py") == []


def test_bool_is_numeric_compatible_with_int():
    assert detect_type_mismatched_addition("print(True + 1)\n", "app.py") == []


def test_bool_is_incompatible_with_string():
    findings = detect_type_mismatched_addition('print(True + "x")\n', "app.py")
    assert len(findings) == 1


def test_skips_list_plus_list():
    assert detect_type_mismatched_addition("print([1, 2] + [3, 4])\n", "app.py") == []


def test_flags_list_plus_tuple():
    findings = detect_type_mismatched_addition("print([1, 2] + (3, 4))\n", "app.py")
    assert len(findings) == 1


def test_flags_dict_plus_dict_since_dict_has_no_add_at_all():
    findings = detect_type_mismatched_addition("print({} + {})\n", "app.py")
    assert len(findings) == 1


def test_flags_set_plus_set_since_set_has_no_add_at_all():
    findings = detect_type_mismatched_addition("print({1, 2} + {3, 4})\n", "app.py")
    assert len(findings) == 1


def test_flags_bytes_plus_string():
    findings = detect_type_mismatched_addition("print(b'x' + 'y')\n", "app.py")
    assert len(findings) == 1


def test_flags_a_single_assignment_variable_mismatch():
    code = 'count = 5\nname = "Alice"\nprint(name + count)\n'
    findings = detect_type_mismatched_addition(code, "app.py")
    assert len(findings) == 1
    assert "name" in findings[0].message
    assert "count" in findings[0].message


def test_ignores_a_variable_reassigned_elsewhere():
    """Deliberately conservative: a name assigned more than once anywhere
    is dropped entirely rather than risk pairing a stale type with a
    later, differently-sourced use."""
    code = 'count = 5\ncount = get_count()\nname = "Alice"\nprint(name + count)\n'
    assert detect_type_mismatched_addition(code, "app.py") == []


def test_ignores_unknown_types_like_function_parameters():
    """A function parameter's actual type at any call site isn't
    knowable without real type inference -- must not be guessed at."""
    code = "def f(x, y):\n    return x + y\n"
    assert detect_type_mismatched_addition(code, "app.py") == []


def test_ignores_a_function_calls_return_value():
    code = 'print(get_name() + " suffix")\n'
    assert detect_type_mismatched_addition(code, "app.py") == []


def test_returns_empty_for_a_syntax_error_file():
    assert detect_type_mismatched_addition("def broken(:\n    pass\n", "app.py") == []
