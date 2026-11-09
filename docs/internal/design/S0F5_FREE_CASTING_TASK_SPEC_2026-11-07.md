# S0-F5 任务书：独立免费施法槽与同环替换

| 项 | 值 |
|---|---|
| 切片编号 | S0-F5（独立新任务，不覆盖旧 SP） |
| 验收载体 | `contact_patron`、`mystic_arcanum_6`、`mystic_arcanum_7`、`mystic_arcanum_8`、`mystic_arcanum_9`、`spell_mastery`、`signature_spells`；并回归 `pact_magic` 的真实槽位 |
| 通用能力 | 无槽施法资源、每槽独立次数、常驻准备、长短休重置、同环选择替换 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Warlock p72–75（核心特性在 p72）；Wizard p79–81（Spell Mastery/Signature 在 p79） |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-B、S0-D2、S0-F4 已合入；常驻准备、来源选择及长休计数可用 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得将六个免费施法载体做成一个共享“免费法术”布尔值。
2. 每槽保存独立 spell、环阶、来源、次数与 recharge；法术效果仍走通用真实施法链。
3. 免费施法只免除 spell slot，不免除施法时间、成分、目标、专注、攻击/豁免或特性明确保留的其他成本。
4. 无真实自动化效果的法术继续 `not_automated`；本切片实现资源与授予，不用空成功事件补齐未自动化法术。
5. 客户端不得提交“免费”标志；服务端必须根据 owner、槽身份、当前次数和所选环阶自行判定。

## 2. 官方英文关键原文与语义边界

SRD p72：

> **Contact Patron.** You always have the Contact Other Plane spell prepared. With this feature, you can cast the spell without expending a spell slot to contact your patron, and you automatically succeed on the spell’s saving throw.

> Once you cast the spell with this feature, you can’t do so in this way again until you finish a Long Rest.

> **Mystic Arcanum.** Choose one level 6 Warlock spell as this arcanum. You can cast your arcanum spell once without expending a spell slot, and you must finish a Long Rest before you can cast it in this way again.

> ... another Warlock spell ... at Warlock levels 13 (level 7 spell), 15 (level 8 spell), and 17 (level 9 spell). You regain all uses of your Mystic Arcanum when you finish a Long Rest.

> Whenever you gain a Warlock level, you can replace one of your arcanum spells with another Warlock spell of the same level.

SRD p79：

> **Spell Mastery.** Choose a level 1 and a level 2 spell in your spellbook that have a casting time of an action. You always have those spells prepared, and you can cast them at their lowest level without expending a spell slot. To cast either spell at a higher level, you must expend a spell slot.

> Whenever you finish a Long Rest, you can study your spellbook and replace one of those spells with an eligible spell of the same level from the book.

> **Signature Spells.** Choose two level 3 spells in your spellbook as your signature spells. You always have these spells prepared, and you can cast each of them once at level 3 without expending a spell slot. When you do so, you can’t cast them in this way again until you finish a Short or Long Rest. To cast either spell at a higher level, you must expend a spell slot.

语义边界：Contact Patron 的自动成功只限用本特性免费联系 patron；Mystic 四环各自一次；Spell Mastery 是无限次最低环且仅限 casting time 为 action 的 1/2 环法术；Signature 两个 3 环各自一次、短休也恢复。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 已有按职业资源/特性免除槽的入口，可扩展但不能把免费次数塞进普通槽数 | `combat/triggered_spells.py::free_cast_resource` |
| 普通准备容量与 spellbook 合法性由统一选择器处理 | `spells/selection.py::requirements`、`validate`、`configure` |
| 战斗只投影可自动化法术，无效果法术不能因免费而绕过校验 | `combat/view.py::_available_spells`；`combat/validation.py::_validate_spell` |
| 法术结算走既有 spell events，免费资源须与真实 cast 同事务 | `combat/resolution.py::_spell_events` |
| 短休/长休有不同资源恢复入口 | `resting/engine.py::complete_short_rest`、`complete_long_rest`、`_recover_class_resources` |
| Contact Other Plane 等目录条目存在但 `effect` 为空，不得据此宣称完整问答效果已实现 | `content/spell_catalogs/srd_spells.json` 的 `contact_other_plane` 条目 |

## 4. 精确数据与行为契约

