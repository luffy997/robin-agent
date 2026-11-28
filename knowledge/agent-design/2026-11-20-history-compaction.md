# 历史裁剪与任务进度保留

- 创建日期：2026-11-20
- 更新日期：2026-11-28
- 状态：已验证
- 参考项目：本地 OpenCode `56dc95bff5f002321a12de5bbbe2783cd0c87e2a`；本地 Pi 阅读修改版 `f13d2791191cfcc557c6a30af8446e836b1741a8`
- 关键词：上下文、历史裁剪、compaction、进度摘要、重复读取

## 1. 问题与现场

用户认为 Robin 历史裁剪过于激进，并询问 OpenCode、Pi 的机制。session `/data/code/nvd_downloader/.robin/sessions/20261120-163900-04586b.json` 在约 112 秒内执行 50 次工具尝试：37 次读取（14 次拒绝）、7 次仅查看文件的 shell、4 次 patch（2 次因缺少 path 拒绝）、2 次列目录。加入 HTTP 处理器后，没有接通 CLI 或进行测试，却在预算耗尽的总结中宣称完成并给出不存在的 serve 命令。

Robin 原生请求默认仅保留 5200 字符序列化历史，包含工具参数、ID 和结果；超限直接丢弃较早完整消息组。修改后源文件 15017 字节，超过 12000 字节全文保留上限。执行器保留已读状态而模型历史可能不再包含正文，是设计冲突；它是否是此次模型失误的唯一原因未经对照验证。模型曾明确回应应补 path，说明最初收到工具错误。

## 2. 参考项目如何解决

### 本地 Pi 阅读修改版：源码事实

`AgentSession._compactBeforeNextAssistantResponse()` 从 session projection 估算使用量；未到阈值直接使用 projection.messages。`shouldCompact()` 判断 `contextTokens > contextWindow - reserveTokens`。默认自动压缩开启，reserveTokens=16384、keepRecentTokens=20000，设置支持模型级覆盖。以 128000 token 窗口为例，阈值为 111616；近期保留量是压缩后的目标，不是每次请求的固定历史上限。

`estimateContextTokens()` 优先使用最近接口用量并估计后续新增消息；projection 路径另有对上下文编辑的处理，不能将旧用量机械应用到任意重写历史。切分点不选 toolResult，避免从孤立结果开始；拆分长轮次时可以单独总结轮次前半部分。摘要模板包含 Goal、Constraints、Done、In Progress、Blocked、Key Decisions、Next Steps、Critical Context，并记录读过和修改过的文件。摘要及 firstKeptEntryId 等保存为 compaction entry，用于后续请求重建；支持 threshold 与 overflow 恢复。

本地仓库为既有阅读修改版，上述结论限定当前文件，不将新增 harness 或 projection 设计泛指官方 Pi 原版。

### 本地 OpenCode：源码事实

`SessionCompaction` 默认 auto=true、keep=15000、background=true。`calculateCeiling()` 以有效输入窗口（同时存在 input/context 时取较小者）减去缓冲计算阈值。默认缓冲为窗口 15%，窗口至少 32000 时至少预留 16000；允许显式配置 tokens 或百分比。有效窗口为 128000 时阈值为 108800。

`estimatePrompt()` 结合接口测量和未测量新增内容；`due()` 在接近阈值时触发。摘要模板明确保留目标、要求、决策、Completed/Active/Blocked、Next Move、相关文件和关键上下文。普通摘要路径将摘要与最新 keep token 的文本上下文组成 conversation-checkpoint，再接新消息；近期保留在此路径是文本化上下文，不能称为原生工具消息原样保留。另有 provider native compaction 分支、后台预生成摘要与上下文溢出后逐级缩减恢复。摘要生成仍可能失败或遗漏，不能保证任务成功。

## 3. 源码阅读入口

| 项目与版本 | 文件、符号与关键行 | 来源链接或本地定位 | 支持的结论 |
| --- | --- | --- | --- |
| Pi f13d279 阅读版 | settings-manager.ts DEFAULT_COMPACTION_TOKEN_SETTINGS:18 | `/data/code/pi/packages/coding-agent/src/core/settings-manager.ts` | 16384 / 20000 默认值 |
| Pi f13d279 阅读版 | compaction.ts estimateContextTokens:218、shouldCompact:289、isCutPointMessage:373、SUMMARIZATION_PROMPT:529 | `/data/code/pi/packages/coding-agent/src/core/compaction/compaction.ts` | 估计、阈值、安全切分、摘要模板 |
| Pi f13d279 阅读版 | agent-session.ts _compactBeforeNextAssistantResponse:590、_runAutoCompaction:2735 | `/data/code/pi/packages/coding-agent/src/core/agent-session.ts` | 请求前检查与持久化压缩 |
| OpenCode 56dc95b | compaction.ts defaults:266、due:430、splitConversation:1002、calculateCeiling:1151 | `/data/code/opencode/packages/core/src/session/compaction.ts` | 默认值、阈值、近期保留 |
| OpenCode 56dc95b | compaction.ts SUMMARY_TEMPLATE:170、普通摘要请求说明:443 | `/data/code/opencode/packages/core/src/session/compaction.ts` | 工作状态与 checkpoint |
| Robin 当前仓库 | build_request:74 | `robin/providers/context.py` | 字符历史预算及直接丢弃 |

