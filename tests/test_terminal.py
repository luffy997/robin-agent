import asyncio
import io
import json
import threading

import pytest

from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from robin.terminal import SlashCompleter, build_prompt_session
from robin.terminal import approval_preview, tool_activity
from robin.terminal import ApprovalPrompt, read_approval, build_approval_application
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
from prompt_toolkit.data_structures import Point


def test_click_expands_and_collapses_shell_command():
    command = "echo " + "full command " * 20
    prompt = ApprovalPrompt("run_shell", {"command": command})
    def click():
        fragments = prompt.fragments()
        fragments[0][2](MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
    assert prompt.fragments()[0][1] == "[+]"
    assert command not in prompt.fragments()[-1][1]
    click()
    assert prompt.fragments()[0][1] == "[-]"
    assert command in prompt.fragments()[-1][1]
    click()
    assert prompt.fragments()[0][1] == "[+]"
    assert command not in prompt.fragments()[-1][1]


def test_rendered_approval_mouse_target_expands_without_submitting():
    async def scenario():
        prompt = ApprovalPrompt("run_shell", {"command": "echo " + "long command " * 20})
        with create_pipe_input() as pipe:
            app = build_approval_application(prompt, input=pipe, output=DummyOutput())
            def after_render(application):
                try:
                    handler = application.renderer.mouse_handlers.mouse_handlers[0][1]
                    handler(MouseEvent(Point(1, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
                    assert prompt.expanded
                    assert not application.is_done
                    pipe.send_text("y\r")
                except BaseException as exc:
                    application.exit(exception=exc)
                finally:
                    application.after_render -= after_render
            app.after_render += after_render
            assert await asyncio.wait_for(app.run_async(), 3) == "y"
    asyncio.run(scenario())


def test_interactive_approval_enables_mouse(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    factory = MagicMock()
    factory.return_value.prompt.return_value = "y"
    monkeypatch.setattr("robin.terminal.build_approval_application", factory)
    factory.return_value.run.return_value = "y"
    assert read_approval("run_shell", {"command": "x" * 50}) == "y"
    assert isinstance(factory.call_args.args[0], ApprovalPrompt)


def test_approval_preview_shortens_values_without_changing_arguments():
    args = {"command": "echo " + "x" * 100, "content": "中" * 30, "timeout": 30}
    preview = json.loads(approval_preview(args))
    assert preview["command"] == args["command"][:20] + "..."
    assert preview["content"] == "中" * 20 + "..."
    assert preview["timeout"] == 30
    assert len(args["content"]) == 30


def test_tool_history_keeps_full_commands_and_matches_results_by_id(monkeypatch):
    from robin.terminal import tool_history_text
    monkeypatch.setenv("ROBIN_API_KEY", "test-secret-token")
    command = "echo " + "long argument " * 40 + "test-secret-token"
    history = [
        {"role": "assistant", "tool_calls": [
            {"id": "done", "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}},
            {"id": "pending", "function": {"name": "run_shell", "arguments": '{"command": "pwd"}'}},
        ]},
        {"role": "tool", "tool_call_id": "done", "content": "exit_code: 0"},
    ]
    text = tool_history_text(history)
    assert "long argument " * 40 in text
    assert "test-secret-token" not in text
    assert "已有结果" in text
    assert "尚无结果" in text
    assert "pwd" in text


def test_tool_history_viewer_is_read_only_and_can_close():
    from robin.terminal import build_tool_history_application
    with create_pipe_input() as pipe:
        app = build_tool_history_application([], input=pipe, output=DummyOutput())
        assert app.full_screen
        assert app.layout.current_buffer.read_only()
        pipe.send_text("q")
        app.run()


def test_toolbar_mouse_opens_current_history(monkeypatch):
    async def scenario():
        seen = []
        monkeypatch.setattr("robin.terminal.show_tool_history", lambda history: seen.append(history))
        with create_pipe_input() as pipe:
            history = []
            session = build_prompt_session(history_callback=lambda: history, input=pipe, output=DummyOutput())
            history.append({"role": "user", "content": "latest"})
            click = session.bottom_toolbar()[0][2]
            click(MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
            for _ in range(100):
                if seen:
                    break
                await asyncio.sleep(0.01)
            assert seen == [history]
    asyncio.run(scenario())


def test_tool_activity_preserves_command_preview_after_completion(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    stream = Terminal()
    monkeypatch.setattr("sys.stderr", stream)
    args = {"command": "echo " + "x" * 100}
    with tool_activity("run_shell", args=args):
        assert approval_preview(args) in stream.getvalue()
    assert stream.getvalue().endswith(f"Finished run_shell {approval_preview(args)}\n")


def test_tool_activity_cleans_up_after_failure_and_skips_nested(monkeypatch):
    rendered = threading.Event()
    class Terminal(io.StringIO):
        def isatty(self):
            return True
        def write(self, value):
            result = super().write(value)
            if "Running" in value:
                rendered.set()
            return result
    stream = Terminal()
    monkeypatch.setattr("sys.stderr", stream)
    with pytest.raises(ValueError):
        with tool_activity("run_shell"):
            assert rendered.wait(2)
            with tool_activity("read_file"):
                pass
            raise ValueError("failed")
    assert "Running run_shell" in stream.getvalue()
    assert "read_file" not in stream.getvalue()
    assert stream.getvalue().endswith("Stopped run_shell\n")
    rendered.clear()
    with tool_activity("write_file"):
        assert rendered.wait(2)
    assert "Running write_file" in stream.getvalue()
    assert stream.getvalue().endswith("Finished write_file\n")


def test_tool_activity_non_terminal_is_silent(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    with tool_activity("run_shell"):
        pass
    assert stream.getvalue() == ""


def test_activity_stays_visible_until_operation_completes(monkeypatch):
    updated = threading.Event()
    class Terminal(io.StringIO):
        def isatty(self):
            return True
        def write(self, value):
            result = super().write(value)
            if "/ Waiting" in value:
                updated.set()
            return result
    stream = Terminal()
    monkeypatch.setattr("sys.stderr", stream)
    with tool_activity("model", label="Waiting for model response...", keep_result=False):
        assert "Waiting for model response..." in stream.getvalue()
        assert updated.wait(2)
        assert not stream.getvalue().endswith("\r\x1b[2K")
    assert stream.getvalue().endswith("\r\x1b[2K")
    assert "Finished model" not in stream.getvalue()


def test_toolbar_reads_current_status():
    status = ["before"]
    with create_pipe_input() as pipe:
        session = build_prompt_session(status_callback=lambda: status, input=pipe, output=DummyOutput())
        assert "before" in session.bottom_toolbar()
        status[0] = "after"
        assert "after" in session.bottom_toolbar()


def test_running_status_refreshes_for_models_and_tools_and_cleans_up(monkeypatch):
    from robin.terminal import TerminalInput
    refreshed = threading.Event()
    class Terminal(io.StringIO):
        def isatty(self):
            return True

        def write(self, value):
            result = super().write(value)
            if "updated usage" in value:
                refreshed.set()
            return result

    stream = Terminal()
    monkeypatch.setattr("sys.stderr", stream)
    terminal = TerminalInput(status_callback=lambda: status)
    # 不启动真实输入应用，只验证交互模式的运行状态生命周期。
    terminal.session = object()
    status = ["initial usage", "cache status"]
    with pytest.raises(ValueError), terminal.activity():
        with tool_activity("model", keep_result=False):
            assert "initial usage" in stream.getvalue()
            assert "cache status" in stream.getvalue()
            status[0] = "updated usage"
            assert refreshed.wait(2)
        with tool_activity("run_shell"):
            assert stream.getvalue().count("updated usage") >= 2
            raise ValueError("failed")
    assert "\x1b8" in stream.getvalue()
    assert stream.getvalue().endswith("Stopped run_shell\n")
    before = stream.getvalue()
    with tool_activity("model", keep_result=False):
        pass
    assert "usage" not in stream.getvalue()[len(before):]


def test_noninteractive_activity_does_not_show_status(monkeypatch):
    from robin.terminal import TerminalInput
    monkeypatch.setattr("sys.stdin", io.StringIO())
    terminal = TerminalInput(status_callback=lambda: ["tokens"])
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    with terminal.activity(), tool_activity("model"):
        pass
    assert stream.getvalue() == ""


def test_activity_animation_does_not_clear_or_repaint_unchanged_status(monkeypatch):
    from robin.terminal import TerminalInput
    animated = threading.Event()

    class Terminal(io.StringIO):
        def isatty(self):
            return True

        def write(self, value):
            result = super().write(value)
            if "/ Waiting" in value:
                animated.set()
            return result

    stream = Terminal()
    monkeypatch.setattr("sys.stderr", stream)
    terminal = TerminalInput(status_callback=lambda: ["Stable usage", "Stable cache"])
    terminal.session = object()
    with terminal.activity(), tool_activity("model", label="Waiting", keep_result=False):
        assert animated.wait(2)
        output = stream.getvalue()
        assert output.count("Stable usage") == 1
        assert output.count("Stable cache") == 1
        assert output.count("\x1b[2K") == 0
        assert "\x1b[K" in output
    assert stream.getvalue().endswith("\r\x1b[2K")


def test_activity_footer_anchors_to_bottom_and_follows_resize(monkeypatch):
    import os
    from robin.terminal import TerminalInput
    updated = threading.Event()
    size = [os.terminal_size((100, 30))]

    class Terminal(io.StringIO):
        def isatty(self):
            return True

        def write(self, value):
            result = super().write(value)
            if "\x1b[39;1HUsage" in value:
                updated.set()
            return result

    stream = Terminal()
    monkeypatch.setattr("sys.stderr", stream)
    monkeypatch.setattr("robin.terminal.shutil.get_terminal_size", lambda: size[0])
    terminal = TerminalInput(status_callback=lambda: ["Usage", "Context"])
    terminal.session = object()
    with terminal.activity(), tool_activity("model", keep_result=False):
        assert "\x1b[29;1HUsage\x1b[K\x1b[30;1HContext" in stream.getvalue()
        assert stream.getvalue().startswith("\r\n" * 3 + "\x1b[3A")
        size[0] = os.terminal_size((100, 40))
        assert updated.wait(2)
        assert "\x1b[29;1H\x1b[2K" in stream.getvalue()
        assert "\x1b[40;1HContext" in stream.getvalue()
    assert "\x1b[40;1H\x1b[2K" in stream.getvalue()


def test_slash_menu_filters_commands():
    completer = SlashCompleter()
    assert [c.text for c in completer.get_completions(Document("/"), None)] == [
        "/help", "/memory", "/session", "/tools", "/reset", "/compact", "/exit", "/quit",
    ]
    assert [c.text for c in completer.get_completions(Document("/se"), None)] == ["/session"]
    assert list(completer.get_completions(Document("explain /help"), None)) == []


def test_terminal_handles_both_backspace_codes():
    with create_pipe_input() as pipe:
        session = build_prompt_session(input=pipe, output=DummyOutput())
        pipe.send_text("abc\x08\x7fd\r")
        assert session.prompt() == "ad"


def test_slash_opens_menu_and_selected_command_submits():
    async def interact():
        with create_pipe_input() as pipe:
            session = build_prompt_session(input=pipe, output=DummyOutput())
            task = asyncio.create_task(session.prompt_async())
            pipe.send_text("/")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if session.default_buffer.complete_state is not None:
                    break
            state = session.default_buffer.complete_state
            assert state is not None
            assert state.completions[0].text == "/help"
            pipe.send_text("\x1b[B\r")
            assert await asyncio.wait_for(task, timeout=2) == "/help"

    asyncio.run(interact())
