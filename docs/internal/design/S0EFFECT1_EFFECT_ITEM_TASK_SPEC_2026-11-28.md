# 任务书：S0-EFFECT1 载荷契约（Effect Item）

| 项 | 值 |
|---|---|
| 切片 | S0-EFFECT1（P1：只定契约与适配器，不产出任何内容） |
| 性质 | 重构的前置片。本片**不改变任何行为** |
| 基线 | `f6614d23` |
| 状态 | P1 已实现（契约 + 归一 + rider 走新路径，行为不变） |
| 后续 | P2 rider 句式（A 类）、P3 `on_fail` 扩展（B1/B2）、P4 法术收敛、P5 handler 长尾 |

## 1. 问题陈述（只列代码事实）

同一个"对目标造成效应"的概念，今天有三份写法，另有一份对外承诺没有执行者。

### 1.1 三份写法

| 概念 | 攻击 rider | 法术 effect | 职业特性 handler |
|---|---|---|---|
| 伤害 | `"damage": [{"dice":"1d6+2","type":"slashing"}]` | `"damage": "3d6", "damage_type": "fire"` | 各 handler 自己拼事件 |
| 条件 | `{"kind":"condition","condition":"poisoned"}` | `"condition": "paralyzed", "condition_duration": "concentration"` | handler 内联 |
| 豁免 | `{"save": {"ability":"con","dc":12}}` | `"save": "con"`（无 DC） | handler 内联 |
| 重复豁免 | 无 | `"save_repeat"`（`hold_person`） | 无 |
| 半伤 | 无 | `"half_on_success"`（`burning_hands`） | 无 |

事实来源：

- `primitives.py:292` `RIDER_KINDS = {"condition", "grapple", "push", "prone"}`；
- `hit_resolution.py:995-1030` 的 rider 结算已经是"体型门 → 豁免 → push/condition"四步，**执行已存在**；
- `spell_effects` 共 **48 条**，而法术目录 **339 条**（覆盖率 14%）；
- `validation.py:628-638` 的 `save_action.on_fail` / `on_success` **只允许 `damage`**；
- `features/kinds.py:126` `KIND_HANDLERS` 共 **30 个**。

### 1.2 一份没有解释器的对外承诺

`docs/rulesets/dnd2024/CONTENT_BUNDLE_CN.md` 公布了 Effect DSL 白名单
（`apply_condition` / `deal_damage` / `force_save` / `make_attack` …），
`bundle.py:31` 用 `ALLOWED_EFFECT_OPS` 校验它；但 `src/` 里 `force_save`、
`make_attack` **零命中**——白名单接受，没人执行。

本片**不处理**这份 DSL（见 §6）。列出来是为了说明：它不是本片要接的第四个消费者，
而是将来若要"内容作者写的效果被真正执行"时的独立提案。

## 2. 目标 / 非目标

**做**：

1. 定义效果项（effect item）的字段闭集；
2. 提供读时归一（`normalize_effect_item`），把三种旧拼写折成效果项；
3. 提供单一应用点（`apply_effect_items`）的**接口与测试**，本片先只服务攻击 rider 一条路径，行为与今天逐字节一致。

**不做**：

- 触发时机（窗口 / 决策 / 回合推进 / 反应经济）——那是另一个轴；
- `src/engine/combat_effects.py`（通用引擎，Issue 212）——specific ruleset 不得反向污染 generic engine；
- 对外 Effect DSL 的解释器；
- 区域几何（cone / radius）；
- `concentration` / `upcast_*` / `target_count`——它们是 casting-shape，属于"来源"。

## 3. 契约

### 3.1 效果项

```json
{"damage": {"dice": "3d4", "type": "bludgeoning"}}
{"condition": "grappled", "escape_dc": 12, "duration": "target_turn_end"}
{"condition": "prone"}
{"push": 30, "max_target_size": "medium"}
{"damage": {"dice": "8d6", "type": "fire"}, "save": "dex", "half_on_success": true}
```

闭集字段：`damage` / `condition` / `duration` / `escape_dc` / `push` / `max_target_size` /
`save` / `half_on_success` / `save_repeat`。未知字段一律拒绝（与现存 `_validate_rider` 同一纪律）。

### 3.2 DC 由来源提供，不写进效果项

**不是偏好，是被数据逼出来的**：`spell_effects` 339 条里没有任何 DC 字段（DC 由施法者算）。
若效果项带 DC，每条法术都要编一个假值。因此：

- 效果项只声明 `save: "con"`（要哪种豁免）；
- DC 由**来源**回答：法术取施法者 DC，怪物取动作 DC，特性取 handler 参数；
- 这与 `cost` 的处理同构（成本来自声明，DC 来自来源）。

### 3.3 寿命三档

**持续生效的效应不是全部效应**，所以先分"瞬间 vs 持续"，再在持续里分两档：

| 档 | 判据 | 例子 | 引擎要记什么 |
|---|---|---|---|
| **瞬间** `instant` | 没有 `duration`、没有维护者 | 伤害、推挤 | 不记状态 |
| **带过期** `expiring` | 有到期点，没有维护者 | "倒地直到其下回合结束"、`duration: target_turn_end` | 条件 + 到期点 |
| **需维护** `maintained` | 有**维护者**（专注 / 每回合重掷） | `save_repeat`、`concentration` | 效果实例 + 维护者 + 终止条件 |