### 4.1 统一免费施法记录

1. 每个槽以 `(owner, feature_id, free_slot_id)` 为身份，保存 `spell_ref`、`spell_level`、`uses_remaining`、`recharge`；Spell Mastery 两个无限槽不伪造有限次数。
2. 可用性由真实角色特性、当前选择、法术自动化状态和目标共同决定；伪造 slot id、spell_ref 或免费环阶均拒绝。
3. 只有实际施法通过验证并进入结算才消费有限次数；非法目标、成分/时间不满足或 `not_automated` 拒绝时不扣。法术随后攻击未中或目标豁免成功仍属于已施放，不退款。
4. 同一施法只能使用一个免费来源；不得同时扣普通槽和免费次数，也不得用 Signature/Mystic 重复免除同一成本。
5. 常驻准备按 S0-F4 来源聚合且不占普通容量；撤销/替换只移除自己的来源，保留其他合法来源。

### 4.2 Warlock

1. Contact Patron 9 级常驻准备 Contact Other Plane；每 Long Rest 一次无槽施法，目标联系对象必须是 patron，且该次法术自身 saving throw 自动成功。普通槽施法不继承此自动成功。
2. Contact Other Plane 的叙事问答效果不属于本切片；若仍无真实执行器，保持 `not_automated` 且不消费免费次数。不得扣次后返回“已联系”的空结果。
3. Mystic Arcanum 在 11/13/15/17 级分别新增一个 6/7/8/9 环 Warlock spell 槽，四个 id 和次数完全独立，每槽 Long Rest 一次，免费施法只能按其固定环阶。
4. 每次获得 Warlock level 最多替换一个已有 arcanum，新法术必须为同环 Warlock spell；不得借一次升级同时换两个或跨环替换。
5. 替换不刷新该槽已消耗次数；新获得的另一环槽按自身规则初始化。Long Rest 一次恢复全部四环已消耗次数，Short Rest 不恢复。

### 4.3 Wizard

1. Spell Mastery 18 级从自己的 spellbook 选择一项 1 环和一项 2 环，二者 casting time 必须恰为 action；Reaction、Bonus Action、1 minute 或 Ritual-only 条目不合法。
2. 两项均常驻准备、不占容量，可以各自无限次以最低环无槽施法，不需要休息重置；以更高环施放时必须正常消耗对应槽。
3. 每次 Long Rest 后可选择研究 spellbook 并替换其中至多一项为同环且符合条件的法术；不是自动换选，也不能一次长休更换两项。
4. Signature Spells 20 级从 spellbook 选两项不同 3 环法术，均常驻准备；每项每 Short 或 Long Rest 一次以 3 环无槽施法，次数互不共享。
5. Signature 以 4 环以上施放必须消耗普通槽且不使用免费次数；官方本段未给出升级替换规则，本切片不得自造 Signature 升级换选。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`rest_catalogs/srd_recovery.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/spells/selection.py`、`progression/advancement.py`、`features/resolver.py`。
3. `src/rulesets/dnd2024/combat/triggered_spells.py`、`view.py`、`validation.py`、`resolution.py`、`reducer.py`。
4. `src/rulesets/dnd2024/resting/engine.py`：仅本切片 recharge 与 Spell Mastery 长休选项。
5. 新建 `tests/rulesets/test_dnd2024_free_casting.py`；本任务书仅可追加执行报告。
6. 不新增 Contact Other Plane 问答效果或其他法术效果；需要白名单外文件时停止。

## 6. 编号测试矩阵

