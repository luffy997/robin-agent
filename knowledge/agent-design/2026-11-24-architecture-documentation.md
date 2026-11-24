# 架构文档组织与源码核验

- 创建日期：2026-11-24
- 更新日期：2026-11-24
- 状态：已验证
- 参考项目：本地 Ally `47250a80f7d9c0ec29de335ace0bf7457f6d06a0`；Robin `df16a93` 工作区
- 关键词：架构文档、README、源码核验、能力边界、面试复盘

## 1. 问题与现场

用户希望参考 `/data/code/ally-agent` 下的架构文档和 README，为 Robin 编写同类文档，并选择两篇都使用中文。原 README 混合产品使用与内部机制，部分描述已过时；`docs/agent-architecture.md` 为空，且被 Git 忽略。

## 2. 参考项目如何解决

文档事实：Ally 的 README 先介绍产品、功能与启动方式，再链接具体能力说明；中文架构文档按一次对话生命周期组织，用流程图和时序图解释循环、上下文、工具、持久化与界面，附机制取舍和参考资料。

本次借鉴信息组织方式，没有核实 Ally 文档中的全部运行时机制，因此不将其能力描述作为上游源码结论，也不将其并发、流式、MCP 或桌面功能归到 Robin。

本地 Ally 工作区另有 CODEGRAPH.md 改动和未跟踪文档；所参考两篇文档在本次查看的 Git 状态中没有改动。版本定位是本地证据，不代表对远端最新版本的判断。

## 3. 源码阅读入口

| 项目与版本 | 文件、符号与关键位置 | 来源链接或本地定位 | 支持的结论 |
| --- | --- | --- | --- |
| Ally `47250a8` | README.md：Features、Getting started | `/data/code/ally-agent/README.md` | 产品入口组织 |
| Ally `47250a8` | docs/agent-architecture.md：目录、全景、核心循环 | `/data/code/ally-agent/docs/agent-architecture.md` | 生命周期、图示和机制解释方式 |
| Robin `df16a93` 工作区 | AgentLoop.run、ToolExecutor.execute | `robin/agent_loop.py`、`robin/tool_executor.py` | 同步循环、顺序工具调用、预算与护栏 |
| Robin `df16a93` 工作区 | build_request、compact_history | `robin/providers/context.py`、`robin/history_compaction.py` | 原生历史与字符预算区别、压缩边界 |
| Robin `df16a93` 工作区 | SessionStore.save、spawn_delegate | `robin/session_store.py`、`robin/runtime.py` | 非原子写入、受限只读委派与恢复限制 |

源码入口先通过 CodeGraph 定位，再按需核对相关实现；函数符号比可能漂移的行号更适合持续阅读。

## 4. Robin 的候选方案

助手建议：README 保持使用入口，架构文档解释机制和边界；以源码核对的当前能力为主体，不把研究建议当作已有实现。图示用于解释请求流转，细节通过源码入口继续阅读。

取舍：两篇中文文档更适合用户学习和复盘，本次不维护双语副本。文档不替代测试和真实网关联调，也不承诺未经验证的发布与安装能力。

## 5. 用户最终决定

2026-11-24：用户选择“两篇都用中文”，随后接受文档方案，包括单独放行架构文档的 Git 跟踪、保留 docs_inner 用户内容、不修改运行时，以及不自动提交。

## 6. 实现与验证

工作区已重写 README，新增中文架构详解和 Mermaid 图，调整 `.gitignore` 仅放行目标架构文件。修正读取次数、终端能力与独立二进制的旧描述，明确上下文估算、审批非沙箱、会话非原子持久化和未实现的 intent 核验。

已核对 `uv run robin --help`、配置模板和相关源码。四篇文档共 58 个相对链接和导航目标通过检查，代码围栏配对正确，五张 Mermaid 图通过结构检查；本机未安装 Mermaid 渲染器，未做渲染验收。确认 docs 下仅目标架构文档可被新增跟踪，CV 文档仍被忽略。Ruff 和 `git diff --check` 通过；纯文档变更未重跑 pytest，不运行真实模型，不修改运行时代码，不自动提交。

## 7. 复盘与自测

- 功能清单应回答用户能做什么，架构文档应解释它为什么能工作、哪里不能保证。
- 借鉴参考文档结构，不等于可以借用参考项目的能力声明。
- 为什么 session 恢复不能直接等同于副作用只执行一次？
- 为什么上下文字符预算不能直接代表最终发送的历史长度？
- 为什么需要从实际调用链核实停止条件，而不是只阅读状态枚举？

## 决策变更记录

暂无。
