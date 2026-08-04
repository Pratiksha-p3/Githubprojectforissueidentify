import ast

from src.analyzers.weak_crypto_checker import detect_weak_crypto


def test_flags_md5():
    code = "import hashlib\n\ndef hash_it(data):\n    return hashlib.md5(data).hexdigest()\n"
    findings = detect_weak_crypto(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == "    return hashlib.sha256(data).hexdigest()"


def test_flags_sha1():
    code = "import hashlib\n\ndef hash_it(data):\n    return hashlib.sha1(data).hexdigest()\n"
    findings = detect_weak_crypto(code, "app.py")
    assert len(findings) == 1
    assert "sha256" in findings[0].fix


def test_skips_sha256_already():
    code = "import hashlib\n\ndef hash_it(data):\n    return hashlib.sha256(data).hexdigest()\n"
    assert detect_weak_crypto(code, "app.py") == []


def test_ignores_file_without_hashlib_import():
    code = "def hash_it(data):\n    return hashlib.md5(data).hexdigest()\n"
    assert detect_weak_crypto(code, "app.py") == []


def test_fix_targets_the_inner_call_not_an_outer_chained_call():
    """Regression: matching by position alone previously produced
    `hashlib.md5(data).sha256()` -- mangling the wrong call in a chained
    expression. See http_timeout_checker's identical bug, caught first."""
    code = "import hashlib\n\ndef hash_it(data):\n    return hashlib.md5(data).hexdigest()\n"
    findings = detect_weak_crypto(code, "app.py")
    assert "hashlib.sha256(data)" in findings[0].fix
    assert ".sha256()" not in findings[0].fix


def test_fix_preserves_an_assignment_target():
    code = (
        "import hashlib\n\n"
        "def hash_it(data):\n"
        "    digest = hashlib.md5(data).hexdigest()\n"
        "    return digest\n"
    )
    findings = detect_weak_crypto(code, "app.py")
    assert findings[0].fix == "    digest = hashlib.sha256(data).hexdigest()"


def test_fix_is_syntactically_valid_when_applied():
    code = "import hashlib\n\ndef hash_it(data):\n    return hashlib.md5(data).hexdigest()\n"
    findings = detect_weak_crypto(code, "app.py")
    lines = code.splitlines()
    lines[findings[0].line - 1] = findings[0].fix
    patched = "\n".join(lines)
    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert namespace["hash_it"](b"x") == __import__("hashlib").sha256(b"x").hexdigest()
