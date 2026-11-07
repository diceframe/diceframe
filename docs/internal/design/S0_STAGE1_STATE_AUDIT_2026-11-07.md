# S0 前置状态审计：12 职业 / 12 子职第一阶段的真实完成度

| 项 | 值 |
|---|---|
| 审计日期 | 2026-11-07 |
| 审计对象 | `feat/dnd2024-s0c-draconic-resilience@d9873aad`（含 S0-A/A2、S0-B、S0-C 与 S0-B 夹具修正 `2499e95b`） |
| 冻结标尺 | `S0F37_COVERAGE_AUDIT_TASK_SPEC_2026-11-07.md` §3.1：140 职业行为行 + 58 子职行 = 198 |
| 规则基准 | `SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 审计方式 | 只读：读 canonical JSON + 在真实运行时上取 `features_for` / `resource_definitions` / `combat_capabilities` / `derive_armor_class`；未改任何产品代码 |
| 结论 | 结构无漂移；行为完成度 26/198（13.1%）；7 类合计 15 行由共享通道兜住；157 行只有排期名。发现 7 项偏差，其中 2 项直接产生错误数值 |

## 1. 结论摘要

第一阶段交出的是一套**通用规则管线**（伤害、暴击阈值、触发窗口、动作经济、被动派生、资源池、骰面最大化、护甲门控、武器精通词条），以及把其中一部分**接到了具体职业特性上**。

- `S0F37` 的 198 行结构基线**完全吻合**：140 + 58，逐职业逐子职数量与冻结值一致，没有内容漂移。
- 但 198 行里只有 **26 行**有机械定义（13.1%），另有 **4 行**通过共享声明借用、**3 行**靠专长通道、**7 行**靠法术位通道实际可用；剩余 **157 行**只有排期表里的一个名字。
- 12 个职业里，`druid` 和 `ranger` 在 `srd_class_features.json` 中**连 bucket 都没有**；20 级时能拥有的特性数：fighter 10、monk 6、rogue 5、barbarian 4、paladin 4、ranger 2、warlock 2、bard/cleric/sorcerer/wizard 各 1、druid 0。
- 两项**直接算错**的偏差：`unarmored_defense` 未接线（野蛮人 AC 12 应为 15，武僧 13 应为 15）；移动速度只取种族值（野蛮人 5 级仍 30ft）。

## 2. 结构对账：无漂移

| 口径 | 冻结值 | 实测 | 判定 |
|---|---:|---:|---|
| 职业行为行（排除 4 族结构项） | 140 | 140 | 一致 |
| 子职行 | 58 | 58 | 一致 |
| 合计 | 198 | 198 | 一致 |

逐职业：barbarian 16、bard 9、cleric 8、druid 10、fighter 12、monk 19、paladin 14、ranger 14、rogue 15、sorcerer 7、warlock 9、wizard 7。

逐子职：Berserker 4、Lore 4、Life 5、Land 5、Champion 6、Open Hand 4、Devotion 5、Hunter 5、Thief 5、Draconic 5、Fiend 5、Evoker 5。

**结论：`srd_classes.json` 与 `srd_subclasses.json` 的排期表本身是完整的、与 SRD 对得上的。问题不在"少了哪一行"，而在"行背后的行为"。**

## 3. 行为覆盖：三层证据

### 3.1 汇总

| 层 | 职业行 | 子职行 | 合计 |
|---|---:|---:|---:|
| 有机械定义（`srd_class_features.json` 中有 `parameters` / `capabilities` / `resource_id` / `choice`） | 13 | 13 | **26** |
| 有动作能力声明（`capabilities`） | 5 | 0 | 5 |
| 有资源绑定（`resource_id`） | 5 | 2 | 7 |
| 有 en/zh 标签 | 22 | 13 | 35 |
| 有测试文件提及 | 36 | 14 | 50 |

`S0F37` §3.1 的 `Monk's Focus` 三个子能力（`flurry_of_blows` / `patient_defense` / `step_of_the_wind`）不在 198 行内，另有 3 条定义，故定义总数 = 26 + 3 = 29。

### 3.2 26 条有定义的行，逐条测试证据

