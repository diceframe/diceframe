# S0 任务索引：SRD 职业与子职落地

| 项 | 值 |
|---|---|
| 范围 | 官方 SRD 5.2.1 的 12 个职业、12 个子职及其特性 |
| 规则基准 | 官方英文 PDF，2025-05-01，CC-BY-4.0；SHA-256 见 `SRD_SOURCE_AUDIT_2026-11-06.md` |
| 代码祖先基线 | `integration/dnd2024-srd@35653f81`；后续切片按依赖链依次叠加，不跳基线 |
| 状态 | 已 rebase 到 `main@10a6ab23`（release 3.1.0，含 `#687`/`#689` D3b、`#688` 拥有过滤、`#692` CoC 轨）。S0-A 已实现（含 A2 迁移，分支 `feat/dnd2024-s0a-subclass-critical-upstream@7a28b25a`）；S0-B 已实现（分支 `feat/dnd2024-s0b-feature-choices@1304beb7`）；S0-C 已实现（分支 `feat/dnd2024-s0c-draconic-resilience@f6d3d8bb`）；D1–D4 修复在链头 `feat/dnd2024-derived-defense-speed@73d11263`；S0-D2 已实现（`feat/dnd2024-s0d2-overchannel@f775ae4f`）、S0-E1 已实现（`feat/dnd2024-s0e1-dark-ones-blessing@8f3182fd`）、S0-E2 已实现并复核（`feat/dnd2024-s0e2-paladin-auras`）；S0-F9 已交付可自动化部分（`feat/dnd2024-s0f9-movement`），环境面按停止条件记录；S0-D3 已实现（`feat/dnd2024-s0d3-cutting-words@fa254b1f`）；S0-D1 已实现（`feat/dnd2024-s0d1-supreme-healing@5283f77d`，stack 在 D3 链头上）；S0-E3 已实现（`feat/dnd2024-s0e3-survivor@c1fef294`，stack 在 D1 链头上）。A 类六份（D1–D3、E1–E3）至此全部交付。剩余 36 份：F1–F37 中除 F9 外 36 份待执行 |
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

