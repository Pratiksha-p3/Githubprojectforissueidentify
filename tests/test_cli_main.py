from src.cli import main as main_module


def test_main_calls_resolve_secrets_before_dispatching(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "resolve_secrets", lambda settings_obj: calls.append(1))
    monkeypatch.setattr(main_module.sys, "argv", ["review-cli", "--help"])

    main_module.main()

    assert calls == [1]


def test_main_help_lists_all_commands(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "resolve_secrets", lambda settings_obj: None)
    monkeypatch.setattr(main_module.sys, "argv", ["review-cli", "--help"])

    main_module.main()

    out = capsys.readouterr().out
    assert "analyze" in out
    assert "review" in out
    assert "index" in out


def test_main_unknown_command_returns_nonzero(monkeypatch):
    monkeypatch.setattr(main_module, "resolve_secrets", lambda settings_obj: None)
    monkeypatch.setattr(main_module.sys, "argv", ["review-cli", "not-a-real-command"])

    assert main_module.main() == 1
