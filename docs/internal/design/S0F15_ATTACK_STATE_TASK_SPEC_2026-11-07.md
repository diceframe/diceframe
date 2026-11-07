# S0-F15 任务书：攻击前声明与连续状态

| 项 | 值 |
|---|---|
| 切片编号 | S0-F15（独立新任务，不覆盖旧 SP） |
| 验收载体 | `reckless_attack`、`frenzy`、`brutal_strike`、`improved_brutal_strike`、`studied_attacks` |
| 通用能力 | 首攻击前声明、持续加减益、放弃 Advantage、目标绑定的下一次攻击状态 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-F14 已合入；真实 Berserker 授予和命中 rider 决策链可用 |
| 建议分支 | `feat/dnd2024-s0f15-attack-state` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从 S0-F14 实际集成提交开工，复用其 rider 决策、事件来源和资源顺序。
2. 五项特性完整实现，不得在攻击掷骰后追认 Reckless/Brutal，也不得把 Advantage 写成最终命中结果。
3. 服务端在每次攻击前重算真实来源；客户端只能声明本切片允许的选项，不能提交 mode、d20、伤害或状态。
4. 13/17 级 Improved Brutal Strike 是同一特性的等级升级，不是两个可重复选择的独立攻击。

## 2. 官方英文关键原文与页码

SRD p29，Barbarian 2 级：

> **Reckless Attack.** When you make your first attack roll on your turn, you can decide to attack recklessly. Doing so gives you Advantage on attack rolls using Strength until the start of your next turn, but attack rolls against you have Advantage during that time.

SRD p30，Berserker 3 级：

> **Frenzy.** If you use Reckless Attack while your Rage is active, you deal extra damage to the first target you hit on your turn with a Strength-based attack. To determine the extra damage, roll a number of d6s equal to your Rage Damage bonus, and add them together. The damage has the same type as the weapon or Unarmed Strike used for the attack.

SRD p29–30，Brutal Strike 及升级：

> **Brutal Strike.** If you use Reckless Attack, you can forgo any Advantage on one Strength-based attack roll of your choice on your turn. The chosen attack roll mustn’t have Disadvantage. If the chosen attack roll hits, the target takes an extra 1d10 damage of the same type dealt by the weapon or Unarmed Strike, and you can cause one Brutal Strike effect of your choice.

> **Forceful Blow.** The target is pushed 15 feet straight away from you. You can then move up to half your Speed straight toward the target without provoking Opportunity Attacks.

> **Hamstring Blow.** The target’s Speed is reduced by 15 feet until the start of your next turn. A target can be affected by only one Hamstring Blow at a time—the most recent one.

> **Level 13: Improved Brutal Strike.** The following effects are now among your Brutal Strike options. **Staggering Blow.** The target has Disadvantage on the next saving throw it makes, and it can’t make Opportunity Attacks until the start of your next turn. **Sundering Blow.** Before the start of your next turn, the next attack roll made by another creature against the target gains a +5 bonus to the roll. An attack roll can gain only one Sundering Blow bonus.

> **Level 17: Improved Brutal Strike.** The extra damage of your Brutal Strike increases to 2d10. In addition, you can use two different Brutal Strike effects whenever you use your Brutal Strike feature.

SRD p48，Fighter 13 级：

> **Studied Attacks.** If you make an attack roll against a creature and miss, you have Advantage on your next attack roll against that creature before the end of your next turn.

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| 攻击来源由条件、射程和 GM grant 聚合，多个模式相抵 | `combat/check_modes.py::attack_sources`、`mode_from_sources`、`attack_mode` |
| 攻击掷骰、命中、伤害和 rider 有统一入口 | `combat/resolution.py::_attack_events` |
| 服务端按真实装备/徒手档案验证攻击，不信客户端攻击身份 | `combat/validation.py::_validate_attack`、`combat/view.py::_declared_attack` |
| Rage damage track 与 Rage 状态已有声明和结算 | class feature catalog、`combat/resolution.py::_class_capability_events` |
| 每回合使用记录可用于 Brutal/Frenzy 频率，但需目标/攻击粒度状态 | `combat/resolution.py::_feature_used_this_turn`、`combat/reducer.py::_reduce_event` |
| 条件与位置由事件落账，移动已有权威入口 | `combat/reducer.py`、`combat/resolution.py::_movement_events` |
| 命中 rider 与可选决策依赖 S0-F14 实际接口 | 前置执行结果；本文不虚构其函数名 |
| 条件速度修正和攻击模式必须读取持续状态 | `combat/check_modes.py::effective_speed`、`attack_sources` |

