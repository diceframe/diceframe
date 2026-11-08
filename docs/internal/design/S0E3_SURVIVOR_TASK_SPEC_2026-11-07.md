# S0-E3 任务书：Survivor 的回合开始再生与死亡豁免

| 项 | 值 |
|---|---|
| 切片编号 | S0-E3（独立新任务；不执行、不覆盖旧 S-P7） |
| 唯一验收载体 | Champion 18 级 `survivor` |
| 通用能力 | 首回合及后续回合的 turn-start 被动、d20 优势来源、死亡豁免天然骰阈值 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Survivor 在 p49 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`；必须先执行并合入 S0-A、S0-E1、S0-E2 |
| 前置任务 | S0-A 子职授予与数值聚合；S0-E2 的 saving throw 光环辅助及事件字段 |
| 建议分支 | `feat/dnd2024-s0e3-survivor` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 本任务书是本切片唯一需求来源。不要读取旧 S-P7 或其他施工计划扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. 开工时记录 S0-E2 合入后的实际集成提交，并从该提交新建干净工作树。不要复用已有施工工作树，不要清理无关未跟踪文件。
3. 若 S0-A 或 S0-E2 尚未合入，停止。Survivor 同时依赖真实 Champion 授予、owner-scoped 身份、特性数值查询与 Aura of Protection 的死亡豁免接入。
4. 本切片完整实现 Survivor 的 **Defy Death** 与 **Heroic Rally**，不把两项子效果拆成“只显示文案”或“下批再做”。
5. 旧草案中“战斗开场首行动者不触发 Heroic Rally”是错误裁剪，本任务明确推翻：SRD 的每个自己的回合开始包括战斗第一轮。
6. 不实现 Remarkable Athlete、Heroic Warrior、Additional Fighting Style、Superior Critical，也不实现其他职业的再生、死亡豁免或 capstone。
7. 不使用 `.rulebooks`、open5e、2014 SRD 或第三方译文作为规则依据；第 2 节官方英文原文是本任务规则基准。

## 2. 官方规则与语义锁定

SRD 5.2.1 p49，Champion：

> **Level 18: Survivor**
>
> You attain the pinnacle of resilience in battle, giving you these benefits.
>
> **Defy Death.** You have Advantage on Death Saving Throws. Moreover, when you roll 18–20 on a Death Saving Throw, you gain the benefit of rolling a 20 on it.
>
> **Heroic Rally.** At the start of each of your turns, you regain Hit Points equal to 5 plus your Constitution modifier if you are Bloodied and have at least 1 Hit Point.

另按同一 SRD 的通用定义：Bloodied 指当前 HP 小于或等于最大 HP 的一半；死亡豁免天然 20 恢复 1 HP，天然 1 记两次失败，10 以上记一次成功，9 以下记一次失败。

锁定以下行为：

1. **Heroic Rally** 仅在持有者自己的回合开始触发，包括 `combat.started` 确立的首行动者第一回合和每次 `dnd2024.turn.advanced` 之后的新行动者回合。
2. 触发门槛为 `1 <= hp <= floor(max_hp / 2)`。0 HP、死亡、稳定、满血或高于半血均不触发；max_hp 必须为正。
3. 再生量为 `5 + 当前 Constitution modifier`。结果为 0 或负数时不产生治疗事件、不倒扣 HP、不消耗 RNG；为正时恢复该数值并由 reducer 截到 max_hp。
4. 再生不消耗动作、附赠动作、反应、移动、法术位或职业资源。Incapacitated 本身不取消该被动，只要角色仍获得回合且满足 HP 门槛；dead/stable/0 HP 按规则不触发。
5. `cannot_regain_hp` 有效时保留一条 `delta=0` 的可观察阻断事件，HP 不变；阻断条件若在回合开始已经到期，则按到期后的状态判断。
6. **Defy Death** 只作用于死亡豁免，不影响普通豁免、能力检定、攻击或先攻。多个优势来源仍只掷两个 d20 取高，不叠加成三骰。
7. 18–20 判定使用优势选择后的**天然 d20 面值**，不是加 Aura of Protection 或其他调整值后的 total。加值不能把 17 变成“视同 20”。
8. 分支优先级为：`selected_natural >= death_save_crit_threshold` → 恢复 1 HP 并清零成功/失败；否则天然 1 → 两次失败；否则 `selected_natural + saving_throw_bonus >= 10` → 一次成功；否则一次失败。
9. Survivor 的默认阈值为 18，无该参数的角色为 20。18、19、20 均获得天然 20 的同一好处；不会额外多恢复 HP 或保留原成功/失败计数。
10. Aura of Protection 的豁免加值继续按 S0-E2 生效：它可以令天然 9 成为普通成功，但天然 1 的两次失败和天然 18–20 的专属分支优先。Survivor 优势与 aura bonus 是不同事件分项，不相互替代。
11. 本切片不顺便补上既有死亡豁免的 Bless 缺口；保持 S0-E2 基线的 `bless_bonus=0`，不得在本切片扩张为通用豁免重构。
12. 再生与阈值均不消耗额外 RNG；只有死亡豁免优势把原来的一次 d20 改为两次 d20。

## 3. 已核实的代码事实

| 事实 | 位置 |
|---|---|
| 基线 `features_for()` 尚未按 subclass_ref 授予 Champion 特性；S0-A 是硬前置 | `features/resolver.py` |
| Champion 子职表已在 18 级列出 `survivor`，canonical 特性定义尚不存在 | `content/subclass_catalogs/srd_subclasses.json`、class feature catalog |
| `_start_combat_event()` 当前只返回一个 `dnd2024.combat.started`，已计算首行动者 view 和初始 economy | `combat/resolution.py` |
| `combat.start` 在 engine 中把该单事件 append 到批次；首回合再生需要同步改为事件列表 | `combat/engine.py` 的 combat.start 分支 |
| `_end_turn_events()` 先做前行动者重复豁免，再选下一行动者，最后返回 `dnd2024.turn.advanced` | `combat/resolution.py` |
| dead/stable、归零 enemy/companion 会被跳过；玩家 0 HP 仍进入死亡豁免流程 | 同上及 `_death_save_events` |
| 基线死亡豁免只掷一次 d20，按 20/1/10 分支，随后在未死且未恢复时推进回合 | `combat/resolution.py::_death_save_events` |
| S0-E2 已为死亡豁免加入 `modifier=0`、`aura_bonus`、`aura_sources`、`total`，并提供共享 saving-throw aura 辅助 | S0-E2 契约及其执行结果 |
| 治疗 `resource.changed(hp)`、`cannot_regain_hp` 阻断和 max HP clamp 已有事件/reducer 先例 | `combat/resolution.py`、`combat/reducer.py` |
| `combat.started` 在 reducer 中先建立 canonical combat；随后同批次 HP 事件可正常落账 | `combat/reducer.py` |
| combat feed 对事件字段显式白名单，阈值审计字段需登记 | `runtime.py` |
| 旧 S-P7 的“首回合不触发”与 SRD 不符，不能继承 | 本任务 §2；旧文件仅作反例，不作需求 |

## 4. 实现契约

### 4.1 特性声明

在 canonical Fighter 特性段增加：

```json
{
  "id": "survivor",
  "owner": "subclass:champion",
  "minimum_level": 18,
  "source_ref": "srd-5.2.1:p49:survivor",
  "summary": "You have Advantage on Death Saving Throws and treat 18-20 as a 20; at the start of each of your turns, if Bloodied and at 1+ HP, regain 5 plus your Constitution modifier HP.",
  "parameters": {
    "death_save_advantage": true,
    "death_save_crit_threshold": 18,
    "turn_start_regen_base": 5,
    "turn_start_regen_ability": "con"
  }
}
```

1. 必须由真实 `subclass:champion` 在 Fighter 18 级授予；不是所有 Fighter 或其他职业自动拥有。
2. 英文/中文 locale 同步为 Survivor／百折不挠；summary 如实包含两项子效果。canonical ref 和参数键不本地化。
3. 不修改 subclass/progression/rest/resource catalog，不创建资源池，不增加 capabilities。
4. 不把 Defy Death 和 Heroic Rally 伪造成两个可由客户端单独开关的特性。

### 4.2 参数校验与查询

1. 在既有声明校验边界增加：`death_save_advantage` 只能为 `true`；不需要关闭时不要声明该键。
2. `death_save_crit_threshold` 必须是非 bool 的 `int` 且 `2 <= value <= 20`；本特性为 18。
3. `turn_start_regen_base` 必须是非 bool 的 `int >= 1`；`turn_start_regen_ability` 必须是六属性 canonical id，二者成对出现。
4. 在 Resolver 增加或复用 S0-A 的数值聚合：阈值取所有实际拥有声明中的最小值，缺省 20；优势来源按 `features_for()` 稳定顺序返回定义身份，不因多个来源增加骰数。
5. 回合再生按实际拥有的每个定义逐项求值；同一 `(owner, local_id)` 不重复。不同特性若未来声明同组参数，各自产生独立事件，不擅自取最大值或相加成一个无来源数值。
6. 校验和查询不改 S0-A 的授予算法，不接受客户端提交参数，不改 `content_version`。

### 4.3 统一 turn-start 再生辅助

在 `CombatResolutionMixin` 增加只读事件构造辅助：

```python
def _turn_start_regen_events(
    self, actor: dict[str, Any],
) -> list[dict[str, Any]]:
    ...
