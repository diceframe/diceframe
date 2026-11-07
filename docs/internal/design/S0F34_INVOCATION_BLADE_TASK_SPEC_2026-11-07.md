# S0-F34 任务书：Pact of the Blade 与其四个进阶 Invocation

| 项 | 值 |
|---|---|
| 切片编号 | S0-F34（独立新任务） |
| 验收载体 | Pact of the Blade、Thirsting Blade、Devouring Blade、Eldritch Smite、Lifedrinker |
| 规则基准 | 官方 SRD 5.2.1；Warlock p72–74 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | F32、F14、F6 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与完成定义

1. 实现 Blade 本体、武器绑定全生命周期、Cha 攻击/伤害、伤害类型选择、攻击次数、Smite 和 Lifedrinker；不是五个名称或静态 capability。
2. 使用 F32 的选项/依赖闭包、F14 的命中后选择、每回合预算与伤害状态，以及 F6 的 Pact Magic 槽恢复接口；武器身份、熟练和 Attack action 攻击次数直接消费当前基线的权威装备与战斗接口，不把 F18 误作装备前置。
3. 所有武器效果只认当前有效 pact weapon；普通武器、已被他人同调/绑定的武器、失效绑定不得获得能力。
4. 不使用旧 SP、2014 规则或第三方资料；canonical ref、武器身份、事件与 source_ref 不本地化。

## 2. 官方英文关键原文与页码

Pact of the Blade，SRD p74：

> As a Bonus Action, you can conjure a pact weapon in your hand—a Simple or Martial Melee weapon of your choice with which you bond—or create a bond with a magic weapon you touch; you can’t bond with a magic weapon if someone else is attuned to it or another Warlock is bonded with it. Until the bond ends, you have proficiency with the weapon, and you can use it as a Spellcasting Focus.

> Whenever you attack with the bonded weapon, you can use your Charisma modifier for the attack and damage rolls instead of using Strength or Dexterity; and you can cause the weapon to deal Necrotic, Psychic, or Radiant damage or its normal damage type.

> Your bond with the weapon ends if you use this feature’s Bonus Action again, if the weapon is more than 5 feet away from you for 1 minute or more, or if you die. A conjured weapon disappears when the bond ends.

Thirsting Blade / Devouring Blade，SRD p74、p72：

> **Thirsting Blade** — Prerequisite: Level 5+ Warlock, Pact of the Blade Invocation. You gain the Extra Attack feature for your pact weapon only. With that feature, you can attack twice with the weapon instead of once when you take the Attack action on your turn.

> **Devouring Blade** — Prerequisite: Level 12+ Warlock, Thirsting Blade Invocation. The Extra Attack of your Thirsting Blade invocation confers two extra attacks rather than one.

Eldritch Smite，SRD p72–73：

> **Eldritch Smite** — Prerequisite: Level 5+ Warlock, Pact of the Blade Invocation. Once per turn when you hit a creature with your pact weapon, you can expend a Pact Magic spell slot to deal an extra 1d8 Force damage to the target, plus another 1d8 per level of the spell slot, and you can give the target the Prone condition if it is Huge or smaller.

Lifedrinker，SRD p73：

> **Lifedrinker** — Prerequisite: Level 9+ Warlock, Pact of the Blade Invocation. Once per turn when you hit a creature with your pact weapon, you can deal an extra 1d6 Necrotic, Psychic, or Radiant damage (your choice) to the creature, and you can expend one of your Hit Point Dice to roll it and regain a number of Hit Points equal to the roll plus your Constitution modifier (minimum of 1 Hit Point).

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 基线没有五项 Invocation 或 pact weapon 状态 | class feature catalog；`combat/view.py::_player_view` |
| 装备武器 profile 由 canonical equipment refs 和 combat catalog 构造 | `combat/view.py::_available_attacks`、`_declared_attack` |
| 攻击属性、熟练、命中和重击在统一函数中计算 | `combat/resolution.py::_attack_events` |
| 攻击次数已有集中读取，不应为 Blade 复制回合逻辑 | `combat/engine.py::_attacks_per_action`、`_attack_cost_event` |
| 命中伤害、附加骰和最终提交共享入口 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| 每回合特性使用已有战斗记账先例 | `combat/resolution.py::_feature_used_this_turn`；`combat/reducer.py` 的 `feature_usage` |
| Hit Dice 短休消费已有真实数量/Con/治疗行为 | `resting/engine.py::complete_short_rest` |
| Pact Magic 槽位长短休规则由 rest/profile 统一处理 | `progression/catalog.py::snapshot`；`resting/engine.py::_recover_spell_slots` |