## 4. 精确实现契约

### 4.1 Reckless Attack 与 Frenzy

1. 增加五项 canonical declaration、owner/等级/source_ref/双语 locale；真实 Berserker 才能获得 Frenzy。
2. Reckless 仅在自己回合第一次 attack roll 掷骰前声明；任何攻击均可触发声明窗口，但后续 Advantage 仅给使用 Strength 的攻击。
3. 声明后至自己下回合开始：自身 Strength-based attack 增加一个 Advantage 来源；所有针对自己的 attack roll 增加一个 Advantage 来源。其他 Disadvantage 仍按共享规则相抵，不直接改写最终命中。
4. 未声明、非第一次攻击、他人回合或事后声明均不启用；状态在正确时点自动到期，apply/replay 不重开窗口。
5. Frenzy 要求本回合实际声明 Reckless 且 Rage 在声明/命中结算时有效；只作用于本回合首个以 Strength-based attack 命中的目标，之前未命中不消费。
6. Frenzy 额外掷 `当前 Rage Damage bonus` 个 d6，类型与该武器/Unarmed Strike 相同；每回合只触发一次，可与合法 Brutal Strike 同次命中共存。

### 4.2 Brutal Strike 声明与伤害

1. 本回合已用 Reckless 才可在一次 Strength-based attack 掷骰前声明 Brutal；每回合选择至多一次攻击。
2. 服务端收集该次攻击全部 Advantage/Disadvantage 来源：存在任何有效 Disadvantage 即拒绝；放弃全部 Advantage 后才掷骰，不能只删 Reckless 而保留其他 Advantage。
3. 声明时不消耗独立资源；未命中不产生额外伤害/效果，但仍消耗本次 Brutal 选择，不能在同回合改投另一攻击。
4. 命中额外造成同武器/Unarmed Strike 类型的 1d10；17 级起为 2d10。额外骰按既有攻击暴击规则处理，抗性按最终伤害实例计算。
5. 命中后可不选效果而保留额外伤害；13 级前只能选 Forceful/Hamstring，13 级起增加 Staggering/Sundering，17 级起可选两个不同效果并分别结算。

### 4.3 四种效果与 Studied Attacks

