# 项目指引

开发与验证约定见 [CLAUDE.md](CLAUDE.md)。

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output. Run it in the target repository directory.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->

## 自动沉淀开源 Agent 研究

当用户询问开源 agent 如何解决某个问题、比较其设计，或要求参考其实现改造 Robin 时，主动读取并执行共享 skill：
[agent-learning](.agents/skills/agent-learning/SKILL.md)。项目包括但不限于 OpenCode、Pi 和 Codex。

后续选择或调整方案、要求实施、完成实现时，同样读取 skill，检索并更新对应主题。普通提及项目名称不触发。
研究完成先记录，未确定的 Robin 改法标为待决定；不要等到实现完成才记录。
这是助手遵循项目规则的工作流，不是后台 hook。只读和规划模式下不写文件；在恢复可写模式后处理尚未沉淀的内容。

知识库入口：[Agent 设计学习索引](knowledge/agent-design/README.md)。
