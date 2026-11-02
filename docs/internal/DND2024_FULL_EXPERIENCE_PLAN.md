# D&D 2024 完整网团体验施工计划

> 维护者内部文档，不是对外契约，不得从玩家文档、网站或 `diceframe-content` 链接。
> 基线核查：`main@03c6da6` / v3.0.3（2026-10-28）。

## 目标

以当前 D&D 2024 SRD 规则集为基础，补齐三块能力，使 DiceFrame 能支撑接近完整规则书深度的网团体验：

1. **内容层**：12 职业特性从"幽灵 id"变为可显示、可结算的完整目录；
2. **效果原语层**：战斗引擎具备 buff / 触发 / 条件伤害 / 动作经济扩展的通用原语；
3. **战术地图层**：从 1D 剧场模式升级为 2D 战场（token、格子/坐标、后续迷雾）。

## 现状核查结论（2026-10-28，均已核实到代码）

### A. 内容层（2026-10-28 二次核查，量化）

规则事实核查源：`.rulebooks/dnd5r/`（CHM 解包 + UTF-8 转码，git 本地隔离，见"核查工具"节）。

| 内容类型 | 仓库现状 | 数量缺口 | 备注 |
|---|---|---|---|
| 职业特性 | deterministic 目录仅 6 个（monk 5 + paladin 1）；progression 表 134 个唯一 id 中 131 个为幽灵（含 15 个结构占位：子职选择 / ASI / epic boon） | **约 116 个具体特性** | 升级提示有 id、面板无内容、无结算 |
| 子职 | `srd_subclasses.json` 含 12 个 SRD 代表子职（结构 + `feature_ids_by_level`），但特性 id 全为幽灵 | 12 个 SRD 子职的全部特性（PHB 全量 48 子职另计 36） | 选择系统已有，内容缺 |
| 法术 | `srd_spells.json` 339 条**元数据**（等级/学派/施法时间/射程/专注），但战斗效果仅 `srd_combat_core.json` 的 `spell_effects` 31 条 | **约 308 个法术无机械效果** | 元数据可展示、可选学，战斗结算缺效果 |
| 专长 | `content/feats/` 6 文件（4 个专长）+ advancement 13 条声明；仅 Magic Initiate / Skilled 机械闭环 | **59 个尚无任何形式覆盖**（形式覆盖 17；机械闭环仅 2） | PHB2024 共 76：起源 10 + 战斗风格 10 + 通用 44 + 传奇恩惠 12 |
| 背景 | `backgrounds/` 4 个 | 约 12 个 | PHB2024 16 |
| 种族 | `species/` 9 个 | 1 个（Aasimar） | 基本齐 |
| 物品 | 三套系统并存：规则集 item bundle 50 文件（身份/AC 字段）+ 战斗目录武器档案 15 条（战斗参数）+ content-pack 叙事 item（source-aware）；AC 公式与装备资格已闭环 | 武器 15/38、护甲 6/14；武器词条 versatile/reach/loading/two-handed/ammunition 未建模；精通词条与护甲受训门控未建模；弹药/医疗包无消耗追踪、货币仅单 GP | 魔法物品为零：DMG2024 约 348 条（9 类），attunement/charges/激活/随机宝物表全缺 |
| 怪物 / NPC / 遭遇 | content-pack 可扩展（4 种 kind，source-aware） | — | 架构正确 |

关键架构事实（不变）：content-pack 可扩展类型仅 `monster / npc_statblock / item / encounter_profile`——此处 `item` 是**叙事库存物品**（背包/奖励/提案），与规则集 `item` bundle 是两套系统；spells / progression / class_feature / subclass / feat / advancement_feat / 规则集 `item`（含战斗武器档案耦合）均为硬编码 `srd_*` bundle，外部内容无法注入。魔法物品因这条缝无法用 content-pack 表达机械效果。

#### 核查工具（本节为执行侧共同资源）

- `.rulebooks/dnd5r/`：社区 5r 规则书 CHM 解包（4877 文件，已 UTF-8），含玩家手册2024 / 城主指南2024 / 怪物图鉴2025 / 2014 全套 / 扩展书 / 第三方。已加入 `.git/info/exclude`，不进 git。
- 用法：任务书的每条规则语义必须附"规则原文出处"（`.rulebooks` 路径 + 行号），执行侧可自行 grep 核对——**引用而非记忆**。示例见 `design/SECOND_WIND_TASK_SPEC_2026-10-28.md` §1。

### B. 效果原语层

| 事实 | 位置 |
|---|---|
| 特性能力可声明的底层动作只有 4 种：`unarmed_strike / dash / dodge / disengage`，注释明确"未知 kind 必须 fail closed" | `src/rulesets/dnd2024/features/models.py` |
| 成本模型仅 `action / bonus_action` + 资源消耗；装备前提仅 `unarmored / no_shield / weapon_kinds` | 同上 |
| 战斗意图全集含 attack / cast_spell / move / death_save / class_capability 等，reducer 已支持伤害/治疗/暴击、专注、反应计数、濒死三态、条件增删 | `src/rulesets/dnd2024/combat/primitives.py`、`reducer.py` |
| **优势劣势管线已存在**：`attack_sources()` 聚合多来源 → 统一 mode → keep-high/low 掷骰；但来源清单硬编码（`faerie_fire`、`dodging`、`gm_grant` 等 5 项），特性无法注册新来源 | `src/rulesets/dnd2024/combat/check_modes.py` |
| **触发式反应已存在（仅法术）**：Shield 的受击反应决策流、Divine Smite 的 `triggered_damage` 命中追加；"命中 → 弹决策 → 花反应 → 结算"闭环完整 | `src/rulesets/dnd2024/combat/reaction_spells.py`、`triggered_spells.py` |
| 伤害公式 AST 无加成注入点（无 rage / sneak attack 类条件伤害） | `src/rulesets/dnd2024/combat/action_adapter.py` |

**法术侧原语核查（2026-10-29 补，逐条分类见 `design/SPELL_GAP_MATRIX.md`）**：

- 308 条无效果法术逐条归类：~40 条可纯数据直填、56 条几何依赖（AoE 22 + zone 34，归阶段三后）、68 条战斗外叙事层、其余 ~144 条需 P1–P13 原语管线；
- **点名制多目标不需要 2D**：`validation.py` 已支持 `target_ids` 列表（bless 3 目标先例）；只有几何 AoE（按位置圈人）依赖地图；
- **condition 目前不阻止行动**：`incapacitated` 只是标记，回合跳过只认 `dead`/`stable`——hold 系缺"门控"而非"回合接管"；
- 1A 的 5 个切片对法术缺口几乎无净贡献（heal 与 triggered_damage 是既有能力的职业侧复用），法术需要独立的原语切片序列。

### C. 战术地图层

| 事实 | 位置 |
|---|---|
| 战斗位置为 **1D 剧场模式**（`position_mode: "theater"`，标量位置，距离=差值）；近战 5 尺、超射程劣势、借机攻击、推撞位移、移动预算校验均已实现 | `src/rulesets/dnd2024/combat/resolution.py`、`validation.py` |
| 地图特性为**叙事节点图**：世界书地点力导向布局 + 当前场景锚定 + 背景图 | `frontend-v2/src/utils/mapLayout.ts`、`src/webui/services/maps.py` |
| 完全缺失：2D 坐标/格子、token 层、迷雾、墙与光照、AoE 模板、实时位置同步 | — |

### 上游与许可

- 上游 `diceframe/diceframe`：AGPL-3.0，v3.0.3（2026-10-26），双人主力（EOEOY + Guan612 占 98% 提交），PR 已到 #603，迭代极快；允许大型重构但要求显式处理 contract。
- 外部参考边界：
  - `foundryvtt/dnd5e` 系统：代码 MIT（可研究/带声明并入），Active Effects 的 change 行模型（`key + mode + value` + duration）是效果实例的建模范本；
  - `foundryvtt/pf2e`：Rule Elements 的谓词 DSL 与触发分类学是触发注册表的参考；
  - open5e / dnd5eapi.co：SRD 5.2 结构化数据，CC-BY-4.0，可带署名重分发，与 AGPL 代码分层共存；
  - 5etools 数据处于灰色地带，不作为默认分发来源；
  - FVTT 核心 canvas 闭源，无代码可参考；PlanarAlly（开源自托管 VTT）是战术层架构参考。

## 实施计划

总体顺序：**效果原语垂直切片先行**（同时是内容层的模板与战术层价值的前提）→ 内容批量填充 → 战术层增量升级。

### 阶段一：效果原语垂直切片（B 层）

阶段一是原语族，按引入顺序分四个子阶段。每个切片 = catalog 数据 + 引擎原语 + reducer 事件 + 测试，独立可验证、可回滚，符合 PR 模板"最小完整改动"要求。

| 子阶段 | 主题 | 切片 / 原语 | 服务的内容域 |
|---|---|---|---|
| 1A | 职业特性切片 | 切片 1–5：heal / effect 实例 / 触发反应 / economy / 谓词伤害 | 12 职业基础特性 |
| 1B | 法术原语 | P1–P9（P10–P13 大件后置） | 308 条无效果法术 |
| 1C | 共用原语 | S-P1–S-P12 | 子职、专长、装备 |
| 1D | 物品原语 | attunement / charges / 激活动作 / 随机表 | 魔法物品与消耗品 |

统一工程约束（贯穿 1A–1D）：

- 每个 change key / mode / 触发点必须是**注册过的原语**，未知即 fail closed（对齐 `features/models.py` 现有注释与 `ENGINEERING_RULES.md` §15）；
- 先有引擎原语、再扩 `ACTION_KINDS` / capabilities schema，不得反向；
- 优势劣势冲突规则从"多来源相消"升级为 5e RAW 的"优劣势各自计数"（改 `check_modes.py` 时注意）；
- 优势来源要推广到豁免/检定，现仅覆盖攻击掷骰；
- 编号规则：P 系列由法术切片引入，S-P 系列是跨域共用原语；同一原语只实现一次，跨域需求在备注标注首个验证载体；
- 内容分批、导入管线与许可规则统一归阶段二；本阶段只定义原语与首个验证载体。

#### 1A：职业特性切片

| # | 切片 | 打通的原语 | 解锁的特性 |
|---|---|---|---|
| 1 | Second Wind | 资源消耗 → 治疗（heal 事件 reducer 已有） | fighter 1 级 |
| 2 | Rage | 持续性 condition 记账（duration / turns_remaining / source_actor_id / established / sustained_this_turn）+ 力量近战伤害加成 + 力量豁免优势 + 物理抗性 + 失能结束 + 回合归属续期 | barbarian 1 级 |
| 3 | Uncanny Dodge | 触发注册表泛化（`on_hit / on_damage_taken / on_miss / on_turn_start`）+ 伤害修正窗口 | rogue 5 级 |
| 4 | Action Surge | `economy.granted` 动作经济事件 | fighter 2 级 |
| 5 | Sneak Attack | 谓词求值器 + 条件伤害（"优势或队友邻近 → +Xd6"） | rogue 1 级 |

#### 1B：法术原语垂直切片（2026-10-29 新增）

动机：法术缺口（308 条）与职业特性是同构问题——先铺原语、再填数据。1A 的切片只服务职业特性，法术需要自己的原语序列。逐条分类与完整清单见 `design/SPELL_GAP_MATRIX.md`。

