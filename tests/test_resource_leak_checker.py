from src.analyzers.resource_leak_checker import detect_unclosed_file_handles


def test_flags_an_unclosed_file_handle():
    code = (
        "def load_session(file_name):\n"
        '    file = open(file_name, "rb")\n'
        "    return file.read()\n"
    )
    findings = detect_unclosed_file_handles(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only, by design
    assert "file" in findings[0].message


def test_skips_when_explicitly_closed():
    code = (
        "def load_session(file_name):\n"
        '    file = open(file_name, "rb")\n'
        "    data = file.read()\n"
        "    file.close()\n"
        "    return data\n"
    )
    assert detect_unclosed_file_handles(code, "app.py") == []


def test_skips_when_the_handle_is_returned():
    """Returning the handle transfers ownership of closing it to the
    caller -- can't verify one way or the other whether that's honored,
    so stays silent rather than risk a wrong flag."""
    code = (
        "def load_session(file_name):\n"
        '    file = open(file_name, "rb")\n'
        "    return file\n"
    )
    assert detect_unclosed_file_handles(code, "app.py") == []


def test_skips_when_the_handle_is_passed_to_another_call():
    code = (
        "def load_session(file_name):\n"
        '    file = open(file_name, "rb")\n'
        "    return pickle.load(file)\n"
    )
    assert detect_unclosed_file_handles(code, "app.py") == []


def test_skips_a_with_statement_open():
    code = (
        "def load_session(file_name):\n"
        '    with open(file_name, "rb") as file:\n'
        "        return file.read()\n"
    )
    assert detect_unclosed_file_handles(code, "app.py") == []


def test_skips_open_result_used_only_as_an_expression():
    """open(...).read() with no assignment at all -- nothing named to
    ever leak past this statement (CPython closes it once refcount hits
    zero at the end of the expression in practice, and there's no
    variable this checker could even flag)."""
    code = 'def load_session(file_name):\n    return open(file_name, "rb").read()\n'
    assert detect_unclosed_file_handles(code, "app.py") == []