1. Contact Patron、四个 Mystic、Spell Mastery、Signature 在 9/11/13/15/17/18/20 级正确授予。
2. 低等级、错误职业/子职、伪造 owner 或伪造 free_slot_id 均不可用。
3. Contact Other Plane 常驻且不占容量；免费联系 patron 每长休一次并自动成功该法术豁免。
4. Contact Patron 普通槽施法不获得特性自动成功；短休不恢复免费联系。
5. Contact Other Plane 无执行器时明确 `not_automated`，请求不扣次数、不生成空问答成功。
6. Mystic 11/13/15/17 分别只选 6/7/8/9 环 Warlock spell，其他列表或环阶拒绝。
7. 四个 Mystic 槽各有独立一次；用 6 环不影响 7/8/9，长休全部恢复而短休不恢复。
8. Mystic 免费施法不扣 Pact Magic 槽，固定环阶不能免费上调；普通槽施法不错误消耗免费次数。
9. 每次 Warlock 升级最多替换一个同环 arcanum，跨环、多槽同换和非法列表均原子拒绝。
10. 替换已消耗 Mystic 槽不刷新次数；新获得的其他环槽不篡改旧槽状态。
11. Spell Mastery 必须来自自己的 spellbook，恰一项 1 环和一项 2 环且 casting time 为 action。
12. Reaction、Bonus Action、1 minute、仅 Ritual 或不在 spellbook 的选择均校验拒绝，不进入角色。
13. Spell Mastery 两项最低环可反复无槽施法，无次数上限、不需要短长休重置。
14. Spell Mastery 高环施放消耗真实槽；免费与高环路径不重复扣账。
15. Spell Mastery 每次长休研究最多替换一项同环合法法术，不在短休或未研究时自动替换。
16. Signature 必须从 spellbook 选两项不同 3 环法术，常驻不占容量，其他环阶/重复选择拒绝。
17. 两个 Signature 免费次数独立；各以 3 环用一次后短休或长休分别恢复。
18. Signature 高环施放扣普通槽且不扣免费次数；一个槽消耗不影响另一个。
19. 不为 Signature 提供官方未写的升级替换入口。
20. 所有免费施法仍验证施法时间、成分、目标、专注和攻击/豁免；免槽不等于免其他规则。
21. 非法目标或 `not_automated` 不消费次数；真实施放后攻击未中/目标豁免成功也不退款。
22. 同一施法只能选择一种免费来源，不能叠加 Mystic/Signature/其他免费资源。
23. 常驻来源被替换时只撤销自己的来源，普通准备或其他授予的同名法术保留。
24. 存档/加载、升级重建、休息和重放后每槽 spell、环阶与次数完全一致。
25. 既有 triggered spells、spell selection、resting 与普通槽施法测试无回归。

## 7. 非目标与停止条件

### 非目标

- Contact Other Plane 的完整叙事问答、任何新 Mystic/Signature 法术效果、魔法物品免费施法。
- Eldritch Master、Magical Cunning、Font of Magic（属 S0-F6）及其他职业免费施法。
- 用无限普通槽、共享布尔值或客户端 `free=true` 代替独立资源。

### 停止条件

