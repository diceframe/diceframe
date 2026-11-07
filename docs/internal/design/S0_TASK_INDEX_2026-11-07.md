# S0 任务索引：SRD 职业与子职落地

| 项 | 值 |
|---|---|
| 范围 | 官方 SRD 5.2.1 的 12 个职业、12 个子职及其特性 |
| 规则基准 | 官方英文 PDF，2025-05-01，CC-BY-4.0；SHA-256 见 `SRD_SOURCE_AUDIT_2026-11-06.md` |
| 代码祖先基线 | `integration/dnd2024-srd@35653f81`；后续切片按依赖链依次叠加，不跳基线 |
| 状态 | S0-A 已实现（分支 `feat/dnd2024-s0a-subclass-critical@37eeafe0`，待评审白名单偏差）；其余 45 份待执行 |
| 唯一执行入口 | 本索引列出的 `S0*.md` 文件；旧 `SP1`–`SP12` 不再作为 S0 执行依据 |

## 1. 为什么单独立 S0 任务体系

阶段一的 `SP1`–`SP12` 是共用原语的**历史任务书**，记录的是当时已经实现并合入的切片，不是当前待执行队列。继续在原文件上覆盖会同时破坏两件事：

- 历史记录被改写，无法再解释阶段一为什么形成这些原语；
- 执行模型会把“已实现的历史任务”和“当前待做的 S0 切片”混为同一份契约。

因此：

1. 旧 `SP*.md` **保持原样**，只作为阶段一历史与背景；
2. S0 每份任务书使用独立文件名与独立状态；
3. 执行时只读取本索引指向的 S0 文件，不读取旧 SP 任务书扩张范围；
4. 旧 SP 中仍然有效的代码事实可以核对，但其任务范围、验收口径和已过时假设不能继承。

## 2. 执行顺序与依赖

```text
S0-A 子职授予主干
  ├─→ S0-B 特性选项主干
  │     └─→ S0-C 被动角色推导
  └─→ S0-D1 骰面最大化
        └─→ S0-D2 Overchannel 使用计数
  └─→ S0-D3 D20 掷后窗口

S0-A 子职授予主干
  └─→ S0-E1 击败触发
        └─→ S0-E2 光环与持续效果
              └─→ S0-E3 回合再生与存活类被动
```

硬依赖：

- **S0-B** 依赖 S0-A 的 `(owner_ref, local_id)` 身份与子职授予；
- **S0-C** 依赖 S0-A 的子职授予，并依赖 S0-B 的选项持久化边界；
- **S0-D1** 依赖 S0-A 的子职授予；
- **S0-D2** 依赖 S0-A 与 S0-D1 的骰节点最大化；
- **S0-D3** 依赖 S0-A 的子职授予与战斗 actor 子职投影；
- **S0-E1** 依赖 S0-A 的子职授予与战斗 actor 子职投影，并可与 D 系列分别施工。
- **S0-E2** 依赖 S0-A 与 S0-E1；四个 saving throw 消费面统一接入光环，供 S0-E3 的 Survivor 复用。
- **S0-E3** 依赖 S0-E2 的死亡豁免光环合成，并修正旧草案首回合不再生的错误；包含 combat.start 与 turn.advanced 两个入口。

### 2.1 F 系列直接前置

下表只列直接前置，传递前置不重复展开；`A`–`E3` 分别指对应 S0 任务。执行时必须从所有直接前置实际合入后的集成提交开工，不能仅因编号靠后就假定接口已经存在。

| 任务 | 直接前置 | 任务 | 直接前置 |
|---|---|---|---|
| F1 | B、D3 | F20 | F1、E3 |
| F2 | B、D2、D3、F1 | F21 | D2、F12、E3 |
| F3 | F1、F2、D2、E2 | F22 | D1、D2、F4、F13 |
| F4 | B | F23 | F13、F14、F22、E2 |
| F5 | B、D2、F4 | F24 | C、F1、F3 |
| F6 | D2、F5 | F25 | F6、F20 |
| F7 | B | F26 | F4、F12、F16、F25 |
| F8 | B、F7 | F27 | F5、F20、F25 |
| F9 | C | F28 | F2、F3、F6、F16、B、D2 |
| F10 | F1、F9 | F29 | F4、F5、D2 |
| F11 | A | F30 | F14、F20、F21、F23、F28、D2 |
| F12 | D2、E2 | F31 | F1、F4、F5 |
| F13 | E2 | F32 | B、D1、F1、F14、F16、F18 |
| F14 | B、F7 | F33 | F4、F5、F32 |
| F15 | F14 | F34 | F6、F14、F32 |
| F16 | C、D1、D2、F14 | F35 | F4、F5、F12、F27、F31、F32 |
| F17 | D2、F10、F14 | F36 | B、F1、F4 |
| F18 | F5 | F37 | A–E3、F1–F36 全部 |
| F19 | B、F11、F12、F14、F18 |  |  |

