from src.analyzers.unused_import_checker import detect_unused_imports


def test_flags_an_unused_import():
    code = "import os\n\ndef f():\n    return 1\n"
    findings = detect_unused_imports(code, "app.py")
    assert len(findings) == 1
    assert "os" in findings[0].message
    assert findings[0].fix == ""  # detection only, by design


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
