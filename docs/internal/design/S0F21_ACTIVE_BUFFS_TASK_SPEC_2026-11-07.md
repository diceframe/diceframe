# S0-F21 任务书：休息限制的主动自我增益

| 项 | 值 |
|---|---|
| 切片编号 | S0-F21（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `tireless`、`natures_veil`、`wholeness_of_body`、`superior_defense` |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Ranger p59，Monk/Open Hand p52；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | D2 owner-scoped 长休使用记账；F12 动态抗性与豁免合成；S0-E3 turn-start 事件顺序 |
| 建议分支 | `feat/dnd2024-s0f21-active-buffs` |
| 状态 | 已由 #784 实现并合并（本文件保留原始契约，状态见 `SLICE_STATUS.md`） |

## 1. 执行纪律与范围锁定

1. 从 D2/F12/S0-E3 合入后的实际提交开工；D2 只提供长休使用记账，不提供持续时间。若动作成本、Wis 次数池、临时 HP、权威时钟或 turn-start 选择任一缺失，停止。
2. 四项特性必须具有真实动作经济、资源扣减、数值结算、到期与存档行为，不能只增加 summary。
3. Tireless 的短休减 Exhaustion 与主动临时 HP 同属本切片；Superior Defense 的触发伤害抗性与失能结束不得延后。
4. 不实现 Monk/Ranger 其他特性、Survivor、通用法术隐形或所有职业 buff；不借本切片重构整个条件系统。
5. 仅使用官方 PDF 和本文锁定的接口，不采用第三方关于持续时间或资源恢复的解释。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p59，Ranger：

> **Level 10: Tireless.** **Temporary Hit Points.** As a Magic action, you can give yourself a number of Temporary Hit Points equal to 1d8 plus your Wisdom modifier (minimum of 1). You can use this action a number of times equal to your Wisdom modifier (minimum of once), and you regain all expended uses when you finish a Long Rest.
>
> **Decrease Exhaustion.** Whenever you finish a Short Rest, your Exhaustion level, if any, decreases by 1.
>
> **Level 14: Nature’s Veil.** As a Bonus Action, you can give yourself the Invisible condition until the end of your next turn. You can use this feature a number of times equal to your Wisdom modifier (minimum of once), and you regain all expended uses when you finish a Long Rest.

SRD 5.2.1 p52，Monk 与 Warrior of the Open Hand：

> **Level 18: Superior Defense.** At the start of your turn, you can expend 3 Focus Points to bolster yourself against harm for 1 minute or until you have the Incapacitated condition. During that time, you have Resistance to all damage except Force damage.
>
> **Level 6: Wholeness of Body.** As a Bonus Action, you can roll your Martial Arts die. You regain a number of Hit Points equal to the number rolled plus your Wisdom modifier (minimum of 1 Hit Point regained). You can use this feature a number of times equal to your Wisdom modifier (minimum of once), and you regain all expended uses when you finish a Long Rest.

SRD 5.2.1 p187，Resistance：同一伤害实例仅应用一次抗性，减半后向下取整。p184，Invisible：攻击者与攻击目标的攻击骰效果只在对方不能看见你时成立。

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 四个 id 已在职业/子职表列出，canonical 定义和 Wis 次数池尚不存在 | `content/progression_catalogs/srd_classes.json`、`content/subclass_catalogs/srd_subclasses.json`、class feature catalog |
| `Focus Points` 使用等级轨道，短休和长休全部恢复 | `content/rest_catalogs/srd_recovery.json`；`resting/engine.py::_recover_class_resources` |
| class capability 可声明 action/bonus action 与职业资源成本并原子结算 | `features/models.py::_parse_capability`；`combat/resolution.py::_class_capability_events` |
| 基线 capability action 仅支持有限 kind，未知 kind 必须 fail closed | `features/models.py::ACTION_KINDS`、`_parse_capability` |
| 临时 HP 使用 `resource.changed(resource="temp_hp")`，reducer 保留新旧较高值，不修改普通 HP | `combat/reducer.py::_apply_temp_hp_change` |
| 治疗支持骰、`cannot_regain_hp` 和 max HP clamp | `combat/resolution.py::_class_capability_events`；`combat/reducer.py::_apply_hp_change` |
| Invisible 已有攻击模式声明，条件可携带来源和期限元数据 | `content/combat_catalogs/srd_combat_core.json`；`combat/check_modes.py::attack_sources` |
| 抗性读取条件注册表及条件元数据；需要扩展为“除 Force 外全部”而非枚举漏型 | `combat/resolution.py::_resist_damage` |
| 短休已有资源恢复和事件列表，长休基线也减 1 Exhaustion | `resting/engine.py::complete_short_rest`、`complete_long_rest` |
| turn.advanced 后已有 turn-start 接入点，回合经济由 `_fresh_economy()` 重建 | `combat/resolution.py::_end_turn_events`；`combat/engine.py::_fresh_economy` |

