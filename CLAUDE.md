# CLAUDE.md

本文件给 Claude Code 在本仓库工作时提供上下文。命令、路径和约束均以当前代码为准。

## 自动沉淀开源 Agent 研究

当用户询问开源 agent 如何解决问题、比较其设计，或要求参考其实现改造 Robin 时，
主动读取并执行仓库共享 skill：[agent-learning](.agents/skills/agent-learning/SKILL.md)。
项目包括但不限于 OpenCode、Pi 和 Codex。后续选择或调整方案、要求实施、完成实现时，
检索并更新对应主题；普通提及项目名不触发。研究完成先记录，未选定的 Robin 改法标为待决定。
无需全局安装；即使客户端未自动发现 skill，也直接读取该文件及其模板。
只读或规划模式不写文件，在恢复可写模式后处理尚未沉淀的内容。
知识库入口：[Agent 设计学习索引](knowledge/agent-design/README.md)。

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
| `robin/providers/clients.py` | LiteLLM 协议适配 / Fake 客户端，统一 `complete(ModelRequest)` |
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

模型通过原生工具调用表达动作，`ModelClient.complete(ModelRequest)` 返回统一的
`ModelResponse`。`providers/types.py` 定义响应、用量与调用 ID；`providers/context.py`
生成 JSON Schema 和结构化消息；`providers/clients.py` 用 LiteLLM 转换四种 API。
Agent Loop 不解析 XML/DSML；普通回答中的这些标签保持文本。多个调用按顺序执行，
每个结果用原调用 ID 回传。预算耗尽后的未执行调用写入拒绝结果。

`--api` > `ROBIN_API` > provider 默认值。OpenAI 默认 Responses，Anthropic/DeepSeek
默认 Anthropic Messages，Ollama 默认 Ollama Chat；自定义网关显式配置协议。
不允许按模型名称或域名自动切换端点，不允许静默丢弃不支持的参数。
会话格式版本为 2；旧历史转成明确标注的摘要，旧用量保留。推理内容单独保存，
仅在协议需要时回传，不拼入终端回答。测试与评测 Fake 响应必须是结构化对象。

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

原生模型消息不使用上述 history 字符预算裁剪：容量充足时保留压缩边界之后的全部有效历史。
`history_compaction.py` 按模型窗口预留输出、4096 token 安全余量及摘要空间，接近上限时
请求不带工具的进度摘要，保留近期约 20000 token（小窗口按可用预算缩小）的完整调用结果组。
摘要包含已完成、失败修改、未完成步骤及验证证据；成功且容量检查通过后才保存
session.history_compaction（summary/through/created_at），原始 history 不删除。
摘要失败或单个近期消息组过大时明确停止，不静默丢弃；摘要调用计入本轮、会话及 delegate 用量。
resume 复用压缩边界，reset 清除；压缩后清空旧读取范围保护，允许获取已退出上下文的正文。
交互命令 `/compact` 可在阈值前手动压缩；无新历史时不调用模型，短会话整体归纳。
状态栏的最近请求上下文优先使用接口返回的输入 token；请求尚未返回或未提供用量时显示明确标注的本地估算。
交互模式的模型等待与工具执行动画同步刷新用量状态，结束或异常时清除临时显示；非交互输出不增加状态行。
本地 token 估算采用 ASCII 三字符约一个 token、非 ASCII 每字符两个及消息开销，不代表接口实际用量。

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
- `context_window` 已纳入运行环境指纹；旧 checkpoint 缺少该字段时仍可恢复。

### 停机条件

达到 `max_steps`、重试上限、模型错误、工具超时、审批拒绝、delegate 失败或最终答案返回时停止，
原因写入 `TaskState.stop_reason` 并进入 report。注意 `Robin` 的默认 `max_steps=6`，
而 CLI 的 `--max-steps` 默认是 50。

工具预算在执行器计数：预算内的校验失败、重复拒绝和审批拒绝也占一步；
超过预算的调用不执行、不再计步。耗尽后最多请求一次不带工具定义的总结，
终端提示“工具预算已耗尽：已用/上限，任务未完成”，状态固定为 `stopped` / `budget_exhausted`，
总结成功或失败均不覆盖停机原因。旧 `step_limit_reached` 记录仍可读取。
重复保护独立于审批：当前用户轮次第三次相同调用、第四次 A/B/A/B 调用在审批前拒绝，
`auto` 同样受限；修改命令文本的语义重复不在本次检测范围。

