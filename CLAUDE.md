# CLAUDE.md

本文件给 Claude Code 在本仓库工作时提供上下文。命令、路径和约束均以当前代码为准。

## 项目概览

Robin Agent 是一个本地 coding agent：模型在受控循环里拼上下文、申请工具、执行工具并沉淀记忆。
Python 包名是 `robin`，运行时类是 `Robin`，环境变量统一使用 `ROBIN_` 前缀，本地状态写入 `.robin/`。

- Python >= 3.10，依赖用 `uv` 管理（`pyproject.toml` + `uv.lock`）
- 入口：`robin`（`robin.cli:main`）或 `python -m robin`
- 测试：pytest；Lint：ruff（只启用 `E4`、`E7`、`E9`、`F`）
- 包内模块文档与注释以中文为主，代码标识符用英文

## 常用命令

```sh
uv sync --locked                     # 安装依赖
uv run robin --help                  # 查看参数
uv run robin "解释这个仓库的结构"     # one-shot
uv run robin --cwd /path/to/repo      # 交互式 REPL
uv run pytest -q                     # 全量测试（约 30s）
uv run pytest tests/test_memory.py -q  # 单文件
uv run ruff check robin tests        # 只检查已入库目录
uv build                             # 生成 wheel 与 sdist
```

测试全部使用 `FakeModelClient`、`tmp_path` 工作区和 fixture，不访问网络。
`tests/test_evaluator.py::test_run_fixed_benchmark_reports_metadata_and_success_definition`
断言 locale 为 `C.UTF-8`，在 `zh_CN.UTF-8` 环境会失败，属已知环境敏感断言。

## 代码地图

| 路径 | 职责 |
| --- | --- |
| `robin/cli.py` | 参数解析、provider/模型选择、欢迎面板、one-shot 与 REPL |
| `robin/runtime.py` | `Robin` 门面：会话、prompt、工具调度、记忆、checkpoint、report |
| `robin/agent_loop.py` | 从 runtime 拆出的主循环（感知→决策→行动→记录→停机） |
| `robin/providers/clients.py` | Ollama / OpenAI 兼容 / Anthropic 兼容 / Fake 客户端，统一 `complete()` |
| `robin/tools.py` | 工具白名单、参数校验、具体实现 |
| `robin/tool_executor.py` | 带元数据的结构化执行层 |
| `robin/tool_context.py` | 传给工具函数的窄上下文（根目录、路径解析、shell env） |
| `robin/security.py` | 敏感变量识别、trace/report 脱敏、shell 环境白名单 |
| `robin/workspace.py` | 工作区快照（git 事实 + 白名单文档）、`clip`/`middle`/`now` |
| `robin/prompt_prefix.py` | 稳定 prefix 构建与 hash 缓存 |
| `robin/context_manager.py` | prompt 组装与分 section 预算压缩 |
| `robin/features/memory.py` | 工作记忆、episodic notes、文件摘要、durable memory |
| `robin/checkpoint.py` | checkpoint 创建与 resume 校验 |
| `robin/task_state.py` | 单次 run 的状态机与停机原因 |
| `robin/run_store.py` | `.robin/runs/<run_id>/` 审计工件 |
| `robin/session_store.py` | `.robin/sessions/<id>.json` 会话持久化 |
| `robin/terminal.py` | prompt_toolkit 输入、历史、slash 命令补全 |
| `robin/evaluation/` | 基准评测、消融/安全/记忆/恢复指标 |

`benchmarks/`、`docs/`、`scripts/` 目前在 `.gitignore` 中，但 README 和评测代码仍引用它们
（例如 `evaluator.DEFAULT_BENCHMARK_PATH`）；改动这些默认路径前先确认仓库里是否存在对应文件。

## 核心机制

### 工具协议

模型用文本标签表达动作，`Robin.parse()` 解析：

```text
<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>
<tool name="patch_file" path="a.py"><old_text>...</old_text><new_text>...</new_text></tool>
<final>最终答案</final>
```