关于第三档要说清一句：**需维护的效果也有到期点**，所以它和"带过期"不是互斥事实，区别在于
**有没有维护者**——维护者决定了"谁会再来问它一次"（回合结束的重掷、专注中断）。
正因为不互斥，才要分成两档而不是让 `duration` 一个字段同时回答两个问题。

判据落在代码里：`effect_items.lifetime_of(item)`，`save_repeat`/`concentration` → `maintained`，
有 `duration` → `expiring`，否则 `instant`。

### 3.4 应用点

```python
apply_effect_items(instance, combat, source, target, items, rng, *, dc) -> list[Event]
```

- 顺序即声明顺序（规则文本的语序有意义：先伤害后条件）；
- 每项独立结算，与今天 `_hit_tail_events` 的逐条循环一致；
- **发出的仍是同样的事件**（`condition.applied` / `resource.changed` / `dnd2024.position.changed`）——
  形状可以变，事件不能变，否则存档与 replay 一起坏。

## 4. 兼容策略（重构的主体）

| 旧形状 | 归一后 | 备注 |
|---|---|---|
| `{"kind":"condition","condition":X}` | `{"condition": X}` | kind 字段消失 |
| `{"kind":"prone"}` | `{"condition":"prone"}` | 空字段 kind 收成条件 |
| `{"kind":"grapple","escape_dc":N}` | `{"condition":"grappled","escape_dc":N}` | `grapple` kind 消失 |
| `{"kind":"push","distance":N}` | `{"push": N}` | 字段名统一 |
| `{"kind":"condition","condition":X,"save":{...}}` | `{"condition":X,"save":"con"}` + DC 由来源 | rider 的 `save.dc` 迁移为动作 DC |
| 法术 `{"damage":"3d6","damage_type":"fire","save":"dex","half_on_success":true}` | `{"damage":{"dice":"3d6","type":"fire"},"save":"dex","half_on_success":true}` | 只在 P4 做 |

规则：

1. **内容零迁移**：旧形状一律继续合法，读时归一；
2. **写时新形状**：导入器与新内容用效果项；
3. **事件不变**：应用点内部可以换实现，事件契约不动；
4. 每条旧形状都要有一条"读时归一"的测试，P4/P5 完成前不删旧路。

## 5. 分片与关系

| 片 | 内容 | 交付物 | 与其它片的关系 |
|---|---|---|---|
| **P1（本片）** | 契约 + 归一 + 应用点接口 + 攻击 rider 走新路径（行为不变） | 代码 + 测试 | 无内容变化 |
| P2 | rider 句式覆盖（A 类：直接施加条件 / 带 escape DC） | 导入器 + 目录 | 产出**新形状**，不再走 `kind` |
| P3 | `on_fail` 接受效果项（B1 伤害+条件、B2 推挤+条件） | 契约 + 结算 | 复用同一个应用点 |
| P4 | `spell_effects` 收敛（48 条，逐条验证） | 内容 + resolver | 一次一条，可中途停 |
| P5 | `KIND_HANDLERS` 的约 11 个"纯施加"handler 退化 | 代码 | 19 个经济类 handler **不动** |

**P2 必须在 P1 之后**：否则 38 条 rider 会按旧 `kind` 形状落进目录，P1 之后要重解析一遍。

## 6. 边界清单（接得上 / 不接）

| 部分 | 结论 | 理由 |
|---|---|---|
| 攻击 `riders[]` | ✅ 接 | 应用循环已存在，只是按 kind 分支 |
| `spell_effects` 的 payload 半 | ✅ 接（P4） | 与 casting-shape 可分离 |
| 事件层 | ✅ 不动 | 事件不变是红线 |
| `KIND_HANDLERS` 30 个 | ⚠️ 只收约 11 个 | 19 个是经济/移动/生命周期，硬接会变垃圾袋 |
| 对外 Effect DSL | ❌ 不接 | 动词语法，且没有解释器；将来单独提案 |
| `radius_ft` / cone | ❌ 不接 | 几何不是载荷；1D 位置做 cone 是伪精度 |
| `concentration` / `upcast` / `target_count` | ❌ 不接 | casting-shape，属于来源 |
| 前端 | ✅ 无影响 | `frontend-v2` 对 `riders` 的读取为零命中 |

## 6.5 P1 执行记录（2026-11-28）

- 新增 `combat/effect_items.py`：字段闭集、`normalize_effect_item(s)`、`legacy_label`、`lifetime_of`、`validate_effect_item`。
- `hit_resolution._statblock_rider_events` 改为"先归一、再按项结算"，**事件形状一个字没动**
  （`source_rider` 由 `legacy_label` 还原成今天会写的词）。
- 8 条新用例（五种旧形状归一、未知 kind 不猜、标签还原、三档寿命）+ 既有 57 条 rider 主路径用例全绿。

## 7. 验收指标

1. **同一函数**：攻击 rider 路径调用 `apply_effect_items`（不是又包一层）；
2. **行为不变**：`tests/rulesets` 全量通过，事件序列逐条相同（用现有用例兜底）；
3. **旧拼写残余计数**：本片结束时等于今天的数量（P1 不消除旧拼写，只建立归一）；
4. **归一覆盖**：§4 五种旧形状各有一条"读时归一"测试。

## 8. 未决

1. `save_repeat` 的回合推进细节（与 concentration 同族，P3 时定）；
2. `escape_dc` 是否也该由来源提供（今天写在数据里，与 `action.dc` 重复的可能）；
3. `duration` 词表是否复用 `rider_duration` 的现有取值（`target_turn_end` 等）。