```

行为：

1. actor 必须来自当前服务器 actor view；无有效角色特性、非 player/companion 或不满足 Bloodied/HP 门槛时返回空表。
2. 对每个实际拥有且装备/条件资格有效的 regen 定义读取 base 与 ability；`amount = base + ability_modifier(current_score)`。`amount <= 0` 跳过该定义。
3. 每个正数定义追加一条：

```json
{
  "type": "resource.changed",
  "resource": "hp",
  "target_id": "player:<fighter>",
  "delta": 8,
  "amount": 8,
  "healing": true,
  "rolls": [],
  "feature_id": "survivor",
  "owner": "subclass:champion",
  "trigger": "turn_start",
  "base": 5,
  "ability": "con",
  "ability_modifier": 3
}
```

示例数值仅为 base 5、Con modifier +3；测试必须由真实角色计算，不固定假写。

4. `cannot_regain_hp` 有效时同一事件改为 `delta=0`、`amount=0` 并附 `blocked_by: "cannot_regain_hp"`；`base`、属性和来源仍用于审计。
5. 辅助不掷骰、不直接写 HP、不支付经济、不调用 reducer，不出现 `survivor`/`champion`/`fighter` 字面逻辑分支。
6. 若 actor view 中回合开始到期条件与 reducer 的到期顺序不同，事件构造必须使用“到期后有效条件”视图，不能令已过期 `cannot_regain_hp` 多阻断一轮。

### 4.4 首回合与后续回合接入

1. 将 `_start_combat_event()` 的返回改为有序事件列表，首项保持原 `dnd2024.combat.started` 内容不变，随后拼接对首行动者 `first` 调用 `_turn_start_regen_events(first)` 的结果。
2. `combat.start` 的 engine 调用由 `events.append(...)` 改为 `events.extend(...)`。不改变先攻排序、首行动者选择、初始 economy、位置或 RNG 顺序。
3. `_end_turn_events()` 在原 `dnd2024.turn.advanced` 之后拼接 `_turn_start_regen_events(next_view)`；事件顺序为：前行动者重复豁免 → turn.advanced → 新行动者再生。
4. reducer 先应用 combat.started/turn.advanced，再应用治疗，因此无需新 reducer 分支。首回合和后续回合共用同一辅助，禁止复制两套公式。
5. 若胜利/团灭导致没有新回合，不产生再生。dead/stable 和 0 HP 角色不因被跳过而触发；0 HP 玩家走死亡豁免而非 Heroic Rally。
6. 额外回合未来复用同一“新行动者回合开始”边界；本切片不实现 Thief's Reflexes 或额外回合系统。

### 4.5 Defy Death 与 Aura 合成

在 `_death_save_events()` 中：

1. 从实际特性收集 `death_save_advantage` 来源和最小 `death_save_crit_threshold`；调用 S0-E2 的 `_saving_throw_aura_bonus(instance, combat, actor)`。
2. 始终生成第一枚 d20；仅优势来源非空时生成第二枚并取高。`rolls` 按掷骰顺序记录两枚，`natural` 为选中值。
3. `modifier=0`、`bless_bonus=0` 保持 S0-E2 契约；`aura_bonus` 为共享辅助结果，`total = natural + aura_bonus`。`aura_sources` 原样透传。
4. 分支严格按 §2.8；`dnd2024.death_save.resolved.roll` 继续记录天然选中值，不改为 total。既有 successes/failures/stable/dead/hp 字段含义不变。
5. `check.resolved(kind="death_save")` 增加或保留：`advantage_mode`、`advantage_sources`、`death_save_crit_threshold`、`aura_bonus`、`aura_sources`、`modifier`、`bless_bonus`、`total`。
6. 优势来源项至少含 `code=feature_id`、`owner`、`mode="advantage"`；多个来源只用于 provenance。无优势时不输出虚假第二骰或 advantage mode。
7. 天然 18–20 恢复后仍按既有逻辑不调用 `_end_turn_events`；未恢复且未死亡时保留既有自动推进。不得在恢复后再触发一次 Heroic Rally，因为当前死亡豁免结算不是新回合开始。
8. `_save_repeat_events`、普通法术豁免、Medicine、攻击和 initiative 不读取这些 Survivor 参数。

### 4.6 reducer、存档与可观察性

1. 复用既有 `resource.changed(hp)` 与 `dnd2024.death_save.resolved` reducer；不新增事件类型、canonical 状态字段或 reducer 特性依赖。
2. 在 combat feed 字段白名单加入 `death_save_crit_threshold`；regen 的 `feature_id`、`owner`、`trigger`、`base`、`ability`、`ability_modifier` 若 feed 当前不显示，可只保留在权威事件账本，不得为显示而扩大敏感角色数据投影。
3. 存档/加载后再次回合开始按当前特性、HP、Con 和条件重新计算；不把 amount 缓存到角色 sheet。
4. 重放只机械应用已生成事件，首回合与后续回合结果一致；不得重掷死亡豁免或再生。

## 5. 改动白名单

只允许修改以下文件；需要任何其他文件时停止并报告：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resolver.py`：仅 §4.2 校验与查询
5. `src/rulesets/dnd2024/combat/resolution.py`
6. `src/rulesets/dnd2024/combat/engine.py`：仅 combat.start 单事件改事件列表的接入
7. `src/rulesets/dnd2024/runtime.py`：仅 death-save feed 字段白名单
8. 新建 `tests/rulesets/test_dnd2024_survivor.py`
9. 本任务书：只允许追加第 10 节执行报告

