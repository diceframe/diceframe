# S0 任务索引：SRD 职业与子职落地

| 项 | 值 |
|---|---|
| 范围 | 官方 SRD 5.2.1 的 12 个职业、12 个子职及其特性 |
| 规则基准 | 官方英文 PDF，2025-05-01，CC-BY-4.0；SHA-256 见 `SRD_SOURCE_AUDIT_2026-11-06.md` |
| 代码祖先基线 | `integration/dnd2024-srd@35653f81`；后续切片按依赖链依次叠加，不跳基线 |
| 状态 | 已 rebase 到 `main@10a6ab23`（release 3.1.0，含 `#687`/`#689` D3b、`#688` 拥有过滤、`#692` CoC 轨）。S0-A 已实现（含 A2 迁移，分支 `feat/dnd2024-s0a-subclass-critical-upstream@7a28b25a`）；S0-B 已实现（分支 `feat/dnd2024-s0b-feature-choices@1304beb7`）；S0-C 已实现（分支 `feat/dnd2024-s0c-draconic-resilience@f6d3d8bb`）；D1–D4 修复在链头 `feat/dnd2024-derived-defense-speed@73d11263`；S0-D2 已实现（`feat/dnd2024-s0d2-overchannel@f775ae4f`）、S0-E1 已实现（`feat/dnd2024-s0e1-dark-ones-blessing@8f3182fd`）、S0-E2 已实现并复核（`feat/dnd2024-s0e2-paladin-auras`）；S0-F9 已交付可自动化部分（`feat/dnd2024-s0f9-movement`），环境面按停止条件记录；S0-D3 已实现（`feat/dnd2024-s0d3-cutting-words@fa254b1f`）；S0-D1 已实现（`feat/dnd2024-s0d1-supreme-healing@5283f77d`，stack 在 D3 链头上）；S0-E3 已实现（`feat/dnd2024-s0e3-survivor@c1fef294`，stack 在 D1 链头上）；S0-F1 已实现（`feat/dnd2024-s0f1-d20-skills@5efb0194`，stack 在 E3 链头上；`5efb0194` 为旧测试对齐的 test-only 补提交）；S0-F2 已实现（`feat/dnd2024-s0f2-bardic-inspiration@ebd6166c`，stack 在 F1 链头上）；S0-F3 已实现（`feat/dnd2024-s0f3-failed-d20-windows@b86eec6a`，stack 在 F2 链头上）；S0-F4 已实现（`feat/dnd2024-s0f4-spell-grants@745baaee`，stack 在 F3 链头上）；S0-F5 已实现（`feat/dnd2024-s0f5-free-casting@910733c0`，stack 在 F4 链头上）；休息面板接线已交付（`feat/dnd2024-rest-panel-wiring@c5e4a4f1`，stack 在 F5 链头上，地形与研究两类长休声明可由 UI 提交，见 §11）；休息的 DM 裁决与逻辑时间推进已交付（`feat/dnd2024-rest-decision@dbb6edd8`，stack 在面板接线之上，见 §12）。A 类六份（D1–D3、E1–E3）至此全部交付；F 类已交付 F1、F2、F3、F4、F5、F6a、F6b、F9。F6 按机制拆三段：**F6a（短休恢复预算）已交付**（`feat/dnd2024-s0f6a-rest-recovery-budget`，stack 在 `fc1907e0` 之上，见 §13）；**F6b（Magical Cunning 1 分钟仪式）已交付**（`feat/dnd2024-s0f6b-magical-cunning-rite`，stack 在 `2d06ce89` 之上，见 §14）；F6c（Font of Magic）待执行。剩余 29 份：F6c、F7、F8、F10–F37 待执行 |
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
| S0-F1 | D20 技能、专精与下限 | Expertise、Jack of All Trades、Reliable Talent、Remarkable Athlete、Scholar | `S0F1_D20_SKILLS_TASK_SPEC_2026-11-07.md` | 22 | **已实现**（`feat/dnd2024-s0f1-d20-skills@5efb0194`，stack 在 E3 链头上；全量回归净引入 0，旧测试对齐见该文件 §10 补记）。`derive_skill_check()` 成为唯一加值入口（0 / PB / 2PB / floor(PB/2) 四档互斥，建卡/升级/live 三处共用）；选择槽新增 `skill_proficiency` kind 与 `grants` 闭集，Expertise 按 `granted_level` 分组合并、按技能去重，三条同名 `expertise` 不串 owner；Reliable Talent 替换面值（单次 RNG，`raw_d20`/`effective_d20`/`d20_floor_sources` 留痕）；Remarkable Athlete 的先攻优势与暴击后移动全部由声明驱动，多来源仍只掷两枚骰。矩阵 21 通过 / 1 部分（Speed 0 只能借 paralyzed 表达）；白名单修订 2 个文件 + 1 个共享夹具、`view.py` 重甲速度双扣的既有缺陷未修，均记录在该文件 §10 |
| S0-F2 | 激励骰实例与掷后修正 | Bardic Inspiration、Font/Superior Inspiration、Peerless Skill、Cutting Words | `S0F2_BARDIC_INSPIRATION_TASK_SPEC_2026-11-07.md` | 25 | **已实现**（`feat/dnd2024-s0f2-bardic-inspiration@ebd6166c`，stack 在 F1 链头上）。激励骰是 `combat.bardic_instances` 里的一等实例，一小时走权威世界时钟（无系统时间、无 tick）；失败面按 `bardic_inspiration` → `peerless_skill` 排队逐个开口，取消不丢后续面；Font 的槽换次数不置 `slot_spell_cast`，Superior 在真实先攻后 `max(current, min(2, max))`；Cutting Words 攻击/伤害面沿用 D3，本切片新增 `ability_check` 成功面。全量净引入 0（11 failed 与基线逐条相同），矩阵 25/25 有证据。**豁免面按裁决单独跟进**、`deafened` 未建模、叠面骰迹只留最后一面见该文件 §10 |
| S0-F3 | 失败 D20 后窗口 | Tactical Mind、Dark One's Own Luck、Countercharm、Indomitable、Disciplined Survivor、Stroke of Luck | `S0F3_FAILED_D20_WINDOWS_TASK_SPEC_2026-11-07.md` | 24 | **已实现**（`feat/dnd2024-s0f3-failed-d20-windows@b86eec6a`，stack 在 F2 链头上）。窗口注册表加两个正交标志而不加第二条管线：`rerolls`（成本买一颗全新 d20 并强制采用，旧结果作废但留在 `rerolled_*`）与 `replaces_natural`（改天然面、不掷骰），加骰/重掷/改面三种形状同一入口分派。每颗 d20 有稳定 `roll_id`（批次内序号，重掷派生 `-r1`，不读时钟）。一个 d20 上的多个合法面用 `settlement.offered` 排队逐个开口（Stroke → Indomitable/Disciplined → Dark Luck → Countercharm），取消不丢后续面；回合末重复豁免改为可暂停续算。全豁免熟练走声明 `saving_throw_proficiency: "all"`，建卡与升级共用一张 `saving_throw_refs`。全量净引入 0（11 failed 与基线逐条相同，通过数 1654→1697）。**死亡豁免不开放重掷面**已由 2026-11-09 的裁定放开（Stroke/Indomitable/Disciplined 在死亡豁免上与其它豁免同权，加骰面仍排除；见 F4 §12.2）、白名单外改 1 行测试，见该文件 §10 |
| S0-F4 | 来源化法术授予 | 五组子职法术、Circle Spells、Magical Discoveries、Magical Secrets | `S0F4_SPELL_GRANTS_TASK_SPEC_2026-11-07.md` | 24 | **已实现**（`feat/dnd2024-s0f4-spell-grants@745baaee`，stack 在 F3 链头上）。七项特性在基线只有 locale 标签与子职目录 id，本切片补声明而不新建管线：`spell_grant_rows()` 每源一行（`owner`/`feature_id`/`spell_ref`/`granted_level`/`terrain`/`spell_ability`），`prepared_view()` 每法术一条有效项并并列 `sources`，容量拆成 `prepared`/`prepared_used`/`granted`/`cantrip` 四个字段，常驻授予不占容量且按授予者职业结算。Land 四地形走长休事务（先校验后落账，非法地形整休不动；切换是替换不是叠加；同地形幂等；缺省不切换但产 `spell_grant_terrain_missing`）。Magical Secrets 只做 `prepared_list_expansion`（不扩容量、不给戏法开列表），Magical Discoveries 恰两项且每级最多换一项。`automation_status` 由 `spell_effects` 真实存在派生，`not_automated` 施放 fail closed。全量净引入 0（11 failed 与基线逐条相同，通过数 1697→1728 = +31 条新测试）。**第一版回归**：把"未回答的授予槽"当非法提交，曾致 62 ERROR / 6 FAILED（全部是 Bard 6 级以上旧夹具），已改为"待选 ≠ 非法"并把边界钉进矩阵 24；两个玩家选择槽未走 `features.feature_choices`（加载期闭集）见该文件 §11；**同日两项裁定已落地**（§12）：`runtime.complete_rest()` 开始转发 `land_terrain`（`rest_option_names` 扩到 `{attunement, land_terrain}`，队伍休息开局前非变更校验），死亡豁免放开重掷面并保留 `death_save_crit_threshold` |
| S0-F5 | 独立免费施法资源 | Contact Patron、Mystic Arcanum 6/7/8/9、Spell Mastery、Signature Spells | `S0F5_FREE_CASTING_TASK_SPEC_2026-11-07.md` | 25 | 已实现 |
| S0-F6 | 休息恢复预算与转换 | Arcane/Natural Recovery、Sorcerous Restoration、Magical Cunning、Eldritch Master、Font of Magic | `S0F6_REST_RESOURCE_CONVERSION_TASK_SPEC_2026-11-07.md` | 25 | **部分交付**：F6a 短休恢复预算（矩阵 1–5、8、9、22、23，§10）与 F6b Magical Cunning 1 分钟仪式（矩阵 10–13，§11）已实现；F6c（Font of Magic，矩阵 14–21）待执行 |
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