| # | 切片（代表法术垂直验证） | 打通的原语 |
|---|---|---|
| P1 | invisibility | attack_sources 数据驱动（感官修正，偿还 `check_modes` 硬编码债） |
| P2 | hold_person | condition 行动门控 + 每回合豁免 tick |
| P3 | scorching_ray | 多目标/多 beam 结算循环（`target_ids` 校验已有） |
| P4 | false_life | temp HP / max HP 修改 |
| P5 | stoneskin | 伤害类型抗性/免疫框架（Rage 的物理抗性已在 #612 review 修复落地，此处泛化到任意类型与来源） |
| P6 | misty_step | 1D 位移 + 速度修改 |
| P7 | counterspell | 施法反应（切片 3 反应管线换触发源） |
| P8 | dispel_magic | 魔法 condition 移除 |
| P9 | revivify | 复活（dead → 存活） |

工程约束见阶段一总纲（原语先行、fail closed、任务书/白名单/停止条件/执行报告协议）。P1–P9 合计约点亮 60+ 条法术，加上 review 桶 13 条快速人工确认与直填管线后续批次，完成后 1–5 级团法术体验基本完整（"直填 ~40 条免人工"的原估计已被原型推翻，见阶段二管线实证）。P10（属性检定）/P11（召唤）/P12（变形）/P13（回合接管）为大件，放阶段二后段与职业填充交错。

#### 1C：共用原语（子职业、专长、装备，2026-10-29 新增）

需求来源（量化见"现状核查结论"A 表）：子职选择系统可用，但 12 个 SRD 代表子职的特性 id 全为幽灵（PHB2024 全量 48）；专长选择链路完整，机械闭环仅 2/76；装备武器 15/38、护甲 6/14，魔法物品为零。三个内容域的机制需求高度重叠，因此共用一套原语，不按内容域各建一套：特性声明、资源、条件、伤害 rider、反应与回合账本继续复用 1A/1B 的 owning module。内容分批、导入与许可统一归阶段二第 4 节。

多数子职/专长/装备特性是既有原语的组合：伤害 rider、资源池、temp HP（P4）、抗性（P5）、豁免 tick（P2）、多目标（P3）、法术列表与熟练项（纯数据）。为补齐新机制，先完成下列原语切片；每个切片仍以一个真实特性做端到端验证。

| # | 原语 | 代表特性 / 备注 |
|---|---|---|
| S-P1 | 暴击阈值修改（19–20、18–20） | Champion：Improved / Superior Critical；小切片、高复用 |
| S-P2 | 骰面操纵：最大化、掷后加骰、从敌方总数减骰 | Supreme Healing、Overchannel、Dark One's Own Luck、Cutting Words；统一骰迹与 RNG 顺序 |
| S-P3 | d20 检定反应中断（掷出后、结算前） | Cutting Words；扩展切片 3 反应注册表到检定窗口，区别于 P7 施法反应 |
| S-P4 | 击败/归零触发 | Dark One's Blessing；与 P4 temp HP 组合 |
| S-P5 | 防御计算修改：AC 公式、HP 上限 | Draconic Resilience（2024 无甲 AC 公式与随职业等级成长的 HP 上限）；任务书须引用 2024 原文，不沿用 2014 数值 |
| S-P6 | 光环：邻接范围被动/免疫 | Devotion 魅惑免疫、Holy Nimbus；先用 1D 距离，不提前引入 2D |
| S-P7 | 回合再生 | Survivor；逐回合时序表锁定，避免 Rage 生命周期 off-by-one 类错误 |
| S-P8 | Hide 动作与附赠动作路由 | Thief Fast Hands；同时解锁基础游荡者 Cunning Action 家族 |
| S-P9 | 受击反应攻击 | Berserker Retaliation；切片 3 反应注册表的攻击效果扩展 |
| S-P10 | 条件攻击加值（平坦值或骰式） | Archery +2、Bless +1d4；区别于 advantage/disadvantage 模式，必须进入统一攻击加值与骰迹管线 |
| S-P11 | 武器精通词条（Mastery） | 2026-10-29 物品核查补入：2024 每把武器带精通词条（武器表精通列 8 种：缓速/迅击/推离/侵扰/削弱/失衡/横扫/擦掠）；需要"特性授予 → 武器档案精通位 → 命中后效果"声明链，效果侧骑 S-P2/S-P10/P6/条件 rider；Weapon Mastery 也是 Fighter 1 级基础特性需求 |
| S-P12 | 护甲受训门控 | 2026-10-29 物品核查补入：未受训穿甲 → 力量/敏捷检定劣势 + **不能施法**；含力量要求（速度 -10）、隐匿劣势、穿脱时长；直接影响战斗引擎与施法校验（Armor Training 专长同源） |

已被推迟的大件仍按原依赖处理：属性检定 rider（Remarkable Athlete / Supreme Sneak / Peerless Skill）归 P10；Dragon Companion 归 P11 召唤；Sculpt Spells 盟友排除归阶段三后的 AoE 几何。Thief's Reflexes（首轮双回合）是独立先攻/额外回合大件，显式标为暂缓，不用假实现冒充完整支持。

##### 三书及后续扩展新增的原语需求

- **随机表**：Wild Magic 涌动。注意狂野术法已属核心 48，因此该原语在 S0 后段就需要，不推迟到三书批次；
- **骰池资源 + 效果菜单**：Battle Master 战技、Psi Warrior；优先复用切片 1/2 的资源、切片 5 的条件 rider 与 capability 选项，重点设计声明 schema，不另造结算引擎；
- **伤害吸收反应**：Spirit Shield、Song of Defense；复用切片 3 决策窗口，将 Uncanny Dodge 的乘法修正扩展为固定值/骰式减法；
- **装备运行时生成/形态切换**：Soulknife 心灵刃、Armorer 模式；这是真正的新原语，必须明确装备 canonical identity、持续期与回收，不用投影假装备；
- 传送/换位（暗影武僧等）优先复用 P6 位移原语。

##### 专长侧需求与验证载体（2026-10-29 核查）

专长与子职同构，但选择系统更完整：创建期起源专长（background/species 赠送）与升级期 advancement feat（4/8/12/16/19 级门控、可重复性、ASI 属性选择）均已有链路。PHB2024 客观清单为 **76 个专长**：起源 10、战斗风格 10、通用 44、传奇恩惠 12；不得沿用"约 50"的旧估算。

当前 `content/feats/` 的 6 个文件只代表 4 个专长（Magic Initiate 有 3 个列表变体），advancement catalog 另有 13 条声明，因此形式覆盖 17/76：

- **Magic Initiate** 已完整闭环：戏法可施、1 环每日免费一次、长休恢复、专长独立施法属性；
- **Skilled** 的技能/工具熟练选择已闭环；
- **Alert** 的 `initiative_proficiency` 字段没有任何运行时消费者，先攻熟练与先攻互换均未实现；
- **Savage Attacker** 没有机制字段；advancement 的 13 条也只有选择/门控数据，箭术、防御、巨武器、双武器 4 个战斗风格全部零效果。

专长不另建原语计划，直接作为 1B/1C 的消费者与端到端验证载体：

| 专长/机制 | 复用或新增原语 |
|---|---|
| Lucky：幸运点池、自身检定优势、敌攻击掷骰后施加劣势 | 资源池 + 优势来源注册 + S-P3（适合作为 d20 反应中断首个验证） |
| Healer：医疗包消耗、生命骰治疗、治疗骰重掷 1 | heal + 消耗品计数 + S-P2 |
| Savage Attacker、Great Weapon Fighting：伤害骰重掷 | S-P2 |
| Tough、Defense：HP 上限、AC 修改 | S-P5 |
| Archery +2、Bless +1d4 | S-P10 条件攻击加值 |
| Tavern Brawler：徒手伤害与推撞 rider | 切片 5 条件 rider + P6 位移 |
| Crafter：工具熟练、折扣、长休快速制作 | 既有熟练模式 + 经济层 + 装备运行时生成；与 Soulknife/Armorer 同族 |
| 通用专长中的属性检定、护甲训练、仪式施法 | 分别归 P10、S-P12、独立仪式施法路径；不因"专长"标签绕过所属 owning module |

#### 1D：物品原语（2026-10-29 新增）

##### 现状：三套并存的物品系统（均已核实到代码）

| 系统 | 数据 | 作用 |
|---|---|---|
| 规则集 item bundle | `content/items/` 50 文件：`item_type`（weapon/armor/shield/focus/ammunition）+ `ac_base`/`use_dex`/`dex_cap` | 规则身份：AC 派生（`character/derivation.py` 的 `derive_armor_class`）与装备资格 |
| 战斗目录武器档案 | `srd_combat_core.json` weapons 15 条：category/damage/damage_type/range/long_range/heavy/light/finesse/thrown_range | 战斗参数：攻击链（切片 1–5 依赖） |
| content-pack item | 5 类（equipment/consumable/treasure/tool/key_item），source-aware | 叙事库存：`content/rewards.py` → inventory 权威路径 |

已闭环：无甲 10+DEX、最优护甲、盾 +2、**中甲 dex_cap**、未知物品 fail-closed；Monk 武器资格；单 GP 货币（`equipment.coins_gp` → gold 投影）。**断裂点**：content-pack 物品能进背包但不能变成可装备/可挥舞的规则物品；魔法物品会卡在这条缝上。

##### 客观清单（2026-10-29 逐表清点）

- 武器 **38**（简易近战 10 + 简易远程 4 + 军用近战 18 + 军用远程 6）：仓库 **15/38**，缺 23（battleaxe、rapier、warhammer、各类弩/火器等）；
- 护甲 **14**（轻 3 + 中 5 + 重 4 + 盾）：仓库 **6/14**（缺 padded/hide/scale/breastplate/half plate/ring/splint/plate）；
- 魔法物品**约 348 条**（DMG2024 第 7 章，按 `<H6>` 条目计）：武器/护甲/药水/戒指/权杖/卷轴/法杖/魔杖/奇物 9 类，含稀有度/激活/诅咒/智能物品/神器体系；仓库为零；
- 冒险装备/坐骑载具/服务/装备包（现有 6 个）待逐表清点后补齐。

##### 基建缺口与归属

| 缺口 | 归属 |
|---|---|
| 武器词条字段 versatile/reach/loading/two-handed/ammunition | 武器档案 schema 扩展（纯数据 + 校验） |
| 武器精通词条 | S-P11 |
| 护甲受训门控/力量要求/隐匿劣势 | S-P12 |
| 多币种 CP/SP/EP/GP/PP（现为单 GP） | 数据 + 投影 |
| 弹药/医疗包消耗追踪 | 消耗品计数（Healer 专长战地医师同源） |
| attunement（绑定 3 件上限）、charges 计数、激活动作 | 新原语；engine `ACTION_KINDS` 已有 `consumable` 是切入点 |
| +N 武器/护甲效果注入 | S-P10 + 切片 5 伤害 rider 复用 |
| 药水/卷轴 | 消耗品动作 + 效果原语；卷轴走施法路径 |
| 随机宝物表（DMG 随机魔法物品/宝藏/宝石/艺术品/金属条/圣物/奥秘/武备/器具） | 随机表原语（与 Wild Magic 涌动同族） |

##### 架构边界

魔法物品效果必须是规则集侧能力（capabilities/parameters），不是 content-pack 叙事条目——避免第三套系统继续堆叠；规则集 `item` 的 source-aware 解耦（阶段二第 5 项）是第三方魔法物品接入前提。导入管线与 I0–I3 批次统一归阶段二第 4 节。

### 阶段二：内容批量填充（A 层）