## 4. 精确实现契约

### 4.1 绑定创建与全生命周期

1. Pact of the Blade 以 `class:warlock`、1 级授予，无等级前置；Thirsting/Devouring/Smite/Lifedrinker 分别为 5/12/5/9 级并满足官方直接前置。
2. Bonus Action 二选一：conjure 一把合法 Simple/Martial Melee weapon 并绑定；或绑定实际触摸的 magic weapon。客户端只能提交合法物品/武器 ref，不能提交熟练、Cha 加值或伤害公式。
3. 他人已 attuned 或另一 Warlock 已 bonded 的 magic weapon 不可绑定；同一持有者对同一把武器的状态不得重复计份。conjured weapon 必须有服务端唯一身份并进入真实装备投影。
4. 绑定期间授予该武器熟练并允许作 Spellcasting Focus。绑定只覆盖该武器实例，不给同类型所有武器。
5. 再次使用绑定 Bonus Action 时旧绑定结束；武器离持有者超过 5 尺并持续至少 1 分钟、或持有者死亡时结束。距离与时间由权威状态判断，不能由客户端声称。
6. 绑定结束时移除熟练/Focus/Cha/伤害类型与进阶能力；conjured weapon 消失，绑定真实 magic weapon 不消失。死亡、装备变更、save/load、探索与战斗一致。

### 4.2 攻击属性、类型与次数

1. 每次 pact weapon 攻击可选择用 Charisma 替代本来可用的 Strength/Dexterity，同时用于 attack 和 damage modifier；熟练与其他加值保持原公式。不得由客户端直接提交 modifier。
2. 每次攻击可选择 normal damage type 或 Necrotic/Psychic/Radiant；选择进入伤害事件并影响抗性，其他武器不变。
3. Thirsting 在持有者 Attack action 中仅对 pact weapon 提供两次攻击；若中途换用非 pact weapon，不得用其支付剩余 Blade 攻击。
4. Devouring 把 Thirsting 的额外攻击从一改二，即 Attack action 总计三次 pact weapon 攻击；不是再加三次，也不与无关 Extra Attack 相加。统一攻击次数接口必须按本特性的 pact weapon 上限计算，不能累加出第四次。
5. 借机攻击、Bonus Action 攻击和他人回合不因 Thirsting/Devouring 自动增加攻击；反应攻击等仍走各自规则。

### 4.3 Eldritch Smite

1. 仅在 pact weapon 命中后、每回合最多一次，可支付一个真实 Pact Magic spell slot；miss、非 pact weapon、无槽或本回合已用时不可声明。
2. 额外 Force 骰为 **`(实际 Pact slot level + 1)d8`**：1 环 2d8、5 环 6d8。骰与武器/其他附伤分开审计，重击是否翻倍遵循统一攻击规则，不重掷既有骰。
3. 可选择给 Huge 或更小目标 Prone；Gargantuan 不施加。Prone 与伤害均在最终命中提交后落账，反应将命中改为 miss 时不得支付或留效果。
4. 每回合使用、槽位和伤害事件支持 apply/replay；reducer 不掷骰、不重新决定目标体型。

### 4.4 Lifedrinker

