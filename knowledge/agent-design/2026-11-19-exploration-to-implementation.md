# 从探索转向实现与测试

- 创建日期：2026-11-19
- 更新日期：2026-11-19
- 状态：已验证
- 参考项目：本地 Pi 阅读版 `f13d2791191cfcc557c6a30af8446e836b1741a8`；本地 OpenCode `56dc95bff5f002321a12de5bbbe2783cd0c87e2a`
- 关键词：过度探索、curl、提示词、实现、测试、阶段控制

## 1. 问题与现场

用户要求在 `/data/code/nvd_downloader` 编写漏洞下载程序。session `20261119-135427-3693a4` 的此前检查发现，已完成的 17 次工具调用包含 16 次 shell 和一次项目指引读取，没有写文件。模型反复改变 NVD 分页 startIndex，探测已知总数附近的边界。

用户认为应该先写程序，再通过测试发现问题，并追问 Pi、OpenCode 是否通过提示词约束工作方式。修改参数的探索不属于完全相同调用或 A/B/A/B 检测范围；工具调用成功也不等于需求已有进展。是否由具体模型能力、上下文裁剪等因素导致，尚未通过对照实验核实。

## 2. 参考项目如何解决

### Pi：精简默认提示，循环交给模型和扩展

源码事实：`buildSystemPromptSections` 组合身份、工具、规则、项目指引、skill 和 cwd。`buildRules` 收集工具贡献和额外规则，默认强调工具用法、简洁回答和清晰路径；所检查的默认构建逻辑没有规定探索若干次后必须写代码。

核心 `runLoop` 在响应有工具调用时执行并继续请求模型。`finishTurn` 可以结束或继续，`beforeToolCall` 提供工具边界控制；会话层还包装了扩展的 turn-end 回调。这些是扩展能力，不能据此推断默认安装了语义进展检测。

本地仓库远端为 `luffy997/pi-note`，有中文阅读注释和修改；上述结论仅代表该固定版本，不能直接当作官方 Pi 当前行为。

### OpenCode：模型专属工作指引与执行层限制分开

源码事实：`OptimizePlugin` 根据模型 ID 选择提示模板，默认使用通用系统提示；存在 agent 自定义 system 时不覆盖。不能把某个模板的规则当成所有模型都会收到的规则。

- Trinity 模板建议理解代码与需求、实现、测试；工具策略要求获得有用结果后采取下一步，不要继续以相同参数重复搜索。
- Kimi 模板明确要求用 write/edit 落盘，然后用 shell 测试，失败后修改并重测。
- 通用系统提示主要描述工具和工作约定，没有上述完整流程。

在检查的 runner 路径中，没有发现按 curl 次数强制从探索切到实现的默认状态机。预算是另一种确定性边界：配置了 agent steps 且达到限制时，增加总结提示，设置 `toolChoice: none`；工具执行边界再次拒绝调用。该计数是 runner 的模型步骤，不能直接等同 Robin 按工具尝试计步。

**来源差异：**此前研究的 OpenCode v1.2.27 使用另一套源码路径。当前本地版本已经有执行层预算拒绝，不能沿用旧版“主要依赖提示词”的描述覆盖当前版本。

## 3. 源码阅读入口

