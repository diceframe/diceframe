# S0-F35 任务书：Chain、Tome 与相关 Eldritch Invocations

| 项 | 值 |
|---|---|
| 切片编号 | S0-F35（独立新任务） |
| 验收载体 | Pact of the Chain、Investment of the Chain Master、Pact of the Tome、Gift of the Protectors、Gaze of Two Minds、Gift of the Depths |
| 规则基准 | 官方 SRD 5.2.1；Warlock p73–74 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | F32、F27、F31、F4、F5、F12 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与真实性门槛

1. 实现 Chain 特殊魔宠与攻击让渡、Investment 五效果、Tome 法术书/仪式、Protectors 全局长休触发、Gaze 感官共享、Depths 水下能力。
2. F27 必须提供真实 familiar/companion 生命周期与 stat blocks，F31/F4/F5 提供法术书和施法主干，F12 提供探索环境；不得用静态文案代替生物、法术或位置行为。
3. 若特殊形态、同位面、水下或感官系统缺失，对应能力标 `guided` 并停止开放相关自动化，不得宣称完整。
4. 不使用旧 SP、2014 规则或第三方资料；canonical ref、生物/法术/名字身份、事件和 source_ref 不本地化。

## 2. 官方英文关键原文与页码

Pact of the Chain，SRD p74：

> You learn the Find Familiar spell and can cast it as a Magic action without expending a spell slot. When you cast the spell, you choose one of the normal forms for your familiar or one of the following special forms: Imp, Pseudodragon, Quasit, Skeleton, Sphinx of Wonder, Sprite, or Venomous Snake … when you take the Attack action, you can forgo one of your own attacks to allow your familiar to make one attack of its own with its Reaction.

Investment of the Chain Master，SRD p73：

> **Aerial or Aquatic.** The familiar gains either a Fly Speed or a Swim Speed (your choice) of 40 feet.
>
> **Quick Attack.** As a Bonus Action, you can command the familiar to take the Attack action.
>
> **Necrotic or Radiant Damage.** Whenever the familiar deals Bludgeoning, Piercing, or Slashing damage, you can make it deal Necrotic or Radiant damage instead.
>
> **Your Save DC.** If the familiar forces a creature to make a saving throw, it uses your spell save DC.
>
> **Resistance.** When the familiar takes damage, you can take a Reaction to grant it Resistance against that damage.

Pact of the Tome，SRD p74：

> You conjure forth a book in your hand at the end of a Short or Long Rest. … The book disappears if you conjure another book with this feature or if you die.
>
> **Cantrips and Rituals.** When the book appears, choose three cantrips, and choose two level 1 spells that have the Ritual tag. The spells can be from any class’s spell list, and they must be spells you don’t already have prepared. While the book is on your person, you have the chosen spells prepared, and they function as Warlock spells for you.
>
> **Spellcasting Focus.** You can use the book as a Spellcasting Focus.

Gift of the Protectors，SRD p73：

> Prerequisite: Level 9+ Warlock, Pact of the Tome Invocation. … With your permission, a creature can take an action to write its name on that page, which can contain a number of names equal to your Charisma modifier (minimum of one name). When any creature whose name is on the page is reduced to 0 Hit Points but not killed outright, the creature magically drops to 1 Hit Point instead. Once this magic is triggered, no creature can benefit from it until you finish a Long Rest. As a Magic action, you can erase a name on the page by touching it.

Gaze of Two Minds / Gift of the Depths，SRD p73：

> **Gaze of Two Minds** — Prerequisite: Level 5+ Warlock. You can use a Bonus Action to touch a willing creature and perceive through its senses until the end of your next turn. As long as the creature is on the same plane of existence as you, you can take a Bonus Action on subsequent turns to maintain this connection … While perceiving … you benefit from any special senses possessed by that creature, and you can cast spells as if you were in your space or the other creature’s space if the two of you are within 60 feet of each other.

