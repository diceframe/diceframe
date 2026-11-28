# S0-SP2 任务书：`condition` 的三维表达力与 `ongoing`

| 项 | 值 |
|---|---|
| 切片编号 | S0-SP2（`S0SP_PLAN.md` 里 S3 的重切） |
| 验收载体 | 4 条法术真的能放、状态真的生效、重复豁免真的按回合走 |
| 规则基准 | 官方 SRD 5.2.1，逐条原文引在 §2；`source_ref` 见 `srd_spells.json` |
| 前置 | S0-SP1（#899）、P2（`hold` 系已合）、`condition` 现有 15 条 |
| 状态 | 待执行；本文是执行契约 |

## 1. 为什么不是"再来一批直填"

SP1 的 8 条候选里 6 条退回。逐条核下来，退回来的原因**不是各自缺一个法术机制**，而是共用的一层太薄：

- `condition` 只有 15 条，每条只能带 `attack_modes` 与 `resist_damage_types` 两个维度；
- `spell_effects` 只能表达"施放那一瞬"，没有"之后每个回合"。

按桶切会把同一层的六个需求拆进六个切片，每个都造一个只服务一条法术的原语。这一片按**层**切：先把 `condition` 补齐、把 `ongoing` 加上，再让 4 条法术落地。

## 2. 原文（这一片要对的字）

| 法术 | 环 | 原文关键句 |
|---|---|---|
| `mind_spike` | 2 | "makes a **Wisdom** saving throw, taking 3d8 Psychic damage on a failed save **or half as much damage on a successful one**" |
| `blindness_deafness` | 2 | "must succeed on a Constitution saving throw, or it has the **Blinded or Deafened condition (your choice)** for the duration. At the end of **each of its turns**, the target **repeats the save**" |
| `ray_of_enfeeblement` | 2 | "Constitution saving throw… On a failed save, the target has **Disadvantage on Strength-based D20 Tests**… it also **subtracts 1d8** from all its damage rolls. The target **repeats the save at the end of each of its turns**"；成功则"Disadvantage on the next attack roll it makes" |
| `phantasmal_force` | 2 | "**On each of your turns**, such a phantasm can deal **2d8** Psychic damage"（**没有**初始伤害） |

**`mind_spike` 是 Wisdom，不是 Int**。SP1 的任务书把它写成 `save: "int"`，我按错的写了，这一片改正。

## 3. 三处 schema 扩展

### 3.1 `condition.check_modes` —— 检定优劣势

现状：`conditions` 的条目只有 `attack_modes`（`{side, mode}`），`check_modes.py` 的 `condition_flags` 只读它。

需要：

```json
"enfeebled": {
  "kind": "condition",
  "attack_modes": [],
  "check_modes": [
    {"surface": "ability_check", "mode": "disadvantage", "abilities": ["str"]},
    {"surface": "skill_check", "mode": "disadvantage", "abilities": ["str"]},
    {"surface": "saving_throw", "mode": "disadvantage", "abilities": ["str"]}
  ],
  "damage_roll_modifier": {"dice": "-1d8"}
}
```

语义要点：

- `surface` 取 `ability_check` / `skill_check` / `saving_throw`——**注意**这与 `check_modifiers` 的 `surface` 不同：那里只有 `skill_check` 有消费者，这里是"条件施加的劣势"，三类检定各有结算入口（`_ability_check_events` / `_skill_check_events` / `_saving_throw_check`），所以三类都要能表达。
- `abilities` 限定属性；缺省 = 全部。`ray_of_enfeeblement` 要的是 "Strength-based D20 Tests"，即力量相关的三类检定。
- 与 `check_modifiers`（特性给的修正）**不是同一件事**：那是加值/替换，这是优劣势。两套并存，不要合并。

### 3.2 `condition.damage_roll_modifier` —— 伤害骰减值

```json
"damage_roll_modifier": {"dice": "-1d8"}
```

`ray_of_enfeeblement` 的"subtracts 1d8 from all its damage rolls"。消费点是伤害结算（`hit_resolution` / `spell_resolution` 的骰池拼接），减到**最低 0**。

### 3.3 `condition` 的参数化（二选一）

`blindness_deafness` 的施法者选 Blinded 或 Deafened。**不新增 `condition_choices` 字段**——那会在 `spell_effects` 里再造一套选择机制。改用既有做法：**拆成两个 effect 变体**不可行（施法者只能选一个），所以用参数：

