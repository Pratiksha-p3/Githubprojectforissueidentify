import ast

from src.analyzers.http_timeout_checker import detect_unguarded_http_calls


def test_flags_get_call_with_no_timeout():
    code = "import requests\n\ndef fetch(url):\n    return requests.get(url)\n"
    findings = detect_unguarded_http_calls(code, "app.py")
    assert len(findings) == 1
    assert "timeout" in findings[0].fix


def test_fix_preserves_the_return_keyword():
    """A previous version of this checker unparsed only the isolated Call
    node, silently dropping whatever statement wrapped it -- for a
    `return requests.get(url)` line, that meant the suggested fix was
    `requests.get(url, timeout=10)` with no `return` at all: applying it
    verbatim would make the function always return None instead of the
    response, with no exception to signal anything went wrong. The
    existing test above never caught this because it only checked for
    the substring "timeout", not the fix's actual text."""
    code = "import requests\n\ndef fetch(url):\n    return requests.get(url)\n"
    findings = detect_unguarded_http_calls(code, "app.py")
    assert findings[0].fix == "    return requests.get(url, timeout=10)"


def test_fix_preserves_an_assignment_target():
    """The same class of bug as above, but for an assignment: dropping
    `response = ` produces a fix that, applied verbatim, leaves every
    later reference to `response` as a NameError -- confirmed live when
    exactly this suggestion was accepted on a real PR and broke the
    file."""
    code = (
        "import requests\n\n"
        "def fetch_data(url):\n"
        "    response = requests.get(url)\n"
        "    return response.json()\n"
    )
    findings = detect_unguarded_http_calls(code, "app.py")
    assert findings[0].fix == "    response = requests.get(url, timeout=10)"


def test_fix_is_syntactically_valid_and_semantically_equivalent_when_applied():
    """Applying the suggested fix in place of the original line must
    produce a file that still parses AND still assigns the same name --
    the actual end-to-end guarantee a GitHub "Apply suggestion" click
    depends on."""
    code = (
        "import requests\n\n"
        "def fetch_data(url):\n"
        "    response = requests.get(url)\n"
        "    return response.json()\n"
    )
    findings = detect_unguarded_http_calls(code, "app.py")
    lines = code.splitlines()
    lines[findings[0].line - 1] = findings[0].fix
    patched = "\n".join(lines)

    ast.parse(patched)  # must still be valid Python
    assert "response = requests.get" in patched  # target name preserved


def test_skips_when_timeout_already_present():
    code = "import requests\n\ndef fetch(url):\n    return requests.get(url, timeout=5)\n"
    assert detect_unguarded_http_calls(code, "app.py") == []


def test_ignores_file_without_requests_import():
    code = "def fetch(url):\n    return requests.get(url)\n"
    assert detect_unguarded_http_calls(code, "app.py") == []


def test_preserves_existing_kwargs_in_fix():
    code = 'import requests\n\ndef fetch(url):\n    return requests.post(url, json={"a": 1})\n'
    findings = detect_unguarded_http_calls(code, "app.py")
    assert len(findings) == 1
    assert "json=" in findings[0].fix
    assert "timeout=10" in findings[0].fix
