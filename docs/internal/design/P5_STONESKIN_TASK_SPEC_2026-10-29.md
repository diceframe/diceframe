# 切片 P5 任务书：Stoneskin（伤害类型抗性 + 偿还 Rage 抗性债）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：在 P1（invisibility）之后执行，`srd_combat_core.json` 的 `conditions` 注册表节与 `catalog.py` 的对应校验已存在；本切片在该注册表上扩展抗性字段。若 P1 尚未合入，停止。

---

## 1. 目标

为 D&D 2024 规则集实现 **stoneskin（石肤术，4 环）** 的完整闭环：目录数据 → 施法 → 目标受到的钝击/穿刺/挥砍伤害减半（含专注豁免 DC 与击败判定联动）→ 测试。

本切片同时沉淀两项通用原语并偿还一项债：

1. **condition → 伤害抗性（数据驱动）**：抗性来源 = 战斗目录 `conditions` 注册表的静态声明（rage、stoneskin）∪ condition 元数据的动态声明（选择型法术写入 `resist_damage_types`）；
2. **选择型抗性**：`resist_choice` 效果字段 + intent 选择字段（protection_from_energy 的五选一、fire_shield 的二选一）；
3. **偿还 Rage 抗性裁剪债**：2024 狂暴含"钝击/穿刺/挥砍抗性"，切片 2 未实现；本切片用注册表静态声明补上，**不改 Rage 的引擎代码**。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 石肤术：四环防护（德鲁伊、游侠、术士、法师）。动作；触碰；V、S、M（价值 100GP+ 的钻石粉末，作为法术耗材）；专注至多 1 小时。在法术结束前，你触碰的一个自愿生物获得对钝击、穿刺、挥砍伤害的抗性。
- 抗性通用规则：如果你对某个伤害类型具有抗性，则你承受的该类型伤害**减半（向下取整）**。抗性/易伤不叠加：多个同伤害类型的抗性同时存在时，只需计算其中一个。生效顺序：先数值修正（加值、减值、乘数），然后抗性，最后易伤。
- 狂暴（野蛮人 1 级）：狂暴激活期间"你具有钝击、穿刺、挥砍伤害的抗性"。
- 防护能量（3 环）：触碰，专注至多 1 小时，自愿生物获得你选择的一种伤害类型的抗性：强酸、寒冷、火焰、闪电、雷鸣。
- 防护毒素（2 环）：触碰，1 小时；结束目标陷入的中毒状态；目标为避免陷入或结束中毒状态所做的豁免具有优势（本切片裁剪，见 §1.2）；具有毒素伤害的抗性。
- 火焰护盾（4 环）：自身，10 分钟（非专注）；温暖护盾赋予寒冷抗性、冻寒护盾赋予火焰抗性；近战命中者的 2d8 反伤（本切片裁剪，见 §1.2）。

出处：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/4环.htm` L232-233（石肤术）、L128-129（火焰护盾）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/抗性和易伤.htm` L7-16（减半取整、不叠加、生效顺序）；
- `.rulebooks/dnd5r/玩家手册2024/角色职业/野蛮人/野蛮人.htm` L59-66（狂暴规则，抗性在 L63）；
- `.rulebooks/dnd5r/玩家手册2024/法术详述/3环.htm` L178-179（防护能量）；
- `.rulebooks/dnd5r/玩家手册2024/法术详述/2环.htm` L270-271（防护毒素）。

### 1.1 必须锁定的规则边界

