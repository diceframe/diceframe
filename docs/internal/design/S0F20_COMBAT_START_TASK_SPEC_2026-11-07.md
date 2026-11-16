# S0-F20 任务书：战斗开始、先攻与额外首回合

| 项 | 值 |
|---|---|
| 切片编号 | S0-F20（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `combat.start`、initiative、`turn.start` 与六项战斗开场特性 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；p29-30/49/51-52/64，Heroic Inspiration p183；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | F1 被动检定与通用先攻来源；S0-E3 首回合及后续回合的 turn-start 事件 |
| 建议分支 | `feat/dnd2024-s0f20-combat-start` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 记录 F1/S0-E3 合入后的实际提交并新建干净工作树；若首行动者 turn-start 仍不能产生有序事件，停止。
2. 完整实现 `feral_instinct`、`uncanny_metabolism`、`perfect_focus`、`persistent_rage`、`heroic_warrior`、`thiefs_reflexes`，不拆成文案授予或后续批次。
3. 先攻骰、选择、资源恢复、首回合 schedule 和每个回合开始均由服务端结算；重放不得重掷或重复恢复。
4. 不实现 Remarkable Athlete、Survivor、Superior Defense 或召唤物回合；仅复用其已有接口，不能借本切片补做其他任务。
5. 仅使用官方 PDF；不采用第三方关于 Thief 额外回合、Persistent Rage 或 Perfect Focus 的解释。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p29、p30，Barbarian：

> **Level 7: Feral Instinct.** You have Advantage on Initiative rolls.
>
> **Level 15: Persistent Rage.** When you roll Initiative, you can regain all expended uses of Rage. After you regain uses of Rage in this way, you can’t do so again until you finish a Long Rest.
>
> In addition, your Rage is so fierce that it now lasts for 10 minutes without you needing to do anything to extend it from round to round. Your Rage ends early if you have the Unconscious condition (not just the Incapacitated condition) or don Heavy armor.

SRD 5.2.1 p49，Champion：

> **Level 10: Heroic Warrior.** During combat, you can give yourself Heroic Inspiration whenever you start your turn without it.

SRD 5.2.1 p51-52，Monk：

> **Uncanny Metabolism.** When you roll Initiative, you can regain all expended Focus Points. When you do so, roll your Martial Arts die, and regain a number of Hit Points equal to your Monk level plus the number rolled. Once you use this feature, you can’t use it again until you finish a Long Rest.
>
> **Perfect Focus.** When you roll Initiative and don’t use Uncanny Metabolism, you regain expended Focus Points until you have 4 if you have 3 or fewer.

SRD 5.2.1 p52，Superior Defense 的回合开始语义为“at the start of your turn”；本切片只复用该边界，不实现其效果。

SRD 5.2.1 p64，Thief：

> **Level 17: Thief’s Reflexes.** You can take two turns during the first round of any combat. You take your first turn at your normal Initiative and your second turn at your Initiative minus 10.

SRD 5.2.1 p183，Heroic Inspiration：

> If you (a player character) have Heroic Inspiration, you can expend it to reroll any die immediately after rolling it, and you must use the new roll. If you gain Heroic Inspiration but already have it, it’s lost unless you give it to a player character who lacks it.

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| `_start_combat_event()` 为玩家、同伴、敌人各掷一次 d20，按 total 降序排序并返回单个 `dnd2024.combat.started` | `src/rulesets/dnd2024/combat/resolution.py::_start_combat_event` |
| `combat.start` 当前以 `events.append(...)` 接入；S0-E3 必须提供首行动者后续事件列表 | `src/rulesets/dnd2024/combat/engine.py::resolve_intent` |
| `_end_turn_events()` 选择下一行动者并生成 `dnd2024.turn.advanced`，已有 Survivor turn-start 接口 | `combat/resolution.py::_end_turn_events`、`_turn_start_regen_events` |
| 每次 `turn.advanced` 重建 action/bonus/reaction/movement 经济并恢复反应 | `combat/engine.py::_fresh_economy`；`combat/reducer.py::_reduce_event` |
| Rage 基线靠续期标记和 `RAGE_MAX_ROUNDS=100` 维持，并在失能族或战斗结束时移除 | `combat/engine.py::_expire_conditions`；`combat/reducer.py::_reduce_event`；`combat/primitives.py` |
| Monk 已有 `focus_points` 与长休资源 `uncanny_metabolism`；Barbarian 的 Rage 池由等级轨道决定 | `content/rest_catalogs/srd_recovery.json`；`content/progression_catalogs/srd_classes.json` |
| 职业治疗、资源支付和 HP clamp 已有事件/reducer 路径 | `combat/resolution.py::_class_capability_events`；`combat/reducer.py::_apply_hp_change` |
| 六个目标 id 已在 progression/subclass 表列出，但 canonical 定义尚不存在 | `content/progression_catalogs/srd_classes.json`；`content/subclass_catalogs/srd_subclasses.json`；class feature catalog |
| combat feed 对公开字段有白名单，新增先攻/来源字段需显式处理 | `src/rulesets/dnd2024/runtime.py::_recent_combat_events` |

