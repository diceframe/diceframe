# S0-F12 任务书：豁免减伤、动态抗性与归零存活

| 项 | 值 |
|---|---|
| 切片编号 | S0-F12（独立新任务，不覆盖旧 SP） |
| 验收载体 | `danger_sense`、Monk/Rogue 各自 `evasion`、`fiendish_resilience`、`relentless_rage` |
| 通用能力 | Dex 豁免优势、半伤效果改写、休息重选抗性、0 HP 前存活拦截 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-D2、S0-E2 已合入；子职授予与光环/豁免合成接口可用 |
| 建议分支 | `feat/dnd2024-s0f12-save-survival` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从 S0-D2/E2 实际合入提交开工，保留其骰窗、豁免来源和事件字段。
2. Monk 与 Rogue 的 Evasion 分别声明、分别授予，但共用同一机械实现，不能按职业名复制不同公式。
3. 所有抗性选择、豁免、HP 与 DC 状态均由服务端验证和持久化；客户端不得提交伤害或存活结果。
4. 本切片不借 Relentless Rage 重做死亡系统，也不把 Fiendish Resilience 写成静态抗性。

## 2. 官方英文关键原文与页码

SRD p29，Barbarian 2 级：

> **Danger Sense.** You have Advantage on Dexterity saving throws unless you have the Incapacitated condition.

SRD p51 与 p63，Monk/Rogue 7 级：

> **Evasion.** When you’re subjected to an effect that allows you to make a Dexterity saving throw to take only half damage, you instead take no damage if you succeed on the saving throw and only half damage if you fail. You don’t benefit from this feature if you have the Incapacitated condition.

Rogue 原文结尾为 “You can’t use this feature if you have the Incapacitated condition.”，机械边界相同。

SRD p76，Fiend 10 级：

> **Fiendish Resilience.** Choose one damage type, other than Force, whenever you finish a Short or Long Rest. You have Resistance to that damage type until you choose a different one with this feature.

SRD p30，Barbarian 11 级：

> **Relentless Rage.** If you drop to 0 Hit Points while your Rage is active and don’t die outright, you can make a DC 10 Constitution saving throw. If you succeed, your Hit Points instead change to a number equal to twice your Barbarian level.

> Each time you use this feature after the first, the DC increases by 5. When you finish a Short or Long Rest, the DC resets to 10.

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| 攻击模式有共享来源聚合，可复用其 provenance 思路但不能误用于豁免 | `combat/check_modes.py::attack_sources`、`mode_from_sources` |
| 失能及 Dex 自动失败由条件注册表聚合 | `combat/check_modes.py::condition_flags` |
| 法术/攻击伤害和抗性已有定点入口 | `combat/resolution.py::_spell_events`、`_attack_events`、`_resist_damage` |
| S0-D2/E2 的预期依赖是骰窗与 saving-throw 合成，不得在本任务重新实现 | 前置任务实际执行结果 |
| 短休、长休分别经统一入口恢复资源 | `resting/engine.py::complete_short_rest`、`complete_long_rest` |
| 休息接口当前没有任意角色字段写入通道，选择必须显式验证 | 同上及 `_recover_class_resources` |
| HP 先扣临时 HP，再 clamp 至 0；玩家归零会添加 Unconscious 并结束 Rage | `combat/reducer.py::_apply_hp_change` |
| 条件、资源、pending decision 由 reducer 落账 | `combat/reducer.py::_reduce_event` |
| Fiend 子职目录列有 10 级特性，但目录条目不代表现有机制 | `content/subclass_catalogs/srd_subclasses.json` |
| 角色 canonical state 经 actor view 投影，不能由 feed 反写 | `combat/view.py::_player_view` |

## 4. 精确实现契约

### 4.1 声明、检定与 Evasion

