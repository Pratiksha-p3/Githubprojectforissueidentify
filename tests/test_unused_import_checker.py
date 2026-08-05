import ast

from src.analyzers.unused_import_checker import detect_unused_imports


def test_flags_an_unused_import():
    code = "import os\n\ndef f():\n    return 1\n"
    findings = detect_unused_imports(code, "app.py")
    assert len(findings) == 1
    assert "os" in findings[0].message


def test_unused_import_fix_is_a_deletion_not_a_missing_fix():
    """fix == "" here means "delete this line", not "no fix was
    generated" -- must be marked via fix_is_deletion so it's still
    treated as a real, applicable fix everywhere a Finding's fix is
    checked (has_fix, apply_fixes_to_file, --auto-apply eligibility)."""
    code = "import os\n\ndef f():\n    return 1\n"
    finding = detect_unused_imports(code, "app.py")[0]
    assert finding.fix == ""
    assert finding.fix_is_deletion is True
    assert finding.has_fix is True


def test_flags_multiple_unused_imports_separately():
    code = "import os\nimport sys\nimport json\n\ndef f():\n    return json.dumps({})\n"
    findings = detect_unused_imports(code, "app.py")
    assert len(findings) == 2
    names = {f.message for f in findings}
    assert any("os" in m for m in names)
    assert any("sys" in m for m in names)
    assert not any("json" in m for m in names)


def test_ignores_a_used_import():
    code = "import os\n\ndef f():\n    return os.getpid()\n"
    assert detect_unused_imports(code, "app.py") == []


def test_ignores_from_import_that_is_used():
    code = "from typing import Optional\n\ndef f(x: Optional[int]) -> int:\n    return x or 0\n"
    assert detect_unused_imports(code, "app.py") == []


def test_flags_unused_from_import():
    code = "from typing import Optional\n\ndef f():\n    return 1\n"
    findings = detect_unused_imports(code, "app.py")
    assert len(findings) == 1
    assert "Optional" in findings[0].message


def test_returns_empty_for_a_syntax_error_file():
    """ruff itself handles a file that doesn't parse by reporting a
    different rule (or nothing for F401 specifically) -- either way this
    must not raise."""
    result = detect_unused_imports("def broken(:\n    pass\n", "app.py")
    assert result == []


def test_groups_multiple_unused_names_from_one_statement_into_one_finding():
    """Regression: `from typing import (Optional, List)` with BOTH
    unused produces two ruff diagnostics that share the SAME deletion
    edit (removing the whole statement). Reporting them as two separate
    findings would give apply_fixes_to_file() two overlapping ranges,
    which its conflict detection would treat as a collision -- neither
    ever gets applied. They must be grouped into one finding covering
    the whole statement."""
    code = "from typing import (\n    Optional,\n    List,\n)\n\n\ndef f():\n    return 1\n"
    findings = detect_unused_imports(code, "app.py")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.line == 1
    assert finding.end_line == 4
    assert "Optional" in finding.message
    assert "List" in finding.message
    assert finding.fix_is_deletion is True


def test_deletion_fix_applied_over_its_range_produces_working_code():
    code = "import os\nimport sys\n\ndef f():\n    return sys.argv\n"
    finding = detect_unused_imports(code, "app.py")[0]

    lines = code.splitlines()
    end = finding.end_line or finding.line
    lines[finding.line - 1 : end] = finding.fix.splitlines()
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert "os" not in patched
    assert namespace["f"]() is not None