- **S0-B** 依赖 S0-A 的 `(owner, local_id)` 身份与子职授予；
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
| S0-B | 特性选项主干 | Champion 7 级 Additional Fighting Style → Defense | `S0B_FEATURE_CHOICES_TASK_SPEC_2026-11-07.md` | 26 | 已实现，待评审 |
| S0-C | 被动角色推导 | Draconic Sorcery 3 级 Draconic Resilience | `S0C_PASSIVE_DERIVATION_DRACONIC_RESILIENCE_TASK_SPEC_2026-11-07.md` | 22 | 已实现，待评审 |
| S0-D1 | 骰面最大化 | Life Domain 17 级 Supreme Healing | `S0D1_SUPREME_HEALING_TASK_SPEC_2026-11-07.md` | 18 | **已实现**（`feat/dnd2024-s0d1-supreme-healing@5283f77d`）。治疗分支的 `has_feature` 补 `owner="subclass:life_domain"`；补齐矩阵 3/12/13/14 与 1/2/4/5/6/7/9/11 的缺失断言。矩阵 18/18 有证据。见该文件 §10 |
| S0-D2 | 递增使用计数 | Evoker 14 级 Overchannel | `S0D2_OVERCHANNEL_TASK_SPEC_2026-11-07.md` | 27 | 已实现，待评审（`feat/dnd2024-s0d2-overchannel`，stack 在 E1 分支 `d0db4810` 上）。首次免费 + `use_index × 环阶` 递增自伤、Wizard 列表门控、`features.usage` 长休计数与 `reset_feature_usage` 全部落地；intent/字段改名 `overchannel` 并同步前端。矩阵 25/26 部分覆盖、矩阵 5 的"5 环法术用 6 环槽"因无自动化 5 环法师伤害法术不可达，均记录在该文件 §10 |
| S0-D3 | D20 掷后反应窗口 | College of Lore 3 级 Cutting Words 攻击检定面 | `S0D3_CUTTING_WORDS_CHECK_WINDOW_TASK_SPEC_2026-11-07.md` | 30 | **已实现**（`feat/dnd2024-s0d3-cutting-words@fa254b1f`）。声明补 `parameters.trigger`/`range_ft` 并在加载期 fail closed；射程不再硬编码 60；多诗人按 actor id 串行、decision id 带 reactor；5 尺内升级重击不再被当成必中（`critical_upgrade`）。新增 38 条，矩阵 30/30 通过。两处形状偏差（`resource_id` 位置、`check_window` 未改 `d20_check_window`）与理由见该文件 §10 |
| S0-E1 | 敌人归零触发＋临时 HP | Fiend Patron 3 级 Dark One's Blessing | `S0E1_DEFEAT_TRIGGER_DARK_ONES_BLESSING_TASK_SPEC_2026-11-07.md` | 30 | 已实现，待评审（`feat/dnd2024-s0e1-dark-ones-blessing@8f3182fd`，stack 在 E2 分支上）。删掉 SRD 没有的 10 尺共享他杀（旧 `defeat_*` 键在加载期拒绝），载荷收敛为四键闭集，事件审计字段由 4 项补到 12 项；矩阵 21/22/23 因单职业不可达，按「已接线、未可达」记录在该文件 §10 |
| S0-E2 | 光环、豁免加值与条件免疫 | Paladin 6/18 级 Aura of Protection / Expansion；Devotion 7 级 Aura of Devotion | `S0E2_PALADIN_AURAS_TASK_SPEC_2026-11-07.md` | 33 | 已实现并通过一轮独立复核（`feat/dnd2024-s0e2-paladin-auras`）。复核修掉 2 个真缺陷（0 HP/死亡持有者仍投射光环、死亡豁免测试建立在不可达状态上），另 5 项契约/可观察性偏差见该文件 §10「审核修订」；3 项白名单外改动与测试 17/18/21/29 的取舍同处记录 |
| S0-E3 | 首回合/回合再生与死亡豁免 | Champion 18 级 Survivor | `S0E3_SURVIVOR_TASK_SPEC_2026-11-07.md` | 33 | **已实现**（`feat/dnd2024-s0e3-survivor@c1fef294`，stack 在 D1 链头上）。首回合改为在 `combat.started` 后再生；regen 逐项求值并带完整 provenance；到期条件用到期后视图；死亡豁免阈值键改 `death_save_crit_threshold`、来源带 owner。矩阵 19 通过 / 14 部分；其中 `death_save.resolved.roll` 因契约与白名单外既有测试冲突，待裁决（该文件 §10 偏差第 6 条） |
| S0-F1 | D20 技能、专精与下限 | Expertise、Jack of All Trades、Reliable Talent、Remarkable Athlete、Scholar | `S0F1_D20_SKILLS_TASK_SPEC_2026-11-07.md` | 22 | 待执行 |
| S0-F2 | 激励骰实例与掷后修正 | Bardic Inspiration、Font/Superior Inspiration、Peerless Skill、Cutting Words | `S0F2_BARDIC_INSPIRATION_TASK_SPEC_2026-11-07.md` | 25 | 待执行 |
| S0-F3 | 失败 D20 后窗口 | Tactical Mind、Dark One's Own Luck、Countercharm、Indomitable、Disciplined Survivor、Stroke of Luck | `S0F3_FAILED_D20_WINDOWS_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F4 | 来源化法术授予 | 五组子职法术、Circle Spells、Magical Discoveries、Magical Secrets | `S0F4_SPELL_GRANTS_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F5 | 独立免费施法资源 | Contact Patron、Mystic Arcanum 6/7/8/9、Spell Mastery、Signature Spells | `S0F5_FREE_CASTING_TASK_SPEC_2026-11-07.md` | 25 | 待执行 |
| S0-F6 | 休息恢复预算与转换 | Arcane/Natural Recovery、Sorcerous Restoration、Magical Cunning、Eldritch Master、Font of Magic | `S0F6_REST_RESOURCE_CONVERSION_TASK_SPEC_2026-11-07.md` | 25 | 待执行 |
| S0-F7 | 武器精通与八词条 | 五职业 Weapon Mastery、Tactical Master；Cleave/Graze/Nick/Push/Sap/Slow/Topple/Vex | `S0F7_WEAPON_MASTERY_TASK_SPEC_2026-11-07.md` | 28 | 待执行 |
| S0-F8 | 战斗风格与戏法选项 | Fighter/Paladin/Ranger Fighting Style；六种风格及 Blessed/Druidic Warrior | `S0F8_FIGHTING_STYLES_TASK_SPEC_2026-11-07.md` | 24 | 待执行 |
| S0-F9 | 无甲防御与特殊移动 | Unarmored Defense/Movement、Fast Movement、Roving、Second-Story Work、Acrobatic Movement | `S0F9_DERIVED_DEFENSE_MOVEMENT_TASK_SPEC_2026-11-07.md` | 26+46 | **部分交付**（`feat/dnd2024-s0f9-movement`）。矩阵 2–11 早在 `feat/dnd2024-derived-defense-speed@73d11263` 已实现（38 条）；本轮补 `roving` / `second_story_work` 声明、`derived.climb_speed`/`swim_speed`、跳跃距离权威（46 条）。矩阵 19–24 按 §7 停止条件 2 记录为不可自动化：combat 是 `position_mode: theater` 的一维距离模型，没有地形/垂直面/液面/支撑事实。详见该文件 §9 |
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

