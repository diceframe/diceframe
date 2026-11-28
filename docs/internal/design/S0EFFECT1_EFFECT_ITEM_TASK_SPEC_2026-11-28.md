# 任务书：S0-EFFECT1 载荷契约（Effect Item）

| 项 | 值 |
|---|---|
| 切片 | S0-EFFECT1（P1：只定契约与适配器，不产出任何内容） |
| 性质 | 重构的前置片。本片**不改变任何行为** |
| 基线 | `f6614d23` |
| 状态 | P1 已实现并通过独立审核修订：**只有契约 + 归一 + 三档判据，没有消费方接入** |
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
3. **不**提供应用点（见 §6.6 的修订说明）。P1 的交付止于"形状"，
   `apply_effect_items` 归 P2——因为一旦接上 rider 路径，"事件一字不动"就要求
   归一是旧 `kind` 的逆函数，而它不是。

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
| **P1（已交付）** | 字段闭集 + 归一 + 三档判据；**无消费方接入** | `effect_items.py` + 9 条用例 | 无行为变化 |
| **P2a（进行中）** | 应用点落地（单一函数）+ rider 路径接入，事件不变 | `effect_items.apply_rider_items` | 无内容变化 |
| P2b | rider 句式覆盖（A 类：直接施加条件 / 带 escape DC） | 导入器 + 目录 | 产出新形状 |
| P3 | `on_fail` 接受效果项（B1 伤害+条件、B2 推挤+条件） | 契约 + 结算 | 复用同一个应用点 |
| P4 | `spell_effects` 收敛（48 条，逐条验证） | 内容 + resolver | 一次一条，可中途停 |
| P5 | `KIND_HANDLERS` 的约 11 个"纯施加"handler 退化 | 代码 | 19 个经济类 handler **不动** |

**P2a 必须在 P1 之后、P2b 之前**：P2b 产出的新形状要有人读；读的人先到位。

**P2a 的设计决定（回应 §6.6 的不可逆问题）**：事件的 `source_rider` **由生产方声明**，
不再从条件名反推。归一函数因此返回 `(source, item)` 两件而不是一件：

- 旧数据：`source` 就是原来的 `kind`（`condition` / `prone` / `grapple` / `push`），所以
  `{"kind":"condition","condition":"prone"}` 仍然写 `"condition"`，事件逐字节不变；
- 新数据：生产方直接声明 `source`（例如 `"prone"`），不再有歧义。

这样"归一有损"不再是问题——损失的是**猜测**，不是信息：生产方本来就知道自己写的是哪一种。

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

## 6.6 独立审核后的修订（2026-11-28）

审核有三条硬结论，全部确认并已改：

1. **"行为不变"不成立**。`{"kind":"condition","condition":"prone"}` 在旧代码里
   `source_rider` 写 `"condition"`，而按条件名反推会写成 `"prone"`；两者归一后不可区分。
   → **撤销 rider 路径的接入**（`hit_resolution.py` 回到 `8aa98926` 的实现），
   P1 不再有消费方；`legacy_label` 删除（它是死代码，且不是旧 `kind` 的逆函数）。
2. **`apply_effect_items` 不存在**，§3.4 与 §7 验收 1 属过度声明。→ 从 P1 移出，归 P2；
   本片的成功判据改为"形状 + 归一覆盖"。
3. **`concentration` 不在闭集却被 `lifetime_of` 读取** → 删除该分支（它是 casting-shape，
   §2 已经这么定了），`maintained` 只看 `save_repeat`。

另外修掉：`validate_effect_item` 曾是死代码（现在 `normalize_effect_item` 遇到未知字段
**拒绝**而不是静默丢弃）；`effect_items.py` 补进 `pyproject.toml` 的 mypy 清单；
测试里 `save_repeat: "end_of_turn"` 是凭空造的词（真实取值是能力码）已改为 `"wis"`。

## 6.7 P2a 执行记录（2026-11-28）

- `effect_items.normalize_rider(raw) -> (source, item)`：`source` 取生产方声明的 `kind`，
  **不从条件名反推**；无 `kind` 的新形状由 item 自己回答（`push` / `condition`）。
- `hit_resolution._apply_effect_items(...)`：唯一应用点（体型门 → 豁免 → 推挤/条件），
  `_statblock_rider_events` 只剩"取 riders → 归一 → 调它"。
- 事件不变的做法换了：不是"反推标签"，而是"**生产方带着标签进来**"。
  `{"kind":"condition","condition":"prone"}` 依旧写 `source_rider: "condition"`。
- 11 条用例（9 + 2 条 source 保留）+ 武器词条 37 条，全绿。

## 7. 验收指标

1. **形状**：`effect_items.py` 提供字段闭集、`normalize_effect_item(s)`、`lifetime_of`、
   `validate_effect_item`；未知字段被拒绝而不是丢弃；
2. **不接消费方**：`git diff 8aa98926..HEAD -- src/rulesets/dnd2024/combat/hit_resolution.py` 为空——
   "行为不变"用"没有改动"来保证，而不是用测试来赌；
3. **归一覆盖**：§4 五种旧形状各有一条用例，另有一条把"归一有损"这个限制固定下来；
4. **三档寿命**：`instant` / `expiring` / `maintained` 各一条。

## 8. 未决

1. `save_repeat` 的回合推进细节（与 concentration 同族，P3 时定）；
2. `escape_dc` 是否也该由来源提供（今天写在数据里，与 `action.dc` 重复的可能）；
3. `duration` 词表是否复用 `rider_duration` 的现有取值（`target_turn_end` 等）。
