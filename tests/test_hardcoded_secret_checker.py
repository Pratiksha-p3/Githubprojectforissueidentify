from src.analyzers.hardcoded_secret_checker import detect_hardcoded_secrets


def test_flags_hardcoded_api_key():
    code = 'API_KEY = "hardcoded-secret-key"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    assert len(findings) == 1
    assert findings[0].line == 1
    assert findings[0].severity.value == "critical"
    assert findings[0].source == "hardcoded_secret_checker"


def test_flags_hardcoded_password():
    code = 'password = "admin123"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    assert len(findings) == 1
    assert "password" in findings[0].message


def test_flags_various_secret_name_shapes():
    code = (
        'SECRET_KEY = "x"\n'
        'access_token = "y"\n'
        'PRIVATE_KEY = "z"\n'
        'db_password = "w"\n'
    )
    findings = detect_hardcoded_secrets(code, "app.py")
    assert len(findings) == 4


def test_does_not_flag_value_read_from_environment():
    code = 'import os\nAPI_KEY = os.environ["API_KEY"]\n'
    assert detect_hardcoded_secrets(code, "app.py") == []


def test_does_not_flag_value_from_a_function_call():
    code = "TOKEN = generate_token()\n"
    assert detect_hardcoded_secrets(code, "app.py") == []


def test_does_not_flag_empty_string():
    code = 'API_KEY = ""\n'
    assert detect_hardcoded_secrets(code, "app.py") == []


def test_does_not_flag_ordinary_variable_names():
    code = 'primary_key = "id"\nsort_key = "name"\n'
    assert detect_hardcoded_secrets(code, "app.py") == []


def test_fix_suggests_reading_from_environment_when_os_is_already_imported():
    code = 'import os\nAPI_KEY = "hardcoded-secret-key"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    assert findings[0].fix == 'API_KEY = os.environ["API_KEY"]'


def test_no_fix_generated_when_os_is_not_imported():
    """Confirmed live: --auto-apply committing this checker's fix to a
    file with no `import os` produced a real NameError on a real PR,
    since nothing added the missing import. Declining to generate a fix
    at all (rather than guessing where to splice an import statement) is
    safer -- same "no fix is better than a wrong fix" precedent
    sql_injection_checker.py already sets."""
    code = 'API_KEY = "hardcoded-secret-key"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""
    assert "no fix generated" in findings[0].message


def test_still_flagged_as_a_finding_even_with_no_fix():
    """The absence of a fix must never suppress the finding itself --
    a hardcoded secret is still a real, critical problem worth
    reporting even when this checker can't confidently auto-generate
    the exact remediation."""
    code = 'API_KEY = "hardcoded-secret-key"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    assert len(findings) == 1
    assert findings[0].severity.value == "critical"


def test_fix_still_generated_when_os_imported_via_import_with_alias():
    code = 'import os as _os\nAPI_KEY = "hardcoded-secret-key"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    # `import os as _os` still imports the `os` module (just bound to a
    # different name) -- but this checker's fix always writes `os.` (not
    # `_os.`), so aliasing to a non-`os` name genuinely doesn't satisfy
    # what this checker needs, and it correctly declines a fix.
    assert findings[0].fix == ""


def test_syntax_error_returns_empty_list():
    assert detect_hardcoded_secrets("def broken(:\n", "app.py") == []