固定版本来源：[OpenCode compaction](https://github.com/anomalyco/opencode/blob/56dc95bff5f002321a12de5bbbe2783cd0c87e2a/packages/core/src/session/compaction.ts)。本次核对本地源码，没有断言这些是最新发行版。

## 4. Robin 的候选方案

助手建议：优先参考 Pi 的较简单方案，按模型窗口和请求 token 预算判断是否压缩；容量充足时保留完整有效历史，接近阈值再生成持久化进度摘要并保留近期完整调用与结果。压缩输出应包含成功/失败的修改、未完成步骤和测试证据；摘要失败不应无声丢掉历史。重复读取保护应结合当前可见代码，避免裁剪后禁止重新获取必要正文。

不宜直接把 5200 字符改成 20000 token 就结束，也不宜同时照搬后台摘要和 native compaction。Robin 现有一 UTF-8 字节估为一 token 的保守算法也需纳入预算设计，避免换名后仍过早裁剪。单工具输出分页与会话历史压缩是不同层次。

## 5. 用户最终决定

2026-11-20：用户回复“可以，按照你说的做”，同意按模型容量管理历史、接近上限再摘要、保留近期工具交互与任务进度，并协调重复读取保护。

## 6. 实现与验证

已实施：`history_compaction.py::compact_history` 保留容量内全部有效历史，预留输出、4096 token 安全余量与摘要空间。接近上限时通过现有 ModelClient 请求无工具的结构化进度摘要，保留近期完整工具组（目标最多约 20000 token，小窗口按可用预算缩小）。成功且压缩后容量校验通过才保存 `session.history_compaction` 的 summary、through、created_at；原始历史保留。resume 使用边界和摘要，reset 清除。摘要调用及失败返回的用量计入本轮、会话及 delegate；压缩不消耗工具步数。

压缩成功后清空旧读取范围与全文缓存，使已退出上下文的代码可重新读取；下一次读取仍受正常范围保护。文件数据块改为用户数据消息，不追加进系统指令。`providers/limits.py::estimate_input` 改为 ASCII 三字符估一 token、非 ASCII 每字符估两 token，加消息开销；是本地估算，不混入接口用量。既有 ContextManager 的字符预算继续用于系统/记忆及诊断文本，原生历史不再按该字符预算丢弃。

限制：未增加供应商原生压缩、后台摘要或摘要模型选择；摘要请求自身超限、最新单组过大、摘要失败或未释放足够空间时明确停止并保留原历史，不静默截断。未实现接口上下文溢出后的自动重试。摘要内容依赖模型，不保证无损；未运行真实模型对照或上游测试。

验证：针对原生消息、读取、模型预算及压缩的 47 项回归通过；压缩和安全相关测试 27 项通过。全量 `uv run pytest -q` 247 项通过，7 条既有弃用警告；`uv run ruff check robin tests`、`git diff --check`、`uv build` 通过。新增用例覆盖容量内保留超过旧字符预算的历史、完整调用结果组、错误证据进入摘要输入、恢复/reset、重复压缩、失败不推进边界、摘要用量不重复计数及单组超限明确停止。测试使用 FakeModelClient，不证明真实模型摘要质量或此次 NVD 任务一定成功。

## 7. 复盘与自测

- 为什么 keepRecentTokens 是压缩后的保留目标，而不是平时的历史容量？
- 接口用量之后新增工具结果如何计入下次请求的估计？
- 摘要需要保留哪些进度证据，才能避免把失败修改当成已完成？

## 决策变更记录

2026-11-20：从候选方案转为用户已决定并在工作区实施；没有自动提交。

2026-11-23：用户要求增加压缩快捷命令，新增交互 `/compact` 并接入 slash 菜单和帮助。
调用既有压缩链路，允许在阈值之前生成摘要；短会话整体归纳，空会话或无新增历史不请求模型。
原始记录、用量统计、恢复及失败保留行为沿用既有实现，不启动工具或新任务。
针对压缩及终端的 25 项测试通过，Ruff 与差异检查通过；无真实模型验证。


2026-11-28：用户要求用 LiteLLM 的 token 计数替代字符估算。已实施
`providers/limits.py::estimate_input` 优先调用当前固定依赖 LiteLLM 1.110.0 的
`token_counter(model=当前模型名, messages=结构化消息, tools=工具定义)`；
`output_budget`、`compact_history` 的触发判断、近期保留、摘要请求和压缩后校验均传递同一模型名。
保留 4096 token 安全余量。无模型名（如 FakeModelClient）继续使用字符预算；
计数异常或返回无效/禁用计数的零值时记录警告并退回字符预算。
未知模型可能由 LiteLLM 使用通用 tokenizer，不能承诺与供应商账单一致。
源码核验了安装版的 `token_counter`、`_MessageCountParams` 与 tokenizer 选择路径；
在线文档访问失败，未依赖未获取的文档结论，也未重跑上游研究。

新增测试覆盖模型名、消息与工具传递、预算计算、异常和无效计数回退，
并使用实际 LiteLLM 的 gpt-4o tokenizer 验证中文、代码、工具定义与调用参数计数。
压缩集成测试覆盖摘要和最终请求共用模型计数。首批 41 项预算/历史/原生消息测试通过；
全量 `uv run pytest -q` 为 267 项通过、1 项失败：
`test_reviewer_skeleton_docs_exist` 因当前工作区缺少 `docs/review-pack/README.md` 失败，
与本次计数变更无关；新增与相关回归通过。Ruff、`git diff --check` 通过。
未调用真实供应商接口验证账单 token 对齐。
