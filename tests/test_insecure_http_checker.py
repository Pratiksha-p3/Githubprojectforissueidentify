import ast

from src.analyzers.insecure_http_checker import detect_insecure_http_urls


def test_flags_a_hardcoded_http_url():
    code = 'PAYROLL_API = "http://payroll.internal/api"\n'
    findings = detect_insecure_http_urls(code, "app.py")
    assert len(findings) == 1
    assert "https://payroll.internal/api" in findings[0].fix
    assert "PAYROLL_API" in findings[0].fix


def test_skips_https_urls():
    code = 'PAYROLL_API = "https://payroll.internal/api"\n'
    assert detect_insecure_http_urls(code, "app.py") == []


def test_skips_localhost():
    code = 'URL = "http://localhost:8000/health"\n'
    assert detect_insecure_http_urls(code, "app.py") == []


def test_skips_loopback_ip():
    code = 'URL = "http://127.0.0.1:8000/health"\n'
    assert detect_insecure_http_urls(code, "app.py") == []


def test_flags_url_used_directly_in_a_call():
    code = 'import requests\n\ndef fetch():\n    return requests.get("http://api.example.com/data")\n'
    findings = detect_insecure_http_urls(code, "app.py")
    assert len(findings) == 1
    assert "https://api.example.com/data" in findings[0].fix


def test_fix_is_syntactically_valid_when_applied():
    code = 'PAYROLL_API = "http://payroll.internal/api"\n'
    findings = detect_insecure_http_urls(code, "app.py")
    lines = code.splitlines()
    lines[findings[0].line - 1] = findings[0].fix
    patched = "\n".join(lines)
    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert namespace["PAYROLL_API"] == "https://payroll.internal/api"
