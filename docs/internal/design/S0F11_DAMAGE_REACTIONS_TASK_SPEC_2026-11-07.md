# S0-F11 任务书：受击反击与伤害减免反应

| 项 | 值 |
|---|---|
| 切片编号 | S0-F11（独立新任务，不覆盖旧 SP） |
| 验收载体 | Berserker `retaliation`、Monk `deflect_attacks`、`deflect_energy`、`slow_fall` |
| 通用能力 | 受击反应窗口、减伤后重定向、反应近战反击、坠落减伤 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | 既有 Uncanny Dodge 反应链可用；S0-A 类真实子职授予前置已满足 |
| 建议分支 | `feat/dnd2024-s0f11-damage-reactions` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围

1. 开工记录实际集成提交并建立干净工作树；先运行 Uncanny Dodge 基线测试。
2. 四项特性完整实现，不得只提供反应按钮、文案或固定减伤。
3. 攻击总伤害、减免、Focus、Reaction、豁免、反击与 HP 变更全部由服务端结算并落事件。
4. 保留 Shield、Uncanny Dodge、后续 hit-trigger 的决策顺序和暂存伤害，不重复提交伤害。
5. 远程重定向需要 Total Cover、坠落需要真实 fall trigger；无权威模型时停止，不伪造。

## 2. 官方英文关键原文与页码

SRD p30，Berserker 10 级：

> **Retaliation.** When you take damage from a creature that is within 5 feet of you, you can take a Reaction to make one melee attack against that creature, using a weapon or an Unarmed Strike.

SRD p51，Monk 3/4 级：

> **Deflect Attacks.** When an attack roll hits you and its damage includes Bludgeoning, Piercing, or Slashing damage, you can take a Reaction to reduce the attack’s total damage against you. The reduction equals 1d10 plus your Dexterity modifier and Monk level.

> If you reduce the damage to 0, you can expend 1 Focus Point to redirect some of the attack’s force. If you do so, choose a creature you can see within 5 feet of yourself if the attack was a melee attack or a creature you can see within 60 feet of yourself that isn’t behind Total Cover if the attack was a ranged attack. That creature must succeed on a Dexterity saving throw or take damage equal to two rolls of your Martial Arts die plus your Dexterity modifier. The damage is the same type dealt by the attack.

> **Slow Fall.** You can take a Reaction when you fall to reduce any damage you take from the fall by an amount equal to five times your Monk level.

SRD p52，Monk 13 级：

> **Deflect Energy.** You can now use your Deflect Attacks feature against attacks that deal any damage type, not just Bludgeoning, Piercing, or Slashing.

SRD p182 的 Falling 提供本切片触发事实：坠落伤害为每 10 尺 1d6 Bludgeoning、至多 20d6，且未避免任何坠落伤害时落地为 Prone。

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| Reaction 统一检查 HP、预算、失能和 `reaction_blocked` | `combat/reaction_spells.py::_reaction_eligible` |
| 当前 feature reaction 只识别 Uncanny Dodge，并校验可见性 | `combat/reaction_spells.py::_reaction_feature_choices` |
| 命中后暂存 attack/damage，建立 pending decision，最终验证再结算 | `_hit_reaction_offer`、`_validate_hit_reaction` |
| 既有 `uncanny_dodge`（Uncanny Dodge）修改暂存 damage 后经 `_submit_hit_damage` 提交并处理专注 | `combat/reaction_spells.py::_finish_hit_reaction` |
| `HIT_REACTION_DECISION` 同时容纳法术/feature 选项；客户端不提供伤害真值 | `combat/reaction_spells.py`、`_hit_reaction_offer` |
| 攻击、移动和伤害事件集中在 resolution；没有独立客户端伤害通道 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| HP/临时 HP/条件由 reducer 应用，反应预算由事件扣减 | `combat/reducer.py::_reduce_event`、`_apply_hp_change` |
| 一维 positions 可提供 5/60 尺距离，未核实 Total Cover 与 fall 事件模型 | `combat/engine.py::_distance`、`initialize_state` |
| Focus 与 Martial Arts die 已投影到 actor view | `combat/view.py::_player_view`、`features/resolver.py` |

## 4. 精确实现契约