1. 每个特性有唯一 `owner`，不会跨职业串用同名短 id；
2. 职业特性只由职业等级授予，子职特性只由实际选择的子职授予；
3. 玩家选项有明确声明、合法集合、持久化、替换与历史重建；
4. 被动值、资源、攻击、法术、反应和 rest 都由真实运行时消费，而不是只显示文案；
5. 无法自动结算的规则必须显式标记为 `guided`／`not_automated`，有负例且不开放假入口；它们不计为 deterministic complete；
6. 英文与中文、升级、存档兼容、reducer 重放和回滚均有测试；
7. 最终按 12 职业＋12 子职动态审计 140 个职业行为特性＋58 个子职特性＝198 行，并另审 48 个通用成长/结构条目；
8. Monk's Focus 3 项、Eldritch Invocations 28 项、Metamagic 10 项、Cunning Strike 6 项分别形成专项矩阵，不重复计入 198，也不能由父特性一行掩盖子项缺口。

## 7. 第一阶段状态审计（2026-11-07）

在执行 S0-D1 之前先做了一次前置审计，结论见 `S0_STAGE1_STATE_AUDIT_2026-11-07.md`。要点：

- 198 行结构（140 + 58）与 §6 第 7 条的冻结口径**完全一致**，内容无漂移；
- 但只有 26 行（13.1%）有机械定义，157 行仅有排期名；`druid`、`ranger` 在 `srd_class_features.json` 中没有任何 bucket；
  - **2026-11-08 复核**：现为 35 行（17.7%）有机械定义（A/B/C/D2/E1/E2 与 F9 的落地）；`ranger` 已随 S0-F9 建 bucket（仅 `roving`），`druid` 仍无 bucket；
- 两项直接算错：`unarmored_defense` 未接线（野蛮人 AC 12 应为 15、武僧 13 应为 15），移动速度只取种族值（`fast_movement`/`unarmored_movement`/`roving` 无消费点），两者都落在 **S0-F9**；
  - **2026-11-08 复核：该条已失效**。S0-C 把 AC 候选与速度加值做成通用参数（`unarmored_ac_ability_bonus` / `speed_bonus_ft` / `speed_bonus_track`），`feat/dnd2024-derived-defense-speed@73d11263` 已接线并有 38 条测试；按特性 id 做字面搜索会误判为"无消费点"。真正缺的是 Roving / Second-Story Work 与跳跃，已由 `feat/dnd2024-s0f9-movement` 补齐；
- 11 个职业资源池声明并写入存档，但因投影只枚举 `feature.resource_id` 而无法出现在角色卡与战斗面（含 cleric/paladin 的 `channel_divinity`、druid 的 `wild_shape`、paladin 的 `lay_on_hands`、sorcerer 的 `sorcery_points`）；
- 163/198 行没有 en/zh 标签；
- `character_builder` 的 11 条长期红灯已定位为 Windows 保留设备名 `CON`（夹具写入 `con.json`），与规则实现无关。

