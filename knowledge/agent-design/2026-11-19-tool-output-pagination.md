# 工具输出截断与重复读取

- 创建日期：2026-11-19
- 更新日期：2026-11-19
- 状态：已验证
- 参考项目：本地 Pi 阅读版 `f13d2791191cfcc557c6a30af8446e836b1741a8`；本地 OpenCode `56dc95bff5f002321a12de5bbbe2783cd0c87e2a`
- 关键词：工具输出、截断、续读、offset、重复读取

## 1. 问题与现场

session `/data/code/nvd_downloader/.robin/sessions/20261119-143529-fc8bfc.json` 的请求是更换 NVD 下载接口。运行时间约 90 秒，50 次工具尝试中有一次列目录、38 次 read_file、11 次 shell（全部用 sed 读同一文件），没有修改文件、测试或访问其他接口。

read_file 的相同参数分别出现 18、14、5 次；5 次被重复保护拒绝，切换范围或 shell 后仍继续。41 个工具结果超过 4000 字符而被截断；例：请求 112–400 行，返回内容只到约 218 行。模型没有按实际可见范围续读。最后一次无工具总结仍收到工具调用，未执行；最终状态保持 stopped / budget_exhausted，50/50。此次输入 143217、输出 3770 token（接口报告）。

## 2. 参考项目如何解决

### Pi 阅读版

源码事实：read 支持 offset / limit，默认输出边界 2000 行或 50 KiB。truncateHead 按完整行收集，不在行中任意截断；第一行超过字节限额时单独返回处理建议。返回实际起止行、文件总行数与下一次 offset；用户 limit 导致提前结束时也给出续读位置。

bash 使用尾部有界输出，截断时保留完整输出文件路径，模型可进一步读取文件。默认 read 工具指引要求用 read 查看文件。

在此前核对的核心循环与本次检索的 agent-loop、agent-session 中未发现默认语义无进展或重复调用检测。扩展回调可以控制工具或轮次，但不等于默认安装了检测器。本地远端是 luffy997/pi-note 阅读修改版，不能直接代表官方原版。

### 当前本地 OpenCode

源码事实：read 输入 offset / limit；文本输出使用 content、offset、lineCount、next 表达读取页面。默认上限 2000 行、50 KiB，单行另有限长策略（2000 字符）。toModelContent 将结构转换为实际显示行范围和 next 续读提示。工具描述还建议大文件用 grep 定位，避免许多小切片。

shell 大输出保留完整输出文件并返回有界预览。读取工具的分页与通用输出限额是不同层，不能以某个读取限额推断所有最终消息都完整进入模型。

在当前 packages/core/src 执行路径和本次仓库检索中，doom_loop 只找到兼容配置与 UI 等入口，未定位到可确认的默认触发器。此前研究的 v1.2.27 有连续三次相同工具、相同参数的审批机制，详见既有主题；它不能覆盖任意跨间隔重复、read 与 sed 等价读取或语义无进展。

**推断：**明确的续读位置能减少因输出不完整导致的重读；不能保证模型遵守，也不能证明这个 session 在换用工具后必然成功。

## 3. 源码阅读入口

| 项目与版本 | 文件、符号与关键行 | 来源链接或本地定位 | 支持的结论 |
| --- | --- | --- | --- |
| Pi f13d279 阅读版 | createReadToolDefinition，158 行 | `/data/code/pi/packages/coding-agent/src/core/tools/read.ts` | 完整行与 nextOffset 提示 |
| Pi f13d279 阅读版 | truncateHead，79 行 | `/data/code/pi/packages/coding-agent/src/core/tools/truncate.ts` | 行/字节双限制、完整行截断 |
| Pi f13d279 阅读版 | bash 输出，326 行 | `/data/code/pi/packages/coding-agent/src/core/tools/bash.ts` | 完整输出文件路径 |
| OpenCode 56dc95b | TextContent，75 行 | `/data/code/opencode/packages/core/src/tool/read-filesystem.ts` | 结构化 next、offset、lineCount |
| OpenCode 56dc95b | toModelContent，340 行附近 | `/data/code/opencode/packages/core/src/tool/plugin/read.ts` | 显示行范围、明确续读 offset |
| OpenCode 56dc95b | Output，94 行 | `/data/code/opencode/packages/core/src/tool/plugin/shell.ts` | 完整输出 outputPath |
| Robin 当前工作区 | tool_read_file / execute | `robin/tools.py:176`、`robin/tool_executor.py:143` | 行范围读取后被通用字符截断 |

