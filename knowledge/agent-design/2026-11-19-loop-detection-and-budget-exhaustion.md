# 重复调用检测与预算耗尽

- 创建日期：2026-11-19
- 更新日期：2026-11-19
- 状态：已验证
- 参考项目：OpenCode v1.2.27；本地 Pi 阅读版 `f13d2791191cfcc557c6a30af8446e836b1741a8`；Robin 当前工作区
- 关键词：doom loop、重复工具调用、无进展、工具预算、收尾、停机原因

## 1. 问题与现场

用户问题：“检测多次无进展的重复调用，并明确显示预算耗尽，这个在 OpenCode、Pi 中如何解决？”

现场来源：本地 session `.robin/sessions/20261119-110629-9b5ab7.json`，不要将整个 session 提交到仓库。
原任务是在 `/data/code` 下编写 NVD 漏洞下载程序，Robin 工作区却是 `/data/code/robin-agent`。

2026-11-19 检查记录的事实：

- session 共记录 70 次工具调用：66 次 `run_shell`、3 次 `write_file`、1 次 `list_files`；工具记录包括被拒绝的调用，不等同于成功执行次数。
- 第一轮 8 次调用，3 次写文件因越出工作区被拒绝；shell 检查、建目录没有解决写入路径冲突。
- 用户说“继续”后，下一轮 50 次调用中有 49 次查看 Git 状态或 diff，任务转向分析 Robin 自身未提交改动。
- 第三轮用户问“你改的哪个地方啊”，又有 12 次 shell 调用查看 diff，记录中没有该轮最终回答。
- 检查时 `/data/code/nvd_downloader` 仍为空；没有证据表明下载程序已完成。
- 第二轮运行工件 `.robin/runs/run_20261119-110836-99ee90/task_state.json` 显示 `tool_steps=50`、`status=completed`、`stop_reason=final_answer_returned`。

auto 模式跳过审批提示，执行动画只展示工具名，因此用户看不到具体命令。常见命令为 `git diff`、`git diff robin/terminal.py | head -300`、`git diff --stat`。它们参数不同，但多次没有推进原任务。

### 同日再次复查：session 仍在增长

北京时间 11:44:03 用户再次提出 NVD 下载任务。复查快照中该轮已至少记录 35 次工具调用（20 次 shell、15 次写文件）；15 次写入均因目标 `/data/code/nvd_downloader/nvd_downloader.py` 越出工作区被拒绝。shell 多次执行 `mkdir -p /data/code/nvd_downloader && echo ok`，也穿插目录检查。检查时目标目录仍为空，没有完成程序的证据。

这是新增失败现场，不是此前 70 次调用的重复统计。检查过程中记录继续增长，数字只是快照；未核实运行进程启动时间和已加载代码，不能据此断言此前新增保护已在该进程生效。Python 源文件修改后，既有进程通常仍使用已加载定义，需要重启验证。本次仅检查与补记，没有停止进程或修改运行时。

## 2. 参考项目如何解决

### OpenCode v1.2.27：精确重复触发权限检查

**源码事实：** `SessionProcessor` 的 `tool-call` 处理分支将当前工具状态写为 running，然后读取当前 assistant 消息的 parts，取最后三个 part。三个 part 都必须是同名工具、状态非 pending，且输入参数的 `JSON.stringify()` 与当前调用一致，才触发 `PermissionNext.ask(permission="doom_loop")`。阈值为 3，默认权限为 ask。

这是一道单独的权限检查，实际处理仍受权限配置影响，并非无条件终止。检测范围是该 assistant 消息的 parts，不能将它描述为任意跨轮历史上的三次相同调用。

**边界：** 它不比较工具结果，也不判定任务目标是否推进；参数不同、交替调用或同一命令增加 `head`/`tail` 时，不满足此精确匹配条件。不能宣称它能防止本次 Robin 的全部循环。

### OpenCode v1.2.27：步骤上限与提示式收尾

**源码事实：** `SessionPrompt` 的循环按模型迭代增加 `step`，使用 `agent.steps ?? Infinity`，未配置时没有有限上限。这与 Robin 每个独立工具调用计一步不同。

当 `step >= maxSteps`，请求插入 `MAX_STEPS` 提示，要求禁止继续调用工具，说明已达到上限、已完成内容、剩余事项和建议。但检查的调用位置仍传入 `tools`，`toolChoice` 也未因 `isLastStep` 改为禁止工具。