1. 每回合 pact weapon 命中一次，可额外造成 1d6 Necrotic/Psychic/Radiant，由持有者在合法窗口选择；不是每把武器或每次命中无限触发。
2. 同次有效触发可选择再消耗一个真实 Hit Point Die，掷该骰并恢复 `roll+Con modifier`，最低 1；没有剩余骰时仍可选择伤害，但不能假治疗。
3. 治疗遵循 max HP、cannot_regain_hp 和公共 HP 事件；骰迹、消耗骰型、Con、实际治疗量可审计。Short Rest 不因为本特性额外恢复 Hit Dice。
4. Smite 与 Lifedrinker 是不同 Invocation；若同次命中均合法，各自每回合限制和选择独立，但事件/RNG 顺序稳定且不能绕过窗口。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；本切片必要 combat catalog 声明；`features/models.py`、`features/resolver.py`、`features/resources.py`；`character/derivation.py`、`character/reconciliation.py`、`character/attunement.py`；`progression/advancement.py`；`combat/view.py`、`validation.py`、`resolution.py`、`check_windows.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；`resting/engine.py`；新建 `tests/rulesets/test_dnd2024_invocation_blade.py`；本任务书仅追加执行报告。

不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎；不得为测试伪造无身份的 pact weapon。

## 6. 逐项编号测试

1. 五项等级/前置授予正确；替换 Pact 或 Thirsting 而保留依赖项时被 F32 闭包拒绝。
2. Bonus Action conjure 合法 Simple/Martial Melee，真实装备/唯一身份/熟练/Focus 生效；非法远程或虚构物品拒绝。
3. 可绑定触摸的 magic weapon；他人 attuned/bonded、未触摸或非 magic 路径按规则拒绝。
4. 再次绑定结束旧绑定；超过 5 尺满 1 分钟和死亡结束；conjured 消失而真实 magic weapon 保留。
5. 绑定后 Cha 可替代 Str/Dex 同时用于攻击与伤害；解绑后无残留，客户端 modifier 无效。
6. 四种伤害类型选择真实进入抗性/事件，仅 pact weapon 生效；save/load 与装备 reconciliation 一致。
7. Thirsting 仅 Attack action 的 pact weapon 总计两次；普通武器和 Bonus/Reaction 攻击不多打。
8. Devouring 12 级总计三次而非四次，且不与无关 Extra Attack 相加；依赖替换后立即撤销。
9. Smite 每回合 pact weapon 命中后可用，miss/普通武器/无槽/重复用拒绝；反应改判 miss 不支付。
10. Smite 1–5 环分别为 2d8–6d8 Force，骰迹、Pact 槽、重击与抗性顺序正确。
11. Smite 可对 Huge 或更小目标 Prone，Gargantuan 不可；条件在最终伤害后落账。
12. Lifedrinker 每回合一次 1d6 三选一伤害；miss、普通武器和重复触发无效。
13. Lifedrinker 可选择消耗真实 Hit Die，恢复 roll+Con（最低 1）；无骰、max HP、治疗阻断和实际骰型正确。
14. Smite+Lifedrinker 同次命中的选择、每回合记账、事件顺序和 RNG 可重复；apply/replay 不重复扣槽/骰/治疗。
15. builder/advance/history、en/zh-CN、战斗/探索/rest、死亡和存档一致；既有攻击、武器、Pact Magic 和装备回归通过。

## 7. 非目标与停止条件

非目标：其他 Invocation、通用召唤武器、多职业、完整物品经济、三维距离和前端表现。

停止条件：前置未合入；没有唯一武器实例身份；无法表示距离持续一分钟或死亡解绑；攻击次数需绕过统一 Attack action/Extra Attack 接口；Smite 被误写成 `level d8`；Hit Die 无真实数量；需要白名单外文件。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_invocation_blade.py -q
python -m pytest tests/rulesets/test_dnd2024_invocation_core.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_resting.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_invocation_blade.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 绑定生命周期、物品身份、Cha/类型与 Focus：
- 攻击次数、Smite 骰/Prone、Lifedrinker/Hit Die：
- 测试 1–15 逐项结果、RNG、apply/replay、save/load：
- 白名单、验收输出、偏差与未决事项：