**执行顺序调整**：S0-D1 至 S0-E3 的载体（`supreme_healing`、`overchannel`、`cutting_words`、`dark_ones_blessing`、`aura_of_devotion`、`survivor`）在上游已有定义与测试，应沿用 S0-C 的方式先做契约审计再定改动；S0-F9 因涉及错误数值，优先级上调到 D 系列之前。（2026-11-08：错误数值部分已在 `feat/dnd2024-derived-defense-speed` 修完，该优先级理由随之失效；剩余环境面受停止条件约束。）

## 8. A 类六份任务书的契约审计（2026-11-07）

完整报告：`S0_CONTRACT_AUDIT_2026-11-07.md`。审计对象 `feat/dnd2024-derived-defense-speed@340b003f`，只读核对，聚焦测试 134 passed。（该 commit 是 rebase 前的链头，现行链头为 `73d11263`；审计结论与内容不受 rebase 影响，见 §9。）

**一句话结论：六份载体全都有落点，没有一份需要从零实现；但六份里只有 D1 和 D3 的行为可以直接沿用，其余四份各自需要改规则或补整块机制。**

| 契约 | 行为落地 | 关键缺口 | 判定 |
|---|---|---|---|
| S0-D1 | 完整 | 查询缺 owner（§4.3.2）；矩阵 3/13/14 无测试 | **已修复**：见 §10 执行报告 |
| S0-D3 | 完整（超范围） | owner / `range_ft` / `trigger` 三处声明形状；已实现 Lucky 与伤害面 | **已修复**：见 §10 执行报告 |
| S0-E3 | 完整 | Heroic Rally **首回合不触发**，被 `test_combat_start_does_not_regenerate` 固化 | **已修复**：见 §10 执行报告 |
| S0-E1 | 完整 | **10 尺共享他杀**（`defeat_share_range_ft`）不属于 SRD p76；事件审计字段缺 8 项 | 删规则 + 改测试 |
| S0-D2 | 核心模型冲突 | 每次施法都自伤，无首次免费；`features.usage` 长休计数整套不存在 | 重做计数与自伤 |
| S0-E2 | 1/3 | **`aura_of_protection` / `aura_expansion` 在特性目录里无定义**；四个豁免面无 `aura_bonus` | 新做 2/3 |

必须优先修的五项（按是否产生错误数值排序）：

1. **E2-1/E2-2**：Paladin 6/18 级光环豁免加值完全不存在（progression 有名字、locale 有标签、feature catalog 无条目 → `features_for()` 永远拿不到）。
2. **E1-1**：队友在 10 尺内击杀时 Fiend Warlock 白拿 temp HP。契约 §1.4 与 SRD p76 都只有"你亲手归零"。
3. **D2-1**：Overchannel 每次施法都自伤。契约 §执行侧须知 4 明确废止"每次 2d12/环"这个模型。
4. **E3-1**：Heroic Rally 首回合不触发。契约 §1.5 原话是"本任务明确推翻"。
5. **D2-2**：`features.usage` 长休作用域计数缺失。注意 `combat.feature_usage` 是"每回合一次"账本，同名异义，**不能复用**。

**审计口径提醒**：本次判定以契约为准、代码为事实。代码里"有测试通过"不等于"符合契约"——E1-1 与 E3-1 两条恰恰是测试断言的正是契约废止的行为。后续修复不得为了让既有测试继续绿而保留错误规则。

**未覆盖**：`srd-5.2.1:p58` vs `p76`（E1-4）因本工作树无 SRD PDF 未复核，需在修复时用原文定稿；D5 元数据漂移不重复；全量 `tests/rulesets` 未复跑（沿用 D1–D4 提交时的 1341 passed / 11 failed 基线）。

## 9. 基线对齐：S0 链 rebase 到 `main@10a6ab23`（2026-11-07）