**结论范围：** 此路径显示的是提示式约束，不能仅凭提示文本声称工具在执行器中已被硬禁用。本次未运行 OpenCode，未核实其终端最终排版或所有持久化状态，不据此声称它有专用 `budget_exhausted` 状态。

### 本地 Pi 阅读版：通过钩子控制循环

**来源范围：** `/data/code/pi` 的 remote 是 `luffy997/pi-note`，包含阅读注释和本地扩展；此处结论仅针对所检查的核心循环，不能直接推广到官方 Pi 的所有版本或扩展。

**源码事实：** `runLoop` 在存在工具调用或待处理消息时继续；没有后续工具、steering/follow-up 消息或显式继续要求时自然结束。模型 error/aborted 也会结束。

每轮模型响应与工具结果完成后调用 `finishTurn`；返回 `{ action: "end" }` 可结束运行。`beforeToolCall` 返回 block 可阻止执行；`terminate` 是批次提前结束提示，按类型契约，需要批次内所有最终工具结果都设置该提示才会提前结束，不能理解为任意一个调用设为 true 就立即终止全部工作。

在检查的 `packages/agent/src/agent-loop.ts`、`packages/agent/src/types.ts` 中，未发现内置精确重复检测、语义无进展判断或固定 `maxSteps`/`maxTurns` 上限。上层可利用这些钩子实现策略；本次未核实所有扩展，也没有验证预算耗尽的专用 UI 或持久化格式。

### 三类重复问题的区别

| 类型 | 示例 | 当前研究支持的检测办法与边界 |
| --- | --- | --- |
| 完全相同调用 | 同一工具、同一参数重复 | 精确比较可覆盖；需明确窗口和跨轮范围 |
| 交替重复 | A → B → A → B | 简单连续相同检测无法覆盖，需要窗口内序列检测 |
| 语义无进展 | 不断换 diff 命令却偏离 NVD 任务 | 参数比较不足；结果重复与任务目标可提供信号，但只读任务没有文件变化也可能在推进 |

## 3. 源码阅读入口

以下行号为本次阅读时的位置，同时记录符号以便后续定位。OpenCode 链接固定到 v1.2.27；本地 Pi 行号须在本地阅读版使用。

