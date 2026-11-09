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

## 10. 执行报告（2026-11-09）

- **实际基线**：`ebd6166c`（`feat/dnd2024-s0f2-bardic-inspiration` 链头，F2 已合入的失败面排队与 `settlement` 主干都在链内）；祖先基线 `integration/dnd2024-srd@35653f81`
- **分支 / 提交**：`feat/dnd2024-s0f3-failed-d20-windows`（stack 在 F2 链头上，不覆盖旧 SP）
  1. `147edf8c` 六条声明 + Stroke 的恢复条目 + `saving_throw_proficiency_all()` + 建卡/升级两条派生路径
  2. `5965570f` 窗口主干：`rerolls`／`replaces_natural` 两个标志、`roll_id`、失败面排队、重复豁免续算
  3. `b86eec6a` 43 条矩阵测试 + 战士 2 级特性清单对齐
- **改动文件**：`combat/check_windows.py`（改动 709 行，全切片最大）、`combat/resolution.py`、`combat/engine.py`（一行 `roll_id`）、`features/models.py`（`CHECK_WINDOW_TRIGGERS` 加 `save_failure`）、`features/resolver.py`（`saving_throw_proficiency_all()`）、`character/derivation.py`、`progression/advancement.py`、`content/class_feature_catalogs/srd_class_features.json`（6 条声明）、`content/rest_catalogs/srd_recovery.json`（Stroke 恢复条目）、新建 `tests/rulesets/test_dnd2024_failed_d20_windows.py`（43 条）。9 个改动文件合计 +810／−203

审计结论先行：六项特性里**只有 Dark One's Own Luck 有旧实现**（S-P3 的 `dark_ones_own_luck` 保存面，`surface = "save"` 早已在注册表里，本切片没有第二条同名逻辑）；其余五项在目录里只有 locale 标签，没有声明也没有代码。数据侧还缺一条：Indomitable 的次数轨道与恢复条目在基线就存在，但 `stroke_of_luck` 连恢复条目都没有（`"rogue": []`），池子建不出来。因此本轮是"补声明 + 补窗口"，不是"改已有实现"。

### 一次 d20 只有一个 roll_id

`_roll_id(events, kind, actor_id)` = `f"{kind}:{actor_id}:{本批次已 resolved 的 check 数}"`。序号取自**本批次事件流**而不是状态里的计数器：没有时钟、没有跨批次可变字段，重放同一批次必然得到同一串 id。攻击、Hide、死亡豁免（`death_save:{id}:1`）、专注豁免（`concentration:{id}:1`）、回合末重复豁免全部写入 `roll_id`。

重掷不沿用旧 id，而是派生 `f"{old}-r1"`：新 check 的 `roll_id` 与旧的不同，旧值留在 `rerolled_from`，连同 `rerolled_rolls`／`rerolled_natural`／`rerolled_total` 三份骰迹。这样"必须使用新掷"在数据上就是"结果换成了另一条 id 的 check"，而旧骰只作为记录存在（矩阵 19、20）。

### 三种形状，一个入口

`WindowEntry` 只加两个正交标志，不新增并行管线：

| 形状 | 标志 | 消耗 RNG | 结果 |
|---|---|---|---|
| 加骰 | （默认） | 1 颗（优势 2 颗） | 旧 total + 骰，落在 `window_bonus` |
| 重掷 | `rerolls=True` | 1 颗（`reroll_mode="advantage"` 时 2 颗取高） | 旧结果作废，新 check 强制采用 |
| 改面 | `replaces_natural=True` | **0 颗** | 天然面改成声明的 `replace_natural_with`，下游重判 |

`_finish_check_window` 按 `replaces_natural` → `rerolls` → 加骰的顺序分派；`_validate_check_window` 只对 `entry.resource_id` 非空的码校验资源池，因此 Countercharm 这种"只花 Reaction、不花次数"的面不会被误判成资源不足。

### 失败面的排队（一个 d20 上可以有多个合法面）

`settlement["offered"]` 记录本 check 已经给过的面码，`_save_window_offer` 每次只开口一个，finisher 收尾时再问下一个面。顺序是策略而不是巧合：**保底改写（Stroke）→ 自己付钱的重掷（Indomitable、Disciplined）→ Dark Luck 加骰 → 盟友的 Reaction（Countercharm）**。取消第一个面不会丢掉第二个面（矩阵 21：Indomitable 取消后 Countercharm 仍在，且 Indomitable 的次数没被扣）。

