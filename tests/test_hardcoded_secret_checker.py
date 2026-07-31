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


def test_fix_suggests_reading_from_environment():
    code = 'API_KEY = "hardcoded-secret-key"\n'
    findings = detect_hardcoded_secrets(code, "app.py")
    assert findings[0].fix == 'API_KEY = os.environ["API_KEY"]'


def test_syntax_error_returns_empty_list():
    assert detect_hardcoded_secrets("def broken(:\n", "app.py") == []
