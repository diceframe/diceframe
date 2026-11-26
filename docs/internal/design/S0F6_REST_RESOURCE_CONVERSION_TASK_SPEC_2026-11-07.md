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
| 状态 | **全部交付**：a/b/c（2026-11-10，§10–§12）、F6d（2026-11-26，§16）、F6e（2026-11-26，§17）；兼职休息不在本片（见 §16.3） |

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
| **F6b** | Magical Cunning 的 1 分钟权威仪式 + Eldritch Master | 10–13 | **已实现**（`feat/dnd2024-s0f6b-magical-cunning-rite`） |
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

## 11. F6b 执行记录（2026-11-10）

**分支**：`feat/dnd2024-s0f6b-magical-cunning-rite`，stack 在 `feat/dnd2024-s0f6a-rest-recovery-budget@2d06ce89` 之上。

### 11.1 「同事务推进」是怎么定的

任务书 §4.2 要求 Magical Cunning「启动一个持续 1 个逻辑分钟的权威仪式，完成时才落账」，同时 §7 的非目标里写着「不实现通用仪式中断规则」。两条合起来留下一个岔口：

- **(a) 同事务**：一次权威写里推进 1 分钟 + 恢复 + 扣次数，失败整笔回滚。代价是「启动」不可观测。
- **(b) 两段式**：先 `start` 记 `started_tick`，等后续时钟推进再收尾。代价是悬空状态：AI GM 席位局没有人在线点收尾就会永久挂着。

**取 (a)**。理由：SRD 原文只说「1 分钟结束时恢复」，没有写任何中断、专注或「被别人打断则失败」的规则；自造一个挂起态等于自造规则，而 (a) 在可观测层面等价——事务提交那一刻，1 分钟确实已经流逝。

### 11.2 契约

内容侧（`rest_catalogs/srd_recovery.json`）：

```json
{"id": "magical_cunning", "minimum_level": 2, "maximum_fixed": 1,
 "short": "none", "long": "all",
 "ritual": {"minutes": 1, "mode": "pact_slots",
            "budget": "half_max_round_up", "full_feature": "eldritch_master"}}
```

- `ritual` 是**独立于 `short_recovery` 的一档**：短休是「休息结束时顺带恢复」，仪式是「先花掉时间，再恢复」。两者共用同一个资源池与「每长休一次」的计数语义。
- `budget = half_max_round_up` 是**槽个数**的一半向上取整（`ceil(max/2)`），不是环阶预算——这一条与 Arcane/Natural 的 `half_level_round_up` 形状相近但含义不同：前者按最大槽数，后者按职业等级合计环阶。名字刻意取得不同，避免复用错。
- `full_feature`：Eldritch Master **不新增一次使用**，而是把同一次 Magical Cunning 的上限换成「全部已消耗」。因此升级声明挂在**仪式**上，按特性 id 判定（`resolver.has_feature`），不按职业等级分支——这是本切片第三次用「声明驱动而非 class id 分支」。
- 特性侧（`srd_class_features.json`）新增 `magical_cunning`（Warlock 2，`resource_id` 指向同池）与 `eldritch_master`（Warlock 20）。locale 标签早已存在，未改语言文件。

引擎侧（`resting/engine.py`）：

- `ritual_options(character)`：只读投影，给出 `resource_id` / `minutes` / `uses_left` / `slot_level` / `maximum` / `expended` / `regain` / `full_budget`。无单职业、非 Pact 施法者、或槽表不止一行时降级为 `[]`，不抛错。
- `complete_ritual(payload, resource_id)`：先 `_plan_ritual` 把「是不是你的仪式、次数、Pact 槽模型、能恢复几个」全部算清楚，再动状态；事件两型 `recover_pact_slots`（含 `minutes`、`full_budget`、`slots`、`before`/`after`）与 `spend_resource_use`。
- `_pact_slots()` 只认 `slot_profile == "pact"` 且槽表**恰好一行**的卡；多行说明这张卡不是 Pact Magic 的形状，宁可拒绝也不猜。

服务侧（`src/webui/services/ruleset_ritual.py`，新增）：