## 10. 跨切片缺口：休息与仪式施法（2026-11-10）

这一节不属于任何单个 F 切片，但在 S0-F4（Land 地形）、S0-F5（法术研究）、S0-F6（休息恢复预算）之间反复出现，统一记在这里，避免每次重新发现。

### 10.1 休息的机械边界

- **时钟一直存在，休息没有接上**（2026-11-10 更正）。权威逻辑时钟在 `GameInstance.world_state["clock"] = {"day", "minute"}`（`world_state.py::fresh_world_state`，从第 1 天 00:00 起），唯一写入口 `world_events.py::advance_world_time`：单次上限 1440 分钟、确定性排序、幂等（`pending → applied/failed`）、随整轮回滚，并顺带结算到期事件与进程。**推进它的只有两处**——回合元数据里的 planner `world_time_advance`，以及 CoC 恢复链的 GM 批准。DND 长休/短休一次都没调用过它，`confirm_elapsed_time` 只是客户端确认。本节初版写作"全仓没有游戏内时钟"，那是只搜了 `in_game_time` 字面量的结论，错的。
- **长休频次不设限**。有钟之后这可以机械执行（记「上次长休时刻」并与当前钟比），但尚未做——"每 24 小时一次"目前仍只是桌面口头约定。
- **没有中断模型**。休息与战斗只有单向互斥：`ruleset_rest.py` 提交时检查"战斗中不能休息"，但 `combat.start` **完全不看** `rest_session`。GM 在 `collecting` 阶段开战会让 session 挂起（已提交者停在 `submitted`，未提交者被拒）；若期间角色 `ruleset_revision` 变化，最终结算会撞 `STALE_CHARACTER_REVISION` 并整队回滚。**这是状态漏洞，不是缺功能。**（§12 的 DM 裁决给了它一个用户可见的出口：拒绝即终止态，不悬挂；但 `combat.start` 仍未清 `rest_session`。）
- **单职业硬限制**。`resting/engine.py::_class_state()` 在 `class_levels` 长度不等于 1 时抛 `rest recovery currently requires one class`，多职业角色无法休息。
- **没有休息前预览**。客户端拿不到"这一觉会恢复什么"，只能按下去再看事件。

