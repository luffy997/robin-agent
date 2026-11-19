# 工具执行前后的文件变化记录与快照范围

- 创建日期：2026-11-19
- 更新日期：2026-11-19
- 状态：已验证
- 参考项目：本地 OpenCode `56dc95bff5f002321a12de5bbbe2783cd0c87e2a`；本地 Pi 阅读版 `f13d2791191cfcc557c6a30af8446e836b1741a8`
- 关键词：工作区快照、Git index、依赖目录、目标文件、大文件、阻塞

## 1. 问题与现场

用户询问 `/data/code/opencode` 和 Pi 如何处理工具执行前后的文件变化记录。
起因是 session `20261119-114940-98e569`（位于 `/data/code/.robin/sessions/`）停在 write_file 前。
模型于北京时间 11:50:22 返回写入请求，但没有工具结果。检查到进程打开了 Pi 的 node_modules 文件，内核栈处于文件读取等待。
Robin 的 `capture_workspace_snapshot` 对 `/data/code` 递归并逐文件 `read_bytes()` 计算 SHA-256；动画在前置快照之后才启动。
此前建议扩大 cwd 没有考虑全工作区扫描成本。这里是前置审计工作阻塞，不是模型还没生成答案。

## 2. 参考项目如何解决

### 本地 OpenCode：范围受限的 Git 树快照

源码事实：当前本地代码已使用 `packages/core` 架构，与上一主题研究的 v1.2.27 不同，不混用版本结论。
`Snapshot.capture` 仅在 location 的 VCS 类型是 Git 且快照启用时工作；其他情况返回 undefined。
它为工作树建立独立的快照 Git 仓库，scope 使用当前 location 相对工作树的位置，继承源仓库忽略规则。

底层 `Git.tree.capture` 通过临时 index、Git 变化枚举和对象树记录变化；未跟踪文件使用 `ls-files --others --exclude-standard`，另通过源仓库 check-ignore 过滤候选。
未跟踪文件大于 2 MiB 时跳过；该限制不等于所有文件统一限制 2 MiB。记录过大的文件 stamp、最近 index fingerprint/tree，满足条件时复用上次树，并协调并发 capture。

`SessionStep.attempt` 在模型请求期间并发启动起始快照，本地工具开始前须具备起始快照；step 完成或失败时获取结束快照并比较文件。
它不是在每个工具前后用应用代码完整重读目录。快照失败会记警告并返回 undefined，调用者允许缺少文件变化记录。

边界：Git 仍然可能枚举目录、读取变化文件；依赖目录是否跳过取决于忽略规则，不是名称自动识别。未检查总扫描超时保证，不能声称任意超大仓库都不会慢。未核实该快照之外所有文件 UI 的实现。

### 本地 Pi 阅读版：工具自身操作目标文件

源码事实：检查的内置 write/edit/bash 工具及核心循环没有 Robin 式工具前后全目录哈希扫描。
write 解析目标路径、进入按文件的 mutation queue、建父目录并直接写目标文件。
edit 读取目标文件，完成替换，写回，并从替换前后的内容生成 diff/patch 返回。
bash 执行命令并收集输出；其中 `OutputAccumulator.snapshot` 是输出缓冲快照，不是工作区文件快照。

边界：这不自动提供任意 shell 副作用的全工作区清单，也不证明所有插件都不做快照。
本地 Pi 是 `luffy997/pi-note` 阅读版，不将其扩展直接当作官方所有版本的行为。

## 3. 源码阅读入口

两个本地仓库均先使用 CodeGraph 定位，再读取具体链路。以下 OpenCode 路径位于固定 commit 的 `packages/core/src/`：