## 4. 精确行为与数据契约

### 4.1 统一先攻掷骰与开场选择

1. 在 F1 的 initiative 来源查询中读取真实特性；Feral Instinct 只给持有者先攻优势。每个有来源者掷两枚 d20 取高，无来源者仍一枚；优势与劣势按通用归一规则抵消，不叠成三骰。
2. `initiative_rolls` 每项保留 `rolls`、选中 `roll`、`modifier`、`total`、`advantage_mode`、`advantage_sources`；原有排序键不变，平手仍按既有确定顺序，不能为 Thief 重掷。
3. 每个符合条件的角色在同一次 `combat.start` 内按 initiative 结算前的稳定 actor 顺序处理开场选择：Uncanny/Perfect → Persistent Rage → Evergreen 等后续已合入钩子。选择只影响自己的资源和 HP，不改变先攻 total。
4. 可选开场能力通过服务端开场决策或明确的预提交选择表达；未选择等同不使用，不得默认自动消耗长休限制。预提交只能声明是否使用，不能声明恢复量或骰值。
5. 所有选择、骰和结果进入同一事件批次；失败校验不扣资源。combat started 事件先建立 canonical combat，再应用资源/HP/turn-start 事件，顺序与 S0-E3 一致。

### 4.2 Uncanny Metabolism 与 Perfect Focus

1. Monk 2 级起可在 roll Initiative 时选择 Uncanny：先检查 `uncanny_metabolism` 有一次可用，再将 Focus 恢复到当前最大值，消耗该长休资源，并掷一个当前 Martial Arts die。
2. 治疗量为 `Monk level + die`，目标是持有者本人，事件含 `feature_id`、`martial_arts_die`、`roll`、`monk_level`、`healing=true`；受 `cannot_regain_hp` 时 HP 不变但 Focus 恢复和资源消耗仍有效。
3. 即使 Focus 已满或 HP 已满，只要明确选择使用，仍按规则消耗长休使用并掷治疗骰；不能借“无实际变化”免费保留资源。
4. Perfect Focus 仅在本次没有使用 Uncanny 且当前 Focus≤3 时，将 current 设为 4；Focus≥4 不变，超过 4 不扣减，不消耗资源、不掷骰。
5. 顺序固定为先决定是否 Uncanny，再判断 Perfect；同一次先攻不得同时恢复全部 Focus 并触发 Perfect。长休只恢复 Uncanny 使用，不为 Perfect 增加任何池。

### 4.3 Persistent Rage 与 Rage 生命周期

