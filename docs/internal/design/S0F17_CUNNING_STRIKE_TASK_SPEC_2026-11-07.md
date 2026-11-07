# S0-F17 任务书：Cunning Strike 的 Sneak Attack 骰池预算

| 项 | 值 |
|---|---|
| 切片编号 | S0-F17（独立新任务，不覆盖旧 SP） |
| 验收载体 | `cunning_strike`、`improved_cunning_strike`、`devious_strikes`、Thief `supreme_sneak`、`elusive` |
| 通用能力 | Sneak d6 预算、伤害后效果、来源化 Hide 保留、攻击 Advantage 否决 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-F10、S0-F14、S0-D2 已合入；真实 Hide、rider 决策和条件重复豁免可用 |
| 建议分支 | `feat/dnd2024-s0f17-cunning-strike` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从前置实际集成提交开工；复用真实 Sneak Attack 资格、Hide 来源和命中 rider 链。
2. 五项特性及全部选项完整实现，不能只支付骰而不执行条件/移动，也不能先掷伤害再倒扣成本。
3. 骰池、DC、目标尺寸/工具、条件和攻击模式由服务端计算；客户端只能声明合法效果组合。
4. Three-Quarters/Total Cover、Large 尺寸或 Poisoner’s Kit 缺少权威事实时停止，不按距离或名称猜测。

## 2. 官方英文关键原文与页码

SRD p63，Rogue 5 级：

> **Cunning Strike.** When you deal Sneak Attack damage, you can add one of the following Cunning Strike effects. Each effect has a die cost, which is the number of Sneak Attack damage dice you must forgo to add the effect. You remove the die before rolling, and the effect occurs immediately after the attack’s damage is dealt.

> If a Cunning Strike effect requires a saving throw, the DC equals 8 plus your Dexterity modifier and Proficiency Bonus.

> **Poison (Cost: 1d6).** The target must make a Constitution saving throw. On a failed save, the target has the Poisoned condition for 1 minute. At the end of each of its turns, the Poisoned target repeats the save, ending the effect on itself on a success. To use this effect, you must have a Poisoner’s Kit on your person.

> **Trip (Cost: 1d6).** If the target is Large or smaller, it must succeed on a Dexterity saving throw or have the Prone condition.

> **Withdraw (Cost: 1d6).** Immediately after the attack, you move up to half your Speed without provoking Opportunity Attacks.

SRD p63，11/14/18 级：

> **Improved Cunning Strike.** You can use up to two Cunning Strike effects when you deal Sneak Attack damage, paying the die cost for each effect.

> **Daze (Cost: 2d6).** The target must succeed on a Constitution saving throw, or on its next turn, it can do only one of the following: move or take an action or a Bonus Action.

> **Knock Out (Cost: 6d6).** The target must succeed on a Constitution saving throw, or it has the Unconscious condition for 1 minute or until it takes any damage. The Unconscious target repeats the save at the end of each of its turns, ending the effect on itself on a success.

> **Obscure (Cost: 3d6).** The target must succeed on a Dexterity saving throw, or it has the Blinded condition until the end of its next turn.

> **Elusive.** No attack roll can have Advantage against you unless you have the Incapacitated condition.

SRD p64，Thief 9 级：

> **Stealth Attack (Cost: 1d6).** If you have the Hide action’s Invisible condition, this attack doesn’t end that condition on you if you end the turn behind Three-Quarters Cover or Total Cover.

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| Sneak Attack 已有每回合资格、等级骰轨和伤害结算 | `combat/resolution.py::_sneak_attack_dice`、`_attack_events` |
| Sneak catalog 声明 `sneak_attack_dice` 与 `{die}d6`，不是本批效果预算 | class feature catalog 的 `sneak_attack` |
| 命中 rider 的可选决策来自 S0-F14，必须在伤害骰前锁定成本 | 前置执行结果、`_attack_events` |
| 条件与重复豁免已有事件入口，但非法术来源须显式支持 | `combat/resolution.py::_save_repeat_events`、`_end_turn_events` |
| 攻击加减益由共享来源聚合，适合实现 Elusive 而非另写命中规则 | `combat/check_modes.py::attack_sources`、`attack_mode` |
| 移动、位置与回合推进均有权威事件入口 | `combat/resolution.py::_movement_events`、`_end_turn_events` |
| Hide 必须有独立来源和 check_total；普通 Invisible 不能替代 | S0-F10 契约与 `combat/resolution.py::_spell_end_trigger_events` |
| 当前 actor view 未核实尺寸、随身 inventory 与 Cover 几何 | `combat/view.py::_actor_view`、`_player_view` |
| 条件/资源/伤害由 reducer 应用，不能由客户端直接写 | `combat/reducer.py::_reduce_event`、`_apply_hp_change` |

