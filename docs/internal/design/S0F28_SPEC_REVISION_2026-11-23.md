# S0-F28 任务书修订：超魔修饰器先行，十选项分处理器

| 项 | 值 |
|---|---|
| 原任务书 | `S0F28_METAMAGIC_TASK_SPEC_2026-11-07.md`（`docs/dnd2024-s0-specs` 分支） |
| 修订原因 | 原文把十个选项当"逐项实现"清单，但没有先定修饰器容器；另有三处依赖在原文写作后已变化 |
| 覆盖范围 | 本文件覆盖原文 §1 的执行顺序、§3 的基线事实、§7 的停止条件；§2 的官方原文与 §6 的测试编号继续有效 |

## 1. 基线变化（原文 §3 之后发生的事实）

| 原文判断 | 现状 |
|---|---|
| 法术目录只有 `components: ["V","S","M"]` | F25 已补 `material_cost_specified` / `material_consumed`（333 条带标记），**Subtle 与 Beast Spells 现在读同一对布尔** |
| 没有通用"被打断/被挡下"的审计事件 | `condition.blocked` 现已被 reducer 接受（F26 补），可作为修饰器拦截的落账形状 |
| 施法校验集中但无修饰入口 | 仍无；`cast_spell` 只带 `spell_ref` / `slot_level` / `targets` / 免费槽与伤害选择 |
| Counterspell 窗口按原法术 V/S/M 判定 | `reaction_spells.py:495` 仍直接读 `spell["components"]`；**Subtle 必须让窗口读"有效成分"**，否则"无成分施法"仍能被反制 |
| 术法点 | F6 已有 `sorcery_points` 资源与 `font_of_magic` 的造槽换算 |

**仍然为零**：`src/rulesets/dnd2024/` 下没有任何 `metamagic` 实现，特性目录里只有 id 与 label。

## 2. 架构决定：容器先行

一个超魔不是"某个法术的特例"，而是**对一次施法的修饰**。所以顺序固定为：

### 2.1 意图形状（唯一入口）

```
cast_spell:
  spell_ref, slot_level, targets, ...原有字段
  metamagic: [
    {"option_id": "careful_spell", "targets": ["player:ally"]},
    {"option_id": "transmuted_spell", "damage_type": "cold"},
    {"option_id": "empowered_spell", "dice": [1, 1]},
  ]
```

- 客户端只提交选项 id 与它自己的参数；**花费、独占、前置、上限都由服务端算**。
- `option_id` 必须是分类 id（`careful_spell` 等），不允许客户端提交点数或"已支付"标记。

### 2.2 三个上限，一个公式

```
可用数量 = 1                                  # 基础
         + 1 if Innate Sorcery 生效且 Sorcery Incarnate（7 级）
免费额度 = 1 if Innate Sorcery 生效且 Arcane Apotheosis（20 级），每回合一次
总花费   = Σ option.cost − 免费额度（不得为负；免费额度只能抵一个选项）
```

- **独占规则是逐选项数据**，不是全局开关：`Empowered` 与 `Seeking` 的规则原文写着"即使已用别的超魔也能使用"，因此它们自身 `exclusive: false`；其余为 `exclusive: true`（占掉一个名额）。
- 7 级 `Sorcery Incarnate` 把"名额"从 1 提到 2；20 级 `Arcane Apotheosis` 只给**每回合一个免费**，不增加名额。

### 2.3 校验顺序（失败即零事件）

```
1. 特性拥有（has_feature("metamagic")）与选项已知（choices 里选过）
2. 选项前置（数据声明：射程/持续/施法时间/伤害类型/目标数/成分）
3. 名额与花费（含 Apotheosis 免费额度、Sorcery Incarnate 加成）
4. 与本次施法的互斥（Quickened 的本回合 1 环+ 限制）
5. 参数合法性（Careful 的目标数 ≤ CHA 调整值、Transmuted 的目标类型在白名单）
```

任何一步失败 → `CombatIntentError`，**不扣点、不掷骰**。

### 2.4 每个选项接在哪（分处理器，不合并成一个大分支）