1. 以切片 1–5 沉淀的 schema 为模板，把 12 职业的幽灵 id 逐个补进 `class_feature_catalog`（name / summary / parameters / capabilities）；
2. **法术填充走生成器管线**（原型已验证：`scripts/rulesets/import_dnd5eapi_spell_effects.py`，快照与产物在 git 排除的 `.imports/`）：
   - 四层：获取（dnd5eapi 2014+2024 快照，CC-BY 署名）→ 映射（2014 骨架 + 2024 补充，fail-closed）→ 核对（`.rulebooks` 2024 原文骰式 + 语义关键词三层防线）→ 分批验收（contracts + 装载测试全过、行为测试抽样、按桶分批提交）；
   - **原型实证（2026-10-29，直填桶 46 条）**：auto=0 / review=13 / manual=33——没有一条可不经人眼入库；机器的真实产出是"13 条带完整草稿的快速人工确认（补 condition/rider 字段）+ 33 条带精确原因的人工处理"（其中 16 条 P 原语等待、5 条 P2 tick/区域硬阻断）；
   - 三层防线缺一不可（原型过程实证）：无数值核对 → 2014 数值污染（cure-wounds 1d8 vs 2d8）；无语义扫描 → "数值正确的错误模式"（初版 18 条假 auto，如 hunter_s_mark 附伤被误判直伤）；无锚点检查 → API 2024 字段残缺误判（mind_spike 无 dc 字段实为 Wis 豁免）；
   - 2014→2024 差异坑实录：acid_arrow 新增回合结束延迟伤害（P2）、harm 改为降生命值上限、shining_smite 改名+施法时机改命中后、规则书保留人名前缀（Melf's）而 SRD 5.2.1 canonical id 去除（别名表处理）；
   - 语义关键词是启发式：降级方向永远保守正确，但原因标签可能不准（如光照半径误标"区域语义"），人工复核以规则书原文为准；
3. 数据源：open5e / dnd5eapi 的 CC-BY SRD 5.2 JSON，映射到 canonical ref 时逐条过 `contracts.py` 校验；
4. **内容分区与批次总表**（各内容域统一按"核心 → 经典三书 → 其它官方 → 第三方"推进；第三方批次均以第 5 项 source-aware 解耦为前提，随时可作为暂停点）：

   | 内容域 | 批次 | 范围与数据处理 |
   |---|---|---|
   | 职业特性 | 直填 | 12 职业幽灵 id 按切片 1–5 schema 逐个补齐（第 1 项） |
   | 法术 | 直填 + 管线 | 生成器管线分批（第 2 项）；56 条几何依赖归阶段三后 |
   | 子职 | S0–S3 | S0：核心 48，先做 12 个 SRD 代表子职（CC-BY 可分发），再补 36（`.rulebooks` 录入）；S1：三书剩余子职（PHB2024 已重做收编的不做 2014 版，其余按 2024 向后兼容规则标注版本语义）；S2：其它官方按需分批；S3：第三方 |
   | 专长 | F0–F3 | F0：核心 76，先用 CC-BY 源客观核对 SRD 5.2.1 清单（约 24 条，以实际清单为准）直填，其余约 52 条 `.rulebooks` 录入；F1：三书（去除已收编条目）；F2：其它官方按需；F3：第三方 |
   | 装备 | 直填 + I0–I3 | 38 武器 + 14 护甲全在 SRD 5.2.1，复用生成器管线提前直填；魔法物品 I0：SRD 子集（CC-BY 核对）；I1：DMG 全量约 348，高频优先（治疗药水、+1 武器、卷轴先行）；I2：其它官方（含 2014 同位物品）；I3：第三方 |

   统一纪律：填充必须先补效果声明与运行时消费者，不能把"可选、可展示"计为"已支持"；Alert 这类已有字段但无消费者的条目必须配行为测试。许可硬边界：SRD 5.2.1 只覆盖各内容域的代表子集，个人本地分叉可参考 `.rulebooks` 录入其它内容；若回馈上游或公开分发，只能纳入有明确许可的数据，不把 5etools 或本地规则书文本当默认分发源，CC-BY 直填与手录内容不得混标。

5. 架构解耦（独立 PR，勿与数据填充混合）：把 spells / progression / class_feature / subclass_catalog / feat / advancement_feat_catalog / 规则集 item 从硬编码 `srd_*` 改为 source-aware 目录，参照 `content/catalog.py` 已有模式与 `ContentRefChain`；第三方子职、专长与魔法物品批次以此为先决条件；
6. 仓库内明确 `LICENSE`（AGPL 代码）与内容署名（CC-BY）的分层边界。

### 阶段三：战术地图层（C 层）

选定路线：**最小 2D 升级（路线 C）**，增量演进而非一步到位。

```text
positions: {actor: int}   →   positions: {actor: [x, y]}     # theater 数轴 → 2D 坐标
MapBackground（已有）      +   token 渲染层（前端新增）
                          +   拖拽 → move intent（受移动预算校验）
```

- 1D 全部机制（距离 / 借机 / 推撞 / 预算）平移到 2D，reducer 事件模型不变；
- token 层复用现有 SSE 多人同步管线；
- 迷雾 / 墙 / 光照延后；嵌入 Owlbear Rodeo 仅作为过渡演示方案（位置权威在第三方客户端，与 EventBatch 模型冲突，不作终局）；
- 差异化方向：结合 `dnd2024/director/` 的 encounter planner，让 AI GM 获得空间权威（自动放怪 / AoE / 站位战术），这是 FVTT / OR2 不具备的能力。

## 进度跟踪

| 阶段 | 事项 | 状态 |
|---|---|---|
| 零 | 三层差距核查（内容 / 原语 / 地图） | ✅ 2026-10-28 |
| 零 | 本计划文档建立 | ✅ 2026-10-28 |
| 1A | 切片 1：Second Wind（heal 原语） | ✅ 2026-10-28 执行完成并审核通过（任务书 v3；改动在 `feat/second-wind` 工作区待提交） |
| 1A | 切片 2：Rage（持续性 condition + 引擎检查点） | ✅ 完成（任务书 v2，分支 `feat/rage`；`established` 保证轮生命周期、str 豁免优势、track 加值、物理抗性、失能结束、仅自身回合续期均已测试锁定。**实现为 `conditions` 专用条目 + 引擎内检查点，非通用 effect 实例**——effect 化、狂暴期间施法/专注限制、检定优势数据驱动归入 P1/P5 偿还；PR #612 review 修复 `53b715e1` 补齐抗性与回合行为） |
| 1A | 切片 3：Uncanny Dodge（触发泛化 + 伤害修正窗口） | ✅ 完成（任务书 v2，分支 `feat/uncanny-dodge`；`hit_reaction` 决策泛化到特性选项、伤害减半、AI 自动选择、人类玩家按钮均已测试/构建锁定） |
| 1A | 切片 4：Action Surge（economy 授予） | ✅ 完成（任务书 v2，分支 `feat/action-surge`；`economy.granted` 通用授予事件、surge_action 独立经济 key、once_per_turn 通用字段、支付优先级、施法结构性排除均已测试锁定） |
| 1A | 切片 5：Sneak Attack（谓词伤害） | ✅ 完成（任务书 v1，分支 `feat/sneak-attack`；武器/优势/盟友谓词、extra_dice_formulas 多附加骰 AST、feature_usage 每回合账本、决策链延迟消耗均已测试锁定） |
| 1B | 法术原语切片 P1–P9（矩阵见 `design/SPELL_GAP_MATRIX.md`） | ✅ 已完成（P1–P9 全部执行完成并审核提交，2026-10-30；法术/特性共用底座 P1–P5 优先落实。任务书：`design/P1_INVISIBILITY_TASK_SPEC_2026-10-29.md`、`design/P5_STONESKIN_TASK_SPEC_2026-10-29.md`、`design/P2_HOLD_PERSON_TASK_SPEC_2026-10-29.md`（v1.1）、`design/P3_SCORCHING_RAY_TASK_SPEC_2026-10-29.md`（v2）、`design/P4_FALSE_LIFE_TASK_SPEC_2026-10-29.md`（v2）、`design/P6_MISTY_STEP_TASK_SPEC_2026-10-29.md`、`design/P8_DISPEL_MAGIC_TASK_SPEC_2026-10-29.md`（v1.1）、`design/P7_COUNTERSPELL_TASK_SPEC_2026-10-29.md`（v1.1）、`design/P9_REVIVIFY_TASK_SPEC_2026-10-29.md`（v1.1）） |
| 1B | P1：invisibility（attack_sources 数据驱动 + 升环目标数 + 专注法术提前终止） | ✅ 2026-10-29 执行完成并审核通过（任务书 v1 + 执行报告；`feat/p1-invisibility` WIP 原型融合入本分支，借机攻击事件序交叉已修并补测） |
| 1B | P5：stoneskin（伤害类型抗性框架 + 偿还 Rage 抗性债） | ✅ 2026-10-29 执行完成并审核通过（任务书 v1 + 执行报告；抗性来源 = 注册表静态声明 ∪ condition 元数据动态声明，`_resist_damage` 集合判成员保证不叠加；Rage 硬编码减半特判移除、改注册表驱动，行为等价由既有测试锁定；免疫/易伤按 §1.2 裁剪，挂钩点已预留） |
| 1B | P2：hold_person（失能族行动门控 + 每回合豁免 tick） | ✅ 2026-10-29 执行完成并审核通过（任务书 v1.1 + 执行报告；四项通用原语——`condition_flags` 单点聚合失能族后果、`save_repeat` 每回合重复豁免、`auto_fail_saves` 免掷骰失败、`target_creature_types` 生物类型限定；sleep 同步获得重复豁免与真实失能后果；prone/石化/睡眠第二阶段按 §1.2 裁剪） |
| 1B | P3：scorching_ray（多 beam 重复目标结算） | ✅ 2026-10-29 执行完成并审核通过（任务书 v2 + 执行报告；`repeat_targets` 开启重复 target_ids、`target_count` 读作射线数上限、升环复用 P1 `upcast_target_count`；审核推翻 v1 两条假前提——批次内逐 beam 条件消耗与法术攻击 bless，均按 RAW 补齐） |
| 1B | P4：false_life（临时生命值原语） | ✅ 2026-10-29 执行完成并审核通过（任务书 v2 + 执行报告；`temp_hp` 效果模式 + `upcast_flat` 固定升环加值、reducer 端 keep-max 与伤害优先吸收；任务书 v1 升环举例错一级由审核按规则书原文修正，实现自始正确） |
| 1B | P6：misty_step（传送原语 + 速度修改数据驱动） | ✅ 2026-10-30 执行完成并审核通过（任务书 v1 + 执行报告；`teleport` 模式产出 forced 位移事件——不扣移动力、不受剩余移动力限制、不触发借机攻击、零 RNG；`speed_modifier` 注册表化 + `effective_speed` 纯函数偿还 `slowed_10` 硬编码债，P2 推迟的 `speed_zero` → economy movement=0 投影一并落地） |
| 1B | P8：dispel_magic（魔法 condition 移除 + 法术来源快照） | ✅ 2026-10-30 执行完成并审核通过（任务书 v1.1 + 执行报告；`dispel` 模式逐 condition 判定——≤ 施放环阶自动终止、更高环阶施法属性检定 DC 10+来源环阶；`condition.applied` 来源快照 `source_spell_ref`/`source_spell_level`；专注法术整道终止走既有 `concentration.ended` 级联，非专注单目标 `condition.removed`；任务书 v1 三处自身错误由审核修正，实现自始正确） |
| 1B | P7：counterspell（施法反应窗口：打断施法） | ✅ 2026-10-30 执行完成并审核通过（任务书 v1.1 + 执行报告；`cast_spell` 先探 `_counter_spell_window`——非 triggered_damage/reaction_defense、带 V/S/M 任一成分、60 尺内存活敌人 `counterspell_dc>0` 且有反应即挂起 GM 决策（`cast_intent` 服务端深拷贝；decline/豁免成功经 `_spell_events` 原样重放）；counter 消耗敌方反应 + 施法者 con 豁免（bless d4、`auto_fail_saves` 免掷骰），失败只耗动作/附赠动作、法术位与免费施放资源不消耗，收尾审计事件 `dnd2024.spell.countered`；`reducer.py` 白名单缺口由审核端裁决以 v1.1 §3.7 单行授权扩入——忽略集注册、纯审计零状态） |
| 1B | P9：revivify（复活原语 + dead 目标校验反转） | ✅ 2026-10-30 执行完成并审核通过（任务书 v1.1 + 执行报告审核端代录；`revive` 模式只发 `character.revived`（hp=1）事件——状态语义由 GM 复活路径共用的既有 reducer 处理器承载，零 RNG、不发 `resource.changed`、不经 healing 管线；validation 盟友集合追加 + dead 检查模式分支反转（revive 要求 dead 且 player-only，其它有益模式对 dead 仍拒绝）+ hp≤0 豁免；复活家族路线经 v1.1 修正——revivify 是唯一战斗内成员，raise dead / reincarnate / resurrection / true resurrection 走探索层 `exploration.cast_spell` 各自立项，revive 事件原语全家族复用） |
| 1B 补充 | R1：复活家族探索层入口（revivify 战斗外 + 家族空响止血） | ✅ 2026-10-30 执行完成并审核通过（任务书 v1.1 + 执行报告；源起 P9 后体验审查——场合 B 战斗外 revivify 硬拒绝、场合 C 家族法术叙事兜底扣位空响，用户裁定分界线：确定性结算机械化、事实裁定归 GM。`revive_effects.py` 语义表五法术唯一数据源 + `is_fully_implemented` 整体判定——未实现语义整法术显式拒绝、不扣位、可读消息，绝不静默降级；revivify 战斗外完整闭环（`spell.cast` → `character.revived`，探索层 `_reduce_event` 镜像 P9 四状态语义）；raise_dead/resurrection 待 SP10 D20 聚合与长休衰减、reincarnate 待角色重建、true_resurrection 唤名永久 GM 通道） |
| 1C | 共用原语 S-P1–S-P12（子职 / 专长 / 装备） | 🔶 12 份任务书已写齐、待执行（2026-10-30 完成，代码事实基线 HEAD `4ef1d33d`；核心 12 子职与专长需求已提出，P10/P11/AoE 依赖仍按原阶段执行。S-P6/S-P11 部分锚点位于 P7/P8 在途改动偏移区，建议排在 P7/P8 合入后执行，勿打断法术原语推进；S-P9 承接 SP3 §8 登记的受击反应注册表化迁移债销账。任务书：`design/SP1_IMPROVED_CRITICAL_TASK_SPEC_2026-10-30.md`、`design/SP2_DIE_MANIPULATION_TASK_SPEC_2026-10-30.md`、`design/SP3_D20_CHECK_WINDOW_TASK_SPEC_2026-10-30.md`、`design/SP4_DARK_ONES_BLESSING_TASK_SPEC_2026-10-30.md`、`design/SP5_DRACONIC_RESILIENCE_TASK_SPEC_2026-10-30.md`、`design/SP6_AURA_PASSIVES_TASK_SPEC_2026-10-30.md`、`design/SP7_SURVIVOR_TASK_SPEC_2026-10-30.md`、`design/SP8_HIDE_AND_BONUS_ACTION_TASK_SPEC_2026-10-30.md`、`design/SP9_RETALIATION_TASK_SPEC_2026-10-30.md`、`design/SP10_CONDITIONAL_CHECK_BONUS_TASK_SPEC_2026-10-30.md`、`design/SP11_WEAPON_MASTERY_TASK_SPEC_2026-10-30.md`、`design/SP12_ARMOR_TRAINING_GATING_TASK_SPEC_2026-10-30.md`） |
| 1D | 物品原语（attunement / charges / 激活动作 / 随机宝物表） | ⬜ 未开始（+N 效果骑 S-P10 与切片 5 rider；现状核查 2026-10-29 完成） |
| 二 | 职业特性批量填充（幽灵 id → catalog） | ⬜ 未开始 |
| 二 | 法术批量填充管线（原型已验证，review 13 + manual 33 待处理） | 🔄 原型完成（`scripts/rulesets/import_dnd5eapi_spell_effects.py`） |
| 二 | 子职分区 S0–S3（核心 48 → 三书 → 其它官方 → 第三方） | ⬜ 未开始（S0 先做 12 个 SRD 代表子职；S3 以 source-aware 解耦为前提） |
| 二 | 专长分区 F0–F3（核心 76 → 三书 → 其它官方 → 第三方） | ⬜ 未开始（现状形式覆盖 17、闭环 2；F0 先核对 SRD CC-BY 清单） |
| 二 | 非魔法装备直填（23 武器 + 8 护甲 + 词条字段，SRD CC-BY 管线） | ⬜ 未开始 |
| 二 | 魔法物品分区 I0–I3（DMG 全量约 348，高频优先） | ⬜ 未开始 |
| 二 | spells / progression / class_feature / subclass_catalog / feat / advancement_feat_catalog / 规则集 item source-aware 解耦 | ⬜ 未开始 |
| 二 | 许可分层声明（AGPL 代码 / CC-BY 内容） | ⬜ 未开始 |
| 三 | positions 升级 2D + token 渲染层 | ⬜ 未开始 |
| 三 | 拖拽 → move intent 交互 | ⬜ 未开始 |
| 三 | 迷雾 / 墙 / 光照（远期） | ⬜ 未开始 |
| 三后 | 几何 AoE / zone 管线（56 条法术依赖，见矩阵） | ⬜ 未开始（zone 按"位置区间"抽象可与 1D 共用） |