24 行有直接测试；`two_extra_attacks` / `three_extra_attacks` 由 `test_dnd2024_extra_attack.py` 的等级参数化覆盖（`(1,1)/(5,2)/(11,3)/(20,4)`）。**这 26 行的数值经运行时抽查全部正确**：

| 检查点 | 实测 | SRD |
|---|---|---|
| Rage 伤害 1/9/16/20 级 | +2 / +3 / +4 / +4 | +2 / +3 / +4 |
| Sneak Attack 骰 1/5/11/20 | 1d6 / 3d6 / 6d6 / 10d6 | 同 |
| Martial Arts 骰 1/5/11/17 | 1d6 / 1d8 / 1d10 / 1d12 | 同 |
| Cutting Words 骰 3/5/10/15 | d6 / d8 / d10 / d12 | 同 |
| Champion 暴击阈值 2/3/14/15 | 21 / 19 / 19 / 18 | 20-20 / 19-20 / 18-20 |
| Uncanny Dodge 伤害倍率 | 0.5 | 减半 |
| 攻击次数 fighter 1/5/11/20 | 1 / 2 / 3 / 4 | 同 |
| 攻击次数 barbarian/monk/paladin/ranger | 1 / 2 / 2 / 2 | 5 级 2 |

### 3.3 动作能力面只覆盖 4 个职业

全库 `capabilities` 声明共 **12 条**，分布在 6 行：

| 特性 | 能力 |
|---|---|
| `rage` | `rage`、`rage_sustain` |
| `second_wind` | `second_wind` |
| `action_surge` | `action_surge` |
| `martial_arts` | `bonus_unarmed_strike` |
| `patient_defense` | `patient_defense`、`patient_defense_focus` |
| `step_of_the_wind` | `step_of_the_wind`、`step_of_the_wind_focus` |
| `flurry_of_blows` | `flurry_of_blows` |
| `cunning_action` | `cunning_dash`、`cunning_disengage`、`cunning_hide` |

Cleric 的 Channel Divinity、Druid 的 Wild Shape、Paladin 的 Lay on Hands / Sacred Weapon、Bard 的 Bardic Inspiration、Sorcerer 的 Metamagic / Innate Sorcery、Warlock 的 Invocation、Ranger 的全部——**没有任何一条是可在战斗里声明的能力**。

## 4. 偏差清单（按影响排序）

### D1 · 严重 · `unarmored_defense` 未接线，野蛮人与武僧 AC 算错

- 事实：`unarmored_defense` 只出现在 `content/classes/{barbarian,monk}.json` 与排期表里，`srd_class_features.json` **没有这条定义**，因此 `resolver.unarmored_ac_bonus_abilities()` 对两者都返回 `()`。
- 后果（运行时实测）：

| 角色 | 属性 | 实测 AC | SRD AC |
|---|---|---:|---:|
| Barbarian 1 | Dex 14 / Con 16 | **12** | 15（10+2+3） |
| Monk 1 | Dex 16 / Wis 14 | **13** | 15（10+3+2） |

- 性质：不是"没做"，而是**通用公式已经存在、数据没接上**——`derive_armor_class()` 已经支持 `unarmored_ability_bonuses` 候选取最大，Draconic Resilience 走的就是这条路，只有这两个 1 级核心特性漏了声明。
- 修法：在 `srd_class_features.json` 增 `barbarian.unarmored_defense`（`unarmored_ac_ability_bonus: "con"`）与 `monk.unarmored_defense`（`"wis"`）。属于内容声明，不需要改代码。

### D2 · 严重 · 移动速度只取种族值，三项速度加值全部未接

- 事实：`derivation.py` 里速度只有一个来源——`species["speed"]`；`fast_movement`（Barbarian 5，+10）、`unarmored_movement`（Monk 2/6/10/14/18，+10…+30）、`roving`（Ranger 6，+5、+10）**没有任何消费点**。
- 后果：野蛮人 5 级、武僧 2 级、游侠 6 级的速度仍等于种族基础值，战斗中的移动预算因此偏小。
- 性质：这是"排期有名字、管线没入口"，需要新增一条速度加值派生（与 AC 同构）。

