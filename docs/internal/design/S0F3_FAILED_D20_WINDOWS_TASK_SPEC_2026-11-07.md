# S0-F3 任务书：失败 D20 后的重掷、替换与递增费用

| 项 | 值 |
|---|---|
| 切片编号 | S0-F3（独立新任务，不覆盖旧 SP） |
| 验收载体 | `tactical_mind`、`dark_ones_own_luck`、`countercharm`、`indomitable`、`disciplined_survivor`、`stroke_of_luck` |
| 通用能力 | 失败窗口、见骰后加值、优势重掷、强制新结果、天然 20 替换、短长休资源 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Fighter p47–48；Fiend p76；Bard p33；Monk p50、p52；Rogue p62–63 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-F1、S0-F2、S0-D2、S0-E2 已合入；失败检定与条件/豁免事件可判定 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得修改无关工作或另建绕过 D3/F2 已合入窗口注册主干的私有管线。
2. 完整实现六项特性，包括 Disciplined Survivor 的全豁免熟练；不得以目录、按钮或静态文案验收。
3. 失败、成功、天然骰、条件来源、Reaction、Focus/Second Wind/Indomitable 次数均由服务端核验。
4. 重掷必须产生真实新 d20 并使用新结果；替换必须修改天然骰并重新计算后续效果，不得只改显示。
5. 本任务新增 Dark One's Own Luck 的完整触发面与扣账，不假定祖先基线或 D3/F2 已有该特性实现，也不得注册同名第二套逻辑。

## 2. 官方英文关键原文与语义边界

SRD p48、p76：

> **Tactical Mind.** When you fail an ability check, you can expend a use of your Second Wind ... Rather than regaining Hit Points, you roll 1d10 and add the number rolled to the ability check ... If the check still fails, this use of Second Wind isn’t expended.

> **Dark One’s Own Luck.** When you make an ability check or a saving throw, you can use this feature to add 1d10 to your roll. You can do so after seeing the roll but before any of the roll’s effects occur.

> You can use this feature a number of times equal to your Charisma modifier (minimum of once), but you can use it no more than once per roll. You regain all expended uses when you finish a Long Rest.

SRD p33、p48：

> **Countercharm.** If you or a creature within 30 feet of you fails a saving throw against an effect that applies the Charmed or Frightened condition, you can take a Reaction to cause the save to be rerolled, and the new roll has Advantage.

> **Indomitable.** If you fail a saving throw, you can reroll it with a bonus equal to your Fighter level. You must use the new roll ... twice before a Long Rest starting at level 13 and three times ... at level 17.

SRD p52、p63：

> **Disciplined Survivor.** Your physical and mental discipline grant you proficiency in all saving throws.

> Additionally, whenever you make a saving throw and fail, you can expend 1 Focus Point to reroll it, and you must use the new roll.

> **Stroke of Luck.** If you fail a D20 Test, you can turn the roll into a 20. Once you use this feature, you can’t use it again until you finish a Short or Long Rest.

语义边界：Dark Luck 不要求先失败，但每掷最多一次；Countercharm 不是所有豁免的通用重掷，只针对会施加 Charmed/Frightened 的效果；Indomitable/Disciplined 的新结果可能更差仍必须使用；Stroke 改的是 d20 天然面值，不是“总值加 20”。

## 3. 已定点核实的代码事实

| 基线与前置事实 | 文件与函数 |
|---|---|
| D3/F2 前置提供统一窗口的注册、验证、资源消费和结束主干；具体签名以实际合入代码为准 | D3 新建的 `combat/check_windows.py` 与 F2 扩展；祖先基线没有该模块 |
| `dark_ones_own_luck` 尚未由 D3/F2 实现，本切片新增成功/失败原骰后的入口与每掷一次限制 | D3/F2 验收范围与本任务 §4.2；不得虚构旧 `_dool_owner` 接口 |
| 死亡豁免与重复豁免有真实结算入口，Dark Luck 必须接入且不能破坏光环等既有修饰 | `combat/resolution.py::_death_save_events`、`_save_repeat_events` |
| Second Wind、Indomitable 等有恢复声明，声明本身不证明失败后窗口完成 | `content/rest_catalogs/srd_recovery.json`；`resting/engine.py::_recover_class_resources` |
| 先攻与攻击、豁免各有真实 d20 结算入口 | `combat/resolution.py::_start_combat_event`、`_attack_events`、`_save_repeat_events` |
| 短休/长休分别恢复资源，Stroke 与 Indomitable 不能混用重置周期 | `resting/engine.py::complete_short_rest`、`complete_long_rest` |
| 派生 saving throws 由角色推导/升级重算，Disciplined 必须进入该入口 | `character/derivation.py`；`progression/advancement.py::_update_derived` |