## 4. 精确行为与数据契约

### 4.1 声明、资源与能力入口

1. 四项特性按 Ranger 10/14、Open Hand 6、Monk 18 的真实 owner 和等级授予；source_ref 分别指向 p59/p52，中英文标签同步。
2. `tireless`、`natures_veil`、`wholeness_of_body` 各有独立资源池，maximum 为 `max(1, Wisdom modifier)`，short 为 none、long 为 all；不能共享次数，也不能用 Focus 或法术位替代。
3. 三个主动能力的 capability id 与 feature id 相同；Tireless 成本 `action=1` 且语义为 Magic action，Nature's Veil/Wholeness 成本 `bonus_action=1`，每次各扣自身资源 1。
4. Superior Defense 不新建次数池，成本为 3 `focus_points`，不消耗动作、附赠动作或反应；仅在自己的 turn-start 窗口可用。
5. 资源不足、动作不足、非当前行动者、失能、非真实 owner 或客户端伪造能力时，在生成任何事件前拒绝；批次原子性保证不会只扣资源不产生效果。

### 4.2 Tireless

1. 临时 HP 公式为 `1d8 + 当前 Wisdom modifier`，最终最小 1；事件含 `resource="temp_hp"`、`amount`、`rolls=[d8]`、`wisdom_modifier`、`feature_id="tireless"`。
2. 只对自己生效，不是治疗，不恢复普通 HP、不清除 Unconscious/stable，也不触发 Disciple of Life 或 Supreme Healing。
3. reducer 沿用临时 HP 保留较高值；新结果较低时不倒扣旧值，但仍支付动作和次数。RNG 每次使用只消耗一个 d8。
4. 每次完成短休时，若真实拥有 Tireless 且 Exhaustion>0，额外减少 1 级并追加 `reduce_exhaustion` 事件，记录 before/after 和 feature 来源；0 级不变、不产生虚假事件。
5. 该短休效果与长休既有减 1 是不同休息的独立结算；同一次短休只减 1，不能按多个特性来源叠加，也不将短休资源恢复改为全部。

### 4.3 Nature's Veil 与 Wholeness of Body

1. Nature's Veil 只给自己添加 `invisible`，来源为 feature，期限严格为持有者下一回合结束；使用当回合不会立即到期。
2. 隐形期间沿用通用看见/看不见和攻击优劣势规则，不额外添加隐藏、不可被定位或攻击后必结束等文本外效果；再次使用只刷新同一条件，不叠层。
3. 回合到期、死亡或其他通用结束条件由 reducer 统一清理；D2 不负责持续时间。战斗外若无法表示“下一回合结束”，不得提供探索模式假按钮。
4. Wholeness 掷当前等级轨道的一个 Martial Arts die，治疗 `roll + Wisdom modifier`，最低 1；事件保留 die、roll、Wis 加值、feature/owner 来源。
5. 该治疗不是法术或 Channel Divinity，不受 Supreme Healing 最大化；`cannot_regain_hp` 时 delta/amount 为 0，但动作和次数已支付。普通 HP 由 reducer 截到 max HP。
6. Wis 为负时次数池仍为 1，治疗公式仍可为 1；短休不恢复次数，长休全部恢复，升级改变 Wis maximum 时沿用资源 resize policy，不静默补满。

### 4.4 Superior Defense 的 turn-start 与抗性