状态图例：⬜ 未开始 / 🔶 进行中 / ✅ 已完成（完成后附 PR 或 commit 链接）。

### 切片 1 审核记录（2026-10-28）

- 验收复跑通过：`test_dnd2024_class_features.py` 33 passed；`-k second_wind` 11 passed；两文件合计 61 passed。
- 白名单合规：改动仅限任务书 §3 列出的 7 个文件 + 报告豁免项；`rest_catalog`、`features_by_level`、resolver、validation、reducer、`frontend-v2/` 均未触碰。
- 执行偏差（已接受）：两条既有用例（`test_non_monk_characters_never_own_monk_features`、`test_non_monk_never_gets_monk_capabilities`）由"非 monk 角色无任何特性"收窄为"不含 monk 特性"（`isdisjoint`）。fighter 获得首个非 monk 特性后原断言前提失效，收窄保持原语义、未放宽新行为。
- planner 侧修正：catalog `summary` 照抄了任务书 v1 模板的过时表述（PB 曲线 + 仅长休），已按 v3 规则语义改为固定次数表（2/3/4）+ 短休恢复 1。后续任务书修订时模板与 §1 语义必须同步。
- 基线备忘（对后续切片的任务书要求）：`tests/rulesets/test_dnd2024_character_builder.py` 存在 11 个上游既有失败（10 × `unresolved reference 'ability:con' in background:soldier`，1 × 404），已用 HEAD worktree 对照证实与本切片无关。后续任务书的"基线全绿"条款应将其列为已知豁免。

### 切片 2 审核记录（2026-10-29）

- 验收复跑通过：`test_dnd2024_rage.py` 15 passed + `test_dnd2024_class_features.py` 44 passed（合计 59）；全仓库 `-k rage` 53 passed；rulesets 全量 11 failed 均为已知上游豁免。
- 白名单合规：9 个白名单文件 + 报告豁免；progression/rest catalog、resolver、view、`frontend-v2/` 零触碰。
- 执行端自我纠正（已接受）：任务书 §3.8.5 数值错误（5 级 +3，实际 1-8 级均 +2）按 track 事实实现；缺宿主法术问题以内存改 `sacred_flame` 的 save 属性解决（bundle 副本不落盘）。
- 偏离 1（方法归属，已接受）：任务书把 `_available_class_capabilities` 指向 engine.py，实际定义在 view.py 的 mixin（view.py 是停止条件 6）。执行端在 engine 同名覆写（super() + 条件过滤），view.py 零改动，两个调用点语义正确。
- planner 侧修正（审核中发现，已修）：**生命周期 off-by-one**——v1 规格"回合结束未续期即移除"把检查点提前到进入回合，违反规则"持续至你的下个回合结束"的第一轮保证。修正为 `established` 两段式：进入回合结束只确立（标记 + 清续期标记、不递减不移除），下个回合结束起才检查续期。连带 `condition.applied` 载荷、测试 6/7/8/9 断言。修正后昏迷联动测试才首次真正测到 reducer 的 pop 路径（原实现里 rage 在回合结束已死，该断言空转）。
- 教训（对后续任务书的要求）：**生命周期类规格必须用逐回合时序表对照规则原文推演**（N 进入 → N 结束 → N+1 行动 → N+1 结束），再落笔实现规格；数值型断言（等级/加值）必须直接引用 §2 契约表的 track 值，不得凭记忆另写。

### 切片 3 审核记录（2026-10-29）

- 验收复跑通过：`test_dnd2024_uncanny_dodge.py` 7 passed + `test_dnd2024_class_features.py` 47 passed（合计 54）；rulesets 全量 11 failed 均为已知上游豁免；前端 `npm run build` 成功。
- 白名单合规：后端 6 文件 + 测试 2 文件 + 报告豁免；随后按审核决定扩入前端 3 文件（见下条）。
- **审核发现的真实缺陷（planner 侧规格错误，已修）**：v1 §6 禁止改动 `frontend-v2/`，但既有 `hit_reaction_spell` 决策卡只渲染"施放法术"与"放弃"两个按钮。游荡者没有法术时 `reactionSpells` 为空，"施放"按钮消失，人类玩家只剩"放弃"——**特性对真实玩家完全不可用**，只有 AI 托管/GM 代选能走通。已扩白名单补前端（`types.ts` 接口、决策卡按钮、五语种文案），构建通过。
- 执行端三处偏离（均已接受）：
  1. catalog 省略 `resource_id: "reaction"`——resolver `_validate_declarations` 要求 `resource_id` 必须在 rest_catalog 声明，而 reaction 是战斗期资源（§6 禁止写入 rest_catalog），保留该字段将导致整个规则集装载失败；
  2. capability 成本用 `cost.resources` 表达——裸 `cost.reaction` 不被 `ACTION_COST_KINDS` 识别，会被静默忽略成"零成本"能力而暴露为可主动提交的动作；
  3. `resolution.py`/`validation.py`/`engine.py` 零改动——`_decision_events` 已把全部 `HIT_REACTION_DECISION` 路由到 `_finish_hit_reaction`，就地扩展即可；`_automatic_hit_reaction` 实际归属 `reaction_spells.py` 而非 engine.py。
- 教训（对后续任务书的要求）：**"服务端已验证"不等于"玩家可用"**——凡新增玩家可选择的行为，任务书必须显式回答"哪个人类玩家的哪次点击会触发它"，并据此决定前端是否在白名单内。后续 Action Surge（经济事件）、Sneak Attack 同样需要前端入口，写任务书时一并确认。

### 切片 4 审核记录（2026-10-29）

