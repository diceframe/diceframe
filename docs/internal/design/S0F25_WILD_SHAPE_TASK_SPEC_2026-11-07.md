# S0-F25 任务书：Wild Shape 全生命周期与 Archdruid

| 项 | 值 |
|---|---|
| 切片编号 | S0-F25（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `wild_shape`、`wild_resurgence`、`beast_spells`、`archdruid` |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Druid p42-43、Shape-Shifting p187；Beast stat block 见 Animals p344-364（推荐 Rat/Riding Horse/Spider/Wolf 分别在 p358/360/361/364）；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | F6 来源化临时槽与资源转换；F20 roll Initiative 与 turn slot；Beast stat block 和形态选择由本切片新建，无既有数据前置 |
| 建议分支 | `feat/dnd2024-s0f25-wild-shape` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 从 F6/F20 实际集成提交开工。**缺少真实 Beast stat block、CR/Fly、装备穿戴事实或形态选择数据时必须停止，不可只扣 Wild Shape 资源后宣称完成。**
2. 本切片覆盖已知形态、长休替换、变形/恢复、装备、THP、施法限制、双向资源转换和 Archdruid 三项；不能仅做外观或 AC 替换。
3. 所有 Beast 数值来自服务端内容，客户端只提交合法 form ref、装备模式及资源选择；不得提交 Beast HP、攻击、骰或统计字段。
4. 不实现 Wild Companion、Land 子职、Polymorph、Beast 以外的通用怪物图鉴或通用召唤；这些由对应前置/后续任务负责。
5. 仅使用官方 PDF；p187 通用变形规则要求未另述的持续效果跨形态保留，死亡恢复真形。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p42，Wild Shape：

> As a Bonus Action, you shape-shift into a Beast form that you have learned for this feature ... You stay in that form for a number of hours equal to half your Druid level, or until you use Wild Shape again, have the Incapacitated condition, or die. You can also leave the form early as a Bonus Action.
>
> **Number of Uses.** You can use Wild Shape twice. You regain one expended use when you finish a Short Rest, and you regain all expended uses when you finish a Long Rest.
>
> **Known Forms.** You know four Beast forms ... maximum Challenge Rating of 1/4 and that lack a Fly Speed (see “Animals” in “Monsters” for stat block options). The Rat, Riding Horse, Spider, and Wolf are recommended. Whenever you finish a Long Rest, you can replace one of your known forms with another eligible form.

同页 Beast Shapes 表：Druid 2/4/8 级的 Known Forms 为 4/6/8，Max CR 为 1/4、1/2、1；Fly Speed 在 8 级前为 No，8 级起可用。

SRD 5.2.1 p43：

> **Temporary Hit Points.** When you assume a Wild Shape form, you gain a number of Temporary Hit Points equal to your Druid level.
>
> **Game Statistics.** Your game statistics are replaced by the Beast’s stat block, but you retain your creature type; Hit Points; Hit Point Dice; Intelligence, Wisdom, and Charisma scores; class features; languages; and feats. You also retain your skill and saving throw proficiencies and use your Proficiency Bonus for them, in addition to gaining the proficiencies of the creature. If a skill or saving throw modifier in the Beast’s stat block is higher than yours, use the one in the stat block.
>
> **No Spellcasting.** You can’t cast spells, but shape-shifting doesn’t break your Concentration or otherwise interfere with a spell you’ve already cast.
>
> **Objects.** You choose whether your equipment falls in your space, merges into your new form, or is worn by it ... Equipment that merges with the form has no effect while you’re in that form.
>
> **Wild Resurgence.** Once on each of your turns, if you have no uses of Wild Shape left, you can give yourself one use by expending a spell slot (no action required). In addition, you can expend one use of Wild Shape (no action required) to give yourself a level 1 spell slot, but you can’t do so again until you finish a Long Rest.
>
> **Beast Spells.** While using Wild Shape, you can cast spells in Beast form, except for any spell that has a Material component with a cost specified or that consumes its Material component.
>
> **Archdruid.** **Evergreen Wild Shape.** Whenever you roll Initiative and have no uses of Wild Shape left, you regain one expended use of it. **Nature Magician.** Choose a number of your unexpended uses ... each use contributing 2 spell levels ... Once you use this benefit, you can’t do so again until you finish a Long Rest. **Longevity.** For every ten years that pass, your body ages only one year.

