# S0 契约审计：A 类六份任务书（D1 / D2 / D3 / E1 / E2 / E3）的真实落地度

| 项 | 值 |
|---|---|
| 审计日期 | 2026-11-07 |
| 审计对象 | `feat/dnd2024-derived-defense-speed@340b003f`（含 S0-A/A2、S0-B、S0-C，以及 D1–D4 修复；文档分支 `docs/dnd2024-s0-specs@5146c291`） |
| 基线变更 | 2026-11-07 22:44 该链已 rebase 到 `main@10a6ab23`（含 `#687`/`#689` D3b、`#688` 拥有过滤、`#692` CoC 轨），链头现为 `73d11263`。唯一冲突是 `features/resolver.py` 的拥有过滤叠加，**不影响本报告任何结论**。新提交 id 与验证数字见 `S0_TASK_INDEX_2026-11-07.md` §9 |
| 被审任务书 | `S0D1_SUPREME_HEALING`(v2)、`S0D2_OVERCHANNEL`(v1)、`S0D3_CUTTING_WORDS_CHECK_WINDOW`(v2)、`S0E1_DEFEAT_TRIGGER_DARK_ONES_BLESSING`(v1)、`S0E2_PALADIN_AURAS`(v1)、`S0E3_SURVIVOR`(v1)，均为 2026-11-07 版 |
| 规则基准 | `SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 审计方式 | 只读。读 canonical JSON、读消费点代码、跑既有聚焦测试。未改任何产品代码、未改任何既有测试 |
| 审计口径 | 契约是判据，代码是事实。**代码里"有测试通过"不等于"符合契约"**：若测试断言的正是契约废止的行为，该条判为违反 |
| 聚焦测试 | `pytest test_dnd2024_{die_manipulation,check_windows,dark_ones_blessing,aura_devotion,survivor}.py` → **134 passed**（111 个测试函数，参数化展开 134） |

## 0. 结论摘要

六份契约里，**没有一份需要从零实现**——六个载体全都在代码里有落点。但完工度分三档：

| 契约 | 载体 | 行为落地 | 契约形状 | 判定 |
|---|---|---|---|---|
| S0-D1 | `supreme_healing` | 完整 | 1 处（查询缺 owner） | **基本达标**，补 1 处防御性修正 + 3 条证据 |
| S0-D3 | `cutting_words` | 完整（超出切片范围） | 3 处（owner / range / trigger） | **行为达标**，补齐声明与门控 |
| S0-E3 | `survivor` | 完整 | 首回合被反向固化 | **需改规则 + 改测试** |
| S0-E1 | `dark_ones_blessing` | 完整 | 含一条 SRD 没有的规则 | **需删一条规则 + 改测试** |
| S0-D2 | `overchannel` | 核心模型冲突 | 使用计数整套缺失 | **需重做计数与自伤模型** |
| S0-E2 | 三个光环 | 1/3（仅 Devotion 条件免疫） | Aura of Protection 完全不存在 | **需新做 2/3** |

必须优先修的五项（按"是否产生错误数值/错误规则"排序）：

1. **E2-1**：`aura_of_protection` 与 `aura_expansion` 在特性目录里**没有任何定义**，Paladin 6/18 级的光环豁免加值完全不存在。这是三个职业特性里最实的一条规则，目前 0 实现。
2. **E1-1**：`dark_ones_blessing` 实现了 **"队友击杀、10 尺内共享"**（`defeat_share_range_ft`）。SRD 5.2.1 p76 只有"你亲手把敌人降至 0 HP"，契约 §1.4 明确废止了共享版；而 `test_shared_kill_follows_the_share_radius` 正在断言这条错规则。
3. **D2-1**：Overchannel **每次施法都自伤**，没有"长休后首次免费"。契约 §执行侧须知 4 明确废止"每次 2d12/环"的简化；当前实现正是被废止的那个模型。
4. **E3-1**：Heroic Rally **首回合不触发**，且 `test_combat_start_does_not_regenerate` 把这个错误裁剪固化成断言。契约 §1.5 原话是"本任务明确推翻"。
5. **D2-2**：`features.usage` 长休作用域计数**整套不存在**（身份、读写、`reset_feature_usage`、reducer 注册）。现有 `combat.feature_usage` 是"每回合一次"账本，与它同名但语义无关，不能替代。

## 1. 偏差分类

审计把偏差分成三类，修复优先级从 R 到 M：

| 类 | 含义 | 判据 |
|---|---|---|
| **R**（Rule） | 产生错误规则结果，或把契约废止的行为固化 | 玩家会看到与 SRD 不符的数值/效果 |
| **S**（Shape） | 规则方向对，但契约要求的形状不符 | 参数键、审计字段、owner 作用域、事件名 |
| **M**（Missing） | 契约要求的机制整体不存在 | 目录无定义、无消费点、无计数 |

| 编号 | 契约 | 类 | 缺口 | 位置（行号为审计快照） |
|---|---|---|---|---|
| E2-1 | E2 §4.1 | **R + M** | `aura_of_protection` / `aura_expansion` 无特性定义；progression 有名字、locale 有标签、feature catalog 无条目 | `srd_class_features.json`（paladin 段仅 2 条） |
| E2-2 | E2 §4.4 | **R + M** | 四个豁免面（法术 / 重复 / 专注 / 死亡）均无 `aura_bonus`；`src/` 全局 grep 无 `aura_bonus` | `resolution.py`、`engine.py::_concentration_events` |
| E1-1 | E1 §1.4 / §6.11 | **R** | 实现了 10 尺共享他杀 | `resolution.py::_enemy_defeated_feature_events` |
| D2-1 | D2 须知 4 / §4.5.6 | **R** | 每次施法都自伤，无首次免费 | `resolution.py:1114-1134` |
| D2-2 | D2 §4.2 | **R + M** | `features.usage` 计数、`use_index`、`feature_usage.changed`、长休清零全缺 | `features/resources.py`、`resting/engine.py` |
| E3-1 | E3 §1.5 / §4.4.1 | **R** | 首回合不触发，且被测试固化 | `resolution.py:168`、`engine.py:834` |
| D2-3 | D2 §4.4.3 | **S** | 无 `class:wizard` 列表门控；`validation.py` 不读 `class_refs` | `validation.py:717-730` |
| D1-1 | D1 §4.3.2 | **S** | `has_feature(..., "supreme_healing")` 未带 owner | `resolution.py:951` |
| D2-4 | D2 §4.4.2 | **S** | `has_feature(..., "overchannel")` 未带 owner | `validation.py:725`、`view.py:471` |
| D3-1 | D3 §4.2.4 / §4.4.7 | **S** | `has_feature(..., "cutting_words")` 未带 owner | `check_windows.py:183` |
| D3-2 | D3 §4.1.4 | **S** | 60 尺是模块常量，不从目录 `range_ft` 读 | `check_windows.py:93` |
| D3-3 | D3 §4.1 | **S** | 内容缺 `trigger`、`range_ft`；`resource_id` 在顶层不在 `parameters` | `srd_class_features.json` |
| E1-2 | E1 §4.3.2 | **S** | 枚举全队 initiative 找受益者，而非只解析 `source_actor_id` | `resolution.py:1244` |
| E1-3 | E1 §4.4 | **S** | 事件缺 `source_actor_id`/`defeated_actor_id`/`delta`/`owner`/`ability`/`ability_modifier`/`class_ref`/`class_level` | `resolution.py:1278-1281` |
| E1-4 | E1 §4.1 | **S** | `source_ref` 写 p58，契约要求 p76 | `srd_class_features.json` |
| E1-5 | E1 §4.1/§4.2 | **S** | 参数名 `defeat_temp_hp_*` 而非 `temp_hp_ability`/`temp_hp_class_ref`/`temp_hp_minimum`/`trigger` | 同上 |
| E2-3 | E2 §2.7 | **S** | `aura_of_devotion` 自带 `aura_range: 10`，不依附 Aura of Protection、不受持有者 Incapacitated 门控 | `engine.py::_aura_immune_conditions` |
| E2-4 | E2 §4.5.3 | **S** | 拦截只写 `blocked_by_aura`，缺 `blocked_condition` | `resolution.py:1054` |
| E3-2 | E3 §4.5.4 | **S** | `death_save.resolved.roll` 记的是 `total` 而非天然面值 | `resolution.py:1840` |
| E3-3 | E3 §4.5.5 | **S** | 字段名 `crit_threshold` ≠ `death_save_crit_threshold` | `resolution.py:1787` |
| E3-4 | E3 §4.3.3 | **S** | regen 事件缺 `owner`/`trigger`/`base`/`ability`/`ability_modifier` | `resolution.py:1742-1748` |
| E3-5 | E3 §2.11 | **S** | 死亡豁免额外消费 Bless d4（`check_bonus`），契约要求保持 0 → RNG 顺序偏移 | `resolution.py:1770-1775` |
| E3-6 | E3 §4.2.5 | **S** | regen 取单一最大 base，契约要求逐定义独立事件 | `resolution.py:1733` |

## 2. S0-D1 Supreme Healing

### 2.1 载体对账

| 契约要求 | 实际 | 判定 |
|---|---|---|
| `classes.cleric` 新增 `supreme_healing`，owner `subclass:life_domain`，`minimum_level: 17`，`source_ref: srd-5.2.1:p40:supreme-healing`，`parameters: {}` | 完全一致 | ✅ |
| en / zh-CN 标签 `Supreme Healing` / `极效治疗` | 两份 locale 均存在 | ✅ |
| `combat/action_adapter.py` 新增并导出 `maximize_dice_nodes()` | 存在（`action_adapter.py:102`） | ✅ |
| 非 dict 输入抛 `CombatIntentError` | `raise CombatIntentError("formula node must be an object")` | ✅ |
| `dice` → `constant`，公式走 `_parse_ndm()` 严格校验 | `count, sides = _parse_ndm(...)`；`{"op": "constant", "value": count * sides}` | ✅ |
| 五种 op 递归，未知节点原样保留，不修改输入树 | `deepcopy` + 递归 + 白名单 op | ✅ |
| 先 `double_dice_counts()` 再最大化 | 测试断言 `2d8 → 32` | ✅ |
| 只改 `mode == "healing"` 分支，在 `roll_damage_node()` 之前 | `resolution.py:951-956` | ✅ |
| 查询**显式带 owner** | `has_feature(..., "supreme_healing")`，**无 owner** | ❌ S |
| 事件仅在应用时加 `maximized: True` | `**({"maximized": True} if maximized else {})` | ✅ |
| 不改 reducer / validation / 其他治疗入口 | 未改 | ✅ |

关于 `has_feature` 缺 owner 的实际影响：`has_feature(owner=None)` 匹配**任意 owner**（`resolver.py:189-193`）。授予侧是安全的——`_granted_feature_keys()` 只从本职业的子职表取键（`resolver.py:913-922`），非 Cleric 伪造 `subclass:life_domain` 拿不到。风险是**未来**别的 owner 也声明同名短 id 时会误命中，这正是契约 §4.3.2 要防的情形。归为 S 类，不是当前 bug。

### 2.2 测试矩阵（§6，18 项）

| 段 | 项 | 结论 | 依据 |
|---|---|---|---|
| 6.1 | 1–2 | 通过 | `test_catalog_declares_cleric_and_wizard_features`、`test_feature_ownership_follows_levels` |
| 6.1 | 3 | 实现安全但无测试 | 授予侧按职业绑定；无"异 owner 不授予"用例 |
| 6.1 | 4–6 | 通过 | `test_maximize_dice_nodes_transforms_only_dice`（五种 op + 不变量 + 未知节点 + `2d8→32`） |
| 6.2 | 7–8 | 通过 | `test_supreme_healing_maximizes_cure_wounds_without_rng`、`.._upcast_dice`（`healing_word` 亦在该用例内） |
| 6.2 | 9–10 | 通过 | 哨兵 RNG（`rolls=[99]` 断言不被消费） |
| 6.2 | 11 | 通过 | `test_cure_wounds_below_seventeen_still_rolls` |
| 6.2 | 12 | 与 D1-1 同源 | 查询不带 owner，该条无法被独立证据守护 |
| 6.2 | 13–14 | 实现符合，**无直接测试** | `cannot_regain_hp` 路径代码正确（`delta: 0`、保留 `maximized`、`rolls: []`）；reducer 未改，`maximized` 是多余字段 |
| 6.3 | 15–18 | 通过 | `test_supreme_healing_does_not_touch_temp_hp`；既有回归 134 passed |

**判定：16/18 通过，2 项同源偏离（D1-1）；建议补 3 条证据（矩阵 3、13、14）并给查询加 owner。**

## 3. S0-D2 Overchannel

这是六份里唯一**核心模型被废止**的一份。

### 3.1 规则模型冲突

契约 §执行侧须知 4 原文：

> 旧版"每次使用都按 2d12/环收费"的简化已废止。第一次长休后使用必须无自伤，第二次才从 2d12/环开始，之后每次增加 1d12/环。

实际实现（`resolution.py:1114-1134`）：

```python
if intent.get("maximize_damage"):
    per_slot = feature_value(..., "cost_dice_per_slot", 2)
    sides    = feature_value(..., "cost_die_sides", 12)
    cost_node = formula_node_from_dice_formula(f"{per_slot * slot_level}d{sides}")
    cost, cost_rolls = roll_damage_node(cost_node, rng)