1. 增加各 canonical feature declaration、owner、等级、p29/p30/p51/p63/p76 source_ref 和双语 locale；Monk/Rogue 的同名 `evasion` 使用稳定 owner-scoped 身份，不重复授予效果。
2. Danger Sense 给所有 Dexterity saving throws 增加一个 Advantage 来源；与其他来源仍只掷两骰取高，不影响攻击、能力检定、先攻或其他属性豁免。
3. 持有者在掷骰时 Incapacitated，则 Danger Sense 与 Evasion 均无效；失能导致的 Dex 自动失败仍按共享 condition flags 处理。
4. Evasion 只匹配“该效果允许 Dex 豁免成功时仅受半伤”的权威声明，例如 `half_on_success`；不能对所有 Dex 豁免或所有伤害全局减半。
5. 匹配效果中：成功伤害为 0，失败伤害为原伤害的 `floor(amount/2)`；无 Evasion 或失能时保持原成功半伤/失败全伤。
6. 豁免的其他成功/失败效果仍按原效果执行；Evasion 只改写伤害，不自动免疫条件、位移或其他 rider。
7. 抗性、易伤与 Evasion 的顺序必须由事件明确：先确定该效果的豁免伤害分支，再按既有 `_resist_damage` 顺序应用一次抗性，不能重复减半。

### 4.2 Fiendish Resilience

1. 在 canonical character 的服务端 `feature_state.fiendish_resilience` 保存唯一 `damage_type`；只接受目录中除 Force 外的合法伤害类型，拒绝 bool、数组、未知值和客户端直接状态写入。
2. 每次完成 Short 或 Long Rest 时可通过显式休息选择参数改选；无选择时保留旧类型，首次从未选择则保持未选择，不默认 Fire。
3. 新选择从该次休息完成起替换旧选择；抗性持续到以后通过本特性另选，不因另一次无选择休息、战斗结束或存档加载而清空。
4. `_resist_damage` 仅在当前伤害类型等于保存类型时减半一次；Force 永远不能由本特性选择，其他来源的 Force 抗性不在本任务范围。
5. 休息结果事件记录 owner、旧类型、新类型和来源；无改变时不伪造恢复事件，重放不得再次要求随机或客户端结果。

### 4.3 Relentless Rage