1. **Forceful**：目标向远离攻击者的直线推 15 尺；随后攻击者可沿直线向目标移动至多一半当前 Speed，且不引发 OA。若一维 positions 无法表达“直线/方向”，停止，不用任意距离修改冒充。
2. **Hamstring**：目标 Speed -15 至攻击者下回合开始，最低有效 Speed 遵循共享边界；同一目标只保留最新一个 Hamstring，不叠加 -30。
3. **Staggering**：目标下一次 saving throw 有 Disadvantage，使用即消费；同时不能进行 OA 至攻击者下回合开始。两部分独立，不因豁免已消费而提前恢复 OA。
4. **Sundering**：至攻击者下回合开始，另一生物对目标的第一次攻击获得 +5；攻击者本人不受益，使用即消费；同一攻击最多一个 Sundering +5。
5. **Studied Attacks**：攻击某生物未命中后，建立 attacker→该 target 的独立标记，至攻击者下回合结束；只强化对该目标的下一次攻击，使用前消费，命中/未中均结束旧标记。
6. 若该下一次攻击仍未中，在本次旧标记消费后再生成一个新的同目标标记；不得让一次攻击同时消费并受益于自己刚产生的效果。不同目标标记不得互相转移或全局覆盖。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/check_modes.py`、`resolution.py`、`validation.py`、`reducer.py`、`engine.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`：仅本批参数/授予查询。
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要持续状态声明。
5. 新建 `tests/rulesets/test_dnd2024_attack_state.py`；本任务书仅追加执行报告。
6. 需要修改白名单外地图、豁免或 UI 文件时停止，不复制平行实现。

## 6. 编号测试矩阵

使用真实 Barbarian/Berserker/Fighter 和完整攻击链；固定 d20/d6/d10 顺序，禁止客户端直接写 Advantage、命中、效果或伤害。

1. 五项声明在正确职业/子职/等级授予，13/17 升级边界与 source_ref、双语标签准确。
2. Reckless 只能在自己回合第一次攻击掷骰前声明；事后、第二次攻击或他人回合声明拒绝。
3. 声明后自身 Strength 攻击与受敌攻击均有 Advantage 至下回合开始，Dex 攻击不获自身 Advantage。
4. Reckless 与 Disadvantage 正常相抵，多来源不掷三骰；到期后两类来源同时消失。
5. Rage+Reckless 下首个 Strength 命中触发 Frenzy，之前 miss 不消费；之后命中不重复。
6. Frenzy 骰数等于当前 Rage Damage bonus、类型同攻击；无 Rage/无 Reckless/非 Strength 不触发。
7. Brutal 无 Reckless、非 Strength、同回合第二次或掷骰后声明均拒绝。
8. 有任何 Disadvantage 时即使 Advantage 相抵也不能 Brutal；无 Disadvantage 时放弃全部 Advantage 后单骰。
9. Brutal miss 无额外伤害/效果且不能改投；hit 在 9–16 级加 1d10、17 级加 2d10，类型准确。
10. Brutal 与 Frenzy 同次合法命中分别结算，不共享错误预算；暴击、抗性和 RNG 顺序正确。
11. 9 级只能选 Forceful/Hamstring，decline 效果仍有额外伤害；13 级新增两项，17 级可选两个不同项。
12. 17 级重复选择同一效果、13 级选两项或低等级选新效果均拒绝，无部分事件落账。
13. Forceful 推离 15 尺及随后半速直线跟随准确，跟随不引发 OA，不能改方向或超距。
14. Hamstring -15 至正确时点，同一目标重复施加只保留最新，不叠加，不与普通速度规则冲突。
15. Staggering 的下一次豁免 Disadvantage 被消费，OA 禁止独立持续至下回合开始。
16. Sundering 仅另一生物对该目标的下一攻击 +5，使用后消费；攻击者本人和多次攻击不重复受益。
17. 同一攻击两个 Sundering 不叠成 +10；效果到期、save/load 与 replay 状态一致。
18. Fighter 13 级 miss 后仅对同一目标的下一攻击有 Advantage，对其他目标无效。
19. Studied 下一攻击无论命中/miss 都消费旧标记；再次 miss 才生成新标记，不自我强化当前攻击。
20. 不同目标标记独立且在下回合结束到期；攻击 roll 而非伤害/能力检定才可消费。
21. 过期 version、重复 resolve、伪造目标/feature/mode 均 fail closed，存档重放无重复状态。
22. 既有 Rage、Monk rider、Sneak、Smite、攻击模式、移动与条件回归测试通过。

## 7. 非目标与停止条件

### 非目标

- 其他 Barbarian/Fighter 特性、通用攻击前 UI 改版、完整机会攻击或 2D 地图。
- 把 Reckless 做成全属性 Advantage、把 Frenzy 做成每次命中、把 Brutal 变成事后加骰。
- 旧产品若已有相反裁剪，不作为覆盖官方原文的依据。

### 停止条件

1. S0-F14 未合入或 rider 决策无法保证攻击前/命中后顺序。
2. 攻击来源无法区分“存在 Disadvantage”与最终 mode，导致 Brutal 资格错误。
3. 一维位置无法验收 Forceful 的直线推离/跟随，或 OA 作用域无权威表示。
4. Studied/Staggering/Sundering 无法在消费与新授予之间保持明确顺序。
5. 需要白名单外文件、客户端骰值或私有前端规则才能完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_attack_state.py -q
python -m pytest tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_hit_riders.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features/resolver.py tests/rulesets/test_dnd2024_attack_state.py
git diff --check
```

## 9. 交付要求

1. 测试 1–22 逐项映射，单独展示 Disadvantage 禁止、13/17 升级和两个不同效果。
2. 提供 Reckless/Frenzy/Brutal 同次攻击的事件顺序、骰序和伤害分项。
3. 展示四种效果及 Studied 的目标绑定、消费、到期、重放证据。
4. 停止或偏差不得包装为完成，交付文件严格对照白名单。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Reckless 与 Frenzy：
- Brutal 声明、伤害和四级效果：
- Studied 目标状态：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放与 RNG：
- 偏差、停止原因与未决事项：
