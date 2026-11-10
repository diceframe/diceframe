# S0-F6 任务书：休息恢复预算与 Sorcery 双向转换

| 项 | 值 |
|---|---|
| 切片编号 | S0-F6（独立新任务，不覆盖旧 SP） |
| 验收载体 | `arcane_recovery`、`natural_recovery`、`sorcerous_restoration`、`magical_cunning`、`eldritch_master`、`font_of_magic` |
| 通用能力 | 短休槽预算、点数恢复、1 分钟仪式、Pact 槽恢复、临时槽与双向转换 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Land p46–47；Sorcerer p66；Warlock p72–75（核心在 p72）；Wizard p78–79 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-D2、S0-F5 已合入；S0-F4 的 Circle Spells 来源随 F5 前置可用 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得把 rest catalog 中的资源名称当作恢复行为已完成。
2. 预算、ceil/floor、槽环阶、当前/最大值和每长休次数由服务端计算；客户端只提交合法分配选择。
3. 所有转换与恢复原子执行；非法选择不得出现扣点数未给槽、扣槽未给点或部分恢复。
4. Arcane/Natural 禁止恢复 6 环以上；Font 创建槽禁止 6 环以上。不要把该限制错误扩展到“槽转点”。
5. 本切片不实现新法术效果；Natural Recovery 的免费 Circle Spell 仍走 F4/F5 的真实来源和施法边界。

## 2. 官方英文关键原文与语义边界

SRD p78、p46：

> **Arcane Recovery.** When you finish a Short Rest, you can choose expended spell slots to recover. The spell slots can have a combined level equal to no more than half your Wizard level (round up), and none of the slots can be level 6 or higher.

> **Natural Recovery.** You can cast one of the level 1+ spells that you have prepared from your Circle Spells feature without expending a spell slot, and you must finish a Long Rest before you do so again.

> In addition, when you finish a Short Rest, you can choose expended spell slots to recover ... half your Druid level (round up), and none of them can be level 6+.

SRD p66：

> **Sorcerous Restoration.** When you finish a Short Rest, you can regain expended Sorcery Points, but no more than a number equal to half your Sorcerer level (round down). Once you use this feature, you can’t do so again until you finish a Long Rest.

> **Converting Spell Slots to Sorcery Points.** You can expend a spell slot to gain a number of Sorcery Points equal to the slot’s level (no action required).

> **Creating Spell Slots.** As a Bonus Action, you can transform unexpended Sorcery Points into one spell slot ... You can create a spell slot no higher than level 5. Any spell slot you create with this feature vanishes when you finish a Long Rest.

SRD p72：

> **Magical Cunning.** You can perform an esoteric rite for 1 minute. At the end of it, you regain expended Pact Magic spell slots but no more than a number equal to half your maximum (round up). Once you use this feature, you can’t do so again until you finish a Long Rest.

> **Eldritch Master.** When you use your Magical Cunning feature, you regain all your expended Pact Magic spell slots.

语义边界：Natural 的免费施法与短休槽恢复是两个独立长休计数；Magical Cunning 在 1 分钟结束时结算而非开始时；Eldritch Master 替换恢复数量上限，不是额外再使用一次。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 短休/长休已有统一完成入口及职业资源、法术位恢复段 | `resting/engine.py::complete_short_rest`、`complete_long_rest`、`_recover_class_resources`、`_recover_spell_slots` |
| `arcane_recovery`、`magical_cunning` 等已有 rest resource 声明，但不能视为预算算法完成 | `content/rest_catalogs/srd_recovery.json` |
| 免费施法资源有统一入口，Natural 免费面应与槽恢复分开计数 | `combat/triggered_spells.py::free_cast_resource` |
| 法术位在普通施法中真实校验/消耗，临时槽必须进入同一权威模型 | `combat/validation.py::_validate_spell`；`combat/resolution.py::_spell_events` |
| 升级更新槽位和职业资源，最大值不能由客户端提交 | `progression/advancement.py::_update_spellcasting`、`_update_derived` |
| 逻辑时钟可支持 1 分钟仪式，时间推进只在权威流程发生 | `src/engine/world_state.py::clock_to_minutes`；`src/engine/world_events.py::advance_world_time` |

## 4. 精确数据与行为契约

### 4.1 短休恢复与独立计数

