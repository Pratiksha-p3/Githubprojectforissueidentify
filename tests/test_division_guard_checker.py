from src.analyzers.division_guard_checker import detect_unguarded_division


def test_flags_division_by_unguarded_parameter():
    code = "def divide(a, b):\n    return a / b\n"
    findings = detect_unguarded_division(code, "app.py")
    assert len(findings) == 1
    assert findings[0].line == 2
    assert "b" in findings[0].message
    assert "if b == 0" in findings[0].fix


def test_skips_when_already_guarded_with_equality_check():
    code = (
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ValueError('b is zero')\n"
        "    return a / b\n"
    )
    assert detect_unguarded_division(code, "app.py") == []


def test_skips_when_guarded_by_truthy_check():
    code = "def divide(a, b):\n    if b:\n        return a / b\n    return 0\n"
    assert detect_unguarded_division(code, "app.py") == []


def test_skips_when_guarded_by_try_except():
    code = (
        "def divide(a, b):\n"
        "    try:\n"
        "        return a / b\n"
        "    except ZeroDivisionError:\n"
        "        return 0\n"
    )
    assert detect_unguarded_division(code, "app.py") == []


def test_ignores_division_by_call_expression_not_a_bare_name():
    code = "def average(numbers):\n    return sum(numbers) / len(numbers)\n"
    assert detect_unguarded_division(code, "app.py") == []


def test_ignores_division_by_non_parameter_local_variable():
    code = "def compute(a):\n    divisor = 10\n    return a / divisor\n"
    assert detect_unguarded_division(code, "app.py") == []


def test_ignores_syntax_error_gracefully():
    assert detect_unguarded_division("def broken(:\n", "app.py") == []
