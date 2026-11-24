"""交互退出后提供带工作区和 session ID 的恢复命令。"""

import shlex
from contextlib import nullcontext

import pytest

from robin import cli
from test_agent_loop import build_agent


@pytest.mark.parametrize("exit_action", ["/exit", "/quit", EOFError, KeyboardInterrupt, "busy_interrupt"])
def test_exit_prints_resume_command(tmp_path, monkeypatch, capsys, exit_action):
    agent = build_agent(tmp_path, [])
    monkeypatch.setattr(cli, "build_agent", lambda args: agent)
    monkeypatch.setattr(cli, "build_welcome", lambda *args, **kwargs: "welcome")

    class Terminal:
        def __init__(self, **kwargs):
            pass

        def read(self):
            if isinstance(exit_action, type):
                raise exit_action()
            return "hello" if exit_action == "busy_interrupt" else exit_action

        def activity(self):
            return nullcontext()

    def interrupt(message):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "TerminalInput", Terminal)
    monkeypatch.setattr(agent, "ask", interrupt)
    assert cli.main([]) == 0
    output = capsys.readouterr().out
    assert output.count("恢复此会话") == 1
    command = shlex.split(output.strip().splitlines()[-1])
    assert command[command.index("--resume") + 1] == agent.session["id"]
    assert command[command.index("--cwd") + 1] == str(agent.root)
