# S0-F22 任务书：Channel Divinity 与治疗分配/rider

| 项 | 值 |
|---|---|
| 切片编号 | S0-F22（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | Cleric/Paladin `channel_divinity`、`lay_on_hands`、`disciple_of_life`、`preserve_life`、`blessed_healer`、`restoring_touch` |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Cleric p37、Life p40、Paladin p53-55；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | D1 Supreme Healing；D2 长休使用记账；F4 常驻法术授予；F13 条件免疫/抑制；Channel 资源沿用基线 |
| 建议分支 | `feat/dnd2024-s0f22-channel-healing` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 从前置任务实际集成提交开工；若 Supreme Healing、法术槽来源、动作/触摸范围或 Channel 共享资源接口不存在，停止。
2. 本切片实现 Divine Spark、基础 Turn Undead、Divine Sense 和全部列出的治疗/解毒/状态移除；Sear Undead 伤害 rider 属于 F23，但共享 Turn 事件必须预留真实来源。
3. Divine Spark、Turn Undead、Preserve Life 共用 Cleric Channel；Divine Sense 与后续 Paladin Channel 选项共用 Paladin Channel，不能各建免费池。
4. 所有分配数字、目标和状态由服务端校验，治疗骰由服务端 RNG 生成；事件可重放，不能由客户端提交治疗结果。
5. 不实现其他 Cleric/Paladin 特性、复活、通用疾病或法术重构；只使用官方 PDF。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p37，Cleric Channel Divinity：

> Each time you use this class’s Channel Divinity, choose which Channel Divinity effect from this class to create. You can use this class’s Channel Divinity twice. You regain one of its expended uses when you finish a Short Rest, and you regain all expended uses when you finish a Long Rest.
>
> **Divine Spark.** As a Magic action, you point your Holy Symbol at another creature you can see within 30 feet of yourself ... Roll 1d8 and add your Wisdom modifier. You either restore Hit Points to the creature equal to that total or force the creature to make a Constitution saving throw. On a failed save, the creature takes Necrotic or Radiant damage (your choice) equal to that total. On a successful save ... half as much damage.
>
> You roll an additional d8 when you reach Cleric levels 7 (2d8), 13 (3d8), and 18 (4d8).
>
> **Turn Undead.** Each Undead of your choice within 30 feet of you must make a Wisdom saving throw. If the creature fails its save, it has the Frightened and Incapacitated conditions for 1 minute. For that duration, it tries to move as far from you as it can on its turns. This effect ends early on the creature if it takes any damage, if you have the Incapacitated condition, or if you die.

SRD 5.2.1 p40，Life Domain：

> **Disciple of Life.** When a spell you cast with a spell slot restores Hit Points to a creature, that creature regains additional Hit Points on the turn you cast the spell. The additional Hit Points equal 2 plus the spell slot’s level.
>
> **Preserve Life.** As a Magic action ... expend a use of your Channel Divinity ... restore a number of Hit Points equal to five times your Cleric level. Choose Bloodied creatures within 30 feet of yourself (which can include you), and divide those Hit Points among them. This feature can restore a creature to no more than half its Hit Point maximum.
>
> **Blessed Healer.** Immediately after you cast a spell with a spell slot that restores Hit Points to one or more creatures other than yourself, you regain Hit Points equal to 2 plus the spell slot’s level.
>
> **Supreme Healing.** When you would normally roll one or more dice to restore Hit Points to a creature with a spell or Channel Divinity, don’t roll those dice for the healing; instead use the highest number possible for each die.

SRD 5.2.1 p53-55，Paladin：