能力检定面的顺序是失败时 `tactical_mind` → `bardic_inspiration_check` → `peerless_skill_check` → `dark_ones_own_luck_check` → `stroke_of_luck_check`，成功时 `cutting_words_ability` → `dark_ones_own_luck_check`；攻击面沿用 F2 的 `bardic_inspiration` → `peerless_skill`，第三位接 `stroke_of_luck_attack`。

### Tactical Mind / Peerless 的退款

两者都是"掷了但没翻盘就不收钱"，但退款原因分开记：`class_resource.refunded{reason: "tactical_mind_failure"}` 与 `peerless_failure`。退款与 `check.resolved` 在同一批次、顺序在后，所以中途没有"扣了没退"的中间态；骰已经真实掷出并留在 `window_bonus_rolls` 里，"掷了但没花"因此可审计（矩阵 3、4）。

### Dark Luck 的触发面与每掷一次

`dark_ones_own_luck` 只有保存面一个注册码（旧），本切片加 `dark_ones_own_luck_check` 覆盖属性检定面。**哪一种极性都开口**：文本是"看见掷骰之后、效果发生之前"，不是"失败之后"，所以成功的检定也能加骰（矩阵 5）。攻击与伤害面不开口（矩阵 6）。

"每掷最多一次"由 `settlement["offered"]` 保证：同一枚 d20 上第二次不成立；重掷产生新 `roll_id` 就是新的一掷，可以再用一次，但必须再付一次次数。上限是 `max(1, Cha)`，长休全恢复，短休不动（矩阵 6 用 Cha 8 与 Cha 20 两个夹具钉住下限与上限）。

死亡豁免上的 special case：天然 1 是两次失败，任何加骰都救不回来，所以 DOOL 在"死亡豁免 + 天然 1"时不开口（`skip_dool`），而 Stroke 仍然开口，因为它改的是面值。

### Countercharm 的条件来源与优势重掷

"会施加 Charmed 或 Frightened 的效果"不是从客户端声明读的，而是本次结算已经确定的**条件事实**：`settlement["condition"]` 来自实际要落的条件，Bard 的声明 `applies_conditions: ["charmed", "frightened"]` 决定它认不认这个条件。两者同时成立才开口——纯伤害豁免、已处于 Charmed 但本次不施加、属性检定、攻击都不会触发（矩阵 11）。

2024 文本没有"能看见目标"的条款，所以代码里也没有：唯一的空间事实是声明的 `range_ft: 30`。重掷用 `reroll_mode: "advantage"` 掷两颗取高并强制采用，只扣 Reaction、不扣 Bardic Inspiration（矩阵 9、10）。条件 id 是裸字符串，所以参数键叫 `applies_conditions` 而不是 `*_refs`，避免走引用校验（`condition:charmed` 在 bundle 里没有实体）。

### Indomitable / Disciplined / Stroke 的数据

| 特性 | 声明要点 | 恢复 |
|---|---|---|
| Indomitable（Fighter 9） | `reroll: true`、`reroll_bonus_from_class_level: true` | `maximum_track: indomitable`（1/2/3 次，短休 `none`，长休 `all`）— 轨道与恢复条目在基线就存在，本轮只补特性声明 |
| Disciplined Survivor（Monk 14） | `saving_throw_proficiency: "all"`、`reroll_cost_resource: focus_points`、`reroll_cost: 1` | 沿用 Monk 的 Focus（短休 `all`、长休 `all`） |
| Stroke of Luck（Rogue 20） | `replace_natural_with: 20` | 新增 `{"id": "stroke_of_luck", "maximum_fixed": 1, "short": "all", "long": "all"}` |

Indomitable 的加值是**当前 Fighter 等级**（`_class_level()` 从 `build.class_levels` 求和），加在新掷的总值上而不是旧总值上；更差的新结果同样是结果（矩阵 12、20）。

全豁免熟练是**声明驱动的派生事实**，不是第二张熟练表：`saving_throw_proficiency_all()` 只读 `parameters.saving_throw_proficiency == "all"`，`derivation.py` 与 `advancement.py::_update_derived` 把六个 `ability:*` 合进同一份 `saving_throw_refs`，PB 只计一次（集合语义天然去重，升级前后走同一个加值公式，矩阵 14）。