1. 每个自己的回合开始（含战斗首回合和 Thief 额外回合）生成一次可选 Superior Defense 窗口；只有 Monk 18 且 Focus≥3 时可支付。
2. 玩家可明确激活或跳过；任何攻击、施法、移动、其他 class capability 或 end_turn 首先关闭该回合开始窗口，之后不能 retroactively 激活。Survivor 等自动 turn-start 事件不被该选择吞掉。
3. 激活事件顺序为 3 Focus spent → condition applied；效果含 `duration_minutes=1`、10 个战斗轮的到期计数、`source_actor_id`、`feature_id`，不使用“直到战斗结束”替代。
4. 抗性匹配全部 canonical damage types，唯一例外 `force`；在 `_resist_damage` 中以排除式声明求值，避免新伤害类型意外漏掉。每次伤害仅减半一次并向下取整，多来源不继续除二。
5. 触发中的伤害不能使用本能力 retroactively 抵抗，因为激活只在自己的回合开始；激活后的攻击、法术、环境和其他来源伤害均适用，Force 永远不减。
6. 持有者在效果期间获得任何 `implies_incapacitated` 条件时立即结束 Superior Defense，并产生 condition removed 审计；恢复后不自动重启，须下一回合开始重新支付。
7. 一分钟到期、失能、死亡、存档/加载及重放必须产生同一终态。若权威逻辑时钟不能表示战斗外经过时间，则停止而不是让效果永久残留或在战斗结束错误清除。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json`
5. `src/rulesets/dnd2024/features/models.py`：仅新增封闭 capability kind/成本校验
6. `src/rulesets/dnd2024/features/resolver.py`
7. `src/rulesets/dnd2024/combat/resolution.py`
8. `src/rulesets/dnd2024/combat/engine.py`
9. `src/rulesets/dnd2024/combat/reducer.py`
10. `src/rulesets/dnd2024/combat/validation.py`
11. `src/rulesets/dnd2024/combat/view.py`
12. `src/rulesets/dnd2024/resting/engine.py`
13. `src/rulesets/dnd2024/runtime.py`：仅必要事件投影
14. 新建 `tests/rulesets/test_dnd2024_active_buffs.py`
15. 本任务书：只允许追加执行报告

不改 progression/subclass catalog、通用 rest service、前端或其他法术效果；需要时停止。

## 6. 编号测试矩阵

1. 四项特性按真实职业/子职和等级授予，低一级、其他子职和其他职业均无能力入口。
2. 三个 Wis 池在 Wis +3 时 maximum=3，在 Wis -2 时 maximum=1；彼此独立且长休回满、短休不回。
3. Tireless 只对自己支付 Magic action 和一次资源，掷一个 d8，临时 HP 为 roll+Wis 且最低 1。
4. Tireless 新临时 HP 较低时保留旧值但仍扣成本；普通 HP、Unconscious 和 stable 不被修改。
5. Wis 为负使公式不大于 0 时仍获得 1；每次只消耗一个 d8，动作或次数不足时零事件。
6. Tireless 短休使 Exhaustion 3→2，0 不变；同一短休只减 1，长休既有恢复不重复计算。
7. Nature's Veil 支付附赠动作和一次资源，Invisible 只作用于自己且来源/期限正确。
8. Invisible 在使用当回合有效，下一回合结束准时移除；攻击或被攻击不触发文本外的提前结束。
9. 看不见持有者的攻击者按通用规则劣势，能看见者不适用；多次使用不叠加成多骰。
10. Nature's Veil 短休不恢复、长休恢复，资源或附赠动作不足时不产生条件。
11. Wholeness 按 Monk 6/11/17 等级轨道使用 d8/d10/d12，治疗为 roll+Wis 且最低 1。
12. Wholeness 是附赠动作且只治疗自己；高等级 Open Hand 真实升级链可构造，非 Open Hand Monk 无此能力。
13. Wholeness 不受 Supreme Healing 影响；`cannot_regain_hp` 时 HP 不变但成本扣除，超过 max HP 部分被 clamp。
14. Superior Defense 在首回合、后续回合和额外 turn slot 均只出现一次选择；Focus<3 不可激活。
15. 激活支付恰好 3 Focus，不支付动作；跳过后或先执行其他回合动作后不能再激活。
16. 激活后 Bludgeoning、Fire、Radiant 等伤害均减半向下取整，Force 全额；多来源抗性不叠加。
17. 激活前已经待结算的伤害不被 retroactively 修改；激活后的不同攻击和环境来源均正确。
18. 获得 Incapacitated、Stunned、Paralyzed 或 Unconscious 时按通用失能标记立即结束，恢复后不自动重启。
19. 一分钟到期准确结束，不能用无限回合或 combat 状态代替；下一回合可再次支付 3 Focus 新激活。
20. 资源变化、条件到期和 HP 结果经 apply/replay 一致，重复 intent 不重复扣费或治疗。
21. 存档加载后 Wis 池、Invisible 剩余期限、Superior Defense 计数与 turn-start 窗口不重置。
22. 其他 actor 的回合开始不能激活持有者 Superior Defense，也不能触发其 Tireless/Wholeness。
23. 客户端伪造目标、Wis 次数、Martial Arts die、Force 例外或失能状态均被服务端拒绝。
24. Rage、Stoneskin、Protection from Energy 等既有抗性仍只应用一次，事件 resistance 来源正确。
25. Second Wind、Monk Focus、Survivor、Thief turn slot、隐形与条件到期回归测试通过。
26. 全量 rulesets、ruff 和 diff 检查通过，公共 feed 不泄漏未许可的角色字段。

## 7. 非目标与停止条件

### 7.1 非目标

- Ranger/Monk 其他特性、通用 Short Rest 重构、所有 buff 的统一改版。
- 探索模式无限期 Invisible、2D 光照/视野、前端动画或新伤害类型系统。
- 将 Superior Defense 简化为 Rage 的物理抗性，或忽略 Force 例外。

### 7.2 停止条件

1. D2/F12/S0-E3 未合入，或 turn-start 可选窗口不能在任何其他回合动作前关闭；不得要求 D2 提供其范围外的持续时间系统。
2. 临时 HP、Wis 次数池、装备/条件资格或资源 resize 语义与本文冲突。
3. 无法表示一分钟真实到期和战斗外时间，或失能族清理必须散落职业名分支。
4. `_resist_damage` 无法安全支持排除 Force 的全类型抗性而不回归既有条件。
5. 核心测试 3、8、11、14–18 需要伪造特性、手写预期事件或修改白名单外文件。
6. 基线或并行分支已有同责实现，存在资源双扣或条件双写。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_active_buffs.py -q
python -m pytest tests/rulesets/test_dnd2024_monk_resources.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_survivor.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/combat src/rulesets/dnd2024/resting/engine.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_active_buffs.py
git diff --check
git status --short
```

