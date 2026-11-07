# S0-F33 任务书：九个施法型 Eldritch Invocations

| 项 | 值 |
|---|---|
| 切片编号 | S0-F33（独立新任务） |
| 验收载体 | Armor of Shadows、Ascendant Step、Mask of Many Faces、Master of Myriad Forms、Misty Visions、One with Shadows、Otherworldly Leap、Visions of Distant Realms、Whispers of the Grave |
| 规则基准 | 官方 SRD 5.2.1；Invocation 正文 p72–74 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | F32、F4、F5 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与真实性门槛

1. 九项均通过 F32 的 Invocation 主干选择，通过 F4/F5 的法术归属和免费施法主干结算；不得新增另一套选项或 at-will 标记。
2. 每项必须保持官方等级、Self/环境限制、正常 casting time、组件、射程、专注、持续时间和目标规则，只免除 Pact Magic spell slot。
3. **法术效果未自动化时必须显式 `not_automated`，该 Invocation 不得标完整。** 不允许“免槽成功但没有法术效果”，也不得以本任务为名补一套简化法术。
4. 基线仅 Invisibility 有确定性 effect，其余八法术在 combat core 中无 effect；One with Shadows 还需权威 Dim Light/Darkness。执行以届时前置状态复核，不把下表规划当现状。

## 2. 官方英文关键原文与页码

SRD p72：

> **Armor of Shadows** — You can cast Mage Armor on yourself without expending a spell slot.

> **Ascendant Step** — Prerequisite: Level 5+ Warlock. You can cast Levitate on yourself without expending a spell slot.

> **Mask of Many Faces** — Prerequisite: Level 2+ Warlock. You can cast Disguise Self without expending a spell slot.

> **Master of Myriad Forms** — Prerequisite: Level 5+ Warlock. You can cast Alter Self without expending a spell slot.

SRD p74：

> **Misty Visions** — Prerequisite: Level 2+ Warlock. You can cast Silent Image without expending a spell slot.

> **One with Shadows** — Prerequisite: Level 5+ Warlock. While you’re in an area of Dim Light or Darkness, you can cast Invisibility on yourself without expending a spell slot.

> **Otherworldly Leap** — Prerequisite: Level 2+ Warlock. You can cast Jump on yourself without expending a spell slot.

> **Visions of Distant Realms** — Prerequisite: Level 9+ Warlock. You can cast Arcane Eye without expending a spell slot.

> **Whispers of the Grave** — Prerequisite: Level 7+ Warlock. You can cast Speak with Dead without expending a spell slot.

各法术的完整效果仍以同一 PDF 的法术条目为准，Invocation 原文不是删减法术效果的理由。

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 九项在基线 class feature catalog 中均不存在 | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` |
| 九个对应 spell 条目存在，含等级、casting time、components、duration | `content/spell_catalogs/srd_spells.json` |
| 基线只有 Invisibility 有 effect；其余八项为 `None` | `content/combat_catalogs/srd_combat_core.json::spell_effects` |
| 未自动化法术在投影中已有 `not_automated` 原因 | `src/rulesets/dnd2024/combat/view.py::_spell_statuses` |
| 校验会拒绝没有 effect 的法术，且检查 prepared/known、动作、槽位、目标和范围 | `combat/validation.py::_validate_spell` |
| 基线免费施法以 slot 0 和 grant resource 表示 | `combat/triggered_spells.py::free_cast_resource`；`combat/reducer.py::_reduce_event` |
| 探索入口另有准备法术、槽位与效果校验 | `exploration/engine.py::_spell_refs`、`_validate` |
| 基线没有权威光照字段；One with Shadows 不能默认环境成立 | 基线 `src/rulesets/dnd2024` 定点扫描 |

## 4. 精确实现契约

### 4.1 九项声明矩阵

| Invocation | 最低 Warlock | 法术与强制限制 | 基线效果状态 |
|---|---:|---|---|
| `armor_of_shadows` | 1 | Mage Armor；self | `not_automated` |
| `ascendant_step` | 5 | Levitate；self | `not_automated` |
| `mask_of_many_faces` | 2 | Disguise Self；正常自我外观规则 | `not_automated` |
| `master_of_myriad_forms` | 5 | Alter Self；self、Concentration | `not_automated` |
| `misty_visions` | 2 | Silent Image；Concentration | `not_automated` |
| `one_with_shadows` | 5 | Invisibility；self；仅 Dim Light/Darkness | 法术已有，环境须另验 |
| `otherworldly_leap` | 2 | Jump；self | `not_automated` |
| `visions_of_distant_realms` | 9 | Arcane Eye；Concentration | `not_automated` |
| `whispers_of_the_grave` | 7 | Speak with Dead；正常尸体/问答限制 | `not_automated` |

1. 上表是内容契约，不是完成清单；执行时逐项把状态更新为真实测试结果，但不得删除未实现项来伪装覆盖。
2. 所有 owner 为 `class:warlock`，source_ref 精确指向 p72 或 p74；中英文 label 与官方名称对应。
3. F32 校验等级与选择所有权；未选择 Invocation 时，即使角色以其他方式知道该法术，也不得获得本项免费施法。

### 4.2 免费施法共同语义

1. 每项提供一个仅本人可用的 `slot_level=0` 特性施法入口，法术按基础等级结算，不消耗 Pact Magic slot、不触发“本回合已耗槽”状态。
2. 免费不等于免动作、Bonus Action、Reaction、组件、专注、目标或持续时间；法术原有施法时间继续决定经济。
3. 若角色也正常准备/知道该法术，正常耗槽施法与 Invocation 免费施法是两个明确选项；不得把全部该法术施法都永久免费。
4. 免费入口不自动升环、不提供额外目标、不绕过 Rage/失能/专注冲突或 Counterspell。被反制后的资源处理遵循 F5 与官方 Counterspell。
5. available_intents、validation、resolution 和探索入口从同一 grant 推导；事件携带 invocation owner/ref、spell_ref、slot_level=0 和真实效果事件。

### 4.3 逐项特殊限制与降级

1. Armor、Ascendant、Master、One with Shadows、Otherworldly Leap 的 Invocation 施法目标必须是持有者本人；客户端指定他人拒绝。
2. Mask、Misty、Visions、Whispers 保持法术原本目标/区域与叙事或可自动化效果；不得为了可测试而改为 Self。
3. One with Shadows 只有施法时处于权威 Dim Light 或 Darkness 才提供免费入口；明亮环境、环境未知或位置超出光源均不能默认成功。光照缺失时标 `guided` 且该项不得完整。
4. 任一法术效果未完整自动化时，该 Invocation 的 `automation_status=not_automated`，可以展示名称、前提与规则来源，但不能出现在可执行免费施法列表。
5. 不为九项设置长休次数；官方均为可重复使用，只要每次施法的正常条件仍满足。临时 HP、持续状态等仍按法术自身规则处理。
6. F32 替换 Invocation 后立即移除对应免费 grant；存档中已存在的法术持续效果按其自身规则处理，不因替换无条件追溯抹除。

### 4.4 完成状态与持久化

1. 每项分别记录 `complete` 或 `not_automated`/`guided` 理由及行为测试引用；一个 Invocation 通过不能代表九项通过。
2. 所有实际施法支持 apply/replay、save/load、战斗/探索和 reducer 零 RNG；未自动化项有负例证明不会扣槽或制造空效果。
3. 公开投影只暴露当前角色可知的规则状态，不泄露 GM 专属环境或其他角色内部数据。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；`features/models.py`、`features/resolver.py`；F4/F5 已建立的 spell grant/selection 模块；`progression/advancement.py`；`combat/view.py`、`validation.py`、`resolution.py`、`reaction_spells.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；`resting/engine.py` 的 grant 清理接线；新建 `tests/rulesets/test_dnd2024_invocation_casting.py`；本任务书仅追加执行报告。