- 验收复跑通过：`test_dnd2024_class_features.py` 51 passed；`-k action_surge` 11 passed；rulesets 全量 480 passed + 11 failed 均为已知上游豁免（`test_dnd2024_character_builder.py`）。
- 白名单合规：8 个白名单文件 + 报告豁免。另有 4 个前端文件与主计划被 `git status` 标记 M，逐一 `git diff --stat` 核实为零内容差异（CRLF/LF 行尾噪音）与本会话 planner 更新，非执行端越权。
- 三条硬性语义全部锁定：**施法结构性排除**（surge_action 不在施法支付域，伪造 cast_spell intent 被拒）、**短休/长休恢复**（rest_catalog 既有声明零触碰）、**每回合一次**（once_per_turn flag 经 `_fresh_economy` 整字典替换天然重置，回合推进即恢复；17 级双资源场景已测）。
- 执行端两处偏离（均已接受）：①攻击支付的实际事件类型是 `dnd2024.attack.spent` 而非通用 action.spent，`resource` 字段落点在 engine.py `_attack_cost_event`（任务书 v1 误指 `_attack_events`）；②`_stabilize_events` 同步改支付选择——stabilize 走独立事件构建路径，任务书 v1 遗漏，不改则 surge 后 stabilize 被拒。
- 架构质量：`economy.granted` 是通用授予事件（grants/flags 双载荷 + `_GRANTABLE_ECONOMY` 白名单校验 + 未知 key/负值/bool fail-closed），非 action_surge 专用分支；`once_per_turn` 是 FeatureCapability 通用字段，切片 5 Sneak Attack 可直接复用。
- 切片 3 教训闭环：任务书 v1 正面回答了"哪个人类玩家的哪次点击"——surge 走既有 capability 按钮，surge 后 attack 等按钮因门控放宽重新出现在投影中，前端零改动成立。
- 教训（对后续任务书的要求）：**支付/经济类规格必须先枚举同一支付域的全部消费路径**（attack、basic actions、stabilize、施法各走独立事件构建），逐路径声明改不改、怎么改；本切片靠执行端补全 stabilize 路径，属于任务书规格漏项。

### 切片 5 审核记录（2026-10-29）

- 验收复跑通过：`test_dnd2024_sneak_attack.py` 21 passed，三目标文件合计 83 passed；全仓库 `-k sneak_attack` 26 passed；rulesets 全量 507 passed + 11 failed 均为已知上游豁免（`test_dnd2024_character_builder.py`），planner 独立复跑核实一致。
- 白名单合规：11 个白名单文件 + 报告豁免；progression/rest catalog、resolver、validation、view、engine、`frontend-v2/` 零触碰（另有前端 3 文件与 `docs/internal/README.md` 的 M 标记经 `git diff` 核实为零内容差异行尾噪音，主计划 M 为 planner 侧规划改动，非执行端越权）。
- 任务书 §1.1 六条规则边界全部测试锁定：盟友按攻击者阵营并按到目标的距离判定（攻击者本人不算）；优劣势相消（`""` 模式）仍可走盟友路线；投掷非灵巧近战武器不触发（不看 `ranged_use`）；偷袭骰并入武器伤害单事件（被 Uncanny Dodge 一并减半）；Shield 改判 miss 不消耗每回合一次；"每回合"≠"每轮"（敌人回合借机攻击可再用，账本记 round+turn_index 归属攻击者）。
- 执行端三处偏离（均已接受）：①借机攻击决策的威胁方攻击档案——enemy 仍走原 `attack_id` 路径（字节不变），player/companion 走 `_available_attacks` 的 `weapon_ref`（case 13 真实 move→decision.resolve 链的必要条件）；②Shield/Uncanny Dodge 同阵营受击用任务书 §3.8 授权的构造例外（生产 offer 不面向同阵营）；③companion 代替第二玩家系任务书明文允许。
- 架构质量：`feature_usage` 是按攻击者 + round/turn_index 的战斗级通用账本，不读当前行动者经济；`dnd2024.feature.used` 对同 actor 同回合同 feature 重复提交 fail-closed，`setdefault` 兼容旧存档免迁移；`attack_damage_node` 的 `extra_dice_formulas` 有序列表让 Hex/偷袭共用一条伤害 AST，重击翻倍全部骰节点而属性修正保持常量；延迟 `feature_use` 元数据只在最终落地伤害路径提交（`_submit_hit_damage` 统一收口全部提交点）。
- 切片 3 教训闭环：任务书 §1.2 的自动附加策略结构性回答了"哪个人类玩家的哪次点击"——满足谓词的第一次合法命中自动附加偷袭，复用既有 Attack 按钮与借机攻击决策卡，前端零改动成立；账本键在任务书中前置约束，经济域零偏离。

### P1 审核记录（2026-10-29）

- 验收复跑通过（WIP 分支 `feat/p1-invisibility`）：`test_dnd2024_invisibility.py` 16 passed + 相邻六文件 91 passed + rulesets 全量 495 passed / 39 deselected（`character_builder` 已知上游豁免），planner 独立复跑与执行报告一致；融合入本分支后（含 `53b715e1` 修复）invisibility + combat 47 passed，全量另见本次提交前复跑。
- 白名单合规：9 个实现文件（src 7 + templates 1 + tests 1）+ 任务书执行报告豁免；WIP 提交混入的 9 份规划文档（P2–P9 任务书与主计划 1A–1D 重组）已剥离，不进 feat 提交，留待后续切片各自携带。
- 任务书 §1.1 八条规则边界全部测试锁定：相消语义零改动（`mode_from_sources` 未动，#13/#14 直测 RAW"多劣一优相消"）；`invisible` 双向声明（#3 目标侧劣势取低骰、#4 攻击者侧优势取高骰）；感官抵消按感知方且只豁免对应方向（#9/#10）；提前终止是整道法术（#4 断言施法者 concentration 清空、非行动目标身上的 condition 一并移除）；攻击检定即触发与命中无关（#4 参数化 miss 用例）；重刷快照语义（#6 恰一次 ended、新 condition 存活）；blur 与 invisible 两份独立 `negated_by` 清单（#11）；`next_attack_*` 消耗语义数据化后行为等价（#12）。
- 执行端五项偏离均为测试写法（见任务书执行报告），已接受；执行报告无 §8 之外的文档产出。
- **融合时审核发现的真实缺陷（已修）**：借机攻击结算 `opportunity[0] = reaction.spent` 按列表位置替换支付事件，与 P1 的"法术提前终止事件插在列表头部"在同一位置交叉——隐形威胁方进行借机攻击时其 `concentration.ended` 会被反应支付覆盖丢失。WIP 分支不含 `53b715e1` 的借机攻击重构且测试未覆盖该场景，故未暴露。修复：按 `dnd2024.attack.spent` 事件类型定位替换，并补 #4b 用例（隐形威胁方借机攻击 → 提前终止与反应支付双存且序正确）锁定。
- **融合时第二交叉（测试适配，已修）**：`53b715e1` 的 `test_an_invisible_attacker_cannot_be_dodged` 注入 `invisible` 时该 condition 尚无攻击模式语义；P1 后隐形攻击者的攻击具有优势（双骰），原两骰序列被攻击双骰耗尽、伤害骰落到 SequenceRng 耗尽 fallback（d6 取 1），伤害 3≠6。测试意图（隐形攻击者不触发 Uncanny Dodge、伤害全额）不变，roll 序列补为三骰 `[15, 8, 4]` 适配优势取高。
- 架构质量：condition → 攻击模式注册表是目录数据（`side`/`mode`/`negated_by`/`consume_on`，逐字段 fail closed），`check_modes` 不再硬编码来源清单，`gm_grant`/`long_range` 按任务书保持代码路径；`ends_on` 只由服务端从目录效果拷贝进 `condition.applied` 载荷，客户端不可声明；`upcast_target_count` 是通用升环计数（bless 行为不变）；`_spell_end_trigger_events` 统一四个调用点（武器攻击 / 施法 / Shield 反应 / Divine Smite）且读预结算视图，`damage_dealt` 按任务书保留为数据位无独立调用点。
- 切片 3 教训闭环：施法走既有 cast 界面（`target_ids` 多目标校验已有）、优劣势自动进 check 投影，前端零改动成立。
- 教训（对后续切片的要求）：**凡改动事件列表结构（插入/重排），必须 grep 所有按位置消费该列表的代码**——本次交叉正是"位置耦合"被两批独立改动同时命中，单侧分支的测试无法暴露；后续切片在整合分支上融合后必须重跑受影响套件而非沿用 WIP 分支的测试结论。
- 遗留提醒（不阻塞本切片）：①整合分支主计划 1B 行引用的 `SPELL_GAP_MATRIX.md` 与 `scripts/rulesets/import_dnd5eapi_spell_effects.py` 来自原始线 `d134f4be`，尚未搬运到本分支，引用悬空；②`53b715e1` 的 Uncanny Dodge"必须看见攻击者"检查未考虑防御者持 `see_invisibility` 的抵消（RAW 下持识破隐形者可对隐形攻击者反应），归后续感官切片统一处理。

### P5 审核记录（2026-10-29）

- 验收复跑通过（整合分支工作树）：`test_dnd2024_stoneskin.py` 13 passed；回归六文件（rage/combat/spells/uncanny_dodge/smite/invisibility）100 passed，与执行报告一致；rulesets 全量复跑见提交记录。
- 白名单合规：6 文件全部命中 §3 白名单（`catalog.py` / `validation.py` / `resolution.py` / `srd_combat_core.json` / 新增 `test_dnd2024_stoneskin.py`）+ 任务书执行报告豁免；无白名单外变更。
- 任务书 §1.1 九条规则边界全部测试锁定：减半一次（#6 rage+stoneskin 仍 7→3）；向下取整（#2 奇数 7→3）；骰迹不动（#2 `rolls == [5]`）；专注 DC 用扣抗后伤害（#4 44→22、DC 11，原始值会得 DC 22 的反证成立）；击败判定同变量链路；豁免减半与抗性独立相乘（#10 11→5→2，inflict_wounds 目录条目确为 saving_throw+half_on_success 已核实）；治疗不经抗性（#12）；修正→抗性顺序（#11 pending 事件已持 3、Uncanny Dodge 决策结算再半得 1，两步减半可交换）；三类 actor（#2 player、#9/#10 enemy 元数据抗性）。
- **Rage 抗性债偿还方式（核心裁定：接受，偏离任务书 §1"不改 Rage 引擎代码"）**：`53b715e1` 在武器攻击路径留下的 `"rage"` 硬编码减半特判被移除，改由注册表条目驱动——属"删特判"而非新增 Rage 路径，正是本计划 Rage 行预告的"数据驱动归入 P5 偿还"。行为等价核实：旧 `int(damage * 0.5)` 与新 `damage // 2` 对非负整数一致（旧 `halved != damage` 仅在 0 时不打标记，新 `amount <= 0` 短路同理）；`resistance == "rage"` 既有事件契约保留（`_resist_damage` 返回来源 id，偏离 §3.4.A 的 bool 签名但守住已发布契约）；`test_dnd2024_rage.py` 原样全过。
- 交叉排查（P1 教训执行）：①`condition.removed` 插入 buff 事件序——reducer 消费方按事件类型处理、schema（target_id/condition）与既有发射点一致，runtime 事件白名单已含该类型，无位置耦合消费方，#8 显式锁定 removed 先于 applied；②`_resist_damage` 与旧 rage 特判均不消耗 RNG，SequenceRng 序列零漂移；③`resisted`/`resistance` 新增事件键——`frontend-v2/src` 全量 grep `resist` 零命中，§7.3 停止条件未触发。
- 执行端七项偏离全部裁定接受（详见任务书执行报告 §C）：#1 签名 (int, str)；#2 即上述 Rage 特判移除；#3–#5 为测试写法（fire bolt 对石肤目标机制不可达、magic missile/inflict_wounds 无匹配抗性来源，向敌人注入元数据抗性覆盖 automatic_damage/saving_throw 路径——元数据仅测试可写，生产端只能由 `resist_choice` 施法写入）；#6 治疗期望 12（7 级智力 +3）；#7 fire_shield `range: 5` 为任务书原样。
- **审核发现：第五伤害构造点（§7.2 场景，executor 未报告，planner 裁定推迟）**：`triggered_spells.py` L206（Divine Smite 的 triggered_damage）自建 hp 事件、未接 `_resist_damage`。今日休眠——smite 为 radiant，现有全部抗性来源（物理/毒素/五元素选择/火焰护盾）无 radiant，行为零偏差；接入归触发/反应注册表扩展切片（fire_shield 反伤 rider 同批），届时补"抗性 vs 触发伤害"测试。归因：任务书 §2 契约表漏列该点，属 planner 侧规格不完备，非执行侧违规。
- planner 侧遗留裁定：fire_shield RAW 为"自身"，但 catalog 校验强制 `5 <= range <= 600`，"自身"编码（放宽下限或引入 self 语义）是首个自身 buff 落地后的待定设计点（P4 false_life 会再遇到），归后续数据批次统一决策，本切片按任务书原样保留并记录。
- 架构质量：抗性来源两处服务端权威（注册表静态声明 ∪ condition 元数据动态写入），集合判成员保证不叠加；`resist_type` 双向 fail closed（声明必选、未声明必拒，#7 双向锁定）；`energy_resistant`/`fire_shield_resistant` 为纯元数据条件（注册表不强制每个 condition id 有条目，`see_invisibility` 同款先例）；`removes_conditions` 只由服务端目录声明触发。
- 遗留（执行报告 §D 已登记，均不阻塞）：免疫/易伤挂钩点在 `_resist_damage` 结构上预留；fire_shield 2d8 反伤 rider 待触发注册表扩展；protection_from_poison 中毒豁免优势待豁免优势来源原语；多源同类型时 `resistance` 来源名依赖 conditions 迭代序（`resisted`/`amount` 不受影响）。

