# S0-F18 任务书：Hunter’s Mark 实例与 Ranger 高等级链

| 项 | 值 |
|---|---|
| 切片编号 | S0-F18（独立新任务，不覆盖旧 SP） |
| 验收载体 | `favored_enemy`、`relentless_hunter`、`precise_hunter`、`feral_senses`、`foe_slayer` 与 `spell:hunter_s_mark` |
| 通用能力 | 总是准备/免费施法、施法者拥有的当前标记、专注例外、Blindsight、伤害骰升级 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-F5 已合入；施法、专注、法术目标和持续效果接口可用 |
| 建议分支 | `feat/dnd2024-s0f18-hunters-mark` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从 S0-F5 实际集成提交开工，复用 spell catalog、施法成本、专注和 reducer，不另建旁路法术。
2. Hunter’s Mark 的基础命中伤害、寻找检定优势、归零转移和专注实例必须真实存在，五项职业特性才有验收基础。
3. 当前标记必须绑定施法者与法术实例，不能用无来源全局 `marked` 条件让其他 Ranger 共享效果。
4. Foe Slayer 只把该角色自己的 Hunter’s Mark 从 d6 改 d10，不改武器、其他法术或全局 spell catalog。

## 2. 官方英文关键原文与页码

SRD p58，Ranger 1 级：

> **Favored Enemy.** You always have the Hunter’s Mark spell prepared. You can cast it twice without expending a spell slot, and you regain all expended uses of this ability when you finish a Long Rest.

> The number of times you can cast the spell without a spell slot increases when you reach certain Ranger levels, as shown in the Favored Enemy column of the Ranger Features table.

SRD p59，Ranger 13/17/18/20 级：

> **Relentless Hunter.** Taking damage can’t break your Concentration on Hunter’s Mark.

> **Precise Hunter.** You have Advantage on attack rolls against the creature currently marked by your Hunter’s Mark.

> **Feral Senses.** Your connection to the forces of nature grants you Blindsight with a range of 30 feet.

> **Foe Slayer.** The damage die of your Hunter’s Mark is a d10 rather than a d6.

SRD p141，Hunter’s Mark：

> **Hunter’s Mark** — Level 1 Divination (Ranger); **Casting Time:** Bonus Action; **Range:** 90 feet; **Components:** V; **Duration:** Concentration, up to 1 hour.

> You magically mark one creature you can see within range as your quarry. Until the spell ends, you deal an extra 1d6 Force damage to the target whenever you hit it with an attack roll. You also have Advantage on any Wisdom (Perception or Survival) check you make to find it.

> If the target drops to 0 Hit Points before this spell ends, you can take a Bonus Action to move the mark to a new creature you can see within range.

> **Using a Higher-Level Spell Slot.** Your Concentration can last longer with a spell slot of level 3–4 (up to 8 hours) or 5+ (up to 24 hours).

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| `spell:hunter_s_mark` 已存在于 spell catalog，source_ref 指向 p141；存在条目不表示效果已实现 | `content/spell_catalogs/srd_spells.json` |
| 当前 combat core 未核实 Hunter’s Mark 的确定性 effect mode | `content/combat_catalogs/srd_combat_core.json` |
| 施法经统一验证，处理成本、目标、射程与选择 | `combat/validation.py::_validate_spell` |
| 专注开始/结束已有事件，concentration 保存 spell_ref/target_ids | `combat/resolution.py::_spell_events` |
| 受伤专注豁免已有入口，Relentless 应在此处按 spell_ref 精确例外 | `combat/engine.py::_concentration_events` |
| 法术条件可携带 owner/source_spell_ref，结束条件可级联结束专注 | `combat/resolution.py::_spell_end_trigger_events`、`_spell_events` |
| 攻击命中与伤害 rider 有统一入口 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| 攻击模式支持按观察者能力抵消条件，但 Blindsight 必须增加 30 尺范围判断 | `combat/check_modes.py::attack_sources`、`attack_mode` |
| Favored Enemy 免费次数轨道已存在：1–4 为 2、5–8 为 3、9–12 为 4、13–16 为 5、17–20 为 6 | `content/progression_catalogs/srd_classes.json` |
| 长休恢复职业资源和法术位已有统一入口 | `resting/engine.py::complete_long_rest`、`_recover_class_resources` |
| Magic Initiate 免费施法是另一来源，不能直接冒充 Ranger 次数池 | `spells/feat_spells.py::feat_spell_grants`、`combat/triggered_spells.py::free_cast_resource` |

