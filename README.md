# Robin Agent

Robin Agent is a local coding agent. Its Python package is `robin`, its runtime
class is `Robin`, configuration variables use the `ROBIN_*` prefix, and local
state is stored in `.robin/`.

## Run on Linux

Requires Python 3.10+ and uv.

```sh
uv sync --locked
cp .env.example .env
# Fill in the API key for your chosen provider in .env.
uv run robin --help
uv run robin --cwd /path/to/repository
uv run robin "Inspect this repository and explain its architecture"
uv run python -m robin --help
```

Provider selection and model configuration are documented in `.env.example`.
Interactive input supports backspace, cursor movement, and command history.
Type `/` to open the command menu, use Up/Down to select, and Enter to execute.
Tab completes commands; Ctrl-D exits.
The existing CLI supports DeepSeek, OpenAI-compatible, Anthropic-compatible,
and Ollama providers. API keys are supplied by the user.

## Development

```sh
uv run pytest -q
uv run ruff check robin tests scripts
uv build
```

`uv build` produces a Python wheel and source distribution, which require Python
to run. A TUI and a standalone Linux executable are planned for later stages.

## Code Map

- `robin/cli.py`: command parsing, configuration, and interactive CLI.
- `robin/runtime.py` and `robin/agent_loop.py`: runtime and agent loop.
- `robin/providers/`: model provider clients.
- `robin/tools.py` and `robin/tool_executor.py`: tools and execution policy.
- `robin/features/`: memory management.
- `robin/evaluation/`: benchmark evaluation and experiment metrics.
- `tests/` and `benchmarks/coding_tasks.json`: regression tests and task fixtures.
- `scripts/`: evaluation commands.

`docs/CV/questions.md` provides interview questions and improvement ideas.
Historical experiment claims require fresh validation against Robin Agent;
archived experiment results are not included in this project.
