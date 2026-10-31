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
| 专长 | `feats/` 6 个 + advancement feats | 约 44 个 | PHB2024 约 50 |
| 背景 | `backgrounds/` 4 个 | 约 12 个 | PHB2024 16 |
| 种族 | `species/` 9 个 | 1 个（Aasimar） | 基本齐 |
| 物品 / 工具 / 技能 / 语言 | SRD 范围齐全 | 魔法物品机械效果系统待查 | item 仅 5 类无效果模型 |
| 怪物 / NPC / 遭遇 | content-pack 可扩展（4 种 kind，source-aware） | — | 架构正确 |

关键架构事实（不变）：content-pack 可扩展类型仅 `monster / npc_statblock / item / encounter_profile`；spells / progression / class_feature / subclass 均为硬编码 `srd_*` bundle，外部内容无法注入。

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
- 阶段一 5 个切片对法术缺口几乎无净贡献（heal 与 triggered_damage 是既有能力的职业侧复用），法术需要独立的原语切片序列。

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

每个切片 = catalog 数据 + 引擎原语 + reducer 事件 + 测试，独立可验证、可回滚，符合 PR 模板"最小完整改动"要求。

| # | 切片 | 打通的原语 | 解锁的特性 |
|---|---|---|---|
| 1 | Second Wind | 资源消耗 → 治疗（heal 事件 reducer 已有） | fighter 1 级 |
| 2 | Rage | 持续性 condition 记账（duration / turns_remaining / source_actor_id / established / sustained_this_turn）+ 力量近战伤害加成 + 力量豁免优势 + 物理抗性 + 失能结束 + 回合归属续期 | barbarian 1 级 |
| 3 | Uncanny Dodge | 触发注册表泛化（`on_hit / on_damage_taken / on_miss / on_turn_start`）+ 伤害修正窗口 | rogue 5 级 |
| 4 | Action Surge | `economy.granted` 动作经济事件 | fighter 2 级 |
| 5 | Sneak Attack | 谓词求值器 + 条件伤害（"优势或队友邻近 → +Xd6"） | rogue 1 级 |

工程约束（贯穿全部切片）：

- 每个 change key / mode / 触发点必须是**注册过的原语**，未知即 fail closed（对齐 `features/models.py` 现有注释与 `ENGINEERING_RULES.md` §15）；
- 先有引擎原语、再扩 `ACTION_KINDS` / capabilities schema，不得反向；
- 优势劣势冲突规则从"多来源相消"升级为 5e RAW 的"优劣势各自计数"（改 `check_modes.py` 时注意）；
- 优势来源要推广到豁免/检定，现仅覆盖攻击掷骰。

### 阶段 1B：法术原语垂直切片（2026-10-29 新增）

动机：法术缺口（308 条）与职业特性是同构问题——先铺原语、再填数据。阶段一的切片只服务职业特性，法术需要自己的原语序列。逐条分类与完整清单见 `design/SPELL_GAP_MATRIX.md`。

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

工程约束与阶段一相同（原语先行、fail closed、任务书/白名单/停止条件/执行报告协议）。P1–P9 合计约点亮 60+ 条法术，加上 review 桶 13 条快速人工确认与直填管线后续批次，完成后 1–5 级团法术体验基本完整（"直填 ~40 条免人工"的原估计已被原型推翻，见阶段二管线实证）。P10（属性检定）/P11（召唤）/P12（变形）/P13（回合接管）为大件，放阶段二后段与职业填充交错。

### 阶段二：内容批量填充（A 层）