### 文件变化观察

文件读取结果按完整行限制在约 4000 字符内，`read_page` 标记包含实际 start、next 和 total；
上下文缩减工具结果时重算 next。超长单行不冒充完整返回，提示使用 shell 定位所需片段。
shell 大输出保存至工作区 `.robin/tool-output/` 的随机脱敏文件，预览保留退出码和完整输出路径；当前尚无自动清理。
同一用户轮次、文件内容版本下，已完整返回两次的范围再次读取会拒绝，跨间隔有效；新范围及内容变化允许读取。
已读区间会合并计算首个未读位置，不推荐超出 EOF 的续读。最近一个完整读完且不超过 12 KiB 的文件，
以数据块保留在实际模型请求中；该版本的全文已被保留时拒绝继续重读。每次请求校验路径及内容哈希，
变化、用户轮次切换、历史压缩或 reset 后淘汰。缓存校验只读取普通文件，使用非阻塞且不跟随符号链接的打开方式，
打开后再次检查文件类型与大小，最多读取 12001 字节；特殊文件、超限或读取失败均淘汰缓存。
额外保留内容由输出 token 预算计算纳入估算，不受原 12000 字符 section 总预算限制。
此范围记录只保存在当前进程，resume 后重新积累；不识别 shell 与 read 的语义等价。

交互终端的工具执行提示及完成行保留参数摘要；输入底栏可点击“工具记录”查看会话中的完整工具参数，
也可用 `/tools` 打开。记录按调用 ID 关联结果，包含尚无结果的中断调用；展示前脱敏，查看不执行工具。
记录窗口使用独立全屏视图，支持滚动、Esc/q 返回；普通滚动区中的历史输出不提供鼠标回调。

write/patch 的前后快照只观察经过路径校验的目标文件，最多读取 2 MiB；更大文件退回元数据比较并标记不完整。
shell 快照不读取文件内容，比较 size/mtime/ctime/inode/mode；遍历前剪枝依赖、缓存、构建目录，
每次最多观察 2000 条目录项，约 1 秒后停止遍历（时间检查不能中断底层文件系统调用）。
工具动画覆盖前置快照、执行和后置快照。`snapshot_scope`、`snapshot_complete`、
`snapshot_limitations` 记录观察范围与缺失原因；`workspace_changed=false` 只表示未观察到变化，
不能将不完整快照解释为完整审计。shell 的元数据比较也不等同于内容校验或撤销快照。

## 配置

- `.env` 从 `repo_root` 向上查找并加载（`config.load_project_env`），已存在的环境变量会被 `.env` 覆盖。
- `ROBIN_PROVIDER` 默认 `deepseek`；模型与 base URL 默认值见 `cli.py`，DeepSeek 走
  Anthropic 兼容端点并显式关闭 thinking。
- DeepSeek 显式切换 API 时由适配层转换关闭推理参数：Messages/Chat 使用
  `thinking.type=disabled`，Responses 使用 `reasoning.effort=none`，Ollama 使用 `think=false`。
- 脱敏名单：`DEFAULT_SECRET_ENV_NAMES` + `--secret-env-name` + `ROBIN_SECRET_ENV_NAMES`。
- 不要把 `.env`、真实密钥、`.robin/`、`dist/` 提交进仓库。

## 开发约定

- 公共 API（`robin/__init__.py` 的导出）变化必须同步 `tests/test_public_api_contract.py`。
- 安全相关改动（路径、审批、脱敏、shell env）必须同时跑 `test_security.py`、
  `test_safety_invariants.py`、`test_allowed_tools.py`。
- provider 差异收敛在 `providers/clients.py` 的 `complete()` 内，runtime 不认识具体 HTTP 协议。
- 测试不要依赖真实网络、真实密钥或固定 locale/时区。
- 注释与文档保持中文风格：说明“为什么”和链路位置，而不是复述代码。

### 提交信息

提交信息只允许类型前缀使用英文，标题描述和正文详情全部使用中文：

```text
docs: 新增 CLAUDE.md 项目指引

补充项目概览、常用命令、代码地图与核心机制说明，覆盖工具协议、
审批策略、上下文预算、记忆分层、持久化与恢复、配置与安全约束。
```

常用前缀：`feat:`、`fix:`、`docs:`、`test:`、`chore:`、`refactor:`。

每次提交还要在正文末尾追加协作者署名：

```text
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