明确不改 reducer、view、check_modes、reaction_spells、triggered_spells、validation、features/models、前端及 progression/subclass/combat/spell/resource/rest catalog。若 S0-E2 未提供契约中的 aura 接口，停止，不在本任务补做 S0-E2。

## 6. 测试矩阵

角色必须通过真实升级链到 Champion 17/18 级并实际选择 Champion；战斗行为通过真实 `resolve_intent -> apply_batch` 链。允许用 SequenceRng 和 fixture 设置 HP、位置、条件，但不得伪造 Survivor 特性或手写预期治疗事件代替生产结算。

### 6.1 授予、声明与校验

1. Champion 18 级拥有 `(subclass:champion, survivor)`；17 级没有；非 Champion 或非 Fighter 没有。
2. builder 直建与逐级 advance 到 18 级得到相同特性；中英文标签和 p49 source_ref 正确。
3. `death_save_advantage` 为 false、1、字符串时 fail closed。
4. 阈值为 bool、字符串、1、21 时 fail closed；18 合法。
5. regen base 为 bool、字符串、0、负数，ability 非法或二者缺一时 fail closed。
6. 多个优势来源查询只返回稳定来源列表，不复制同一 owner-scoped 身份；阈值聚合取最小值。

### 6.2 Heroic Rally 门槛与数值