```json
"condition": {"choices": ["blinded", "deafened"], "picked": 0}
```

即允许 `spell_effects.condition` 是字符串**或**对象。对象形态只支持 `choices`（其余字段与字符串形态同义）。消费者按 `picked` 索引取一个条件 id；`picked` 缺省 0。

> 这一条是本片唯一"改字段类型"的地方，所以单独说明：它比新增一个平行字段更轻，而且把"选一个"的语义留在声明处，不在结算处分支。

### 3.4 `spell_effects.ongoing` —— 之后每个回合

```json
"ongoing": {
  "trigger": "turn_end" | "caster_turn_start",
  "save_repeat": "con",
  "damage": "2d8",
  "damage_type": "psychic"
}
```

- `trigger: "turn_end"` + `save_repeat` → 目标回合结束时重复豁免，成功则条件结束、法术结束（`blindness_deafness`、`ray_of_enfeeblement`）。
- `trigger: "caster_turn_start"` + `damage` → 施法者回合开始时对仍在法术内的目标再结算一次伤害（`phantasmal_force`）。
- **不做** `bonus_action` / `magic_action` 那两种——它们是"授予一个动作"，属 capability，见 §6。

## 4. 逐条落地

| 法术 | `mode` | 新增用到 | 备注 |
|---|---|---|---|
| `mind_spike` | `saving_throw` | 无（`save: "wis"`, `3d8`, `half_on_success`） | 本片唯一零新增的一条，先落地证明链路 |
| `blindness_deafness` | `saving_throw` | `condition` 对象形态 + `ongoing.save_repeat` | 每回合结束重复豁免 |
| `ray_of_enfeeblement` | `saving_throw` | `check_modes` + `damage_roll_modifier` + `ongoing.save_repeat` | 失败分支；成功分支的"下次攻击劣势"用 `condition` + `after_next_attack`（既有时长码） |
| `phantasmal_force` | `saving_throw` | `ongoing.trigger: caster_turn_start` + `damage` | **无初始伤害**，施放只施加"幻象存在"这一状态 |

## 5. 验收

每条四条，缺一不算点亮（沿用 SP1）：

1. **施放走得通**，且 `blindness_deafness` 要覆盖"选 blinded"与"选 deafened"两条；
2. **事件流有内容**：`condition.applied` 带 `source_ref`，重复豁免要产生 `check.resolved` 且 `kind="saving_throw"`；
3. **数值断言**：`mind_spike` 断言半伤的具体点数；`ray_of_enfeeblement` 断言"受该条件的角色攻击掷骰为劣势"（`check.resolved.advantage_mode == "disadvantage"`），**不是**只断言条件存在；
4. **重复真的按回合**：推过一轮，断言 `blindness_deafness` 的目标在回合结束豁免（用可控骰让它必败 → 条件保留；再推一轮 → 成功则移除）。

外加两个全局门槛：

- `python scripts/spell_coverage.py` 的 1–3 环覆盖要涨，`--check` 基线随之上调；
- 全量 `tests/rulesets tests/architecture` 全绿，ruff/mypy 干净。

## 6. 不做（这一片明确排除）

| 项 | 为什么 |
|---|---|
| `heat_metal` | "Bonus Action on each of your later turns to deal this damage again" 是**授予动作**，不是自动 tick；另需"掉落持握物"的机制 |
| `vampiric_touch` | "make the attack again on each of your turns as a Magic action" 同上；另有"施法者回一半"需要 `heal_self_fraction` |
| `bestow_curse` / 多变体 | 六个诅咒各不同，属内容而非 schema |
| 几何 AoE / 区域 | 需 2D，见 `S0SP_PLAN.md` |

## 7. 停止条件

- 某项扩展需要动 `attack_resolution` 的主路径才能生效 → 停，记 GAP；
- `condition` 的对象形态被超过两处消费者读取、且语义开始分叉 → 停，退回"新增平行字段"的方案重新评估；
- `ongoing` 需要在存档里持久化额外状态（如"第几回合"）才能成立 → 停，先界定"回合"的唯一权威再继续。

## 8. 交付物

- 三处 schema 扩展 + 各自消费者；
- 4 条 `spell_effects`；
- 端到端用例（含二选一两分支、重复豁免两轮、劣势断言）；
- 执行报告：逐条记"实际用了哪个字段、有没有偏离、§7 有没有触发"；
- `SLICE_STATUS.md` 的 SP 段增行；`BASELINE_EFFECTS` 更新。