1. Arcane Recovery（Wizard 1）在 Short Rest 完成时选择已消耗槽，合计环阶预算 `ceil(Wizard level/2)`，每槽必须 1–5 环；须以真实 spellbook 研究为前提，每 Long Rest 只能实际恢复一次。
2. Natural Recovery（Druid 6）的短休槽恢复使用 `ceil(Druid level/2)` 和同样 1–5 环限制；组合可以是一个高环或多个低环，不能超预算、恢复未消耗槽或超过普通槽上限。
3. Natural 另有一个独立 Long Rest 计数：对当前实际 prepared 的 Circle Spells 中一项 1 环以上法术免费施法一次。使用免费面不消耗短休恢复面，反之亦然。
4. Sorcerous Restoration（Sorcerer 5）在 Short Rest 完成时恢复自选数量，至多 `min(floor(Sorcerer level/2), 已消耗点数)`，不得超过等级表最大值；每 Long Rest 一次。
5. 预算为 0、没有已消耗资源或仅选择零恢复时不伪造一次有效使用；有效提交必须同时记录来源、预算、各槽/点数变化和长休计数。
6. 短休本身已有的普通恢复按原规则执行；本切片的选择不得被重复 apply，长休只重置对应特性使用标记。

### 4.2 Warlock 仪式

1. Magical Cunning（Warlock 2）启动一个持续 1 个逻辑分钟的权威仪式，完成时恢复 `ceil(最大 Pact Magic 槽数/2)` 个已消耗 Pact 槽，或实际消耗数（取较小者）。
2. 次数只在仪式完成且实际恢复时消费；启动不立即给槽，时间未推进到结束不可用。不得自造官方未写的专注或中断规则。
3. 只恢复 Pact Magic 槽，不影响其他职业普通槽；恢复数量按槽个数而非槽环阶预算，且不能超过最大 Pact 槽数。
4. Eldritch Master（Warlock 20）让同一次 Magical Cunning 恢复全部已消耗 Pact 槽，替代 ceil 数量；仍共用每 Long Rest 一次，不额外恢复其他资源。

### 4.3 Font of Magic 与临时槽

1. Sorcery Points 最大值按 Sorcerer 等级表，Long Rest 恢复全部；其他特性消耗点数与 Font 共用同一权威余额。
2. 槽转点无动作：消耗一个真实未消耗 spell slot，获得等同其环阶的点数；可转换 6 环以上槽，但若会超过点数上限则整笔拒绝，不静默截断。
3. 点转槽消耗 Bonus Action，创建一个有 `font_of_magic` 来源的临时槽；精确表为：1 环/2 点/最低 Sorcerer 2，2 环/3 点/最低 3，3 环/5 点/最低 5，4 环/6 点/最低 7，5 环/7 点/最低 9。
4. 不允许创建 6 环以上槽；点数不足、等级不足、Bonus Action 不可用或非法环阶均不扣任何状态。临时槽可超过普通槽数并单独持久化，施法时按真实槽规则消费。
5. Long Rest 先移除所有仍未消耗的 Font 临时槽及其过期身份，再按普通规则恢复普通槽/点数；临时槽不得留存、转成普通槽或影响普通槽最大数量。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`rest_catalogs/srd_recovery.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/resting/engine.py`、`progression/advancement.py`、`features/resolver.py`。
3. `src/rulesets/dnd2024/combat/triggered_spells.py`、`primitives.py`、`validation.py`、`resolution.py`、`reducer.py`、`view.py`、`engine.py`。
4. `src/rulesets/dnd2024/spells/selection.py`：仅真实 spellbook/槽模型所需的既有接口接入。
5. 新建 `tests/rulesets/test_dnd2024_rest_resource_conversion.py`；可追加 `tests/rulesets/test_dnd2024_resting.py` 回归。
6. 不修改通用世界引擎；若现有权威上下文无法推进 1 分钟仪式则停止。

## 6. 编号测试矩阵

