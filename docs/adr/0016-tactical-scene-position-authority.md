# ADR-0016: 战术场景与战斗位置归属

- Status: Accepted (local implementation; unreleased)
- Date: 2026-11-28

> 2026-11-28 维护者授权集成验收角色裁定，按本草案 Decision（ROAD 加法方案）Accept。施工计划见 [FVTT_COMPAT_WORKPLAN.md](../internal/FVTT_COMPAT_WORKPLAN.md) §3.1。依赖本决策的任务（combat-01 等）自此解锁。

## Context

DiceFrame 要做原生的战术地图（Tactical Scene），并导入 Foundry VTT 的场景数据。Token 的位置有两类消费者：非战斗时的摆放和探索；战斗中的移动、距离、射程和范围效果。

当前的事实：

- 战斗位置是 dnd2024 规则集 combat state 的一部分。`combat.positions` 的形状是 `{actor: int}`，`position_mode` 是 `"theater"`。位置由 `dnd2024.position.changed` 事件经 reducer 修改，随 EventBatch 重放（`src/rulesets/dnd2024/combat/engine.py`）。
- generic combat engine 没有位置概念。`CombatState` 只有 `entities`、`hp_resource`、`barriers`、`buffs`。
- 已有的三份计划都规定二维战斗由规则集 runtime 或 reducer 拥有：`DND2024_FULL_EXPERIENCE_PLAN.md` 阶段三、`plans/gameplay-ux-combat-roadmap.md` §5.2、`plans/hd2d-gameplay-ux-master-plan.md` §7.2。
- 场景的后端已经施工：场景和它的 Token 实例存在能力模块槽位里。它的契约曾写“Token 位置权威属 Scene Runtime”。照此实现战斗，会出现两套位置 authority：场景槽位一份，combat state 一份。

[ADR-0004](0004-generic-combat-extension.md) 要求规则集拥有自己的 reducer，客户端只提交 intent。`ARCHITECTURE_CN.md` §0.1 指出，在错误的层写一份正确的逻辑会产生第二套 authority。

必须保留的约束：

- 旧存档和旧客户端默认继续走 `theater`。无法安全解释的数据 fail closed。
- 叙事地点图和战术图是两个投影，不共享坐标语义（ROAD §4 第 7 条，HD-2D §8 第 7 条）。
- 核心流程文件不点名能力模块（`tests/architecture/test_module_pluggability.py`）。

## Decision

### 1. 位置按“是否处于战斗中”划分

| 状态 | 权威 owner | 写入口 |
|---|---|---|
| 非战斗：探索、GM 摆放、Freeform、没有战斗的 CoC | 场景槽位 | 场景写操作，带 revision 和权限检查 |
| 战斗中的参战 Token | 规则集 combat state | ruleset intent，经 reducer |
| 战斗中的非参战对象：墙、Tile、光照、未参战的 Token | 场景槽位 | 场景写操作 |

### 2. 进出战斗的同步

- **绑定是显式的。** 战斗记录一个 `board`：从场景取出的网格尺寸、格距和阻挡信息，外加指向来源的引用。不按名称匹配。
- **进入战斗。** 参战者的初始位置由绑定时的 Token 位置一次性生成，作为可重放的事件。旧存档没有空间信息时禁止猜测。
- **战斗进行中。** 每个 Token 的坐标只有一个来源。服务端在投影里标明 authority 是场景还是战斗。前端不得自己合并两份坐标。
- **离开战斗。** 用一次显式的场景写操作把最终位置写回场景，带 revision 检查。场景在此期间被改过就不写回，并报告冲突。

### 3. 字段形状采用加法方案

保留 `theater` 的 `position: number`，新增 `position_mode: "grid"`、`board` 和每个 actor 的 `spatial_position`。不把 `position` 改成联合类型。

### 4. 依赖方向

规则集不 import 场景模块。规则集声明它需要的空间输入，由应用服务从场景取好后作为显式输入传入。

### 5. 术语

- 实体叫 Tactical Scene。HD-2D 计划里的 `SceneMap` 指同一个实体。
- 战斗内的网格快照叫 `board`，二维位置叫 `spatial_position`。
- 空间模式是 `theater`、`zone`、`grid`。只有规则集显式声明 `grid` 时才出现战术棋盘。
- 代码标识符带 `tactical_scene` 前缀：槽位、模块、包、service、route、action、事件类型、错误码、消息键。文档载荷里的词汇保持导入来源的对象名。

### 6. 回滚

战斗位置随 combat state 回滚和重放，这是现有机制。场景槽位是否随回合快照回滚，不在本 ADR 决定，见施工计划 §19 第 6 项。

### 7. AI GM

AI GM 通过同一条 intent 通道提交站位、移动和范围效果，不直接写坐标。

## Alternatives Considered

- **场景拥有全部位置，规则集通过 capability 校验。** 这是 v1 施工计划和已施工契约的路线。没有选它：规则集要在结算时读写另一个模块的状态，与 ADR-0004 和三份现有计划冲突；EventBatch 的重放会依赖场景槽位当时的状态。
- **把 `positions` 的值从整数改成 `[x, y]`。** 这是 D&D 计划阶段三的写法。没有选它：它改变已持久化事件的形状，旧存档、旧事件和所有现有消费者都要跟着改。仓库里已有的做法是加新键、不改旧值，例如 zone 的 `projection: "theater_1d"`。
- **嵌入第三方 VTT。** D&D 计划已经否决：位置权威在第三方客户端，与 EventBatch 模型冲突。

## Consequences

### Benefits

- 一份事实只有一个 authority。
- `theater` 的旧存档、旧事件和现有消费者不需要改动。
- 前端的坐标来源单一，由服务端标明。
- 从 FVTT 导入的场景可以直接成为二维战斗的底图。

### Costs / Risks

- 进出战斗需要显式的同步，要处理“战斗中场景被改”的冲突。
- 投影要标明 authority，前端和 SSE 的契约更复杂。
- 玩家在探索中的移动请求要等本决策落地之后才能实现。

### Migration / Compatibility

- 不影响已有的存档。`theater` 的 `position` 不变。
- 新增的字段只在 `grid` 模式出现。旧客户端遇到 `grid` 按未知模式处理，前端降级为名单，不猜坐标。
- 场景槽位的 schema 策略见施工计划 §6.1。
- 回滚方式是不启用 `grid`，回到 `theater`。

## Validation

- `theater` 的旧存档和现有测试全部不变。
- `grid` 模式的距离、对角线、占用、墙的阻挡、移动预算和事件重放各有测试。
- 进出战斗同步的测试，包括场景被改过时不写回。
- 投影测试：每个 Token 的坐标只有一个 authority。
- 架构守卫：规则集不 import 场景模块。
- 多人：P2P 与 Web 走同一条 intent 链。

## Supersedes / Superseded By

不取代任何 ADR。

作废两处文字：v1 施工计划 §3.2 的“Token 场景位置归 Scene Runtime”，以及已施工契约里“Token 位置权威属 Scene Runtime”一句。

约束 `DND2024_FULL_EXPERIENCE_PLAN.md` 阶段三的字段形状：二维位置按本 ADR 第 3 条落地，不把 `positions` 的值改成数组。