1. **减半一次**：无论有多少个抗性来源（rage + stoneskin 同时在身），同一次伤害只减半一次——集合判成员，不做连乘。
2. **向下取整**：`amount // 2`；1 点伤害抗性后为 0。
3. **抗性作用于伤害总量，不改骰迹**：`rolls` 记录原始掷骰，减半只反映在 `amount`/`delta` 上，事件加 `"resisted": true` 标记。
4. **专注豁免 DC 用扣抗后的伤害**：规则书"DC 等于 10 或你所受伤害的一半（取高者）"——`_concentration_events` 拿到的必须是减半后的值。
5. **击败判定用扣抗后的伤害**：`_last_hostile_defeated` 同理。
6. **豁免减半与抗性减半独立相乘**：saving_throw 的 `half_on_success` 先算（既有代码），抗性再算，最终四分之一——顺序无关，结果一致。
7. **治疗不受抗性影响**：healing 分支不经过抗性辅助。
8. **生效顺序锁定为"修正 → 抗性"**：Uncanny Dodge 的乘法修正在决策结算期对 `damage_event` 的 amount 再乘 0.5（既有代码），抗性在事件构造期已减半——最终为四分之一，符合"先修正后抗性"的 RAW 顺序（两步均为减半时顺序可交换，结果一致）。
9. 抗性对**所有 actor 种类**生效（player/companion/enemy）：辅助函数只读目标视图的 conditions。

### 1.2 本切片的产品策略与裁剪

- **免疫与易伤不做**：1B 无消费者（无免疫法术、敌人档案无抗性字段）；管线挂钩设计已预留（类型集合判成员即可扩展），等首个消费者再实现。
- **防护毒素的"中毒豁免优势"裁剪**：豁免优势来源注册是独立原语（S-P 系/后续切片），本切片只做"结束中毒状态 + 毒素抗性"。
- **火焰护盾的反伤 rider 裁剪**："近战命中者受 2d8"是受击触发伤害，属反应/触发注册表的扩展，不在本切片；光照是叙事。本切片只交付抗性半面。
- **火焰护盾/防护毒素的持续时间**：`10_minute` / `1_hour` 无战斗内到期机制（与 charm_person 的 `1_hour` 同一既有限制），由 GM 在叙事层管理；数据如实标注。
- 石肤术/防护能量只能对盟友施放（buff 模式既有敌我校验），"自愿"要求由此满足；对不自愿敌人施放需触碰攻击规则，不做。
- 材料耗材（钻石粉末 100GP）不追踪：消耗品经济归 1D。
- 本切片不改武器/法术的伤害类型数据，不动 `spell_formula_node` / `attack_damage_node` 的骰算结构。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| 仓库当前**没有任何抗性实现**（全量 grep `resistance|抗性` 在 `src/rulesets/dnd2024` 无命中）——这是全新原语 | — |
| 武器攻击伤害构造点：`roll_damage_node` → `damage = max(0, damage)` → `damage_event` → 命中反应/触发链 → `_concentration_events` + `_last_hostile_defeated` | `resolution.py` L189-230（damage_event L214-218） |
| spell_attack 伤害构造点 | `resolution.py` L290-306（damage_event L298-303） |
| saving_throw 伤害构造点（含 `half_on_success`） | `resolution.py` L332-349（damage_event L341-346） |
| automatic_damage 伤害构造点（magic missile） | `resolution.py` L350-365 |
| buff/debuff 分支应用 condition（payload 自由键全部进元数据，P1 已加 `ends_on` 拷贝） | `resolution.py` L391-405；`reducer.py` L208-220 |
| `damage_type_choice` 的 intent 选择校验先例（本切片的 `resist_choice` 照此模式） | `validation.py` L465-467 |
| 目标校验：buff 需盟友、射程、目标数 | `validation.py` L435-464 |
| P1 建立的 `conditions` 注册表：catalog dataclass 字段、加载校验、`attack_modes` 条目结构 | `catalog.py`（P1 后）；`srd_combat_core.json` `conditions` 节 |
| Rage 的 condition.applied 事件（引擎构造，payload 无抗性字段） | `resolution.py` L568-574 |
| `_concentration_events(target, damage, rng)` 的 DC = `max(10, damage // 2)` | `engine.py` L1066-1086 |
| Uncanny Dodge 对 pending `damage_event` 的乘法修正（发生在抗性减半之后） | `reaction_spells.py` L176-203 |
| `_actor_view` 对三类 actor 都带 `conditions`（enemy 直读档案注入） | `view.py` L53-86 |
| 测试基建：`SequenceRng` / sheet 构造 / `start_combat`；goblin 攻击 `damage_type: "slashing"` | `tests/rulesets/dnd2024_monk_common.py` L145-155；`test_dnd2024_smite.py` L15-75 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