1. 以切片 1–5 沉淀的 schema 为模板，把 12 职业的幽灵 id 逐个补进 `class_feature_catalog`（name / summary / parameters / capabilities）；
2. **法术填充走生成器管线**（原型已验证：`scripts/rulesets/import_dnd5eapi_spell_effects.py`，快照与产物在 git 排除的 `.imports/`）：
   - 四层：获取（dnd5eapi 2014+2024 快照，CC-BY 署名）→ 映射（2014 骨架 + 2024 补充，fail-closed）→ 核对（`.rulebooks` 2024 原文骰式 + 语义关键词三层防线）→ 分批验收（contracts + 装载测试全过、行为测试抽样、按桶分批提交）；
   - **原型实证（2026-10-29，直填桶 46 条）**：auto=0 / review=13 / manual=33——没有一条可不经人眼入库；机器的真实产出是"13 条带完整草稿的快速人工确认（补 condition/rider 字段）+ 33 条带精确原因的人工处理"（其中 16 条 P 原语等待、5 条 P2 tick/区域硬阻断）；
   - 三层防线缺一不可（原型过程实证）：无数值核对 → 2014 数值污染（cure-wounds 1d8 vs 2d8）；无语义扫描 → "数值正确的错误模式"（初版 18 条假 auto，如 hunter_s_mark 附伤被误判直伤）；无锚点检查 → API 2024 字段残缺误判（mind_spike 无 dc 字段实为 Wis 豁免）；
   - 2014→2024 差异坑实录：acid_arrow 新增回合结束延迟伤害（P2）、harm 改为降生命值上限、shining_smite 改名+施法时机改命中后、规则书保留人名前缀（Melf's）而 SRD 5.2.1 canonical id 去除（别名表处理）；
   - 语义关键词是启发式：降级方向永远保守正确，但原因标签可能不准（如光照半径误标"区域语义"），人工复核以规则书原文为准；
3. 数据源：open5e / dnd5eapi 的 CC-BY SRD 5.2 JSON，映射到 canonical ref 时逐条过 `contracts.py` 校验；
4. 架构解耦（独立 PR，勿与数据填充混合）：把 spells / progression / class_feature 从硬编码 `srd_*` 改为 source-aware 目录，参照 `content/catalog.py` 已有模式与 `ContentRefChain`；
5. 仓库内明确 `LICENSE`（AGPL 代码）与内容署名（CC-BY）的分层边界。

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
| 一 | 切片 1：Second Wind（heal 原语） | ✅ 2026-10-28 执行完成并审核通过（任务书 v3；改动在 `feat/second-wind` 工作区待提交） |
| 一 | 切片 2：Rage（持续性 condition + 引擎检查点） | ✅ 完成（任务书 v2，分支 `feat/rage`；`established` 保证轮生命周期、str 豁免优势、track 加值、物理抗性、失能结束、仅自身回合续期均已测试锁定。**实现为 `conditions` 专用条目 + 引擎内检查点，非通用 effect 实例**——effect 化、狂暴期间施法/专注限制、检定优势数据驱动归入 P1/P5 偿还；PR #612 review 修复 `53b715e1` 补齐抗性与回合行为） |
| 一 | 切片 3：Uncanny Dodge（触发泛化 + 伤害修正窗口） | ✅ 完成（任务书 v2，分支 `feat/uncanny-dodge`；`hit_reaction` 决策泛化到特性选项、伤害减半、AI 自动选择、人类玩家按钮均已测试/构建锁定） |
| 一 | 切片 4：Action Surge（economy 授予） | ✅ 完成（任务书 v2，分支 `feat/action-surge`；`economy.granted` 通用授予事件、surge_action 独立经济 key、once_per_turn 通用字段、支付优先级、施法结构性排除均已测试锁定） |
| 一 | 切片 5：Sneak Attack（谓词伤害） | ✅ 完成（任务书 v1，分支 `feat/sneak-attack`；武器/优势/盟友谓词、extra_dice_formulas 多附加骰 AST、feature_usage 每回合账本、决策链延迟消耗均已测试锁定） |
| 1B | 法术原语切片 P1–P9（矩阵见 `design/SPELL_GAP_MATRIX.md`） | ⬜ 未开始（建议顺序 P1→P5→P2→P3→P4→P6→P8→P7→P9，P1–P5 为法术/特性共用底座优先） |
| 二 | 法术批量填充管线（原型已验证，review 13 + manual 33 待处理） | 🔄 原型完成（`scripts/rulesets/import_dnd5eapi_spell_effects.py`） |
| 二 | spells / progression / class_feature source-aware 解耦 | ⬜ 未开始 |
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
