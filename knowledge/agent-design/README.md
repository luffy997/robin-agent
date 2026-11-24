# Agent 设计学习索引

带着 Robin 的真实问题阅读开源实现，再记录用户选择和实际验证。
共享工作流见 [agent-learning](../../.agents/skills/agent-learning/SKILL.md)，
新笔记使用其[统一模板](../../.agents/skills/agent-learning/references/note-template.md)。

按设计问题组织，每个问题持续更新同一篇；日期为笔记最近更新日期。
状态表示证据进度，不代表只要模型输出了总结就完成了任务。

| 主题 | 参考项目 | 状态 | 更新日期 |
| --- | --- | --- | --- |
| [架构文档组织与源码核验](2026-11-24-architecture-documentation.md) | 本地 Ally、Robin | 已验证 | 2026-11-24 |
| [历史裁剪与任务进度保留](2026-11-20-history-compaction.md) | 本地 OpenCode、本地 Pi 阅读版、Robin | 已验证 | 2026-11-23 |
| [工具输出截断与重复读取](2026-11-19-tool-output-pagination.md) | 本地 OpenCode、本地 Pi 阅读版、Robin | 已验证 | 2026-11-19 |
| [从探索转向实现与测试](2026-11-19-exploration-to-implementation.md) | 本地 OpenCode、本地 Pi 阅读版、Robin | 已验证 | 2026-11-19 |
| [文件变化记录与快照范围](2026-11-19-workspace-snapshot-scope.md) | 本地 OpenCode、本地 Pi 阅读版、Robin | 已验证 | 2026-11-19 |
| [重复调用检测与预算耗尽](2026-11-19-loop-detection-and-budget-exhaustion.md) | OpenCode v1.2.27、本地 Pi 阅读版、Robin | 已验证 | 2026-11-19 |