### P2 审核记录（2026-10-29）

- 验收复跑通过（整合分支工作树）：`test_dnd2024_hold_person.py` 17 passed；回归七文件（combat / spells / smite / uncanny_dodge / monk_combat / invisibility / stoneskin）119 passed，与执行报告逐项一致；rulesets 全量复跑见提交记录。
- 白名单合规：8 个改动文件 + 新增测试文件全部命中 §3 白名单（`srd_combat_core.json` / `catalog.py` / `check_modes.py` / `validation.py` / `resolution.py` / `engine.py` / `reaction_spells.py` / `dnd2024_monk_common.py` / 新增 `test_dnd2024_hold_person.py`）+ 任务书执行报告豁免；无白名单外变更。
- 任务书 §1.1 十条规则边界全部测试锁定：①失能只禁动作不禁移动（#4 禁 attack/cast、move 报 speed zero；#5 纯 `incapacitated` 5 尺 move 合法、超速仍被既有距离门拒）；②`death_save` / `end_turn` 不在门控集合；③重复豁免归属目标回合且成功不发 `concentration.ended`（#6 断言事件序 check→removed→turn.advanced、无 ended、施法者专注保留；#7 失败保留）；④DC 为施放当刻快照（#2 元数据 `save_repeat`）；⑤自动失败不掷骰（#10 `RecordingRng` d20 请求数为 0、`rolls == []`、全额伤害 9）；⑥自动重击要求命中（#11 natural 15 非 20 升级且伤害骰翻倍、#11b 30 尺不升级、#12 法术攻击同款）；⑦失能打断目标自身专注（#13 `ended(actor_id=goblin, reason=incapacitated)` 且 blessed 级联移除）；⑧失能族不进回合跳过清单；⑨类人限定按目标 `creature_type`（#9 beast 被拒）；⑩伤害不解除麻痹（无对应事件路径）。
- 交叉排查（P1 教训执行）：①`_end_turn_events` 全仓库仅 `engine.py` L650 与 `resolution.py` L1046 两个调用方，§7 停止条件 3 未触发，两处均已传 rng；②新增 check.resolved 键（`rolls: []` / `natural: 0` / `auto_failed` / `repeat_save`）——服务端消费方均为直通（runtime 白名单按事件类型而非键），`frontend-v2/src` 对三键零命中，既有渲染取 `event.total ?? event.natural`，自动失败渲染为 0 但不抛错，§7 停止条件 1 未触发；③`condition_flags` 无引擎依赖、无 RNG 消耗，校验 / 结算 / 反应三处共用同一聚合，无重复实现。
- 架构质量：四项原语全部数据驱动——失能族后果由注册表四键声明并经 `condition_flags` 单点聚合，引擎内无 condition 名分支（§6 禁止项 8 合规）；`save_repeat` / `target_creature_types` 只能来自目录效果，客户端不可写（禁止项 10）；`_fresh_economy` 未动（禁止项 9）；catalog 的"仅声明被动标志"条目分支沿用 P5 的 resist-only 先例。
- 执行端偏差全部接受（详见执行报告 §C）：5 级法师替代建议的 3 级（#8 需 3 环位）、#11/#12 拆为独立战斗实例、#10 选 `burning_hands` 并用 `RecordingRng` 记录请求区间、fire bolt 2d10 为既有 cantrip 升级、sleep 近似按 §1.2 保留。
- 值得记录的连带效果：`unconscious` 注册表条目对 0 HP 玩家立即活效（reducer 直写 `conditions["unconscious"]`），使"5 尺内攻击倒地玩家自动重击"从无变为有——RAW 正确且任务书 §2 已预告，全量复跑无既有测试依赖旧行为。
- 遗留（执行报告 §E 已登记，均不阻塞）：sleep 第二阶段（二次失败转 unconscious、伤害 / 摇醒终止、免疫清单）；prone 与倒地距离条件优劣势；先攻劣势与惊喜轮；`available_intents` 未按失能过滤；失能单位自动 `end_turn` 代点未实现；速度归零的 economy 投影仍走 `_fresh_economy`。

### P3 审核记录（2026-10-29）

- 验收复跑通过（整合分支工作树）：`test_dnd2024_scorching_ray.py` 11 passed；回归八文件（combat / spells / smite / uncanny_dodge / monk_combat / invisibility / hold_person / stoneskin）136 passed；rulesets 全量 549 passed / 39 deselected（1897.55s；P2 基线 538 + 本切片 11，`character_builder` 已知上游豁免）。
- 白名单合规：6 个改动文件（`srd_combat_core.json` / `catalog.py` / `validation.py` / `resolution.py` / `engine.py` / 新增 `test_dnd2024_scorching_ray.py`）+ 任务书执行报告豁免；无白名单外变更。
- **审核推翻任务书 v1 两条假前提（核心裁定：按 RAW 补齐，不迁就现状）**：①§1.1 #4 断言"`_consume_attack_conditions` 每 beam 调用一次即逐 beam 消耗"——实测三道射线全部优势、并产生 3 份重复 `condition.removed`（一次施法只产出一个事件批次，状态在 `apply_batch` 才落地）；§1.1 #4 断言"bless 对每道射线攻击掷骰生效"——`spell_attack` 分支此前完全不吃 bless（只有武器攻击与豁免分支有）。两处均按原文补齐：批次内局部隐藏已消耗条件、法术攻击逐 beam 投 1d4、专注豁免补 1d4。
- 执行端 v1 未按 §7.1 停止报告，而是只写 7/9 测试项并跳过 §8 执行报告；v2 已补 #7/#10/#11、删除不可达的 #9、补齐报告。属执行流程违规，已记录在任务书 §8-C。
- 规则边界测试锁定：三道射线集中同一目标 = 三份独立 check + 三份 2d6（#2）；分散三目标各一份（#3）；重复 id 仅 `repeat_targets` 合法（#4a/#4b）；3 环位 4 道 ok / 5 道拒（#5）；中间 beam 单独重击只翻倍自身骰（#6）；优势只覆盖第一道且 condition 恰移除一次（#7）；少射宽容（#8）；bless 逐 beam 加骰（#10）。
- 交叉排查（P1/P2 教训执行）：①`condition.removed` 去重后同一批仅一份，reducer 按事件类型消费、无位置耦合；②`check.resolved` 的 `bless_bonus` 是既有投影白名单允许键（`runtime.py`），前端 `CombatLiveBar.vue` 已按 `event.bless_bonus` 叠加，新增字段零消费方风险；③RNG 序列仅在"施法者/专注者持 blessed"时新增一枚 d4，既有测试零依赖（全量复跑证实）。
- 已知不可达 / 遗留（执行报告 §D 已登记，均不阻塞）：①敌人视图硬编码 `concentration: None`，敌方施法者专注模型缺失，矩阵 #9（专注逐伤害事件）删除；②`_last_hostile_defeated` 逐 beam 用批次前 HP 快照，多 beam 累计击杀时本批不发 `dnd2024.combat.ended`（reducer 标 `defeated`、回合结束补发胜利）；③前端仍单目标提交 `target_ids`，多道射线编排是前端任务（§1.2 裁剪）；④`target_count` 在 `repeat_targets` 下的"射线上限"重载语义未在 schema 层固化。

### P4 审核记录（2026-10-29）

- 验收复跑核实（执行端回报，全量数字与 P3 基线吻合）：`test_dnd2024_false_life.py` 12 passed；回归八文件 134 passed；rulesets 全量 561 passed / 39 deselected（2434.84s；P3 基线 549 + 本切片 12，`character_builder` 已知上游豁免）。
- 白名单合规：6 个改动文件（`srd_combat_core.json` / `catalog.py` / `reducer.py` / `resolution.py` / `validation.py` / `view.py` + 新增 `test_dnd2024_false_life.py`）+ 任务书执行报告豁免；diff 逐文件核对无其它任务残留。
- 规则逐条核对（对照 `.rulebooks/dnd5r/玩家手册2024/法术详述/1环.htm` L113-114 与临时生命值通则）：①目录条目与原文一致（`temp_hp: "2d4+4"`、`upcast_flat: 5`、Self → 盟友校验 + range 5）；②keep-max 在 `_apply_temp_hp_change`，`max(现有, 新值)` 不分来源，「同一来源重复施放」同样覆盖，RAW 玩家二选一的简化偏差已在任务书 §1.1#1 声明；③**吸收顺序与 P5 抗性框架一致**——`_resist_damage` 在 resolution 生成伤害事件时已把抗性减半写入 `delta`（武器/法术攻击/豁免/自动伤害四个调用点），reducer 的 temp 吸收用同一 `delta`，完整顺序为「豁免修正 → 抗性减半 → temp 吸收 → hp」，符合 RAW（抗性修正伤害本身，temp 吸收已修正的伤害）；④死亡豁免失败累积（`before == 0 and delta < 0`）在吸收之后判定，全吸收不加失败（测试 #10 锁定）；⑤0 HP 目标拒绝：validation L478 豁免集合不含 `temp_hp`，与 §1.1#4 声明的偏差一致；⑥长休清除无引擎钩子，按 §1.2 裁剪记遗留。
- **任务书 v1 升环数值矛盾（审核修正，实现无需返工）**：§1 规则书原文与 §3.4 代码均为「每比一环高一环 +5」，但 §1.1#5 与矩阵 #7 的举例把「高几环」少算一级（等价 `slot_level - 2`，2 环位会错误 +0）。执行端正确地以规则书原文为准（测试 2 环位 → 11、3 环位 → 16），审核核实规则书原文后升 v2 修正举例，未改实现迁就错误示例。
- 交叉排查：①`resource.changed` 的 `resource` 字段在 `runtime.py` 投影白名单内（L1129），既有消费方不假设恒为 `"hp"`；②view 投影：`_player_view` 是唯一新增暴露点，companion 复用同一路径（与 reducer 的 companion keep-max 对齐），enemy 分支与战场 roster 轻量投影均不含 `temp_hp`；③前端 `CombatLiveBar.vue` / `Dnd2024CombatPanel.vue` 会把 temp 授予事件渲染为「恢复 N 点生命」/「HP +N」——展示层语义偏差，权威状态不受影响，属任务书 §1.2 已裁剪的前端范围，记遗留；④`_apply_temp_hp_change` 对非 player/companion fail closed（`EventBatchError`），与敌人无 temp 来源的现状一致。
- 遗留（执行报告 §10.5 已登记，均不阻塞）：长休清理、敌人侧 temp、heroism / armor of agathys / aid 等其它 temp 或 max_hp 机制、战斗结束不清 temp、前端 temp 展示（含上述「恢复 N 点生命」误标）、对倒地盟友赋 temp 的 0 HP 边界。