1. Barbarian 15 级起可在 roll Initiative 时选择恢复全部已消耗 Rage；新增 `persistent_rage` 长休一次资源，选择后 current Rage 设为等级轨道 maximum，再消耗该资源。
2. Rage 已满时仍可选择并消耗，但未选择不消耗；短休不恢复 `persistent_rage`，普通 Rage 池原有短休恢复不变。
3. 15 级后进入的 Rage 使用 `duration="persistent_rage"` 和 100 轮总时长，不需要攻击、迫使豁免或附赠动作续期；不得每回合要求 `sustained_this_turn`。
4. Persistent Rage 仅因 Unconscious 或穿上 Heavy armor 提前结束；单纯 Incapacitated、Stunned、Paralyzed 不再按旧 Rage 分支结束，除非实际存在 Unconscious。15 级前仍走基线规则。
5. 100 个战斗轮对应 10 分钟，是总持续上限且不因旧 `rage_sustain` 重置；战斗结束本身不是官方列出的提前结束条件，不得清除仍在期限内的 Persistent Rage。Heavy armor 判定读取真实装备协调结果，不信任客户端标志。

### 4.4 Heroic Warrior 与每个 turn.start

1. 首行动者和后续每个新行动者都经过同一 turn-start 管线；S0-E3 的再生、F20 的 Heroic Warrior 和已合入的其他钩子按注册顺序各执行一次。
2. Champion 在自己的回合开始且没有 Heroic Inspiration 时，可选择给自己一份；已有时不触发，也不自动转送他人。若提供转送，必须由通用 Heroic Inspiration 规则显式选择缺少它的玩家角色，不能默认转移。
3. 状态为唯一布尔事实及来源，不叠层；事件 `dnd2024.heroic_inspiration.changed` 至少含 `actor_id`、`granted=true`、`feature_id="heroic_warrior"`。
4. Heroic Inspiration 消耗时只能在一次骰已掷后立即选择重掷其中一枚，必须使用新值；优劣势只重掷所选一枚。若 F1 尚未提供该通用消耗接口，本切片只交付授予状态并停止于集成缺口，不能伪造“优势”代替。
5. 重复 view、available_intents、事件重放或存档读取不授予额外实例；两个 Thief 回合各自是一次 turn.start。

### 4.5 Thief's Reflexes 首轮 schedule