**动机**：原四个 S0 分支的 merge-base 都是 `8948bc3a`，而上游 main 已前进 37 个提交到 `10a6ab23`（release 3.1.0）。`8948bc3a` 含 D1–D4 物品原语（`ef4a5923`），但不含 D3b（`#687`/`#689`）与 `#688` 拥有过滤。S0 将来推上游前必须建立在含这些改动的基点上。

**结果**：七个提交线性 rebase 到 `10a6ab23`，四个分支指针同步移动。

| 分支 | 新头 | 旧头 |
|---|---|---|
| `feat/dnd2024-s0a-subclass-critical-upstream` | `7a28b25a` | `c1b865e7` |
| `feat/dnd2024-s0b-feature-choices` | `1304beb7` | `2499e95b` |
| `feat/dnd2024-s0c-draconic-resilience` | `f6d3d8bb` | `d9873aad` |
| `feat/dnd2024-derived-defense-speed` | `73d11263` | `340b003f` |

**冲突面**：只有一个文件——`features/resolver.py`，其余 30 个文件自动合并。

冲突性质是**叠加而非二选一**：

- `#688`（`aca01a34`，已合入 main）把拥有过滤抽成单一入口 `_owned_feature_definitions(class_id, level)`，让 `features_for` 与 `attacks_per_action` 共用；
- S0-A1（`0ee1cff6`）把授予键改成 owner 感知的 `_granted_feature_keys(class_id, level, character)`，并删除了 `#688` 引用的 `_granted_feature_ids` / `_subclass_feature_ids`。

解法：保留 `#688` 的单一入口结构，把它的 granted 计算换成 `_granted_feature_keys`，并给 `_owned_feature_definitions` 补 `character` 参数透传。

**第二处冲突（S0-A 校验 vs `#688` 测试）**：S0-A 的 `_validate_owner_grant` 要求 `minimum_level` 精确等于首次授予等级，而 `#688` 新增的 `test_a_grant_below_the_declared_minimum_level_grants_no_attack` 明确要求容忍第三方 bundle 的等级漂移（注释原话："A third-party bundle can do exactly this, so the sheet and the attack count must agree instead of drifting apart"）。同时 S0-B 的 `test_a_declared_level_must_match_the_subclass_grant` 又要求子职声明必须精确匹配。

裁决：**按 owner 类型分强度校验**。

- 子职声明（`subclass:*`）仍要求 `minimum_level` 等于子职表首次授予等级 —— 子职表是它唯一授予源，等级漂移会静默移动解锁点；
- 类声明（`class:*`）只要求"有授予"，允许等级漂移 —— `features_for` 的 `minimum_level > level` 过滤已能正确处理，硬抛错会把一处美观问题升级成整个 bundle 拒绝加载。

该修正已 squash 进 S0-A1（`00d5987d`），两个原本对立的测试现在共存。

**验证**：

| 项 | 结果 |
|---|---|
| 全量 `tests/rulesets` | **1366 passed / 11 failed**（同步 18m13s） |
| 11 条失败 | 全部在 `test_dnd2024_character_builder.py`，根因 `unresolved reference 'ability:con'`（Windows 保留设备名 `CON` 使 `content/abilities/con.json` 无法落盘），与 rebase 前基线 11 条一致，**零新增** |
| 用例总数变化 | 1341 → 1366（+25，来自 `#692` CoC 轨等上游新增） |
| S0-A 单点可用性 | 在第 3 个提交上单跑 `test_dnd2024_extra_attack.py` → **46 passed**，证明校验修正已随第一个提交落地 |
| 对立测试共存 | `test_dnd2024_extra_attack.py` + `test_dnd2024_feature_choices.py` → **99 passed** |
| 树一致性 | squash 前后文件树 diff 为 0 行 |

**备份**：`prealign-s0a`、`prealign-s0b`、`prealign-s0c`、`prealign-dds` 四个 tag 指向 rebase 前各头，可完整回滚。

**未做**：S0-B / S0-C / 链头三个分支未各自单跑全量（仅链头跑全量 + S0-A 单点跑聚焦）；四份需改规则的任务书尚未开工。
