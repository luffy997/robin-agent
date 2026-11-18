"""Terminal input editing and slash-command completion."""

import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

COMMANDS = {
    "/help": "Show available commands",
    "/memory": "Show working memory",
    "/session": "Show saved session path",
    "/reset": "Clear session history and memory",
    "/exit": "Exit Robin",
    "/quit": "Exit Robin",
}


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command, description in COMMANDS.items():
            if command.startswith(text):
                yield Completion(command, start_position=-len(text), display_meta=description)


def build_prompt_session(**kwargs):
    bindings = KeyBindings()

    # Terminals send either Ctrl-H or DEL for the backspace key.
    @bindings.add("c-h")
    @bindings.add("backspace")
    def delete_before_cursor(event):
        event.current_buffer.delete_before_cursor()

    @bindings.add("enter")
    def submit(event):
        buffer = event.current_buffer
        if buffer.complete_state is not None:
            completion = buffer.complete_state.current_completion
            if completion is not None:
                buffer.apply_completion(completion)
            else:
                buffer.cancel_completion()
        buffer.validate_and_handle()

    return PromptSession(
        completer=SlashCompleter(),
        complete_while_typing=True,
        key_bindings=bindings,
        reserve_space_for_menu=6,
        bottom_toolbar=" / commands | Up/Down select | Tab complete | Enter send | Ctrl-D exit",
        style=Style.from_dict({"prompt": "ansicyan bold"}),
        **kwargs,
    )


class TerminalInput:
    def __init__(self):
        self.session = build_prompt_session() if sys.stdin.isatty() and sys.stdout.isatty() else None

    def read(self):
        if self.session is None:
            return input("\nrobin> ")
        return self.session.prompt([("class:prompt", "\nrobin> ")])