1. Rogue Thief 17 级在 combat.start 时拥有两个首轮 turn slot：正常 slot 使用原 `total`，第二 slot 使用 `total-10`；两者与所有其他战斗者统一按 total 降序及稳定 tie-break 排序。
2. 第二 slot 不是立即附在正常回合后，也不是额外复制角色；它只占其计算位置。两个 slot 使用同一 actor identity，但必须有不同 `turn_index`/slot id，分别刷新经济、反应和 turn-start。
3. 权威状态增加明确 `turn_slots`：`{slot_id, actor_id, initiative_total, kind:"normal"|"thief_reflexes", rounds:[1]}`；普通 slot 每轮有效，Thief 第二 slot 仅 round 1。公开 `initiative` 可由 slot 投影，不能丢失审计。
4. 进入第二轮时 schedule 只保留正常 slot，Thief 仍按原正常 Initiative 行动一次；不得再次插入 `Initiative-10`，不得重新掷骰或让第二轮变成三次行动。
5. `_end_turn_events()` 按 slot 而非数组中 actor 首次出现位置推进，跳过死亡/稳定/0 HP companion 时不得误删另一个 slot；若正常 slot 不再有效，首轮第二 slot 是否可行动按持有者当前状态单独判断。
6. 每回合使用和条件期限以真实 slot 为边界；保存/加载、战斗中加入、召唤物共享先攻及结束战斗不得把 schedule 重排成未定义顺序。F27 的 linked slot 必须复用此模型。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json`：仅 `persistent_rage`
5. `src/rulesets/dnd2024/features/resolver.py`
6. `src/rulesets/dnd2024/combat/resolution.py`
7. `src/rulesets/dnd2024/combat/engine.py`
8. `src/rulesets/dnd2024/combat/reducer.py`
9. `src/rulesets/dnd2024/combat/validation.py`
10. `src/rulesets/dnd2024/combat/view.py`
11. `src/rulesets/dnd2024/combat/primitives.py`：仅 slot/常量共享定义
12. `src/rulesets/dnd2024/runtime.py`
13. 新建 `tests/rulesets/test_dnd2024_combat_start_features.py`
14. 本任务书：只允许追加执行报告

不改 progression/subclass catalog、通用前端、休息服务或其他特性目录；需要这些改动时停止。

## 6. 编号测试矩阵

1. 六个特性按 Barbarian 7/15、Monk 2/15、Champion 10、Thief 17 的真实等级授予，低一级和其他职业不获得。
2. Feral Instinct 先攻掷两骰取高，rolls/total/来源完整；其他先攻仍单骰，多优势来源不增加第三骰。
3. Feral 与劣势抵消时按通用模式结算；先攻平手和 RNG 顺序与基线一致。
4. Uncanny 使用后 Focus 回满、长休资源扣除，治疗为 Monk level+当前 Martial Arts die，事件顺序可重放。
5. Uncanny 在 Focus/HP 已满时明确使用仍扣资源并掷骰；资源不足或同场第二次使用被拒绝。
6. `cannot_regain_hp` 不阻止 Focus 恢复与资源消耗，只阻止 HP；长休后 Uncanny 可再用。
7. 使用 Uncanny 的同次先攻 Perfect 不触发；未使用且 Focus 为 0/3 时补到 4，Focus 为 4/5 时不变。
8. Perfect 不消耗长休资源、不掷骰；Monk 14 级没有，15 级拥有。
9. Persistent Rage 选择后 Rage 回满并消耗长休资源；未选择不变，短休不恢复该开场能力，长休恢复。
10. 15 级 Rage 不攻击、不续期时在第 99 轮仍存在，并于第 100 轮结束/满 10 分钟时到期；15 级前旧 Rage 按原规则结束，战斗结束不提前清除，上限不重置。
11. Persistent Rage 在单纯 Incapacitated/Stunned 时不结束，在 Unconscious 或真实穿上 Heavy armor 时立即结束。
12. Champion 首回合及后续回合无 Heroic Inspiration 时各可授予一次；已有时不叠加、不重复事件。
13. 两个 Thief 回合均执行 Heroic Warrior 等 turn-start；重复投影和重放不额外授予。
14. Heroic Inspiration 重掷只选一枚且必须使用新骰；已有时的转送只能选择缺少它的玩家角色。
15. Thief 正常 slot 使用原 total，第二 slot 使用 total-10 并落在统一排序的正确位置，不固定在正常回合后。
16. 两个 slot 分别刷新动作、附赠动作、反应、移动与每回合 feature usage，身份仍为同一角色。
17. 首轮经过所有 slot 后进入第二轮，只保留正常 Thief slot；第二轮及以后没有 Initiative-10 插入。
18. Thief 第二 slot 与其他 actor 同 total 时 tie-break 确定且重放一致；不额外消耗先攻 RNG。
19. 首行动者为 Survivor/Champion/Thief 时，combat started 先于 turn-start 事件，事件列表不错序、不丢首回合。
20. 前行动者重复豁免、turn.advanced、新行动者开场的顺序保持；额外 slot 不错误推进两次 round。
21. 死亡、稳定、0 HP companion 和胜利/团灭跳过逻辑不会误用另一个 slot 或生成不存在的新回合。
22. 存档加载后继续首轮 schedule，重放 combat.start 不重复 Focus/Rage/HP/Heroic 变化。
23. 非本人提交开场选择、伪造恢复量/骰值/额外 slot、跨职业使用均被拒绝。
24. 战斗中加入和后续 linked slot 不重排已结算先攻，不产生第二轮 Thief 插入。
25. 公共 feed 只显示许可字段，完整 HP/资源不泄漏给无权限席位；权威账本审计完整。
26. Rage、Survivor、Action Surge、Sneak Attack、Uncanny Dodge 与条件到期测试无回归。
27. combat start、turn advance、death save、Hold Person、Counterspell 和召唤前置测试通过。
28. 全量 rulesets 测试、ruff 和 diff 检查通过；任何基线失败均有 HEAD 对照。

## 7. 非目标与停止条件

### 7.1 非目标

- Remarkable Athlete、Survivor、Superior Defense、Archdruid 的效果本体。
- 通用先攻分数制、2D 战斗棋盘、完整坐骑/召唤物 AI 或前端改版。
- 把 Heroic Inspiration 简化成永久优势，或让 Thief 第二轮继续双回合。

### 7.2 停止条件

1. F1/S0-E3 未提供优势来源、首行动者 turn-start 和可重放事件列表。
2. 无法用同一 actor identity 表达两个独立 slot，或回合经济/条件期限必须靠复制角色才能实现。
3. Heroic Inspiration 通用状态与消耗接口缺失，且不能在本白名单内完整补齐授予及消费契约。
4. Persistent Rage 会与旧失能/战斗结束清理双写，无法保留完整 10 分钟，或 Heavy armor 无权威装备事实。
5. 需要改 progression、前端、通用休息服务或其他切片才能通过核心测试。
6. 基线或并行改动已有同责 combat schedule，存在顺序或 RNG 冲突。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_combat_start_features.py -q
python -m pytest tests/rulesets/test_dnd2024_survivor.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_monk_resources.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_resting.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_combat_start_features.py
git diff --check
git status --short
```