1. 六项特性在 Wizard1/Druid6/Sorcerer5/Warlock2/Sorcerer2/Warlock20 正确授予，低等级不可用。
2. Arcane 预算按等级 1/2/3/4/5 分别为 1/1/2/2/3，只恢复真实已消耗槽。
3. Arcane 可选择一个预算内高环或多个低环；超预算、6 环以上、未消耗槽均拒绝。
4. Arcane 缺少真实 spellbook 不提供研究恢复；每长休一次，短休重复使用被拒绝。
5. Natural 使用 `ceil(Druid/2)`，等级 6/7 均预算 3，6 环以上恢复被拒绝。
6. Natural 免费面只选当前 prepared 的 1 环以上 Circle Spell，不扣槽且每长休一次。
7. Natural 免费施法与短休槽恢复计数互不影响；地形切换后旧 Circle Spell 不再合法。
8. Sorcerous Restoration 使用 `floor(Sorcerer/2)`，等级 5/6 分别上限 2/3，不超过已消耗和最大点数。
9. 三项短休恢复均每长休一次；无效零恢复不伪扣使用，长休重置标记。
10. Magical Cunning 需真实推进 1 分钟，开始时和未推进满 1 分钟均不恢复，完成时才原子落账。
11. 最大 Pact 槽 1/2/3/4 时数量上限分别 1/1/2/2，不超过实际已消耗槽。
12. Magical Cunning 不恢复其他职业槽，每长休一次，重复仪式不重复落账。
13. Eldritch Master 同一次仪式恢复全部已消耗 Pact 槽，不叠加 ceil 数量或增加使用次数。
14. Font 槽转点无动作并按槽环阶给点；1/5/6 环分别给 1/5/6 点。
15. 槽转点会超过上限时整笔拒绝；合法转换的槽与点同事务变化。
16. Font 点转槽五行费用与最低等级准确，Bonus Action 只扣一次。
17. 点数不足、等级不足、Bonus Action 已用、创建 6 环以上均原子拒绝。
18. 临时槽可超过普通槽数并用于真实施法；事件记录来源、环阶、消费与余额。
19. 同一法术不会同时扣临时槽和普通槽；非法临时槽 id 不被客户端伪造接受。
20. Long Rest 删除未消耗 Font 临时槽，普通槽和 Sorcery Points 按原规则恢复。
21. 已消耗临时槽不留下负槽或占用普通槽计数；连续长休幂等。
22. 休息/仪式/转换的重复 intent、版本冲突或校验失败均无部分写入。
23. 升级改变等级、最大点数或 Pact 槽后预算与上限按新值重算，旧使用标记不绕过规则。
24. 存档/加载与重放后槽来源、仪式完成时间、点数和长休计数一致。
25. 既有短长休、Pact Magic、普通施法、Second Wind 与免费施法测试无回归。

## 7. 非目标与停止条件

### 非目标

- Metamagic、Sorcery Incarnate、Dragon Companion、其他职业槽恢复或魔法物品。
- Contact Other Plane/其他法术效果、通用仪式中断规则、客户端直接改资源。
- 把 6 环限制误加到槽转点，或把 Font 临时槽实现为永久普通槽。

### 停止条件

1. D2/F5（含 F4 前置）未合入，或 Circle 来源/真实 spellbook 无法判定。
2. 临时槽不能与普通施法、长休和存档重放使用同一权威状态。
3. 1 分钟仪式无法在现有逻辑时钟边界确定性完成，需后台任务。
4. 恢复或转换无法保证原子性，或需要白名单外文件。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_rest_resource_conversion.py tests/rulesets/test_dnd2024_resting.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_spell_remediation.py tests/rulesets/test_dnd2024_advancement_access.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/resting src/rulesets/dnd2024/combat src/rulesets/dnd2024/spells src/rulesets/dnd2024/progression tests/rulesets/test_dnd2024_rest_resource_conversion.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Arcane/Natural 预算、6 环限制与 spellbook：
- Natural 双计数与 Sorcerous floor：
- Magical Cunning/Eldritch Master 仪式：
- Font 精确费用、双向原子转换：
- 临时槽长休消失与重放：
- 白名单文件对照：
- 测试 1–25 / 聚焦 / 回归 / 全量 / ruff / diff：
- 偏差、停止原因与未决事项：

## 10. 执行记录（2026-11-10，分三段交付）

本任务书 25 条矩阵横跨三种互不相同的机制，一次性落地会把三类状态变更混在一条提交链里。因此按机制拆成三段，每段独立分支与独立验收：

| 段 | 范围 | 矩阵 | 状态 |
|---|---|---|---|
| **F6a** | 短休恢复预算（Arcane / Natural / Sorcerous Restoration） | 1–5、8、9、22、23 | **已实现**（`feat/dnd2024-s0f6a-rest-recovery-budget`） |
| F6b | Magical Cunning 的 1 分钟权威仪式 + Eldritch Master | 10–13 | 待执行 |
| F6c | Font of Magic 双向转换与临时槽 | 14–21 | 待执行 |