### D3 · 中 · 11 个职业资源池"存得进、投不出"

`rest_catalogs/srd_recovery.json` 声明了每职业的资源池，`Dnd2024RestEngine.sync_resources()` 也确实把它们写进 `ruleset_character.resources.class`（最大值从 track 算得正确）。但 `resolver.resource_definitions()` 只枚举**有 `resource_id` 的特性**，于是下面这些池永远进不了角色卡、战斗投影和 UI：

| 职业 | 池 | 存储 | 投影 |
|---|---|---|---|
| Barbarian | `rages` | 有 | 有 |
| Bard | `bardic_inspiration` | 有 | **无** |
| Cleric | `channel_divinity` | 有 | **无** |
| Druid | `wild_shape` | 有 | **无** |
| Fighter | `second_wind` / `action_surge` | 有 | 有 |
| Fighter | `indomitable` | 有 | **无** |
| Monk | `focus_points` | 有 | 有 |
| Monk | `uncanny_metabolism` | 有 | **无** |
| Paladin | `lay_on_hands` / `channel_divinity` | 有 | **无** |
| Paladin | `paladins_smite` | 有 | 有 |
| Ranger | `favored_enemy` | 有 | **无** |
| Sorcerer | `sorcery_points` / `innate_sorcery` | 有 | **无** |
| Warlock | `magical_cunning` | 有 | **无** |
| Wizard | `arcane_recovery` | 有 | **无** |
| Warlock | `dark_ones_own_luck` | 有 | 有（需 `fiend_patron`） |

计 11 个池受影响。其中 `bardic_inspiration` 更微妙：它唯一的绑定来自子职特性 `cutting_words`，所以**没有选 College of Lore 的诗人，1 级就拿不到诗人激励**——这是把职业资源挂在子职特性上的错位。

### D4 · 中 · 163/198 行没有任何本地化标签

标签文件只有 50 个键，其中 35 个命中 198 行；剩下 163 行在 zh-CN 下走 `_humanize()` 回退，界面会直接显示 `Improved Brutal Strike` 这类英文。

### D5 · 轻 · 内容元数据三处不一致

1. `srd_class_features.json` 的 `classes` 只有 10 个键，**缺 `druid` 与 `ranger`**——这两个职业在特性目录里是彻底的空白，不是"属性没填"。
2. 该文件的 catalog 级 `source_ref` 写的是 `srd-5.2.1:p49-p52:monk-features`，但它实际含 10 个职业（p28–p82），页码范围与内容不符。
3. 同一文件 `automation_level: "deterministic"`，而 `srd_classes.json` 是 `"guided"`；实际只覆盖 26/198 行，`deterministic` 这个声明明显高于事实。

### D6 · 中 · 7 个施法职业的 `spellcasting` 不是被拥有的特性

`features_for(bard/cleric/druid/paladin/ranger/sorcerer/wizard, 1)` 返回空——`spellcasting` 在排期表里，但没有定义，`_key_for_grant()` 找不到唯一同名声明就丢弃。施法本身还能用，因为法术位走另一条独立通道（`slot_profiles` + `spell_catalog` + `class_entity.spellcasting_ability`），但这意味着：

- `has_feature(character, "spellcasting")` 恒为 `false`；
- 角色卡上没有这一行，也没有它的标签；
- 任何"拥有施法特性"的判定（如专长前提、揭示、UI 过滤）都不能基于特性身份，只能另打补丁。

### D7 · 记录更正 · 11 条 `character_builder` 失败的真正原因

此前被记为"环境问题"。实测根因是 Windows 保留设备名：`tests/rulesets/test_dnd2024_character_builder.py` 的 `runtime` 夹具按 id 写 `content/abilities/con.json`，而 `CON` 在 Windows 上是控制台设备名——`Path.write_text()` 不报错、`exists()` 返回 `True`、文件大小 `0`，但目录里根本没有这个文件。于是 `ability:con` 从未注册，`background:soldier` 的 `ability_refs` 解析失败。

