# S0-F19 任务书：Hunter 子职完整战斗特性

| 项 | 值 |
|---|---|
| 切片编号 | S0-F19（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | Hunter 的 `hunters_lore`、`hunters_prey`、`defensive_tactics`、`superior_hunters_prey`、`superior_hunters_defense` |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Hunter p61，Hunter's Mark p141；PDF SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | F11 伤害反应窗；F12 豁免/动态抗性；F14 命中 rider；F18 Hunter's Mark 实例；S0-B 选项授予 |
| 建议分支 | `feat/dnd2024-s0f19-hunter` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 开工时记录上述前置任务合入后的实际提交，从该提交新建干净工作树；任何前置接口缺失均停止，不用职业名分支临时补洞。
2. 本切片交付五项特性及其选项替换、私有情报、额外攻击、第二目标伤害和反应抗性；不得只加入 catalog 文案。
3. 规则仅引用本文列出的官方 PDF 页码；不得使用第三方 Hunter 文本、2014 规则或旧计划扩张范围。
4. 不实现 Ranger 其他特性、其他子职、Hunter's Mark 本体或通用反应系统；这些由前置任务提供。
5. 所有目标、范围、已攻击记录、资源、反应和特性选项均由服务端权威状态判定，客户端不能提交情报或伤害结果。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p61，Hunter：

> **Level 3: Hunter’s Lore.** While a creature is marked by your Hunter’s Mark, you know whether that creature has any Immunities, Resistances, or Vulnerabilities, and if the creature has any, you know what they are.
>
> **Hunter’s Prey.** You gain one of the following feature options of your choice. Whenever you finish a Short or Long Rest, you can replace the chosen option with the other one.
>
> **Colossus Slayer.** When you hit a creature with a weapon, the weapon deals an extra 1d8 damage to the target if it’s missing any of its Hit Points. You can deal this extra damage only once per turn.
>
> **Horde Breaker.** Once on each of your turns when you make an attack with a weapon, you can make another attack with the same weapon against a different creature that is within 5 feet of the original target, that is within the weapon’s range, and that you haven’t attacked this turn.
>
> **Defensive Tactics.** Whenever you finish a Short or Long Rest, you can replace the chosen option with the other one. **Escape the Horde.** Opportunity Attacks have Disadvantage against you. **Multiattack Defense.** When a creature hits you with an attack roll, that creature has Disadvantage on all other attack rolls against you this turn.
>
> **Level 11: Superior Hunter’s Prey.** Once per turn when you deal damage to a creature marked by your Hunter’s Mark, you can also deal that spell’s extra damage to a different creature that you can see within 30 feet of the first creature.
>
> **Level 15: Superior Hunter’s Defense.** When you take damage, you can take a Reaction to give yourself Resistance to that damage and any other damage of the same type until the end of the current turn.

SRD 5.2.1 p141，Hunter's Mark：

> Until the spell ends, you deal an extra 1d6 Force damage to the target whenever you hit it with an attack roll.

SRD 5.2.1 p187，Resistance：伤害减半并向下取整，同一次伤害只应用一次抗性。

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 子职表已按 3/7/11/15 级列出五个 Hunter 特性，但 canonical 特性定义尚不存在 | `templates/rulesets/dnd2024_srd/content/subclass_catalogs/srd_subclasses.json`；`content/class_feature_catalogs/srd_class_features.json` |
| `features_for()` 合并职业与子职授予；`feature_value()` 可读取声明参数 | `src/rulesets/dnd2024/features/resolver.py::features_for`、`feature_value` |
| 先攻、命中和伤害均由服务端生成事件，敌人数据不能由 preset 客户端伪造 | `combat/resolution.py::_start_combat_event`；`combat/validation.py::_validate` |
| 武器攻击共用攻击模式和命中结算；Hunter rider 应进入同一公式/提交流程 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| `feature.used` 已按 actor、round、turn_index 记录每回合使用 | `combat/reducer.py::_reduce_event`；`resolution.py::_feature_used_this_turn` |
| 抗性从条件注册表及条件元数据读取，单次只减半一次 | `combat/resolution.py::_resist_damage` |
| F11 前置提供伤害反应窗，暂存并提交真实 `damage_event`，反应共享 `combat.reactions` | `combat/reaction_spells.py::_finish_hit_reaction`、`_reaction_feature_choices`；`combat/resolution.py::_submit_hit_damage` |
| 短休和长休已有统一入口，rest option 由 runtime 显式声明后转发 | `resting/engine.py::complete_short_rest`、`complete_long_rest`；`runtime.py::complete_rest` |
| combat feed 是显式字段白名单，不能把敌人完整防御情报公开给其他席位 | `runtime.py::_recent_combat_events` |