| 选项 | 接入点 | 必须共用的既有事实 |
|---|---|---|
| Careful | 豁免窗（`_saving_throw_check` 的自动成功与半伤归零） | 豁免事件里的 `success` 与半伤来源 |
| Distant | 射程计算 | 法术 `range` 与距离判定 |
| Empowered | 掷骰窗（保存原始骰 → 重掷 → **强制采用**） | `check_windows` 的 `rerolls` 机制 |
| Extended | 持续时长 + 专注豁免优势 | 专注状态与 `rounds_left` / 分钟时钟 |
| Heightened | 豁免窗（劣势） | `mode_from_sources` |
| Quickened | 动作经济 | `economy.action` / `bonus_action` 与"本回合已施法"标记 |
| Seeking | 攻击窗（未命中后重掷） | `check_windows` 的攻击掷骰事件 |
| Subtle | **有效成分** | 与 Counterspell 共用一个 `effective_components(spell, metamagic)` |
| Transmuted | 伤害管线（类型替换，必须在抗性求值前） | `_resist_damage` 的单一减半 |
| Twinned | 升环目标数 | `slot_level` 与目标数上限 |

**Subtle 是唯一必须"先抽函数"的一条**：`reaction_spells.py:495` 现在直接读 `spell["components"]`，所以先建 `effective_components()`，让施法与反制窗口都读它；否则会出现"玩家声称无成分施法、反制窗口却说有 V"的裂缝。

## 3. 数据契约

### 3.1 选项声明（新增，落在特性目录的 `parameters.metamagic_options`）

```
{
  "id": "careful_spell", "cost": 1, "exclusive": true,
  "requires": {"saving_throw": true},
  "parameter": {"kind": "targets", "limit": "cha_modifier", "minimum": 1},
  "source_ref": "srd-5.2.1:p66:careful-spell"
}
```

- `requires` 只放**可机械判定**的前置：`range_min_ft` / `duration_min` / `casting_time` / `damage_types` / `saving_throw` / `attack_roll` / `upcast_target_bonus`。
- 无法机械判定的（如 Twinned 只对"可升环多选目标"的法术）**必须由法术数据声明**（`upcast_target_bonus: true`），不允许运行时靠文本猜。

### 3.2 角色侧状态

```
features.metamagic.known = ["careful_spell", "quickened_spell"]     # 2/10/17 级各 2 个
features.innate_sorcery = {"uses": 2, "active_until_minute": 0}      # 长休回满
```

- 选项选择复用 S0-B 的 owner-scoped 多选/替换主干（升级时 2 个；升级可换 1 个）。
- `Innate Sorcery` 的 2 次是**长休资源**，进 `srd_recovery.json`；7 级的"花 2 点术法点再开"走 F6 的术法点，不新建资源。

## 4. 与原任务书的差异（覆盖原文 §1、§7）

1. **§1 的执行顺序改为**：先容器（意图字段 + 三个上限 + 校验顺序 + `effective_components`），再按 §2.4 的表逐项接处理器；不得先写某一个选项再补容器。
2. **§3 的基线事实以 §1 表为准**：材料布尔已存在、`condition.blocked` 可落账、Counterspell 仍读原始成分（必须改）。
3. **停止条件替换为**：
   - 法术目录缺 `material_cost_specified` / `material_consumed` 时**不得**实现 Subtle（fail closed，而不是退回文本匹配）；
   - 决策窗无法保存原始骰时**不得**实现 Empowered / Seeking；
   - 反制窗口不能与施法共用 `effective_components()` 时**停止**，不允许两边各判一次；
   - 需要新建第二套术法点资源、或把"已支付"交给客户端时**停止**。
4. **不实现**：Font of Magic 本体、Sorcerous Restoration、其他职业的超魔、2014 Twinned、前端呈现。

## 5. 验收口径（沿用原文 §6 编号，补充断言位置）

- 金额与上限断言落在 `sorcery_points` 的 current 上（不是事件里的 `cost` 字段）。
- Empowered / Seeking 必须断言**最终采用的骰值**（`rolls` / `natural`），不能只断言"发生了一次重掷"。
- Subtle 必须同时断言两件事：施法成功、**反制窗口判定为不可反制**。
- Quickened 必须断言"本回合再放 1 环+ 法术被拒"，以及反向顺序同样被拒。


---

## 6. 执行进展（2026-11-23，第一轮）

### 6.1 已落地：容器 + Subtle + Transmuted