SRD 5.2.1 p187，Shape-Shifting：除非效果另述，conditions、spells、curses 等持续效果跨形态保留；死亡恢复真形。

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 四个 id 已在 progression 表按 Druid 2/5/18/20 列出，canonical 定义尚不存在 | `content/progression_catalogs/srd_classes.json`；class feature catalog |
| `wild_shape` 资源已有等级轨道、短休+1/长休全恢复；2–5 级 2 次、6–16 级 3 次、17–20 级 4 次 | `content/rest_catalogs/srd_recovery.json`；`content/progression_catalogs/srd_classes.json` |
| 祖先基线没有 Beast/monster stat block catalog；F6 不提供 Beast，本切片必须新建官方数据与只读加载器 | 基线 `templates/rulesets/dnd2024_srd/content/` 文件清单；§5 明确标记的新建文件 |
| canonical 角色包含 abilities、proficiencies、HP/HD、装备、features 和 spellcasting | `character/derivation.py::derive`；`character/reconciliation.py::reconcile` |
| 临时 HP、HP、条件和法术槽分别由既有事件/reducer 管理 | `combat/reducer.py::_apply_temp_hp_change`、`_apply_hp_change`、`_reduce_event` |
| 法术 cast 会结束旧专注再开始新专注；变形本身尚无事件 | `combat/resolution.py::_spell_events`；`exploration/engine.py::resolve_intent` |
| 装备以 item ref 协调，merge 后必须使装备效果失效而非仅隐藏 | `character/reconciliation.py::reconcile`、`_project_equipped_refs` |
| 长休通过 runtime 显式 rest options 转发，generic service 不需知道 D&D 字段 | `runtime.py::complete_rest`、`rest_option_names`；`resting/engine.py::complete_long_rest` |
| F20 提供 roll Initiative 钩子；当前资源恢复不能由客户端伪造 | `combat/resolution.py::_start_combat_event` 的 F20 集成边界 |
| spell catalog 有 components 数组，但是否有价格/消耗详情必须逐法术核实 | `content/spell_catalogs/srd_spells.json`；`combat/view.py::_available_spells` |

## 4. 精确行为与数据契约

### 4.1 已知形态与长休替换

1. 四特性按 Druid 2/5/18/20 授予，source_ref 指向 p42-43；`wild_shape` 沿用现有资源，不另建变形次数池。
2. 本切片的 owner-scoped 权威选择状态保存 `known_form_refs`，每个都是新建 Beast catalog 中唯一的 content ref。2/4/8 级完成选择后数量必须恰为 4/6/8，CR 上限为 1/4、1/2、1；8 级前拒绝任何 Fly Speed>0 的形态。
3. 建卡和升级只能选符合当前等级条件的真实 Beast；数量不足时不能进入需要完整选择的结算，重复 ref、非 Beast、CR/Fly 超标均 fail closed。
4. 每次长休可通过 `wild_shape_replacement={old_form_ref,new_form_ref}` 替换**一个**已知形态；新旧 ref 必须真实存在、old 已知晓、new 合法且不与剩余重复，不提交则全部保持。
5. 替换在休息完成批次原子落账并产生事件；一次请求多个替换、短休替换或变形中改成不一致状态时拒绝。当前活跃形态不因其 ref 被替换而自动获得新统计。
6. 新建 Beast catalog 覆盖官方 Animals p344-364 中全部 CR0–1 Beast 的完整 stat block，至少包含 p42 推荐的 Rat/Riding Horse/Spider/Wolf 及 Find Familiar 的 CR0 形态；逐项记录官方页码 source_ref、CR、速度、属性、AC/HP/HD、熟练、感官、攻击和形体。字段缺失或来源不符时加载 fail closed，不用测试夹具冒充内容。

### 4.2 真实变形、保留字段与装备