依赖图不能替代任务书中的停止条件。F10 的 Hide 环境事实、F16 的 AoE 成员、F25 的 Beast 数据、F26 的区域/掩蔽、F30 的位面/光照、F31 的真实 Spell Scroll，以及 F32–F35 的感官/形态等边界，仍须在执行时逐项核实；缺口只能形成明确的 `guided`／`not_automated` 非完成记录或停止，不能用近似模型冒充验收。

## 3. 当前任务清单

| 编号 | 垂直切片 | 验收载体 | 任务书 | 测试 | 状态 |
|---|---|---|---|---|---|
| S0-A | 子职授予主干＋暴击阈值 | Champion 3/15 级 Improved / Superior Critical | `S0A_SUBCLASS_GRANT_IMPROVED_CRITICAL_TASK_SPEC_2026-11-07.md` | 23 | 已实现，待评审 |
| S0-B | 特性选项主干 | Champion 7 级 Additional Fighting Style → Defense | `S0B_FEATURE_CHOICES_TASK_SPEC_2026-11-07.md` | 26 | 待执行 |
| S0-C | 被动角色推导 | Draconic Sorcery 3 级 Draconic Resilience | `S0C_PASSIVE_DERIVATION_DRACONIC_RESILIENCE_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-D1 | 骰面最大化 | Life Domain 17 级 Supreme Healing | `S0D1_SUPREME_HEALING_TASK_SPEC_2026-11-07.md` | 18 | 待执行 |
| S0-D2 | 递增使用计数 | Evoker 14 级 Overchannel | `S0D2_OVERCHANNEL_TASK_SPEC_2026-11-07.md` | 28 | 待执行 |
| S0-D3 | D20 掷后反应窗口 | College of Lore 3 级 Cutting Words 攻击检定面 | `S0D3_CUTTING_WORDS_CHECK_WINDOW_TASK_SPEC_2026-11-07.md` | 30 | 待执行 |
| S0-E1 | 敌人归零触发＋临时 HP | Fiend Patron 3 级 Dark One's Blessing | `S0E1_DEFEAT_TRIGGER_DARK_ONES_BLESSING_TASK_SPEC_2026-11-07.md` | 30 | 待执行 |
| S0-E2 | 光环、豁免加值与条件免疫 | Paladin 6/18 级 Aura of Protection / Expansion；Devotion 7 级 Aura of Devotion | `S0E2_PALADIN_AURAS_TASK_SPEC_2026-11-07.md` | 33 | 待执行 |
| S0-E3 | 首回合/回合再生与死亡豁免 | Champion 18 级 Survivor | `S0E3_SURVIVOR_TASK_SPEC_2026-11-07.md` | 33 | 待执行 |
| S0-F1 | D20 技能、专精与下限 | Expertise、Jack of All Trades、Reliable Talent、Remarkable Athlete、Scholar | `S0F1_D20_SKILLS_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F2 | 激励骰实例与掷后修正 | Bardic Inspiration、Font/Superior Inspiration、Peerless Skill、Cutting Words | `S0F2_BARDIC_INSPIRATION_TASK_SPEC_2026-11-07.md` | 25 | 待执行 |
| S0-F3 | 失败 D20 后窗口 | Tactical Mind、Dark One's Own Luck、Countercharm、Indomitable、Disciplined Survivor、Stroke of Luck | `S0F3_FAILED_D20_WINDOWS_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F4 | 来源化法术授予 | 五组子职法术、Circle Spells、Magical Discoveries、Magical Secrets | `S0F4_SPELL_GRANTS_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F5 | 独立免费施法资源 | Contact Patron、Mystic Arcanum 6/7/8/9、Spell Mastery、Signature Spells | `S0F5_FREE_CASTING_TASK_SPEC_2026-11-07.md` | 25 | 待执行 |
| S0-F6 | 休息恢复预算与转换 | Arcane/Natural Recovery、Sorcerous Restoration、Magical Cunning、Eldritch Master、Font of Magic | `S0F6_REST_RESOURCE_CONVERSION_TASK_SPEC_2026-11-07.md` | 25 | 待执行 |
| S0-F7 | 武器精通与八词条 | 五职业 Weapon Mastery、Tactical Master；Cleave/Graze/Nick/Push/Sap/Slow/Topple/Vex | `S0F7_WEAPON_MASTERY_TASK_SPEC_2026-11-07.md` | 28 | 待执行 |
| S0-F8 | 战斗风格与戏法选项 | Fighter/Paladin/Ranger Fighting Style；六种风格及 Blessed/Druidic Warrior | `S0F8_FIGHTING_STYLES_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F9 | 无甲防御与特殊移动 | Unarmored Defense/Movement、Fast Movement、Roving、Second-Story Work、Acrobatic Movement | `S0F9_DERIVED_DEFENSE_MOVEMENT_TASK_SPEC_2026-11-07.md` | 26 | 待执行 |
| S0-F10 | Bonus Action 路由与移动效果 | Instinctive Pounce、Tactical Shift、Cunning Action、Steady Aim、Fast Hands、Fleet Step、Heightened Focus | `S0F10_ACTION_ROUTING_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F11 | 受击与伤害反应 | Retaliation、Deflect Attacks、Deflect Energy、Slow Fall | `S0F11_DAMAGE_REACTIONS_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F12 | 豁免减伤、抗性与归零存活 | Danger Sense、Monk/Rogue Evasion、Fiendish Resilience、Relentless Rage | `S0F12_SAVE_DAMAGE_SURVIVAL_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F13 | 条件免疫、抑制与移除 | Mindless Rage、Self-Restoration、Aura of Courage | `S0F13_CONDITION_IMMUNITY_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F14 | 命中 rider 与选项升级 | Stunning/Empowered/Radiant Strikes、Open Hand、Blessed/Elemental Fury 系列 | `S0F14_HIT_RIDERS_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F15 | 攻击前声明与连续状态 | Reckless Attack、Frenzy、Brutal Strike、Improved Brutal Strike、Studied Attacks | `S0F15_ATTACK_STATE_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F16 | 法术修饰与 AoE 保护 | Potent Cantrip、Sculpt Spells、Empowered Evocation、Elemental Affinity | `S0F16_SPELL_MODIFIERS_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F17 | Cunning Strike 骰池预算 | Cunning/Improved Cunning/Devious Strikes、Supreme Sneak、Elusive；六个选项 | `S0F17_CUNNING_STRIKE_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F18 | Hunter's Mark 与 Ranger 高等级链 | Favored Enemy、Relentless/Precise Hunter、Feral Senses、Foe Slayer、Hunter's Mark | `S0F18_HUNTERS_MARK_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F19 | Hunter 子职完整战斗链 | Hunter's Lore/Prey、Defensive Tactics、Superior Hunter's Prey/Defense | `S0F19_HUNTER_SUBCLASS_TASK_SPEC_2026-11-07.md` | 26 | 待执行 |
| S0-F20 | 战斗开始、先攻与额外首回合 | combat.start、initiative、turn.start 与六项开场特性 | `S0F20_COMBAT_START_TASK_SPEC_2026-11-07.md` | 28 | 待执行 |
| S0-F21 | 休息限制的主动自我增益 | Tireless、Nature's Veil、Wholeness of Body、Superior Defense | `S0F21_ACTIVE_BUFFS_TASK_SPEC_2026-11-07.md` | 26 | 待执行 |
| S0-F22 | Channel Divinity 与治疗链 | Channel Divinity、Lay on Hands、Disciple of Life、Preserve Life、Blessed Healer、Restoring Touch | `S0F22_CHANNEL_HEALING_TASK_SPEC_2026-11-07.md` | 28 | 待执行 |
| S0-F23 | 武器增益、控制与 Smite 光环 | Sacred Weapon、Smite of Protection、Abjure Foes、Sear Undead | `S0F23_CHANNEL_CONTROL_TASK_SPEC_2026-11-07.md` | 26 | 待执行 |
| S0-F24 | 属性、熟练与总值替换被动 | Primal Champion、Body and Mind、Slippery Mind、Indomitable Might | `S0F24_PASSIVE_CAPSTONE_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F25 | Wild Shape 全生命周期 | Wild Shape、Wild Resurgence、Beast Spells、Archdruid 与真实 Beast 数据 | `S0F25_WILD_SHAPE_TASK_SPEC_2026-11-07.md` | 28 | 待执行 |
| S0-F26 | Land 主动区域与抗性 | Land's Aid、Nature's Ward、Nature's Sanctuary | `S0F26_LAND_ACTIVE_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F27 | 真实召唤物生命周期 | Faithful Steed、Wild Companion、Dragon Companion 及对应召唤物 | `S0F27_SUMMONS_TASK_SPEC_2026-11-07.md` | 30 | 待执行 |
| S0-F28 | Sorcerer 高阶能力与超魔 | Innate Sorcery、Metamagic、Sorcery Incarnate、Arcane Apotheosis；十个选项 | `S0F28_METAMAGIC_TASK_SPEC_2026-11-07.md` | 20 | 待执行 |
| S0-F29 | 神术干预与创造真言 | Divine Intervention、Greater Divine Intervention、Words of Creation | `S0F29_DIVINE_WORDS_TASK_SPEC_2026-11-07.md` | 14 | 待执行 |
| S0-F30 | 跨日、位面与长时状态 | Quivering Palm、Hurl Through Hell、Intimidating Presence、Dragon Wings、Holy Nimbus | `S0F30_LONG_EFFECTS_TASK_SPEC_2026-11-07.md` | 18 | 待执行 |
| S0-F31 | 魔法物品、法术书与抄录 | Use Magic Device、Ritual Adept、Memorize Spell、Evocation Savant | `S0F31_SPELLBOOK_ITEMS_TASK_SPEC_2026-11-07.md` | 14 | 待执行 |
| S0-F32 | Invocation 主干与核心选项 | 多选/替换/依赖闭包及八个核心 Eldritch Invocations | `S0F32_INVOCATION_CORE_TASK_SPEC_2026-11-07.md` | 14 | 待执行 |
| S0-F33 | 九个施法型 Invocations | Armor of Shadows 至 Whispers of the Grave 九项逐项验收 | `S0F33_INVOCATION_CASTING_TASK_SPEC_2026-11-07.md` | 15 | 待执行 |
| S0-F34 | Blade pact 与进阶选项 | Pact of the Blade、Thirsting/Devouring Blade、Eldritch Smite、Lifedrinker | `S0F34_INVOCATION_BLADE_TASK_SPEC_2026-11-07.md` | 15 | 待执行 |
| S0-F35 | Chain/Tome 与相关选项 | Pact of the Chain/Tome、Investment、Gift of the Protectors、Gaze、Depths | `S0F35_INVOCATION_PACTS_TASK_SPEC_2026-11-07.md` | 18 | 待执行 |
| S0-F36 | 语言、熟练与常备授予 | Primal Knowledge、Divine/Primal Order、Druidic、Deft Explorer、Thieves' Cant、Bonus Proficiencies | `S0F36_SIMPLE_GRANTS_TASK_SPEC_2026-11-07.md` | 15 | 待执行 |
| S0-F37 | 最终机器覆盖审计 | 动态 198 行主矩阵、48 行成长项、3/28/10/6 专项矩阵与真实 JUnit 证据 | `S0F37_COVERAGE_AUDIT_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |

## 4. 每份任务书的固定结构

执行侧可假定每份 S0 任务书至少包含：

1. 范围锁定、代码基线与前置依赖；
2. 官方 SRD 5.2.1 原文与准确页码；
3. 已核实的代码现状，不把猜测写成事实；
4. 精确行为契约与数据字段；
5. 改动文件白名单；
6. 编号测试矩阵；
7. 明确非目标；
8. 停止条件；
9. 聚焦与回归验收命令；
10. 执行报告模板。

缺少上述任一项，不应交给执行模型。

## 5. 统一执行纪律

- 从任务书指定的前置提交新建干净工作树，记录实际提交号；不要猜提交号，不要复用已有施工工作树。
- 一个切片只交付一个垂直链路；不借机补齐整个职业、整个子职或通用 DSL。
- 只有白名单内文件可改；白名单外必须停止并报告，不得“顺手修”。
- 测试必须逐项对应矩阵编号；不得用“全量通过”替代逐项证据。
- 任何规则语义冲突以官方英文 SRD 原文为准；不引用 2014 SRD、open5e 旧版或第三方译文作为规则依据。
- SRD 之外的职业、子职、专长、法术和魔法物品不进入 S0。

## 6. S0 完成定义

S0 不以“198 个授予身份都有 JSON”为完成。至少满足：

1. 每个特性有唯一 `owner_ref`，不会跨职业串用同名短 id；
2. 职业特性只由职业等级授予，子职特性只由实际选择的子职授予；
3. 玩家选项有明确声明、合法集合、持久化、替换与历史重建；
4. 被动值、资源、攻击、法术、反应和 rest 都由真实运行时消费，而不是只显示文案；
5. 无法自动结算的规则必须显式标记为 `guided`／`not_automated`，有负例且不开放假入口；它们不计为 deterministic complete；
6. 英文与中文、升级、存档兼容、reducer 重放和回滚均有测试；
7. 最终按 12 职业＋12 子职动态审计 140 个职业行为特性＋58 个子职特性＝198 行，并另审 48 个通用成长/结构条目；
8. Monk's Focus 3 项、Eldritch Invocations 28 项、Metamagic 10 项、Cunning Strike 6 项分别形成专项矩阵，不重复计入 198，也不能由父特性一行掩盖子项缺口。