## 4. 精确实现契约

### 4.1 Favored Enemy 与法术实例

1. 增加五项 canonical feature declaration、等级/source_ref/双语 locale；Favored Enemy 使 Hunter’s Mark 总是作为 Ranger spell 准备，不占普通 prepared 数量。
2. 建立服务端 `favored_enemy` 次数资源，最大值严格取既有轨道 `[2×4, 3×4, 4×4, 5×4, 6×4]`；Long Rest 全部恢复，Short Rest 不恢复。
3. 免费施法不消耗 spell slot 但消耗一次资源；无免费次数时可正常消耗合法 slot。支付方式由服务端可选集提供，伪造 free cast 或负数次数拒绝。
4. 施法仍为 Bonus Action、90 尺、可见单一生物、V、Concentration；基础至多 1 小时，slot 3–4 为 8 小时、5+ 为 24 小时。
5. 在施法者 concentration 实例中保存 `spell_ref=spell:hunter_s_mark` 与唯一 `mark_target_id`；同一时间每个施法者只有一个当前标记。新专注法术按既有规则结束旧实例。
6. 施法者以任意 attack roll 命中当前标记时额外掷 1d6 Force；每回合不限次数，其他目标和其他角色的攻击不获得。寻找该目标的 Wisdom (Perception/Survival) 检定增加一个 Advantage 来源。
7. 当前目标降至 0 HP 后，可用 Bonus Action 将同一实例移至 90 尺内可见新目标；不重新掷施法、不再扣 slot/免费次数，不重置剩余持续时间，旧目标立即不再是当前标记。

### 4.2 高等级特性