## 4. 精确行为与数据契约

### 4.1 授予、选项与休息替换

1. 五项定义使用真实 Hunter 子职授予等级与 `srd-5.2.1:p61:<feature>`；中英文 locale 同步，canonical id 不本地化。
2. `hunters_prey` 仅允许 `colossus_slayer|horde_breaker`，`defensive_tactics` 仅允许 `escape_the_horde|multiattack_defense`；默认选择只能来自 S0-B 的真实建卡答案，不在运行时擅自替玩家选。
3. 选择存于 S0-B 的 owner-scoped 答案；F12 只提供动态抗性先例，不保存 Hunter 选项。每次完成短休或长休可分别替换当前选项，未提交替换则保持不变。每次休息每组最多替换一次，不接受非法 id、越权 owner 或战斗中替换。
4. 战斗投影、RNG 和事件只读取完成休息后的有效选项；旧选项不得残留为条件或隐藏 rider。

### 4.2 Hunter's Lore 私有情报

1. 仅当当前角色的 Hunter's Mark 与目标存在有效 `source_actor_id`/mark 关联时，向该角色投影目标真实防御情报；标记转移或结束后立即撤回。
2. 情报结构为 `immunities`、`resistances`、`vulnerabilities` 三组具体 canonical 类型列表，并区分 damage 与 condition 条目；三组均可为空，但不得用布尔“有/无”代替具体名称。
3. 数据来自服务端 enemy stat block，不从客户端 payload 或公开名称推断。权威事件可记 `dnd2024.hunter_lore.revealed`，含 `actor_id`、`target_id`、`source_spell_ref` 和上述列表。
4. 该事件及 view 字段仅对标记拥有者和有全知权限的 GM 可见；公共 feed、其他玩家和敌方投影不得泄漏。缺真实防御字段时停止，不把空列表当作“目标没有”。

### 4.3 两种 Prey 与 Defensive Tactics

1. Colossus Slayer：武器命中且目标在本次命中前 `hp < max_hp` 时添加 1d8 武器伤害；满血目标不触发。每回合最多一次，只有伤害最终提交才记 `feature.used`，被反应改为未命中则不记。
2. Horde Breaker：每次自己的回合第一次声明时，从当前攻击的同一 `weapon_ref` 发起额外攻击；第二目标必须不同于原目标、距原目标不超过 5 尺、处于该武器合法射程内，且本回合尚未被持有者攻击。
3. Horde Breaker 不额外支付动作或攻击次数，但仍走完整攻击骰、命中、伤害、反应和 `feature.used`；第二目标不满足任一条件则拒绝而非换最近目标。它不能在他人回合由借机攻击触发。
4. Escape the Horde 只给针对持有者的 Opportunity Attack 添加劣势来源；普通攻击不受影响。Multiattack Defense 在敌人攻击骰命中持有者后施加来源条件，使该敌人本回合后续所有攻击持有者的攻击骰劣势。
5. Multiattack Defense 不追溯改判已命中的第一击；同一攻击者重复命中不叠加骰数。条件以 `(source_actor_id, target_id, round, turn_index)` 限定并在当前回合结束移除。

