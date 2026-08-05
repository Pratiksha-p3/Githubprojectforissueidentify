from src.analyzers.invalid_method_checker import detect_invalid_method_calls


def test_flags_append_on_a_string_literal():
    findings = detect_invalid_method_calls('"hello".append("x")\n', "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only -- correct fix isn't derivable
    assert findings[0].severity.value == "critical"


def test_skips_append_on_a_list_literal():
    assert detect_invalid_method_calls("[1, 2].append(3)\n", "app.py") == []


def test_skips_a_real_string_method():
    assert detect_invalid_method_calls('"hello".upper()\n', "app.py") == []


def test_flags_a_made_up_string_method():
    findings = detect_invalid_method_calls('"hello".push("x")\n', "app.py")
    assert len(findings) == 1
    assert "push" in findings[0].message


def test_flags_append_on_a_tuple_literal():
    """Tuples are immutable -- no append, no mutation methods at all."""
    findings = detect_invalid_method_calls("(1, 2).append(3)\n", "app.py")
    assert len(findings) == 1


def test_flags_append_on_a_dict_literal():
    findings = detect_invalid_method_calls("{}.append(1)\n", "app.py")
    assert len(findings) == 1


def test_flags_append_on_a_set_literal():
    """Sets use .add(), not .append()."""
    findings = detect_invalid_method_calls("{1, 2}.append(3)\n", "app.py")
    assert len(findings) == 1


def test_skips_add_on_a_set_literal():
    assert detect_invalid_method_calls("{1, 2}.add(3)\n", "app.py") == []


def test_flags_a_single_assignment_variable():
    code = 's = "hello"\ns.append("x")\n'
    findings = detect_invalid_method_calls(code, "app.py")
    assert len(findings) == 1
    assert "'s'" in findings[0].message


def test_ignores_a_variable_reassigned_elsewhere():
    """Deliberately conservative: a name assigned more than once anywhere
    is dropped entirely rather than risk pairing a stale type with a
    later, differently-sourced use."""
    code = 's = "hello"\ns = get_thing()\ns.append("x")\n'
    assert detect_invalid_method_calls(code, "app.py") == []


def test_ignores_unknown_types_like_function_parameters():
    code = "def f(x):\n    x.append(1)\n"
    assert detect_invalid_method_calls(code, "app.py") == []


def test_ignores_a_function_calls_return_value():
    code = "get_thing().append(1)\n"
    assert detect_invalid_method_calls(code, "app.py") == []


def test_returns_empty_for_a_syntax_error_file():
    assert detect_invalid_method_calls("def broken(:\n    pass\n", "app.py") == []
