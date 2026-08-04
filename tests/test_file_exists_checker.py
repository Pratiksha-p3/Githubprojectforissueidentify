import ast
import os
import tempfile

from src.analyzers.file_exists_checker import detect_unguarded_file_open
from src.core.grounding import is_trustworthy


def test_flags_unguarded_open_of_a_variable_path():
    code = "def load(path):\n    return open(path).read()\n"
    findings = detect_unguarded_file_open(code, "app.py")
    assert len(findings) == 1
    assert "path" in findings[0].message


def test_fix_for_a_with_statement_wraps_the_full_block_body_too():
    """Regression: the fix used to wrap only the `with open(...) as f:`
    header line, dropping its indented body entirely -- producing a
    `with` statement with no suite, which doesn't even parse. The fix
    must now span the statement's full range (Finding.end_line) and wrap
    every line of it, body included."""
    code = "def read_config(path):\n    with open(path) as f:\n        return f.read()\n"
    findings = detect_unguarded_file_open(code, "app.py")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.line == 2
    assert finding.end_line == 3
    assert is_trustworthy(finding, code)

    wrapped = f"if True:\n{finding.fix}"
    ast.parse(wrapped)  # must not raise


def test_fix_applied_in_place_of_the_with_statement_produces_working_code():
    code = "def read_config(path):\n    with open(path) as f:\n        return f.read()\n"
    finding = detect_unguarded_file_open(code, "app.py")[0]

    lines = code.splitlines()
    lines[finding.line - 1 : finding.end_line] = finding.fix.splitlines()
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("hello")
        tmp_path = tmp.name
    try:
        assert namespace["read_config"](tmp_path) == "hello"
    finally:
        os.unlink(tmp_path)


def test_skips_when_guarded_by_exists_check():
    code = (
        "import os\n"
        "def load(path):\n"
        "    if os.path.exists(path):\n"
        "        return open(path).read()\n"
        "    return None\n"
    )
    assert detect_unguarded_file_open(code, "app.py") == []


def test_skips_when_guarded_by_try_except():
    code = (
        "def load(path):\n"
        "    try:\n"
        "        return open(path).read()\n"
        "    except FileNotFoundError:\n"
        "        return None\n"
    )
    assert detect_unguarded_file_open(code, "app.py") == []


def test_ignores_open_of_a_literal_path():
    code = 'def load():\n    return open("config.json").read()\n'
    assert detect_unguarded_file_open(code, "app.py") == []