记录实际 Python 路径和测试 1–28 映射；至少单独展示 Uncanny/Perfect 互斥、Persistent Rage 十轮、Heroic 两个 turn-start、Thief 第二轮不插入四组证据。

## 9. 交付要求

1. 一个提交/PR 只含本切片，报告真实基线、改动清单和 slot 数据形状。
2. 报告先攻及额外骰 RNG、资源恢复顺序、首/后续 turn-start、重放和存档证据。
3. 证明普通 Rage 与 Persistent Rage 分流、旧特性回归和公开投影权限。
4. 不得把未实现的 Heroic 消耗或第二轮双回合写成正确行为。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：基线 `842b06c0`（`integration/dnd2024-srd` 栈顶）；分支 `feat/dnd2024-s0f20-combat-start`；提交见本条 PR，`feat` / `test` / `docs` 三段。
- 先攻来源与开场选择：见 §10.1。
- Uncanny / Perfect / Persistent 资源结果：见 §10.2。
- Heroic Inspiration 状态与消费：见 §10.3。
- Thief turn slot 与第二轮：见 §10.4。
- 改动文件对照白名单：见 §10.5。
- 测试 1–28 逐项结果：见 §10.6。
- 聚焦 / 回归 / 全量 / ruff / diff 输出：见 §10.7。
- RNG、重放、存档与权限证据：见 §10.8。
- 与契约的偏差及原因：见 §10.9。

### 10.1 先攻来源与开场选择

- 先攻优势仍走 F1 的来源查询（`_initiative_roll`）：`feral_instinct` 为特性声明、`invisible` 为条件声明，多来源由 `mode_from_sources` 归一成 advantage，不产生第三骰；无来源者只掷一枚，事件里没有 `rolls` 键。
- 开场选择只从 `combat.start` 的 `initiative_choices` 读取，键集限定为 `INITIATIVE_CHOICES`（`uncanny_metabolism`、`persistent_rage`），值只接受 `True`/`False`。未知键、非布尔值、非玩家 actor、缺特性、长休资源已耗尽均在 validation 层拒绝，不掷骰、不扣资源。
- 结算顺序：先攻骰 → `_initiative_opening_events`（按去重后的先攻 actor 顺序，每个 actor 先 Uncanny 或 Perfect，再 Persistent Rage）→ `dnd2024.combat.started` → 首行动者 turn-start。全部在同一批次内。
- RNG 消耗顺序固定为：玩家 d20 →（可选第二枚）→ 同伴 d20 → 敌人 d20 → Uncanny 的 Martial Arts die。Perfect Focus 与 Persistent Rage 不取随机数。

### 10.2 Uncanny / Perfect / Persistent 资源结果

