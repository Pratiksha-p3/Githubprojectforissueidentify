import ast

from src.analyzers.unsafe_yaml_checker import detect_unsafe_yaml_load


def test_flags_load_with_no_loader():
    code = "import yaml\n\ndef load(raw):\n    return yaml.load(raw)\n"
    findings = detect_unsafe_yaml_load(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == "    return yaml.safe_load(raw)"


def test_flags_load_with_unsafe_loader_and_drops_the_kwarg():
    code = "import yaml\n\ndef load(raw):\n    return yaml.load(raw, Loader=yaml.FullLoader)\n"
    findings = detect_unsafe_yaml_load(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == "    return yaml.safe_load(raw)"


def test_skips_when_already_using_safe_loader():
    code = "import yaml\n\ndef load(raw):\n    return yaml.load(raw, Loader=yaml.SafeLoader)\n"
    assert detect_unsafe_yaml_load(code, "app.py") == []


def test_skips_when_already_using_safe_load():
    code = "import yaml\n\ndef load(raw):\n    return yaml.safe_load(raw)\n"
    assert detect_unsafe_yaml_load(code, "app.py") == []


def test_ignores_file_without_yaml_import():
    code = "def load(raw):\n    return yaml.load(raw)\n"
    assert detect_unsafe_yaml_load(code, "app.py") == []


def test_fix_preserves_an_assignment_target():
    code = "import yaml\n\ndef load(raw):\n    config = yaml.load(raw)\n    return config\n"
    findings = detect_unsafe_yaml_load(code, "app.py")
    assert findings[0].fix == "    config = yaml.safe_load(raw)"


def test_fix_is_syntactically_valid_when_applied():
    code = "import yaml\n\ndef load(raw):\n    config = yaml.load(raw)\n    return config\n"
    findings = detect_unsafe_yaml_load(code, "app.py")
    lines = code.splitlines()
    lines[findings[0].line - 1] = findings[0].fix
    patched = "\n".join(lines)
    ast.parse(patched)  # must not raise