- `POST /api/games/{game_key}/character/{user_id}/ritual`：`authoritative_write` → 绑定校验 → 战斗闸 → 幂等（`ruleset_operation_log` 里 `kind == "ritual"`）→ 版本校验 → 引擎 → `set_character_sheet` → `advance_game_time(minutes)` → `save`。任一环节失败，角色与 `world_state` 一起回滚。
- `GET .../ritual/options`：只读，不建会话、不碰卡。
- 需要 `confirm_elapsed_time: true`：时间会推进这件事不能静默发生。
- **时钟推进抽成共享实现** `src/webui/services/world_time.py::advance_game_time()`：推进 → Adventure gate 重估 → World Memory 排队 → `source_round`，四件套一处实现。休息的 `_advance_rest_time` 改为调用它，行为不变，只是第二个消费者出现时不会再各写一份。

### 11.3 偏差与未决

1. **「开始时」不可观测**：同事务的直接后果。矩阵 10 因此改写为「事务提交时 1 分钟确实已流逝」，并以「时钟被拒则整笔回滚」钉住原子性。
2. **没有任何已消耗槽的仪式直接拒绝**，而不是「无损空转」。§4.1.5 的「零恢复不伪扣使用」在休息里是静默跳过（一觉照睡），但仪式的**全部意义**就是换回槽；空转会静默烧掉每日一次，所以这里选择报错。
3. **未做通用仪式窗口**：矩阵 10 的另一种读法是「DM 确认无人有事再推进」，那需要挂起态 + 收口补差额，属于独立切片（见索引 §14 的 L2）。本段只交付 L1。
4. **未给 AI GM 工具**：同事务路径没有等待态，不存在 §12 那种「AI 席位局永久等待」的死锁面，因此不需要 `decide_*` 工具。
5. **前端未接线**：投影与两个端点已就绪，角色面板还没有画入口。与 F6a 同一处理。
6. 多职业仍不可举行仪式：`_class_state()` 的单职业硬限制未变。

### 11.4 F6b 验收

