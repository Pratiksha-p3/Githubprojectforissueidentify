from src.analyzers.path_traversal_checker import detect_path_traversal


def test_flags_unguarded_path_join_with_a_parameter():
    code = (
        "import os\n\n"
        "def read_file(base_dir, filename):\n"
        "    path = os.path.join(base_dir, filename)\n"
        "    return open(path).read()\n"
    )
    findings = detect_path_traversal(code, "app.py")
    assert len(findings) == 1
    assert "filename" in findings[0].message
    assert findings[0].fix == ""  # detection only, by design


def test_skips_when_guarded_by_dotdot_check():
    code = (
        "import os\n\n"
        "def read_file(base_dir, filename):\n"
        '    if ".." in filename:\n'
        '        raise ValueError("bad path")\n'
        "    path = os.path.join(base_dir, filename)\n"
        "    return open(path).read()\n"
    )
    assert detect_path_traversal(code, "app.py") == []


def test_skips_when_guarded_by_secure_filename():
    code = (
        "import os\n"
        "from werkzeug.utils import secure_filename\n\n"
        "def read_file(base_dir, filename):\n"
        "    filename = secure_filename(filename)\n"
        "    path = os.path.join(base_dir, filename)\n"
        "    return open(path).read()\n"
    )
    assert detect_path_traversal(code, "app.py") == []


def test_skips_when_guarded_by_realpath_check():
    code = (
        "import os\n\n"
        "def read_file(base_dir, filename):\n"
        "    path = os.path.join(base_dir, filename)\n"
        "    resolved = os.path.realpath(path)\n"
        "    return open(resolved).read()\n"
    )
    assert detect_path_traversal(code, "app.py") == []


def test_ignores_join_with_no_parameter_involved():
    code = (
        "import os\n\n"
        "def read_file():\n"
        '    path = os.path.join("static", "config.json")\n'
        "    return open(path).read()\n"
    )
    assert detect_path_traversal(code, "app.py") == []


def test_ignores_open_without_os_path_join():
    code = "def read_file(path):\n    return open(path).read()\n"
    assert detect_path_traversal(code, "app.py") == []
