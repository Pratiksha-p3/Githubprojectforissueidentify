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