| 项 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_rest_resource_conversion.py` | **23 passed**（F6a 16 → +7） |
| `pytest tests/rulesets/test_dnd2024_ritual_authority.py`（新增） | **6 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1816 passed / 0 errors**（24:38），11 条仍为 `character_builder` 基线，通过数 1803→1816 = +13（引擎 +7、权威路径 +6），净引入 0 |
| `ruff check`（改动文件） | All checks passed |
| `git diff --check` | 干净 |

## 12. F6c 执行记录（2026-11-10）

**分支**：`feat/dnd2024-s0f6c-font-of-magic`，stack 在 `feat/dnd2024-s0f6b-magical-cunning-rite@cf6abb2f` 之上。

### 12.1 契约

内容侧（`srd_class_features.json`，术士 2 级新增 `font_of_magic`）：

```json
"parameters": {"created_slot_costs": [[1,2,2],[2,3,3],[3,5,5],[4,6,7],[5,7,9]]},
"capabilities": [
  {"id": "font_slot_to_points", "cost": {}, "actions": [{"kind": "font_slot_to_points"}]},
  {"id": "font_create_slot", "cost": {"bonus_action": 1}, "actions": [{"kind": "font_create_slot"}]}
]
```

- 两个方向是**两条能力声明**，不是一个 kind 加一个 intent 字段：`cost` 决定要不要附赠动作，"不用动作"与"要附赠动作"本来就该由声明回答，而不是由结算代码分支判。
- 五行表进内容（`created_slot_costs` = 环阶/点数/最低职业等级），代码只有校验器：`parse_created_slot_costs()` 对重复行、非整数、超 5 环一律整体拒绝——半张表被静默丢弃比没有表更糟。
- 等级下限按**授予该特性的职业等级**（`declaring_class_level()` 按 `build.class_levels` 里 `class:<feature.class_id>` 那一行取），不是角色总等级；兼职时两者不是一回事。为此 `resolver.feature_definition(id)` 新增：`features_by_key` 是 `(owner, id)` 复合键，只知道 id 的调用方需要一个按 id 找定义的入口。

规则侧（新建 `src/rulesets/dnd2024/spells/font_of_magic.py`）：纯函数，`_plan` 只读、`_apply` 才动状态。

- `font_slots(character)`：临时槽按**环阶计数**存 `spellcasting.class.font_slots`（`{"3": 1}`），与普通槽表并列而不是并入。读时清洗（非 1–5 环、非正数一律丢弃），所以手改存档也造不出 6 环临时槽。
- `slot_to_points_plan` / `create_slot_plan`：前者拒绝"已消耗槽"和"会超过点数上限"，后者拒绝表外环阶、等级不足、点数不足。**都不做截断**：点满了换槽是整笔拒绝，静默截断等于用一个真实法术位换回少于其价值的东西。
- `apply_slot_to_points` / `apply_create_slot`：只发事件、只改传进来的 canonical 映射；事件里带 `source: font_of_magic`、环阶、点数余额。
- `clear_font_slots`：长休时删除（不是合并）临时槽，返回被删掉的计数。

战斗侧：

- 能力校验两条新分支（`_validate_class_capability`）：`font_slot_to_points` 走 `slot_to_points_plan`，`font_create_slot` 走 `create_slot_plan`。两者都在任何事件产生之前完成，因此"点数不够/等级不够/上限会溢出"都是结构化拒绝，不会留下半个转换。
- 结算（`_font_of_magic_events`）：重算同一份计划再应用——校验与结算读同一张权威卡，结算期再拒一次是伪造 intent 的最后一道门。
- **临时槽优先消耗**：`_spell_cost_events` 在 `slot_level > 0` 时先看该环阶有没有临时槽，有就发 `dnd2024.spell.font_slot_spent`，否则发原来的 `slot_spent`。一笔施法永远只发一个扣槽事件，所以"同一法术不会同时扣临时槽和普通槽"是结构性成立，不是靠测试挑出来的。选它的理由是两者都在长休重置：先花临时槽不损失任何可选性。
- reducer：`font_slot_spent` 扣到 0 就删键（不留 0，否则会被读成"还有一个空槽"），并置 `slot_spell_cast`；`font_slot_created` 校验 1–5 环后加一。伪造的批次在 reducer 层就死掉。

探索侧（`exploration/engine.py`）：投影、校验、结算、reducer 四处同步。附赠动作是战斗经济的事实，但"临时槽是真槽"不是——出战斗后它照样能付一次施法。

长休（`resting/engine.py`）：在恢复职业资源与普通槽**之前**调用 `clear_font_slots`，事件 `discard_font_slots` 带被删计数。顺序反了会让"长休后槽位总数"在一瞬间把临时槽算成普通槽。已清空时返回空字典，所以连续两次长休不产生事件（幂等）。

升级（`progression/advancement.py`）：无需改动——`next_magic` 是 `{**deepcopy(previous), ...}`，临时槽随旧块整体保留；点数上限由 `sync_resources` 按新等级重算。

### 12.2 偏差与未决

1. **白名单外新增一个源文件** `spells/font_of_magic.py`（§5 只允许新建测试）。理由：两个方向的算术要同时被休息、战斗、探索、升级四处调用，塞进任何一处都会让另外三处 import 一个语义不相干的模块。风险已由"纯函数 + 无状态"限制住。
2. **出战斗造不了槽**：`font_create_slot` 是战斗能力，探索态没有职业能力入口（`EXPLORATION_INTENT_TYPES` 只有三条）。这与 Bard 的 Font of Inspiration 现状一致；补一个探索态入口是独立切片，不塞进本段。
3. **前端未接线**：`available_intents` 与投影已就绪，角色面板还没有画"换点/造槽"的入口。与 F6a、F6b 同一处理。
4. **多职业仍受限**：等级下限按授予特性的职业算，但 `_class_state()` 的单职业硬限制没动，休息面仍不接受兼职卡。
5. 矩阵 22（重复 intent / 版本冲突无部分写入）由既有的 `apply_event_batch` 原子性覆盖，未新增专门用例；矩阵 23 的"升级后按新值重算"同样由 `sync_resources` 既有用例覆盖。

### 12.3 F6c 验收

| 项 | 结果 |
|---|---|
| `test_dnd2024_rest_resource_conversion.py` | **32 passed**（23 → +9） |
| 新增 `test_dnd2024_font_of_magic.py` | **13 passed** |
| 相邻 8 套件定向回归 | **144 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1838 passed**，11 条仍为 `character_builder` 基线，1816→1838 = +22，净引入 0 |
| `ruff check`（改动文件） | All checks passed |
| `git diff --check` | 干净 |

## 13. 复核（2026-11-25）：a/b/c 在，矩阵 6/7 与前端仍缺

在 `f2d86b1a` 上实测：

| 套件 | 结果 |
|---|---|
| `test_dnd2024_rest_resource_conversion.py` + `test_dnd2024_ritual_authority.py` + `test_dnd2024_font_of_magic.py` | **51 passed** |

所以三段都是真的在主线（此前一度按「`src/` 里 grep 不到 `arcane_recovery`」误判为未实现——**这份实现是声明驱动的，id 只在内容表里**，判断有无实现必须看内容声明 + 套件，不能只看代码字符串）。

剩下的三块，本片补前两块：

| 块 | 内容 | 卡点 |
|---|---|---|
| **F6d** | 矩阵 6/7：Natural Recovery 的免费施法面 | `natural_recovery` 的声明里根本没有这一面，summary 也省了这句 |
| **F6e** | 三个入口的 UI | 投影与端点齐全，`frontend-v2` 零命中 |
| 不在本片 | 兼职卡不能休息/仪式（`_class_state()` 硬要求单职业） | 休息引擎的通用缺口，另片 |

## 14. F6d 契约：Natural Recovery 的免费施法面

### 14.1 为什么不是 `free_cast_slots`

`free_cast_slots` 是「预先选一个法术、占一个槽、施法时匹配 `spell_ref`」的形状；Natural 是「施法那一刻从当前地形的授予里任选一个」。硬套前者只能退化成「准备时先选死一个」，与原文不符，且要动 selection 与 reducer 三处。

**已有更好的落点**：`spells/casting.py::free_cast_resource()` 的第三条分支——**当某条 grant 行带 `resource_id` 时，无槽施法直接扣那个职业资源**。Natural 的语义正好是「某组授予里的任一法术，无槽一次，每长休一次」，所以它是一个**资源 + 授予标记**，不是一个选择槽。

### 14.2 改动

1. `rest_catalogs/srd_recovery.json`：druid 段新增一个资源，**不复用 `natural_recovery`**（后者已承载短休槽恢复那一次；§4.1.3 要求两个计数互不影响）：

   ```json
   {"id": "natural_recovery_cast", "minimum_level": 6, "maximum_fixed": 1,
    "short": "none", "long": "all",
    "requires_subclass": "subclass:circle_of_the_land",
    "source_ref": "srd-5.2.1:p46:natural-recovery"}
   ```

2. `srd_class_features.json`：`circle_of_the_land_spells` 的 `parameters` 新增 `free_cast_resource_id: "natural_recovery_cast"`；同时把 `natural_recovery` 的 `summary` 补回被省掉的免费施法那句（summary 是玩家可读事实，漏一句就是错一句）。

3. `features/resolver.py::actor_projection()`：声明了 `free_cast_resource_id` 的特性，把它**合并进自己产出的每一条授予行**（同一个 `row` 加一个键，不另起一行），且**只在池真的可用时**才标（等级够、子职对、存档里已经有这个池）。

   不写成"另加一条只有 `{spell_ref, resource_id}` 的派生行"：那会让同一法术在 `spell_grants` 里出现两次，而新增的那行丢掉了 `owner` / `feature_id` / `granted_level` / `terrain` 全部 provenance，与 `_spell_grant_rows()` 自己声明的"一行一个来源"相撞。

校验与扣费路径**一行不动**：`free_cast_resource` 先按 `spell_ref` 过滤授予、再找 `resource_id` 且 `current > 0` 的资源；`spell_cost_events(slot_level=0)` 发 `dnd2024.class_resource.spent`；`apply_free_cast_cost` 扣 `resources.class.natural_recovery_cast.current`。

### 14.3 行为

1. 只对**当前地形、当前等级**授予的 Circle Spell 生效——地形切换后旧列表立即失效（grant 行按当前地形算，不需要额外的失效逻辑）。
2. 每长休一次；短休不恢复（`short: "none"`）。
3. 与短休槽恢复**互不影响**：花掉免费面仍可提交 `recovery`，反之亦然。
4. 环阶不设上限：原文是「1 环以上」，实现里池分支不看环阶（只有 `>= 1` 才有意义，0 环被 `free_cast_resource` 直接拒绝）。注意 Land 表的写法是 `<职业等级>:spell:<id>`——`arid:9:spell:wall_of_stone` 是「德鲁伊 9 级时授予 `wall_of_stone`」，而 `wall_of_stone` 本身是 **5 环**；Land 表里没有 6 环以上的法术，所以「高环也能免费」目前只有代码保证、没有内容可验证。这与 Arcane/Natural 槽恢复的「6 环以上禁止」是**两回事**，不要串。
5. 拒绝且不留痕：无余额、非法术、非当前地形、非本子职。

### 14.4 编号测试（接 §6 的 6/7）

| # | 断言 |
|---|---|
| 6a | 月德鲁伊（非 land）没有 `natural_recovery_cast` 资源池 |
| 6b | land 德鲁伊 6 级：任意当前地形法术无槽施放成功，资源 1→0，不产生 `slot_spent` |
| 6c | 同一长休内第二次无槽施放被拒（资源为 0），且不发任何事件 |
| 6d | 非 Circle 授予的法术无槽施放被拒（即便有余额） |
| 6e | 长休恢复资源；短休不恢复 |
| 6f | 花掉免费面后仍可提交 `recovery`（短休槽预算），反序亦然 |
| 7 | 切换地形后，旧地形的法术不再能免费（新地形法术可以） |

## 15. F6e 契约：入口的 UI（已交付，见 §17）

投影与端点都已就绪，缺的只是画。两处各自读什么：

| 入口 | 读 | 写 |
|---|---|---|
| 短休恢复选择器 | `GET .../rest/options` → `rest_options.short_recovery`（`budget` / `max_slot_level` / 已消耗槽 / `uses_left`） | `complete_short_rest` 的 `recovery = {<resource_id>: {"slots": {"<环阶>": n}}}`（点数池是 `{"points": n}`） |
| Magical Cunning 仪式 | `GET .../ritual/options` → `resource_id` / `minutes` / `regain` / `full_budget` / `uses_left` | `POST .../ritual`，必须带 `confirm_elapsed_time: true` 与 `expected_revision`（时间会推进） |

**第三处（Font 换点/造槽）不需要 frontend 工作**：复核后确认它的两条 capability 早就是
`class_capability`，战斗面板对这类条目做通用渲染（`Dnd2024CombatPanel.vue` 的
`capabilityActions` + 既有用例）。真正的缺口是"出战斗后没有入口"——那是探索态意图的
缺口（§12.2 第 2 条），属于引擎切片，不是 UI。

约束：两处都只渲染服务端给的数量与费用（`created_slot_costs` 五行表来自内容，不在前端写死）；非法组合不要让用户点得动，但最终拒绝仍在服务端。

## 16. F6d 执行记录（2026-11-26）

**分支**：`feat/dnd2024-s0f6d-free-cast-face`，基座 `f2d86b1a`（远端 fetch 被连接重置，未能确认上游是否已前进）。

### 16.1 落地

| 文件 | 改动 |
|---|---|
| `content/rest_catalogs/srd_recovery.json` | druid 新增 `natural_recovery_cast`（6 级、`maximum_fixed: 1`、`short: none`、`long: all`、`requires_subclass`） |
| `content/class_feature_catalogs/srd_class_features.json` | `circle_of_the_land_spells.parameters.free_cast_resource_id`；`natural_recovery` 的 summary 补回免费施法那句 |
| `content/locales/{en,zh-CN}/class_feature_catalogs/srd_class_features.json` | `natural_recovery_cast` 的显示名 |
| `features/resolver.py` | `actor_projection()` 把 `resource_id` 合并进原授予行（仅当池可用）；`class_resources` 过滤 0/0 行；`class_resource_definition()` 补子职门禁 |
| `features/resources.py` | `ClassResourceDeclaration` 新增 `requires_subclass`（此前解析时被丢弃，导致投影无法执行休息表已有的门禁） |
| `spells/casting.py` | `free_cast_resource()`：0 环直接拒绝；多池改为取第一个有余额的池 |
| `combat/reaction_spells.py` | 调用 `free_cast_resource` 时补传法术环阶 |

### 16.2 与 §14 的偏差

1. **`resource_id` 合并在原行上，不是新增一条派生行**（§14.2 第 3 条已按此改）：新增行会让同一法术在 `spell_grants` 里出现两次，且丢掉 provenance。
2. **补了子职门禁**：`sync_resources` 早就按 `requires_subclass` 删池，但 `ClassResourceDeclaration` 解析时把这个字段丢了，投影于是一直给月德鲁伊报一个 store 里不存在的自然恢复（1/1）。这是本片之外就存在的投影/存储不一致，被 F6d 的池暴露出来，一并修。
3. **补了两处 helper 边界**：`free_cast_resource` 不认 0 环（戏法不花槽）、多池取第一个有余额的（原来只取第一条带池的授予，第一池空了第二池就永远用不上）。
4. **内容引用没有加载期校验**：`free_cast_resource_id` 写错仍是静默失效（测试里有内容一致性守卫，见 §16.4 第 11 条）。

### 16.3 已知缺口

- **GAP-46**：`normalize_character_submission()` 会把职业资源重建为满，等于给"每长休一次"开后门（所有职业资源共有，不是本片引入）。
- ~~**前端未接线**（F6e，§15）~~：已由 §17 接线。投影当初就带了 `resource_id` 与叫法，UI 缺的只是画。
- **GAP-47**：兼职角色不能休息、不能举行仪式（`_class_state()` 要求 `class_levels` 恰好一行）。这是休息引擎的通用限制，F6 只是第三次撞上，单独立项。
- **高环未验证**：Land 表最高 5 环，"6 环以上也能免费"只有代码保证（见 §14.3 第 4 条）。

### 16.4 验收

| 项 | 结果 |
|---|---|
| `test_dnd2024_rest_resource_conversion.py` | **43 passed**（32 → +11；第 11 条是内容漂移守卫，审核后补） |
| 变异验证 | 去掉内容声明 → 6b / 等级门禁 / 地形切换 三条立刻变红；去掉子职门禁 → 6a 变红 |
| `tests/rulesets` 全量 | 见 §16.5（本次同时改了 `casting.py` 与 `class_resource_definition`，必须全量） |
| 新增用例 | 6a（moon 的 store 与投影双断言）、等级门禁（5 vs 6）、6b（单行 + 事件）、6b（带槽施法不花免费面）、戏法不免费、6c/6d（正向前置 + 花掉后不可用 + 非授予法术）、6e（短休不恢复 / 长休恢复，均带正反向断言）、6f 正序、6f 反序、7（地形切换）、跨内容表的漂移守卫 |

### 16.5 全量与整仓

| 项 | 结果 |
|---|---|
| `tests/rulesets` 全量（`-n 6 --dist loadfile`） | **2944 passed / 0 failed**（12m32s） |
| 整仓全量 | **11015 passed / 10 failed / 3 errors / 12 skipped**；失败与基线同名同数：`test_updater`（MAX_PATH）1、`test_docker_launcher` 6、`docker_data_generation` 1、`docker_self_update_reentry` 1、`runtime_dependencies` 1，`docker_runtime_v1_upgrade` 3 errors |
| `ruff check` / `mypy` | 干净 |

这一段同时改了 `casting.py`、`class_resource_definition()`（所有职业资源投影都走它）与 `reaction_spells.py`，所以按规矩跑了全量而不是只跑定向套件。

---

## 17. F6e 执行记录（2026-11-26）

同一个工作树、同一个分支（`feat/dnd2024-s0f6d-free-cast-face`）。与 F6d 分开提交，是因为前者动引擎、本段只动前端，回滚边界不同；本段自身是两个提交——画界面（`96123fe9`）与逐级上限（`baf99eae`）。

### 17.1 落地

| 文件 | 改动 |
|---|---|
| `frontend-v2/src/api/types.ts` | `RestShortRecovery` / `RestRecoverySlotRow` / `RitualOption` / `RulesetRitualOptionsResponse` / `RulesetRitualResponse`；`RestOptions.short_recovery` 可选 |
| `frontend-v2/src/api/rulesets.ts` | `RestSubmissionOptions.recovery`（有才发）；`fetchLiveCharacterRitualOptions` / `performLiveCharacterRitual`（后者固定带 `confirm_elapsed_time: true`，因为服务端在同一次写入里推进时钟） |
| `ProfessionalCharacterCenter.vue` | 短休恢复小节：按资源池画每个环阶的"已用/预算"、超预算挡住提交、只发选中的格子；仪式小节：读 `/ritual/options`、确认门禁、成功后 `emit('saved')` 并重读剩余次数；打开「法术与资源」页读一次休息选项 |
| `tests/ProfessionalCharacterCenter.test.ts` | 13 → 18 条；另有三条既有用例（连同 `beforeEach` 的默认桩）绑在"打开面板零请求"这个已过期假设上，一并改写 |

### 17.2 与 §15 的偏差

1. **第三处（Font 换点/造槽）不需要前端**：复核后确认两条 capability 早已是 `class_capability`，战斗面板对这类条目做通用渲染。缺的是"出战斗后没有入口"，那是探索态意图的缺口（§12.2 第 2 条），属引擎切片。
2. **§15 的前提句已过期，不是"加一个开关"**：原 watcher 上写着"短休没有任何声明，打开面板也不该产生请求"。这句话从 F6a 合入起就不成立（短休一直有 `short_recovery` 声明），只是没有消费者。这次把 watcher 改成"打开这一页读一次"，并把断言"零请求"的用例改成"只读选项、绝不提交"——后者才是真正要守的性质。
3. **`recovery` 只在短休发**：长休由服务端按自己的规则回满，带过去会被硬拒。空对象也不发（服务端会拒成 `would not change anything`）。
4. **仪式与休息的选项分开拉**：仪式读失败不该拦住休息，反之亦然。两者的失败各自显示在自己的小节里。
5. **补了逐级上限**（提交 `baf99eae`）：第一版只挡总预算，1 环消耗 2 个、预算 3 环时填 3 个仍能提交，然后被服务端拒成 `cannot recover more level 1 slots than are expended`。服务端 fail-closed 是对的，但 §1 第 2 条要求"客户端只提交合法分配选择"，所以前端也逐级算 `买回 − 已消耗`，为正就挡住并在那一行报错。

### 17.3 已知缺口

- **恢复粒度是"每环阶买回几个"，不是"具体哪几个槽"**：引擎的 `recovery` 契约就是 `{"slots": {"3": 1}}`，所以 UI 也只能画到这个粒度。要支持挑具体槽位得先改引擎。
- **点数池模式的"买回几点"上限取 `min(spent, budget)`**：服务端下发的是"已消耗点数"（`spent`）与"预算"（`budget`），没有下发"本次最多能买回多少"这个单值。目前唯一的点数池是 Sorcerous Restoration（`budget = half_level_round_down`，`track = sorcery_points`），预算不可能大于已消耗，所以取小者就是"能回多少"；若以后出现"至少回 1 点"这类下限规则，需要服务端补字段而不是前端自己推。
- **没有 e2e**：只有组件用例。角色中心的休息页不在 CI 的 e2e smoke 里，所以"面板对着活服务端跑通"没有被自动化覆盖。
- **兼职仍不可用**：仪式与休息都撞 `_class_state()` 的单职业硬限制（GAP-47）。

### 17.4 验收

| 项 | 结果 |
|---|---|
| `test_dnd2024_rest_resource_conversion.py`（后端回归，本段未改后端） | **43 passed**（32 → +11，改动全在 §16） |
| `npx vue-tsc -b` | 通过 |
| `npx eslint src tests --max-warnings 0` | 通过（CI 同命令） |
| `npm run locales:check` | 通过（ja/de/ru 的既有缺失不变，本次没有新增 i18n 键——面板用内联双语 `text()`） |
| `npx vitest run` 全量 | **1744 passed / 1746**；两条失败都是计时断言（`mapLayout` 的 300ms 性能上限、`useTheme` 的初始化耗时），单独跑 10 passed |
| `npx vite build` | 通过（CI 同步骤；产物落在被忽略的 `static-v2/assets/`） |
| 组件用例 | **18 passed**（13 → +5：短休预算渲染与提交体、超预算拦截、逐级上限拦截、仪式投影渲染、仪式确认门禁与提交体） |

计时类用例的说明要留在这里：`mapLayout` 与 `useTheme` 的断言读的是墙钟时间，在 `vitest run` 并行时会双双越线（这两轮分别是 `useTheme` 17.1s / 22.0s、`mapLayout` 约 340ms 撞 300ms 上限），单独跑则 10 passed。这不是本次改动引入的，但说明"本机 vitest 全绿"不是一个可靠的验收口径——要判回归必须单独重跑这两个文件。