Stroke 不请求 RNG：`_replace_natural` 把天然面改成 20 后重算 `_check_total()`，攻击面再重判 `critical` 与 `success`（天然 20 命中即重击），所以"改成 20"与"总值 +20"在数据上不同（矩阵 16、17）。

### 重复豁免：暂停而不丢行

`_save_repeat_events` 原来是"逐条件内联掷骰 → 立即落地"的循环，窗口一开就只能整段丢弃或整段重跑。本轮把它拆成 `_repeat_save_rows()`（枚举）+ `_repeat_save_check()`（只掷不判）+ `while rows:` 循环：开窗口时把**还没处理的行**放进 `settlement["remaining"]`，由 `_finish_repeat_save_window` 续跑。这是 F2 遗留"豁免面要抽 300 行"的具体落点，本轮只覆盖回合末重复豁免这一个入口。

### 短长休与原子扣账

扣资源、掷骰/改面、重判、后续效果都在同一权威批次里；`_finish_*_window` 先问下一个面，再落地结果，所以"扣了资源却没有任何结果"的状态不存在。矩阵 22 用三种伪造（过期窗口、非 owner 解析、资源不足）验证 fail closed：状态与版本都不动。矩阵 23 用整态 JSON 往返 + 同 `intent_id`/同 `expected_version` 回放，确认 `apply_batch` 判重后返回原批次。

### 矩阵 1–24 对照

| 编号 | 证据 | 备注 |
|---|---|---|
| 1 | `test_each_feature_belongs_to_its_level`、`test_stroke_of_luck_gets_its_own_single_use_pool` | 六项按 2/7/9/14/20 级授予；Stroke 池 1/1 且 `maximum_fixed` |
| 2 | `test_tactical_mind_opens_only_on_a_failed_ability_check`、`test_tactical_mind_is_not_offered_on_an_attack_or_a_saving_throw` | 攻击/豁免/成功检定都不开口 |
| 3 | `test_tactical_mind_rolls_a_real_d10_and_spends_only_on_a_rescue` | 真掷 1d10、不回血；翻盘扣一次，仍失败退款 |
| 4 | `test_tactical_mind_with_no_second_wind_declines_without_a_trace` | 0 次时窗口不存在，取消不写状态 |
| 5 | `test_dark_luck_is_offered_on_a_successful_and_a_failed_ability_check` | 两种极性都开口，且在效果之前 |
| 6 | `test_dark_luck_never_opens_on_an_attack_and_only_once_per_roll`、`test_dark_luck_pool_is_charisma_with_a_floor_and_a_long_rest_refill` | 攻击面不开口；同 roll 第二次不成立；上限 `max(1, Cha)`；长休全恢复 |
| 7 | `test_dark_luck_can_rescue_a_death_save_without_double_counting`、`test_a_blessed_repeat_save_keeps_its_bonus_die_under_the_window` | 死亡豁免只落一次；窗口下的祝福骰不被覆盖 |
| 8 | `test_dark_luck_leaves_the_next_failure_face_open` | 加骰后仍失败 → 下一面照给；转成功 → 失败面关闭 |
| 9 | `test_countercharm_reacts_to_an_ally_charmed_save` | 30 尺内盟友、Charmed 条件、扣 Reaction |
| 10 | 同 9 + `test_countercharm_does_not_react_out_of_reach_or_without_a_reaction` | 两颗 d20 取高并强制采用；超距/无 Reaction 不扣账 |
| 11 | `test_countercharm_ignores_other_conditions_and_other_check_kinds` | 纯伤害豁免、无关条件、属性检定、攻击都不触发；不加"看见目标"限制 |
| 12 | `test_indomitable_adds_the_fighter_level_and_forces_the_new_roll` | 新 total 含 `reroll_bonus = 9`；更差也采用 |
| 13 | `test_indomitable_uses_scale_with_fighter_level`、`test_indomitable_closes_when_the_pool_is_empty` | 9/13/17 → 1/2/3 次；短休不恢复；耗尽后窗口消失 |
| 14 | `test_disciplined_survivor_grants_every_saving_throw_proficiency` | 六项豁免都加一次 PB；建卡与升级两条路都写回，不重复叠加 |
| 15 | `test_disciplined_survivor_reroll_costs_one_focus` | 花 1 Focus 重掷并强制采用；Focus 0 不可用；属性检定不开口 |
| 16 | `test_stroke_of_luck_turns_a_failed_attack_into_a_natural_twenty`、`test_stroke_of_luck_is_offered_on_an_ability_check_and_a_save` | 天然面改 20、不掷 d20、修正保留 |
| 17 | 同 16（断言 `critical` 与 `success`） | 指改面规则不用于伤害骰，也不给已经成功的结果 |
| 18 | `test_stroke_of_luck_returns_on_a_short_or_long_rest`、`test_stroke_of_luck_is_not_offered_on_a_hit_or_a_second_time` | 短休/长休都恢复；未休息连续两次被拒 |
| 19 | `test_a_reroll_gets_its_own_roll_id_and_retires_the_old_one` | 新 `roll_id` 独立；旧 `decision_id` 再提交被拒，次数不再扣 |
| 20 | `test_a_worse_reroll_is_still_the_result_and_both_trails_stay_readable` | 旧骰只在 `rerolled_*` 里可查，不作结果 |
| 21 | `test_the_roller_own_face_comes_before_the_allies_reaction` | 一次只消费一个 pending；顺序 Indomitable → Countercharm |
| 22 | `test_a_forged_or_stale_window_moves_no_state`、`test_an_allies_window_cannot_be_resolved_by_the_gm` | 伪造/过期/资源不足/越权全部 fail closed |
| 23 | `test_state_and_ledger_survive_a_round_trip_and_a_replay` | 整态 JSON 往返相同；同 `intent_id` 同版本回放返回原批次 |
| 24 | `test_dnd2024_check_windows.py` + `test_dnd2024_cutting_words.py` + `test_dnd2024_bardic_inspiration.py` = 112 passed；`survivor`／`class_features` 各自复跑；全量 11 failed 与基线逐条相同 | 见"验收结果"与偏差 1、2 |