> **Gift of the Depths** — Prerequisite: Level 5+ Warlock. You can breathe underwater, and you gain a Swim Speed equal to your Speed. You can also cast Water Breathing once without expending a spell slot. You regain the ability to cast it in this way again when you finish a Long Rest.

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 基线没有六项 Invocation、Book of Shadows 或名字页状态 | class feature catalog；`combat/view.py::_player_view` |
| companion 有 canonical 角色、active、战斗 actor 与探索 HP 投影 | `combat/view.py::_companion`、`_actor_view`；`exploration/engine.py::_party_targets` |
| Find Familiar、Water Breathing 有 spell 条目但基线无 combat effect | spell catalog；combat core `spell_effects` |
| 基线没有七种特殊 familiar 的授权即可视为已自动化；必须核对 F27 真实 stat blocks | `content/subclass_catalogs` 不作为怪物来源；F27 交付边界 |
| 法术准备/法术书有统一选择与投影 | `spells/selection.py::validate`、`configure`；`combat/view.py::_player_view` |
| 长短休有事件与状态提交，可挂接魔典和长休恢复 | `resting/engine.py::complete_short_rest`、`complete_long_rest` |
| HP 归零/死亡由 reducer 处理，不能只在客户端改成 1 | `combat/reducer.py::_apply_hp_change`；`exploration/engine.py::_apply_hp_change` |
| 基线无 plane/swim_speed 权威运行时字段 | 基线源码定点扫描；F12/F27 必须证明届时支撑 |

## 4. 精确实现契约

### 4.1 Chain 与 Investment

1. Chain 以 `class:warlock`、1 级授予；学习 Find Familiar，并可作为 Magic action 免 Pact 槽施放。正常仪式/其他施法入口不因本特性被删除。
2. 魔宠可选正常形态或 Imp、Pseudodragon、Quasit、Skeleton、Sphinx of Wonder、Sprite、Venomous Snake；每种必须引用 F27 的真实 stat block。缺任一形态时不得用普通 Beast 代替或把该形态标完整。
3. 持有者 Attack action 可放弃自己的一次尚未执行攻击，让 familiar 用 Reaction 做一次攻击；实际减少持有者攻击、消耗 familiar Reaction，familiar 失能/无反应时不可执行。
4. Investment 要求 5 级+Chain，对持有者施放的 Find Familiar 所召唤魔宠生效。选择 Fly 或 Swim Speed 40；Quick Attack 用持有者 Bonus Action 命令 familiar 执行 Attack action，不与 §4.1.3 的反应让渡混为同一动作。
5. 魔宠造成 B/P/S 伤害时可改 Necrotic/Radiant；其强迫豁免使用 Warlock spell save DC；魔宠受伤时持有者可花 Reaction 给其该次伤害 Resistance。各项事件带 familiar 与 invocation 来源。
6. 魔宠消失、死亡、重新召唤或替换 Invocation 后旧加成不残留；动作经济、攻击和伤害均走公共结算。

### 4.2 Tome 与 Gift of the Protectors

1. Tome 以 `class:warlock`、1 级授予；每次 Short/Long Rest 结束 conjure Book of Shadows。再次 conjure 或持有者死亡时旧书消失，书中临时准备法术随失效书撤销。
2. 出现时选择三个任意职业列表 cantrips 和两个任意职业列表、1 环、Ritual spells；五项都不得是角色已经 prepared 的法术。去重、等级、Ritual 和列表归属由服务端目录验证。
3. 书在持有者身上时五项视为 prepared 且作为 Warlock spells，使用 Charisma；不占普通 prepared 数，可作 Spellcasting Focus。书不在身上、消失或死亡时不可使用。
4. Gift 要求 9 级+Tome；书出现时生成名字页。经持有者许可，生物花 Action 写名；容量 `max(1, Cha mod)`，同一生物不可重复占名。持有者可触摸书以 Magic action 擦名。
5. 名字在页上的生物被降至 0 HP 但未 outright killed 时，改为 1 HP；每完成持有者 Long Rest 前全局仅触发一次，触发后任何名字生物都不能再受益。
6. 触发必须位于统一 HP 结算边界，保留伤害/事件来源并避免死亡/昏迷/死亡豁免被错误建立；战斗与探索一致。擦除后该生物不再受保护，长休只重置触发，不清空名字。

### 4.3 Gaze 与 Depths

