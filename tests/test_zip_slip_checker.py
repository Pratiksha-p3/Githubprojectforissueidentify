from src.analyzers.zip_slip_checker import detect_zip_slip


def test_flags_unguarded_extractall():
    code = (
        "import zipfile\n\n"
        "def extract(path, dest):\n"
        "    with zipfile.ZipFile(path) as zf:\n"
        "        zf.extractall(dest)\n"
    )
    findings = detect_zip_slip(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only, by design


def test_flags_unguarded_extract():
    code = (
        "import zipfile\n\n"
        "def extract_one(path, dest, member):\n"
        "    with zipfile.ZipFile(path) as zf:\n"
        "        zf.extract(member, dest)\n"
    )
    findings = detect_zip_slip(code, "app.py")
    assert len(findings) == 1


def test_skips_when_members_are_validated_via_namelist():
    code = (
        "import zipfile\n\n"
        "def extract(path, dest):\n"
        "    with zipfile.ZipFile(path) as zf:\n"
        "        for name in zf.namelist():\n"
        '            if ".." in name:\n'
        "                raise ValueError(name)\n"
        "        zf.extractall(dest)\n"
    )
    assert detect_zip_slip(code, "app.py") == []


def test_skips_when_guarded_by_dotdot_check():
    code = (
        "import zipfile\n\n"
        "def extract(path, dest, member_name):\n"
        '    if ".." in member_name:\n'
        '        raise ValueError("bad")\n'
        "    with zipfile.ZipFile(path) as zf:\n"
        "        zf.extractall(dest)\n"
    )
    assert detect_zip_slip(code, "app.py") == []


def test_ignores_unrelated_extract_methods():
    code = "def run(obj):\n    obj.extract_data()\n"
    assert detect_zip_slip(code, "app.py") == []