| 项目与版本 | 文件、符号与关键行 | 来源 | 支持的结论 |
| --- | --- | --- | --- |
| OpenCode v1.2.27 | `session/processor.ts:21`、`:153`，`SessionProcessor.create` | [检测源码](https://github.com/anomalyco/opencode/blob/v1.2.27/packages/opencode/src/session/processor.ts#L153) | 阈值、parts 范围、精确参数比较与权限请求 |
| OpenCode v1.2.27 | `agent/agent.ts:59`，`Agent` 默认权限 | [权限源码](https://github.com/anomalyco/opencode/blob/v1.2.27/packages/opencode/src/agent/agent.ts#L59) | doom_loop 默认 ask |
| OpenCode v1.2.27 | `session/prompt.ts:330`、`:562`、`:674`，`SessionPrompt` | [循环源码](https://github.com/anomalyco/opencode/blob/v1.2.27/packages/opencode/src/session/prompt.ts#L562) | 迭代计数、默认 Infinity、收尾请求仍携带 tools |
| OpenCode v1.2.27 | `session/prompt/max-steps.txt` | [收尾提示](https://github.com/anomalyco/opencode/blob/v1.2.27/packages/opencode/src/session/prompt/max-steps.txt) | 要求明确说明达到上限及未完成事项 |
| 本地 Pi 阅读版 f13d279 | `packages/agent/src/agent-loop.ts:190`、`:334`，`runLoop` | `/data/code/pi` | 循环与 finishTurn 结束钩子 |
| 本地 Pi 阅读版 f13d279 | `packages/agent/src/types.ts:66`、`:322`，`BeforeToolCallResult`、`beforeToolCall` | `/data/code/pi` | block 与批次 terminate 的契约 |
| Robin 当前工作区 | `robin/runtime.py:592`，`Robin.repeated_tool_call` | [本地源码](../../robin/runtime.py) | 最近两次历史参数都相同才拒绝新调用 |
| Robin 当前工作区 | `robin/agent_loop.py:300`，`AgentLoop.run` | [本地源码](../../robin/agent_loop.py) | 达到工具预算后总结可能进入成功收口 |

OpenCode processor 源文件已通过固定 tag 的 raw URL 重新获取并比对 SHA-256，与本次阅读文件一致。其余固定版本文件已阅读；这属于源码核查，不是运行集成测试。

## 4. Robin 的候选方案

以下为研究阶段的候选方案；用户已选择其中两层简单规则，见第 5 节。未被选入本次的建议仍保留供后续研究：

- 将重复保护与普通工具审批分开，auto 仍执行保护。滑动窗口先覆盖精确重复和短周期交替重复；阈值、窗口长度和处理动作待决定。
- 结合重复结果与重复失败判断可能无进展；不要用“没有改文件”单独判定。shell 输出可能受时间、服务状态变化影响，需要控制误报。
- 保留独立工具调用计步，并在执行器硬性拒绝超预算调用；收尾请求不提供工具，最多再获取一次总结，模型异常时也保留预算耗尽事实。
- 终端明确显示预算使用量及未完成状态；持久化使用独立的预算耗尽原因，避免总结文本将其覆盖为成功。具体状态名与现有 `TaskState` 如何兼容尚未设计。
- auto 模式保留具体命令展示；保留当前任务目标，避免“继续”成为历史裁剪后唯一明确的用户目标。这两个问题与重复检测相关，但需要分别实施。

取舍：精确检测易解释但覆盖有限；语义无进展检测更复杂且有误报风险。借鉴 OpenCode 的权限检查和 Pi 的策略钩子，不意味着直接复制它们的计数或停机语义。

## 5. 用户最终决定

2026-11-19 用户明确要求实施两层规则：重复保护独立于审批，auto 仍检测完全相同调用和 A/B/A/B；预算硬限制，耗尽后最多一次纯文本总结，终端明确提示未完成，持久化原因保持 `budget_exhausted`。

本次采用第三次相同调用、第四次 A/B/A/B 调用在执行前拒绝，范围限定为当前用户轮次。拒绝结果回传模型，可改用不同调用或回答；不是任意重复都立即终止整轮。当前不实现语义无进展判断、命令展示或跨轮任务目标保留。

## 6. 实现与验证

**已在本地工作区实现，尚未提交。** `Robin.repeated_tool_call` 检测第三次精确重复和 A/B/A/B；`ToolExecutor.execute` 在审批前保护，并统一计工具预算。预算内的校验与审批拒绝也计步，超预算调用不计步、不执行。

`build_request(finalization=True)` 不提供工具定义；循环最多获取一次收尾响应。正常总结、空响应、原生工具请求、异常和长度截断均不执行额外工具。`TaskState.stop_budget_exhausted` 保持 `stopped` / `budget_exhausted`，终端和最终回答包含已用/上限及任务未完成提示。旧 `step_limit_reached` 接口保留兼容。

已做的核查：重新统计 session 工具记录，读取对应运行状态，检查目标目录是否有文件；阅读 Robin 当前代码；先通过 CodeGraph 定位本地 Pi 核心循环，再核查类型契约；获取并阅读 OpenCode 固定版本源码。

验证：完整 `uv run pytest -q` 216 项通过（7 条依赖或既有弃用警告）；新增测试覆盖 auto 下精确/交替重复、审批前阻断、跨用户轮次隔离，以及收尾成功、空响应、工具请求、报错、截断时的持久化原因与执行器禁止副作用。已有多工具批次测试验证预算外调用按 ID 返回拒绝且不写文件。完整测试后扩大收尾异常捕获范围，并重跑循环、原生工具、安全及权限相关回归，46 项通过；`uv run ruff check robin tests`、`git diff --check` 均通过。

未做：未运行 OpenCode/Pi 进行复现，未使用真实网关或物理终端手动验收。当前没有与本主题运行时改法关联的 commit。

## 7. 复盘与自测

学到的要点：工具成功不代表任务推进；精确重复不等于语义无进展；提示词要求停止不等于执行器禁止；输出总结不应抹掉预算耗尽事实。

1. OpenCode 的 lastThree 来自哪一层消息？为什么不能保证拦住跨模型轮次的 A/B 交替调用？
2. 一次响应提出三个工具时，按模型轮次和按工具调用计步有什么差别？预算只剩一步时应如何处理剩余调用？
3. 如何区分只读调查正在推进与无进展循环？为什么预算耗尽后的总结不能直接代表任务完成？

## 决策变更记录

2026-11-19：由待决定转为实施用户指定的两层简单规则；语义无进展检测继续留作候选，不扩张本次范围。