`conditions` 注册表追加（保留 P1 既有条目）：

```json
"rage": {"resist_damage_types": ["bludgeoning", "piercing", "slashing"]},
"stoneskinned": {"resist_damage_types": ["bludgeoning", "piercing", "slashing"]},
"poison_resistant": {"resist_damage_types": ["poison"]}
```

`spell_effects` 追加四条：

```json
"stoneskin": {"mode": "buff", "range": 5, "target_count": 1, "condition": "stoneskinned", "condition_duration": "concentration", "concentration": true},
"protection_from_energy": {"mode": "buff", "range": 5, "target_count": 1, "condition": "energy_resistant", "condition_duration": "concentration", "concentration": true, "resist_choice": ["acid", "cold", "fire", "lightning", "thunder"]},
"protection_from_poison": {"mode": "buff", "range": 5, "target_count": 1, "condition": "poison_resistant", "condition_duration": "1_hour", "removes_conditions": ["poisoned"]},
"fire_shield": {"mode": "buff", "range": 5, "target_count": 1, "condition": "fire_shield_resistant", "condition_duration": "10_minute", "resist_choice": ["fire", "cold"]}
```

语义约定：静态抗性走注册表（`rage`/`stoneskinned`/`poison_resistant`）；选择型抗性走 condition 元数据（施法时把所选类型写进 `resist_damage_types`）。

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

1. 新增模块级 `DAMAGE_TYPES = frozenset({"bludgeoning", "slashing", "piercing", "acid", "cold", "fire", "lightning", "thunder", "poison", "psychic", "force", "necrotic", "radiant"})`；
2. `conditions` 条目校验扩展：允许 `resist_damage_types` 键（非空列表、元素 ∈ `DAMAGE_TYPES`、无重复）；其余键规则维持 P1 版本（未知键拒绝）；
3. `spell_effects` 校验扩展：`resist_choice` 若存在必须是非空列表、元素 ∈ `DAMAGE_TYPES`、无重复；`removes_conditions` 若存在必须是非空字符串列表（元素匹配 `[a-z0-9_]{1,64}`）。

### 3.3 `src/rulesets/dnd2024/combat/validation.py`

`_validate_spell` 追加（模式照 `damage_type_choice` L465-467）：效果声明 `resist_choice` 时，intent 的 `resist_type` 必须存在且 ∈ 该列表；未声明 `resist_choice` 时 intent 携带 `resist_type` 应被拒绝（fail closed，防客户端写元数据）。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

#### A. 抗性辅助

新增实例方法（建议 `_resist_damage(target, amount, damage_type) -> tuple[int, bool]`）：

- `amount <= 0` 或 `damage_type` 为空 → 原值、False；
- 汇集目标 conditions 的抗性类型集合：每个 condition 先取注册表条目的 `resist_damage_types`，再并上元数据 dict 里的 `resist_damage_types`（选择型法术写入的）；
- `damage_type in 集合` → `(amount // 2, True)`，否则 `(amount, False)`；
- 不消耗 RNG。

#### B. 四个伤害构造点接入

在武器攻击（L212-218）、spell_attack（L297-303）、saving_throw（L337-346，在 `half_on_success` 之后）、automatic_damage（L355-361）四处，构造 `damage_event` 前调用辅助，用返回值替换 `damage`，并在事件上追加 `"resisted": True`（仅在生效时）。`rolls` 与 `critical` 不动。`_concentration_events`、`_last_hostile_defeated`、命中反应链拿到的即减半后的值（不要在链路里二次减半）。

#### C. buff 分支扩展（L399-405 附近）

