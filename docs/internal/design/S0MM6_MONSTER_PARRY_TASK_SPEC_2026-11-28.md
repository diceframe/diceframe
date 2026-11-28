# 任务书：S0-MM6 怪物反应 Parry

| 项 | 值 |
|---|---|
| 切片 | S0-MM6（接 S0-MM5 的组合池） |
| 载体 | stat block 的 `reactions` 容器 + 命中反应窗口 |
| 规则基准 | SRD 5.2.1（Parry：见攻击者、**一次**命中改判、花一个反应、AC 加熟练加值） |
| 基线 | `e674f859`（MM5） |
| 状态 | 已实现（契约 + 窗口 + 结算 + 导入）；端到端窗口用例未写，见 §6 |

## 1. 范围

CR ≤ 4 里因为 `Parry (Reaction)` 而进不了库的条目。它与池叠加时解锁最多：`bandit-captain`、`guard-captain`、`pirate` 等现在只差这一项。

## 2. 契约

### 2.1 敌人档案新增 `reactions`

```json
"reactions": [{"id": "parry", "kind": "ac_bonus", "bonus": "proficiency"}]
```

- `kind` 目前只有 `ac_bonus` 一种（闭集，未知即拒绝）；
- `bonus` 目前只接受 `"proficiency"`（本片不猜属性加值）；
- 条目 0–2 条，`id` 正则同攻击 id。

它进 `_ENEMY_KEYS`：这是**规则字段**，与 `actions` / `traits` 同类，不是内容缺口标记（后者只能放目录级，见 S0-MM4）。

### 2.2 命中反应窗口

`_hit_reaction_offer` 在防守者是**敌人**且 `reactions` 里有 `ac_bonus`、且本回合反应未花时，增加一个 `parry` 选项：

- 决策 `kind` 仍是 `hit_reaction_spell`（同一生命周期，不新增第二种）；
- `options` 增加 `"parry"`；`assigned_to` 走既有的"真人 / GM"分叉（敌人恒为 GM）。

### 2.3 结算

`option == "parry"`：

1. 发 `dnd2024.reaction.spent{reason: "parry"}`；
2. `armor_class = 决策里的 AC + 该生物的 `proficiency_bonus``；
3. 重算 `hit = critical or attack_total >= armor_class`，发 `check.resolved{kind: "reaction_defense"}`；
4. 若不再命中 → **不再结算伤害**（与护盾术同一路径）；
5. 无论命中与否，反应都花掉。

## 3. 导入器

`map_actions` 遇到 `action_type == "REACTION"` 且名字是 `Parry` 时，产出 `reactions` 条目；其余 Reaction 动作仍是缺口（`unmapped` 已如实记账）。

## 4. 逐项测试

1. 敌人有 `parry` 且反应未花：命中后窗口出现，选 `parry` → AC 提升、若改判落空则无伤害。
2. 选 `decline` → 伤害照旧，反应不花。
3. 反应已花 → 窗口不再出现 `parry`。
4. 契约：`kind` 未知 / 条目超过 2 / 缺 `bonus` → 加载失败。
5. 目录：条目数增长，且 `bandit-captain` 进库（若仍有别的缺口则如实留在 `skipped`）。

## 6. 执行记录（2026-11-28）

- 目录 **154 → 156**：`bandit-captain`、`knight`、`noble` 带 `reactions` 入库；`skipped` 62。
- 契约：`reactions` 进 `_ENEMY_KEYS`，`kind` / `bonus` 均闭集，最多两条。
- 窗口：`_enemy_reaction_choices` 与角色侧共用 `combat["reactions"]` 与同一个"已花"判据；
  `options` 增加 `parry`；决策里带 `parry: {reaction_id, bonus}`。
- 结算：`option == "parry"` 花反应、AC 加熟练加值、重算命中，**落空则不发伤害事件**（与护盾术同一路径）。
- GM 自动化作答默认 `decline`（Parry 是可选的），GM 面板可手动选。

**未完成**：端到端用例（"命中→窗口→选 parry→改判落空"）没写。原因是本文件夹具里的
玩家角色没有可用的攻击档案，无法在同一个战斗里制造"玩家打怪、怪 Parry"的序列。
现在覆盖的是契约两条（未知 kind 被拒、没有可加加值时不给选项）与目录一条。
补这条用例需要给夹具装武器，属于测试基础设施改动，不在本片。

## 5. 非目标

- 其它反应型动作（Nimble Escape、Legendary Resistance 的巢穴形态等）。
- Parry 的"每回合多次攻击只挡一次"之外的变体（如加值来自属性）。
