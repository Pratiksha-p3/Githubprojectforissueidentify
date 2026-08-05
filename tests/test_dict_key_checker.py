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


def test_flags_unguarded_key_access_on_a_direct_json_call_result():
    code = (
        "def handler(response):\n"
        "    data = response.json()\n"
        "    return data['user_id']\n"
    )
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    assert "response doesn't include it" in findings[0].message


def test_flags_unguarded_key_access_via_a_same_file_json_wrapper_function():
    """The real-world shape this was built for: a local helper that
    wraps the actual .json() call, then a caller elsewhere in the file
    treats its result as an ordinary dict with no guard -- confirmed
    against a real bug (`data = fetch_data(url); data["address"]["city"]`
    raised KeyError in practice)."""
    code = (
        "def fetch_data(url):\n"
        "    response = requests.get(url, timeout=10)\n"
        "    return response.json()\n\n"
        "def main():\n"
        "    data = fetch_data('http://x')\n"
        "    print(data['address']['city'])\n"
    )
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    assert findings[0].line == 6  # anchored at the assignment, not the def line
    assert "data = fetch_data" in findings[0].fix


def test_json_derived_local_fix_applies_after_the_assignment_not_the_def_line():
    """A .json()-derived local doesn't exist until its assignment runs --
    inserting its guard at the top of the function (like a parameter's)
    would reference the name before it's bound."""
    code = (
        "def handler(response):\n"
        "    x = 1\n"
        "    data = response.json()\n"
        "    return data['key']\n"
    )
    findings = detect_unguarded_dict_access(code, "app.py")
    assert findings[0].line == 3
    assert findings[0].fix.startswith("    data = response.json()")


def test_json_derived_local_fix_spans_a_multi_line_assignment_correctly():
    """Regression: a multi-line assignment (`data = fetch_data(\\n
    url\\n)`) previously got its guard spliced in after just the FIRST
    line, truncating the call mid-expression -- produced invalid Python
    that src/core/grounding.py's is_valid_fix() correctly rejected, but
    that meant the whole finding silently vanished instead of being
    fixed. The fix must span the assignment's full range."""
    import ast

    code = (
        "def fetch_data(url):\n"
        "    response = requests.get(url, timeout=10)\n"
        "    return response.json()\n\n"
        "def main():\n"
        "    data = fetch_data(\n"
        '        "http://x"\n'
        "    )\n"
        "    print(data['address'])\n"
    )
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.line == 6
    assert finding.end_line == 8

    lines = code.splitlines()
    lines[finding.line - 1 : finding.end_line] = finding.fix.splitlines()
    patched = "\n".join(lines)
    ast.parse(patched)  # must not raise
    assert "fetch_data(" in patched and '"http://x"' in patched


def test_skips_json_derived_local_when_guarded():
    code = (
        "def handler(response):\n"
        "    data = response.json()\n"
        "    if 'user_id' not in data:\n"
        "        raise KeyError('missing')\n"
        "    return data['user_id']\n"
    )
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_flags_a_direct_dict_literal_missing_a_key():
    code = "print({'a': 1, 'b': 2}['c'])\n"
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only -- correct fix isn't derivable
    assert findings[0].severity.value == "critical"
    assert "'c'" in findings[0].message


def test_skips_a_direct_dict_literal_with_a_present_key():
    code = "print({'a': 1, 'b': 2}['a'])\n"
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_flags_a_single_assignment_variable_missing_a_key():
    """The shape this was actually built for: `d = {"a": 1}` followed by
    `d["missing"]` elsewhere -- indexing through a variable, not the
    literal directly."""
    code = "d = {'a': 1}\nprint(d['missing'])\n"
    findings = detect_unguarded_dict_access(code, "app.py")
    assert len(findings) == 1
    assert "'d'" in findings[0].message
    assert "'missing'" in findings[0].message


def test_skips_a_tracked_variable_with_a_present_key():
    code = "d = {'a': 1}\nprint(d['a'])\n"
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_ignores_a_variable_reassigned_elsewhere():
    """Deliberately conservative: a name assigned more than once anywhere
    is dropped entirely rather than risk pairing a stale key set with a
    later, differently-sourced use."""
    code = "d = {'a': 1}\nd = get_dict()\nprint(d['missing'])\n"
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_ignores_a_dict_literal_with_a_dynamic_key():
    code = "extra = {}\nprint({**extra, 'a': 1}['b'])\n"
    assert detect_unguarded_dict_access(code, "app.py") == []


def test_ignores_a_variable_key_on_a_literal():
    """A variable key can't be statically verified missing or present --
    it depends on what value the variable holds at that point, which
    this project has no dataflow analysis to determine."""
    code = "key = 'x'\nprint({'a': 1}[key])\n"
    assert detect_unguarded_dict_access(code, "app.py") == []