工具白名单固定为 `list_files`、`read_file`、`search`、`run_shell`、`write_file`、`patch_file`，
以及仅在 `depth < max_depth` 时暴露的 `delegate`。新增工具要同时改
`BASE_TOOL_SPECS`、`_TOOL_RUNNERS`、`TOOL_EXAMPLES`、`validate_tool` 并补测试。

关键校验：`run_shell` 超时必须在 `[1, 120]`；`patch_file` 的 `old_text` 必须在文件中恰好出现一次；
所有路径经 `resolve_workspace_path` 解析，越出工作区或符号链接逃逸都会被拒绝。

### 审批与权限

`approval_policy` 取 `ask` / `auto` / `never`，`run_shell`、`write_file`、`patch_file` 属于 risky 工具；
`read_only=True` 时写操作直接拒绝；`allowed_tools` 可进一步收窄可见工具集。

### 上下文

- `prompt_prefix` 由工具签名 + 工作区指纹缓存，工作区变化时 `refresh_prefix(force=True)`。
- `ContextManager` 总预算 12000 字符，分 section 预算：prefix 3600 / memory 1600 /
  relevant_memory 1200 / history 5200，压缩顺序为 relevant_memory → history → memory → prefix。
- feature flags：`memory`、`relevant_memory`、`context_reduction`、`prompt_cache`，默认全开
  （`runtime.DEFAULT_FEATURE_FLAGS`），评测会用 `_temporary_feature_flags` 做消融。

### 记忆分层

- working：当前任务摘要 + 最近文件；episodic notes ≤ 12；file summaries ≤ 6。
- durable：写入 `.robin/memory/MEMORY.md` 及 topic 文件，topic 固定为 `project-conventions`、
  `key-decisions`、`dependency-facts`、`user-preferences`。
- durable 提升依赖固定前缀行：`Project convention:` / `Decision:` / `Dependency:` / `Preference:`
  及对应中文 `项目约定：` / `决策：` / `依赖：` / `偏好：`；修改这些正则要同步 `tests/test_memory.py`
  与 `tests/test_evaluator.py`。

### 持久化与恢复

- 会话：`.robin/sessions/<session_id>.json`（history + memory + checkpoints）。
- 运行工件：`.robin/runs/<run_id>/task_state.json`、`trace.jsonl`、`report.json`。
- resume 状态：`no-checkpoint`、`full-valid`、`partial-stale`、`workspace-mismatch`、`schema-mismatch`。
- `checkpoint.RUNTIME_IDENTITY_KEYS` 决定恢复时哪些运行环境变化会导致不匹配；
  新增影响行为的 runtime 字段时要一并登记。

### 停机条件

达到 `max_steps`、重试上限、模型错误、工具超时、审批拒绝、delegate 失败或最终答案返回时停止，
原因写入 `TaskState.stop_reason` 并进入 report。注意 `Robin` 的默认 `max_steps=6`，
而 CLI 的 `--max-steps` 默认是 50。

## 配置

- `.env` 从 `repo_root` 向上查找并加载（`config.load_project_env`），已存在的环境变量会被 `.env` 覆盖。
- `ROBIN_PROVIDER` 默认 `deepseek`；模型与 base URL 默认值见 `cli.py`，DeepSeek 走
  Anthropic 兼容端点并显式关闭 thinking。
- 脱敏名单：`DEFAULT_SECRET_ENV_NAMES` + `--secret-env-name` + `ROBIN_SECRET_ENV_NAMES`。
- 不要把 `.env`、真实密钥、`.robin/`、`dist/` 提交进仓库。

## 开发约定

- 公共 API（`robin/__init__.py` 的导出）变化必须同步 `tests/test_public_api_contract.py`。
- 安全相关改动（路径、审批、脱敏、shell env）必须同时跑 `test_security.py`、
  `test_safety_invariants.py`、`test_allowed_tools.py`。
- provider 差异收敛在 `providers/clients.py` 的 `complete()` 内，runtime 不认识具体 HTTP 协议。
- 测试不要依赖真实网络、真实密钥或固定 locale/时区。
- 注释与文档保持中文风格：说明“为什么”和链路位置，而不是复述代码。