### P6 审核记录（2026-10-30）

- 验收复跑核实：`test_dnd2024_misty_step.py` 目标文件审核端复跑 17 passed（12 函数，#3 六参数化）；回归十一文件（combat / spells / smite / uncanny_dodge / monk_combat / invisibility / hold_person / scorching_ray / false_life / stoneskin / misty_step）176 passed（执行端回报 = 基线 159 + 17）；rulesets 全量 578 passed / 39 deselected（执行端回报 28:02，P4 基线 561 + 17，`character_builder` 已知上游豁免）。
- 白名单合规：6 个改动文件（`srd_combat_core.json` / `catalog.py` / `check_modes.py` / `engine.py` / `validation.py` / `resolution.py` + 新增 `test_dnd2024_misty_step.py`）+ 任务书执行报告豁免；reducer / view / 前端零改动（§6 禁止项遵守）。**另有预研任务书文件在树内，未纳入本切片提交**（5 个未跟踪的 `SP1/SP4/SP5/SP7/SP8_*_TASK_SPEC_2026-10-30.md`，用户另开会话的后续切片预研产物，对应本计划 S-P 表；本切片提交用显式文件路径，未触碰它们）。
- 规则逐条核对（对照 `.rulebooks/dnd5r/玩家手册2024/法术详述/2环.htm` L251-252）：①目录条目与原文一致（`mode: teleport`、`range: 30`、附赠动作走既有 `casting_time` 经济、Self → 仅施法者目标 + 盟友校验）；②「传送不是移动」四要点全部落地——事件无 `movement_cost` 键、不进 `_movement_events`、不受剩余移动力限制（测试 #11：move 30 耗尽后仍可传送）、不触发借机攻击（见下）；③零 RNG：分支不调 `rng`，测试 #12 哨兵骰 999 未被弹出；④`teleport_distance` 双向 fail closed（teleport 必填非零 int 且 `abs <= range`；非 teleport 携带即拒，照 P5 `resist_type` 先例）；⑤「未被占据」与「你能看见」按 §1.2 裁剪（1D 无占用语义、无视觉系统），偏差已记录。
- **借机攻击触发谓词的拦截证据（矩阵 #6）**：结构上 teleport 分支只产一条裸 `dnd2024.position.changed`（`forced: True`），借机决策的唯一挂起点 `_movement_events` 不在路径上；行为上测试用 goblin position 5（相邻、反应 1）断言传送后无 `decision.pending`、无 `check.resolved`、goblin 反应仍 1；对照组同场景 `move +25` 仍挂起 `opportunity_attack` 决策（`threat_actor_ids == ["enemy:goblin-1"]`）——证明是传送专属豁免而非借机系统失效。与 push 路径（同样 forced、不触发）构成同一解释（只有使用速度的移动才触发，参照贤者谏言判据）的一致实现。
- **`slowed_10` 数据驱动的等价性证据（矩阵 #7/#8/#10）**：`_fresh_economy` 硬编码 `if "slowed_10" in conditions: speed = max(0, speed - 10)` 删除，改 `effective_speed(registry, conditions, base)`；注册表条目 `slowed_10: {"speed_modifier": -10}` 对 30 尺 actor 的求值 `max(0, 30 + (-10)) == 20` 与旧代码逐值相等，floor 语义（`max(0, ...)`）两端一致。测试 #7 锁定 ray_of_frost 现行行为逐事件不变：`turn.advanced.economy.movement/speed == 20`（30−10）且 apply 后 goblin 无 slowed_10（快照先于过期的顺序保持——reducer 仍先 `_expire_conditions` 后装 economy）；#8 锁定 speed 10 + slowed_10 → 0 不为负；#10 纯函数单测覆盖空 conditions、单修正、双修正求和、floor 0、speed_zero 压倒、正修正叠加六种。`_fresh_economy` 由 `@staticmethod` 改实例方法，两个调用点本就经 `self`，零调用方改动。
- 交叉排查：①`speed_zero` → economy 投影是 P2 显式推迟项，本切片落地（矩阵 #9：paralyzed 敌人回合 movement/speed == 0 且后续 move 意图被 P2 门控以 "speed zero" 拒——两切片联动验证）；②`economy.speed/movement` 既有消费方语义不变（同源同值）；③catalog 校验的允许键并集正确追加 `speed_modifier` 且 fail closed（bool / 非 int / 0 / ±600 越界全拒）；④附赠动作经济复用现行机制（测试 #12：action 保持 1、bonus_action 归零、第二个附赠动作法术被拒）。
- 遗留（执行报告 §5 已登记，均不阻塞）：`teleport_distance` 前端距离选择器（面板适配前可经 intent 直交）、传送终点占用与可见性待位置模型升级、其余传送法术与速度提升法术（haste 整套）未落数据。

### P8 审核记录（2026-10-30）

- 验收复跑核实：`test_dnd2024_dispel_magic.py` 审核端复跑 12 passed（42.12s）；回归子集（含 P3/P4/P5/P6 产物）188 passed、rulesets 全量 590 passed / 39 deselected（执行端回报 27:49 = P6 基线 578 + 本切片 12，`character_builder` 已知上游豁免）。
- 白名单合规：5 个改动文件（`srd_combat_core.json` / `catalog.py` / `validation.py` / `resolution.py` + 新增 `test_dnd2024_dispel_magic.py`）+ 任务书执行报告豁免；reducer / view / engine / 前端零改动（§6 禁止项遵守）。
- **4 环及以上检定已落地（无缺口，交接疑虑排除）**：DC 计算位置在 `resolution.py` dispel 分支的 `"target": 10 + source_level`；检定 = 单 d20 + `ability_modifier(actor["abilities"][actor["spell_ability"]])`（`kind: "ability_check"`，`bless_bonus` 硬编码 0——属性检定不吃 bless，正确）；`roll + modifier < target` 时 `continue` 保留该 condition，成功才终止。测试 #6 锁定失败路径（target=14=10+4、`success: False`、paralyzed 保留、无 `concentration.ended`）、#7 锁定成功级联。**升环已落地未裁剪**：#8 验证 4 环位对 level 4 来源零检定直接 `condition.removed`（RAW「小于等于该施放环阶」）。执行端摘要只是没提检定，实现自始正确。
- 规则逐条核对（对照 `.rulebooks/dnd5r/玩家手册2024/法术详述/3环.htm` L104-106）：①「法术即告终止」整道语义——专注来源发 `dnd2024.concentration.ended`（owner 去重，一次 dispel 恰一条），reducer 级联移除全部目标（#5：单次 3 环 upcast hold_person 双 goblin，dispel 其一，双目标 paralyzed 均消失、无逐目标 removed）；非专注来源发 `condition.removed`（reason=dispelled）——与 P2 重复豁免「仅该目标」的措辞差异已按 RAW 分立；②来源快照 `source_spell_ref` / `source_spell_level` 经既有 `condition.applied` 契约扩展（全部事件键入元数据），reducer 零改动、不改语义；免费施放取法术基础环阶（#3：Magic Initiate charm_person slot 0 → `source_spell_level == 1`）；③旧存档/无来源元数据的 condition（rage、dodging、stable 等）在判定循环 `continue` 跳过——fail closed 不可解除，无「魔法清单」硬编码；④阵营放行正确（可解盟友身上敌方 debuff），dead 拒绝、dying 允许；⑤RNG 可预测：仅高于阈值的来源消耗 d20，#4 `RecordingRng.d20_requests() == 0` 锁定零消耗场景。
- **任务书 v1 三处自身错误（审核修正升 v1.1，实现无需返工）**：①§3.5 写「5 级拿四环位」，bundle `slot_profiles.full` 实际 L7 才有 `{4:1}`（L5 为 `{1:4,2:3,3:2}`），矩阵 #8 需 7 级——修任务书不改实现；②矩阵 #3 原举例 smite 是 `triggered_damage` 不施加 condition，改用 Magic Initiate 免费施放 charm_person，断言 `source_spell_level == 1` 恰好锁定 §1.1 边界 3（免费施放取基础环阶），非绕开；③矩阵 #5 原「两次施法」与专注 RAW 冲突（专注中断即整道终止，第二次施放不成立），改单次 3 环 upcast hold_person 双目标，级联语义如上核实正确。
- **SP 预研行处置结果**：交接要求对主计划 1C 行做补丁级暂存（剔除 SP 块、只暂存 P8 hunk）；审核期间用户另一会话已自行以 `61bde0b3` 提交该行与 12 份 `SP*_TASK_SPEC_2026-10-30.md`（核实该提交 13 文件，无任何 P8 文件卷入），工作树变干净，补丁级暂存操作作废不再需要；本切片两笔提交均显式路径，未混入他人改动。
- 交叉排查：①`check.resolved` 事件投影白名单（`runtime.py` L1123-1130）无 `ability` 键——dispel 检定事件投到玩家侧时属性名被剥离，展示层遗留（机械结算不受影响，记遗留不阻塞）；②`SPELL_MODES` 追加 `dispel` 走既有模式注册路径，无 generic 分支污染；③`concentration.ended` 复用 P2 既有 reducer 级联，owner/reason 契约不变。
- 遗留（执行报告 §10.5 已登记，均不阻塞）：物件/魔法效应目标裁剪（无实体模型）、reaction 路径来源快照缺口（shield 一轮存续）、前端目标选择器任意阵营适配、`ability` 键投影、greater restoration 家族数据补录。

### P7 审核记录（2026-10-30）