## 4. 精确实现契约

### 4.1 骰池预算与通用结算

1. 增加五项 canonical declaration、owner/等级/source_ref/双语 locale；Stealth Attack 仅真实 Thief 9 级加入选项。
2. 仅当本次攻击已满足原 Sneak Attack 全部资格且尚未掷 Sneak 伤害时开放效果选择；普通命中、无 Sneak 或已掷伤害后不能追加。
3. 当前骰池来自真实 Rogue 等级轨道。5 级最多一个效果，11 级起最多两个；每个效果分别支付成本，总成本不得超过当前骰池。
4. 在掷骰前从 Sneak 公式扣除所选 d6，再掷剩余骰；允许剩余 0 骰时基础武器伤害仍正常结算，不能先掷完整骰再减少已得伤害。
5. 所有 save DC 固定为 `8 + 当前 Dexterity modifier + Proficiency Bonus`，不用 spell save DC；效果按玩家选择顺序在本次攻击伤害全部落账后立即发生。
6. 未选择效果保留完整 Sneak 骰；miss 不支付骰、不触发效果；pending resolve 重验攻击、目标、工具和状态，重复 resolve 不重复扣骰/伤害。

### 4.2 七类效果

1. **Poison，1d6**：必须实际随身携带 Poisoner’s Kit（不要求虚构熟练）；Con 失败 Poisoned 1 分钟，每个目标自己回合末重复 Con，成功结束该来源。
2. **Trip，1d6**：仅 Large 或更小目标；Dex 失败 Prone，成功无效果。Huge 或尺寸未知不可选。
3. **Withdraw，1d6**：无豁免；伤害后立即移动至多一半当前 Speed，且该移动不引发 OA；Speed 0 时只能选 0 距离，不产生额外移动。
4. **Daze，2d6**：Con 失败则目标下一回合只能在 move、action、Bonus Action 三类中实际使用一类；Reaction 不在禁止列表，选择移动后不能再行动/附赠，反之亦然。
5. **Knock Out，6d6**：Con 失败则该来源 Unconscious 1 分钟或至受到任何伤害；每个目标回合末重复 Con 成功结束。受伤只移除该来源，不误删其他 Unconscious 来源。
6. **Obscure，3d6**：Dex 失败 Blinded 至目标下回合结束；共享 Blinded 的攻击/可见性效果必须真实生效。
7. **Stealth Attack，1d6**：仅当攻击者具有 `source_action=hide` 的 Invisible 才可选；普通法术/特性 Invisible 不符合。攻击后的 Hide 终止进入本效果边界，回合结束仅在 Three-Quarters 或 Total Cover 后保留，否则结束 hidden 与该来源 Invisible。

### 4.3 Elusive 与状态边界