1. **Relentless Hunter**：仅当 concentration 实例是持有者自己的 Hunter’s Mark 时，伤害不触发会破坏该专注的 Con save；伤害仍正常扣 HP。
2. 该例外不阻止 Incapacitated、死亡、施放另一专注法术、主动结束、持续时间到期或其他合法非伤害终止；也不保护其他法术。
3. **Precise Hunter**：持有者对“当前由自己的 Hunter’s Mark 标记”目标的所有 attack roll 增加一个 Advantage 来源；转移后旧目标立即失效、新目标生效，其他 Ranger 的标记无效。
4. **Feral Senses**：在 actor view 增加来源化 `blindsight: 30`；30 尺内按 Blindsight 处理 Invisible/Blur 等视觉条件抵消，31 尺及以外不得借该特性抵消。
5. Blindsight 不等于全图 Truesight，不忽略 Total Cover 或提供未定义侦测信息；攻击模式必须同时考虑观察者范围与其他来源。
6. **Foe Slayer**：仅持有者自己的 Hunter’s Mark 伤害公式由 1d6 改 1d10，每次命中仍只掷一枚；其他法术、武器、盟友的 Hunter’s Mark 和基础攻击骰不变。
7. 所有额外伤害经事件/reducer 和既有抗性边界结算；客户端不能选择 d10、指定当前标记或提交 Force 伤害。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`、`content/rest_catalogs/srd_recovery.json`。
3. `src/rulesets/dnd2024/combat/resolution.py`、`validation.py`、`check_modes.py`、`reducer.py`、`engine.py`、`runtime.py`。
4. `src/rulesets/dnd2024/features/resolver.py`、`features/combat.py`：仅本批资源/特性查询。
5. `src/rulesets/dnd2024/resting/engine.py`：仅 Favored Enemy 长休恢复的必要接入。
6. 新建 `tests/rulesets/test_dnd2024_hunters_mark.py`；本任务书仅追加执行报告。
7. 不改 spell catalog 的全局 d6 公式、progression 数值、前端或其他法术；前置接口不足时停止。

## 6. 编号测试矩阵

使用真实 Ranger 等级、spell slots、完整施法/攻击/休息链；SequenceRng 固定伤害，禁止手写 concentration、当前标记或免费次数。

1. 五项特性在 1/13/17/18/20 级准确授予，低一级无效果，source_ref 与双语标签正确。
2. Hunter’s Mark 总是准备且不占普通 prepared 数；非 Ranger 不因 catalog 条目自动获得 Favored Enemy。
3. 免费次数在 1/4/5/8/9/12/13/16/17/20 级分别为 2/2/3/3/4/4/5/5/6/6，升级不免费重置已消耗次数。
4. 免费施法扣一次资源不扣 slot；次数耗尽可合法用 slot；Long Rest 全恢复、Short Rest 不恢复。
5. 施法验证 Bonus Action、90 尺、可见单目标、V 和 Concentration；超距/不可见/多目标/伪造免费均拒绝。
6. 基础持续 1 小时，slot 3–4/5+ 分别 8/24 小时；实例保存 caster、spell_ref、唯一 mark_target_id。
7. 施法者每次以任意 attack roll 命中当前标记额外 1d6 Force，同回合第二次仍触发，其他目标不触发。
8. 其他角色攻击同一目标、其他 Ranger 的标记或无当前实例均不获得伤害；Force 抗性按既有边界结算。
9. 对当前目标的 Wisdom (Perception/Survival) 寻找检定有 Advantage，其他属性/技能/目标不误用。
10. 新专注法术结束旧 Hunter’s Mark 及其 Precise/伤害效果，不留下无来源标记。
11. 目标降至 0 后可 Bonus Action 转移至 90 尺内可见新目标，不扣 slot/免费次数、不重置持续时间。
12. 转移至超距/不可见目标、目标未归零时转移或无 Bonus Action 均拒绝；转移后旧目标立即失效。
13. 13 级 Relentless 使受伤不再触发 Hunter’s Mark 的专注破坏豁免，HP 伤害和其他伤害事件仍正常。
14. Relentless 不保护其他法术，也不阻止 Incapacitated、新专注、主动结束或到期终止 Hunter’s Mark。
15. 17 级 Precise 只对自己的当前标记攻击加 Advantage；其他目标/他人标记/寻找检定不误用。
16. 标记转移、专注结束、目标归零和 save/load 后 Precise 目标始终与权威实例一致。
17. 18 级 Blindsight 在 30 尺内抵消适用的 Invisible/Blur 视觉攻击效果，31 尺不抵消。
18. Blindsight 不变成 Truesight、不忽略 Total Cover，且与其他 senses/条件来源正确合成。
19. 20 级 Foe Slayer 仅自己的 Hunter’s Mark 每次命中改 1d10 Force；19 级仍 1d6。
20. Foe Slayer 不改武器骰、其他法术、盟友标记或全局 spell catalog；新施法/转移后仍正确。
21. 客户端伪造 mark_target_id、免费次数、d10、Blindsight 范围或专注例外均被拒绝/忽略。
22. 并发施法/转移、过期 version、重复 resolve 不产生双标记或重复资源扣减。
23. apply/replay、存档加载、长休恢复和战斗结束的 HP、slots、次数、标记、感官与 RNG 一致。
24. 既有 Ranger 施法、其他专注法术、Invisible/Blur、攻击、休息与战斗回归测试通过。

## 7. 非目标与停止条件

### 非目标

- Hunter 子职特性、Nature’s Veil、其他 Ranger 法术、通用标记 UI 或全图感官系统。
- 把 Relentless 写成所有专注免疫、把 Precise 写成全目标 Advantage、把 Foe Slayer 写成全局 d10。
- 无来源全局条件、多当前目标或客户端维护的 Hunter’s Mark 状态。

### 停止条件

1. S0-F5 未合入或 concentration 无法保存施法者拥有的唯一当前目标。
2. 总是准备/免费次数无法复用真实 spell 与 rest 边界，需要平行施法通道。
3. 受伤专注例外会影响其他法术，或非伤害终止无法保持。
4. 当前感官模型无法表达 30 尺 Blindsight，只能提供无限范围布尔抵消。
5. 需要白名单外文件、修改全局法术骰或客户端权威状态才能验收。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_hunters_mark.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features src/rulesets/dnd2024/resting/engine.py tests/rulesets/test_dnd2024_hunters_mark.py
git diff --check
```

## 9. 交付要求

1. 测试 1–24 逐项映射，展示免费次数轨道、转移不付费和专注例外边界。
2. 提供两名 Ranger 的来源隔离、30/31 尺 Blindsight 与 d6/d10 对照证据。
3. 报告专注事件、当前标记、资源恢复、RNG、存档和重放，不只展示 UI 文案。
4. 不得把 spell catalog 已有条目写成基线已实现；偏差/停止必须明示。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Favored Enemy 准备、资源与恢复：
- Hunter’s Mark 实例、伤害与转移：
- Relentless/Precise/Feral/Foe Slayer：
- 白名单文件对照：
- 测试 1–24 / 聚焦 / 回归 / 全量 / ruff / diff：
- 多 Ranger、存档、重放与 RNG：
- 偏差、停止原因与未决事项：