7. 18 级 Champion 作为首行动者开始战斗，HP 恰为 `max_hp // 2` 且至少 1：`combat.started` 后立即产生一次再生，恢复 `5 + Con mod`。
8. 首行动者不是 Champion：开场不产生其再生；轮到 Champion 时在对应 `turn.advanced` 后触发一次，不错过第一轮的自身回合。
9. HP 为 `max_hp // 2 + 1`、满血、0、dead、stable 时不触发；max_hp 非正时 fail closed 为空。
10. Con modifier 为 -3 时恢复 2；使结果为 0 或负数的 fixture 不产生事件、不扣 HP、不消耗 RNG。
11. 治疗超过 max HP 时 reducer clamp；再生前 Bloodied 判定不被 clamp 后的终值反向改变。
12. `cannot_regain_hp` 持续有效时事件 delta/amount 为 0、来源和 blocked_by 正确；HP 不变。
13. 回合开始到期的 `cannot_regain_hp` 不多阻断一轮；事件使用到期后状态。
14. 每回合只触发一次；同一回合重复 available_intents、feed 投影或存档读取不生成额外再生。
15. 17 级 Champion、其他职业和无特性 companion 在相同 HP 状态下均不触发。
16. 胜利/团灭没有新回合时不产生再生；他人回合开始不触发 Champion 的再生。
17. 事件不含骰、rolls 为空、RNG 零消耗，不支付任何经济或资源；owner、base、ability 审计完整。

### 6.3 回合时序与持久化

18. 前行动者有 save_repeat 时，事件顺序为 repeat save → turn.advanced → Champion regen；不提前到前行动者回合末。
19. `combat.started` 保持首项且字段不变，regen 在其后；initiative、初始 economy 和 RNG 顺序与基线一致。
20. 首回合及后续回合 apply 后重放得到相同 HP、conditions、turn、economy；存档加载后继续回合不重复上一回合再生。
21. 从死亡豁免天然 18 恢复 1 HP 后不在同一结算中额外触发 Heroic Rally；下一次自己的回合开始再按 Bloodied 判断。

### 6.4 Defy Death

22. 18 级 Champion 死亡豁免掷两骰取高，advantage_sources 含 survivor 及正确 owner；17 级仍单骰。
23. 多个优势来源仍只掷两骰；rolls 顺序与 SequenceRng 一致，不消耗第三骰。
24. 选择天然 18、19、20 均恢复 1 HP，成功/失败清零，death_save.resolved.roll 保留天然值，threshold=18 可观察。
25. 选择天然 17 时不触发“视同 20”；按 total 与 10 比较记普通成功或失败。
26. 双骰 [1,18] 取 18 后恢复 1 HP；[1,15] 取 15 后普通成功；[1,1] 仍两次失败。
27. 无 aura 时天然 9 为失败；有效 aura +1 时天然 9 为普通成功，但不是“视同 20”。
28. aura bonus 不能把天然 17 提升为阈值分支；天然 1 即使 total >=10 仍两次失败；天然 20 始终专属恢复。
29. Survivor 优势、aura bonus、aura sources、threshold 分项共存；total 只含天然骰与 aura，不包含虚构 Con/Cha 调整值。
30. 天然 18–20 恢复后按既有路径不自动推进；普通稳定/死亡/未醒路径的计数、stable/dead 和自动推进不回归。
31. Defy Death 不影响普通 saving throw、save_repeat、Medicine、initiative、攻击或 Counterspell。

### 6.5 回归

32. 含 S0-E2 Paladin 光环的 Survivor 死亡豁免在 apply/replay、save/load 后结果一致，feed 字段完整。
33. 既有 combat start、turn advance、Rage、Hold Person、Death Save、Paladin Aura、Improved Critical、Dark One's Blessing 测试通过。

## 7. 非目标与停止条件

### 7.1 非目标

- Champion 其他特性、Thief's Reflexes 额外回合、其他职业 capstone。
- 通用怪物再生、临时 HP、治疗选择 UI、死亡系统重构。
- 既有 Bless×死亡豁免缺口、优势/劣势全来源通用化、2D 或前端改版。
- 把首回合不触发的旧产品裁剪保留为测试预期。