## 4. 精确数据与行为契约

### 4.1 统一窗口与原子性

1. 每次 d20 roll 生成稳定 `roll_id`、阶段、原始骰、修正和效果状态；Dark Luck 记录该 `roll_id` 是否已使用，重掷产生新 `roll_id`，不得沿用一个计数造成歧义。
2. 初始骰可见且效果尚未发生时开放 Dark Luck；若修正后失败，再开放适用的失败窗口。若已转成功，不再提供 Tactical Mind、Indomitable、Stroke 等失败专属选项。
3. 多个可选特性按确定顺序逐个询问，每次只消费一个 pending 决策；取消、非法目标、资源不足或过期窗口均不写状态。
4. 扣资源、掷骰、替换/重掷、重判结果与后续效果在同一权威批次提交；异常或重复请求不得留下半次扣款。
5. 所有事件记录原 roll、加成骰或新骰、最终采用结果、资源变化和来源；RNG 请求顺序可重放。

### 4.2 各特性契约

1. **Tactical Mind（Fighter 2）**：仅失败 ability check；使用一次 Second Wind 但不执行回血，掷 1d10 加入本次总值。转成功扣一次，仍失败不扣；不得用于攻击/豁免或恢复 HP。
2. **Dark One’s Own Luck（Fiend 6）**：ability check 或 saving throw 在看见原骰后、效果前可加 1d10，无论原结果将成功还是失败；每 `roll_id` 至多一次，上限 `max(1, Cha)`，Long Rest 全恢复，不用于攻击或伤害。
3. **Countercharm（Bard 7）**：自己或 30 尺内生物对“会施加 Charmed 或 Frightened 的效果”豁免失败后，以 Reaction 重掷；新掷具有 Advantage 并强制采用。2024 文本不要求看见目标，不擅自增加视觉条件。
4. Countercharm 不用于仅造成伤害的豁免、已经处于 Charmed/Frightened 但本次效果不施加该条件的豁免、属性检定或攻击；只消费 Reaction，不虚构 Bardic Inspiration 成本。
5. **Indomitable（Fighter 9/13/17）**：失败 saving throw 可重掷并加当前 Fighter level 到新总值，强制新结果；每 Long Rest 分别 1/2/3 次，短休不重置。
6. **Disciplined Survivor（Monk 14）**：获得所有六项 saving throw 熟练并进入派生/升级重建；失败 saving throw 可花 1 Focus 重掷并强制新结果，不加额外等级或 PB 两次。
7. **Stroke of Luck（Rogue 20）**：失败 D20 Test 后把该 d20 天然面改为 20，保留合法修正并重判；按天然 20 触发相应自动成功/暴击等下游规则。每次 Short 或 Long Rest 恢复一次，不用于伤害骰。
8. Stroke 不请求新的 d20 RNG；天然 20 后不再是失败，不得继续打开失败窗口。Indomitable/Disciplined 重掷后若仍失败，可按各自合法窗口继续，但同一资源不得递归重复消费。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`rest_catalogs/srd_recovery.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/check_windows.py`、`check_modes.py`、`resolution.py`、`reducer.py`、`validation.py`、`view.py`。
3. `src/rulesets/dnd2024/character/derivation.py`、`progression/advancement.py`、`features/resolver.py`。
4. `src/rulesets/dnd2024/resting/engine.py`：仅本切片资源重置。
5. 新建 `tests/rulesets/test_dnd2024_failed_d20_windows.py`；可追加 `tests/rulesets/test_dnd2024_cutting_words.py` 的回归。
6. 需要修改通用世界引擎、前端规则或白名单外法术效果时停止。

## 6. 编号测试矩阵