固定版本入口：[OpenCode read 工具](https://github.com/anomalyco/opencode/blob/56dc95bff5f002321a12de5bbbe2783cd0c87e2a/packages/core/src/tool/plugin/read.ts)。

## 4. Robin 的候选方案

助手建议；用户已同意，实施结果见第 6 节：

1. 优先修复读取结果契约：完整行截断，记录实际范围、next_start、是否到文件末尾；超长单行单独处理。确保执行器及模型上下文的后续裁剪不会截掉续读提示。
2. shell 大输出保留有界预览与完整输出路径，明确恢复读取方式；路径依然受工作区权限约束。
3. 若加强跨间隔重复保护，按文件版本与已返回范围记录，文件改变后允许重新读取。简单统计同一路径调用次数会误伤正常开发。无需先解析任意 shell 的语义。
4. 工具结果契约、历史信息保留、重复保护与硬预算分别负责不同层次。不能只增加提示词，也不应声称上游提供通用语义进展判定。

## 5. 用户最终决定

用户在研究后回复“同意”，并随后要求“继续”，同意落实读取分页、shell 完整输出与结合文件版本的重复读取保护。

## 6. 实现与验证

已实施工作区改动：`robin/tool_output.py` 生成完整行页面与续读标记；`tools.py` 的读取工具使用页面；`providers/context.py` 再次缩减结果时重算 next 并保留 shell 完整输出路径。超长单行明确返回未展示，并提示 shell 提取片段。

`tool_executor.py` 在 shell 输出超过 4000 字符时保存随机脱敏文件，预览保留退出码和读取入口。同一轮、同一文件内容哈希下，一个范围已被两次完整返回时，后续被既有范围完整覆盖的读取会拒绝；扩展到新内容的读取和文件修改后重读仍可执行。范围记录限当前进程，resume 后重新积累，reset 清空；不解析 shell 的语义等价。工件尚无自动清理，超时异常输出仍走原有错误处理，不承诺保存。

新增回归用例验证分页无漏行、上下文缩减后续读、超长行、跨间隔重复拒绝、扩大范围及修改后重读、shell 失败退出码与保存结果脱敏。针对性测试 21 项通过；`uv run pytest -q` 全量 228 项通过（7 条既有弃用警告），`uv run ruff check robin tests` 与 `git diff --check` 通过。未进行真实模型或真实接口对照，不能证明彻底消除循环。没有自动 Git 提交。
既有循环保护及预算的已验证范围见[重复调用主题](2026-11-19-loop-detection-and-budget-exhaustion.md)；开发工作指引见[探索与实现主题](2026-11-19-exploration-to-implementation.md)。本次现场证明这些保护不等同于任务能完成。

## 7. 复盘与自测

- 为什么请求 end=400 不意味着模型实际看到了第 400 行？
- 为什么必须用实际返回行数计算续读位置，且保护续读提示不被后续裁剪？
- 为什么同一文件读了很多次不能直接证明无进展，文件版本有何作用？

## 决策变更记录

2026-11-19：审计 session `20261119-151602-abfb9c`，15:16:40 工具结果已累计覆盖文件 266 行，但后续仍然重读。共 51 个调用，其中一个超预算未执行；22 个重复读取拒绝，4 个 shell 都是 cat 读取源文件，没有源码修改。原区间判断只检查单独页面，且局部推算的 next 把已读范围甚至 267 行错误推荐为未读。历史裁剪也意味着“工具返回过”不等于“当前模型可见”。

用户回复“接受”，同意合并范围、修正全文已读提示，并保留必要代码上下文。工作区新增 merge_ranges；局部重复提示根据区间并集计算首个未读行，EOF 后不再给出虚假续读指令。最近完整读完的小文件（输入字节与解码字符均不超过 12000）保留一个全文数据块到实际模型请求中，此版本再次读取会拒绝。缓存每次请求重新校验工作区路径、内容哈希及用户轮次，reset 清空；较大文件仍按原来的有界页面保护处理，未声称已保留全文。额外数据块纳入模型输出预算估算，但独立于现有 section 字符预算。只缓存当前进程，未增加恢复持久化，也未识别 shell 的等价读取。

新增针对合并全文覆盖、历史预算极小时全文仍进入请求、内容变化失效、reset、续读跳过所有已读区间的回归；针对性 20 项通过。全量 `uv run pytest -q` 230 项通过，安全相关测试单独 20 项通过；Ruff 与 diff 检查通过。无真实模型对照结果。

2026-11-19：用户要求修复代码审查 P1“缓存校验可能让 Agent 永久卡住”。原 `read_context()` 直接 `read_bytes()`，缓存路径被替换为 FIFO 时可能阻塞，文件增长后也会无界读取。现由 `_cached_file_matches()` 先检查普通文件和 12000 字节大小限制，以 `O_NONBLOCK | O_NOFOLLOW` 打开，再用 `fstat` 检查打开的对象，最多读取 12001 字节；不符合条件或发生 I/O 错误时淘汰缓存。打开后的检查覆盖检查与打开之间被替换为 FIFO 的竞态，不跟随替换后的符号链接；限量读取覆盖检查后文件增长。仍不承诺中断底层普通文件系统 I/O。

验证：针对性读取与安全测试 33 项通过；全量 237 项通过（7 条既有弃用警告）。随后补充符号链接替换参数用例，读取测试文件最终 14 项全部通过；Ruff 与 `git diff --check` 通过。本次仅修复缓存校验 P1，分页标记被正文伪造的 P2 尚未修复。无真实模型对照，无自动提交。