### 7.2 停止条件

1. S0-A/S0-E2 未合入，或实际接口、owner、事件字段与本契约不一致。
2. combat.start 的事件返回类型已被其他切片重构，或首行动者 view 无法在 RNG 不变的前提下用于再生。
3. 回合开始条件到期发生在 reducer 且无法在事件构造侧得到一致的到期后视图，需要改 reducer 才能修复。
4. 死亡豁免下游要求 `death_save.resolved.roll` 必须是 total，或其现有字段语义与 SRD 分支冲突。
5. 18 级真实 Champion 无法通过 builder/advance 构造，且原因不在本白名单可修范围。
6. 最小测试 7、8、24 无法在不伪造特性/事件或修改白名单外文件的情况下通过。
7. 基线已存在 Survivor、turn-start 通用管线或并行改动，存在双写风险。
8. 需要迁移 content_version、修改 reducer/view 或扩张到其他职业才能完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_survivor.py -q
python -m pytest tests/rulesets/test_dnd2024_paladin_auras.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_dark_ones_blessing.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_rage.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/engine.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_survivor.py
git diff --check
```

使用项目实际 Python 解释器并记录路径。若既有测试文件名已变化，核实后记录替换，不创建空文件。`test_dnd2024_character_builder.py` 的 11 个已知上游失败仅在 HEAD 对照一致时豁免。

## 9. 交付要求

1. 一个提交/PR 只包含本切片，报告实际基线提交与全部白名单变更。
2. 测试 1–33 逐项映射；必须单独展示首回合、后续回合、18 阈值、1/18 双骰和 aura 合成证据。
3. 保存聚焦、回归、全量、ruff、diff 检查结果；任何豁免须有 HEAD 对照。
4. 明确说明首回合事件形状、turn-start 到期条件处理、RNG 变化和 reducer 零改动验证。
5. 不得把 Bless×死亡豁免或其他非目标写成已修复。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 特性授予与声明校验：
- 首回合和后续回合接入：
- turn-start 到期条件处理：
- 死亡豁免优势、阈值与 Aura 合成：
- 改动文件对照白名单：
- 测试 1–33 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG 与 reducer 重放证据：
- 与契约的偏差及原因：
- 未决事项（不得把非目标列为已实现）：

---

## 10. 执行报告（2026-11-08）

- **实际基线**：`5283f77d`（`feat/dnd2024-s0d1-supreme-healing` 链头，stack 在 D3 `fa254b1f` → F9 `25afa0d7` 上；S0-A 与 S0-E2 均已在链内，`_saving_throw_aura_bonus()` 与 `test_dnd2024_paladin_auras.py` 均存在）
- **分支 / 提交**：`feat/dnd2024-s0e3-survivor` / `36b4af29`（实现）+ `c1fef294`（补矩阵证据）+ `9e48c6cd`（回退 `roll` 语义并留注释）
- **改动文件**：`combat/resolution.py`、`combat/engine.py`、`features/resolver.py`、`runtime.py`、`tests/rulesets/test_dnd2024_survivor.py`
- **契约写作时的事实**：Survivor 的声明、`_turn_start_regen_events()` 与 Defy Death 的 18–20 分支在基线已存在。本切片不是从零实现，而是把实现收敛到契约，并推翻旧的"首回合不触发"裁剪。

### 特性授予与声明校验

声明本来就符合 §4.1（`owner: subclass:champion`、`minimum_level: 18`、`source_ref: srd-5.2.1:p49:survivor`、四个参数），中英标签（Survivor / 百折不挠）也已就位，本轮未改内容文件。校验改了两处越界：

1. `death_save_crit_threshold` 原接受 `2..19`，契约要求 `2..20`——20 表示"无特殊分支"，是合法声明。
2. `turn_start_regen_base` 原接受 `0`，契约要求 `>= 1`。"不治疗"必须由 `base + 负调整值` 得到，不能靠声明 0 得到。

### 首回合和后续回合接入

`_start_combat_event()` 的返回从 `dict` 改为 `list[dict]`：第一项仍是原 `dnd2024.combat.started`（字段逐一未变），其后拼接 `_turn_start_regen_events(first)`。`engine.py` 的 `combat.start` 分支由 `events.append(...)` 改为 `events.extend(...)`。先攻排序、首行动者选择、初始 economy、位置与 RNG 顺序均未改。

后续回合不变：`_end_turn_events()` 仍是 `repeat save → turn.advanced → 新行动者再生`。

事件批次的第一项是 `intent.submitted`（审计包装），`dnd2024.combat.started` 排在其后，再生再排在 started 之后；测试按索引关系断言，不依赖绝对位置。

### turn-start 到期条件处理

`_turn_start_regen_events()` 原来直接读 `actor["conditions"]`，而 reducer 的 `_expire_conditions()` 会在同一批次应用 `turn.advanced` 时清掉 `duration ∈ {actor_turn_start, target_turn_start}` 的条件。两处读的是不同时点，导致"本回合开始到期"的 `cannot_regain_hp` 多阻断一轮。

修法在事件构造侧：判断阻断前先剔除这两个 duration 的条件，得到契约 §4.3.6 要求的"到期后有效条件视图"。**未改 reducer**，也未新增状态。

### 死亡豁免优势、阈值与 Aura 合成

- `advantage_sources` 现在每项含 `code` / `owner` / `mode`：新增 `feature_flag_source_rows()` 返回 `{code, owner}`，`_death_save_events()` 拼 `mode`。Lucky 仍只有 `code`，因为它是专长（`content/feats/lucky.json`），没有 class feature owner——与既有攻击掷骰的 provenance 形状一致。
- `check.resolved(kind="death_save")` 的阈值键由 `crit_threshold` 改为 `death_save_crit_threshold`。`crit_threshold` 在同一事件形状里已表示攻击重击范围（`check_windows.py` 的重算路径），同名两义会误导消费方。
- `dnd2024.death_save.resolved.roll` **保留 `total`，未按 §4.5.4 改为天然值**——见"与契约的偏差"第 6 条。核对过 reducer 与前端都不读该字段，所以这是纯粹的契约不一致，不是运行时错误。
- Aura 合成保持不变：`_saving_throw_aura_bonus()` 的 `bonus` 只进 `total`，不进 `natural`；18–20 与天然 1 的分支都按天然面值判定。
- feed 白名单加入 `death_save_crit_threshold`（§4.6.2），其余 regen 审计字段（`feature_id` / `owner` / `trigger` / `base` / `ability` / `ability_modifier`）**没有**加入白名单——它们留在权威事件账本里，不扩大公共投影。

### 改动文件对照白名单

| 白名单项 | 是否改动 | 说明 |
|---|---|---|
| 1–3 内容与 locale | 否 | 声明与标签在基线已符合 §4.1，改动会使本切片变成"重写已正确的数据" |
| 4 `features/resolver.py` | 是 | 两处校验边界 + 新增 `feature_flag_source_rows()`（§4.2） |
| 5 `combat/resolution.py` | 是 | 首回合、regen 逐项与审计字段、到期视图、death-save 三项 |
| 6 `combat/engine.py` | 是 | 仅 `combat.start` 的 append → extend |
| 7 `runtime.py` | 是 | 仅 feed 白名单一个字段 |
| 8 新建 survivor 测试 | 改而非新建 | 该文件在 S-P7 已存在并固化了"首回合不触发"；本切片改写其中 1 条、新增 12 条 |
| 9 本任务书 | 是 | 本节 |

未改 reducer、view、check_modes、reaction_spells、triggered_spells、validation、features/models、前端、progression/subclass/combat/spell/resource/rest catalog。

### 测试 1–33 逐项结果

| # | 结果 | 证据 |
|---|---|---|
| 1 | 通过 | `test_level_eighteen_fighter_owns_survivor`（18 True / 17 False）、`test_ownership_requires_an_explicit_subclass_selection`（无 `subclass_ref` False，写入后 True）、`test_other_classes_never_own_survivor_and_roll_one_die`（rogue / wizard 18 级 False） |
| 2 | 通过 | `test_the_declaration_keeps_its_identity_in_both_locales`（两 locale 的 owner / 等级 / source_ref / 四个参数 + 标签存在）。"builder 直建与逐级 advance 一致"未单独设测：18 级只有 advance 一条真实路径，preset 直建固定 1 级，无法在不造假的前提下对照 |
| 3 | 通过 | `test_catalog_rejects_a_non_true_death_save_advantage`（False / 1 / "true"） |
| 4 | 通过 | `test_catalog_rejects_an_invalid_death_save_threshold`（1 / 21 / "18" / True）+ `test_catalog_accepts_a_threshold_of_twenty`（20 合法） |
| 5 | 通过 | `test_catalog_rejects_an_invalid_regeneration_base`（-1 / 0 / True / "5"）、`..._ability`（"xyz" / 3）、`test_catalog_requires_the_regeneration_pair_together`（缺一） |
| 6 | 通过 | `test_two_declarations_take_the_lowest_threshold_and_keep_both_sources`：两条声明 → 阈值取 17、两枚骰（不是三枚）、两条 provenance 按 catalog 顺序 |
| 7 | 通过 | `test_combat_start_regenerates_for_a_bloodied_first_actor`：HP = `max_hp // 2`，`combat.started` 后立即一条再生 |
| 8 | 通过 | `test_combat_start_does_not_regenerate_for_another_first_actor`：敌人先攻时开场批次只有 started；轮到 Champion 的 `turn.advanced` 后触发一次 |
| 9 | 通过 | `test_regeneration_skips_above_bloodied_and_full_hp`（`max_hp//2 + 1` / 满血）、`test_regeneration_never_ticks_on_a_downed_actor`（0 HP / dead / stable） |
| 10 | 通过 | `test_regeneration_amount_uses_the_constitution_modifier`（Con 4 → mod -3 → 5-3 = 2）、`test_a_non_positive_amount_produces_no_event`（base 1 + mod -3 → -2，无事件、不扣 HP、不掷骰） |
| 11 | 通过 | `test_regeneration_is_clamped_to_max_hp_by_the_reducer`（`amount > 剩余`，apply 后停在 max） |
| 12 | 通过 | `test_cannot_regain_hp_blocks_the_regeneration`（delta / amount 0、`blocked_by`、来源字段仍在） |
| 13 | 通过 | `test_a_condition_that_expires_at_turn_start_does_not_block_twice`（`duration: actor_turn_start` 的 `cannot_regain_hp` 不阻断，apply 后条件已被 reducer 清掉） |
| 14 | 部分 | "每回合一次"由 `test_bloodied_fighter_regenerates_at_turn_start` 与重放测试覆盖；"重复 `available_intents` / feed 投影 / 存档读取不生成额外再生"没有专门断言——该辅助只在 `_end_turn_events()` 与 `_start_combat_event()` 两处被调用，只读路径不产生事件 |
| 15 | 部分 | 17 级 Champion（`test_level_seventeen_fighter_keeps_a_single_die_and_normal_success` 的战斗侧）与其他职业（`test_other_classes...`）已覆盖；"无特性 companion"未单独构造，走的是同一 `active_features_for()` 空集分支 |
| 16 | 部分 | "他人回合开始不触发 Champion 再生"由 `test_combat_start_does_not_regenerate_for_another_first_actor` 覆盖；胜利/团灭分支由 `_end_turn_events()` 的提前 return + `test_dnd2024_combat.py` 回归覆盖，未专门构造 |
| 17 | 通过 | `test_combat_start_regenerates_for_a_bloodied_first_actor`：`rolls == []`、`trigger`/`base`/`ability`/`ability_modifier`/`owner` 全在、economy 的动作与反应计数不变 |
| 18 | 通过 | `test_repeat_save_precedes_turn_advance_and_regeneration`（事件顺序 repeat save → advanced → regen） |
| 19 | 通过 | 同上两条首回合测试：started 字段不变、排在最前、其后才是 regen；回归 295 passed 说明先攻与经济未漂移 |
| 20 | 部分 | `test_a_replayed_start_batch_does_not_regenerate_twice`（重放 `applied is False`、HP 不变）；"存档加载后继续回合不重复上一回合再生"未构造，该行为依赖 canonical 里没有缓存的 amount（事件账本重放即机械应用） |
| 21 | 通过 | `test_a_natural_eighteen_does_not_also_rally`（恢复 1 HP，但同批没有再生事件） |
| 22 | 通过 | `test_death_save_has_advantage_and_provenance`（两骰、`advantage_mode`、带 owner 的来源、阈值 18）；17 级单骰见 #15 |
| 23 | 通过 | `test_two_declarations_take_the_lowest_threshold_and_keep_both_sources`：`len(rolls) == 2`，两来源只用于 provenance |
| 24 | 部分 | `test_threshold_eighteen_to_twenty_treats_as_a_twenty`（18 / 19 / 20 都恢复 1 HP 且计数清零）与 `threshold=18` 的可见性通过。**"`death_save.resolved.roll` 保留天然值"未达成**：见偏差第 6 条 |
| 25 | 通过 | `test_advantage_and_the_aura_are_separate_entries_on_one_save`：天然 17 + aura 1 = total 18，`hp == 0`、一次普通成功 |
| 26 | 通过 | `test_advantage_keeps_natural_one_and_takes_the_highest`（[1,1]、[1,15]）+ `test_advantage_takes_the_eighteen_and_restores_a_hit_point`（[1,18]） |
| 27 | 通过 | 同上 aura 测试的第三段：无 aura 时天然 9 → total 9 → 失败；有 aura +1 → total 10 → 普通成功且 `hp == 0` |
| 28 | 通过 | aura 测试第一段（天然 17 不被提升为阈值分支）；`test_advantage_keeps_natural_one_and_takes_the_highest` 的 [1,1]（天然 1 仍两次失败）；`test_threshold_eighteen_to_twenty_treats_as_a_twenty`（天然 20 始终专属恢复） |
| 29 | 通过 | 同一条 aura 测试断言 `natural` / `aura_bonus` / `aura_sources` / `death_save_crit_threshold` / `total` 分项共存，`total == natural + aura_bonus`（`modifier` 与 `bless_bonus` 保持 0） |
| 30 | 部分 | 18–20 恢复后不自动推进由 `_death_save_outcome_events()` 的 `hp` 早退保证，`test_a_natural_eighteen_does_not_also_rally` 与既有 stable/dead 测试覆盖结果；"未自动推进"本身未单独断言 turn 是否前进 |
| 31 | 通过（回归侧） | `test_dnd2024_paladin_auras.py` / `test_dnd2024_combat.py` / `test_dnd2024_hold_person.py` / `test_dnd2024_rage.py` 全绿；`_death_save_events()` 之外没有任何入口读 Survivor 参数（`_save_repeat_events`、普通豁免、Medicine、initiative 均未改） |
| 32 | 部分 | aura 合成与重放分开覆盖（#22 与 #20），没有一条"aura + 死亡豁免 + apply/replay + save/load"的端到端测试 |
| 33 | 通过 | 回归 295 passed；全量见下节 |

