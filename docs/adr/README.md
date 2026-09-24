# Architecture Decision Records

ADR 用来记录“为什么做出一个长期架构决定”，不是所有 PR 的审批流程。

## 架构方向提案

- [ADR-0005：通用底座、玩法拼装与具体游戏](0005-composable-game-platform.md) — `Proposed`；记录跨玩法架构的目标边界、取舍与验证方式，尚未落地，不替代当前架构事实来源。

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