```

目录参数 `cost_dice_per_slot: 2` / `cost_die_sides: 12` / `cost_damage_type: necrotic` 就是被废止的那个模型本身：**每一次**都掷 `2 × slot_level` 个 d12。没有首次免费，没有 `use_index`，没有递增。

因此矩阵 §6.3 的 15、16、17、21、22、24 六条全部无对应实现；§6.2 的 7（"使用计数从 0 到 1 且无自伤"）必然失败。

### 3.2 载体对账

| 契约要求 | 实际 | 判定 |
|---|---|---|
| `classes.wizard.overchannel`，owner `subclass:evoker`，14 级，p82 | 一致（除参数） | ✅ |
| `parameters: {maximum_slot_level, usage_scope, self_damage_die, initial_self_damage_dice}` | `{max_slot_level, cost_dice_per_slot, cost_die_sides, cost_damage_type}` | ❌ S |
| `features/resources.py` 的 owner-scoped 长休计数（`(owner, feature_id, scope)`） | **不存在**；`character/*`、`features/*` 中 grep `"usage"` 无命中 | ❌ M |
| `feature_usage_count()` 只读辅助 | 不存在 | ❌ M |
| reducer 按 `before_count` 校验后写 `after_count` | 不存在 | ❌ M |
| `complete_long_rest()` 清零 + `reset_feature_usage` 事件 | `complete_long_rest()` 无任何 usage 处理 | ❌ M |
| `combat/view.py` 投影 `features.usage` | 只投影 `subclass_ref` | ❌ M |
| intent 字段 `{"overchannel": true}` | 字段名 `maximize_damage` | ❌ S |
| 非 bool 值拒绝 | ✅ `raise CombatIntentError("maximize_damage must be a boolean")` | ✅ |
| `class:wizard in spell["class_refs"]` | **`validation.py` 全文无 `class_refs`** | ❌ R |
| 实际 `slot_level` 1–5 | ✅ `if not 1 <= int(slot_level) <= max_slot` | ✅ |
| 必须具有伤害效果 | ✅ `if effect["mode"] not in {...} or not effect.get("damage")` | ✅ |
| 三个伤害分支最大化；重击先翻倍 | ✅ `_maybe_overchannel` 在 790/888/920 三处 | ✅ |
| `dnd2024.feature_usage.changed` 事件 | 不存在 | ❌ M |
| 自伤绕过 `_resist_damage()` | ✅ 直接构造 `resource.changed` | ✅ |
| 自伤后走公共专注检定 | ✅ `_concentration_events(actor, cost, rng)` | ✅ |
| 不改公共 feed 白名单 | ✅ | ✅ |

注意 `combat/reducer.py:120` 的 `"feature_usage": {}` 与 `resolution.py:1292::_feature_used_this_turn` 是**每回合一次**的账本（key 为 actor + round + turn_index），与契约要求的**长休作用域计数**同名异义，不能替代，也不能复用。这一点必须在修复时写清楚，否则很容易被误认为"已经有了"。

### 3.3 测试矩阵（§6，28 项）

| 段 | 项 | 结论 |
|---|---|---|
| 6.1 | 1–2 | 通过 |
| 6.1 | 3 | 无 owner 查询（D2-4）；无异 owner 用例 |
| 6.1 | 4 | 偏离：字段名 `maximize_damage`；非 bool 拒绝通过 |
| 6.1 | 5 | 偏离：槽位 1–5 有校验，**但无 Wizard 列表门控** |
| 6.1 | 6 | 缺失：无门控即无法验证"非塑能仍合法" |
| 6.2 | 7 | **违反**：无首次免费，必然自伤 |
| 6.2 | 8–12 | 数值部分通过（`test_overchannel_maximizes_saving_throw_damage` / `_automatic_damage` / `_each_ray_after_crit_doubling`）；自伤环阶部分随 D2-1 失效 |
| 6.2 | 13–14 | 13 由既有回归兜住；14（首次免费零 RNG）缺失 |
| 6.3 | 15–17 | **缺失**：无计数、无 `use_index`、无递增 |
| 6.3 | 18–19 | 通过（`_ignores_resistance`、`_can_drop_the_caster`、`_triggers_concentration_check`） |
| 6.3 | 20–22 | 缺失：使用计数不存在，"不撤销计数"与"重放计数一致"无法成立 |
| 6.4 | 23 | 缺失：反制后计数不变无计数可验 |
| 6.4 | 24–25 | **缺失**：长休/短休对计数无影响，因为无计数 |
| 6.4 | 26–28 | 26 通过（`test_supreme_healing_does_not_touch_temp_hp` 等）；27 无需改 feed；28 回归通过 |

**判定：约 9 项通过、4 项偏离、15 项缺失。核心是"重做计数与自伤模型"，不是打补丁。**

## 4. S0-D3 Cutting Words

六份里落地最完整的一份，同时也**超出切片范围**。

### 4.1 载体对账

| 契约要求 | 实际 | 判定 |
|---|---|---|
| 新建 `combat/check_windows.py` | 存在 | ✅ |
| 常量 `CHECK_WINDOW_DECISION = "d20_check_window"` | 实际为 `"check_window"` | ⚠️ S（名字不同） |
| 候选枚举 player/companion，enemy 不新增框架 | `_cutting_words_bards()` 只扫 players + companions | ✅ |
| 敌人攻击、成功、非天然 1 才开窗 | `if attacker.get("side") != "enemy": return []`；`if not check.get("success"): return ""` | ✅ |
| Blinded 反应者 / Invisible 攻击者不满足看见 | 两处显式排除 | ✅ |
| 距离 ≤ 60 尺 | `_distance(...) > CUTTING_WORDS_RANGE_FT` | ✅ |
| 60 尺从目录 `range_ft` 读 | **硬编码模块常量**（`check_windows.py:93`） | ❌ S |
| `_reaction_eligible()` + owner-scoped 复查 | `_reaction_eligible` ✅；`has_feature` **无 owner** | ❌ S |
| spend：`decision.cleared` → `reaction.spent` → `class_resource.spent` → 一次 `1d<die_sides>` | 实现齐全 | ✅ |
| `check.resolved(kind="cutting_words")` 审计字段 | 实现齐全 | ✅ |
| 天然 20 / S0-A 暴击必中，扣减不能改 d20 身份 | 保留 `natural`、`critical` | ✅ |
| AI：非重击且最大骰可翻盘才 spend | 分支存在 | ✅ |
| 内容 `parameters: {trigger, die_sides_track, resource_id, range_ft}` | 实际 `resource_id` 在顶层，`parameters` 仅 `{die_sides_track}` | ❌ S |
| 不改 reducer / reaction_spells / view / resources | 未改 | ✅ |

`die_sides_track: bardic_die` 能工作是因为 `feature_value(..., "die_sides")` 会自动解析 `<key>_track` 约定（`resolver.py:277-286`），这一条实现是对的。

**范围提示**：`check_windows.py` 共 46 个测试函数，覆盖了 Lucky、Dark One's Own Luck、Cutting Words 的伤害掷骰面，以及死亡豁免窗口。契约 §1.4 明确"不实现它的伤害掷骰面、属性检定面"，§4.2 要求"只承载本切片的掷后攻击窗口，不发明通用插件框架"。实际做成了通用窗口框架。这不是缺陷（能力更强），但**任务索引的口径必须改**，否则后续 F 系列会重复排期。

### 4.2 测试矩阵（§6，30 项）

`test_dnd2024_check_windows.py` 46 个函数 / 48 个展开用例，全绿。抽查覆盖：开窗资格、60/61 尺边界、Blinded、Invisible、失能、资源为 0、非 assigned 拒绝、伪造数值无效、spend/decline 取值、改判后 Shield 读到新 total、多诗人串行、AI 决策、重放。

**判定：30/30 行为通过；3 项契约形状偏离（D3-1 owner、D3-2 range、D3-3 声明），外加范围已扩张需在索引更正。**

## 5. S0-E1 Dark One's Blessing

### 5.1 一条与 SRD 相反的规则

契约 §1.4：

> 本切片只实现 **伤害来源亲自把敌人降至 0 HP → 来源获得临时 HP**。不实现旧 S-P4 所写的"他人击败、10 尺内共享"；**该效果不属于 SRD 5.2.1 的 Dark One's Blessing**。

SRD 5.2.1 p76 原文（契约 §2 引用）："When you reduce an enemy to 0 Hit Points, you gain Temporary Hit Points equal to your Charisma modifier plus your Warlock level (minimum of 1)." —— 主语是 **you**，没有距离、没有共享。

实际实现（`resolution.py:1244-1276`）：

```python
for raw_actor_id in combat.get("initiative") or []:      # 枚举全队
    ...
    share = definition.parameters.get("defeat_share_range_ft")
    eligible = actor_id == attacker_id
    if not eligible and isinstance(share, int) and not isinstance(share, bool):
        eligible = self._distance(combat, actor_id, target_id) <= share
```

目录里 `defeat_share_range_ft: 10`，`test_dnd2024_dark_ones_blessing.py:295` 的 `test_shared_kill_follows_the_share_radius` 参数化了 `(0,0,5) → True`、`(0,15,20) → False`。

即：**队友在 10 尺内击杀时，Fiend Warlock 白拿 temp HP**。契约矩阵 §6.11 要求"即使 10 尺内也不获得"。这属于 R 类，且被测试固定住了。

### 5.2 载体对账

| 契约要求 | 实际 | 判定 |
|---|---|---|
| `classes.warlock.dark_ones_blessing`，owner `subclass:fiend_patron`，3 级 | 一致 | ✅ |
| `source_ref: srd-5.2.1:p76:dark-ones-blessing` | 实际 `p58` | ❌ S |
| `parameters: {trigger, temp_hp_ability, temp_hp_class_ref, temp_hp_minimum}` | `{defeat_temp_hp_ability, defeat_temp_hp_add_class_level, defeat_share_range_ft}` | ❌ S |
| 只解析 `source_actor_id` 的特性 | 枚举全队 initiative | ❌ S |
| 不添加共享半径 | 有 `defeat_share_range_ft: 10` | ❌ R |
| 归零守卫：非 enemy / 结算前 hp≤0 / damage≤0 / 未跨过 0 | 四条件齐全 | ✅ |
| 多射线：只在首次跨过时触发一次 | `prior_damage` + `defeated_targets` 去重 | ✅ |
| `amount = max(min, cha_mod + warlock_level)` | `max(1, modifier + level)`；但 level 来自 `class_state()`，不校验 `class:warlock` | ⚠️ S |
| 事件字段 `owner`/`ability`/`ability_modifier`/`class_ref`/`class_level`/`source_actor_id`/`defeated_actor_id`/`delta` | 只有 `target_id`/`amount`/`feature_id` | ❌ S |
| 顺序：伤害 → 专注 → temp HP → victory | 顺序正确 | ✅ |
| 不动 reducer；keep-max | 复用 `resource.changed(temp_hp)` | ✅ |
| 七个最终接入点 | 攻击直通、spell_attack、saving_throw、automatic_damage、Uncanny Dodge、Shield、Divine Smite 均有调用 | ✅ |

`class_state()` 只返回单职业；多职业角色会返回 `None` 并被跳过——这与契约 §4.3.4 "拿不到匹配职业状态属于集成错误，停止并报告"的处理不同（当前是静默跳过）。

### 5.3 测试矩阵（§6，30 项）

`test_dnd2024_dark_ones_blessing.py` 16 个函数全绿，覆盖归零守卫、负数魅力保底、keep-max、temp HP 吸收、0 HP 不复活、多射线去重、saving throw 击杀、victory 顺序。

| 段 | 项 | 结论 |
|---|---|---|
| 6.1 | 1–6 | 1–3 通过；4 偏离（p58 + 参数键）；5 偏离（校验针对 `defeat_*` 而非契约参数集）；6 通过 |
| 6.2 | 7–14 | 7–10、12–14 通过；**11 违反**（共享半径） |
| 6.3 | 15–23 | 通过（含 18/19 多射线与多目标、20 借机、21 Shield/Uncanny Dodge、22/23 Divine Smite） |
| 6.4 | 24–30 | 通过 |

**判定：约 24 项通过、5 项偏离、1 项违反。修复要点是删掉共享半径这条规则、改一条测试、补齐事件审计字段。**

## 6. S0-E2 Paladin Auras

三分之一实现，且缺的是最核心的三分之二。

### 6.1 Aura of Protection 完全不存在

Paladin 段在特性目录里只有两条：

```text
['paladins_smite', 'aura_of_devotion']
```

`aura_of_protection` 与 `aura_expansion` 在以下三处**都有**痕迹，唯独没有定义：

| 位置 | 有什么 | 缺什么 |
|---|---|---|
| `progression_catalogs/srd_classes.json` | Paladin L6 `aura_of_protection`、L18 `aura_expansion` 排在授予表里 | — |
| `locales/en|zh-CN/class_feature_catalogs/*.json` | `aura_of_protection` 有标签 | — |
| `content/class_feature_catalogs/*.json` | **无条目** | 整个 `ClassFeatureDefinition` |

`features_for()` 用 `by_key.get(key)` 取定义（`resolver.py:162-165`），`by_key` 里没有 → 即使 20 级 Paladin 也**永远不拥有**这两条特性，`has_feature()` 恒为 False，`feature_value()` 恒返回默认值。

连带后果：契约 §4.4 要求的四个豁免消费面全部为空。

| 消费面 | 契约要求 | 实际 |
|---|---|---|
| 法术豁免 | `total = d20 + modifier + bless + aura_bonus`，事件新增 `aura_bonus` / `aura_sources` | 无 `aura_bonus`（`src/` 全局 grep 为空） |
| 回合末重复豁免 | 同一辅助、按结算时位置重算 | 同上 |
| 死亡豁免 | `modifier: 0`、`bless_bonus: 0`、`aura_bonus`、`aura_sources` | 只有 `check_bonus`（Bless d4） |
| 受伤专注豁免 | `_concentration_events` 增 `instance`/`combat`，加 aura bonus | 未加 |

§6.2（8 项）与 §6.3（8 项）因此整体缺失，§6.5 的 31/32（重放、feed 白名单）也失去对象。

### 6.2 Aura of Devotion：实现了，但依附关系不同

实现位置：`engine.py::_aura_immune_conditions()`（1371 起），由 `resolution.py:1050` 在构造 `condition.applied` 之前调用。

| 契约要求 | 实际 | 判定 |
|---|---|---|
| 免疫 `charmed`，队友 + 本人（距离 0 在范围内） | 正确 | ✅ |
| 敌方目标不受玩家光环 | `if str(ally.get("side")) != target_side: continue` | ✅ |
| 依附 Aura of Protection 的 10/30 尺范围 | 自带 `aura_range: 10`，与基础光环无关 | ❌ S |
| 持有者 Incapacitated 时连带失效 | **无门控** | ❌ S |
| 参数 `aura_source_feature_id` + `aura_condition_immunities` | `aura_range` + `aura_immune_conditions`，无 source 引用 | ❌ S |
| 拦截不写 `condition.applied`，在 `check.resolved` 记 `blocked_condition` + `blocked_by_aura` | 只写 `blocked_by_aura: <feature_id>`，无 `blocked_condition` | ❌ S |
| 已有 Charmed 进入光环：暂时无效但**不删除** | 正确（不发 `condition.removed`） | ✅ |
| 18 级 30 尺 | 无 `aura_expansion`，30 尺不存在 | ❌ M |

`_aura_immune_conditions` 有一处做得比契约更稳：它用 `has_feature(..., owner=definition.owner)` 显式 owner-scoped 查询，并在注释里写明"同名特性可以同时存在于 class 与 subclass 两个 owner 下，用短 id 查询会命中错误的那一条"。这条经验应当反哺给 D1/D2/D3 的四处缺 owner 查询。

### 6.3 测试矩阵（§6，33 项）

| 段 | 项 | 结论 |
|---|---|---|
| 6.1 | 1 | **缺失**：L6 无 Aura of Protection |
| 6.1 | 2 | 通过（Devotion L6 无、L7 有） |
| 6.1 | 3 | 部分：Devotion 的 owner 正确；Protection 不存在 |
| 6.1 | 4 | 偏离：Protection/Expansion 无标签可验 |
| 6.1 | 5–6 | **缺失**：`aura_range_ft` 等参数不存在，无校验对象 |
| 6.2 | 7–14 | **全缺失**（8 项） |
| 6.3 | 15–22 | **全缺失**（8 项） |
| 6.4 | 23 | 通过（`test_ally_inside_aura_is_immune_and_the_check_names_the_aura`） |
| 6.4 | 24 | 部分：11 尺外正常 Charmed 通过；30 尺缺失 |
| 6.4 | 25–30 | 通过（本人、敌人、边界、重复豁免、非 Charmed、双光环） |
| 6.5 | 31–32 | **缺失**（依赖 aura bonus 事件与 feed 字段） |
| 6.5 | 33 | 通过（回归） |

**判定：约 10 项通过、4 项偏离、19 项缺失。这是六份里工作量最大的一份。**

## 7. S0-E3 Survivor

内容是六份里最贴契约的（§4.1 的 JSON 与契约逐字段一致），问题集中在两个接入点和字段口径。

### 7.1 首回合被反向固化

契约 §1.5：

> 旧草案中"战斗开场首行动者不触发 Heroic Rally"是错误裁剪，**本任务明确推翻**：SRD 的每个自己的回合开始包括战斗第一轮。

实际：

| 位置 | 现状 |
|---|---|
| `resolution.py:168` | `def _start_combat_event(...) -> dict[str, Any]:` 返回**单个 dict** |
| `engine.py:834` | `events.append(self._start_combat_event(...))` |
| `tests/.../test_dnd2024_survivor.py:549` | `def test_combat_start_does_not_regenerate()` ← 断言首回合**不**触发 |

契约 §4.4.1/4.4.2 要求返回列表 + `events.extend(...)`，§6.7 要求"首行动者 HP 恰为 `max_hp//2` 时，`combat.started` 后立即产生一次再生"。

对比：`turn.advanced` 路径是正确接上的（`resolution.py:1713` 用 `*self._turn_start_regen_events(next_view)` 展开）。所以只差首回合这一个接入点，而现有测试正把错误行为锁死。

### 7.2 其余对账

| 契约要求 | 实际 | 判定 |
|---|---|---|
| `classes.fighter.survivor`，owner `subclass:champion`，18 级，p49 | 逐字段一致 | ✅ |
| `death_save_advantage: true` 只能为 true | `resolver.py:1125-1129` 校验 | ✅ |
| `death_save_crit_threshold` 2..20 | 校验存在 | ✅ |
| `turn_start_regen_base` / `_ability` 成对、ability 合法 | 校验存在 | ✅ |
| Bloodied `1 <= hp <= max_hp//2` | `if hp < 1 or maximum <= 0 or hp > maximum // 2` | ✅ |
| `amount = base + con_mod`，≤0 跳过 | 正确 | ✅ |
| `cannot_regain_hp` → `delta=0` + `blocked_by` | 正确 | ✅ |
| 每定义独立事件 | 用 `max_feature_value_source` 取单一最大 base | ❌ S（E3-6） |
| 事件含 `owner`/`trigger`/`base`/`ability`/`ability_modifier` | 只有 `feature_id` | ❌ S（E3-4） |
| 首回合接入 | 单 dict + append | ❌ R（E3-1） |
| 死亡豁免优势：仅优势来源非空才掷第二骰 | `feature_flag_sources` 非空才掷 | ✅ |
| 阈值分支优先级：阈值 → 天然 1 → total≥10 → 失败 | `_death_save_outcome_events` 顺序正确 | ✅ |
| `death_save.resolved.roll` 保留天然值 | 写的是 `check["total"]` | ❌ S（E3-2） |
| 字段 `death_save_crit_threshold` | 写的是 `crit_threshold` | ❌ S（E3-3） |
| `bless_bonus = 0`（不补 Bless 缺口） | 实际掷 Bless d4 作为 `check_bonus` | ❌ S（E3-5） |
| 死亡豁免 aura 合成 | 无 `aura_bonus`（依赖 E2） | ❌ M |
| 不改 reducer | 未改 | ✅ |

### 7.3 测试矩阵（§6，33 项）

| 段 | 项 | 结论 |
|---|---|---|
| 6.1 | 1–6 | 通过（含 5 条 fail-closed 参数化） |
| 6.2 | 7 | **违反**：`test_combat_start_does_not_regenerate` |
| 6.2 | 8–17 | 8–12、14–17 通过；13（到期条件不多阻断一轮）无直接测试；17 缺审计字段 |
| 6.3 | 18–21 | 18/19/20 通过（`test_repeat_save_precedes_turn_advance_and_regeneration`）；19 的"regen 紧随 started"不成立；21 无直接测试 |
| 6.4 | 22–31 | 22–26 通过（含阈值 18/19/20、双骰取高、`[1,1]`）；27–29 **缺失**（aura 分项）；30/31 通过 |
| 6.5 | 32–33 | 32 **缺失**（依赖 E2）；33 通过 |

**判定：约 25 项通过、6 项偏离、2 项缺失（其中 1 项违反被测试固定）。**

## 8. 内容声明 vs 契约参数 对账

一次看全六份的声明漂移：

| 特性 | 契约参数 | 实际参数 | 差 |
|---|---|---|---|
| `supreme_healing` | `{}` | `{}` | — |
| `survivor` | `death_save_advantage`, `death_save_crit_threshold`, `turn_start_regen_base`, `turn_start_regen_ability` | 同 | — |
| `cutting_words` | `trigger`, `die_sides_track`, `resource_id`, `range_ft` | `resource_id`(顶层), `die_sides_track` | 缺 2 |
| `overchannel` | `maximum_slot_level`, `usage_scope`, `self_damage_die`, `initial_self_damage_dice` | `max_slot_level`, `cost_dice_per_slot`, `cost_die_sides`, `cost_damage_type` | 全不同 |
| `dark_ones_blessing` | `trigger`, `temp_hp_ability`, `temp_hp_class_ref`, `temp_hp_minimum` | `defeat_temp_hp_ability`, `defeat_temp_hp_add_class_level`, `defeat_share_range_ft` | 全不同 |
| `aura_of_protection` | `aura_range_ft`, `aura_save_bonus_ability`, `aura_save_bonus_minimum` | **无定义** | 全缺 |
| `aura_expansion` | `aura_expands_feature_id`, `aura_range_override_ft` | **无定义** | 全缺 |
| `aura_of_devotion` | `aura_source_feature_id`, `aura_condition_immunities` | `aura_range`, `aura_immune_conditions` | 全不同 |

只有 `supreme_healing` 与 `survivor` 的声明完全对得上契约。这不是巧合——这两份是 v2/v1 里"声明最简"的两份，`overchannel` 和 `dark_ones_blessing` 恰恰是被 v2 大改规则的两份。

## 9. 建议修复顺序

按"错误规则优先级高于契约形状、依赖链自下而上"排：

| 序 | 项 | 类 | 理由 |
|---|---|---|---|
| 1 | E2-1 + E2-2：补 `aura_of_protection` / `aura_expansion` 定义，接四个豁免消费面 | R+M | 缺口最大；且 E3 的 27–29、32 在等它 |
| 2 | E1-1：删共享半径，改 `test_shared_kill_follows_the_share_radius` | R | 唯一一条"多给玩家收益"的错误规则 |
| 3 | D2-1 + D2-2：重做长休计数与递增自伤 | R+M | 影响玩家受伤量；`features.usage` 是后续 Holy Ward 等的共用基建 |
| 4 | E3-1：`_start_combat_event` 改事件列表 + 改 `test_combat_start_does_not_regenerate` | R | 改动最小（1 个接入点 + 1 条测试），收益明确 |
| 5 | D3-1/2/3、D1-1、D2-4、E1-2..5、E2-3/4、E3-2..6：owner 作用域、参数键、审计字段 | S | 批量、低风险、可一个提交收口 |
| 6 | 补证据：D1 矩阵 3/13/14、D2 矩阵 6、E3 矩阵 13/21 | — | 无测试的条目先补测试再判定 |

依赖关系提醒：E2 必须先于 E3 的 aura 分项（E3 §6.27–29、32）；D2 的 `features.usage` 与 E2 无冲突，可并行。

## 10. 本次审计未覆盖

1. **`srd-5.2.1:p58` vs `p76`**（E1-4）：本工作树无 `SRD_CC_v5.2.1.pdf`，无法在本次审计中核对 Fiend Patron 的印刷页。契约 §2 声称 p76；需在修复时用 PDF 复核后定稿，不要凭任何一个数字直接改。
2. **D5 元数据漂移**（`srd_class_features.json` 缺 druid/ranger bucket、catalog `source_ref` 误写 monk 页、`automation_level` 只覆盖 26/198）：已在阶段一状态审计登记，本次契约审计不重复。
3. **阶段二 D1–D4 修复的回归**：本次只跑 5 个聚焦文件（134 passed），未复跑全量 `tests/rulesets`。全量基线是 1341 passed / 11 failed（全部 `character_builder` 的 `CON` 环境失败），D1–D4 提交时已核对一致。
4. **前端与公共 feed 的实际渲染**：本次只核对字段白名单是否存在，未验证 UI 呈现。

## 11. 审计本身的可复核性

```bash
# 聚焦测试（134 passed）
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest \
  tests/rulesets/test_dnd2024_die_manipulation.py \
  tests/rulesets/test_dnd2024_check_windows.py \
  tests/rulesets/test_dnd2024_dark_ones_blessing.py \
  tests/rulesets/test_dnd2024_aura_devotion.py \
  tests/rulesets/test_dnd2024_survivor.py -q --basetemp=.ptmp-audit-a

# 光环缺失的直接证据
python -c "import json,io;d=json.load(io.open('templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json',encoding='utf-8'));print([f['id'] for f in d['classes']['paladin']['features']])"
# → ['paladins_smite', 'aura_of_devotion']

# 四个缺 owner 的查询
grep -rn 'has_feature(.*"supreme_healing"\|has_feature(.*"overchannel"\|has_feature(.*"cutting_words"' src/
# 全局无 aura_bonus
grep -rn 'aura_bonus' src/     # → 空
# 无 features.usage
grep -rn '"usage"' src/rulesets/dnd2024/character/ src/rulesets/dnd2024/features/   # → 空
```

行号均为 `340b003f` 时快照，只作定位参考，不作契约。该链已 rebase 到 `main@10a6ab23`，行号已有偏移——复查请按符号名定位，不要按这里的行号。
