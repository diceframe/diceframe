# S0 祈唤逐项实现状态

> 台账，不是规格。每次有祈唤相关的切片合入时**同批更新本表**；口径见 §1，
> 没有证据的格子写"无"，不写"基本完成"。
>
> 范围：F32–F35 四份任务书点名的 28 项。SRD 全文是否还有第 29 项，归 S0-F37
> 覆盖度审计核对；本表不替它作答。

## 1. 状态口径

| 标记 | 含义 | 判据 |
|---|---|---|
| ⬜ 未声明 | 特性目录里没有这条祈唤 | `srd_class_features.json` 无该 id |
| 🟡 不可选 | 已声明，但投影里 `available: false`，提交被拒 | `invocation_effect_status` 为 `guided` / `not_automated`，或缺前置 |
| 🟠 可选·效果未接 | 能选上、能落到角色卡，但效果没有任何消费方 | 声明 `complete`，`src/` 里无人读它的参数 |
| ✅ 已实现 | 效果落在权威路径上，并有行为用例 | 用例断言事件流/权威函数，不只是"能选" |

**这张表存在的原因**：`complete` 这个状态只说明"效果有落点"，不说明"效果已生效"。
把两者混在一格，下一次就会有人拿"能选上"当"做完了"。

## 2. F32：主干与八项核心（分支 `feat/dnd2024-s0f32-invocation-core`）

主干已交付：数量来自 `tracks.eldritch_invocations`、逐级替换、依赖闭包、选项前置、
单对象绑定；建卡（1 级 1 个名额）与升级两条路径都规划这个槽，31 条用例在
`test_dnd2024_invocation_core.py`。提交号不写：分支会 rebase，写死的 SHA 必失效。
状态：PR #892 待合并（基座 `0b12b0ea`）。

| 祈唤 | 等级 | 前置 | 声明 | 选择 | 效果 | 主干证据 |
|---|---:|---|---|---|---|---|
| Agonizing Blast | 2 | 魔契师 + 造成伤害的魔契师戏法；可重复 | ✅ `complete` | ✅ | ✅ 每次伤害 roll 加 Cha（`_add_cantrip_damage_bonus`） | 绑定谓词 + 所有权 + 伤害等于骰和+Cha + 未绑定不受影响 |
| Devil's Sight | 2 | 魔契师 2 级 | 🟡 `guided` | 🟡 不可选 | 🟡 缺光照/黑暗模型 | 投影带 `guided`、提交被拒 |
| Eldritch Mind | **1** | 无前置（原文没有 Prerequisite 行） | ✅ `complete` | ✅ | ✅ 专注豁免优势，事件带来源 id | 掷两骰取高；1 级唯一可选项有断言 |
| Eldritch Spear | 2 | 魔契师 + 造成伤害的魔契师戏法；可重复 | ✅ `complete` | ✅ | ✅ 射程 +30×魔契师等级（先加后乘） | 绑定戏法 120→180（2 级）；未绑定戏法不变 |
| Fiendish Vigor | 2 | 魔契师 2 级 | ✅ `complete` | ✅ | ✅ 免槽 False Life + 骰面取最大（固定 12） | 免槽载体矩阵 + `amount == 12` 且 `maximized` |
| Lessons of the First Ones | 2 | 魔契师 2 级；可重复 | 🟡 `guided` | 🟡 不可选 | 🟡 升级路径没有 Origin 专长池 | 无（用例未覆盖） |
| Repelling Blast | 2 | 魔契师 + 需攻击检定的魔契师戏法；可重复 | ✅ `complete` | ✅ | ✅ 命中后推 0–10 尺直线远离 | 命中推 10 尺；超距被拒；Huge 被拒 |
| Witch Sight | 15 | 魔契师 15 级 | ✅ `complete` | ✅ | ✅ Truesight 30 进 `sense_declarations`/`vision_codes` | 行存在、20 尺命中、40 尺不命中 |

## 3. F33：九个施法型（未开工）

九项都要"授予法术 + 免 Pact 槽施放"：`always_prepared` + `free_cast_slots`
是 F5 已建的路径，**不需要新语义**；卡点在法术本体效果，不在祈唤。

| 祈唤 | 等级 | 法术 | 声明 | 效果 | 备注 |
|---|---:|---|---|---|---|
| Armor of Shadows | 1 | Mage Armor（自身） | ⬜ | — | 法术效果未自动化 |
| Ascendant Step | 5 | Levitate（自身） | ⬜ | — | 同上 |
| Mask of Many Faces | 2 | Disguise Self | ⬜ | — | 同上 |
| Master of Myriad Forms | 5 | Alter Self（自身，专注） | ⬜ | — | 同上 |
| Misty Visions | 2 | Silent Image（专注） | ⬜ | — | 同上 |
| One with Shadows | 5 | Invisibility（自身） | ⬜ | — | 需光照，缺模型 → 只能 `guided` |
| Otherworldly Leap | 2 | Jump（自身） | ⬜ | — | 法术效果未自动化 |
| Visions of Distant Realms | 9 | Arcane Eye（专注） | ⬜ | — | 同上 |
| Whispers of the Grave | 7 | Speak with Dead | ⬜ | — | 需要尸体/问答事实 |