14 条"部分"里有 5 条属于同一类：契约要求的行为由既有代码路径保证（早退、只读、无缓存），但本切片没有为它们单独设测。没有一条是"实现了但行为相反"。

### 聚焦 / 回归 / 全量 / ruff / diff 输出

全量的 11 条失败全部是 `test_dnd2024_character_builder.py` 的 `ability:con`（Windows 保留设备名 `CON`）环境失败，与 D1/D3/F9 同一失败集，本轮零新增。D1 的全量是 1567 passed（11 failed），本轮多出的 10 条是本切片新增的 survivor 测试。

```
python -m pytest tests/rulesets/test_dnd2024_survivor.py -q --basetemp=.ptmp-e3count
46 passed

python -m pytest tests/rulesets/test_dnd2024_paladin_auras.py tests/rulesets/test_dnd2024_class_features.py \
  tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_dark_ones_blessing.py \
  tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_hold_person.py \
  tests/rulesets/test_dnd2024_rage.py -q --basetemp=.ptmp-s0e3r3
295 passed

python -m pytest tests/rulesets -q --basetemp=.ptmp-e3full-02
1577 passed, 11 failed

ruff check（resolver / resolution / engine / runtime / survivor 测试）
All checks passed!

git diff --check
通过（exit 0）
```

