# S0-F14 任务书：命中 Rider 与职业选项升级

| 项 | 值 |
|---|---|
| 切片编号 | S0-F14（独立新任务，不覆盖旧 SP） |
| 验收载体 | `stunning_strike`、`empowered_strikes`、`radiant_strikes`、`open_hand_technique`、`blessed_strikes`、`improved_blessed_strikes`、`elemental_fury`、`improved_elemental_fury` |
| 通用能力 | 命中后选择、每回合预算、条件 rider、职业选项继承与真实升级 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-B、S0-F7 已合入；选项选择和伤害类型/抗性边界可用 |
| 建议分支 | `feat/dnd2024-s0f14-hit-riders` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从 S0-B/F7 实际集成提交开工；不得另建平行选项状态或绕过伤害抗性。
2. 八项 canonical 特性及各自子效果完整实现，升级必须改变骰数、射程或临时 HP 等真实机制。
3. 命中、额外骰、Focus、豁免、条件和临时 HP 均由服务端决定；客户端只能选择当前合法的 rider/类型/目标。
4. 不把 Cleric 与 Druid 同名 Potent Spellcasting 合并成跨职业加值，不把两个升级变成第二次独立选择。

## 2. 官方英文关键原文与页码

SRD p51–52，Monk/Open Hand：

> **Stunning Strike.** Once per turn when you hit a creature with a Monk weapon or an Unarmed Strike, you can expend 1 Focus Point to attempt a stunning strike. The target must make a Constitution saving throw. On a failed save, the target has the Stunned condition until the start of your next turn. On a successful save, the target’s Speed is halved until the start of your next turn, and the next attack roll made against the target before then has Advantage.

> **Empowered Strikes.** Whenever you deal damage with your Unarmed Strike, it can deal your choice of Force damage or its normal damage type.

> **Open Hand Technique.** Whenever you hit a creature with an attack granted by your Flurry of Blows, you can impose one of the following effects on that target.

> **Addle.** The target can’t make Opportunity Attacks until the start of its next turn. **Push.** The target must succeed on a Strength saving throw or be pushed up to 15 feet away from you. **Topple.** The target must succeed on a Dexterity saving throw or have the Prone condition.

SRD p55、p38：

> **Radiant Strikes.** When you hit a target with an attack roll using a Melee weapon or an Unarmed Strike, the target takes an extra 1d8 Radiant damage.

> **Blessed Strikes.** You gain one of the following options of your choice. **Divine Strike.** Once on each of your turns when you hit a creature with an attack roll using a weapon, you can cause the target to take an extra 1d8 Necrotic or Radiant damage (your choice). **Potent Spellcasting.** Add your Wisdom modifier to the damage you deal with any Cleric cantrip.

> **Improved Blessed Strikes.** The option you chose for Blessed Strikes grows more powerful. **Divine Strike.** The extra damage of your Divine Strike increases to 2d8. **Potent Spellcasting.** When you cast a Cleric cantrip and deal damage to a creature with it, you can give vitality to yourself or another creature within 60 feet of yourself, granting a number of Temporary Hit Points equal to twice your Wisdom modifier.

SRD p43：

> **Elemental Fury.** You gain one of the following options of your choice. **Potent Spellcasting.** Add your Wisdom modifier to the damage you deal with any Druid cantrip. **Primal Strike.** Once on each of your turns when you hit a creature with an attack roll using a weapon or a Beast form’s attack in Wild Shape, you can cause the target to take an extra 1d8 Cold, Fire, Lightning, or Thunder damage (choose when you hit).

> **Improved Elemental Fury.** **Potent Spellcasting.** When you cast a Druid cantrip with a range of 10 feet or greater, the spell’s range increases by 300 feet. **Primal Strike.** The extra damage of your Primal Strike increases to 2d8.

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| 攻击命中、伤害和 Sneak/Smite 等 rider 已有结算入口 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| 每回合 feature 使用可按 actor/round/turn/feature 记录 | `combat/resolution.py::_feature_used_this_turn`、`combat/reducer.py::_reduce_event` |
| Focus、Martial Arts die、攻击档案来自 actor/feature 投影 | `combat/view.py::_player_view`、`_available_attacks` |
| Flurry 的攻击由 capability 生成，须保留“由 Flurry 授予”的来源 | `combat/resolution.py::_class_capability_events` |
| 法术伤害、射程、豁免和效果由 spell/effect catalog 结算 | `combat/resolution.py::_spell_events`、`combat/validation.py::_validate_spell` |
| 法术 catalog 有 level/range/school/class 等元数据 | `spells/catalog.py::Dnd2024SpellCatalog.get` |
| 条件、HP、临时 HP 由事件/reducer 落账 | `combat/reducer.py::_reduce_event`、`_apply_temp_hp_change` |
| `option_of` 已表达声明归属，但实际持久化选择依赖 S0-B | `features/models.py::ClassFeatureDefinition`、前置执行结果 |
| 抗性/伤害类型边界依赖 S0-F7 | `combat/resolution.py::_resist_damage`、前置执行结果 |