### 10.2 仪式施法完全缺失

`ritual` 在代码里出现两次，都不是实现：`spells/catalog.py` 只校验它是 bool，`spells/selection.py::_casting_segment()` 把它**剥掉**（`"1 minute or Ritual"` → `"1 minute"`）。没有"不消耗法术位、多花 10 分钟"的路径。直接后果：

- `alarm`（1 环）、`tiny_hut`（3 环）都是 `1 minute or Ritual`，`prayer_of_healing`（2 环，10 分钟，短休用）也是休息场景常用法术，三者都没有 `spell_effects` 条目，`combat/validation.py::_validate_spell` 第一道门即拒；
- S0-F5 §9.11 偏差 6 的"目录里没有仅仪式样本"由此而来——不是样本缺失，是机制缺失；
- 长休前的"准备行动"（放警戒、造小屋）因此没有任何机械表达。

### 10.3 前端可达性

服务端已经能算、但 UI 送不进去的字段有两个：`land_terrain`（S0-F4 地形）与 `free_cast`（S0-F5 法术研究）。`character.rest` 的 P2P 字段白名单只有 `attunement`，`resolveLiveCharacterRest()` 也只发 `rest/hit_dice/confirm_elapsed_time/expected_revision/operation_id`。

**已解决**（见 §11）：两项都通了，读侧新增 `GET /api/games/{key}/character/{uid}/rest/options`。

