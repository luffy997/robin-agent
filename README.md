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

The input toolbar shows token usage for the latest request (including retries
and delegated work), session totals, cache reads and hit rate, and the configured
reasoning mode. It refreshes when input resumes. Missing provider counters are
shown as unknown; incomplete totals are marked. Session totals survive resume
and are cleared by `/reset`. Old sessions cannot recover historical usage.
These counters describe reported API usage, not price or context occupancy.
The existing CLI supports DeepSeek, OpenAI-compatible, Anthropic-compatible,
and Ollama providers. API keys are supplied by the user.

## Model Adapter

Robin uses LiteLLM directly as a Python library (no Proxy). The agent loop sends
structured messages and JSON Schema tools through `ModelClient`; it receives a
`ModelResponse` with text, reasoning, native tool calls, stop reason and usage.
Only native calls execute tools. XML or DSML in an answer stays ordinary text.
Loop protection applies even with automatic approval: the third identical call
or the fourth call in an A/B/A/B sequence is rejected within each user turn.
Tool attempts (including validation and approval rejections) consume the step
budget. Once exhausted, no further tools execute; one tool-free summary request
may follow. The terminal reports the used/total budget and the persisted run
remains `stopped` with reason `budget_exhausted`, even if summarization fails.
Multiple calls run sequentially, each with its own validation and approval.
File reads return complete lines with an explicit continuation cursor; context
reduction recalculates that cursor when fewer lines fit. Repeated reads of an
unchanged range are rejected after two successful returns within the current
turn, including reads separated by other calls. New ranges and changed content
remain readable. This additional range tracking starts fresh after resume.
Read ranges are merged when finding unread content. The latest fully read file
up to 12 KiB is retained in model requests while unchanged within the turn;
further reads of that retained version are rejected. Changes invalidate the
retained content. Larger files continue to use paginated reads.
Large shell results are saved as redacted files under `.robin/tool-output/`;
the preview includes the full-output path for paginated reading. These files
currently require manual cleanup.
Approval previews show the first 20 characters of long text arguments followed
by `...`; execution uses the full arguments. Shortened shell approvals show a
`[+]` marker: click it to expand the full command, then click `[-]` to collapse.
Only `y`/`yes` approves execution. Non-interactive approvals show full arguments.
Interactive terminals display a
spinner with an argument preview while a tool runs, followed by a persistent
completion line with the same preview, including in auto approval mode. Click
`工具记录` in the input toolbar or enter `/tools` to browse full tool arguments
from the current session, including completed calls and resumed history.
The scrollable viewer closes with Esc or q; opening it never reruns a command.
A separate
spinner indicates waiting for the model response.

Select the wire protocol with `--api` or `ROBIN_API`. Priority is CLI, environment,
then provider default. Custom URLs never select a protocol by hostname.

| Provider | Default API |
| --- | --- |
| openai | `responses` |
| anthropic | `anthropic-messages` |
| deepseek | `anthropic-messages` |
| ollama | `ollama-chat` |

`chat-completions` is also available for compatible gateways. Model identifiers
are sent unchanged. LiteLLM routing prefixes are internal to the adapter.
Base URLs are API roots: `/v1` for OpenAI-compatible gateways; either the root or
`/v1` for Anthropic-compatible gateways. A full `/v1/messages` URL also works.
Authentication, timeouts, bounded retries and reasoning settings live in the
adapter. DeepSeek and Ollama retain explicit reasoning-off defaults. Unsupported
parameters fail explicitly; endpoints and providers are never tried as fallbacks.
Telemetry and remote model metadata fetching are disabled for these adapters.

DeepSeek gateway example (replace the URL and fill the key locally):

```dotenv
ROBIN_PROVIDER=deepseek
ROBIN_API=anthropic-messages
ROBIN_DEEPSEEK_API_BASE=https://your-gateway.example/anthropic
ROBIN_DEEPSEEK_API_KEY=
ROBIN_DEEPSEEK_MODEL=deepseek-flash
```

```sh
uv run robin --api anthropic-messages "Explain Newton's first law"
# A Chat Completions gateway can use the anthropic provider's credentials/model:
uv run robin --provider anthropic --api chat-completions --base-url https://gateway.example/v1
```

New sessions use format version 2 and retain call IDs, results and required
reasoning replay fields. Old history is loaded as a labeled legacy summary,
without inventing native calls; existing usage totals are retained. `/reset`
clears that summary and usage. A truncated or abnormal model response fails the
run instead of being treated as success.

Model limits follow CLI flags (`--max-new-tokens`, `--context-window`), then
environment (`ROBIN_MAX_NEW_TOKENS`, `ROBIN_CONTEXT_WINDOW`), then the selected
provider/model entry in workspace-root `robin.models.json`. Unconfigured models
default to 16,384 output tokens and a 128,000-token context window. These are
local fallback budgets, not discovered model capabilities. For example:

```json
{
  "deepseek": {
    "deepseek-flash": {"maxTokens": 16384, "contextWindow": 128000}
  }
}
```

Use limits supported by your gateway. Each request caps output against remaining
context after a conservative local estimate of messages/tools and a 4,096-token
safety margin. The estimate uses UTF-8 byte length plus message overhead; it may
reduce output earlier than necessary and is never displayed as actual usage.
If no room remains, Robin fails before sending the request.
On truncation, no tools from that response execute;
restart with a larger limit and `--resume latest`, then ask Robin to continue.
The gateway or model may enforce a separate output limit.

To add an API, implement `ModelClient.complete(ModelRequest) -> ModelResponse`
and register/configure it in the provider layer; the loop does not import
LiteLLM types. `FakeModelClient` also requires structured `ModelResponse` fixtures.

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