- Uncanny：先校验 `uncanny_metabolism` 长休资源 ≥1，再把 `focus_points` 补到 maximum，然后 `resource.changed`（`healing=true`、`feature_id`、`martial_arts_die`、`roll`、`monk_level`、`amount = monk_level + roll`），最后 `class_resource.spent` 1 点。`monk_level` 读 `build.class_levels` 里 monk 那一行，多职业时不会被算成 0。
- `cannot_regain_hp` 只把 HP 增量压到 0 并带 `blocked_by`，Focus 恢复与资源消耗照常。
- Perfect Focus：仅当本次未用 Uncanny 且 `current ≤ 3` 时补到 4（不超 maximum）；不消耗资源、不掷骰。
- Persistent Rage：选择后 `rages` 补到等级轨道的 maximum，再 `class_resource.spent` 1 点；未选择不消耗。资源表新增 `persistent_rage`（`short: none`、`long: all`）。
- 15 级 Rage 记 `duration="persistent_rage"`、`turns_remaining=100`；每轮结束递减，没有旧 Rage 那条"进入回合免费"的豁免，因此第 99 轮结束剩 1、第 100 轮结束到期。只被 Unconscious 或真实重甲提前结束；纯 Incapacitated/Stunned/Paralyzed 不结束；`combat.ended` 不清除。

### 10.3 Heroic Inspiration 状态与消费

- 角色级布尔状态 `heroic_inspiration = {"granted": true, "source": "heroic_warrior"}`，不叠层；已有则不出事件、不覆盖、不转送。
- 授予点在 turn-start 管线（`_turn_start_events`：先 Survivor 再生，后 Heroic），首行动者、普通回合、Thief 第二 slot 走同一个入口。
- **消费端缺口（按裁决登记，不实现）**：Heroic Inspiration 的重掷是"任意骰、掷后立即重掷并必须采用新值"，本仓 d20 窗口无法表达，本切片不伪造"优势"代替。已加反向断言 `test_heroic_inspiration_has_no_spending_entry_point`：授予存在，但 `available_intents` 里没有任何 heroic 入口。
- 公共 feed 白名单新增 `dnd2024.heroic_inspiration.changed` 与 `granted` / `feature_id`；投影不夹带 HP、资源或角色表。

### 10.4 Thief turn slot 与第二轮

- `initiative` 保持按人去重的列表（旧读者只问"谁在场"），新增权威 `turn_slots: [{slot_id, actor_id, initiative_total, kind, rounds?}]`。`slot_id` 形如 `player:gm#1` / `player:gm#2`；`rounds` 只在 `thief_reflexes` slot 上出现，恒为 `[1]`。
- 排序键与先攻一致：total 降序 → 非玩家后置 → actor_id → normal 先于 thief_reflexes。第二 slot 的 total 是 `total - 10`，落在其计算位置（测试里 goblin 16 位于 25 与 15 之间）。
- `turn_index` 从此是 slot 下标；`_current_actor`、公开视图的 `current_actor_id` / `turn_slot_id`、回合级额度判定（Brutal Strike 半速移动、暴击半速移动）全部改读 slot。旧存档无 `turn_slots` 时由 `combat_turn_slots()` 退回"每个 actor 一个 slot"，顺序与改动前一致。
- 进入第二轮时 `thief_reflexes` slot 因 `rounds=[1]` 被跳过，只保留正常 slot；轮次只在真正绕回一周时 +1。
- 战斗中迟到的玩家由 reducer 同时插入 `initiative` 与 `turn_slots`（插在当前 slot 之后），已结算顺序与当前下标都不动。

### 10.5 改动文件对照白名单

白名单内：class feature catalog、`srd_recovery.json`（仅 `persistent_rage`）、`combat/resolution.py`、`combat/engine.py`、`combat/reducer.py`、`combat/validation.py`、`combat/view.py`、`combat/primitives.py`（仅 slot 与常量）、`runtime.py`、新建测试文件、本任务书追加报告。

白名单外但有改（均为既有测试的断言同步，见 §10.9）：`tests/rulesets/test_dnd2024_class_features.py`、`test_dnd2024_monk_resources.py`、`test_dnd2024_improved_critical.py`、`test_dnd2024_survivor.py`。

未改动：`features/resolver.py`、progression/subclass catalog、前端、休息服务、两份 locale 文案（六个名称在本地化文件里早已存在，本次只补 canonical 定义）。

### 10.6 测试 1–28 逐项结果

40 条用例覆盖矩阵 1–12、15–25；13 部分覆盖、14 反向断言、其余未覆盖项见 §10.9。