1. 在 canonical character 的服务端 `feature_state.relentless_rage` 保存 `next_dc`，初始 10；每次实际使用该特性进行豁免（无论成败）后下一次 DC 增 5，Short/Long Rest 完成时重置 10。
2. 触发必须是 Rage 当前有效、一次伤害将使 HP 从大于 0 降至 0，且未发生 outright death；临时 HP 吸收后仍不归零时不触发。
3. 在 `_apply_hp_change` 施加 0 HP、Unconscious 和结束 Rage 之前建立权威保存/决策边界；选择使用后由服务端进行 Constitution saving throw，DC 为当前 `next_dc`。
4. 成功时本次归零被替代为 `hp = 2 × Barbarian level`，不超过 max HP 的既有上限；不添加本次 Unconscious、不因本次归零结束 Rage，并将下一 DC 加 5。
5. 失败或 decline 时走原归零后果；失败也是一次实际使用，下一 DC 仍增 5，decline 未使用则不递增。不得把递增条件暗改为仅成功。
6. 该豁免保留 S0-D2/E2 的合法加值/来源合成，但 DC 递增只由本特性状态决定；客户端不得提交 HP、Barbarian level、DC 或豁免 total。
7. 成功恢复不是治疗触发，不重复触发“受伤后”反应；同一伤害只结算一次，事件必须能审计原伤害、save、DC 和替代 HP。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/check_modes.py`、`resolution.py`、`reducer.py`、`validation.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`：仅本批参数/owner 查询。
4. `src/rulesets/dnd2024/resting/engine.py`：仅 Fiend 选择与 Relentless DC 重置。
5. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要效果/状态声明。
6. 新建 `tests/rulesets/test_dnd2024_save_damage_survival.py`；本任务书仅追加执行报告。
7. 需要改其他角色 schema、迁移或 UI 文件时停止并报告。

## 6. 编号测试矩阵

使用真实等级、子职、Rage、法术/效果和休息入口；以 SequenceRng 固定豁免，禁止直接写 HP 存活结果或伪造 `half_on_success`。

1. 五项声明在正确职业/子职和等级授予；Monk/Rogue Evasion 各有正确 owner 与 source_ref。
2. Danger Sense 的 Dex 豁免掷两骰取高，来源可观察；多个 Advantage 来源仍只两骰。
3. Danger Sense 不影响 Str/Con/Wis 豁免、攻击、能力检定或先攻。
4. Incapacitated 时 Danger Sense 不加 Advantage；Paralyzed/Stunned/Unconscious 经共享 flags 同样生效。
5. Monk 与 Rogue 对合法 half-on-success Dex 效果均为成功 0、失败半伤，公式不重复应用。
6. 非 half-on-success 的 Dex 效果、无豁免攻击和其他属性豁免不被 Evasion 改写。
7. Incapacitated 时 Evasion 失效，恢复原成功半伤/失败全伤及自动失败规则。
8. Evasion 只改伤害，不删除原效果的合法条件/位移；抗性只在规定顺序应用一次。
9. 奇数伤害失败时向下取整；成功为 0 时不产生虚假 HP 治疗或重复专注伤害。
10. Fiend 10 级短休可改选 Acid/Cold/Fire 等合法类型，长休也可改选，Force 始终拒绝。
11. 非法类型、bool、数组、未知字段和未经休息的改选均拒绝，canonical state 不变。
12. 无改选休息保留原抗性；首次未选择不默认类型；存档/加载与重放保持相同选择。
13. 仅选中类型减半一次，其他类型和 Force 不受本特性影响，事件含旧/新选择。
14. 11 级 Rage 中受到将 HP 降至 0 的伤害时可尝试 DC10 Con 豁免；非 Rage 或不归零不触发。
15. 临时 HP 完全吸收伤害时不触发； outright death 不走存活分支。
16. 成功后 HP 改为 `2 × Barbarian level`，不添加本次 Unconscious、不结束 Rage，下一 DC 为 15。
17. 连续实际尝试依次 DC10/15/20；失败走正常归零且同样增加下一 DC，decline 未使用则不增加。
18. 短休和长休均把下一 DC 重置为 10；升级、普通回合和战斗结束不错误重置。
19. 存活豁免的合法加值/来源合成准确，客户端伪造 total、DC、level 或 HP 均被忽略/拒绝。
20. 同一伤害只触发一次；成功恢复不重复触发受伤反应、不产生额外治疗或额外 RNG。
21. 触发前后 save/load、pending decision、apply/replay 的 HP、Rage、条件、DC 与 RNG 一致。
22. 既有 Rage、Overchannel、Paladin Aura、死亡豁免、休息与伤害抗性测试无回归。

## 7. 非目标与停止条件

### 非目标

- 全职业 Evasion 外的新型豁免重写、死亡规则重构、通用 feature_state 平台迁移。
- 其他 Fiend/Barbarian/Monk/Rogue 特性、所有抗性来源或 UI 美化。
- 把失败尝试也递增 DC、把成功恢复写成普通治疗或事后移除 Unconscious。

### 停止条件

1. S0-D2/E2 未合入或豁免合成接口与契约不一致。
2. 无法识别效果是否真实声明“Dex 成功半伤”，需要按法术名硬编码。
3. 休息或 canonical character 没有可验证、可持久化的选择边界，且白名单无法闭合。
4. Relentless Rage 无法在归零后果前拦截，必须事后删条件或双写 HP。
5. outright death 当前无权威判定，或需要白名单外 schema/迁移才能区分。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_save_damage_survival.py -q
python -m pytest tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/resting/engine.py src/rulesets/dnd2024/features/resolver.py tests/rulesets/test_dnd2024_save_damage_survival.py
git diff --check
```

## 9. 交付要求

1. 测试 1–22 逐项映射，展示失能、非匹配豁免、Force 拒绝和休息重选证据。
2. 单列 Relentless Rage 在 reducer 归零前的事件顺序、DC10→15→20 与休息重置。
3. 报告伤害/抗性顺序、RNG、存档和重放结果，不只给最终 HP 截图。
4. 偏差和停止原因必须显式列出，不得将事后补救算作成功实现。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Danger Sense 与 Evasion 接入：
- Fiend 选择、持久化与抗性：
- Relentless 归零边界、DC 与 HP：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放、RNG 与 reducer 证据：
- 偏差、停止原因与未决事项：