1. 六项特性在正确职业/子职与等级授予；低等级、错误子职和伪造 owner 不可用。
2. Tactical Mind 仅在失败 ability check 开放，不用于攻击、豁免或成功检定。
3. Tactical Mind 掷真实 1d10、不回血；转成功扣 Second Wind，仍失败不扣且事件可重放。
4. Second Wind 为零、窗口取消或重复提交时不扣资源、不改变检定。
5. Dark Luck 在 ability check/save 的成功或失败原骰后均可加 1d10，且发生在原结果效果前。
6. Dark Luck 不用于攻击/伤害，每 `roll_id` 最多一次；上限按 Cha 最小 1，Long Rest 全恢复。
7. 死亡豁免与专注豁免可接入同一窗口主干且光环修饰不回归；Dark Luck 转成功后不执行原失败效果。
8. Dark Luck 后仍失败可继续合法 Bardic/失败窗口；转成功则失败窗口关闭。
9. Countercharm 只对自己或 30 尺内目标的 Charmed/Frightened 效果失败豁免开放，并消耗 Reaction。
10. Countercharm 新掷有两个 d20 的 Advantage 且强制采用；原结果被替代，距离/反应非法时不扣账。
11. 纯伤害豁免、非相关条件、属性检定或攻击不触发 Countercharm；不额外要求看见目标。
12. Indomitable 新 saving throw 加 Fighter level 且强制采用更差结果；不是加原骰或自由选择两结果。
13. Fighter 9/13/17 每长休分别 1/2/3 次，短休不恢复，次数耗尽后窗口关闭。
14. Disciplined Survivor 14 级后六项豁免均加一次 PB；升级/重建不重复叠加。
15. Disciplined 失败豁免花 1 Focus 重掷且强制新结果；Focus 0 不可用，不用于属性检定。
16. Stroke 对失败 attack/check/save 把天然骰改为 20，不额外掷 d20，修正与自动成功正确。
17. Stroke 对攻击可产生合法 Critical Hit 后续；不用于伤害、先成功结果或重复窗口。
18. Stroke 使用后 Short Rest 与 Long Rest 均恢复；未休息连续两次使用被拒绝。
19. 重掷后的新 `roll_id` 独立记录 Dark Luck 每掷一次限制，旧 roll 不得被再次结算。
20. 所有“必须使用新掷”均不得保留较优旧骰；原始与采用骰迹同时可查。
21. 多特性竞争时 pending 顺序确定，一次只消费一个决策，无递归窗口或重复效果。
22. 非法 roll_id、伪造条件来源、资源不足与过期窗口均 fail closed，战斗状态不变。
23. 存档/加载与重放后使用次数、roll_id、Reaction、Focus 和最终结果一致。
24. 既有 Lucky、Cutting Words、Bardic Inspiration、休息与豁免测试无回归。

## 7. 非目标与停止条件

### 非目标

- Tactical Master、Second Wind 本身回血改版、Perfect Focus、其他 Rogue/Monk 高阶特性。
- 任意检定无限重掷、客户端选择骰值、为 Countercharm 添加 2014 或自造限制。
- 只修改目录/静态豁免值而不接真实失败窗口。

### 停止条件

1. F1/F2/D2/E2 未合入，或窗口无法区分原 roll 与重掷 roll。
2. Charmed/Frightened 的效果来源不能在结算时权威判定，需客户端声明。
3. 资源退款、天然 20 下游或短长休无法原子且可重放地实现。
4. 需要白名单外文件或重写无关骰子系统。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_failed_d20_windows.py tests/rulesets/test_dnd2024_cutting_words.py -q
python -m pytest tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_advancement_access.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/character src/rulesets/dnd2024/progression src/rulesets/dnd2024/resting tests/rulesets/test_dnd2024_failed_d20_windows.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 统一 roll_id、窗口顺序与重放：
- Tactical Mind/Peerless 类退款交互：
- Dark Luck 触发面与每掷限制：
- Countercharm 条件来源与优势重掷：
- Indomitable/Disciplined/Stroke 数据：
- 短长休与原子扣账：
- 白名单文件对照：
- 测试 1–24 / 聚焦 / 回归 / 全量 / ruff / diff：
- 偏差、停止原因与未决事项：