1. 持有者未 Incapacitated 时，从所有“针对持有者的 attack roll”来源中移除 Advantage，包括 Reckless、Invisible、Prone、GM grant 等；不是只删除条件来源。
2. 移除 Advantage 不删除 Disadvantage；原 Advantage+Disadvantage 相抵的攻击在 Elusive 下应只剩 Disadvantage。持有者自己的攻击不受影响。
3. 持有者 Incapacitated 时 Elusive 暂停，正常来源重新计算；不缓存过滤后的 mode。
4. 条件到期、受伤终止、Daze 行动类别和 Supreme Sneak 的回合末检查均进入 canonical state，可在 save/load 与 replay 后得到同一结果。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/resolution.py`、`validation.py`、`check_modes.py`、`reducer.py`、`engine.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`：仅本批特性/参数查询。
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要效果与条件声明。
5. 新建 `tests/rulesets/test_dnd2024_cunning_strike.py`；本任务书仅追加执行报告。
6. 需要新增物品/尺寸/掩蔽系统或修改其他文件时停止，不伪造事实。

## 6. 编号测试矩阵

使用真实 Rogue/Thief、Sneak 资格、Hide 与完整攻击链；固定骰序，禁止手写骰池、工具、尺寸、Cover 或效果结果。

1. 五项特性在正确等级/子职授予，14 级新选项与 Thief 9 级选项边界准确。
2. 只有合法 Sneak Attack 命中开放选择；无 Sneak、miss、已掷伤害或同回合第二次 Sneak 不可选。
3. Poison/Trip/Withdraw 各扣 1d6，Daze/Obscure/Knock Out 分别扣 2/3/6d6；超预算拒绝且状态不变。
4. 成本在掷骰前扣除，剩余骰与事件 rolls 精确；剩余 0 时基础攻击伤害仍结算且不产生虚假 Sneak 骰。
5. 5 级只能一个效果，11 级最多两个且分别付骰；第三个效果或重复 resolve 拒绝。
6. 所有 save DC 为 8+Dex mod+PB，使用当前角色数值，不用 spell DC 或客户端 DC。
7. Poison 无 Poisoner’s Kit 不可选；有工具时 Con 失败 Poisoned 1 分钟，回合末重复 save 成功结束。
8. Trip 仅 Large 或更小，Dex 失败 Prone、成功无效果；Huge/未知尺寸 fail closed。
9. Withdraw 在伤害后立即半速移动且不引发 OA，无豁免，不超距、不突破 Speed 0。
10. 多效果在伤害后按选择顺序分别结算，互不免费；任一非法组合在掷骰前整体拒绝。
11. Daze 目标下一回合只能实际使用 move/action/Bonus Action 之一，跨类第二种被验证拒绝，Reaction 不被误禁。
12. Daze 只持续其下一回合，之后恢复正常；效果不是永久条件或当前回合立即限动。
13. Knock Out 失败 Unconscious，1 分钟、回合末重复 save 与受伤终止均准确；其他来源条件不误删。
14. Obscure 失败 Blinded 至目标下回合结束，攻击/可见性效果生效并准时到期。
15. Stealth Attack 仅 Hide 来源 Invisible 可选，普通 Invisibility/其他特性来源拒绝。
16. 使用该攻击后，回合结束在 Three-Quarters/Total Cover 后保留 hidden；无掩蔽则结束，不错误保留。
17. 其他 Invisible 来源不被 Stealth Attack 的 Hide 终止误删；Cover 在攻击与回合末由服务端重验。
18. Elusive 未失能时所有针对自己的攻击均无 Advantage，包括 Reckless、Invisible、Prone 和 GM grant。
19. Elusive 保留 Disadvantage，原相抵场景变为纯 Disadvantage；自己的攻击和盟友攻击不受影响。
20. Elusive 持有者 Incapacitated 时过滤暂停，恢复后重新按当前来源计算。
21. 客户端伪造工具、尺寸、Cover、效果成本、save total 或条件终止均无效。
22. pending save/load、过期 version、重复 decision、apply/replay 的骰序、HP、位置和条件一致。
23. 条件与 Daze 在 AI/人类回合、移动、动作和回合结束路径均不绕过权威验证。
24. 既有 Sneak Attack、Hide、Monk rider、Invisible、Hold Person 与战斗回归测试通过。

## 7. 非目标与停止条件

### 非目标

- 新通用毒药制作、完整物品库存 UI、2D 掩蔽地图、所有 Rogue/Thief 特性。
- 把效果成本转为 Focus/资源点、把 Daze 做成 Incapacitated、把 Elusive 做成 Disadvantage。
- 通过普通 Invisible 或距离阈值冒充 Supreme Sneak 的 Hide/Cover 条件。

### 停止条件

1. S0-F10/F14/D2 未合入或无法保证“先扣骰、后掷伤害、伤害后效果”。
2. 缺少权威 Poisoner’s Kit、生物尺寸或 Cover 模型，且白名单无法闭合。
3. Sneak 当前实现把骰公式过早物化，无法在不重掷/双写伤害的情况下扣成本。
4. Daze 的行动类别或 Knock Out 的来源化终止无法被验证/reducer 一致支持。
5. 需要白名单外文件、客户端规则或状态迁移才能完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_cunning_strike.py -q
python -m pytest tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_action_routing.py tests/rulesets/test_dnd2024_hit_riders.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features/resolver.py tests/rulesets/test_dnd2024_cunning_strike.py
git diff --check
```

## 9. 交付要求

1. 测试 1–24 逐项映射，逐项报告七种效果成本、save、持续时间和终止。
2. 展示完整骰池→扣成本→剩余 rolls→伤害→效果的事件/RNG 证据。
3. 单列 Hide 来源与回合末 Cover、Elusive 相抵场景及存档重放。
4. 模型缺失必须停止，不得将七项中的部分实现称为切片完成。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 骰池预算与两效果：
- Poison/Trip/Withdraw 与 Devious 三效果：
- Supreme Sneak 的 Hide/Cover：
- Elusive 来源过滤：
- 白名单文件对照：
- 测试 1–24 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放、AI 与 RNG：
- 偏差、停止原因与未决事项：
