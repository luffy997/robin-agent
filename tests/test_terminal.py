import asyncio

from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from robin.terminal import SlashCompleter, build_prompt_session


def test_toolbar_reads_current_status():
    status = ["before"]
    with create_pipe_input() as pipe:
        session = build_prompt_session(status_callback=lambda: status, input=pipe, output=DummyOutput())
        assert "before" in session.bottom_toolbar()
        status[0] = "after"
        assert "after" in session.bottom_toolbar()


def test_slash_menu_filters_commands():
    completer = SlashCompleter()
    assert [c.text for c in completer.get_completions(Document("/"), None)] == [
        "/help", "/memory", "/session", "/reset", "/exit", "/quit",
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
