from src.cli.explain import explain, main


def test_explain_known_category_returns_zero(capsys):
    code = explain("KeyError")
    out = capsys.readouterr().out
    assert code == 0
    assert "KeyError" in out
    assert "dict.get" in out


def test_explain_unknown_category_returns_one_with_suggestions(capsys):
    code = explain("keyerorr")
    out = capsys.readouterr().out
    assert code == 1
    assert "isn't in the remediation catalog" in out
    assert "KeyError" in out  # close-match suggestion


def test_main_with_no_args_lists_everything(capsys):
    code = main([])
    out = capsys.readouterr().out
    assert code == 0
    assert "KeyError" in out
    assert "SQL Injection" in out


def test_main_with_list_flag_lists_everything(capsys):
    code = main(["--list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "KeyError" in out


def test_main_with_name_delegates_to_explain(capsys):
    code = main(["ZeroDivisionError"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ZeroDivisionError" in out
