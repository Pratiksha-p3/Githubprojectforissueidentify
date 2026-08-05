from src.analyzers.infinite_recursion_checker import detect_unconditional_self_recursion


def test_flags_a_function_that_unconditionally_calls_itself():
    code = "def recurse():\n    recurse()\n"
    findings = detect_unconditional_self_recursion(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only -- correct fix isn't derivable
    assert findings[0].severity.value == "critical"
    assert "recurse" in findings[0].message


def test_flags_a_function_with_other_unconditional_statements_before_the_call():
    """Still unconditional -- print(n) always runs, then foo always calls
    itself, with no if/return anywhere to ever stop it."""
    code = "def foo(n):\n    print(n)\n    foo(n + 1)\n"
    findings = detect_unconditional_self_recursion(code, "app.py")
    assert len(findings) == 1


def test_skips_a_function_with_an_if_guarding_the_recursive_call():
    """The real, common shape of correct recursion -- a base case."""
    code = "def countdown(n):\n    if n <= 0:\n        return\n    countdown(n - 1)\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_skips_a_function_with_a_return_anywhere_in_its_body():
    code = "def f(n):\n    result = n\n    return result\n    f(n)\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_skips_a_function_with_a_while_loop():
    code = "def f():\n    while True:\n        f()\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_skips_a_function_with_a_try_except():
    code = "def f():\n    try:\n        f()\n    except Exception:\n        pass\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_skips_a_non_recursive_function():
    code = "def f():\n    print('hello')\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_skips_a_decorated_function():
    """A decorator could rewrite the call entirely (e.g. memoization) --
    not reasoned about."""
    code = "@some_decorator\ndef recurse():\n    recurse()\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_skips_a_generator_function():
    """yield changes when the body even runs -- a generator's body
    doesn't execute until iterated, so this pattern doesn't guarantee
    infinite recursion the same way."""
    code = "def gen():\n    yield 1\n    yield from gen()\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_ignores_an_if_inside_an_unrelated_nested_function():
    """The if lives in a DIFFERENT function's scope -- must not wrongly
    clear the outer function just because ast.walk() would otherwise see
    it as a descendant node."""
    code = (
        "def recurse():\n"
        "    def helper():\n"
        "        if True:\n"
        "            return 1\n"
        "    recurse()\n"
    )
    findings = detect_unconditional_self_recursion(code, "app.py")
    assert len(findings) == 1


def test_does_not_flag_a_call_to_a_different_function_with_the_same_name_elsewhere():
    code = "def f():\n    g()\n\ndef g():\n    if True:\n        return\n    g()\n"
    assert detect_unconditional_self_recursion(code, "app.py") == []


def test_flags_an_async_function_too():
    code = "async def recurse():\n    await recurse()\n"
    findings = detect_unconditional_self_recursion(code, "app.py")
    assert len(findings) == 1


def test_returns_empty_for_a_syntax_error_file():
    assert detect_unconditional_self_recursion("def broken(:\n    pass\n", "app.py") == []