> **Lay On Hands.** You have a pool ... equal to five times your Paladin level. As a Bonus Action, you can touch a creature (which could be yourself) ... restore a number of Hit Points ... up to the maximum amount remaining in the pool. You can also expend 5 Hit Points from the pool ... to remove the Poisoned condition ... those points don’t also restore Hit Points.
>
> **Divine Sense.** As a Bonus Action ... For the next 10 minutes or until you have the Incapacitated condition, you know the location of any [Celestial, Fiend, or Undead] within 60 feet ... and you know its creature type. Within the same radius, you also detect ... any place or object that has been consecrated or desecrated.
>
> **Restoring Touch.** When you use Lay On Hands ... remove one or more ... Blinded, Charmed, Deafened, Frightened, Paralyzed, or Stunned. You must expend 5 Hit Points ... for each ... those points don’t also restore Hit Points.

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| Cleric/Paladin 的 `channel_divinity` 与 Paladin `lay_on_hands` 池和休息策略已声明 | `content/rest_catalogs/srd_recovery.json`；`content/progression_catalogs/srd_classes.json` |
| 六个目标特性在 progression/subclass 表有授予 id，canonical 定义尚不存在 | `content/progression_catalogs/srd_classes.json`；`content/subclass_catalogs/srd_subclasses.json`；class feature catalog |
| 法术治疗读取 slot level、目标与公式，并已有 Supreme Healing 最大化入口 | `combat/resolution.py::_spell_events` |
| 治疗通过 `resource.changed(hp)`，条件通过 `condition.applied/removed`，HP 由 reducer clamp | `combat/reducer.py::_reduce_event`、`_apply_hp_change` |
| class capability 能原子支付动作与资源，但既有 heal 只支持自我固定公式 | `features/models.py`；`combat/resolution.py::_class_capability_events` |
| 法术槽扣除记录真实 `slot_level`，免费施法另有资源路径 | `combat/reducer.py::_reduce_event`；`combat/triggered_spells.py::_spell_cost_events` |
| 探索模式只支持有限 healing/buff，不能假装已有 Channel 或圣化侦测 | `exploration/engine.py::resolve_intent`、`_SUPPORTED_EFFECT_MODES` |
| 抗性、豁免、专注与击败由统一战斗结算处理 | `combat/resolution.py::_resist_damage`、`_spell_events`、`_concentration_events` |
| combat feed 是白名单，私有侦测位置不能广播 | `runtime.py::_recent_combat_events` |

## 4. 精确行为与数据契约

### 4.1 Channel 共享资源与通用审计

1. 六个特性定义使用真实职业/子职 owner、等级与官方 source_ref；Cleric Channel 2 级、Paladin Channel 3 级，资源 id 各自为现有 `channel_divinity`，短休+1、长休全恢复。
2. 每个 Channel 能力批次恰好产生一次 `dnd2024.class_resource.spent(resource_id="channel_divinity", amount=1)` 和对应动作成本；多目标不重复扣费，失败校验零事件。
3. 事件均含 `feature_id`、`channel_effect`、`actor_id`、真实目标及 `spell_save_dc`（如需豁免）；效果 id 为 `divine_spark|turn_undead|preserve_life|divine_sense`，客户端不能注入未授予选项。
4. Channel 不是法术施放：不写 `dnd2024.spell.cast`、不消耗法术槽、不触发 Disciple/Blessed Healer 的“spell with a spell slot”条件；Supreme Healing 只按明确的治疗骰规则介入。

### 4.2 Divine Spark、Turn Undead 与 Divine Sense

1. Divine Spark 目标必须是持有者能看见、30 尺内的**另一**生物；Magic action 选择 `heal` 或 `damage`，伤害模式同时选择 `necrotic|radiant`。
2. d8 数量按 Cleric 2–6/7–12/13–17/18–20 为 1/2/3/4，公式加当前 Wis；治疗模式直接恢复，伤害模式进行 Constitution save，失败全额、成功向下减半。
3. Supreme Healing 仅最大化 Divine Spark 的治疗 d8，不最大化其伤害，也不把 Wis 改成最大值；maximized 事件 rolls 按 D1 契约保留可审计骰面且不重掷。
4. Turn Undead 是 Magic action，对 30 尺内所选真实 Undead 各做 Wisdom save；失败者获得同一来源的 Frightened 与 Incapacitated，持续 1 分钟，并在自己的回合尽量远离施法者。
5. 任一目标在实际受到正数伤害时立即移除其这两个 Turn 状态；施法者失能或死亡时移除全部目标状态。Sear Undead 的 Radiant damage 特例由 F23 处理，本切片不得提前误删。
6. Divine Sense 为 Bonus Action，持续 10 分钟或施法者失能；私有投影列出 60 尺内 Celestial/Fiend/Undead 的真实位置和 creature type，以及同半径圣化/渎神地点或物件。
7. 没有权威位置、视线、creature type 或 consecration/desecration 数据时停止；不能仅扣 Channel 后返回空列表，也不能把 Divine Sense 公开给无权限席位。

### 4.3 Lay On Hands 与 Restoring Touch