### 10.4 建议顺序（2026-11-10 按实际交付重排）

1. ~~**休息面板接线**~~ → 已交付（§11）。
2. ~~**游戏时钟接入**~~ → 已交付（§12）：不走「休息结算即推时钟」，而是复用 recovery 的「申请 → DM 批准」范式，批准那一刻推进。顺序上仍成立的判断是：**时钟必须先于频次限制**，因为频次限制要拿「上次长休时刻」和当前钟比。
3. **中断语义**：`combat.start` 遇到 `collecting` / `awaiting_approval` 的 session 就标记中断并清空参与者。改动最小，修的是漏洞，至今未做。
4. **长休频次限制**（24 小时一次）：需要「上次长休时刻」的独立记录，且要给 DM 留覆盖口（SRD 允许判定「这不算新的冒险日」）。
5. **仪式施法机制**：`alarm` / `tiny_hut` / `prayer_of_healing` 的前置，同时消掉 F5 的偏差 6。时钟到位后，「仪式多花 10 分钟」才有地方记。

## 11. 休息面板接线（2026-11-10）

不是 S0-F 任务书，是 §10.3 的直接修补：把服务端已经算好的两类长休选择交给 UI，并把 `free_cast` 的写入通道补齐。

**分支**：`feat/dnd2024-rest-panel-wiring`，stack 在 `feat/dnd2024-s0f5-free-casting@910733c0` 之上。
**提交**：`5e565c21` 服务端投影与 `free_cast` 通道 → `cb831508` 前端面板 → `a2ec04d1` 测试 → `c5e4a4f1` 独立负债修复（见 11.4）。

### 11.1 服务端