1. Wild Shape 为 Bonus Action+1 资源，意图只含合法 `form_ref` 和每件携带装备的 `drop|merge|wear` 模式；服务端从本切片 Beast catalog 构造 `dnd2024.wild_shape.assumed`。
2. 活跃状态至少含 `active_form_ref`、`known_form_refs`、`assumed_at`、`duration_hours=floor(druid_level/2)`、`equipment_modes`、`source_actor_id`；不得仅存显示名或“已变形”布尔。
3. 保留 personality/memories/speech、原 creature type、当前及最大 HP、Hit Dice、Int/Wis/Cha、class features、languages、feats；替换其他游戏统计为 Beast 的真实 AC、Str/Dex/Con、速度、感官、攻击等。
4. 技能/豁免熟练取原角色与 Beast 的并集，原熟练使用自己的 PB；每项 modifier 比较后取较高者。不得把 Beast HP/HD、creature type 或 mental scores 覆盖给角色。
5. 每次 assume 获得等于当前 Druid level 的 THP，沿用 keep-max reducer；再次变形先原子结束旧形态再进入新形态并再次支付/获得 THP，不能刷新持续时间而不扣资源。
6. `drop` 装备留在原空间，`merge` 装备在形态内无任何效果，`wear` 仅在 Beast catalog 的形体数据与 GM 权威判定均允许时正常生效；装备不变形、不改大小，不可穿者只能 drop 或 merge。
7. 装备模式逐 item ref 保存，恢复时准确还原携带/穿戴/掉落事实，不能复制、销毁或静默全部穿戴。缺权威地面物件或可穿戴判定时停止。
8. 攻击、AC、速度和特性资格读取变形后的统一 actor view；merged 装备不参与 AC、攻击、熟练或其他效果，保留的 class features 仍按各自条件工作。

### 4.3 恢复、持续效果与施法

1. 形态在持续小时数到期、再次 Wild Shape、获得任何 `implies_incapacitated` 条件、死亡或 Bonus Action 主动离开时结束；失能/死亡不能等回合末。
2. `dnd2024.wild_shape.reverted` 记录 reason、form ref 和装备还原；普通 HP/HD 在变形期间一直保留，恢复不得治疗、重置伤害或补满 HP。
3. p187 的既有 conditions、spells、curses 和其他持续效果默认跨形态保留；变形本身不产生 `concentration.ended`，已有法术目标与 Concentration 继续。
4. 2–17 级 Beast form 禁止一切 cast spell 意图，但仍可执行保留的非施法 class feature；已有 Concentration 不被“禁止施法”误删。
5. 18 级 Beast Spells 放开 Beast form 施法，唯一材料例外为“M 有指定价格”或“M 被消耗”的法术仍禁止；其他 V/S/M 均不再因 Beast 肢体而额外限制。
6. 每个法术必须从权威组件元数据判定 `material_cost_specified`/`material_consumed`；缺数据时 fail closed，不得把所有 M 一概禁止或一概允许。

### 4.4 Wild Resurgence 与 Archdruid

