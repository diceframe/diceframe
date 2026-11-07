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

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Contact Patron 自动成功与 `not_automated` 边界：
- 四个 Mystic 独立槽及同环替换：
- Spell Mastery action/spellbook/长休限制：
- Signature 独立短长休次数：
- 免槽不免其他规则及原子扣账：
- 白名单文件对照：
- 测试 1–25 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重建与重放证据：
- 偏差、停止原因与未决事项：
