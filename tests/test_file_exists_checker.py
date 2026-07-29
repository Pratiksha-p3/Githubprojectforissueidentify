from src.analyzers.file_exists_checker import detect_unguarded_file_open


def test_flags_unguarded_open_of_a_variable_path():
    code = "def load(path):\n    return open(path).read()\n"
    findings = detect_unguarded_file_open(code, "app.py")
    assert len(findings) == 1
    assert "path" in findings[0].message


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