1. S0-B/D2/F4 未合入，或常驻来源/选择槽不能保持同环身份。
2. 免费资源无法与真实施法原子结算，或必须绕过 `not_automated` 才能验收。
3. 四个 Mystic 或两个 Signature 无法独立持久化、休息与重放。
4. 需要白名单外文件或新增本非目标法术效果。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_free_casting.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_spell_remediation.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/spells src/rulesets/dnd2024/combat src/rulesets/dnd2024/resting src/rulesets/dnd2024/progression tests/rulesets/test_dnd2024_free_casting.py
git diff --check
```

## 9. 执行报告

### 9.1 基线、分支与提交

- 实际基线：`feat/dnd2024-s0f4-spell-grants@745baaee`（F4 链头，含 2026-11-09 的两项后续裁定）。任务书写的祖先基线 `integration/dnd2024-srd@35653f81` 是 F4 之前的共同祖先。
- 分支：`feat/dnd2024-s0f5-free-casting`，stack 在 F4 之上，不改动 F4 的提交。
- 提交：
  1. `5aa3bf88` 七条免费施法声明
  2. `330457e3` 免费槽的校验、投影、扣账、recharge 与长休研究
  3. `910733c0` 25 条矩阵测试

### 9.2 七条声明与扁平编码

| 特性 | owner | 起始等级 | 槽 | 环阶 | recharge | 次数 | 来源 |
|---|---|---|---|---|---|---|---|
| `contact_patron` | `class:warlock` | 9 | `contact_patron` | 5 | `long_rest` | 1 | 固定法术 |
| `mystic_arcanum_6` | `class:warlock` | 11 | `mystic_arcanum_6` | 6 | `long_rest` | 1 | 邪术师列表 |
| `mystic_arcanum_7` | `class:warlock` | 13 | `mystic_arcanum_7` | 7 | `long_rest` | 1 | 邪术师列表 |
| `mystic_arcanum_8` | `class:warlock` | 15 | `mystic_arcanum_8` | 8 | `long_rest` | 1 | 邪术师列表 |
| `mystic_arcanum_9` | `class:warlock` | 17 | `mystic_arcanum_9` | 9 | `long_rest` | 1 | 邪术师列表 |
| `spell_mastery` | `class:wizard` | 18 | `spell_mastery_1` / `_2` | 1 / 2 | `unlimited` | ∞ | 自己的法术书 |
| `signature_spells` | `class:wizard` | 20 | `signature_1` / `_2` | 3 / 3 | `short_rest` | 1 | 自己的法术书 |

职业进度表里早就写着 `contact_patron`（9 级）与 `mystic_arcanum_6/7/8/9`（11/13/15/17 级）四个独立 id，`spell_mastery`（18）与 `signature_spells`（20）。因此秘奥按**四个特性**声明，每特性一格，"四格独立"是结构而不是约定。

一个槽是一个字符串：`"<slot_id>:<环阶>:<recharge>:<次数>:<法术>"`，`-` 表示由玩家选，`0` 次表示无限。扁平编码不是审美选择：`features/models.py::_require_scalar` 只接受标量与标量列表，嵌套对象会被加载器拒绝，而该文件不在白名单。

### 9.3 Contact Patron 与 `not_automated` 边界

- 9 级常驻 `spell:contact_other_plane`，走 S0-F4 的 `always_prepared` 通道，来源聚合成一条 `class_feature` 行，**不占普通准备容量**（矩阵 3）。
- 免费槽 `contact_patron` 是固定法术槽：`spell_ref` 与环阶由声明给定，客户端只能确认不能改写（提交别的法术得到 `is not a player choice`）。
- `contact_other_plane` 在 `spell_catalogs` 里存在，但 `combat_catalogs/srd_combat_core.json` 的 `spell_effects` 里**没有条目**，因此 `automation_status == not_automated`。施法请求在 `_validate_spell` 的第一道门就被拒（`spell is not deterministically automated yet`），**不产生事件、不扣次数、不升级版本号**（矩阵 5）。
- `free_cast_save_auto: true` 声明在特性上并投影到槽位记录（`save_auto_succeed`），但**没有任何消费方**：这条法术没有执行器，本切片不伪造它的豁免，也不生成"已联系宗主"的空结果。矩阵 4 因此验的是"自动成功只属于这条授予、不挂在法术或普通槽上"，而不是自动成功本身。这是任务书 §4.2.2 明写的边界。

### 9.4 四个 Mystic 的独立性与同环替换

- 每格 `(owner, feature_id, slot_id)` 三元组独立，记录里带 `spell_ref / spell_level / recharge / uses_remaining`；**记录与声明不一致**（被改过环阶或 recharge）时该槽 `valid: false`，既不参与施法也不被当成"已选"（矩阵 2）。
- 免费槽只按**法术自身环阶**匹配：`free_cast_resource(actor, spell_ref, spell_level)` 要求 `slot.spell_level == spell.level`，所以不存在"免费上调"；邪术师最高只有 5 环契约槽，6 环法术只有免费这一条路（矩阵 8）。
- 每次获得邪术师等级最多替换**一个**已有秘奥：`free_cast_replacement: 1` 且四格共享 `free_cast_replacement_group: "mystic_arcanum"`，跨组按组计数，一次提交换两格直接拒绝（矩阵 9）。
- 替换不刷新次数：`_free_cast_record` 保留原 `uses_remaining`；新解锁的另一格按自身规则初始化（矩阵 10）。
- 长休恢复四格、短休一格都不恢复（矩阵 7）。

因为 6–9 环**没有任何法术有自动化效果**（目录只到 4 环），秘奥的扣减无法经由真实施法到达。矩阵 7 因此提交与真实施法完全相同的那一条 reducer 事件 `dnd2024.free_cast.spent` 来扣账，再验"同一格花第二次被 reducer 拒绝""另外三格不受影响""长休补满、短休不动"。扣账走的是生产代码，不是测试里手改计数。

### 9.5 Spell Mastery 的 action / spellbook / 长休限制

- 两项分别锁 1 环与 2 环（`spell_mastery_1` / `_2`），`free_cast_casting_time: "Action"`。判定用 `_casting_segment()` 归一化施法时间：`"Action or Ritual"` 算 Action，`"1 minute or Ritual"`、`"Bonus Action, ..."`、`"Reaction, ..."` 都不算（矩阵 11、12）。
- 来源是**自己的法术书**：`_free_cast_ref_error` 对 `source == spellbook` 的槽比对 `spellbook_refs`，不在书里的法术被拒（矩阵 12）。目录里没有"仅 Ritual"的施法时间取值，所以"仪式"这一条用 `1 minute or Ritual` 的 `alarm` 覆盖。
- 无限次：记录里**没有** `uses_remaining` 字段（`uses: 0`），`_spell_cost_events` 对无限槽**不生成** `free_cast.spent` 事件——没有计数可扣，就不该发事件。三次连续免费施法后 1 环槽仍是 4/4（矩阵 13）。
- 以更高环施放走普通槽：`slot_level=2` 生成 `dnd2024.spell.slot_spent`，不碰免费槽（矩阵 14）。
- 长休研究：`complete_long_rest(payload, free_cast=...)` 先校验再落账，一次最多换一项（`free_cast_rest_replace: 1`），短休给这个参数直接 `RestError`（矩阵 15）。落账顺序是"先换、再按 recharge 补满"，否则会把"研究完睡一觉"算成"睡完又花掉一次"。

### 9.6 Signature 的独立短长休次数

- 两项必须不同（`_free_cast_duplicate_errors` 按 `feature_id` 分组去重），必须 3 环，必须来自法术书（矩阵 16）。
- 两项都常驻准备、不占容量，各 1 次、`short_rest`：一次 `dispel_magic` 只扣 `signature_1`，`signature_2` 仍满（矩阵 17）。
- 以 4 环施放扣普通槽、不扣免费次数（矩阵 18）。
- 官方本段没有升级换选规则，本切片也不提供：`free_cast_replacement: 0` + `free_cast_rest_replace: 0`，两条通道都拒绝（矩阵 19）。

### 9.7 免槽不免其他规则与原子扣账

- 免费施法只跳过 `spell.slot_spent`。动作经济、组件与目标、专注、施法攻击/豁免全部照旧：动作已花掉时免费施法仍被拒（矩阵 20）。
- 扣账与施法同事务：成本事件在 `_spell_cost_events` 一处生成，进入结算才落账，因此被拒的请求不扣次数；进入结算后即使目标没有可解效果也不退款（矩阵 21）。
- 一次施法只用一个来源：`free_cast_resource` 返回单行，成本事件只发一条。顺序是 **无限槽 > 专长 > 有限槽 > 职业资源**：法术精通永远不会耗尽，让一次长休一次的专长或一次长休一次的秘奥去顶替它，是不可恢复的浪费（矩阵 22）。
- 常驻来源被替换只撤销自己的来源：`spell_mastery_1` 从 `magic_missile` 换成 `burning_hands` 后，`magic_missile` 在准备视图里退回只剩 `class_list` 一条来源（矩阵 23）。

### 9.8 白名单文件对照

| 任务书 §5 | 实际 |
|---|---|
| 1 `srd_class_features.json`、`srd_recovery.json` 及 locale | 只改 `srd_class_features.json`（+7 特性）。`srd_recovery.json` **未改**：免费槽的 recharge 由特性声明自带，不进 `class_resources`，否则一个"无限次"会被塞进有限次数的资源表。locale 七条标签基线已有，未改。 |
| 2 `spells/selection.py`、`progression/advancement.py`、`features/resolver.py` | 三个都改。 |
| 3 `combat/triggered_spells.py`、`view.py`、`validation.py`、`resolution.py`、`reducer.py` | 改了四个；`resolution.py` **未改**——免费施放没有独立结算分支，成本只在 `_spell_cost_events` 一处产生，多一个分支就多一处可以忘记扣账的地方。 |
| 4 `resting/engine.py` | 改：`_recover_free_cast_slots`、`_plan_free_cast_rebind` / `_apply_free_cast_rebind`、短休拒收 `free_cast`。 |
| 5 新建 `test_dnd2024_free_casting.py` | 新建，25 条。 |
| 6 不新增法术效果 | 未新增任何 `spell_effects` 条目，`contact_other_plane` 等仍是空 effect。 |

白名单外改了**一处测试**：`tests/rulesets/test_dnd2024_spell_grants.py::test_matrix_5_fiend_table_reuses_the_canonical_spells`。该断言原本比较"邪术师 9 级全部授予 == 邪魔表"，而 9 级现在确实多一条 Contact Other Plane（SRD 如此）。改为只比较 `owner == subclass:fiend_patron` 的行，断言强度不变——它验的本来就是邪魔表本身是否逐项正确。

`runtime.py` **未改**，因此长休研究目前只能在引擎入口触发（见 9.11 未决 1）。

### 9.9 验收

| 命令 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_free_casting.py -q` | **25 passed** |
| `pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_spell_remediation.py -q` | 见下行（合并跑的九文件） |
| 施法/休息九文件定向回归（含 spell_grants、advancement_access、origin_feat_spells、smite、dispel_magic、class_features） | **184 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1756 passed**（1669.64s）。11 条与 F2 已知基线同名同数，全在 `test_dnd2024_character_builder.py`；通过数 = 基线 1731 + 本切片 25，**净引入 0 失败** |
| `ruff check`（spells/combat/resting/progression/features + 新测试） | All checks passed |
| `git diff --check` | 干净 |

