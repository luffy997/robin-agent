"""命令行入口。

这个模块负责把“用户怎么启动 robin”翻译成 runtime 能理解的对象：
解析参数、挑模型后端、构建工作区快照、恢复或新建 session，
最后进入 one-shot 或交互式循环。
"""

import argparse
import os
import shutil
import shlex
import sys
import textwrap
from pathlib import Path

# 支持直接运行 `python robin/cli.py`（或 IDE 里右键 Run）：直接运行脚本时
# `__package__` 为空，Python 只会把脚本所在目录加进 sys.path，找不到 robin 包。
# 这里手动把包所在的项目根目录插到最前面，让 `from robin.x import y` 始终可解析。
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robin.config import load_project_env, provider_env
from robin.providers.clients import APIS, AnthropicCompatibleModelClient, OllamaModelClient, OpenAICompatibleModelClient
from robin.runtime import Robin, SessionStore
from robin.providers.limits import model_limits, positive_integer
from robin.providers.context import build_request
from robin.terminal import TerminalInput, show_tool_history
from robin.usage import status_lines
from robin.workspace import WorkspaceContext, middle

DEFAULT_SECRET_ENV_NAMES = (
    "ROBIN_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_TOKEN",
    "ROBIN_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ROBIN_DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY",
    "ROBIN_RIGHT_CODES_API_KEY",
    "RIGHT_CODES_API_KEY",
    "GITHUB_PAT",
    "GH_PAT",
)

WELCOME_ART = (
    "       .-~~~~~-.",
    "     .'         '.",
    "    /             \\",
    "   |===============|",
    "  __\\_____________/__",
    " '-------------------'",
)
WELCOME_NAME = "robin"
WELCOME_SUBTITLE = "lightweight local coding agent"
WELCOME_STATUS = "calm shell, ready for work"
HELP_DETAILS = textwrap.dedent(
    """\
    Commands:
    /help    Show this help message.
    /memory  Show the agent's distilled working memory.
    /session Show the path to the saved session file.
    /tools   View full tool commands and history.
    /compact Compact conversation history into a progress checkpoint.
    /reset   Clear the current session history and memory.
    /exit    Exit the agent.
    """
).strip()
DIRECT_RUN_PROMPT = "请阅读当前工作区，这个项目的memory是怎么做的？"


DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENAI_BASE_URL = "https://www.right.codes/codex/v1"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_ANTHROPIC_BASE_URL = "https://www.right.codes/claude/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_PROVIDER = "deepseek"
PROVIDER_CHOICES = ("ollama", "openai", "anthropic", "deepseek")
SECRET_ENV_NAMES_VAR = "ROBIN_SECRET_ENV_NAMES"


def _effective_provider(args):
    # Provider 选择优先级：
    # 1. 用户显式传入 --provider
    # 2. 项目 .env / shell 里的 ROBIN_PROVIDER
    # 3. 代码里的默认 provider
    """按 CLI、环境变量和默认值确定 provider。"""
    provider = getattr(args, "provider", None) or provider_env(
        "ROBIN_PROVIDER", default=DEFAULT_PROVIDER
    )
    if provider not in PROVIDER_CHOICES:
        choices = ", ".join(PROVIDER_CHOICES)
        raise ValueError(f"unknown provider: {provider}. expected one of: {choices}")
    return provider


def _effective_model(args, provider):
    # 模型选择优先级：
    # 1. 用户显式传入 --model
    # 2. provider 对应的环境变量
    # 3. 代码里的默认值
    """按 CLI、环境变量和默认值确定模型名。"""
    explicit_model = getattr(args, "model", None)
    if explicit_model:
        return explicit_model
    if provider == "openai":
        model = provider_env("ROBIN_OPENAI_MODEL", ("OPENAI_MODEL",))
        if model:
            return model
        return DEFAULT_OPENAI_MODEL
    if provider == "anthropic":
        model = provider_env("ROBIN_ANTHROPIC_MODEL", ("ANTHROPIC_MODEL",))
        if model:
            return model
        return DEFAULT_ANTHROPIC_MODEL
    if provider == "deepseek":
        model = provider_env("ROBIN_DEEPSEEK_MODEL", ("DEEPSEEK_MODEL",))
        if model:
            return model
        return DEFAULT_DEEPSEEK_MODEL
    return DEFAULT_OLLAMA_MODEL


