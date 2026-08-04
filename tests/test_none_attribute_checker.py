import ast

from src.analyzers.none_attribute_checker import detect_unguarded_none_attribute_access


def test_flags_unguarded_attribute_access_after_dict_get():
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user")\n'
        "    return user.role\n"
    )
    findings = detect_unguarded_none_attribute_access(code, "app.py")
    assert len(findings) == 1
    assert "user" in findings[0].message


def test_ignores_requests_get_which_is_not_dict_get():
    """Regression: requests.get(url) matches the same `.get(single-arg)`
    shape as dict.get(key) syntactically, but is an HTTP request, not a
    dict lookup, and never returns None -- confirmed live as a false
    positive on a real PR before this exclusion existed."""
    code = (
        "import requests\n\n"
        "def fetch_data(url):\n"
        "    response = requests.get(url)\n"
        "    return response.json()\n"
    )
    assert detect_unguarded_none_attribute_access(code, "app.py") == []


def test_still_flags_get_on_a_non_imported_name():
    """The exclusion is specifically for names bound by `import` in this
    file -- a parameter or local variable named the same as a module
    (e.g. a dict parameter that happens to be called `requests`) must
    still be flagged normally."""
    code = (
        "def handler(requests):\n"
        "    user = requests.get('user')\n"
        "    return user.role\n"
    )
    findings = detect_unguarded_none_attribute_access(code, "app.py")
    assert len(findings) == 1


def test_flags_unguarded_attribute_access_after_re_match():
    code = (
        "import re\n\n"
        "def extract(s):\n"
        '    m = re.match(r"[0-9]+", s)\n'
        "    return m.group()\n"
    )
    findings = detect_unguarded_none_attribute_access(code, "app.py")
    assert len(findings) == 1


def test_flags_unguarded_attribute_access_after_re_search():
    code = (
        "import re\n\n"
        "def extract(s):\n"
        '    m = re.search(r"[0-9]+", s)\n'
        "    return m.group()\n"
    )
    findings = detect_unguarded_none_attribute_access(code, "app.py")
    assert len(findings) == 1


def test_skips_when_guarded_by_is_none_check():
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user")\n'
        "    if user is None:\n"
        "        return None\n"
        "    return user.role\n"
    )
    assert detect_unguarded_none_attribute_access(code, "app.py") == []


def test_skips_when_guarded_by_truthiness_check():
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user")\n'
        "    if user:\n"
        "        return user.role\n"
        "    return None\n"
    )
    assert detect_unguarded_none_attribute_access(code, "app.py") == []


def test_skips_when_guarded_by_try_except():
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user")\n'
        "    try:\n"
        "        return user.role\n"
        "    except AttributeError:\n"
        "        return None\n"
    )
    assert detect_unguarded_none_attribute_access(code, "app.py") == []


def test_skips_get_with_a_default_value():
    """A non-None default means the result can't be None from this call
    alone, so there's nothing to guard against."""
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user", {})\n'
        "    return user.role\n"
    )
    assert detect_unguarded_none_attribute_access(code, "app.py") == []


def test_ignores_non_attribute_usage():
    """Returning or testing the value directly (not accessing an
    attribute on it) never raises AttributeError."""
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user")\n'
        "    return user\n"
    )
    assert detect_unguarded_none_attribute_access(code, "app.py") == []


def test_fix_applied_produces_a_working_guard():
    code = (
        "def get_role(payload):\n"
        '    user = payload.get("user")\n'
        "    return user.role\n"
    )
    findings = detect_unguarded_none_attribute_access(code, "app.py")
    finding = findings[0]

    lines = code.splitlines()
    lines[finding.line - 1] = finding.fix
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    try:
        namespace["get_role"]({})
        raise AssertionError("expected AttributeError")
    except AttributeError:
        pass