### 9.10 存档、重建与重放

- 存档往返：`json.loads(json.dumps(sheet))` 后四个槽的 `spell_ref / spell_level / recharge / uses_remaining` 逐字段相同（矩阵 24）。
- 升级重建：19 级升 20 级时只提交两项签名，`spell_mastery_*` 的既有选择原样保留（`_update_spellcasting` 合并而不覆盖，矩阵 24）。
- 重放：授予与槽位都不缓存在派生结构里——`grant rows` 每次从声明与记录重算，`free_cast_slots` 只有"玩家选了什么 / 还剩几次"两个可变字段，因此一次重放不会带出第二条来源。
- 休息：recharge 只改 `uses_remaining`，不碰 `spell_ref`；长休研究的落账在恢复之后，研究过的槽也是满的。

### 9.11 偏差、停止原因与未决事项

1. **长休研究没有运行时转发**。`runtime.complete_rest()` 不转发 `free_cast`，而 `runtime.py` 不在白名单（§5.6 的停止条件 4）。引擎入口 `complete_long_rest(payload, free_cast=...)` 与 `payload["free_cast"]` 都已留好，接上只需要在 runtime 加一条选项通道——与 F4 当时 `land_terrain` 的缺口完全同型，那次由用户裁定后补齐。**未决**。
2. **Contact Patron 的自动成功无消费方**。见 9.3。不伪造豁免、不生成空结果。
3. **秘奥四格只能靠事件扣账**。6–9 环没有自动化法术，这是任务书 §7 停止条件 2 允许的边界（不是绕过 `not_automated`）。等后续法术效果切片补上 6–9 环后，`free_cast.spent` 会自动成为真实施法的沉没成本，无需改本切片的代码。
4. **`srd_recovery.json` 未改**。免费槽的 recharge 写在特性里；把它塞进 `class_resources` 会让"无限次"变成一张有上限的资源表，反而破坏 §4.1.1 的"不伪造有限次数"。
5. **`-` 与 `0` 两个扁平哨兵**。`free_cast_slots` 的一行是 `<slot>:<环阶>:<recharge>:<次数>:<法术>`，`-` = 玩家选、`0` = 无限次。原因同 §9.2：`_require_scalar` 不接受嵌套对象，而 `features/models.py` 不在白名单。解析失败的行直接丢弃，不猜测。
6. **不可"仅仪式"样本**。目录里没有只有 Ritual 一种施法时间的法术，矩阵 12 的仪式一条用 `1 minute or Ritual` 的 `alarm` 覆盖；判定逻辑本身按"首个施法模式必须恰为 Action"实现，与样本无关。
7. **白名单外改一行测试**。见 9.8 末尾。
8. **无限槽优先于专长免费施放**。这是一条刻意的排序（§9.7），不是继承来的行为：基线里专长优先于职业资源，而无限槽若排在专长后面，`Magic Initiate` 的一次长休一次会被法术精通白白吃掉。