- `resting/engine.py::rest_options(character)`：只读投影，返回 `land_terrain`（owner / feature_id / current / options / required）与 `free_cast`（只列 `rest_replace > 0` 的槽，带当前法术、候选、预算与 `valid`）。不建会话、不改 revision，重复调用无副作用。
- `spells/selection.py::rest_free_cast_rows(character)`：槽位归 spells 模块、地形归 features 模块，引擎只做组装，避免两边各维护一份"哪些槽能在长休换"。
- `runtime.py`：`rest_option_names` 增加 `free_cast`；`complete_rest` 增加同名关键字并转发给 `complete_long_rest`；短休收到它直接 `ValueError`；`validate_rest_options` 增加分支，用 `engine.validate_free_cast()`（deepcopy 探针）在**开局时**就拒绝非法研究——队伍休息中途失败会整队回滚，所以必须前置。
- 新增只读路由 `GET /api/games/{key}/character/{uid}/rest/options`（`Api.ruleset_rest_options` → `ruleset_rest.read_options()`），不加写锁、不碰 revision、不建休息会话。
- 顺带修掉一处事件噪音：`_apply_free_cast_rebind` 过去会给"计划里带上但没有改动"的槽也发 `rebind_free_cast`（`before == after`），现在只给真正换掉的那一格发。

### 11.2 前端

- `resolveLiveCharacterRest()` 增加可选第七参 `{ landTerrain, freeCast }`；**空值不发字段**——空白地形不等于"不选地形"，空研究表也不该进入 rebind 路径。
- `bridge.ts` 的 `character.rest` 白名单补 `land_terrain` / `free_cast`，P2P 与 HTTP 两条路径同构。
- `ProfessionalCharacterCenter.vue`：**切到长休时才**拉选项（打开面板零请求、短休零请求）；地形预填当前值；研究是"先选要换的一格，再选新法术"的单选，预算取自服务端的 `rest_replace`；当前研究项不进候选；读不到选项时降级为旧行为并把原因显示出来，**不阻断休息**。

### 11.3 验收