不在本切片为八项缺失法术编造 combat effects，不新增光照/位面系统。不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. 九项分别验证最低等级 1/5/2/5/2/5/2/9/7，未达级、未选择和错误 owner 均无免费 grant。
2. Armor of Shadows 仅对自己免 Pact 槽 Mage Armor；效果完整时验证 AC/持续时间，否则 `not_automated` 且不可执行。
3. Ascendant Step 5 级仅对自己 Levitate；正常动作、组件和 Concentration 保留，未自动化时不得空施法。
4. Mask of Many Faces 2 级免费 Disguise Self；真实外观效果或明确 `not_automated`，不得以 Self buff 假代替。
5. Master of Myriad Forms 5 级免费 Alter Self，保留 Concentration 和各选项真实效果；缺任一所选效果不得标完整。
6. Misty Visions 2 级免费 Silent Image，保留 Concentration/持续时间；未自动化时不产生虚假幻象状态。
7. One with Shadows 5 级仅在 Dim Light/Darkness 免费对自己 Invisibility；明亮环境拒绝，光照未知时 guided 且不完整。
8. Otherworldly Leap 2 级免费对自己 Jump；真实跳跃效果或 `not_automated`，不得只写条件名称。
9. Visions of Distant Realms 9 级免费 Arcane Eye，保留真实传感器/Concentration 规则；缺效果时不可执行。
10. Whispers of the Grave 7 级免费 Speak with Dead，保留尸体和问答限制；缺效果时不可执行。
11. 所有可执行项 slot 0 不扣 Pact 槽、不设置 slot_spell_cast；正常耗槽版本仍扣槽，两者不串权。
12. 所有可执行项仍支付正确动作/组件/专注，Counterspell、Rage、失能、目标与射程不被免费状态绕过。
13. F32 替换后旧 grant 消失、新 grant 生效；builder/advance/history、en/zh-CN、save/load 一致。
14. 每项都有独立完成状态和测试映射；未自动化负例不扣槽、不消耗物品、不产生假效果，apply/replay 与 reducer 零 RNG。
15. 既有 Invisibility、Warlock、施法、探索、rest 和 progression 回归通过。

## 7. 非目标与停止条件

非目标：补齐九个法术本体、F32/F34/F35 选项、通用光照/传感器/尸体问答系统、多职业和前端表现。

停止条件：F32/F4/F5 未合入；执行侧试图把未自动化法术标完整；无法区分 Invocation 免费与同法术正常耗槽；Self/环境门槛无权威事实；需要新增法术效果或白名单外系统才能换绿。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_invocation_casting.py -q
python -m pytest tests/rulesets/test_dnd2024_invocation_core.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_progression.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_invocation_casting.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 九项逐项 complete/not_automated/guided 状态及理由：
- 免 Pact 槽、Self、等级和环境限制证据：
- 测试 1–15 逐项结果、Counterspell、apply/replay、save/load：
- 白名单、验收输出、偏差与未决事项：