- 验收复跑核实：`test_dnd2024_counterspell.py` 审核端复跑 13 passed（50.24s）；回归子集 188 passed、rulesets 全量 603 passed / 39 deselected（执行端回报 28:17 = P8 基线 590 + 本切片 13，`character_builder` 已知上游豁免）。
- 白名单合规：6 个改动文件（`validation.py` / `resolution.py` / `view.py` / `reaction_spells.py` / `engine.py` / `reducer.py`）+ 新增 `test_dnd2024_counterspell.py`；`reducer.py` 恰为 v1.1 §3.7 裁决授权的单行改动（忽略集追加 `dnd2024.spell.countered`，无其它状态写入、unknown-type fail-closed 未动），`events.py` / `runtime.py` / 前端零改动。
- 规则逐条核对（对照 `.rulebooks/dnd5r/玩家手册2024/法术详述/3环.htm` L95-96）：①触发 = 看见 60 尺内生物施展带 V/S/M 任一成分的法术——窗口判定 `set(components) & {V,S,M}` 非空 + `_distance <= 60` + 敌方 hp>0、`counterspell_dc>0`、反应未耗；②施法者 con 豁免 = 单 d20 + `saving_throws["con"]` + bless d4（豁免吃 bless，与 P8 dispel 属性检定不吃 bless 正确分立）；`auto_fail_saves` 命中 con 免掷骰（`rolls=[]`、`auto_failed=True`、total 0）；③失败 = 法术消散且毫无效果（无 `spell.cast`/效果/专注事件）+ 动作或附赠动作按 casting_time 浪费（#8 misty_step → resource=bonus_action）+ **法术位与免费施放资源不消耗**（#5/#8 断言 slots 不变、无 slot_spent）；④成功 = 经 `_spell_events` 完整重放（成本/法术位/效果照常）；⑤被反制的新专注从未施放——无 `concentration.ended`、旧专注保留（#10 blur 保持）；⑥2024 无升环条款——未实现任何 upcast 分支，正确；⑦敌方消耗反应（`reaction.spent` reason=counterspell）。
- 架构核对：挂起/恢复照借机攻击先例（pending 期间只放行 `decision.resolve`/`combat.message`，#3 三类意图被拒）；`cast_intent` 服务端 `deepcopy`、客户端不可写；多威胁 actor_id 排序取第一（§1.1#6 声明的简化）；`counterspell_dc` 走敌人档案数值校验（0-30 缺省 0，bool 拒绝——fail closed，无能力即不开窗）；敌人施法禁令回归未动（#12）；reaction_defense/triggered_damage 排除在窗口首个 mode 分支（#11）。
- **v1.1 裁决执行核实**：`reducer.py` 单行授权被精确遵守（diff 仅忽略集一行）；`dnd2024.spell.countered` 在返回批次、事件 ledger、叙述者上下文三处权威可见（裁决时已核实），玩家侧 combat feed 滞后登记于执行报告 §10.4、未扩 scope；#11 采用窗口级验证（mode 排除分支即被测单元，避免把测试耦合进 P3/P6 挂起链路，语义等价）；#13 全部 339 条法术均声明成分（catalog 装载即拒绝空 `components`，无成分过滤分支不可达）——测试改断言「全部法术有成分」，未造假数据或死代码。
- 遗留（执行报告 §10.4 已登记，均不阻塞）：玩家侧 combat feed 不显示 `spell.countered`、`check.resolved` 投影缺 `ability` 键（P8 已登记）、敌人无通用施法档案（玩家侧反向反制不可玩）、encounter preset 目录 schema 的 `counterspell_dc` 接入。

### P9 审核记录（2026-10-30）

- 验收复跑核实：`test_dnd2024_revivify.py` 审核端复跑 10 passed（38.31s）；rulesets 全量 613 passed / 39 deselected（28:55 = P7 基线 603 + 本切片 10，审核端重跑——执行端全量中断未留数字）。
- 白名单合规：实质改动恰为 4 个白名单代码文件（`srd_combat_core.json` +1 行、`catalog.py`、`validation.py`、`resolution.py` +9 行）+ 新增 `test_dnd2024_revivify.py`；reducer 零改动（§6.1 禁止事项遵守，GM 复活路径共用契约未动）。`git status` 另见的 `reaction_spells.py` / `view.py` / `test_dnd2024_stoneskin.py` / P2–P8 任务书 / `srd_spells.json` 的 M 经 diff 核实全部为行尾幻影（无内容变化），未纳入提交、未 renormalize。
- 执行报告缺口：执行端全量中断后未补录执行报告——任务书 §10 由审核端代录（基于 diff 与复跑事实），已如实标注。
- 规则逐条核对（对照 `.rulebooks/dnd5r/玩家手册2024/法术详述/3环.htm` L184）：触及 5 尺（目录 range 5 + #7 射程拒绝）；动作施法（economy action 归零）；3 环位（#9 低环拒绝 + 位耗尽拒绝）；复活回 1 HP（revived hp=1 + canonical hp==1，RAW 固定值、无数据字段，§6.9 遵守）；只 dead（#3 dying——0 HP + unconscious + death_saves 未满 3 失败——被拒「revive spells require a dead target」）；player-only（#6 dead companion 在 validation kind 门被拒，防 apply 阶段 EventBatchError，§6.2 遵守）；dead 拒绝不泄漏（#4 healing 对 dead 盟友仍拒「dead targets require a resurrection effect」，§6.7 遵守）；死亡来源无关（#10 `damage_at_zero_hp` 来源照常复活）；复活后意图恢复（#8 death_save 消失、attack 出现）。
- 事件与状态（#2）：事件序 `action.spent` → `slot_spent` → `spell.cast` → `character.revived` 精确断言 + `NoRollRng` 零掷骰；dead/unconscious/stable/death_saves 四状态清理与 hp=1 由既有 reducer `character.revived` 处理器完成（GM 复活命令同契约，本切片只发事件）。
- 探索层行为变化（v1.1 §1.2 预告的②）：revivify 落 combat 效果条目后，`exploration.cast_spell` 对其显式拒绝「no exploration-safe deterministic effect」（fail closed）——战斗外 revivify 从叙事兜底变硬拒绝，机械通道过渡期仍为 GM revive 命令，家族切片①（探索层 revive 分支）落地后接管。已在 v1.1 登记，非回归。
- 遗留（不阻塞）：1 分钟时限 / 钻石耗材 / 老死与肢体限制照 §1.2 裁剪；companion / enemy 复活未开（kind 门 + canonical 位置已记）；前端 dead 盟友目标选择器适配照 P2 投影滞后先例。

### R1 审核记录（2026-10-30）

- 验收复跑核实：审核端复跑 `test_dnd2024_exploration_revive.py` + `test_dnd2024_revivify.py` 20 passed（56.04s）+ `tests/test_dnd2024_exploration.py` 6 passed（16.15s）= 26 passed，与执行端报告一致；rulesets 全量见任务书 §8.6（执行端 `.venv` 环境运行）。审核端 shell 的系统 python 缺 `peewee`（tests/ 根文件导入链需要），复跑一律用 `.venv\Scripts\python.exe`——环境差异、非代码问题（P9 未改动文件同样症状判别）。
- 白名单合规：改动恰为白名单 2 代码文件（`exploration/engine.py` +34 行、新建 `revive_effects.py` 98 行）+ 新测试 226 行 + 任务书；combat catalog / combat reducer / resolution / validation / `srd_spells.json` / 前端零触碰；无硬编码法术拒绝清单（语义表唯一数据源）。`git status` 另见的 10 个 M（P2–P8 任务书、`reaction_spells.py`、`view.py`、`srd_spells.json`、`test_dnd2024_stoneskin.py`）经 diff 复核全部为行尾幻影，未纳入提交、未 renormalize。
- 矩阵逐条（6/6，10 用例）：#1 战斗外 revivify——事件序 `slot_spent→cast→revived` 精确断言、hp=1、四状态清空、3 环位 1→0、`combat.status=="none"`；#2 dying / 存活 / 敌方三用例拒绝且位不变；#3 无位拒绝且不扣；#4 reincarnate / true_resurrection 显式拒绝（`UNSUPPORTED_MESSAGE`、`"events" not in resolved`、位不变、无 spell.narrative——空响钉死）；#5 raise_dead / resurrection 拒绝 + `is_fully_implemented` 断言 False + 语义表仅 revivify 全实现；#6 P9 + 探索层回归绿。
- 偏差判定（2/2 成立）：① 探索层有独立 `_reduce_event`（非复用 combat reducer），`character.revived` 处理器镜像写入 `exploration/engine.py`（白名单内）——语义与 P9 逐字段一致（仅 player、`max(1,hp)` clamp `max_hp`、清 dead/unconscious/stable/death_saves），任务书 v1「reducer 战斗外可用」为审核端事实误判，v1.1 修正；② combat reducer `int(event.get("hp"))` 不吃 `"full"`，`revive_events` 前置解析为目标 `max_hp` 整数——事件契约保持整数，reducer 未动。
- 语义表设计核实：`IMPLEMENTED_KEYS={"hp","cleanse"}` + `IMPLEMENTED_CLEANSES={"poisoned"}`，`is_fully_implemented` 对 hp 类型收紧（排除 bool/非 int/非 "full"）、cleanse 子集判定——部分生效不可能；`condition.removed` 落地器在探索层已存在（engine L483），cleanse 路径就绪待 raise_dead 启用。
- 体验收口验证：场合 B（战斗外 revivify）→ 玩家自助闭环；场合 C（家族空响）→ 显式拒绝 + 不扣位 + 可读消息，`spell.narrative` 兜底对复活家族不再可达。
- 遗留（不阻塞，任务书 §8.5 登记）：raise_dead / resurrection 待 SP10 D20 聚合与长休衰减钩子；reincarnate 待角色重建；true_resurrection 唤名永久 GM 通道；死亡时刻 / 死因 / 遗体状态维持 GM 叙事裁定（无 canonical 字段，既定口径）。

### P1–P9 / R1 提交后修复记录（2026-10-30）

- 依据：`docs/internal/design/P1_P9_R1_POST_REVIEW_REMEDIATION_TASK_SPEC_2026-10-30.md`（v1.0，执行模型 dsv4.1f）；不新增法术切片，不重排 1C。
- 修复 A（反应资格与 Counterspell）：人类 GM 的 `counter_spell` pending 不再被自动 `decline`，`available_intents` 允许 assigned GM（即使非当前行动者）处理决策；Counterspell 开窗/最终结算与借机攻击统一复查 HP>0、反应可用、非失能、非 `reaction_blocked`；前端补 counter 入口。
- 修复 B（Self 与施法契约）：catalog 支持可选 `target_scope: "self"` 并对非法值 fail closed，`false_life` / `misty_step` 声明 Self，validation 统一替代 teleport 特判；`view.py` 投影 `target_side` / `target_count` / `upcast_target_count` / `repeat_targets` / `resist_choice` / target `is_dead`；前端改为投影驱动目标与参数（多目标、resist、teleport、Revivify 仅 dead 盟友）；P2P `ruleset.intent` 白名单新增 `resist_type` / `teleport_distance`。
- 修复 C（探索资格与死亡豁免）：`exploration/engine.py` 建立并共用施法资格（HP>0、非 dead、非失能），`available_intents` 与 `_validate` 同步；`combat/reducer.py` 在临时 HP 吸收前保存原始伤害事实，0 HP 全吸收仍 +1 失败、重击 +2；P4 任务书已追加 §10.6 勘误。
- 验收：后端定向 87 passed；全量见任务书 §11 执行报告；前端 86 passed，typecheck / lint / build 通过（环境 npm 11.13.0）。
- 后置（§8 明确非目标，继续登记）：Counterspell 视觉与视线、复活家族扩展、hosted AI 自动策略、recent combat feed 白名单、全仓库行尾 renormalize 等。

## 待定决策

| # | 决策点 | 选项 | 当前倾向 |
|---|---|---|---|
| 1 | token 拖拽语义 | a) 拖拽即意图（drop 提交 move intent，服务端校验）<br>b) 双层状态（机械权威 + 表现自由） | a（保权威纯净，体验不足再局部引入 b） |
| 2 | 战术层路线 | a) 嵌入 OR2<br>b) 自建完整战棋（参考 PlanarAlly）<br>c) 最小 2D 升级 | c |
| 3 | 与上游关系 | a) 垂直切片逐个回馈上游<br>b) 独立分叉演进 | 未定（取决于上游对 schema 扩容的接受度，建议切片 1 先试投） |
| 4 | 本文档是否入库 | 根级计划文档（如 GAL_TRACK_PLAN.md）为已跟踪先例 | 入库 |

## 相关文档

- 架构事实来源：`docs/ARCHITECTURE_CN.md`、`docs/ENGINEERING_RULES.md`（§15 Testing 为硬门槛）
- 代码导览：`docs/wiki/05-rulesets-and-content.md`、`docs/wiki/10-key-reference.md`
- 协作流程：`CONTRIBUTING.md`（前端必须 npm 10；后端 `pytest -q --cov=src` 离线可跑）
- 法术缺口逐条分类：`docs/internal/design/SPELL_GAP_MATRIX.md`（2026-10-29，308 条，P1–P13 原语映射）