## 4. F34：契约刃（未开工，需要新语义）

这五项的阻塞不是"没写"，是**主干装不下**：`object_ref` 只能指向静态 ref，
而 Pact of the Blade 绑的是**物品实例**，还要 conjure 一把带服务端身份的武器、
按武器计算攻击次数上限。

| 祈唤 | 等级 | 前置 | 声明 | 效果 | 需要的语义 |
|---|---:|---|---|---|---|
| Pact of the Blade | 1 | 无 | ⬜ | — | 武器实例绑定 + 生命周期（离手/死亡/换绑）+ Cha 替代 + 伤害类型 |
| Thirsting Blade | 5 | Pact of the Blade | ⬜ | — | 按**武器**计的攻击次数上限（`attacks_per_action` 现在按职业取最大） |
| Devouring Blade | 12 | Thirsting Blade | ⬜ | — | 同上，上限改 3 且不与 Extra Attack 相加 |
| Eldritch Smite | 5 | Pact of the Blade | ⬜ | — | 命中后窗口 + 支付 Pact slot（F14 的窗口机制可承载，但是新处理器） |
| Lifedrinker | 9 | Pact of the Blade | ⬜ | — | 命中后窗口 + 可选消耗 Hit Die 治疗 |

## 5. F35：契约链与魔典（未开工，需要别的子系统）

| 祈唤 | 等级 | 前置 | 声明 | 效果 | 阻塞 |
|---|---:|---|---|---|---|
| Pact of the Chain | 1 | 无 | ⬜ | — | F27 familiar 生命周期未开工；还要让渡自身攻击 |
| Pact of the Tome | 1 | 无 | ⬜ | — | **一次选择要开 3 戏法 + 2 仪式**，`object_ref` 装不下；书实体还要有生命周期 |
| Investment of the Chain Master | 5 | Pact of the Chain | ⬜ | — | 依赖 Chain；改魔宠速度/攻击/抗性 |
| Gift of the Protectors | 9 | Pact of the Tome | ⬜ | — | 名字页 + HP 保底 + 每长休一次，落在 HP 结算边界 |
| Gaze of Two Minds | 5 | — | ⬜ | — | 借感官 + 借空间施法；位面未建模，F35 已规定缺支撑标 `guided` |
| Gift of the Depths | 5 | — | ⬜ | — | 一半有落点（`derived.swim_speed` 已有）；水下呼吸要 Water Breathing 效果 |

## 6. 汇总

| 状态 | 数量 | 是哪几项 |
|---|---:|---|
| ✅ 已实现 | **6** | Agonizing、Eldritch Mind、Eldritch Spear、Fiendish Vigor、Repelling、Witch Sight |
| 🟡 不可选（已声明） | 2 | Devil's Sight、Lessons of the First Ones |
| ⬜ 未声明 | 20 | F33 九项、F34 五项、F35 六项 |
| 合计 | 28 | |

**池子广度是一个显式边界**：本片只声明 8 项，而职业表在 12 级要 8 个；在两把伤害戏法下
最多凑出 **7** 个合法组合（agonizing×2、spear×1、repelling×2、mind、vigor），剩下的名额
无法填。此时升级**不拦**，差额记在需求槽的 `shortfall` 里；"有得选却漏选"仍然照拦。
容量按**可选取次数**算（可重复选项每绑一个对象多一次），并且把"同一批里新获得的依赖项"
算进去——F34 的 Pact of the Blade + Thirsting Blade 就是同批获得。这两条都有用例
（`test_the_capacity_counts_picks_not_option_rows`、
`test_two_options_granted_together_may_depend_on_each_other`）。

F33–F35 把余下 20 项声明上去之后，shortfall 应当自然归零——届时要把用例里的期望值改成
0，而不是把豁免留着。

**苦痛魔爆的多束场景是 GAP，不是没测**：SRD 2024 的 Eldritch Blast 在 5/11/17 级各多一束，
而数据里它只有一道 `1d10`，法术攻击层也没有"多束"抽象（详见 `GAP_REGISTER.md` GAP-44）。
所以"每束各加一次 Cha"这条验收（任务书 §6.5）**在法术子系统动工前无法验收**；
单束场景已测。谁先动法术抽象层，谁负责把这行改成 ✅。

**1 级建卡的口径**：`builder_choices.invocation_choices` 会投影这个槽（1 级只有
Eldritch Mind 可选），作答会写成与升级路径同形状的 `feature_choices` 记录；**未作答不
阻断建卡**（与 Expertise 的既有约定一致），首次升级按 `track` 补齐。`missing_choices`
目前不含祈唤，所以"欠祈唤"的角色在修复入口看不到——这是已知缺口，不在本片范围。

**老卡**：本切片之前建的魔契师卡没有池记录。存档重放（`normalize_character_submission`）
允许这种卡跳过必填槽并种下 `legacy: true` 的记录，后续等级沿用豁免，直到玩家真正提交
一次完整选择。正常升级不走这条豁免。

**结论**：主干与"选择"这一层 28/28 可用；F32 的八项里 6 项效果已落地并有行为
用例，2 项按停止条件记 `guided`。剩余 20 项属 F33–F35，其中 F34/F35 各有需要
新语义的部分（见上面两节）。
