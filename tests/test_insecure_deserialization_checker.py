from src.analyzers.insecure_deserialization_checker import detect_insecure_deserialization


def test_flags_pickle_load():
    code = "import pickle\n\ndef load(f):\n    return pickle.load(f)\n"
    findings = detect_insecure_deserialization(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only, by design


def test_flags_pickle_loads():
    code = "import pickle\n\ndef load(data):\n    return pickle.loads(data)\n"
    findings = detect_insecure_deserialization(code, "app.py")
    assert len(findings) == 1


def test_ignores_file_without_pickle_import():
    code = "def load(f):\n    return pickle.load(f)\n"
    assert detect_insecure_deserialization(code, "app.py") == []


def test_ignores_unrelated_load_calls():
    code = "import pickle\nimport json\n\ndef load(f):\n    return json.load(f)\n"
    assert detect_insecure_deserialization(code, "app.py") == []
