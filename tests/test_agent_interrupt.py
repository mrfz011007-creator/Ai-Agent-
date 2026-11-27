from __future__ import annotations

import agent


def test_main_handles_keyboard_interrupt_during_request(monkeypatch, capsys):
    monkeypatch.setattr(agent, "get_runtime", lambda: object())
    monkeypatch.setattr("builtins.input", lambda _prompt: "run a request")

    def interrupt_request(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(agent, "handle_request", interrupt_request)

    agent.main()

    assert "Agent berhenti." in capsys.readouterr().out


def test_main_handles_exit_command(monkeypatch, capsys):
    monkeypatch.setattr(agent, "get_runtime", lambda: object())
    monkeypatch.setattr("builtins.input", lambda _prompt: "exit")

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("exit must not be routed as a model request")

    monkeypatch.setattr(agent, "handle_request", unexpected_request)

    agent.main()

    assert "Agent berhenti." in capsys.readouterr().out
