from src.analyzers.http_timeout_checker import detect_unguarded_http_calls


def test_flags_get_call_with_no_timeout():
    code = "import requests\n\ndef fetch(url):\n    return requests.get(url)\n"
    findings = detect_unguarded_http_calls(code, "app.py")
    assert len(findings) == 1
    assert "timeout" in findings[0].fix


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
