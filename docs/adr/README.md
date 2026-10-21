# Architecture Decision Records

ADR 用来记录“为什么做出一个长期架构决定”，不是所有 PR 的审批流程。状态表示记录的决策状态；`Accepted` 不等于已经发布。

## 已接受的长期决策

已接受的剧情表现决策见 [ADR-0006：共享结构化剧情片段](0006-structured-story-presentation.md)。

内核状态、可选对局偏好与 44→45 存档拆分见 [ADR-0007：运行时状态归属](0007-runtime-state-ownership.md)（本地实现，尚未发布）。

自由计划、幕间成长、只读结团与 45→46 读者隔离见 [ADR-0008](0008-run-plans-and-finales.md)（本地实现，尚未发布）。

资源恢复 authority 见 [ADR-0010：资源恢复的服务端 authority](0010-resource-recovery-authority.md)（本地实现，尚未发布）。

DF Agent 的统一应用动作目录、动态 descriptor、有限服务端授权与领域 authority 边界见 [ADR-0011：应用动作与 Agent 委托](0011-application-actions-and-agent-delegation.md)（`Accepted`，本地实现，尚未发布）。当前实现范围和剩余边界见 [DF_AGENT_IMPLEMENTATION.md](../design/DF_AGENT_IMPLEMENTATION.md) 与 [DF_AGENT_CAPABILITY_EVOLUTION.md](../design/DF_AGENT_CAPABILITY_EVOLUTION.md)。

[ADR-0010：GAL 作为第一个非跑团玩法](0010-gal-first-non-trpg-gameplay.md) — `Accepted`；gal 立项与八项边界决策（定位、组合式实现、好感度模块、推进模式、节点权威、transport 声明、立绘资产、合规边界）。施工顺序见[轨道 P 计划](../GAL_TRACK_PLAN.md)。

以下记录仍是提案：

- [ADR-0005：通用底座、玩法拼装与具体游戏](0005-composable-game-platform.md) — 拆能力部分 `Accepted`（轨道 R 已落地）；组合层部分保持 `Proposed`，触发条件已满足（见 ADR-0010），契约从实现中归纳。

## 什么时候需要 ADR

通常只有以下情况值得写：

- 改变主要依赖方向；
- 重新定义 persistence / migration architecture；
- 重新定义 plugin / extension model；
- 重新定义 Content / Ruleset / Adventure 的长期模型；
- 拆分或合并 repository / package；
- 替换一个影响大量后续工作的核心 abstraction；
- 多个长期方案都合理，需要记录为什么选择其中一个。

## 什么时候不需要

以下情况一般不需要 ADR：

- 普通 bug fix；
- UI 调整；
- 小型 refactor；
- 单次 migration；
- 普通新 API；
- 实现细节变化；
- 没有长期设计争议的普通功能。

复杂但只影响当前 PR 的设计，可以直接写在 PR 的 Design Note 中。

## 状态

ADR 使用：

- `Proposed`
- `Accepted`
- `Superseded`
- `Deprecated`

如果新 ADR 取代旧 ADR，不删除历史文件，在旧 ADR 中标注被哪个 ADR supersede。

## 命名

```text
0001-short-title.md
0002-short-title.md
```

编号只表示记录顺序，不表示优先级。

## 原则

ADR 记录的是：

> 当时基于什么上下文，为什么选择这个方向。

它不是：

> 以后永远不能改变。

如果未来条件变化，写一个新的 ADR supersede 旧决策即可。