1. 池上限沿用 `5 × Paladin level`；每次 Bonus Action 触摸一个 5 尺内目标，可为自己。意图含 `healing_amount` 非负整数和 `remove_conditions` 数组。
2. `healing_amount` 可在 0 到剩余池之间任意选择，一次动作只治疗一个目标；池可分多次使用。支付请求量而非“实际缺口”，超过 max HP 的部分由 clamp 损失，不能自动少扣。
3. 基础可移除 `poisoned`，14 级 Restoring Touch 另允许 `blinded|charmed|deafened|frightened|paralyzed|stunned`；每个实际移除的状态另付 5 点。
4. 总成本为 `healing_amount + 5 × 状态数`，不得超过当前池；同一状态不得重复计费，目标没有该状态时拒绝而非白扣 5 点。所有支付、治疗、移除在同一批次。
5. 用于状态的点数不治疗；0 治疗+合法状态移除是有效 Lay On Hands。死亡目标不能靠本能力复活，0 HP 未死亡目标可正常治疗。
6. 多状态可在同一次使用一并移除，顺序为请求中规范化的 canonical 状态顺序；`cannot_regain_hp` 只阻止治疗部分，不阻止付费移除状态。

### 4.4 Preserve Life 与法术治疗 rider

1. Preserve Life 为 Magic action+1 Channel，形成当次 `5 × Cleric level` 的固定治疗预算，不是可跨动作保存的新资源池，不含任何骰。
2. 目标为 30 尺内、结算时 Bloodied 的任意选择生物，可包括自己；`allocations` 将预算以非负整数分给所选目标。每目标不得超过 `max(0, floor(max_hp/2)-current_hp)`，终值不能超过半血。
3. 分配总和不得超过当次预算；未分配或因半血上限不能使用的部分随动作结束消失。`cannot_regain_hp` 目标只能分配 0，不能绕过阻断。
4. 每个正数分配生成独立 `resource.changed`，含 `feature_id="preserve_life"`、`pool_total`、`allocated_amount`；Supreme Healing 不处理固定预算。
5. Disciple of Life：真实消耗法术槽的法术在实际恢复某生物 HP 后，对同一生物追加 `2+slot_level`，每次施法每目标最多一次；免费施法、Channel、Lay On Hands 不触发。
6. Blessed Healer：真实槽法术在一次施法中实际治疗至少一个非自己目标后，立即治疗自己一次 `2+slot_level`；多目标不重复，纯自疗不触发。
7. rider 也在同一施法批次内，带 `source_spell_ref`、`slot_level`、`rider_target_id`；`cannot_regain_hp` 对 rider 同样有效。若原治疗全被阻断，不虚构“实际治疗”触发。
8. Supreme Healing 先最大化原法术/Channel 的治疗骰，再加固定 rider；rider 本身没有骰，不能再次最大化或升级。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/models.py`
5. `src/rulesets/dnd2024/features/resolver.py`
6. `src/rulesets/dnd2024/combat/resolution.py`
7. `src/rulesets/dnd2024/combat/reducer.py`
8. `src/rulesets/dnd2024/combat/validation.py`
9. `src/rulesets/dnd2024/combat/view.py`
10. `src/rulesets/dnd2024/exploration/engine.py`：仅相同 Channel/Lay On Hands 探索入口与私有侦测
11. `src/rulesets/dnd2024/runtime.py`
12. 新建 `tests/rulesets/test_dnd2024_channel_healing.py`
13. 本任务书：只允许追加执行报告

不改 rest/progression/subclass/spell catalog、通用前端或其他职业文件；F23 的 Sear rider 不在本切片实现。

## 6. 编号测试矩阵

1. 六特性按 Cleric 2、Life 3/6、Paladin 1/3/14 真实授予；低一级、其他子职和职业没有对应入口。
2. Cleric Divine Spark/Turn/Preserve 共享同一 Channel，连续两次后第三次失败；短休只回 1，长休回满。
3. Paladin Divine Sense 与后续 Channel 选项共享 Paladin 池，不生成 Cleric 池或额外免费次数。
4. Divine Spark 在 2/7/13/18 级分别使用 1/2/3/4d8+Wis；另一生物、30 尺和可见性校验有效。
5. Spark 治疗直接恢复；伤害模式 Con 失败全额、成功半额向下，Necrotic/Radiant 选择与抗性正确。
6. Supreme Healing 最大化 Spark 治疗骰且不消耗 RNG，但不最大化伤害或 Wis；普通 Channel 仍只扣一次。
7. Turn Undead 仅所选 Undead 做 Wis save，失败者同时 Frightened+Incapacitated，成功者无状态。
8. Turn 状态令目标回合尽量远离；正数伤害、施法者失能/死亡分别正确结束，非目标不受影响。
9. Divine Sense 为 Bonus Action 且 10 分钟/失能结束，返回具体位置和类型；非三类生物和超 60 尺目标不出现。
10. 圣化/渎神地点物件有真实数据时可侦测，私有投影不广播；缺数据时按停止条件失败而非假成功。
11. Lay On Hands 池在 Paladin 1/5/20 为 5/25/100，短休不恢复、长休恢复。
12. 一次 Bonus Action 可支付任意 1..池余额治疗一个触摸目标；超额、非整数、负数、超 5 尺和死亡目标被拒绝。
13. 请求治疗大于目标缺口时按请求扣池、HP 截顶；0 HP 未死亡目标可恢复，池可跨多次动作分配。
14. 5 点移除 Poisoned 不治疗；0 治疗+解毒有效，目标无 Poisoned 时不扣点。
15. Restoring Touch 同次可移除两种或更多允许状态，每种 5 点且不治疗；重复状态只计一次。
16. 14 级前不能移除 Restoring 列表状态；非列表状态、池不足或目标实际没有状态时整批拒绝。
17. `cannot_regain_hp` 只阻止治疗，不阻止状态移除；所有支付和状态变化 apply/replay 一致。
18. Preserve Life 预算为 5×Cleric level，Magic action+一次 Channel，无骰且不受 Supreme Healing 改写。
19. 多 Bloodied 目标可任意分配并包括自己；每目标终值不超过 floor(max_hp/2)，非 Bloodied/超 30 尺不可选。
20. 半血目标只能分配 0，超额分配、超过预算和负数被拒绝；未使用预算不保留到下次动作。
21. Preserve 每正数目标有独立来源事件，`cannot_regain_hp` 目标不能被分配治疗。
22. Disciple 对真实槽法术的每个实际受治疗目标追加一次 2+slot level；升环使用真实 slot level。
23. 多目标法术对每个目标各加一次 Disciple，同一目标同一施法不重复；免费施法、Channel 和 Lay On Hands 不触发。
24. Blessed Healer 在治疗至少一个其他目标后只治疗自己一次；纯自疗和全被阻断不触发，多目标不叠加。
25. Supreme Healing 先最大化原治疗骰再加固定 rider；rider 不掷骰，事件来源和 RNG 顺序稳定。
26. Cure Wounds、Healing Word、多目标治疗、cannot_regain_hp、死亡/稳定和复活既有测试无回归。
27. 存档加载后 Channel/Lay 池、Turn/Divine Sense 持续与私有权限正确，重复 view/intents 无副作用。
28. 全量 rulesets、ruff、diff 通过；伪造分配、目标、slot level 或治疗骰不能改变权威结果。

## 7. 非目标与停止条件

### 7.1 非目标

- Sear Undead、Sacred Weapon、Abjure Foes、Smite of Protection（F23）。
- 其他领域/誓约、复活、通用疾病治疗、法术目录扩张或前端改版。
- 把 Preserve Life 变成永久点数池，或让固定 rider 被 Supreme Healing 再次最大化。

### 7.2 停止条件

1. D1/D2/F4/F13 接口未合入，或无法区分真实槽法术、免费施法和 Channel。
2. Divine Sense 缺少权威位置/类型/圣化数据，或私有投影必须改白名单外系统。
3. Lay On Hands/ Preserve 的分配无法原子校验和落账，或 HP 半血上限与 reducer 冲突。
4. Turn Undead 的伤害结束钩子无法为 F23 保留明确特例，需双写状态。
5. 核心测试 2、12、19、22–25 只能通过伪造事件、客户端骰或省略子效果通过。
6. 基线或并行分支已有同责实现，存在资源池、rider 或事件重复。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_channel_healing.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_revivify.py tests/rulesets/test_dnd2024_survivor.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/combat src/rulesets/dnd2024/exploration/engine.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_channel_healing.py
git diff --check
git status --short
```

记录实际 Python 路径与测试 1–28 映射，单独保存共享 Channel、任意分配、半血上限、两种 rider 和 Supreme Healing 集成证据。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、事件顺序、资源成本和分配 schema。
2. 展示 Divine Spark 各等级骰、Turn 提前结束、Divine Sense 私有数据和 Restoring 多状态证据。
3. 报告 RNG、原子性、重放/存档、探索/战斗一致性和 feed 权限。
4. 不得把 F23、不存在的圣化数据或第三方规则写成已实现。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Channel 共享资源与三种 Cleric/Paladin 效果：
- Lay On Hands / Restoring Touch：
- Preserve Life 分配与半血上限：
- Disciple / Blessed / Supreme 集成：
- 改动文件对照白名单：
- 测试 1–28 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与权限证据：
- 与契约的偏差及原因：