### 4.1 共享反应窗口与顺序

1. 保留同一 `HIT_REACTION_DECISION` 权威入口，将可选 feature 按 catalog 顺序列出；窗口创建和最终 resolve 都重新检查特性、Reaction、失能、攻击类型、来源和距离。
2. Deflect 的触发点是攻击已命中且本次攻击总伤害已掷定、尚未提交 HP；先记录 `original_amount`、`reduction`、`final_amount`，再进入后续 hit-trigger 或伤害提交。
3. Retaliation 只在最终伤害实际应用于持有者后触发，伤害必须大于 0 且来源是 5 尺内生物；不能在攻击命中但 Shield 使攻击失效时提前触发。
4. 同一伤害最多消费一次 Reaction；选择 Deflect/Uncanny Dodge/Shield 后不再提供另一个需要 Reaction 的选项。Decline 不扣 Reaction。
5. 暂存伤害必须沿 `next_decision` 同步更新，任何路径只调用一次最终提交；重放不重新掷减伤、反击或豁免骰。

### 4.2 Deflect Attacks / Deflect Energy

1. 3 级起仅当攻击伤害包含 Bludgeoning、Piercing 或 Slashing 时可选；13 级 `deflect_energy` 扩展到任意伤害类型，不要求原类型仍存在。
2. 选择后支付 1 Reaction，减伤为 `1d10 + 当前 Dexterity modifier + Monk level`，最低 final damage 为 0；减伤大于伤害时记录实际归零，不产生治疗。
3. 仅当 `final_amount == 0` 时开放重定向选择；再支付 1 Focus。伤害剩 1 或更多时即使 Focus 充足也不能重定向，未选择重定向不扣 Focus。
4. 原攻击为近战：新目标必须可见且在 Monk 5 尺内；原攻击为远程：新目标必须可见、在 60 尺内且不处于 Total Cover 后。攻击者本人未被规则排除，不擅自增加排除条件。
5. 新目标做 Dexterity saving throw，DC 使用 Monk 特性既有权威 save DC；失败受 `两次 Martial Arts die + Dexterity modifier` 伤害，成功不受该重定向伤害。
6. 重定向伤害类型与原攻击一致；混合类型若无单一权威表示则停止，不按客户端选择或任意取首项。
7. 重定向不是一次攻击掷骰，不触发 Sneak Attack、Smite、命中 rider 或 Retaliation 的“攻击命中”路径；它是 Deflect 的 save/damage 效果。

### 4.3 Retaliation 与 Slow Fall