- 全量 1314 例：**1303 passed / 11 failed**，11 条全部是这一个夹具引起的。
- 真实规则集不受影响，因为文件被命名为 `constitution.json`（实体 id 仍是 `con`）——这是一个能规避 `CON` 的巧合，不是约定。
- 判定：**产品实现无缺陷**，但"环境问题"这个结论不准确；修法是把夹具文件名改成 `ability_{id}.json` 或 `con_ability.json`。

## 5. 缺口清单：157 行只有名字

按 owner 排列（`无定义/总行数`）：

| 职业 | 缺口 | 具体 |
|---|---|---|
| Barbarian | 15/16 | unarmored_defense, danger_sense, reckless_attack, primal_knowledge, extra_attack¹, fast_movement, feral_instinct, instinctive_pounce, brutal_strike, relentless_rage, improved_brutal_strike, persistent_rage, indomitable_might, primal_champion, weapon_mastery² |
| Bard | 9/9 | bardic_inspiration, spellcasting³, expertise, jack_of_all_trades, font_of_inspiration, countercharm, magical_secrets, superior_inspiration, words_of_creation |
| Cleric | 8/8 | spellcasting³, divine_order, channel_divinity, sear_undead, blessed_strikes, divine_intervention, improved_blessed_strikes, greater_divine_intervention |
| Druid | 10/10 | spellcasting³, druidic, primal_order, wild_shape, wild_companion, wild_resurgence, elemental_fury, improved_elemental_fury, beast_spells, archdruid |
| Fighter | 6/12 | fighting_style⁴, tactical_mind, tactical_shift, indomitable, tactical_master, studied_attacks |
| Monk | 17/19 | unarmored_defense, unarmored_movement, uncanny_metabolism, deflect_attacks, slow_fall, extra_attack¹, stunning_strike, empowered_strikes, evasion, acrobatic_movement, heightened_focus, self_restoration, deflect_energy, disciplined_survivor, perfect_focus, superior_defense, body_and_mind |
| Paladin | 13/14 | lay_on_hands, spellcasting³, weapon_mastery², fighting_style⁴, channel_divinity, extra_attack¹, faithful_steed, aura_of_protection, abjure_foes, aura_of_courage, radiant_strikes, restoring_touch, aura_expansion |
| Ranger | 14/14 | spellcasting³, favored_enemy, weapon_mastery², deft_explorer, fighting_style⁴, extra_attack¹, roving, expertise, tireless, relentless_hunter, natures_veil, precise_hunter, feral_senses, foe_slayer |
| Rogue | 12/15 | expertise, thieves_cant, weapon_mastery², steady_aim, cunning_strike, evasion, reliable_talent, improved_cunning_strike, devious_strikes, slippery_mind, elusive, stroke_of_luck |
| Sorcerer | 7/7 | spellcasting³, innate_sorcery, font_of_magic, metamagic, sorcerous_restoration, sorcery_incarnate, arcane_apotheosis |
| Warlock | 9/9 | eldritch_invocations, pact_magic, magical_cunning, contact_patron, mystic_arcanum_6/7/8/9, eldritch_master |
| Wizard | 7/7 | spellcasting³, ritual_adept, arcane_recovery, scholar, memorize_spell, spell_mastery, signature_spells |

¹ 借 Fighter 的共享 `extra_attack` 声明，实测正确。² 借 Fighter 的共享 `weapon_mastery` 声明，实测可用。³ 走 `slot_profiles` 通道，施法可用但无特性身份（D6）。⁴ 走专长通道，1 级实际能选到风格专长，但 `fighting_style` 这一行本身没有被拥有。

子职侧缺口 45/58，唯一有 5 条定义满覆盖的是 Champion（缺 `remarkable_athlete`、`heroic_warrior`）。整条子职链未动的有：Circle of the Land（5/5）、Warrior of the Open Hand（4/4）、Hunter（5/5）。

## 6. 与 S0 任务书的对应关系

按这次审计，43 份"待执行"任务书的真实状态分三类：

**A. 代码已存在，应按 S0-C 的方式做契约审计**（6 份）

