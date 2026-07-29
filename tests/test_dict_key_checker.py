from src.analyzers.dict_key_checker import detect_unguarded_dict_access


def test_flags_unguarded_literal_key_access_on_parameter():
    code = "def handler(payload):\n    return payload['user_id']\n"
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    assert "user_id" in findings[0].message


def test_consolidates_multiple_keys_on_same_param_into_one_finding():
    code = (
        "def handler(payload):\n"
        "    a = payload['x']\n"
        "    b = payload['y']\n"
        "    return a, b\n"
    )
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    assert "x" in findings[0].message and "y" in findings[0].message


def test_skips_when_guarded_by_in_check():
    code = (
        "def handler(payload):\n"
        "    if 'user_id' not in payload:\n"
        "        raise KeyError('missing')\n"
        "    return payload['user_id']\n"
    )
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_skips_when_guarded_by_get():
    code = "def handler(payload):\n    x = payload.get('user_id')\n    return payload['user_id']\n"
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_ignores_dynamic_key():
    code = "def handler(payload, key):\n    return payload[key]\n"
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_ignores_non_parameter_dict():
    code = "def handler():\n    d = {'x': 1}\n    return d['x']\n"
    assert detect_unguarded_dict_access(code, "app.py") == []