| 项 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_rest_options.py` | **15 passed** |
| 休息/施法/队伍休息定向回归（7 个文件） | **167 passed** |
| `pytest tests/rulesets -q`（全量） | **11 failed / 1771 passed / 0 errors**（23:48）；11 条与 F2–F5 基线逐条同名（全在 `test_dnd2024_character_builder.py`），通过数 1756→1771 = +15，净引入 0 |
| `frontend-v2` `npm test` | **221 files / 1587 tests passed** |
| `vue-tsc -b` 与 `eslint`（改动文件） | 通过 |
| `ruff check` 与 `git diff --check` | 通过 |

### 11.4 期间发现的两笔债

1. **F5 矩阵 7 的 `_spend()` 从未真正运行过**。上一轮我在全量已经启动之后才补上这段代码，全量跑的是旧文件，于是"25 passed"掩盖了一个非法批次（缺 `batch_id`、缺版本）。本次改为 `stable_batch_id` + `expected_version/result_version` 的合法批次，并把"同一格再花一次"改成断言 `EventBatchError("already spent")`——reducer 是抛错回滚，不是返回 `applied: False`。
2. **S0-D2 的 Overchannel 文案断言已失效**。`f775ae4f` 把文案改成"超限导能：伤害最大化"，同一提交的测试仍断言"过载伤害最大化"，前端套件因此长期有一条红的。已单独提交对齐，不并入本切片的功能提交。

## 12. 休息的 DM 裁决与逻辑时间（2026-11-10）

任务书：`S0_REST_DECISION_TASK_SPEC_2026-11-10.md`。这是 §10.1 的第二条（时钟）与 §10.4 的第 2 项，采用「复用 recovery 的申请 → 批准范式」而非「休息结算即推时钟」。

**分支**：`feat/dnd2024-rest-decision`，stack 在 `feat/dnd2024-rest-panel-wiring@c5e4a4f1` 之上。
**提交**：`ab7c4425` AI GM 工具 → `8f136cc7` 服务端裁决与时钟 → `2c743a5e` 前端 → `dbb6edd8` 测试 → `fc1907e0` 矩阵 18。

### 12.1 状态机

```text
idle ──首个提交──> collecting
collecting ──集齐 required──> awaiting_approval      (party)
collecting ──solo 提交──> resolving                  (solo，等价于自己批准)
awaiting_approval ──DM approve──> resolving ──> completed
awaiting_approval ──DM reject──> rejected
resolving ──任一角色失败──> error
```

`awaiting_approval` 是唯一新增的中间态；`rejected` 是终止态，下一次提交可直接重开。`rejected` 不修改任何角色卡、不推进时钟。

### 12.2 时钟只在一个地方动

`runtime.rest_duration_minutes(rest)` 是时长的**唯一来源**（DND2024：短休 60、长休 480，取 SRD 下界）。声明缺失的规则集按 0 处理、照常结算；返回值非法时 `REST_DURATION_INVALID` fail closed，角色与时钟都不动。

推进发生在批准之后、`completed` 之前，同一个 `authoritative_write` 与 `_lock` 内：分段（`MAX_ADVANCE_MINUTES = 1440`）→ `advance_adventure_world(instance)` 重估 Adventure gate 与进程 due_at → `queue_world_memory(...)` 让 AI GM 知道时间流逝 → `source_round` 挂回合号。任一步失败，`before_players` 与 `world_state` 快照一起回滚，会话置 `error`。

### 12.3 逻辑时间取代墙钟

会话与参与者不再写 `datetime.now()`：

| 旧字段 | 新字段 |
|---|---|
| `started_at`（ISO 字符串） | `started_tick`（绝对逻辑分钟） |
| `submitted_at` | `submitted_tick` |
| `resolved_at` | `resolved_tick` + 投影 `resolved_clock` |

旧存档里的字符串字段不迁移、不解析，读到即忽略；`public_rest_session` 对缺失字段沉默降级。`rest_activity.py` 的会话身份从 `started_at` 改为 `started_tick` 参与哈希。**理由**：同一存档在不同时刻、不同机器打开必须描述同一次休息，墙钟不满足这一点。

### 12.4 权限与幂等

- `POST /api/games/{key}/rest/decide`，body `{approve, operation_id}`；`_gm_only_inst` 前置拦截，非 GM 403。
- 幂等**先于**状态检查：同一 `operation_id` 重放时会话早已离开 `awaiting_approval`，若先判状态会把一次成功的裁决报成失败（实测踩到，已按此顺序修正）。
- `awaiting_approval` 期间参与者可再次提交以修改自己的申报，会话清空 `decision` 重新等待。

### 12.5 AI GM 不能死锁

新增工具 `decide_party_rest`（`mutation=True`、`automatic=False`、`effect="external"`，guard 走 `gm_instance`）。没有它，AI 席位当局的 AI GM 局会让队伍休息永久停在 `awaiting_approval`。

### 12.6 验收

| 项 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_rest_decision.py` | **16 passed**（矩阵 18 于 `fc1907e0` 补齐） |
| 休息/生命周期/队伍/活动/绑定定向回归（12 个文件） | **259 passed**（对齐后） |
| `pytest tests/rulesets -q`（规范切片套件全量） | **11 failed / 1787 passed / 0 errors**（13:27）；11 条仍是 `character_builder` 基线，通过数 1771→1787 = +16，净引入 0 |
| `frontend-v2` `npm test` | **221 files / 1589 tests passed** |
| `vue-tsc -b`、`eslint`、`ruff check`、`git diff --check` | 通过 |

**关于 `pytest tests`（非切片套件全量）**：本环境跑出 `18 failed / 7035 passed / 2094 errors`，但把报错文件单独跑是 **133 passed**，逐条不可复现——这是批量运行的环境噪声（并发跑尤其明显），不作为验收依据。切片验收仍以 `tests/rulesets` 为准，另有受影响的 `tests/*` 定向回归。