## 4. 精确实现契约

### 4.1 声明、选择与通用 rider 顺序

1. 增加八项声明、正确 owner/等级/source_ref/双语 locale；`improved_*` 读取 S0-B 已保存的同一选项，不开放第二个独立选项。
2. 可选 rider 必须在命中后、相应伤害最终提交前通过服务端 pending choice 或等价决策；decline 不支付可选 Focus/每回合预算，也不阻塞基础伤害。
3. 额外伤害事件记录基础攻击、feature_id、owner、类型、骰和每回合使用；抗性只由 S0-F7 对最终各伤害实例应用一次。
4. 攻击 rider 的伤害骰属于该次攻击时，暴击按既有攻击伤害规则 doubling；法术 Potent 的固定属性加值不变成骰、不因暴击翻倍。
5. 同一命中可合法共存的不同 rider 不因共用 feature.used 键互相误删；同一 rider 的每回合限制则必须在窗口创建与最终 resolve 重验。

### 4.2 Monk 与 Paladin

1. **Stunning Strike**：每回合一次，仅 Monk weapon/Unarmed Strike 命中后可选，支付 1 Focus；目标做 Con save。失败 Stunned 至自己下回合开始；成功则 Speed 减半至同一时点，且下一次对其攻击有 Advantage，首次使用即消费。
2. **Empowered Strikes**：每次 Unarmed Strike 造成伤害时可保留原类型或改 Force；不提供其他类型，不修改武器攻击，不在未造成伤害时选类型。
3. **Radiant Strikes**：每次以 Melee weapon 或 Unarmed Strike 的攻击掷骰命中均额外 1d8 Radiant；无每回合限制，不适用于远程武器或法术攻击。
4. **Open Hand Technique**：仅 Flurry 授予的每次攻击命中可选且至多选一项 Addle/Push/Topple；普通徒手攻击不能冒充 Flurry。
5. Addle 禁止目标 OA 至目标下回合开始；Push 为 Str save 失败后推离至多 15 尺；Topple 为 Dex save 失败后 Prone。豁免使用 Monk 特性 DC，不用客户端 DC 或施法 DC。
6. 同一 Flurry 的不同命中可分别选不同效果；同一命中不得叠加两项，未命中不选、不消耗效果机会。

### 4.3 Cleric 与 Druid 选项升级