1. slot→use：每个自己的回合最多一次，仅当 Wild Shape current=0 时，可 no action 消耗一个真实普通或 F6 临时法术槽恢复 1 use；current>0、无槽或非自己回合不可用，并记录 `feature.used`。
2. use→slot：no action 消耗 1 Wild Shape use，经 F6 创建 `source="wild_resurgence"` 的 level 1 临时槽；以 D2/F6 owner-scoped long-rest usage 记录本方向已用，短休不重置。临时槽可超过普通 slots_max，长休先移除再恢复正常槽，不另建平行槽余额。
3. 两个方向不能在同一声明中循环伪造成免费无限资源；每步独立校验、独立事件，use/slot 不得为负，重放不重复恢复。
4. Evergreen 在每次 roll Initiative 时若 current=0 自动恢复 1 use；current>0 不变，无骰、无动作、无长休限制。必须接入 F20 同一开场顺序，不能每场战斗多次触发。
5. Nature Magician：20 级 no action 选择 1..当前未消耗 uses 的整数 N，全部消耗并经 F6 创建 `source="archdruid_nature_magician"`、level `2N` 的临时槽；本角色最多 4 uses，因此合法等级为 2/4/6/8。以 `archdruid` 的独立 long-rest usage 记账，与 Wild Resurgence 转换不共享，长休清除临时槽并重置。
6. Longevity 声明 `physical_years_per_decade=1` 并在特性/年龄派生投影中可观察：每真实经过 10 年身体只老 1 年。无权威年龄时不虚构出生年份；本切片不新建历法系统，但不得省略该参数或误写为停止衰老。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. 新建 `templates/rulesets/dnd2024_srd/content/creature_catalogs/srd_beasts.json`、`templates/rulesets/dnd2024_srd/locales/en/creature_catalogs/srd_beasts.json`、`templates/rulesets/dnd2024_srd/locales/zh-CN/creature_catalogs/srd_beasts.json`（基线均不存在；仅官方 Beast 数据）
5. 新建 `src/rulesets/dnd2024/character/creature_catalog.py`：Beast/后续召唤 stat block 的封闭校验与只读加载
6. `src/rulesets/dnd2024/features/resolver.py`
7. `src/rulesets/dnd2024/character/derivation.py`
8. `src/rulesets/dnd2024/character/reconciliation.py`
9. `src/rulesets/dnd2024/combat/resolution.py`
10. `src/rulesets/dnd2024/combat/reducer.py`
11. `src/rulesets/dnd2024/combat/validation.py`
12. `src/rulesets/dnd2024/combat/view.py`
13. `src/rulesets/dnd2024/exploration/engine.py`
14. `src/rulesets/dnd2024/resting/engine.py`
15. `src/rulesets/dnd2024/runtime.py`
16. 新建 `tests/rulesets/test_dnd2024_wild_shape.py`
17. 本任务书：只允许追加执行报告

Beast 数据只能在第 4 项新建，不得归派给 F6；长休限制复用 D2/F6 usage，不新增 rest 资源。不改 progression/subclass/spell/rest catalog、通用 rest service 或前端；缺数据时停止而非扩大白名单。

## 6. 编号测试矩阵

1. 四特性按 Druid 2/5/18/20 授予，低一级和其他职业无效果；Wild Shape 次数轨道与短/长休保持正确。
2. 2/4/8 级已知形态数量和 CR 上限为 4/6/8 与 1/4、1/2、1，8 级前 Fly 被拒绝、8 级起合法 Fly 可选。
3. 非 Beast、重复、未知 ref、CR/Fly 超标和客户端伪造 stat block 全部 fail closed。
4. 长休只替换一个已知形态，未提交保持不变；短休、多替换、旧 ref 未知或新 ref 冲突均拒绝。
5. 变形支付 Bonus Action+1 use，使用真实 Beast stat block；缺本切片官方 Beast 数据时整个能力不可用且不扣资源。
6. 保留 creature type、HP/HD、Int/Wis/Cha、class features、语言、专长和说话能力；替换 Str/Dex/Con、AC、速度和攻击。
7. 技能/豁免熟练取并集且每项 modifier 取高，自己的 PB 正确；Beast HP/mental scores 不覆盖角色。
8. 每次 assume 获得 Druid level THP，较低值沿用 keep-max；再次变形扣新 use、更新形态和 THP，不免费刷新。
9. drop/merge/wear 逐 ref 正确落账；merged 装备无 AC/攻击等效果，wear 只在合法形体有效，装备不变大变小。
10. 不可穿装备被强制 wear 时拒绝；恢复后所有装备和地面物件准确还原，无复制、丢失或自动全穿戴。
11. 持续 conditions/spells/curses 跨形态保留，已有 Concentration 不因 assume/revert 中断。
12. Bonus Action 主动离开、持续小时到期、再次变形、Incapacitated 和死亡分别以正确 reason 恢复真形。
13. 变形期间受伤和恢复后 HP 连续，不因恢复补满；0 HP/死亡不被变形或 THP 伪造成复活。
14. 17 级 Beast form 施法被拒绝但已有 Concentration 保留；18 级 Beast Spells 可用合法法术。
15. 有价格 M 或消耗 M 的法术在 18 级仍禁止；无价格且不消耗 M 的法术允许，缺组件元数据 fail closed。
16. slot→use 仅在 current=0、自己的回合且每回合一次，消耗选定真实槽并恢复 1 use；current>0 时拒绝。
17. use→slot 消耗 1 use 并创建 F6 `wild_resurgence` 来源的 level 1 临时槽，长休前第二次拒绝；短休不重置，临时槽可超过普通 max。
18. 两个方向分别审计，apply/replay 不重复资源；客户端不能伪造 slot level、current 或每回合使用记录。
19. Evergreen 在 roll Initiative 且 current=0 时恢复 1，current>0 不变；同场只触发一次且不消耗 RNG。
20. Nature Magician 将 N=1/2/3/4 uses 转为 F6 来源化 2/4/6/8 级临时槽，N=0、超过余额和非整数被拒绝；不得套用 Font 的 5 环上限。
21. Nature Magician 长休前不能再用，与 level 1 Wild Resurgence 限制独立；长休删除两种临时槽、重置两项 usage，再恢复正常槽。
22. Longevity 参数和派生投影为每 10 年身体 1 年；无年龄数据时不虚构年龄，不误写成完全不老。
23. 变形中攻击、豁免、技能、装备资格和保留 class feature 均使用统一 actor view，无职业名硬编码分支。
24. 战斗/探索的施法、变形和恢复行为一致；失能时立即恢复并终止需要终止的后续动作。
25. apply/replay、save/load 后 known forms、活跃形态、装备模式、THP、持续时间和资源完全一致。
26. Rage、Draconic Resilience、Survivor、Heroic turn-start、法术治疗与装备协调回归通过。
27. F20 先攻及 Thief slot 不错误重复 Evergreen；其他 actor 的开场不恢复持有者的 Wild Shape。
28. 全量 rulesets、ruff、diff 通过；任何只扣资源而没有真实 Beast 事件的实现均判验收失败。