### 4.4 Superior Prey 与持续反应抗性

1. Superior Hunter's Prey 在持有者每回合首次实际伤害其当前标记目标后开放一个可选第二目标；第二目标须不同、持有者可看见且距第一目标不超过 30 尺。
2. 第二目标仅结算该 Hunter's Mark 实例的额外 Force 伤害，按当前实例独立掷一枚其伤害骰（基础 1d6，Foe Slayer 后为 1d10）；不得复用第一目标总伤害，也不得复制武器骰、属性加值、Colossus Slayer、Sneak Attack 或其他 rider。事件记录独立 `rolls`、`source_damage_event_id` 与 `second_target_id`。
3. 未实际造成伤害、标记不属于持有者、第二目标不可见/超距或本回合已使用时不得结算。两个目标分别应用抗性、HP、专注和击败处理；RNG 顺序为第一目标既有结算后第二目标的一枚标记伤害骰。
4. Superior Hunter's Defense 使用 F11 的**伤害落地前**反应窗。选择后支付一次反应，对触发伤害类型获得抗性，先减半该次待提交伤害，再持续抵抗同类型后续伤害直到当前回合结束。
5. 持续状态含 `damage_type`、`source_actor_id`、`round`、`turn_index`、`feature_id`；抗性不叠加，不跨到持有者下一回合。放弃反应、反应不足或无正数待结算伤害时不支付、不添加状态。
6. 单个伤害事件有多个类型时，F11 必须先拆成单类型包或提供明确类型选项；无法无歧义确定触发类型则停止，不默认选择第一种。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resolver.py`：仅参数校验与声明查询
5. `src/rulesets/dnd2024/combat/resolution.py`
6. `src/rulesets/dnd2024/combat/check_modes.py`
7. `src/rulesets/dnd2024/combat/reaction_spells.py`
8. `src/rulesets/dnd2024/combat/reducer.py`
9. `src/rulesets/dnd2024/combat/validation.py`
10. `src/rulesets/dnd2024/combat/view.py`：仅 Lore 私有投影与合法目标
11. `src/rulesets/dnd2024/runtime.py`：仅私有情报及 feed 防泄漏
12. 新建 `tests/rulesets/test_dnd2024_hunter.py`
13. 本任务书：只允许追加执行报告

需要修改休息通用服务、Hunter's Mark 本体、怪物目录或其他文件时停止；那些属于前置任务边界。

## 6. 编号测试矩阵

1. Hunter 2 级无五项特性；3 级仅有 Lore/Prey，7/11/15 级依次获得后续特性，非 Hunter 不获得。
2. 两组选项非法值、跨 owner 答案和伪造客户端选项均 fail closed；中英文 source_ref 与标签一致。
3. 短休可替换 Prey 而不改 Defensive Tactics；长休可替换后者；不提交替换时选择保持，战斗中请求被拒绝。
4. Lore 对当前标记目标返回三组具体类型而非布尔值，空组有明确空列表；未标记和标记结束后不可见。
5. 标记转移后旧目标情报撤回、新目标情报出现；其他玩家公共 feed 不含列表，GM 可见。
6. Colossus Slayer 对受伤目标添加一次 1d8，对满血目标不添加；伤害类型、rolls 和 feature 来源正确。
7. 同一回合第二次合格命中不再加 Colossus；下一回合恢复，未命中或被改为未命中不消耗使用次数。
8. Horde Breaker 第二攻击沿用同一武器且不另扣动作；完整命中与伤害管线只执行一次。
9. 第二目标与原目标相同、距原目标 6 尺、超出武器射程或本回合已被攻击时均不可选。
10. Horde Breaker 每回合一次，下一回合重置；他人回合的借机攻击不能触发。
11. Escape the Horde 使借机攻击劣势，普通攻击模式与 RNG 不变；多个劣势来源不增加骰数。
12. Multiattack Defense 第一击不追溯，第二击及其后攻击劣势；其他攻击者或下一回合不受影响。
13. Superior Prey 对 30 尺内可见第二目标独立结算当前标记的一枚额外 Force 骰（1d6 或 Foe Slayer 1d10），事件不含武器或其他 rider。
14. 第二目标 31 尺、不可见、同一目标、无有效标记或第一目标未受伤时均不结算。
15. Superior Prey 每回合一次，两个目标分别计算抗性、HP、专注和击败；第二目标只新增一枚标记伤害骰，RNG 与重放顺序稳定。
16. Superior Defense 支付反应后触发伤害向下减半一次，后续同类型伤害也减半。
17. 后续不同类型伤害不减免；当前回合结束后同类型恢复全额，下一回合可重新反应。
18. 反应不足、放弃窗口或无正数伤害时不扣反应、不写持续状态；多来源抗性不叠加成四分之一。
19. 多类型伤害未拆分且无明确选择时 fail closed，不默认取第一种类型。
20. 低等级角色、非 Hunter 和已替换掉的旧选项不能产生任何对应 rider 或反应窗。
21. 所有攻击/反应批次经 `resolve_intent -> apply_batch` 后重放得到相同事件、HP、条件、RNG 和 feature usage。
22. 存档加载后 Lore 权限、当前选项、每回合使用及持续抗性保持；重复 available_intents 不产生副作用。
23. 客户端伪造第二目标、防御情报、额外骰或反应类型均被服务端拒绝或忽略。
24. 既有 Hunter's Mark、Sneak Attack、Uncanny Dodge 和 Divine Smite 交互测试无回归。
25. 公共 combat feed 不出现 Lore 列表；权威账本仍保留足够审计来源，save/load 后权限不扩张。
26. 既有 combat、class features、resting、rage、monk 与 F11 伤害反应测试通过。

## 7. 非目标与停止条件

### 7.1 非目标

- Hunter's Mark 施法/升环/转移本体、Favored Enemy、Precise Hunter 和 Foe Slayer。
- 通用怪物图鉴 UI、2D 视野重构、全类型易伤伤害系统或其他子职。
- 把 Lore 公开给全队，或把 Superior Prey 实现为复制整次武器伤害。

### 7.2 停止条件

1. F11/F12/F14/F18/S0-B 未合入，或标记来源、武器身份、选项 owner、伤害窗不能满足本文契约；F18 不提供伤害反应窗。
2. 敌人权威 stat block 没有真实 Immunities/Resistances/Vulnerabilities，或无法做席位级私有投影。
3. 额外攻击会绕过动作经济、重复消费 RNG、无法记录本回合已攻击目标，或需要重写通用攻击调度。
4. 反应无法在触发 HP 落账前减半，或多类型伤害语义仍不唯一。
5. 需要修改白名单外文件、伪造目标数据、只写文案或省略任一子效果才能交付。
6. 基线或并行分支已存在 Hunter 实现，存在双写或事件语义冲突。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_hunter.py -q
python -m pytest tests/rulesets/test_dnd2024_damage_reactions.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_resting.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_hunter.py
git diff --check
git status --short
```

记录实际 Python 路径、逐项测试映射、聚焦/回归/全量输出；已知上游失败只有在同一基线对照完全相同时才可豁免。

## 9. 交付要求

1. 单个提交/PR 只含本切片及白名单文件，报告真实集成基线和所有改动。
2. 单独展示 Lore 私有性、两种 Prey、两种 Defense、30 尺第二目标和触发伤害先减免的证据。
3. 报告 RNG 顺序、反应预算、每回合记账、重放/存档及公开 feed 防泄漏结果。
4. 不得把非目标或 guided 情报描述写成已自动化。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 特性授予与休息选项：
- Lore 数据及权限：
- 攻击、第二目标与反应抗性：
- 改动文件对照白名单：
- 测试 1–26 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与 feed 证据：
- 与契约的偏差及原因：
- 未决事项（不得把非目标列为已实现）：