| 项 | 位置 | 状态 |
|---|---|---|
| `metamagic` 意图字段与定价 | `spells/metamagic.py::plan` | ✅ 名额（1／Incarnate 2）、Apotheosis 免费额度、独占与非独占比、点数不足拒绝 |
| 十选项的数据表 | 同上 `OPTIONS` | ✅ 十个 id、费用、`exclusive`、前置（射程/持续/施法时间/伤害/攻击/升环/豁免） |
| 施法校验 | `validation.py::_validate_metamagic` | ✅ 与结算共用同一个 `plan()`，失败即拒、不扣点 |
| 扣点与审计 | `resolution.py::_metamagic_events` + reducer | ✅ `class_resource.spent(sorcery_points)`、`metamagic.applied`、`metamagic.free_used` |
| Transmuted Spell | 同上 | ✅ 改写 `effect["damage_type"]` 的副本，抗性与伤害读同一份 |
| Subtle Spell | `metamagic.effective_components` | ✅ 唯一权威；有价格/被消耗的 M 仍保留 |
| 特性声明 | `srd_class_features.json`（sorcerer） | ✅ `innate_sorcery`/`metamagic`/`sorcery_incarnate`/`arcane_apotheosis` |
| 战斗投影 | `primitives.FEATURE_STATE_KEYS` | ✅ `metamagic.known` 进 actor view（否则战斗里永远"未选"） |

### 6.1b 已落地：Innate Sorcery 本体

| 项 | 位置 | 状态 |
|---|---|---|
| 开启意图 | `primitives.INTENT_TYPES` + `engine` 分派 | ✅ `innate_sorcery`，附赠动作 |
| 两条支付路径 | `_innate_sorcery_events` | ✅ 有次数扣 1 次；无次数且 7 级则扣 2 术法点 |
| 长休资源 | `srd_recovery.json`（sorcerer） | ✅ `innate_sorcery` 2 次（原本已有，未重复） |
| DC +1 | `view.py::_player_view` | ✅ 条件激活时 `spell_save_dc + 1` |
| 法术攻击优势 | `resolution.py::_spell_events` 的 spell_attack 分支 | ✅ 只加在法术攻击路径，武器攻击不受影响 |
| 校验 | `_validate_innate_sorcery` | ✅ 重复开启、附赠动作已用、次数与点数都不够时拒绝 |

### 6.1c 已落地：Quickened / Careful / Heightened

| 项 | 位置 | 状态 |
|---|---|---|
| Quickened | `triggered_spells._spell_cost_events(quickened=)` + `_validate_spell` 成本判定 | ✅ 成本从动作**换成**附赠动作（不是多扣一项） |
| Careful | `_saving_throw_check(forced_outcome="success")` | ✅ 自动成功、不掷骰、事件带 `auto_succeeded` 与来源 |
| Heightened | 同上 `forced_outcome="disadvantage"` | ✅ 只对指定目标追加劣势来源，不写进目标状态 |


### 6.1d 已落地：Distant / Extended / Twinned

| 项 | 位置 | 状态 |
|---|---|---|
| Distant | `_spell_effective_range(..., plan)` | 射程 5 尺以上翻倍；触及变 30 尺；Self 不变 |
| Extended | `_metamagic_events` 的效果副本 | `duration_rounds` / `duration_minutes` 翻倍（上限 24 小时），不改目录原值 |
| Twinned | `_validate_spell` 的 `required_count` | 有效环阶 +1：多一个目标，槽位仍按原环阶 |
| Twinned 前置 | `metamagic._check_requires` | 认 `upcast_target_count > 0` 或 `upcast_target_bonus` |


### 6.1e 已落地：升级选择（2/10/17）与 Extended 的专注优势

| 项 | 位置 | 状态 |
|---|---|---|
| 2/10/17 各选 2 个 | `advancement.metamagic_choices_at` + `requirements` 的 `metamagic_option_ids` | 已接；与 F25 已知形态同口径：**不提交不拦升级**，提交了必须恰好 N 个、不重复、不含已选 |
| 每次升级可换 1 个 | `metamagic_replace {drop, add}` | 已接：drop 必须是升级后已知（含本批新选），add 必须是升级后未知 |
| 写入角色状态 | `apply_next_level` | 写 `features.metamagic.known`（去重追加 / 原位替换），与预览共用 `metamagic.resolve_replacement` |
| 专注豁免优势 | 施放时 `concentration.started.extended` → `_concentration_events` | 已接：Extend 过的专注豁免掷两个 d20 取高，事件带 `advantage_sources` |

**已覆盖**：端到端用例三条——1→2 级选两个并写入；3→4 级替换一个并写入；9→10 级同批次新增两个、再换掉其中一个（含"不能 add 同批新选项"的反向用例）。替换槽按"本批选择已写入"后的已知集合生成下拉，预览顺序不影响候选。