| 项目 | 文件与符号 | 定位 |
| --- | --- | --- |
| OpenCode | `snapshot.ts:133`，`Snapshot.capture` | [固定 commit](https://github.com/anomalyco/opencode/blob/56dc95bff5f002321a12de5bbbe2783cd0c87e2a/packages/core/src/snapshot.ts#L133) |
| OpenCode | `git.ts:528`、`:557`、`:771`，`listChanges`、`updateIndex`、`attemptCapture` | [固定 commit](https://github.com/anomalyco/opencode/blob/56dc95bff5f002321a12de5bbbe2783cd0c87e2a/packages/core/src/git.ts#L528) |
| OpenCode | `session/runner/step.ts:80`、`:292`，`SessionStep.attempt` | [固定 commit](https://github.com/anomalyco/opencode/blob/56dc95bff5f002321a12de5bbbe2783cd0c87e2a/packages/core/src/session/runner/step.ts#L80) |
| Pi 阅读版 | `packages/coding-agent/src/core/tools/write.ts:58`，工具 execute | `/data/code/pi` |
| Pi 阅读版 | `packages/coding-agent/src/core/tools/edit.ts:186`，工具 execute | `/data/code/pi` |
| Pi 阅读版 | `packages/coding-agent/src/core/tools/bash.ts:245`，工具 execute | `/data/code/pi` |
| Robin | `robin/runtime.py`，`Robin.capture_workspace_snapshot` | [本地源码](../../robin/runtime.py) |

上游链接用于复查固定 commit；本次证据来自本地源码，没有运行这些参考项目进行性能对比。

## 4. Robin 的候选方案

助手建议：write/patch 参考 Pi，只追踪目标文件；shell 的任意副作用单独采用有边界的变化观察。
若采用 Git 方案，可借鉴 OpenCode 的 scope、忽略规则与增量 index，非 Git 目录需另设有限扫描或明确缺少审计的降级。
扫描须有文件数、单文件/总读取量边界，遍历时剪枝依赖目录，避免先遍历再过滤；不完整快照必须显式标识，不能报告成完整无变化。
活动提示应覆盖快照阶段，但动画不能替代性能修复。具体方案和阈值待决定。

## 5. 用户最终决定

2026-11-19 用户回复“接受”，选择文件工具按目标观察、shell 单独有界追踪的方案。
实现采用轻量元数据观察而非引入独立 Git 快照仓库；不提供全工作区内容审计或撤销能力。

## 6. 实现与验证

已在本地工作区实施，未提交：新增 `robin/snapshots.py`，write/patch 只读取目标文件，读取量最多 2 MiB，超出时退回元数据比较并标记限制；shell 不读取文件内容，比较 size/mtime/ctime/inode/mode。
shell 使用 scandir 在进入依赖、缓存和构建目录前剪枝，最多 2000 条目录项，约 1 秒停止扫描；时间检查发生在文件系统调用之间，不保证中断阻塞 I/O。
不跟随观察文件的符号链接；目标读取使用 O_NOFOLLOW/O_NONBLOCK 并检查普通文件，避免误读 FIFO。
记录已完整枚举目录，部分扫描时不把未观察到的路径误报成创建或删除；完整覆盖目录中的新增与删除仍可识别。
`ToolExecutor` 的活动提示覆盖整个前后快照与执行阶段；成功与异常结果均记录 snapshot_scope、snapshot_complete、snapshot_limitations。
shell 的元数据观察不是内容一致性证明；不完整观察也不能证明没有变化。没有实现 .gitignore 语义或 Git 增量树。

验证：目标观察不遍历其他目录、shell 不读取内容且剪枝 node_modules、文件大小与目录项上限、符号链接及部分快照差异测试通过。既有运行时、安全、权限相关 76 项通过；完整测试 219 项通过（7 条既有或依赖弃用警告），随后新增目标范围和异常写入副作用集成测试单独运行通过；Ruff 与 git diff --check 通过。
未终止现有 Robin 进程，未使用真实网关或执行参考项目性能基准。

## 7. 复盘与自测

要点：审计不是工具核心动作，不能让无边界审计阻塞写一个文件；Git 增量快照与工具级 diff 的成本及覆盖范围不同。

1. 写单个文件为什么通常只需要目标文件的前后状态？shell 为什么不同？
2. OpenCode 的 2 MiB 限制适用于哪些文件？Git 忽略规则与硬编码依赖目录有哪些区别？
3. 快照缺失或被截断时，怎样避免把它错误解释为“没有文件变化”？

## 决策变更记录

2026-11-19：用户接受优化建议，按工具范围拆分观察；Git 快照仓库留待未来确有撤销/全量审计需求时研究。