| 矩阵项 | 用例 |
|---|---|
| 1 | `test_the_six_features_arrive_at_their_own_level` |
| 2 | `test_feral_instinct_rolls_two_dice_and_keeps_the_higher`、`test_a_second_advantage_source_still_rolls_two_dice`、`test_a_barbarian_below_seven_rolls_a_single_die` |
| 3 | 平手部分：`test_a_thief_slot_tied_with_an_enemy_orders_deterministically`；劣势抵消见 §10.9 |
| 4 | `test_uncanny_metabolism_refills_focus_heals_and_spends_its_charge` |
| 5 | `test_uncanny_metabolism_at_full_focus_still_spends_and_rolls`、`test_uncanny_metabolism_cannot_be_declared_again_before_a_long_rest` |
| 6 | `test_uncanny_metabolism_cannot_regain_hp_still_refills_focus` |
| 7 | `test_perfect_focus_fills_to_four_only_from_below[0-4/3-4/4-0/5-0]`、`test_perfect_focus_does_not_fire_when_uncanny_was_used` |
| 8 | 同上（`rng.values == []` 证明不掷骰）；等级在矩阵 1 |
| 9 | `test_persistent_rage_refills_every_use_and_spends_its_charge`、`test_persistent_rage_untouched_when_not_declared`、`test_persistent_rage_charge_follows_the_rest_catalogue` |
| 10 | `test_persistent_rage_lasts_ninety_nine_rounds_and_expires_on_the_hundredth`、`test_level_seven_rage_keeps_the_old_duration`、`test_persistent_rage_does_not_end_with_the_combat`、`test_level_fifteen_rage_is_recorded_as_persistent` |
| 11 | `test_persistent_rage_survives_incapacitation_but_not_unconsciousness`、`test_persistent_rage_ends_on_real_heavy_armor` |
| 12 | `test_heroic_warrior_grants_at_turn_start_then_stops`、`test_heroic_warrior_is_silent_before_level_ten` |
| 13 | 两个 slot 各自走 `_end_turn_events`：`test_both_thief_slots_refresh_economy_and_reactions`；重放不额外授予：`test_replaying_combat_start_neither_rerolls_nor_regrants`；Thief 第二 slot 上的 Heroic 见 §10.9 |
| 14 | 未实现，反向断言 `test_heroic_inspiration_has_no_spending_entry_point` |
| 15 | `test_thief_gets_two_slots_with_the_second_ten_lower` |
| 16 | `test_both_thief_slots_refresh_economy_and_reactions` |
| 17 | `test_second_round_keeps_only_the_normal_slot` |
| 18 | `test_a_thief_slot_tied_with_an_enemy_orders_deterministically` |
| 19 | `test_combat_started_precedes_every_turn_start_grant` |
| 20 | `test_the_first_round_wraps_only_after_every_slot` |
| 21 | `test_a_dead_thief_loses_both_slots` |
| 22 | 重放：`test_replaying_combat_start_neither_rerolls_nor_regrants`；存档加载见 §10.9 |
| 23 | `test_illegal_initiative_choices_are_refused[4 组]`、`test_a_character_without_the_feature_cannot_declare_it` |
| 24 | `test_a_late_joiner_gets_one_slot_and_keeps_the_schedule` |
| 25 | `test_the_public_feed_keeps_heroic_inspiration_to_its_public_fields` |
| 26–27 | 见 §10.7 的回归命令 |
| 28 | 见 §10.7 |

### 10.7 聚焦 / 回归 / 全量 / ruff / diff 输出

- 聚焦：`pytest tests/rulesets/test_dnd2024_combat_start_features.py -q` → `40 passed`。
- 邻接回归：`test_dnd2024_survivor.py test_dnd2024_rage.py test_dnd2024_monk_resources.py test_dnd2024_combat.py test_dnd2024_resting.py` → `136 passed`。
- 全量：`pytest tests/rulesets -q -n 6 --dist loadfile` → `2186 passed`，`0 failed`，453 s（Windows 本机，Python `E:\Github\diceframe\.venv`）。
- 静态：`ruff check src/ tests/` → `All checks passed!`；`mypy` → `Success: no issues found in 102 source files`；`git diff --check` 无输出。