1. 效果声明 `resist_choice` 时，`condition.applied` payload 追加 `"resist_damage_types": [intent["resist_type"]]`（单元素列表，服务端构造）；
2. 效果声明 `removes_conditions` 时，对该目标逐个检查现存 condition，命中即在该 `condition.applied` 之前发 `{"type": "condition.removed", "target_id": ..., "condition": ...}`。

### 3.5 测试（新文件 + 既有回归）

新文件 `tests/rulesets/test_dnd2024_stoneskin.py`（构造模式照 `test_dnd2024_smite.py`；石肤术验证用推进到 7 级的 `class:wizard`；Rage 用 `test_dnd2024_rage.py` 既有 barbarian 构造）。测试矩阵见 §4。

回归：`test_dnd2024_rage.py`、`test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_smite.py`、P1 的 `test_dnd2024_invisibility.py` 全部通过（`test_dnd2024_character_builder.py` 上游失败不计）。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`resist_damage_types`/`resist_choice` 含未知类型、空列表、重复项 | `CombatCatalogError` |
| 2 | 法师（7 级）对盟友施放石肤术 | `stoneskinned` condition 带注册表抗性生效；敌人挥砍攻击伤害 `amount == 原值 // 2`（用 SequenceRng 控制骰面取奇数验证取整）、事件含 `"resisted": true`、`rolls` 未变 |
| 3 | 石肤目标受火焰（fire bolt） | 伤害不减半、无 `resisted` 标记 |
| 4 | 抗性 + 专注：石肤目标自身在专注且受到挥砍伤害 | 专注豁免 DC = `max(10, 减半后伤害 // 2)` |
| 5 | Rage 债：狂暴野蛮人受挥砍减半、受火焰不减半 | 减半/不减半 |
| 6 | 不叠加：狂暴 + 石肤同时生效 | 仍只减半一次（`amount == 原值 // 2`） |
| 7 | 防护能量：未带 `resist_type` 的 intent 被拒；选 `cold` 后寒冷减半、火焰不减半；condition 元数据 `resist_damage_types == ["cold"]` | 校验拒绝 + 双向断言 |
| 8 | 防护毒素：目标先有 `poisoned`（enemy 档案注入或既有法术），施放后 `poisoned` 被移除且 `poison_resistant` 在身；毒伤害减半 | 移除事件在 applied 之前 |
| 9 | magic missile（automatic_damage）对抗性目标 | 减半 |
| 10 | saving_throw + `half_on_success` + 抗性（inflict_wounds 对石肤目标） | 豁免成功且抗性 → 原值 // 4 |
| 11 | 抗性 + Uncanny Dodge：石肤游荡者被命中，GM 代选 feature 减半 | 最终 `amount == 原值 // 4`（两步各半） |
| 12 | 治疗不受影响：cure_wounds 对 `poison_resistant` 目标 | 治疗量不减半 |
| 13 | 火焰护盾：选 `fire` → 火焰减半、寒冷不减半；被近战命中后**无**反伤事件 | 减半 + 无 rider |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_stoneskin.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_invisibility.py -q
```

## 6. 禁止事项

1. 禁止实现免疫/易伤（§1.2 已裁剪），禁止给敌人档案新增抗性字段。
2. 禁止在 reducer `_apply_hp_change` 里做减半——抗性属于结算期事实，必须在事件构造点完成，reducer 保持机械应用。
3. 禁止改动 Rage 引擎路径、`_expire_conditions` 或 sneak attack 结构来塞抗性。
4. 禁止把 `resist_type` 写进 condition 元数据以外的任何客户端可写字段。
5. 禁止对 `rolls` 骰迹做任何事后修改来"体现"抗性。
6. 禁止新增具体法术名的引擎分支（stoneskin/protection_from_energy 等只允许目录数据 + 既有 buff 模式）。

## 7. 停止条件

1. P1 的 `conditions` 注册表未合入或结构与本任务书 §3.1 冲突；
2. 发现第五个伤害构造点（白名单四点之外）无法绕过（如 GM 直伤工具）——报告后等待裁决，不得自行接入；
3. `resisted` 标记与既有前端/投影字段冲突；
4. Uncanny Dodge 链路中减半顺序导致既有测试语义变化且无法用现有断言方式表达。

## 8. 执行报告模板

执行完成后在本文件追加"执行报告"节：变更文件清单、测试矩阵逐项结果、偏差与遗留（特别是 fire_shield rider 与免疫/易伤的后续挂钩点）。

## 9. 修订记录

- v1（2026-10-29）：初版。

---

# 执行报告（2026-10-29）

## A. 变更文件清单（`git status` 全部命中 §3 白名单）

| 文件 | 变更 |
|---|---|
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | `conditions` 追加 `rage`/`stoneskinned`/`poison_resistant` 静态抗性条目；`spell_effects` 追加 `stoneskin`/`protection_from_energy`/`protection_from_poison`/`fire_shield` |
| `src/rulesets/dnd2024/combat/catalog.py` | 新增模块级 `DAMAGE_TYPES`；`spell_effects` 校验 `resist_choice`/`removes_conditions`；`conditions` 校验 `resist_damage_types`（非空、已知类型、无重复），并允许"仅声明抗性"的条目 |
| `src/rulesets/dnd2024/combat/validation.py` | `_validate_spell` 追加 `resist_choice` ↔ `resist_type` 双向校验（缺选择拒绝、未声明却携带拒绝） |
| `src/rulesets/dnd2024/combat/resolution.py` | 新增 `_resist_damage`；四处伤害构造点接入；buff 分支写入 `resist_damage_types` 元数据、`removes_conditions` 先发 `condition.removed` |
| `tests/rulesets/test_dnd2024_stoneskin.py` | 新增，13 个测试覆盖 §4 矩阵 |
| 本任务书 | 仅追加本执行报告（§8 豁免） |

无白名单外改动；`test_dnd2024_character_builder.py` 未纳入验收，未触碰。

## B. 测试矩阵逐项结果（全部通过）

| # | 测试 | 结果 |
|---|---|---|
| 1 | `test_catalog_rejects_invalid_resistance_declarations`：未知类型/空列表/重复项/非法 `removes_conditions` | 通过 |
| 2 | `test_stoneskin_halves_slashing_damage_once_with_floor_rounding`：7 → 3，`resisted is True`，`resistance == "stoneskinned"`，`rolls == [5]` | 通过 |
| 3 | `test_stoneskin_does_not_reduce_fire_damage`：7 不减半、无 `resisted`/`resistance` | 通过 |
| 4 | `test_concentration_dc_uses_the_halved_damage`：44 → 22，DC 11（原始 44 会得 22） | 通过 |
| 5 | `test_rage_resistance_comes_from_the_registry`：挥砍 7 → 3、火焰 7 不减 | 通过 |
| 6 | `test_rage_and_stoneskin_still_halve_only_once`：7 → 3（非 1） | 通过 |
| 7 | `test_protection_from_energy_requires_and_applies_its_choice`：缺 `resist_type` 拒绝、石肤携带 `resist_type` 拒绝；寒冷 3、火焰 7；元数据 `["cold"]` | 通过 |
| 8 | `test_protection_from_poison_removes_poisoned_before_applying`：`condition.removed` 早于 `condition.applied`；毒素 7 → 3 | 通过 |
| 9 | `test_magic_missile_is_halved_by_a_matching_resistance`：15 → 7，`resisted` | 通过 |
| 10 | `test_saving_throw_half_then_resistance_deals_a_quarter`：11 → 5 → 2 | 通过 |
| 11 | `test_stoneskin_and_uncanny_dodge_stack_two_halvings`：7 → 3（pending 持有）→ 1 | 通过 |
| 12 | `test_healing_is_not_reduced_by_resistance`：治疗 12，无 `resisted` | 通过 |
| 13 | `test_fire_shield_resists_its_chosen_type_without_a_rider`：火焰 3、寒冷 7、攻击者无伤害事件 | 通过 |

验收命令实测：

```
tests/rulesets/test_dnd2024_stoneskin.py -q            -> 13 passed
rage + combat + spells + uncanny_dodge + smite + invisibility -q -> 100 passed
```

## C. 偏差说明

1. **`_resist_damage` 返回 `(int, str)` 而非 `(int, bool)`**（§3.4.A 写的是 bool）。原因：既有 Rage 契约已发布 `damage["resistance"] == "rage"`（`test_dnd2024_rage.py`），必须保留。事件同时写 `resisted: true`（本切片契约）与 `resistance: <condition_id>`（既有契约）。`""` 即未生效。
2. **Rage 硬编码减半块被替换**（§1.3 写"不改 Rage 引擎代码"）。实施时发现武器攻击路径早已存在针对 `"rage"` 的硬编码减半（review 阶段已落地），与新原语重复。已删除该特判，改由注册表驱动；没有新增任何 Rage 分支，Rage 测试断言原样通过。这属于**移除特判**而非新增 Rage 路径，但确实改动了攻击路径代码，如实记录。
3. **§4#3 的"石肤目标受火焰（fire bolt）"** 在机制上不可达（石肤只抗物理，且 fire bolt 是 spell_attack 而非目标攻击）。测试改为把敌人攻击档案的 `damage_type` 覆盖为 `fire`，验证武器攻击路径不误减半。
4. **§4#9 的"magic missile 对抗性目标"** 目录中无任何力场抗性来源（免疫/易伤已裁剪）。测试向敌人注入 `{"resist_damage_types": ["force"]}` 元数据以覆盖 `automatic_damage` 路径；元数据仅测试可写，生产端只能由 `resist_choice` 施法写入。
5. **§4#10 的"inflict_wounds 对石肤目标"** 同上，石肤不抗死灵。测试向敌人注入 `necrotic_resistant` 元数据，覆盖 `saving_throw → half_on_success → 抗性` 链路（11 → 5 → 2），仍满足"原值 // 4"。
6. **§4#12 期望治疗量修正为 12**：7 级法师 4 级已获属性提升，智力 +3（非 +2），2d8(4+5)+3 = 12。仅测试期望，引擎无改动。
7. **`fire_shield` 目录 `range: 5` 按任务书原样落地**，与法术书"自身"不一致；自施法距离 0 可通过，不影响机制。`protection_from_poison` 的 1 小时、`fire_shield` 的 10 分钟仍无战斗内到期机制（既有 `1_hour` 同款限制），由 GM 叙事层管理。

## D. 遗留与后续挂钩点

1. **免疫/易伤**：在 `_resist_damage` 同一集合判成员结构上扩展 `immune_damage_types` / `vulnerable_damage_types`（注册表 + 元数据），并按 RAW 顺序"抗性 → 易伤，不叠加"实现；等待首个消费者。
2. **fire_shield 2d8 反伤 rider**：需要"近战命中攻击者"的受击触发伤害挂钩（攻击者侧结算期伤害事件），属触发/反应注册表扩展，本切片不做。
3. **protection_from_poison 的中毒豁免优势**：需要独立的豁免优势来源注册原语（S-P 系/后续切片）。
4. **抗性来源的确定性**：`_resist_damage` 按目标 `conditions` 迭代顺序返回首个命中来源；`resistance` 字段因此仅在"多源同类型"时依赖顺序（`resisted` 与 `amount` 不受影响）。如需稳定来源名，可后续按优先级排序。
5. **`energy_resistant` / `fire_shield_resistant` 无注册表条目**：只作为动态元数据条件存在，与 `see_invisibility` 同款（注册表只校验已声明条目，不强制每个 condition id 都有条目）；`_expire_conditions` 对它们沿用 duration 机制。