### 12.7 偏差与未决

1. **`combat.start` 仍不看清 `rest_session`**（§10.1 第三条状态漏洞未修）。本切片给了它一个用户可见的出口（拒绝即终止态，不悬挂），但 DM 在 `collecting` 阶段开战仍会让会话挂起。
2. **长休频次限制未做**：有钟之后可机械执行，但需要「上次长休时刻」的独立记录，且要给 DM 留覆盖口。
3. **长休的"至少"留白未建模**：规则时长取下界，DM 想表达「这一夜过了十小时」目前只能另行推进时钟，没有把这次休息与那段额外时间关联起来。
4. **单人模式不走批准**：solo 下玩家即 GM，提交即结算并推进时间。这是刻意不做成立即点击的空按钮，与「队伍需要一个裁决人」的立场一致。
5. **前端未加新的 locale 键**：等待/裁决文案走面板内联 `text(cn, en)`，避免为两个键改动五份语言文件；`PartyActions` 的队伍进度行因此仍只显示「3/3 准备就绪」。

## 13. S0-F6a：短休恢复预算（2026-11-10）

F6 的 25 条矩阵横跨三种机制，按机制拆三段执行（见该任务书 §10）。本段只做**短休预算**：Arcane Recovery / Natural Recovery / Sorcerous Restoration。

**分支**：`feat/dnd2024-s0f6a-rest-recovery-budget`，stack 在 `feat/dnd2024-rest-decision@fc1907e0` 之上。
**提交**：`93c5e78a` 内容与引擎 → `99d430b4` 测试 → `2d06ce89` 下标测试对齐。

### 13.1 之前的状态

六项特性**一条特性定义都没有**：`srd_class_features.json` 只有标签与进阶表条目；`arcane_recovery` / `magical_cunning` 在恢复目录里只是「每长休重置」的计数；`natural_recovery` / `sorcerous_restoration` / `eldritch_master` / `font_of_magic` 连计数都没有。任务书 §3 说「已有 rest resource 声明」只对前两项成立。

### 13.2 做法

预算口径写进恢复目录，代码里没有按职业分支：

```json
"short_recovery": {"mode": "spell_slots", "budget": "half_level_round_up", "max_slot_level": 5}
```

- `mode` = `spell_slots`（合计环阶）或 `points`（点数）；`budget` = 上取整 / 下取整。
- `requires_subclass`：休息表按职业键入，说不出「只有某个子职」。没有这道闸，月德鲁伊 6 级会拿到永不可用的自然恢复计数。`sync_resources` 按 `features.subclass_ref` 判定，不满足即删池。
- 载荷 `recovery = {<id>: {"slots": {"3": 1}} | {"points": 2}}`，短休专用；`rest_option_names` 扩到四项，通用休息服务因此会自动转发（四段链的 ①②③④ 全通，未改 web/frontend）。
- 事件三型：`recover_spell_slots` / `recover_class_points` / `spend_resource_use`；零恢复视为无操作，不消耗当天的一次。

### 13.3 偏差

1. **任务书矩阵 5 的数字本身有误**：「等级 6/7 均预算 3」与它引用的原文（round up）矛盾，7 级应为 4。按原文实现。
2. 矩阵 4 的「缺少真实 spellbook 不提供恢复」未建模——原文只要求已消耗的槽，不额外加门。
3. Natural Recovery 的免费施法面（矩阵 6/7）不在本段：它需要一种新的 `free_cast_source`（从授予列表里选），F5 的三档 `{fixed, class_list, spellbook}` 表达不了。
4. 前端未接线：投影已交出 `short_recovery`，面板还没画选择器。

### 13.4 验收