def _configured_secret_names(args):
    """合并 CLI 和环境变量配置的敏感变量名。"""
    configured_secret_names = set(DEFAULT_SECRET_ENV_NAMES)
    configured_secret_names.update(str(name).upper() for name in args.secret_env_names)
    extra_names = os.environ.get(SECRET_ENV_NAMES_VAR, "")
    if extra_names.strip():
        configured_secret_names.update(
            item.strip().upper()
            for item in extra_names.split(",")
            if item.strip()
        )
    return sorted(configured_secret_names)


def _build_model_client(args):
    """根据 provider 配置创建对应的模型客户端。"""
    provider = _effective_provider(args)
    api = getattr(args, "api", None) or provider_env("ROBIN_API")
    options = {"api": api} if api else {}
    # CLI 只负责把 provider 选择翻译成具体 client。
    # 协议差异封装在 provider 适配层。
    if provider == "openai":
        model = _effective_model(args, provider)
        base_url = getattr(args, "base_url", None) or provider_env("ROBIN_OPENAI_API_BASE", ("OPENAI_API_BASE",), DEFAULT_OPENAI_BASE_URL)
        api_key = provider_env(
            "ROBIN_OPENAI_API_KEY",
            ("OPENAI_API_KEY", "ROBIN_RIGHT_CODES_API_KEY", "RIGHT_CODES_API_KEY", "ROBIN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        )
        return OpenAICompatibleModelClient(
            **options,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(args, "openai_timeout", getattr(args, "ollama_timeout", 300)),
        )
    if provider == "anthropic":
        model = _effective_model(args, provider)
        base_url = getattr(args, "base_url", None) or provider_env("ROBIN_ANTHROPIC_API_BASE", ("ANTHROPIC_API_BASE",), DEFAULT_ANTHROPIC_BASE_URL)
        api_key = provider_env(
            "ROBIN_ANTHROPIC_API_KEY",
            ("ANTHROPIC_API_KEY", "ROBIN_RIGHT_CODES_API_KEY", "RIGHT_CODES_API_KEY", "ROBIN_OPENAI_API_KEY", "OPENAI_API_KEY"),
        )
        return AnthropicCompatibleModelClient(
            **options,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(args, "openai_timeout", getattr(args, "ollama_timeout", 300)),
        )
    if provider == "deepseek":
        model = _effective_model(args, provider)
        base_url = getattr(args, "base_url", None) or provider_env("ROBIN_DEEPSEEK_API_BASE", ("DEEPSEEK_API_BASE",), DEFAULT_DEEPSEEK_BASE_URL)
        api_key = provider_env("ROBIN_DEEPSEEK_API_KEY", ("DEEPSEEK_API_KEY",))
        return AnthropicCompatibleModelClient(
            **options,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(args, "openai_timeout", getattr(args, "ollama_timeout", 300)),
            # 保留 DeepSeek 显式关闭推理的默认配置。
            reasoning_disabled=True,
        )

    model = _effective_model(args, provider)
    host = getattr(args, "host", DEFAULT_OLLAMA_HOST)
    return OllamaModelClient(
        **options,
        model=model,
        host=host,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout=args.ollama_timeout,
    )


def build_welcome(agent, model, host):
    """构建 CLI 启动时展示的欢迎面板。"""
    width = max(68, min(shutil.get_terminal_size((80, 20)).columns, 84))
    inner = width - 4
    gap = 3
    left_width = (inner - gap) // 2
    right_width = inner - gap - left_width

    def row(text):
        """渲染欢迎面板的一行文本。"""
        body = middle(text, width - 4)
        return f"| {body.ljust(width - 4)} |"

    def divider(char="-"):
        """渲染欢迎面板的分隔线。"""
        return "+" + char * (width - 2) + "+"

    def center(text):
        """把文本居中到欢迎面板宽度。"""
        body = middle(text, inner)
        return f"| {body.center(inner)} |"

    def cell(label, value, size):
        """渲染欢迎面板中的一个字段值。"""
        body = middle(f"{label:<9} {value}", size)
        return body.ljust(size)

    def pair(left_label, left_value, right_label, right_value):
        """渲染欢迎面板中的键值对。"""
        left = cell(left_label, left_value, left_width)
        right = cell(right_label, right_value, right_width)
        return f"| {left}{' ' * gap}{right} |"

    line = divider("=")
    rows = [center(text) for text in WELCOME_ART]
    rows.extend(
        [
            center(WELCOME_NAME),
            center(WELCOME_SUBTITLE),
            center(WELCOME_STATUS),
            divider("-"),
            row(""),
            row("WORKSPACE  " + middle(agent.workspace.cwd, inner - 11)),
            pair("MODEL", model, "BRANCH", agent.workspace.branch),
            pair("APPROVAL", agent.approval_policy, "SESSION", agent.session["id"]),
            row(""),
        ]
    )
    return "\n".join([line, *rows, line])


def build_agent(args):
    """根据 CLI 参数装配出一个可运行的 Robin 实例。

    为什么存在：
    命令行参数只是字符串和开关，runtime 需要的是已经装配好的对象图：
    model client、workspace snapshot、session store、secret 配置等。
    这个函数负责把“启动参数”翻译成“agent 运行现场”。

    输入 / 输出：
    - 输入：`argparse` 解析后的 `args`
    - 输出：一个新的 `Robin`，或一个从旧 session 恢复出来的 `Robin`

    在 agent 链路里的位置：
    它是整个程序启动链路里最靠近 runtime 的装配点。`main()` 先调它，
    得到 agent 后，后面无论是 one-shot 还是 REPL 模式，都会落到 `ask()`。
    """
    # 这里是 CLI 到 runtime 的装配点：
    # 先采集工作区快照和加载项目级环境，再整理 secret 名单、模型后端和 session。
    workspace = WorkspaceContext.build(args.cwd)
    load_project_env(workspace.repo_root)
    configured_secret_names = _configured_secret_names(args)
    store = SessionStore(workspace.repo_root + "/.robin/sessions")
    model = _build_model_client(args)
    provider = _effective_provider(args)
    output_limit, context_window = model_limits(workspace.repo_root, provider, _effective_model(args, provider))
    configured_output = getattr(args, "max_new_tokens", None) or provider_env("ROBIN_MAX_NEW_TOKENS")
    configured_context = getattr(args, "context_window", None) or provider_env("ROBIN_CONTEXT_WINDOW")
    output_limit = positive_integer(configured_output or output_limit, "max_new_tokens")
    context_window = positive_integer(configured_context or context_window, "context_window")
    session_id = args.resume
    if session_id == "latest":
        session_id = store.latest()
    if session_id:
        return Robin.from_session(
            model_client=model,
            workspace=workspace,
            session_store=store,
            session_id=session_id,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=output_limit,
            context_window=context_window,
            secret_env_names=configured_secret_names,
        )
    return Robin(
        model_client=model,
        workspace=workspace,
        session_store=store,
        approval_policy=args.approval,
        max_steps=args.max_steps,
        max_new_tokens=output_limit,
        context_window=context_window,
        secret_env_names=configured_secret_names,
    )


def build_arg_parser():
    """创建 Robin CLI 的参数解析器。"""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Minimal coding agent for DeepSeek, OpenAI-compatible, Anthropic-compatible, or Ollama models.",
    )
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt.")
    parser.add_argument("--api", choices=APIS, default=None, help="Model API protocol (or ROBIN_API).")
    parser.add_argument("--cwd", default=".", help="Workspace directory.")
    parser.add_argument(
        "--provider",
        choices=PROVIDER_CHOICES,
        default=None,
        help="Model backend to use. Defaults to ROBIN_PROVIDER or deepseek.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override. Defaults to qwen3.5:4b for Ollama, ROBIN_OPENAI_MODEL for openai, ROBIN_ANTHROPIC_MODEL for anthropic, and ROBIN_DEEPSEEK_MODEL for deepseek when set.",
    )
    parser.add_argument("--host", default=DEFAULT_OLLAMA_HOST, help="Ollama server URL.")
    parser.add_argument("--base-url", default=None, help="Provider API base URL for deepseek, openai, or anthropic.")
    parser.add_argument("--ollama-timeout", type=int, default=300, help="Ollama request timeout in seconds.")
    parser.add_argument("--openai-timeout", type=int, default=300, help="OpenAI-compatible request timeout in seconds.")
    parser.add_argument("--resume", default=None, help="Session id to resume or 'latest'.")
    parser.add_argument("--approval", choices=("ask", "auto", "never"), default="ask", help="Approval policy for risky tools.")
    parser.add_argument(
        "--secret-env-name",
        dest="secret_env_names",
        action="append",
        default=[],
        help="Extra environment variable names to treat as secrets for trace/report redaction.",
    )
    parser.add_argument("--max-steps", type=int, default=50, help="Maximum tool/model iterations per request.")
    parser.add_argument("--max-new-tokens", type=lambda v: positive_integer(v, "max-new-tokens"), default=None,
                        help="Output token limit: CLI > ROBIN_MAX_NEW_TOKENS > model config > 16384.")
    parser.add_argument("--context-window", type=lambda v: positive_integer(v, "context-window"), default=None,
                        help="Context token limit: CLI > ROBIN_CONTEXT_WINDOW > model config > 128000.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature sent to Ollama.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p sampling value sent to Ollama.")
    return parser


def main(argv=None):
    """解析命令行参数并启动 Robin。"""
    args = build_arg_parser().parse_args(argv)
    agent = build_agent(args)

    model = getattr(agent.model_client, "model", getattr(args, "model", DEFAULT_OLLAMA_MODEL))
    host = getattr(agent.model_client, "host", getattr(agent.model_client, "base_url", getattr(args, "host", DEFAULT_OLLAMA_HOST)))
    print(build_welcome(agent, model=model, host=host))

    if args.prompt:
        # one-shot 模式：只跑一次 ask，不进入 REPL 循环。
        prompt = " ".join(args.prompt).strip()
        if prompt:
            print()
            try:
                print(agent.ask(prompt))
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        return 0

    try:
        return _interactive(agent)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        project = Path(__file__).resolve().parent.parent
        launcher = ["uv", "run", "--project", str(project), "robin"] if (project / "pyproject.toml").is_file() else ["robin"]
        command = shlex.join([*launcher, "--cwd", str(agent.root), "--resume", agent.session["id"]])
        print(f"\n恢复此会话：\n  {command}")


def _interactive(agent):
    terminal = TerminalInput(
        status_callback=lambda: status_lines(agent.turn_usage, agent.session["usage"], agent.model_client,
                                             agent.context_usage, agent.context_window),
        history_callback=lambda: agent.redact_artifact(agent.session["history"]),
    )
    while True:
        # 交互模式：每次读取一条用户输入，交给同一个 agent，
        # 因此 session history 和 working memory 会跨轮延续。
        try:
            user_input = terminal.read().strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print(HELP_DETAILS)
            continue
        if user_input == "/memory":
            print(agent.memory_text())
            continue
        if user_input == "/session":
            print(agent.session_path)
            continue
        if user_input == "/tools":
            show_tool_history(agent.redact_artifact(agent.session["history"]))
            continue
        if user_input == "/reset":
            agent.reset()
            print("session reset")
            continue
        if user_input == "/compact":
            try:
                prompt, metadata = agent._build_prompt_and_metadata("")
                with terminal.activity():
                    build_request(agent, prompt, metadata, force_compaction=True)
                print("历史已压缩，原始记录已保留。" if metadata["history_compacted"] else "暂无可压缩的新历史。")
            except (RuntimeError, ValueError) as exc:
                print(agent.redact_text(str(exc)), file=sys.stderr)
            continue

        if user_input.startswith("/"):
            print("Unknown command. Type /help to list commands.")
            continue

        print()
        try:
            with terminal.activity():
                answer = agent.ask(user_input)
            print(answer)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)


def _direct_run_argv(argv):
    """给 IDE 直接运行本文件时使用的默认 one-shot 参数。"""
    if len(argv) > 1:
        return argv[1:]
    return [DIRECT_RUN_PROMPT] if DIRECT_RUN_PROMPT else []


if __name__ == "__main__":
    raise SystemExit(main(_direct_run_argv(sys.argv)))
