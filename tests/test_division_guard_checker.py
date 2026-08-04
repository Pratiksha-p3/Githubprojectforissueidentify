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


def test_flags_division_by_len_of_a_parameter():
    """The far more common real shape: an average-style division by a
    sequence parameter's length, e.g. `total / len(numbers)` -- raises
    ZeroDivisionError for an empty sequence, same underlying bug as
    dividing by a bare zero-valued parameter."""
    code = "def average(numbers):\n    return sum(numbers) / len(numbers)\n"
    findings = detect_unguarded_division(code, "app.py")
    assert len(findings) == 1
    assert "numbers" in findings[0].message
    assert "len(numbers) == 0" in findings[0].fix


def test_skips_len_shape_when_guarded_by_truthy_check():
    """An empty sequence is falsy, so `if numbers:` / `if not numbers:`
    guards the len()==0 condition just as validly as an explicit
    len() comparison."""
    code = (
        "def average(numbers):\n"
        "    if not numbers:\n"
        "        return 0\n"
        "    return sum(numbers) / len(numbers)\n"
    )
    assert detect_unguarded_division(code, "app.py") == []


def test_skips_len_shape_when_guarded_by_len_comparison():
    code = (
        "def average(numbers):\n"
        "    if len(numbers) == 0:\n"
        "        return 0\n"
        "    return sum(numbers) / len(numbers)\n"
    )
    assert detect_unguarded_division(code, "app.py") == []


def test_ignores_division_by_an_unrelated_call_expression():
    """Only the len(<param>) shape is covered -- any other call
    expression as the divisor is a different, harder-to-guard shape and
    stays out of scope, same reasoning as before."""
    code = "def average(numbers):\n    return sum(numbers) / compute_count(numbers)\n"
    assert detect_unguarded_division(code, "app.py") == []


def test_ignores_division_by_non_parameter_local_variable():
    code = "def compute(a):\n    divisor = 10\n    return a / divisor\n"
    assert detect_unguarded_division(code, "app.py") == []


def test_ignores_syntax_error_gracefully():
    assert detect_unguarded_division("def broken(:\n", "app.py") == []