`ruff format --check` 在这 5 个文件上**基线就是红的**（stash 对照确认），与 D1/D3 一致，本轮不做全文件重排。

### RNG 与 reducer 重放证据

- 再生不掷骰：首回合与后续回合两条测试都用恰好足够的 `SequenceRng`（先攻用掉即止），再生若多抽一次会抛 `StopIteration`。
- 死亡豁免的骰数只由优势决定：单骰（17 级）与双骰（18 级）各有用例；两个优势来源仍是两枚（#23）。
- 重放：`apply_batch` 第二次接收同一批次返回 `applied is False`，HP 与条件不变。
- reducer 零改动：`git diff` 不含 `reducer.py`；新增字段（`death_save_crit_threshold`、regen 的 provenance）都是审计键，落账仍按 `delta` / `amount` / `hp` / `successes` / `failures`。

### 与契约的偏差及原因

1. **Lucky 的 provenance 没有 owner**（§4.5.6 要求每项含 owner）。Lucky 是专长而非 class feature，没有 class owner；伪造 `feat:lucky` 会引入目录里不存在的新身份。保持与既有攻击掷骰 provenance 相同形状。
2. **`advantage_sources` 的 `owner` 是新字段**（基线只有 `code` / `mode`）。若前端或其他切片依赖严格的项形状，需要同步；本切片未找到消费方。
3. **内容文件（白名单 1–3）未改**，因为声明与标签已符合 §4.1。任务书的"增加声明"是按"尚不存在"写的，事实不成立。
4. **测试文件是改写而非新建**（白名单第 8 项），理由同上：该文件已存在并固化了旧裁剪。
5. **`max_feature_value_source()` 现在没有调用方**。它原本只服务再生；逐项求值后不再需要。留着是为避免删掉可能被其他分支引用的公开方法，但它"取最大 base"的语义与 §4.2.5 相反，后续应删除或改名。
6. **`death_save.resolved.roll` 仍是 `total`，违反 §4.5.4。** 契约明确要求天然选中值，而 `test_dnd2024_check_windows.py::test_dark_ones_own_luck_adds_a_d10_to_a_failed_death_save` 断言 `(successes, failures, roll) == (1, 0, 13)`，其中 13 是 5 + 窗口 d10 的 8。这个测试在 §5 白名单之外，而任务书要求"需要任何其他文件时停止并报告"。已回退到 total，并在调用点留了注释。两种修法：把那一行断言改为 5（符合契约），或维持现状并修订契约 §4.5.4。**需要裁决。**

### 未决事项

1. **`death_save.resolved.roll` 的语义待裁决**（偏差第 6 条）：契约要天然值，既有测试要 total，两者不能同时成立。
2. 上表 14 条"部分"中的 5 条结构性缺口（#14 #15 #16 #20 #30 #32）没有专门测试。若后续要给"存档加载"或"胜利回合"加保障，需要新的夹具。
2. `_turn_start_regen_events()` 的到期过滤只处理 `actor_turn_start` / `target_turn_start` 两种 duration。若将来出现"在回合开始到期的 `cannot_regain_hp`"用别的 duration 表达，这里要同步；目前 catalog 里唯一的 `cannot_regain_hp`（chill touch）是 `caster_next_turn_end`，不受影响。
3. `check.resolved(kind="death_save")` 的阈值键改名后，任何外部消费者若按 `crit_threshold` 读死亡豁免都需要更新。仓库内无此消费者，前端也未读。
4. 非目标未动：Remarkable Athlete、Heroic Warrior、Additional Fighting Style、Superior Critical、其他职业 capstone、Bless×死亡豁免缺口、通用优势/劣势重构、前端。