### 10.8 RNG、重放、存档与权限证据

- RNG：先攻按 actor 顺序取骰，Feral 只让持有者多取一枚；Uncanny 的治疗骰排在全部先攻骰之后；Perfect Focus 与 Persistent Rage 不取随机数（`rng.values == []`）。
- 重放：同一 `intent_id` + `expected_version` 再提交，`resolve_intent` 返回 `replayed: true` 与原 `batch_id`，`apply_batch` 返回 `applied: false`，HP / Focus / 长休资源都不变；RNG 未被消费。
- 存档：`turn_slots` 是权威状态里的普通 JSON，旧状态没有该键时 `combat_turn_slots()` 退回单 slot，不做读时迁移。
- 权限：公开 feed 只输出白名单字段与 `event_id`/`batch_id`/`intent_type`/`state_version` 审计包装，不带 `hp`/`resources`/`character`；`encounter_presets` 仍只给 GM。

### 10.9 与契约的偏差及原因

1. **白名单外改了 4 份既有测试，只改断言。** `test_dnd2024_monk_resources.py`、`test_dnd2024_improved_critical.py`、`test_dnd2024_class_features.py` 里的 2 级 Monk 特性列表现在多了 `uncanny_metabolism`——这是矩阵 1 要求的，列表必须同步；`test_dnd2024_survivor.py` 两处按事件位置断言（`types[-2]`、`len(types) == 3`）被 Heroic Warrior 的 turn-start 事件打破，改为断言"推进在前、再生在后 / 再生紧随 started"，比原断言更严。
2. **矩阵 3 的"Feral 与劣势抵消"没有用例。** 内容里不存在任何先攻劣势来源（`initiative_advantage` 只有三处，全是 true），无法在不改 catalog 的前提下构造；归一逻辑由 F1 的 `mode_from_sources` 承担并有既有测试。本切片只补了平手确定性。
3. **矩阵 13 的"Thief 第二 slot 上执行 Heroic Warrior"没有直接用例。** 需要 Thief/Champion 多职业角色，而仓库的 advancement 路径只产出单职业角色；结构上两个 slot 走同一个 `_end_turn_events` → `_turn_start_events` 入口，且该测试已证明第二 slot 会重建经济与反应。
4. **矩阵 14 按用户裁决只登记缺口**（见 §10.3），用反向断言把缺口钉在测试里。
5. **矩阵 22 的"存档加载"只做了重放那一半。** `tests/rulesets` 里没有战斗存档的读写入口，状态就是 `ruleset_runtime.state` 里的普通字典；`turn_slots` 缺失时的回退路径由 `combat_turn_slots()` 覆盖。
6. **Persistent Rage 的 100 轮计数**改掉了旧 Rage 分支的"进入回合免费"豁免：10 分钟是从进入那一刻起的 100 轮，因此第 99 轮结束剩 1、第 100 轮结束到期。若沿用旧豁免会晚一轮到期，与矩阵 10 的"第 100 轮结束"矛盾。
7. **`turn_index` 语义变更**（从 initiative 下标改为 slot 下标）。没有 slot 的战斗两者等价；有 Thief 的战斗里旧客户端若用 `turn_index` 去索引 `initiative` 会错位，因此公开视图新增 `turn_slot_id`，并以 `current_actor_id` 为准。
8. **只看门禁才现形的缺陷**：
   - `_current_actor` 与两处"是否本人回合"判定原本都拿 `initiative[turn_index]` 比较，单 slot 时永远正确，只有 Thief 第二 slot 才会错。
   - 全量跑完后才发现 `test_dnd2024_improved_critical.py` 也硬编码了 2 级 Monk 特性列表。
   - 我在 resolution 里新写的 `_class_level(character, class_id)` 与 `CheckWindowsMixin._class_level(character)` 同名，MRO 上盖掉了后者，死亡豁免的 Indomitable 重掷加成因此抛 `TypeError`——F20 自己的 40 条全过，是邻接回归里两条死亡豁免用例炸出来的。已删掉自写版本，改用既有 `font_of_magic.declaring_class_level`。