| 任务 | 载体 | 状态 |
|---|---|---|
| S0-D1 | `supreme_healing` | 有定义、`test_dnd2024_die_manipulation.py` 覆盖 |
| S0-D2 | `overchannel` | 有定义（4 个 cost 参数）、`die_manipulation.py` 覆盖 |
| S0-D3 | `cutting_words` | 有定义、`check_windows` 体系覆盖 |
| S0-E1 | `dark_ones_blessing` | 有定义、`test_dnd2024_dark_ones_blessing.py` 专测 |
| S0-E2 | `aura_of_devotion` | 有定义、`test_dnd2024_aura_devotion.py` 专测；但 `aura_of_protection` / `aura_expansion` / `aura_of_courage` 未做 |
| S0-E3 | `survivor` | 有定义、`test_dnd2024_survivor.py` 专测 |

**B. 部分已做，只差补齐子项**（5 份）

| 任务 | 已有 | 缺 |
|---|---|---|
| S0-F7 | 5 职业 `weapon_mastery` 轨道 + 4 个词条（`graze`/`sap`/`vex`/`slow`） | `cleave`/`nick`/`push`/`topple` 在 `WEAPON_MASTERY_EFFECTS` 中显式为 `{"kind": "unimplemented"}`；Tactical Master 未做 |
| S0-F8 | `additional_fighting_style` 选项 + 4 种风格专长（Archery/Defense/Great Weapon Fighting/Two-Weapon Fighting） | Blessed Warrior、Druidic Warrior |
| S0-F10 | `cunning_action` 三能力、`fast_hands`、Monk's Focus 三选项 | Steady Aim、Fleet Step、Heightened Focus、Instinctive Pounce、Tactical Shift |
| S0-F11 | `retaliation` | Deflect Attacks/Energy、Slow Fall |
| S0-F22 | `paladins_smite` 资源 | Channel Divinity、Lay on Hands、Disciple of Life、Preserve Life 等 |

**C. 完全未开始**（约 32 份）：F1–F6、F9、F12–F21、F23–F36、F37。

其中 D1（无甲防御）落在 S0-F9，D2（速度）也落在 S0-F9——**S0-F9 是当前唯一产生错误数值的未完成任务，优先级应最高**。

## 7. 建议的下一步

1. **先修 D1、D2**：两者都是"管线已有、声明没接"，改动量小、影响面是 12 个职业里的 3 个。D1 只加两条 JSON 声明；D2 需要在 `derivation.py` 加一条速度加值派生（可复用 AC 的候选模式）。
2. **再修 D3 的投影枚举**：让 `resource_definitions()` 以 `rest_catalog.class_resources` 为来源，而不是反过来以特性为来源；顺手把 `bardic_inspiration` 从 `cutting_words` 上解绑。
3. **补 D4 标签**：163 个 id 的 en/zh 标签，机械可做。
4. **更正 D5 元数据**：补 druid/ranger bucket 是新增内容（属于 S0-F 系列的范围），`source_ref` 与 `automation_level` 可立即修正。
5. **D7**：改夹具文件名，让 11 条红变绿，把"环境失败"这个含糊结论清掉。
6. 完成上述后，再按 A→B→C 的顺序推进 43 份任务书；A 类 6 份应像 S0-C 一样先做契约审计，不要从零重写。

## 8. 复现方式

审计用两个只读脚本在临时工作树上执行（未提交）：

- `.audit_stage1_v2.py`：从 `progression_catalogs/srd_classes.json`、`subclass_catalogs/srd_subclasses.json`、`class_feature_catalogs/srd_class_features.json`、`rest_catalogs/srd_recovery.json` 与两个 locale 文件生成 198 行矩阵，逐行标注 `definition / params / capabilities / resource_id / choice / label / src 提及 / 测试提及`。
- 运行时探针：`Dnd2024ClassFeatureResolver.unarmored_ac_bonus_abilities`、`resource_definitions`、`combat_capabilities`、`feature_value`、`attacks_per_action`、`derive_armor_class`，以及 `Dnd2024RestEngine.sync_resources`。

判定边界：本次"无实现"的结论只对**运行时可见行为**负责。凡是仅存在于排期表、无法被 `features_for` 返回的 `local_id`，一律记为未实现——这正是 `S0F37` §1.4 要求的口径（"有 catalog 行不等于行为完成"）。