## 7. 非目标与停止条件

### 7.1 非目标

- Wild Companion、Land 特性、Polymorph、通用怪物图鉴或召唤系统。
- 新历法/衰老模拟、2D 形体编辑器、第三方 Beast 数据或前端改版。
- 以“保留 HP”为理由使用假 Beast stat block，或把所有材料成分一刀切。

### 7.2 停止条件

1. 本切片新建的官方 Beast catalog 未覆盖所需 CR0–1 形态，或 CR/Fly、熟练、攻击和形体/装备数据任一关键字段缺失；不得再等待 F6 提供其范围外数据。
2. F20 未提供可重放 roll Initiative 钩子，或 turn slot 会重复 Evergreen。
3. 地面物件、装备 merge/wear、持续小时或失能恢复无权威生命周期，不能靠视图假装。
4. 法术缺少材料价格/消耗元数据，或 Beast Spells 必须修改白名单外 spell catalog 才能判定。
5. 资源转换可超余额、无限循环、与 slots_max/reducer 冲突，或需要通用 rest service 改造。
6. 核心测试 5–18 只能只扣资源、伪造 Beast/装备/法术数据或手写预期事件通过。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_wild_shape.py -q
python -m pytest tests/rulesets/test_dnd2024_combat_start_features.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_draconic_resilience.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/character src/rulesets/dnd2024/combat src/rulesets/dnd2024/exploration/engine.py src/rulesets/dnd2024/resting/engine.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_wild_shape.py
git diff --check
git status --short
```

记录实际 Python 路径、新建 Beast 数据版本、F6 临时槽接口和测试 1–28 映射；必须展示真实 Beast、装备三模式、失能恢复、材料例外和双向资源证据。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、Beast 内容来源、形态与装备状态 schema。
2. 展示保留/替换字段对照、持续效果、THP、恢复原因及 Archdruid 三项证据。
3. 报告 RNG、资源原子性、重放/存档、战斗/探索一致性和数据缺口停止结果。
4. 不得把 Wild Companion、第三方形态或不完整 stat block 写成已实现。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 新建官方 Beast 数据与已知形态：
- 变形统计、THP、装备和恢复：
- Beast Spells 材料例外：
- Wild Resurgence / Archdruid：
- 改动文件对照白名单：
- 测试 1–28 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与数据完整性证据：
- 与契约的偏差及原因：