1. Retaliation 选择后支付 1 Reaction，并仅以 5 尺内实际造成伤害的生物为目标进行一次近战武器或 Unarmed Strike 攻击。
2. 反击复用正常攻击档案、攻击掷骰、加减益、暴击、伤害和事件链，但不支付 Action/Bonus Action/attacks_remaining；不得由客户端指定命中或伤害。
3. 若来源死亡、消失、超距、不再可成为目标或持有者失去 Reaction 资格，最终 resolve 拒绝且不倒扣资源；窗口不得指向任意其他生物。
4. Slow Fall 仅在实际 fall 触发时提供 Reaction，减伤精确为 `5 × Monk level`，最终伤害最低 0；不是每 10 尺减一次，也不是通用 Bludgeoning 抗性。
5. 归零坠落伤害即满足 Falling 的“avoids taking any damage”，不施加该次坠落导致的 Prone；仍受伤时保留既有 Prone 规则。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/reaction_spells.py`、`resolution.py`、`validation.py`、`reducer.py`、`engine.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`：仅本批参数读取与资格查询。
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要效果声明。
5. 新建 `tests/rulesets/test_dnd2024_damage_reactions.py`；本任务书仅追加执行报告。
6. 其他文件、通用场景编辑器或前端不在白名单；需要时停止。

## 6. 编号测试矩阵

通过真实 Berserker/Monk 角色和完整战斗决策链测试；使用 SequenceRng 固定骰序，不得直接写 reaction 结果或最终伤害。

1. 四项特性在正确等级授予，低一级不授予；Retaliation 仅真实 Berserker 获得。
2. 无 Reaction、0 HP、Incapacitated 或 `reaction_blocked` 时四项均不提供，伪造 resolve 被拒绝。
3. 3 级 Monk 可对含 B/P/S 的命中使用 Deflect，纯 Fire 不可；13 级后纯 Fire 等任意类型可。
4. 减伤严格为 1d10+Dex mod+Monk level，事件记录三项与骰值，只掷一枚 d10。
5. 减伤小于原伤害时 HP 只扣差额；大于原伤害时归零但不治疗，原伤害不重复提交。
6. 最终伤害大于 0 时没有重定向选项且不扣 Focus；恰减至 0 时才可选择并另扣 1 Focus。
7. Focus 为 0 时仍可减伤但不能重定向；decline redirect 不扣 Focus。
8. 近战原攻击只能选可见 5 尺内目标，6 尺或不可见目标拒绝。
9. 远程原攻击只能选可见 60 尺内且无 Total Cover 目标；61 尺或 Total Cover 拒绝。
10. 重定向目标 Dex save 失败受两次 Martial Arts die+Dex mod，成功为 0；DC、骰和类型准确。
11. 重定向不产生攻击命中，不触发 Sneak/Smite/命中 rider，也不误付 Action。
12. Shield 使原命中转为未中时不触发 Deflect/Retaliation；Uncanny Dodge 与 Deflect 不得同轮重复消费 Reaction。
13. 后续 hit-trigger 存在时暂存伤害保持减免后的同一数值，最终 HP、专注和事件顺序正确。
14. Berserker 受到 5 尺内生物大于 0 的最终伤害后可 Reaction 反击一次。
15. 0 伤害、非生物来源、6 尺来源或未实际受伤均不触发 Retaliation。
16. Retaliation 只能攻击原伤害来源，可选择合法近战武器或 Unarmed Strike，不能换远程武器或其他目标。
17. 反击正常掷攻击/伤害并支持合法加减益和暴击，但不扣 Action、Bonus Action 或攻击次数。
18. 反应窗口后来源超距/失效或防御者失能时最终 resolve 安全拒绝，无资源或 HP 部分落账。
19. 4 级 Monk 坠落时可用 Reaction 减 `5 × Monk level`；3 级或无 Reaction 不可用。
20. Slow Fall 减伤 0 边界、超额归零和 Prone 抑制准确；普通攻击 Bludgeoning 不适用该减伤。
21. 保存/加载 pending decision、apply/replay、AI 自动 decline/选择均不重复掷骰或重复伤害。
22. 既有 Shield、Counterspell、Uncanny Dodge、Smite、Monk Focus 与战斗回归测试通过。

## 7. 非目标与停止条件

### 非目标

- 通用反应系统重写成新协议、所有怪物反应、完整地形/坠落编辑器。
- Uncanny Dodge 规则扩张、Shield 重设计、其他 Monk/Berserker 特性。
- 把重定向做成免费攻击或把 Retaliation 做成客户端即时攻击。

### 停止条件

1. 既有 Uncanny Dodge 链或真实子职授予在前置基线不可用。
2. 暂存伤害无法在不重复提交/破坏后续决策的前提下加入减伤。
3. 没有权威 Total Cover、可见性或 fall trigger，且不能在本白名单内闭合验收。
4. 混合伤害类型无法确定重定向的唯一类型，或 Monk save DC 尚无权威来源。
5. 需要修改白名单外文件或让客户端提供伤害、距离、骰值才能通过测试。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_damage_reactions.py -q
python -m pytest tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features/resolver.py tests/rulesets/test_dnd2024_damage_reactions.py
git diff --check
```

## 9. 交付要求

1. 测试 1–22 逐项映射，单列减至 0/未减至 0、近战/远程、成功/失败豁免证据。
2. 报告 Reaction/Focus 支付、暂存伤害、Retaliation 时点与 Slow Fall/Prone 的完整事件顺序。
3. 保存 apply/replay、pending save/load、RNG 和回归输出。
4. 任何模型缺口均作为停止事实报告，不得以近似规则交付。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 反应窗口与资格复核：
- Deflect 数值、类型和重定向：
- Retaliation 最终伤害后反击：
- Slow Fall 与 Prone：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 暂存伤害、重放、存档与 RNG：
- 偏差、停止原因与未决事项：
