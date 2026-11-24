"""Terminal input editing and slash-command completion."""

import sys
import shutil
import json
import threading
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.mouse_events import MouseButton, MouseEventType
from prompt_toolkit.application import Application
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.application import run_in_terminal
from .security import redact_artifact


COMMANDS = {
    "/help": "Show available commands",
    "/memory": "Show working memory",
    "/session": "Show saved session path",
    "/tools": "View full tool commands and history",
    "/reset": "Clear session history and memory",
    "/compact": "Compact conversation history into a checkpoint",
    "/exit": "Exit Robin",
    "/quit": "Exit Robin",
}


def approval_preview(args):
    """缩短展示参数，保留实际执行参数不变。"""
    def shorten(value):
        if isinstance(value, str):
            return value[:20] + "..." if len(value) > 20 else value
        if isinstance(value, dict):
            return {key: shorten(item) for key, item in value.items()}
        if isinstance(value, list):
            return [shorten(item) for item in value]
        return value
    return json.dumps(shorten(args), ensure_ascii=False)


_activity_lock = threading.Lock()
_activity_status = ContextVar("activity_status", default=None)


class ApprovalPrompt:
    def __init__(self, name, args):
        self.name = name
        self.preview = approval_preview(args)
        self.full = json.dumps(args, ensure_ascii=False)
        self.expandable = name == "run_shell" and self.preview != self.full
        self.expanded = False

    def toggle(self, event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            self.expanded = not self.expanded
            from prompt_toolkit.application import get_app
            get_app().invalidate()

    def fragments(self):
        parts = []
        if self.expandable:
            marker = "[-]" if self.expanded else "[+]"
            parts.append(("ansicyan bold", marker, self.toggle))
            parts.append(("", " "))
        details = self.full if self.expanded else self.preview
        parts.append(("", f"approve {self.name} {details}? [y/N] "))
        return parts


def read_approval(name, args):
    prompt = ApprovalPrompt(name, args)
    if sys.stdin.isatty() and sys.stdout.isatty():
        return build_approval_application(prompt).run()
    # 管道或无鼠标终端输入时展示完整参数，避免隐藏审批内容。
    return input(f"approve {name} {prompt.full}? [y/N] ")


def build_approval_application(prompt, **kwargs):
    """独立文本控件保留鼠标回调，不能使用 PromptSession 的行前缀。"""
    answer = TextArea(height=1, prompt="> ", multiline=False)
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event):
        event.app.exit(result=answer.text)

    @bindings.add("c-c")
    @bindings.add("c-d")
    def cancel(event):
        event.app.exit(result="n")

    return Application(
        layout=Layout(HSplit([
            Window(FormattedTextControl(prompt.fragments), wrap_lines=True, dont_extend_height=True),
            answer,
        ]), focused_element=answer),
        key_bindings=bindings,
        mouse_support=True,
        full_screen=False,
        **kwargs,
    )


@contextmanager
def tool_activity(name, *, label=None, keep_result=True, args=None):
    """仅在交互终端显示动画；嵌套 delegate 共用外层提示。"""
    stream = sys.stderr
    if not stream.isatty() or not _activity_lock.acquire(blocking=False):
        yield
        return
    stopped = threading.Event()
    details = f" {approval_preview(args)}" if args is not None else ""
    label = label or f"Running {name}{details}..."
    failed = False
    status_callback = _activity_status.get()
    drawn_rows = 0
    previous_lines = []
    footer_lines = {}
    anchored = False

    def clear():
        nonlocal drawn_rows
        if anchored:
            output = ["\x1b8"]
            for row in footer_lines:
                if row <= shutil.get_terminal_size().lines:
                    output.append(f"\x1b[{row};1H\x1b[2K")
            output.append("\x1b8\r\x1b[2K")
            stream.write("".join(output))
            return
        output = []
        for index in range(drawn_rows):
            output.append("\r\x1b[2K")
            if index < drawn_rows - 1:
                output.append("\x1b[1A")
        stream.write("".join(output))
        drawn_rows = 0

    def draw(frame):
        nonlocal drawn_rows, previous_lines, anchored, footer_lines
        size = shutil.get_terminal_size()
        width = max(1, size.columns - 1)
        line = f"{frame} {label}"
        if get_cwidth(line) > width:
            while line and get_cwidth(line + "...") > width:
                line = line[:-1]
            line += "..." if width >= 3 else ""
        lines = [line]
        if status_callback:
            lines.extend(render_toolbar(status_callback, width, include_hints=False).splitlines())
            if size.lines >= 4:
                status = lines[1:][:size.lines - 3]
                output = []
                if not anchored:
                    # 先预留底栏和间隔；接近屏幕底部时由终端自然滚动对话。
                    reserve = len(status) + 1
                    output.append("\r\n" * reserve + f"\x1b[{reserve}A\r\x1b7")
                    anchored = True
                output.append("\x1b8\r" + line + "\x1b[K")
                current_footer = dict(zip(range(size.lines - len(status) + 1, size.lines + 1), status))
                for row in footer_lines.keys() - current_footer.keys():
                    if row <= size.lines:
                        output.append(f"\x1b[{row};1H\x1b[2K")
                for row, text in current_footer.items():
                    if footer_lines.get(row) != text:
                        output.append(f"\x1b[{row};1H{text}\x1b[K")
                output.append("\x1b8")
                stream.write("".join(output))
                footer_lines = current_footer
                stream.flush()
                return
            if anchored:
                clear()
                anchored = False
                footer_lines = {}
                previous_lines = []
                drawn_rows = 0
        lines = lines[:max(1, shutil.get_terminal_size().lines - 1)]
        if len(lines) == drawn_rows:
            # 光标停留在最后一行，只更新变化的行，避免 SSH 中反复清空底栏。
            output = [f"\x1b[{drawn_rows - 1}A" if drawn_rows > 1 else ""]
            for index, (old, new) in enumerate(zip(previous_lines, lines)):
                if old != new:
                    output.append("\r" + new + "\x1b[K")
                if index < drawn_rows - 1:
                    output.append("\x1b[1B")
            stream.write("".join(output))
        else:
            clear()
            stream.write("\r\x1b[2K" + "\r\n".join(lines))
        drawn_rows = len(lines)
        previous_lines = lines
        stream.flush()

    def animate():
        for_index = 0
        while not stopped.is_set():
            frame = "|/-\\"[for_index % 4]
            draw(frame)
            for_index += 1
            stopped.wait(0.1)

    worker = threading.Thread(target=animate, daemon=True)
    try:
        draw("|")
        worker.start()
        yield
    except BaseException:
        failed = True
        raise
    finally:
        stopped.set()
        worker.join()
        try:
            clear()
            if keep_result:
                stream.write(f"{'Stopped' if failed else 'Finished'} {name}{details}\n")
            stream.flush()
        finally:
            _activity_lock.release()


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command, description in COMMANDS.items():
            if command.startswith(text):
                yield Completion(command, start_position=-len(text), display_meta=description)


