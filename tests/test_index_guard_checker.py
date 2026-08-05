import ast

from src.analyzers.index_guard_checker import detect_unguarded_index_access


def test_flags_unguarded_index_access():
    code = "def first_item(items):\n    return items[0]\n"
    findings = detect_unguarded_index_access(code, "app.py")
    assert len(findings) == 1
    assert "items" in findings[0].message


def test_skips_when_guarded_by_len_greater_than():
    code = (
        "def first_item(items):\n"
        "    if len(items) > 0:\n"
        "        return items[0]\n"
        "    return None\n"
    )
    assert detect_unguarded_index_access(code, "app.py") == []


def test_skips_when_guarded_by_truthiness_check():
    code = (
        "def first_item(items):\n"
        "    if items:\n"
        "        return items[0]\n"
        "    return None\n"
    )
    assert detect_unguarded_index_access(code, "app.py") == []


def test_skips_when_guarded_by_try_except():
    code = (
        "def first_item(items):\n"
        "    try:\n"
        "        return items[0]\n"
        "    except IndexError:\n"
        "        return None\n"
    )
    assert detect_unguarded_index_access(code, "app.py") == []


def test_ignores_a_variable_index():
    """A variable index (`items[i]`) can't be statically verified safe
    or unsafe -- e.g. it might be bounded by a `range(len(items))` loop
    -- so it's deliberately not flagged, matching dict_key_checker's
    literal-key-only scope."""
    code = "def get(items, i):\n    return items[i]\n"
    assert detect_unguarded_index_access(code, "app.py") == []


def test_ignores_negative_index():
    code = "def last_item(items):\n    return items[-1]\n"
    assert detect_unguarded_index_access(code, "app.py") == []


def test_higher_index_requires_a_stronger_guard():
    code = (
        "def third_item(items):\n"
        "    if len(items) > 0:\n"
        "        return items[2]\n"
        "    return None\n"
    )
    findings = detect_unguarded_index_access(code, "app.py")
    assert len(findings) == 1  # len > 0 doesn't cover index 2


def test_flags_a_direct_literal_indexed_out_of_bounds():
    code = "print([1, 2, 3][5])\n"
    findings = detect_unguarded_index_access(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only -- correct fix isn't derivable
    assert "3 item" in findings[0].message


def test_flags_a_single_assignment_variable_indexed_out_of_bounds():
    """The shape the checker was actually missing: `numbers = [1, 2, 3]`
    followed by `numbers[5]` elsewhere -- indexing through a variable,
    not the literal directly."""
    code = "numbers = [1, 2, 3]\nprint(numbers[5])\n"
    findings = detect_unguarded_index_access(code, "app.py")
    assert len(findings) == 1
    assert "numbers" in findings[0].message
    assert "3 item" in findings[0].message


def test_flags_a_negative_index_out_of_bounds():
    """Regression: Python's AST represents a negative literal (`-5`) as
    UnaryOp(USub, Constant(5)), NOT a single Constant(-5) the way a
    positive literal is -- ast.parse() never folds unary minus. Missing
    that shape meant every negative-index case was silently never even
    recognized as a literal, let alone bounds-checked. Caught by testing
    the negative case directly rather than assuming it worked because
    the positive case did."""
    code = "x = (1, 2, 3)\nprint(x[-5])\n"
    findings = detect_unguarded_index_access(code, "app.py")
    assert len(findings) == 1


def test_skips_a_valid_negative_index_on_a_tracked_literal():
    code = "x = (1, 2, 3)\nprint(x[-1])\n"
    assert detect_unguarded_index_access(code, "app.py") == []


def test_skips_a_valid_index_on_a_tracked_literal():
    code = "numbers = [1, 2, 3]\nprint(numbers[1])\n"
    assert detect_unguarded_index_access(code, "app.py") == []


def test_ignores_a_variable_reassigned_elsewhere():
    """Deliberately conservative: a name assigned more than once anywhere
    is dropped entirely rather than risk pairing a stale length with a
    later, differently-sourced use -- this project has no real dataflow
    analysis to know which assignment actually reaches which use."""
    code = "numbers = [1, 2, 3]\nnumbers = get_numbers()\nprint(numbers[5])\n"
    assert detect_unguarded_index_access(code, "app.py") == []


def test_fix_applied_produces_a_working_guard():
    code = "def first_item(items):\n    return items[0]\n"
    findings = detect_unguarded_index_access(code, "app.py")
    finding = findings[0]

    lines = code.splitlines()
    lines[finding.line - 1] = finding.fix
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert namespace["first_item"]([42]) == 42
    try:
        namespace["first_item"]([])
        raise AssertionError("expected IndexError")
    except IndexError:
        pass