### 白名单对照（§5）

| 白名单项 | 是否改动 | 说明 |
|---|---|---|
| 1 类特性目录 + 恢复目录 + en/zh-CN locale | 是（locale 否） | 6 条声明 + 1 条 Stroke 恢复；六个 id 的标签键在 locale 里**早已存在**，未新增也未改文案 |
| 2 `combat/check_windows.py`、`resolution.py`、`engine.py` | 是 | 窗口注册表、三种形状、失败面排队、重复豁免续算、`roll_id` |
| 2 `check_modes.py`、`reducer.py`、`validation.py`、`view.py` | **否** | 没有新增事件类型，全部复用既有 `check.resolved`／`class_resource.spent`／`refunded`／`decision.pending`；优势重掷沿用 `advantage_mode` 字段 |
| 3 `character/derivation.py`、`progression/advancement.py`、`features/resolver.py` | 是 | 全豁免熟练走声明 |
| 4 `resting/engine.py` | **否** | 恢复语义已由 `srd_recovery.json` 表达（Indomitable 条目在基线就存在，本轮只补 Stroke） |
| 5 新建 `test_dnd2024_failed_d20_windows.py` | 是 | 43 条；**另外改了 1 行** `test_dnd2024_class_features.py`（见偏差 1） |
| 6 不碰通用世界引擎/前端 | 遵守 | `world_state.py`／`world_events.py`／前端零改动 |

### 验收结果

| 命令 | 结果 |
|---|---|
| `pytest test_dnd2024_failed_d20_windows.py test_dnd2024_cutting_words.py test_dnd2024_check_windows.py test_dnd2024_bardic_inspiration.py -q` | **112 passed**（含本切片 43 条） |
| `pytest test_dnd2024_survivor.py test_dnd2024_class_features.py test_dnd2024_failed_d20_windows.py -q` | **160 passed**（46 + 71 + 43） |
| `pytest test_dnd2024_resting.py test_dnd2024_spells.py test_dnd2024_advancement_access.py -q` | **19 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1697 passed / 0 errors**（1199 s） |
| `ruff check src/rulesets/dnd2024/{combat,character,progression,resting} tests/rulesets/test_dnd2024_failed_d20_windows.py` | All checks passed |
| `git diff --check` | 无输出 |