### 6.1f 已落地：Empowered / Seeking（十项完成）

| 项 | 位置 | 状态 |
|---|---|---|
| Empowered | `resolution._damage_roll` | 重掷**点数最低的 N 颗**并采用；重掷走同一 `damage_node` 的第二次掷骰，所以每颗骰子的面数（d6/d8/d10）仍由节点决定 |
| Empowered 上限 | `_validate_metamagic` | 重掷数 ≤ 魅力调整值（至少 1），超出直接拒绝 |
| Seeking | `_spell_events` 的 spell_attack 分支 | 未命中时只重掷那颗 d20，加值与掩蔽不重来，**强制采用**，事件带 `seeking{before, after}` |
| 审计 | `dnd2024.metamagic.rerolled` + reducer | 记录重掷了哪几颗、前值与后值 |

**为什么不把"选哪几颗"交给客户端**：服务器先掷骰，客户端拿到的就是结果；让客户端回传"我要重掷第 3、5 颗"意味着要么把骰子发给客户端再信任它，要么多开一个异步决策窗。选最低的那几颗是玩家看到骰面后唯一理性的选择，也把服务端保持为唯一掷骰者。

**Seeking 与 Lucky 的区别**：Lucky 是掷骰前加一个 d20 取高（优势），Seeking 是**未命中之后**换一个结果；两者都在同一颗 d20 上，但入口不同，事件字段也不同（`advantage_sources` vs `seeking`）。

### 6.2 未落地

| 项 | 缺什么 |
|---|---|
| 前端 | 未做（与既有约定一致：不在本片开并行 UI） |

### 6.3 本轮验证

```
tests/rulesets/test_dnd2024_metamagic.py   16 passed
全量 tests/rulesets                      2739 passed（10:17）
ruff / mypy                              干净
```

## 7. §6.1b 的返工：Innate 从专用意图迁到 `class_capability`（2026-11-25）

`## 6.1b` 记的是"Innate 本体已落地"，落地形态是**专用意图** `innate_sorcery`：自己的
校验分支、自己的事件分支、自己的 `INTENT_TYPES` 条目。这个形态有一个当时没写进来的
问题——**它不在 `combat_capabilities` 投影里，所以面板上没有瓦片承载它**。引擎能结算，
玩家点不到，正是 `#836` / `#838` 的同一个毛病。

按 `ENGINEERING_RULES` §25（主动职业特性的唯一入口）重做：

| 原 | 现 |
|---|---|
| 意图类型 `innate_sorcery` | `class_capability` + `capability_id: innate_sorcery` / `innate_sorcery_from_points` |
| `_validate_innate_sorcery`（次数、附赠动作、等级、术法点四处判定） | 声明里的 `cost` + `exhausted_resource_id` + `requires_feature_id`；剩下"做成什么"交给结算 |
| `_innate_sorcery_events` 自己发 `action.spent` / `class_resource.spent` | 只发 `condition.applied`（`paid_with` 区分两种价格）；成本由通用块统一落账 |
| `INTENT_TYPES` 里的 `innate_sorcery` | 删除 |

两条声明（`innate_sorcery` 特性 → `capabilities`）：

```json
{"id": "innate_sorcery",
 "cost": {"slot": "bonus_action", "resources": [{"id": "innate_sorcery", "amount": 1}]},
 "forbidden_condition": "innate_sorcery", "actions": [{"kind": "innate_sorcery"}]}
{"id": "innate_sorcery_from_points",
 "cost": {"slot": "bonus_action", "resources": [{"id": "sorcery_points", "amount": 2}]},
 "forbidden_condition": "innate_sorcery",
 "exhausted_resource_id": "innate_sorcery", "requires_feature_id": "sorcery_incarnate",
 "actions": [{"kind": "innate_sorcery"}]}
```

`exhausted_resource_id` 与 `requires_feature_id` 是本片为这件事新补的两个门禁字段：
前者表达"次数先用完，这条路才成立"（与 `pool_resource_id` 的"要有余额"正好相反），
后者表达"第二条价格挂在 7 级的另一条特性上"。两者都只影响可用性，不参与结算。

成本写的是 `#848` 引入的新形态（`slot` / `resources`），旧的 `{"bonus_action": 1}`
仍被解析器接受，但新代码不再产生它。