| 项目与版本 | 文件、符号与关键行 | 来源链接或本地定位 | 支持的结论 |
| --- | --- | --- | --- |
| Pi 阅读版 f13d279 | `buildRules`、`buildSystemPromptSections`，82、120 行 | `/data/code/pi/packages/coding-agent/src/core/system-prompt.ts` | 默认提示组合与扩展规则 |
| Pi 阅读版 f13d279 | `runLoop`，305、336 行 | `/data/code/pi/packages/agent/src/agent-loop.ts` | 工具驱动循环、finishTurn 边界 |
| Pi 阅读版 f13d279 | `_installAgentBoundaryHooks`，678 行 | `/data/code/pi/packages/coding-agent/src/core/agent-session.ts` | 扩展可请求继续，不等于默认进展检测 |
| OpenCode 56dc95b | `OptimizePlugin`，16、67 行 | `/data/code/opencode/packages/core/src/plugin/optimize.ts` | 按模型选择模板，尊重 agent 自定义 system |
| OpenCode 56dc95b | Doing tasks、Tool usage policy，73、86 行 | `/data/code/opencode/packages/core/src/plugin/system-prompt/trinity.txt` | 实现、测试与避免重复搜索的软约束 |
| OpenCode 56dc95b | General Guidelines for Coding，23、30 行 | `/data/code/opencode/packages/core/src/plugin/system-prompt/kimi.txt` | 写代码、运行测试、失败迭代 |
| OpenCode 56dc95b | `stepLimitReached`，261、280 行 | `/data/code/opencode/packages/core/src/session/runner/llm.ts` | 预算到达后 toolChoice none |
| OpenCode 56dc95b | `executeTool`，118 行 | `/data/code/opencode/packages/core/src/session/runner/step.ts` | 执行边界拒绝工具 |
| Robin 当前工作区 | `build_prompt_prefix`，62 行 | `robin/prompt_prefix.py` | 开发流程、围绕阻塞点探索、模拟测试与有限真实请求 |

OpenCode 固定版本入口：[optimize.ts](https://github.com/anomalyco/opencode/blob/56dc95bff5f002321a12de5bbbe2783cd0c87e2a/packages/core/src/plugin/optimize.ts)。其余文件可在同一 commit 下定位。

## 4. Robin 的候选方案

以下为研究时的助手建议；第 1、2 项已按用户同意实施，第 3 项的额外进展提醒未实施：

1. 借鉴 OpenCode，为开发任务明确“必要信息足够后写最小实现，再用测试暴露问题”的工作指引；对真实 API 的探索需服务于具体未知点。不要把完成所有边界探测当作写代码的前提。
2. 网络下载器优先通过本地模拟响应验证分页、空页、失败与重试，再做少量真实连通性验证。不是禁止 curl，也不是所有任务统一限制探测次数。
3. 保留 Robin 已有重复保护与硬预算。若仍需进展提醒，应单独设计证据与误报边界；未写文件不总是无进展，不能将只读调查任务强制变成写代码任务。

提示词影响动作选择，预算保证副作用停止；两者都不能证明语义上一定完成任务。

## 5. 用户最终决定

2026-11-19，用户回复“可以”，同意先补充开发工作指引：必要信息足够后写最小实现，再测试和修正；保留已有重复保护与预算。本次未选择新增阶段状态机或语义进展检测。

## 6. 实现与验证

已修改工作区中的 `robin/prompt_prefix.py::build_prompt_prefix`，增加四条工作指引：开发任务先做必要调查、再实现和验证；探索围绕具体阻塞点；网络边界优先模拟测试，真实请求用于有限核实；只读任务不擅自改代码，最终报告实际检查和阻塞。

验证：`uv run pytest tests/test_prompt_prefix.py tests/test_context_manager.py tests/test_native_loop.py -q`，23 项通过；`uv run ruff check robin tests` 和 `git diff --check` 通过。使用已有测试验证提示组装、上下文与原生循环没有回归；未调用真实模型，没有证据证明提示词能彻底消除过度探索。没有新增镜像提示文案的测试，也未进行真实网络验证。

状态“已验证”仅指上述代码检查。已有重复保护和预算见[独立主题](2026-11-19-loop-detection-and-budget-exhaustion.md)。未创建 Git 提交；正在运行的进程需重启后加载新的提示构建逻辑。

## 7. 复盘与自测

- 为什么某个模型提示模板包含开发流程，不代表所有模型都采用同样规则？
- Pi 的 finishTurn 能提供控制边界，为什么不能据此说它默认识别无进展？
- curl 参数持续变化时，提示词、重复检测和预算各能解决什么问题？

## 决策变更记录

暂无。