1. Gaze 要求 5 级；Bonus Action 触摸 willing creature，持续到持有者下一回合结束。之后每回合可再花 Bonus Action 维持，但双方必须同位面；不维持即结束。
2. 持续期间获得目标特殊感官；双方相距 60 尺内时，施法可选择从自己空间或目标空间决定来源/射程。超过 60 尺仍可视感官持续，但不能借位施法。
3. 同位面、willing、触摸、感官和空间来源必须由权威状态证明；缺 plane/sense 支撑时标 `guided`，不得默认允许跨位面维持或免费扩展射程。
4. Depths 要求 5 级；持续授予 underwater breathing 和等于当前 Speed 的 Swim Speed，速度变化时同步推导。
5. Water Breathing 每个 Long Rest 可免 Pact 槽施放一次，保留正常施法时间、目标、组件和 24 小时效果；Short Rest 不恢复。法术未自动化时标 `not_automated`，Depths 不得整体标完整。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；F27 已提供的 monster/companion catalog 的本切片引用；必要 combat/rest catalog 声明；`features/models.py`、`features/resolver.py`、`features/resources.py`；`spells/selection.py` 与 F4/F5/F31 主干；`character/derivation.py`、`character/reconciliation.py`；`progression/advancement.py`；`combat/view.py`、`validation.py`、`resolution.py`、`reaction_spells.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；`resting/engine.py`；新建 `tests/rulesets/test_dnd2024_invocation_pacts.py`；本任务书仅追加执行报告。

不得为通过测试编造七种 stat block、光照/位面/水下系统或法术效果。不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. 六项等级与 Chain/Tome 前置正确；F32 替换依赖闭包、en/zh-CN 和历史重建一致。
2. Chain 学习 Find Familiar 并可 Magic action 免槽；普通施法入口不串权，未拥有 Invocation 无 grant。
3. 七种特殊形态逐一引用真实 stat block；缺形态明确 guided/停止，不能用普通 Beast 冒充。
4. Attack action 放弃一次攻击后 familiar 以 Reaction 攻击一次；持有者少一打、Reaction 消耗和失能限制正确。
5. Investment 的 Fly/Swim 40 二选一真实投影；重新召唤和替换后无残留。
6. Quick Attack 花持有者 Bonus Action 让 familiar 执行 Attack action，与反应让渡分别测试、不共享错误成本。
7. 魔宠 B/P/S 可改 Necrotic/Radiant；其他类型不改，抗性按最终类型。
8. 魔宠豁免 DC 等于 Warlock spell save DC；受伤时持有者 Reaction 授予该次 Resistance，无反应不触发。
9. Tome 在 Short/Long Rest 末出现，重召/死亡使旧书消失；装备/Focus 和准备权限同步撤销。
10. 三戏法+两 1 环 Ritual 可跨职业但不可已 prepared/重复/非法；在身上作为 Cha Warlock 法术，不占普通准备数。
11. Protectors 名字容量在 Cha 负/0/正时至少一/正确上限；写名需许可和 Action，擦名需触摸与 Magic action。
12. 名字生物 0 HP 且未 outright killed 改 1 HP； outright death 不救，战斗/探索/reducer 状态一致。
13. 全局长休仅触发一次：一个名字生物触发后其他名字生物不能再触发；持有者 Long Rest 重置但名字保留。
14. Gaze 触摸 willing、Bonus Action、下一回合末和逐回合维持准确；不同位面不能维持，不维持即结束。
15. Gaze 共享特殊感官，60 尺内可选己方/目标空间施法，超过 60 尺不能借位；缺权威系统时 guided 而非假通过。
16. Depths 水下呼吸与 Swim=当前 Speed 在战斗/探索/速度变化中一致。
17. Depths 免费 Water Breathing 每 Long Rest 一次，Short Rest 不恢复，正常组件/目标/24 小时保留；未自动化则 not_automated 且不完整。
18. 全部 apply/replay、save/load、rest、死亡、历史与 reducer 零 RNG；既有 companion、法术、战斗、探索和装备回归通过。

## 7. 非目标与停止条件

非目标：F32–F34 其他选项、完整怪物库、通用召唤/位面/感官/水下系统、多职业和前端表现。

停止条件：任一前置未合入；特殊形态或 familiar 生命周期不真实；HP 0→1 只能由客户端实现；Tome 准备法术无所有权；plane/sense/swim 缺失却要求完整；需要白名单外文件。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_invocation_pacts.py -q
python -m pytest tests/rulesets/test_dnd2024_invocation_core.py tests/rulesets/test_dnd2024_invocation_casting.py tests/rulesets/test_dnd2024_spellbook_items.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_resting.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_invocation_pacts.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- Chain/Investment 七形态、动作与五效果：
- Tome/Protectors 准备法术、名字和全局长休：
- Gaze/Depths 的 guided/not_automated 边界：
- 测试 1–18 逐项结果、RNG、apply/replay、save/load：
- 白名单、验收输出、偏差与未决事项：