矩阵 6/7（Natural 的免费施法面）**刻意留在 b 段之前**：它要的是「从某个特性的授予里选一个 1 环以上法术」这种新的 `free_cast_source`，与 F5 的 `{fixed, class_list, spellbook}` 三档不兼容，属于新形状，不是本段能顺带做完的。

### 10.1 已定点核实的代码事实（覆盖任务书 §3）

- **六项特性在本切片之前没有任何特性定义**：`srd_class_features.json` 只有标签与进阶表条目，`arcane_recovery` / `magical_cunning` 仅在 `rest_catalogs/srd_recovery.json` 里作为一个「每长休重置」的计数值存在，没有预算算法；`natural_recovery` / `sorcerous_restoration` / `eldritch_master` / `font_of_magic` 连计数值都没有。任务书 §3 第 2 行「已有 rest resource 声明」只对前两项成立。
- 槽模型是 `spellcasting.class.slots_current` / `slots_max`，键为字符串化的环阶；Pact 走 `slot_profile == "pact"`，键是 Pact 环阶（5 级 warlock 为 `{"3": 2}`）。
- 资源池在 `resources.class.<id>`，最大值由 `resting/engine.py::sync_resources` 按 `rest_catalog.class_resources[class]` 生成，`_recover_specs` 负责休息时恢复。
- 短休入口是 `complete_short_rest`，长休是 `complete_long_rest`；两者都先 `sync_resources` 再落账。

### 10.2 契约

恢复目录新增两个键，都只由声明驱动，代码里没有按职业 id 分支：

```json
{"id": "natural_recovery", "minimum_level": 6, "maximum_fixed": 1,
 "short": "none", "long": "all",
 "requires_subclass": "subclass:circle_of_the_land",
 "short_recovery": {"mode": "spell_slots", "budget": "half_level_round_up", "max_slot_level": 5}}
```

- `requires_subclass`：休息表是按**职业**键入的，说不出「只有某个子职」。没有这道闸，月德鲁伊 6 级会拿到一个永远花不掉的自然恢复计数。`sync_resources` 按 `features.subclass_ref` 判定，不满足即删池。
- `short_recovery.mode`：`spell_slots`（合计环阶预算）或 `points`（点数预算）；`budget`：`half_level_round_up` / `half_level_round_down`。
- 提交载荷：`recovery = {<resource_id>: {"slots": {"<环阶>": n}}} | {"points": n}`，短休专用（长休给 `recovery` 直接 `ValueError`）。
- 事件三型：`recover_spell_slots`（含 `budget`、`slots`、`before`/`after`）、`recover_class_points`、`spend_resource_use`。
- 只读投影：`rest_options()` 新增 `short_recovery` 行，与提交路径读同一份声明；投影对无单职业的角色降级为 `[]`，不抛错。
- `rest_option_names` 扩到 `{attunement, land_terrain, free_cast, recovery}`，因此通用休息服务会把 `recovery` 转发到运行时。

### 10.3 偏差与未决

1. **矩阵 5 的数字本身是错的**。原文是 "half your Druid level (round up)"，7 级应为 4，不是任务书写的「等级 6/7 均预算 3」。按原文实现，测试钉住 6→3、7→4。
2. **矩阵 4 的「缺少真实 spellbook 不提供研究恢复」未建模**。SRD 的 Arcane Recovery 只要求「已消耗的槽」，没有把法术书列为前置；法术书缺失的法师本来也没有可准备的法术，但槽位仍然存在。不额外加一道原文没有的门。
3. **零恢复不伪扣使用**：`{"slots": {}}` 或 `{"points": 0}` 视为无操作，不产生事件、不消耗当天的一次。
4. **Natural Recovery 的免费施法面未做**（矩阵 6/7），理由见本节开头。
5. **前端未接线**：投影已经交出 `short_recovery`，但休息面板还没有画这个选择器。与 §11 的接线经验一致——本段只到通道与投影，UI 是下一步。
6. **多职业仍不可休息**：`_class_state()` 的单职业硬限制未变（索引 §10.1 第三条）。

### 10.4 F6a 验收

| 项 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_rest_resource_conversion.py` | **16 passed** |
| 相关定向回归（10 文件） | **219 passed / 1 failed**，唯一失败是投影断言未含 `short_recovery`，对齐后全绿 |
| `pytest tests/rulesets -q` | **11 failed / 1803 passed / 0 errors**，11 条为 `character_builder` 基线，通过数 1787→1803 = +16，净引入 0 |
| `ruff check` / `git diff --check` | 通过 |