def tool_history_text(history):
    """从会话生成可恢复的工具记录，调用 ID 用来关联执行结果。"""
    results = {item.get("tool_call_id"): item for item in history if item.get("role") == "tool"}
    records = []
    for item in history:
        for call in item.get("tool_calls", []):
            function = call.get("function", {})
            arguments = function.get("arguments", "{}")
            try:
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            except (ValueError, TypeError):
                pass
            result = results.get(call.get("id"))
            state = "已有结果" if result is not None else "尚无结果（待执行或已中断）"
            details = json.dumps(redact_artifact(arguments), ensure_ascii=False, indent=2)
            records.append(f"{len(records) + 1}. {function.get('name', 'unknown')} · {state}\n{details}")
    return "\n\n".join(records) or "暂无工具记录"


def build_tool_history_application(history, **kwargs):
    viewer = TextArea(text=tool_history_text(history), read_only=True,
                      scrollbar=True, wrap_lines=True)
    bindings = KeyBindings()

    @bindings.add("escape")
    @bindings.add("q")
    @bindings.add("c-c")
    def close(event):
        event.app.exit()

    return Application(layout=Layout(HSplit([
        Window(FormattedTextControl("工具记录 · 完整参数 · 滚轮/方向键滚动 · Esc/q 返回"), height=1),
        viewer,
    ]), focused_element=viewer), key_bindings=bindings, full_screen=True,
        mouse_support=True, **kwargs)


def show_tool_history(history):
    if sys.stdin.isatty() and sys.stdout.isatty():
        build_tool_history_application(history).run()
    else:
        print(tool_history_text(history))


def build_prompt_session(status_callback=None, history_callback=None, **kwargs):
    bindings = KeyBindings()

    def open_history(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            asyncio.ensure_future(run_in_terminal(lambda: show_tool_history(history_callback()), in_executor=True))

    def toolbar():
        text = render_toolbar(status_callback)
        if history_callback is None:
            return text
        return [("ansicyan bold", "[工具记录]", open_history), ("", " /tools\n" + text)]

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
        bottom_toolbar=toolbar,
        mouse_support=True,
        style=Style.from_dict({"prompt": "ansicyan bold"}),
        **kwargs,
    )


def render_toolbar(status_callback=None, width=None, *, include_hints=True):
    width = width or shutil.get_terminal_size().columns
    lines = list(status_callback()) if status_callback else []
    if width < 90:
        lines = [line.replace("本轮 Token 输入", "入").replace(" / 输出", " 出")
                 .replace("最近请求上下文（估算）", "上下文估算").replace(" token", "")
                 .replace("最近请求上下文（接口）", "上下文接口")
                 .replace("会话累计", "累计").replace("缓存读取", "缓存")
                 .replace(" / 命中率", " 命中").replace(" / 等级", " 等级") for line in lines]
    if include_hints:
        lines.append("/ commands | Up/Down select | Tab complete | Enter send | Ctrl-D exit")
    result = []
    for line in lines:
        if get_cwidth(line) > width:
            while get_cwidth(line) > max(0, width - 1):
                line = line[:-1]
            line += "…"
        result.append(line)
    return "\n".join(result)


class TerminalInput:
    def __init__(self, status_callback=None, history_callback=None):
        self.status_callback = status_callback
        self.session = build_prompt_session(status_callback=status_callback, history_callback=history_callback) if sys.stdin.isatty() and sys.stdout.isatty() else None

    @contextmanager
    def activity(self):
        token = _activity_status.set(self.status_callback if self.session is not None else None)
        try:
            yield
        finally:
            _activity_status.reset(token)

    def read(self):
        if self.session is None:
            return input("\nrobin> ")
        return self.session.prompt([("class:prompt", "\nrobin> ")])