记录实际 Python 路径；若聚焦回归文件尚未由前置任务合入，停止而不是创建同名空测试。所有豁免必须有同基线对照。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、资源池与 capability 数据形状。
2. 单独展示短休减 Exhaustion、下一回合末 Invisible、Wholeness 公式、Superior Defense 时序和 Force 例外。
3. 报告 RNG、资源原子性、回合到期、重放/存档和公开 feed 结果。
4. 不得把战斗外永久效果或其他非目标写成已完成。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 特性与 Wis/Focus 资源：
- Tireless / Nature's Veil / Wholeness：
- Superior Defense 时序、抗性、到期：
- 改动文件对照白名单：
- 测试 1–26 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与权限证据：
- 与契约的偏差及原因：
- 未决事项（不得把非目标列为已实现）：

## 11. 执行报告（2026-11-16）

### 11.1 实际基线 / 分支 / 提交

- 基线：`afe718a3`（S0-F20 三提交之后的 `main` 工作树），分支 `feat/dnd2024-s0f21-active-buffs`。
- 提交：`feat` / `test` / `docs` 三段，见 §11.9。
- Python：`E:\Github\diceframe\.venv\Scripts\python.exe`；`ruff`/`mypy` 用 `C:\Python314\Scripts\`（venv 内无这两个工具，pyproject 已锁 `python_version=3.11`）。

### 11.2 特性与 Wis/Focus 资源

| 特性 | owner / 等级 | 资源池 | capability 成本 | 动作 kind |
|---|---|---|---|---|
| `tireless` | `class:ranger` 10 | `tireless`，`max(1, Wis)`, short none / long all | `action: 1` + 1 次 | `temp_hp` |
| `natures_veil` | `class:ranger` 14 | `natures_veil`，同上 | `bonus_action: 1` + 1 次 | `invisible` |
| `wholeness_of_body` | `subclass:warrior_of_the_open_hand` 6 | `wholeness_of_body`，同上 | `bonus_action: 1` + 1 次 | `heal` |
| `superior_defense` | `class:monk` 18 | 复用 `focus_points` | `turn_start: true` + 3 Focus | `resistance_all` |

三个 Wis 池在 `srd_recovery.json` 用 `maximum_ability_modifier: "wis"` + `minimum: 1`，`short: none`、`long: all`，彼此独立；升级走既有 resize policy，不静默补满（测试 2 覆盖）。

### 11.3 Tireless / Nature's Veil / Wholeness

- **Tireless**：`temp_hp_dice: "1d8"` + `temp_hp_dice_ability: "wis"` + `temp_hp_dice_minimum: 1`；结算发 `resource.changed(resource="temp_hp")`，携 `rolled`/`ability_modifier`/`rolls`/`feature_id`。reducer 的 keep-max 决定"较低结果仍支付成本"。短休在 `_recover_class_resources(..., "short")` **之前**调 `_apply_tireless_exhaustion`，只有真实拥有 Tireless 且 `exhaustion > 0` 才减 1 并追加 `reduce_exhaustion`（带 before/after/`feature_id`）。
- **Nature's Veil**：条件带 `duration: caster_next_turn_end` + `turns_remaining: 2`，走既有 `_expire_conditions`（自己回合结束→1，下个回合结束→0 移除）。攻击不结束它；重复使用覆盖同一条条件（不叠层、不加骰）。
- **Wholeness**：`heal_dice: "martial_arts"` 由 `unarmed_strike_die(character)` 解析成当前轨道的 d8/d10/d12；`heal_ability: "wis"` 加进 `heal_bonus`；`heal_minimum: 1` 作用在最终治疗量上（Wis 为负也能得到 1）。`cannot_regain_hp` 时 `delta/amount = 0` 但动作与次数已扣。

### 11.4 Superior Defense 时序、抗性、到期

- **窗口**：`_fresh_economy` 写入 `economy["turn_start_window"] = {actor_id, feature_id, capability_id}`；只在有 `turn_start` 成本的能力且资源够付时才开（Focus<3 不开窗，也不给按钮）。
- **关闭**：`resolve_intent` 对任何非 `combat.start`/`combat.end`/`combat.message`/`encounter.ready`/`encounter.unready` 的意图追加 `dnd2024.turn_window.closed`（reducer 幂等、只在 `actor_id` 匹配时 pop）。所以攻击、施法、移动、其他 capability、`end_turn` 与激活自身都会关窗，关掉后不能回头补。
- **激活顺序**：`intent.submitted → dnd2024.class_resource.spent(3) → condition.applied → dnd2024.turn_window.closed`；不产生 `dnd2024.action.spent`，`economy.action/bonus_action` 保持 1。
- **抗性**：条件带 `resist_all_except: ["force"]`，`_resist_damage` 在同一处与枚举式来源一起求值、仍只 `return` 一次，多来源不叠加；Force 永远全额。
- **到期**：条件同时记 `expires_at_minute`（世界时钟）与 `expires_at_round`（10 轮）。`turn.advanced` 前查两个上限；任何其他战斗意图只查时钟上限（`end_turn` 不重复发同一条移除事件），因此一场在一分钟内结束的战斗留下的效果会在下一次战斗意图/下一场 `combat.start` 时被清掉。
- **失能收尾**：`_incapacitation_cleanup_events` 从本批 `condition.applied` 合并后判断 `condition_flags(...)["incapacitated"]`，对带 `ends_on_incapacitated` 元数据的条件发 `condition.removed(reason="incapacitated")`；不按来源分支，恢复后不自动重启。

### 11.5 改动文件对照白名单

| 白名单项 | 状态 |
|---|---|
| 1 class feature catalog（content） | 改（四项定义 + 标签） |
| 2 locales/en、3 locales/zh-CN | **未改**：四项标签基线已存在（`自然帷幕`/`卓越防御`/`不倦`/`身心完满`） |
| 4 rest catalog | 改（三个 Wis 池 + `wholeness_of_body` 的 `requires_subclass`） |
| 5 `features/models.py` | 改（`CapabilityCost.turn_start`、三个 kind、装载期校验） |
| 6 `features/resolver.py` | 改（`combat_capabilities(actor_id=...)`） |
| 7 `combat/resolution.py` | 改（三个新分支、heal 公式、排除式抗性、到期） |
| 8 `combat/engine.py` | 改（窗口生成/关闭、失能收尾、时钟到期接线） |
| 9 `combat/reducer.py` | 改（`turn_window.closed`） |
| 10 `combat/validation.py` | **未改**：伪造能力的拒绝走 `_validate_class_capability` → `view.blocked_reason`，不需要新分支 |
| 11 `combat/view.py` | 改（传 `actor_id`） |
| 12 `resting/engine.py` | 改（`_apply_tireless_exhaustion`） |
| 13 `runtime.py` | **未改**：公共 feed 按事件类型投影，新事件不携带未许可字段（测试 26） |
| 14 `tests/rulesets/test_dnd2024_active_buffs.py` | 新建（65 条） |
| 15 本任务书 | 只追加本报告 |

**白名单外改动**（必须说明）：
- `src/rulesets/dnd2024/features/combat.py`：`CombatContext.turn_start`、`_cost_views` 的 `turn_start` 成本行、`_blocked_reason` 的"start of your turn has already passed"。§4.4.1–2 要求"窗口已过就不能激活"，而该拒绝消息与可用性视图只在这一个文件里产生；不允许改它就只能伪造一个不存在的能力入口。
- `src/rulesets/dnd2024/combat/primitives.py`：`SUPERIOR_DEFENSE_CONDITION`/`SUPERIOR_DEFENSE_DURATION`/`TURN_WINDOW_CLOSED_EVENT` 三个常量。它们同时被 `resolution.py`（写条件）与 `engine.py`（关窗、清理）引用，`primitives.py` 是 combat 层唯一共享常量家。

### 11.6 测试 1–26 逐项结果

全部通过（`tests/rulesets/test_dnd2024_active_buffs.py`，65 条，其中参数化展开后覆盖下列矩阵）：

1. 授予与 owner：`test_the_four_features_appear_only_at_their_own_level`、`..._another_way_of_war_has_no_wholeness...`、`..._ranger_has_no_monk_capability...`
2. Wis 池：`test_three_wisdom_pools_are_separate_and_scaled_by_wisdom`（+3→3）、`test_a_negative_wisdom_still_leaves_one_use`（-2→1）、`test_the_pools_refill_on_a_long_rest_only`、`test_advancing_does_not_silently_refill_a_spent_pool`
3. `test_tireless_costs_a_magic_action_and_one_charge`
4. `test_tireless_keeps_the_higher_pool_and_still_pays`、`test_tireless_never_touches_hit_points_or_other_conditions`
5. `test_tireless_still_grants_one_when_the_formula_is_not_positive`、`test_tireless_rolls_exactly_one_die_per_use`、`..._without_a_charge_produces_no_events`、`..._without_an_action_produces_no_events`
6. `test_a_short_rest_lowers_exhaustion_by_one_with_tireless`（3→2）、`test_a_short_rest_invents_no_exhaustion_event_at_zero`、`test_a_ranger_below_tenth_level_gains_nothing...`、`test_a_long_rest_still_takes_exactly_one_level`
7. `test_natures_veil_costs_a_bonus_action_and_marks_only_its_owner`
8. `test_the_veil_survives_its_own_turn_and_ends_after_the_next`、`test_attacking_does_not_end_the_veil_early`
9. `test_an_attack_from_a_blind_attacker_is_at_disadvantage`（隐形→disadvantage，到期后同一攻击回到无优劣势）、`test_the_veil_does_not_stack`
10. `test_the_veil_is_not_recovered_by_a_short_rest`、`test_the_veil_without_a_bonus_action_creates_no_condition`
11. `test_wholeness_rolls_the_martial_arts_die_of_its_level[d8/d10/d12]`
12. `test_wholeness_is_a_bonus_action_for_itself_only`
13. `test_wholeness_heals_one_at_the_worst_and_clamps_at_the_maximum`、`test_wholeness_pays_its_cost_even_when_healing_is_blocked`
14. `test_the_window_opens_with_the_first_turn_of_the_combat`、`test_a_monk_without_three_focus_is_offered_nothing`、`test_the_next_turn_offers_the_window_again`（额外 turn slot 见 §11.8）
15. `test_superior_defense_costs_three_focus_and_no_action`、`test_the_window_closes_after_the_first_thing_you_do`、`test_ending_the_turn_also_closes_the_window`
16. `test_superior_defense_resists_every_type_but_force[bludgeoning/fire/radiant/force]`、`test_resistance_does_not_stack_with_another_source`
17. `test_damage_taken_before_the_activation_is_not_rerolled`
18. `test_any_incapacitating_condition_ends_it[incapacitated/paralyzed/stunned/unconscious]`、`test_becoming_incapacitated_ends_superior_defense`、`test_recovering_from_incapacitation_does_not_turn_it_back_on`、`test_a_harmless_condition_leaves_it_alone`
19. `test_superior_defense_expires_after_ten_rounds`、`test_the_world_clock_can_end_it_before_the_rounds_do`、`test_a_live_effect_is_not_swept_by_the_clock`、`test_a_leftover_buff_is_cleared_when_the_next_fight_starts`
20. `test_replaying_the_activation_neither_spends_nor_heals_twice`
21. `test_the_pools_and_the_window_survive_a_round_trip`
22. `test_another_actors_turn_cannot_open_or_use_the_window`
23. `test_forged_declarations_are_refused[3 变体]`、`test_the_force_exception_cannot_be_declared_by_the_client`
24. `test_rage_resistance_still_halves_once_with_its_own_source`
25. `test_existing_features_still_work_after_the_slice` + 邻接四份 99 条
26. `test_the_public_feed_does_not_leak_the_new_condition_fields` + 全量 2251 条

### 11.7 聚焦 / 回归 / 全量 / ruff / diff 输出

```
pytest tests/rulesets/test_dnd2024_active_buffs.py -q                 65 passed        (69.18s)
pytest monk_resources + monk_combat + resting + survivor -q           99 passed        (223.23s)
pytest tests/rulesets -q -n 6 --dist loadfile                         2251 passed      (466.23s / 672.98s 两轮，第二轮与提交的测试文件一致)
ruff check <白名单 5 个目录/文件>                                     All checks passed
mypy（全仓）                                                          Success: no issues found in 102 source files
git diff --check                                                      无输出
```

### 11.8 RNG、重放、存档与权限证据

- **RNG**：Tireless 每次恰好消耗一个 d8（`rolled` 与 `rolls` 一致）；Wholeness 一次一个 Martial Arts die；Superior Defense 不掷骰。敌人攻击的 `SequenceRng([15, 4, 4])` 在优势与普通两种情况下都给出同一伤害，避免"测试自己制造骰序"。
- **原子性**：资源不足/动作不足/窗口已关/伪造能力都在生成事件前拒绝（`resolve_intent` 返回 `ok: false`），不存在"只扣资源不产生效果"的批次。
- **重放**：同一 `intent_id` 重放返回 `replayed: true` 且 `apply_batch` 返回 `applied: false`，Focus 仍为 15、治疗不二次生效。
- **存档**：`get_character_sheet` 往返后 Focus 15 与 `expires_at_round: 11` 保留；窗口在支付后已关闭，因此存档里不残留"可再付一次"的记号。
- **权限**：窗口按 `actor_id` 绑定，非当前行动者开不出窗也用不了；公共 feed 不投影 `resist_all_except`/`expires_at_*` 等内部字段。

### 11.9 与契约的偏差及原因

1. §5 白名单缺两个必要文件（`features/combat.py`、`combat/primitives.py`），已在 §11.5 说明理由；白名单里 `validation.py`/`runtime.py` 两项未用到。
2. §6.14 的"额外 turn slot"未单独构造：Thief 的额外回合需要 Rogue 17，与 Monk 18 不能同角色；该路径由 F20 的 `_fresh_economy` 重建逻辑共用，本切片只验证"每个自己的回合各开一次"（首回合与后续回合）。
3. `heal` 分支由固定 `heal_bonus` 扩展为 `heal_dice`/`heal_ability`/`heal_minimum`，既有治疗能力（Second Wind 等）行为不变（全量 2251 条通过）。
4. 战斗外没有新的时间推进入口：残留效果依赖"下一次战斗意图/下一场 `combat.start`"或回合推进时读到的世界时钟清除，不在长休里做条件清理（见 §11.10）。

### 11.10 未决事项

1. **战斗外到期依赖时钟被读取**：世界时钟停走且不再发生任何战斗意图时，`superior_defense` 条件会留在角色表上直到下一次开战。Bardic Inspiration 的既有做法是"读时判定"，条件需要落库所以不能照抄；要彻底解决需要一个"条件到期"的通用清扫入口（属其他切片）。
2. **§6.13 的 Supreme Healing 交叉验证**没有专门用例：职业 capability 的治疗路径从不读取法术最大化，结构上不可能受影响；要构造 Life Cleric 17 的治疗用例需要另一条升级链。
3. **未完成**：Ranger/Monk 其他特性、探索模式隐形、通用 Short Rest 重构、前端按钮与动画均不在本切片，未实现。