| 项 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_rest_resource_conversion.py` | **16 passed** |
| 相关定向回归（resting / class_features / free_casting / spell_grants / rest_options / rest_decision / lifecycle / advancement / feat_resources 共 10 文件） | **219 passed / 1 failed**——唯一失败是投影断言写死了两个空字段，按新契约补 `short_recovery: []` 后该集合全绿 |
| `pytest tests/rulesets -q`（规范切片套件全量） | **11 failed / 1803 passed / 0 errors**（18:53）；11 条仍为 `character_builder` 基线，通过数 1787→1803 = +16，净引入 0 |
| `ruff check` / `git diff --check` | 通过 |

**期间踩到的一处下标脆弱性**：`test_dnd2024_draconic_resilience.py` 的 `_mutated()` 按下标改 `sorcerer.features[0]`。插在它前面的 `sorcerous_restoration` 没有 `parameters` 块，于是 8 条参数化用例从「断言被拒」变成 `KeyError`。改成按 id 取。这类按下标读内容表的写法在切片不断插入新特性时必然失效，已是第二次（前一次是 F4 的授予槽）。

## 14. S0-F6b：Magical Cunning 的 1 分钟权威仪式（2026-11-10）

F6 的第二段（任务书 §11）。范围是矩阵 10–13：一个要花真实 1 分钟的仪式，以及它在 20 级被 Eldritch Master 改成满额的那一次。

**分支**：`feat/dnd2024-s0f6b-magical-cunning-rite`，stack 在 `feat/dnd2024-s0f6a-rest-recovery-budget@2d06ce89` 之上。

### 14.1 之前的状态

`magical_cunning` 只是恢复目录里一个「每长休重置」的计数，没有任何仪式语义；`eldritch_master` 连计数都没有。逻辑时钟 `world_state.clock` 与 `advance_world_time` 早就存在，但**休息之外没有第二个消费者**——DND 的 1 分钟仪式从来没有真正推进过时间。

### 14.2 做法：同事务，而不是挂起态

仪式在**一次权威写**里完成四件事：校验 → 恢复 Pact 槽 → 扣掉当天的一次 → 推进 1 分钟。任一失败整笔回滚。

为什么不建挂起态：SRD 只写「1 分钟结束时恢复」，没有任何中断/专注/被打断即失败的条文。挂起态会顺手引入一套原文没有的中断规则；而同事务在可观测层面等价——提交那一刻，1 分钟确实已经流逝。

内容侧只加一条声明：

```json
"ritual": {"minutes": 1, "mode": "pact_slots",
           "budget": "half_max_round_up", "full_feature": "eldritch_master"}
```

- `half_max_round_up` 是**槽个数**的一半上取整，和短休那档 `half_level_round_up`（按等级合计环阶）名字刻意区分。
- `full_feature` 让 Eldritch Master 只**替换同一次使用**的上限，不新增次数；判定走 `resolver.has_feature`，不按职业等级分支。

### 14.3 顺带修掉的重复

时钟推进的四件套（推进、Adventure gate 重估、World Memory 排队、`source_round`）原本只长在休息服务里。仪式成为第二个消费者，于是抽成 `services/world_time.py::advance_game_time()`，休息改为调用它——行为不变，但下一个消费者出现时不会再各抄一份。

### 14.4 偏差

1. 「开始时」不可观测（同事务的直接后果），矩阵 10 改写为「提交时 1 分钟已流逝 + 时钟被拒则整笔回滚」。
2. 没有任何已消耗槽的仪式**直接拒绝**：空转会静默烧掉每日一次。
3. 未做仪式窗口（L2 挂起式），理由见 §14.2；前端也未接线。

### 14.5 验收

| 项 | 结果 |
|---|---|
| 引擎套件 `test_dnd2024_rest_resource_conversion.py` | **23 passed**（16 → +7） |
| 新增 `test_dnd2024_ritual_authority.py` | **6 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1816 passed**，11 条为 `character_builder` 基线，1803→1816 = +13，净引入 0 |