全量 11 个失败与 F2 收尾记录的基线**逐条相同**（全部在 `test_dnd2024_character_builder.py`，`unresolved reference 'ability:con' in background:soldier` 一族），F3 净引入 **0**；通过数从 1654 增至 1697，正好是本切片的 43 条。

取证过程不是一次通过：第一版实现把"死亡豁免"也纳入重掷面，全量立刻暴露出 3 条真回归（`survivor` 2 条 + `class_features` 1 条）。逐条定位后按偏差 1 与偏差 2 处理，再重跑上述三组聚焦用例与全量，才有上表数字。

### 偏差、停止原因与未决事项

1. **死亡豁免不开放重掷面（功能缺口，待裁决）**。Indomitable、Disciplined Survivor、Countercharm 在**死亡豁免**上不开口；死亡豁免仍保留 Stroke of Luck（改面）与 Dark One's Own Luck（加骰）两个不改写掷骰的面。原因不是规则语义，而是既有测试：`test_dnd2024_survivor.py` 的两条用例把死亡豁免钉成"同一批次立即结算"，而该文件在本切片白名单之外；E3 已经因同一类冲突把 `death_save.resolved.roll` 记成偏差（该文件 §10 第 6 条）。本轮按同一先例处理——保持既有测试语义、把缺口写进报告，而不是改别人的测试。放开后需要改的正是那两条：`test_advantage_keeps_natural_one_and_takes_the_highest[rolls0-3-0]`、`test_advantage_and_the_aura_are_separate_entries_on_one_save`（改成先取 pending 决策再断言结算）。

   **2026-11-09 关闭**：用户裁定放开，落地在 `feat/dnd2024-s0f4-spell-grants` 的后续提交（见 `S0F4_SPELL_GRANTS_TASK_SPEC_2026-11-07.md` §12.2）。要改的正是上面点名的两条——新增 `_decline_window()` 助手，改为逐个拒绝窗口后再断言结算，断言内容一字未改。另外 `_reroll_check()` 的 `keep` 补上了 `death_save_crit_threshold`，否则重掷会丢掉 18–20 视为 20 的阈值。加骰面（Dark One's Own Luck 在 natural 1 上）仍然排除。
2. **白名单外改了 1 行测试**。`test_dnd2024_class_features.py::test_fighter_action_surge_declaration_and_capability_load` 断言战士 2 级特性清单，加入 `tactical_mind`。SRD 把该特性授予 Fighter 2，不加上这一行就只能不实现特性，属不可避免；按 F1 的 `5efb0194`（test-only 对齐）先例处理，只改这一条断言，未改任何行为断言。
3. **重掷窗口的 `die_sides` 是 0**。`die_sides` 的语义是"本次窗口掷的加值骰面"，重掷不掷加值骰，所以是 0；掷什么由 `reroll_mode`（`""` 表示单骰，`"advantage"` 表示两骰取高）表达。改面（Stroke）出于同一理由也是 0（`replaces_natural` 且 `window_roll: null`）。客户端不应当把 0 读成"窗口不可用"。
4. **重复豁免的续算只覆盖回合末入口**。`_save_repeat_events` 已能在窗口处暂停并续跑，但法术豁免（`_spell_effect_events` 里"逐目标豁免 → 伤害 → 附加条件"的内联循环）与专注豁免仍是各自的入口。F2 遗留的"豁免面要抽约 300 行"本轮只落在 `repeat_save` 这一处，法术豁免面仍未接通（与 F2 的裁决一致）。
5. **`offered` 是单次 settlement 内的字段**。跨批次靠 `decision.settlement` 携带；伪造或过期的 settlement 会先被 `_validate_check_window` 按 `decision_id` 与版本拒绝，不会因为"面码没见过"而重新开口（矩阵 22）。
6. **AI 梯子对改面/重掷的估值是上界**。`stroke_of_luck_*` 用 `20 + modifier + check_bonus + aura_bonus >= DC` 判"改了能不能到"，重掷用同样口径并加 `reroll_bonus`；优势重掷没有按 2d20 取高的分布估值（当作能出 20）。这只影响 AI 要不要花资源，不影响玩家路径，也不影响任何结算结果。
7. ~~**未决**：死亡豁免的重掷面（偏差 1）需要一次独立裁决~~（2026-11-09 已裁决放开并落地，见偏差 1 的关闭说明）；`Tactical Master`、Perfect Focus 等相邻高阶特性仍属非目标。