1. **Divine Strike**：每个自己的回合一次，weapon attack roll 命中可选 Necrotic/Radiant；7–13 级 1d8，14 级起 2d8。Unarmed Strike 不因 Cleric 选项自动算作 weapon。
2. **Cleric Potent Spellcasting**：只对真实 Cleric cantrip 的伤害加当前 Wisdom modifier；其他职业戏法、非戏法和无伤害戏法不加。
3. 14 级起，施放 Cleric cantrip 且实际对生物造成伤害后，可选自己或 60 尺内另一生物获得 `2 × Wisdom modifier` 临时 HP；复用临时 HP 替换而非相加规则，非正值按既有合法边界处理并记录。
4. **Primal Strike**：每个自己的回合一次，weapon 或 Wild Shape Beast form 攻击命中可选 Cold/Fire/Lightning/Thunder；7–14 级 1d8，15 级起 2d8。
5. **Druid Potent Spellcasting**：只对真实 Druid cantrip 伤害加 Wisdom modifier；15 级起仅当该 cantrip 原射程至少 10 尺时增加 300 尺，Self、Touch、5 尺不增加。
6. 增程必须同时进入可用目标、服务端射程验证和结算；只改显示或只改 catalog 文案不算完成。职业归属以实际法术来源判定，不能仅按角色当前职业猜。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/resolution.py`、`validation.py`、`reducer.py`、`check_modes.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`、`features/models.py`：仅必要声明参数与选项查询。
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要条件/效果声明。
5. 新建 `tests/rulesets/test_dnd2024_hit_riders.py`；本任务书仅追加执行报告。
6. 不改 S0-B/F7 的选择/抗性算法、法术数据全集、Wild Shape 系统或前端；接口不足时停止。

## 6. 编号测试矩阵

以真实 Cleric/Druid/Monk/Paladin/Open Hand 角色、真实选项和完整攻击/施法链验收；SequenceRng 固定骰序，禁止伪造命中、选择或 rider 伤害。

1. 八项特性在正确等级/子职授予，升级读取同一 S0-B 选项，错误职业/选项不串用。
2. Stunning Strike 每回合最多一次，仅 Monk weapon/Unarmed 命中可选，支付 1 Focus；未中和非法武器不触发。
3. Stunning 失败 Stunned 至自己下回合开始；成功不 Stunned，但 Speed 减半且下一次对其攻击 Advantage 被消费。
4. Focus 不足、同回合第二次、decline 或伪造目标均不生效，基础命中伤害仍正确结算。
5. Empowered Strikes 的 Unarmed 可选原类型或 Force；其他类型/武器攻击拒绝，抗性按最终类型计算。
6. Radiant Strikes 对每次合法 Melee weapon/Unarmed 命中加 1d8 Radiant，同回合第二次仍加；远程/法术不加。
7. Open Hand 仅 Flurry 命中开放选择，每个命中至多一项；普通徒手与未命中不触发。
8. Addle 只抑制目标 OA 至其下回合开始；Push Str 失败推至多 15 尺；Topple Dex 失败 Prone，成功均无对应效果。
9. 三次 Flurry 命中可分别选择，事件含正确 Flurry 来源与 Monk DC，不能单命中叠三项。
10. Divine Strike 每回合一次、weapon attack 限定、类型二选一；14 级由 1d8 真实变 2d8。
11. Cleric Potent 只对 Cleric cantrip 伤害加一次 Wis；Druid/其他来源戏法、非伤害戏法和非戏法不加。
12. 14 级 Cleric cantrip 实际伤害后可给自己或 60 尺内另一生物 `2×Wis` 临时 HP；未伤害或超距不开放。
13. 临时 HP 替换规则和数值正确，不叠加；升级不是只改 summary。
14. Primal Strike 每回合一次，weapon/合法 Beast form 攻击限定，四类型选择准确；15 级变 2d8。
15. Druid Potent 只对 Druid cantrip 加 Wis；Cleric 同名选项不泄露到 Druid。
16. 15 级 Druid 原射程至少 10 尺的 cantrip 增程 300；Self/Touch/5 尺不增，验证和可用目标同步。
17. 额外攻击伤害骰在暴击时按统一攻击规则处理；固定 Wis 加值不伪装成骰或错误翻倍。
18. 多个合法 rider 共存时顺序、伤害实例、抗性、每回合键和事件 provenance 正确，不相互吞并。
19. 可选 rider 的 pending save/load、decline、过期 version 和重复 resolve 均安全，不重复扣资源或伤害。
20. 所有新状态 apply/replay 后一致，RNG 顺序可解释，条件在正确时点到期。
21. 非持有者与低一级角色无对应 rider/升级，客户端伪造 feature_id、选项、类型或射程无效。
22. 既有 Monk、Sneak Attack、Smite、法术、临时 HP、条件与 S0-B/F7 回归测试通过。

## 7. 非目标与停止条件

### 非目标

- 其他职业命中 rider、所有 Wild Shape 形态、完整 OA/位移地图或法术系统重构。
- 新增第三套 Potent Spellcasting 通用加成、让升级重新选项、前端动效。
- 用文案中的 2d8/+300/临时 HP 代替真实机械验收。

### 停止条件

1. S0-B/F7 未合入，或无法取得同一职业选项与真实法术来源。
2. Flurry 攻击缺少可追踪授予来源，需要按武器名或攻击顺序猜测。
3. 当前 spell range 模型无法让增程同时进入验证与可用目标。
4. 位移/Prone/Stunned/OA 的必要权威语义缺失，且白名单不能闭合。
5. 需要白名单外文件、客户端骰值或状态迁移才能通过核心测试。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_hit_riders.py -q
python -m pytest tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features tests/rulesets/test_dnd2024_hit_riders.py
git diff --check
```

## 9. 交付要求

1. 测试 1–22 逐项映射，分别展示每回合、每 Flurry 命中、每次命中三种频率。
2. 对两组选项各提供 1d8→2d8 或法术升级的实际行为证据，而非 diff 文案。
3. 报告 rider 顺序、暴击、抗性、临时 HP、存档重放和 RNG。
4. 偏差/停止项不得包装为部分完成；所有八项范围必须可追踪。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Monk/Paladin rider：
- Cleric 选项与升级：
- Druid 选项与升级：
- 决策、资源、条件与伤害顺序：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放、暴击与 RNG：
- 偏差、停止原因与未决事项：
