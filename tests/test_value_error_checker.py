from src.analyzers.value_error_checker import detect_guaranteed_value_errors


def test_flags_int_on_a_non_numeric_string_literal():
    findings = detect_guaranteed_value_errors('int("abc")\n', "app.py")
    assert len(findings) == 1
    assert "raise ValueError" in findings[0].fix
    assert findings[0].severity.value == "critical"

    import ast

    lines = ['int("abc")']
    end = findings[0].end_line or findings[0].line
    lines[findings[0].line - 1 : end] = findings[0].fix.splitlines()
    ast.parse("\n".join(lines))  # must not raise


def test_fix_stays_valid_even_when_the_literal_contains_a_double_quote():
    """Regression: the generated fix must NOT embed the literal's raw
    text via hand-picked quote characters -- confirmed live that doing
    so broke the generated statement's own syntax the moment the value
    itself contained a `"`."""
    code = 'int(\'ab"c\')\n'
    findings = detect_guaranteed_value_errors(code, "app.py")
    assert len(findings) == 1

    import ast

    lines = code.splitlines()
    end = findings[0].end_line or findings[0].line
    lines[findings[0].line - 1 : end] = findings[0].fix.splitlines()
    ast.parse("\n".join(lines))  # must not raise


def test_skips_int_on_a_genuinely_numeric_string_literal():
    assert detect_guaranteed_value_errors('int("42")\n', "app.py") == []


def test_skips_int_on_a_numeric_string_with_surrounding_whitespace():
    """int() itself strips whitespace -- must call the real int(), not a
    naive regex, or this would be a false positive."""
    assert detect_guaranteed_value_errors('int("  42  ")\n', "app.py") == []


def test_skips_int_on_a_negative_numeric_string():
    assert detect_guaranteed_value_errors('int("-5")\n', "app.py") == []


def test_flags_float_on_a_non_numeric_string_literal():
    findings = detect_guaranteed_value_errors('float("xyz")\n', "app.py")
    assert len(findings) == 1


def test_skips_float_on_a_numeric_string_literal():
    assert detect_guaranteed_value_errors('float("3.14")\n', "app.py") == []


def test_skips_float_on_inf_and_nan_strings():
    """CPython's own float() genuinely accepts these -- calling the real
    converter (not reimplementing its rules) is what gets this right."""
    assert detect_guaranteed_value_errors('float("inf")\n', "app.py") == []
    assert detect_guaranteed_value_errors('float("nan")\n', "app.py") == []


def test_skips_int_with_a_base_argument():
    """int(x, base) changes what's valid -- e.g. "ff" is fine for base
    16 -- so the two-argument form is deliberately left alone."""
    assert detect_guaranteed_value_errors('int("ff", 16)\n', "app.py") == []


def test_flags_a_single_assignment_variable():
    code = 's = "abc"\nint(s)\n'
    findings = detect_guaranteed_value_errors(code, "app.py")
    assert len(findings) == 1
    assert "'s'" in findings[0].message


def test_ignores_a_variable_reassigned_elsewhere():
    code = 's = "abc"\ns = get_thing()\nint(s)\n'
    assert detect_guaranteed_value_errors(code, "app.py") == []


def test_ignores_unknown_argument_sources_like_function_parameters():
    code = "def f(x):\n    return int(x)\n"
    assert detect_guaranteed_value_errors(code, "app.py") == []


def test_ignores_a_function_calls_return_value():
    assert detect_guaranteed_value_errors("int(get_thing())\n", "app.py") == []


def test_ignores_an_unrelated_function_named_int_shaped_call():
    """Only bare int()/float() name calls are reasoned about -- not e.g.
    obj.int("abc")."""
    assert detect_guaranteed_value_errors('obj.int("abc")\n', "app.py") == []


def test_returns_empty_for_a_syntax_error_file():
    assert detect_guaranteed_value_errors("def broken(:\n    pass\n", "app.py") == []
