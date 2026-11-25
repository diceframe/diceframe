# S0-F28 执行报告：Innate Sorcery、Metamagic 十选项与术士高阶能力

| 项 | 值 |
|---|---|
| 任务书 | `S0F28_METAMAGIC_TASK_SPEC_2026-11-07.md` + 修订稿 `S0F28_SPEC_REVISION_2026-11-23.md` |
| 状态 | 工作树，未提交、未开 PR |
| 验证 | 全量 `tests/rulesets` **2739 passed**（16:36）；`ruff` / `mypy` 干净；`test_dnd2024_metamagic.py` **16 passed** |

## 1. 交付

| 切片 | 内容 |
|---|---|
| 容器 | `spells/metamagic.py`：十项声明（cost / exclusive / parameter / requires）、`plan()`（名额 1+Incarnate、Apotheosis 按"最贵的一项"免费、逐项独占）、`effective_components()` |
| 校验 | `_validate_metamagic` 与结算共用同一个 `plan()`；失败即拒、不扣点；Empowered 重掷数 ≤ CHA 调整值；Careful 目标数 ≤ CHA 调整值且必须是本次法术的目标 |
| 结算 | 扣点 `sorcery_points`、`metamagic.applied` / `free_used` / `rerolled` 三个审计事件 |
| Innate Sorcery | 附赠动作；有次数扣 1、否则 7 级扣 2 术法点；DC+1 与法术攻击优势都只对"术士法术"（`class_refs` 判定），豁免事件带 `dc_base` / `innate_sorcery` 供对账，法术行投影 `innate_sorcery_applies`；长休 2 次 |
| 十选项 | Careful（自动成功 + **半伤归零**）、Distant（Touch→30、其余翻倍、Self 不变）、Empowered（重掷最低 N 颗）、Extended（时长翻倍 + 专注优势）、Heightened（单目标劣势）、Quickened（成本换成附赠动作）、Seeking（未命中重掷并强制采用）、Subtle（有效成分，**施法与反制共用**）、Transmuted（六种元素互换）、Twinned（有效环阶 +1 的目标数） |
| 升级 | 2/10/17 各选 2 个（`metamagic_option_ids`，可选提交）；每次升级可换 1 个（`metamagic_replace`） |

## 2. 评审后修掉的（第一轮独立评审，15 条）

| # | 问题 | 后果 |
|---|---|---|
| 1 | `_feature_character` 不带 `conditions` | 校验里 Incarnate/Apotheosis **恒为假**：双名额与免费额度整体失效，且与结算侧口径分裂 |
| 2 | `effective_components` 无人调用 | Subtle 花 1 点买不到任何东西；无声施法仍会打开反制窗口 |
| 3 | Careful 只"自动成功"，没做"半伤归零" | 选中的盟友在火球上仍吃半伤 |
| 4 | Careful 目标数无上限、不校验是否本次目标 | 1 点可让任意多个生物自动成功 |
| 5 | Transmuted 不校验原类型是否在六种内 | Radiant/Necrotic 也能被改成火焰 |
| 6 | Distant 把 Touch（数据里是 `range: 5`）翻成 10 尺 | 触及法术射程错误 |
| 9 | Apotheosis 实现成"总价减 1 点" | Heightened（2 点）仍扣 1 点，与"一个选项免费"不符 |
| 10 | 触发型法术接受超魔但静默丢弃 | 玩家扣了点、什么也没发生 |

## 2b. 第二轮：把"是否必选"提到升级引擎，并抽象时长

### 2b.1 通用必填规则（评审第 11 条的正确修法）

原实现每条特性自己回答"这个槽不选会怎样"，结果是同一件事写了很多遍、还答错过。
现在改为**升级引擎统一判定**：

- requirement 带 `count` 且没有 `optional` → 未填即报错，不能确认升级；
- 已经由自己的 planner 判过的槽位（`feature_choice`、`spell_selection`、
  `ability_increase`）标 `enforced: True`，通用规则不重复判、避免两套文案；
- 真正可选的只有"替换"这类（原文 "can replace"），显式标 `optional`。

特性侧因此只剩"提供哪些槽"，不再回答"必不必填"。第一版把
`feature_choice` 也扫进去，导致 400+ 条既有用例失败（它们的槽位 id 是
`feature_choice:class:bard:expertise:...`），修正为 `enforced` 标记后收敛。

### 2b.2 时长抽象（评审第 7 条）

新增 `spells/duration.py`：`base()` 从目录取基础值（`duration_rounds` /
`condition_duration` / `mark_duration_hours` / 法术原文），`effective()` 在其上
叠加**升环增量**（`upcast_duration_rounds|minutes|hours`）与 Extended 翻倍（24 小时上限），
区域、条件、标记三条路径都读同一个函数。

关键一点：**无法换算的代码原样带回**（`target_turn_start`、`caster_next_turn_end`、
`concentration`、`until dispelled`）。第一版把它们当成 24 小时，结果
"到你下一回合开始"的减速与"无法回血"变成了真正的一天，两条既有用例立刻抓到。

## 2c. 前端：升级面板接上两种 kind

后端把超魔选择改成必填后，面板不接就等于"2 级术士点不了升级"。本轮补上：

- `kind: "metamagic"` → 复选框组，上限来自后端 `count`，选满即禁用其余项；
- `kind: "metamagic_replace"` → 两个下拉（换掉 / 换成），留空即不换（替换是可选动作）；
- 提交键分别是 `choices.metamagic_option_ids` 与 `choices.metamagic_replace{drop, add}`。

验证：`vitest tests/Dnd2024AdvancementPanel.test.ts` 10 passed（新增一条"选满两个后第三个禁用"
并断言提交里带 `metamagic_option_ids`）；`eslint src tests --max-warnings 0` 与 `vue-tsc -b` 均干净。

## 3. 未修（明确记录）

| # | 问题 | 为什么先空着 |
|---|---|---|
| 14 | Twinned 数据两种形态（`upcast_target_bonus` 布尔标记 / `upcast_target_count` 目标数） | `_check_requires` 两种都认，当前数据只用后者；没有第二种形态的样本可测 |

### 3a. 上一版记账里清掉的三条（替换通道）

| 原问题 | 现在的做法 |
|---|---|
| 替换缺端到端用例 | 新增 3 条用例：3→4 级预览→提交→写入 `known`；9→10 级同批撞车被拒；9→10 级换掉本批新选项后写入结果正确 |
| 替换不查同批次重复 | 校验以"本批选择已写入"后的集合判 `add`：同批新选项不能再当 `add`，否则一级结束时列表里会出现两个同名选项 |
| `metamagic_replace.known` 只取当前角色 | 改用 `known_after_picks(known, picked)`：先选后换与先换后选给出同一组下拉，预览刷新不会再换一批候选 |

替换的校验与写入现在共用 `spells/metamagic.resolve_replacement`：preview 允许什么，apply 就写什么，不再两侧各写一遍——上一版 apply 侧遇到不认识的 `drop` 是静默跳过，正是"预览过、写入丢"的来源。

### 3b. §4.5 的一次返工：法表判定不是施法门槛

第一版把"术士法术"这条读成了**施法前置**：在 `_validate_spell` 里加
`_require_class_spell_list`，把职业法术的 `class_refs` 与「本职业 ∪
`prepared_list_expansion`」求交，空交集就拒绝并报
`"spell is not on your class spell list"`；为此还把 `prepared_list_expansion` 从
`spells/selection.py` 提到 resolver。

这个方向是错的。Innate Sorcery 的两个增益限定"你的术士法术"，限定的是**增益的适用面**，
不是"能施放什么"：施法合法性在选法术时就由 `catalog.list_for_class` /
`catalog.require` 判过，施法口只该认"角色拥有这个法术"。再判一次法表会把多职业、
feat/物种授予、扩展法表这些**合法的跨表来源**一起挡回去，且拒绝面与原文毫无关系。

已回退的部分（`validation.py`、`features/resolver.py`、`spells/selection.py` 全部回到基线）：

- 删除 `_require_class_spell_list` 及其调用点；
- 删除 resolver 上的 `prepared_list_expansion`，`spells/selection.py` 恢复原实现。

改成做的事（这才是原文要求的那条）：

| 位置 | 改动 |
|---|---|
| `combat/resolution.py::is_sorcerer_spell` | 保持唯一权威：读 `class_refs` 含 `class:sorcerer`，与施法者等级、职业行无关 |
| `combat/resolution.py::_spell_save_dc` | DC +1 的判定点，唯一入口 |
| `combat/resolution.py` 法术攻击分支 | 优势来源 `innate_sorcery` 只在判定为真时加入 |
| `combat/resolution.py` `save_repeat` 快照 | 原来读 `actor["spell_save_dc"]`（基础值），首次豁免 +1、后续豁免不 +1；改为 `_spell_save_dc(actor, spell)`，两处同口径 |
| `combat/resolution.py` 豁免事件 | 记 `target`（实际 DC）与 `dc_base`（基础 DC），+1 生效时带 `innate_sorcery: true`；不开加成时两个键都不出现 |
| `combat/view.py` 法术行 | 投影 `innate_sorcery_applies`（状态激活且判定为真），面板能解释同一角色为何有的法术 +1、有的不 +1 |

新增 5 条用例（`test_dnd2024_metamagic.py`），用`spell:burning_hands`（术士表）
与 `spell:vicious_mockery`（仅吟游诗人表）、`spell:fire_bolt`（术士）与
`spell:eldritch_blast`（仅邪术师表）做对照：

1. 非术士表法术**照样能施放**——拒绝面只剩"是否拥有"，`"spell is not on your class spell list"` 不再出现；
2. 术士表法术 `target == dc_base + 1` 且带 `innate_sorcery`；非术士表法术两者都不带；
3. 攻击优势同口径：`advantage_sources` 含 `innate_sorcery` 的只有术士表法术，另一条 `advantage_mode == ""`；
4. `sleep` 的 `save_repeat.dc` 与首次豁免的 `target` 相等（同一次施法不留 1 点差）；
5. 法术行 `innate_sorcery_applies`：激活后术士表 true、非术士表 false，未激活时全 false。

第一条同时是对第一版实现的反向证明：把这条法术塞进准备列表后仍能施放，说明施法口确实
没有法表门槛；若哪天有人再把那道门加回来，这条用例会立刻变红。

## 4. 已知语义取舍

1. **Empowered 由服务端重掷"点数最低的 N 颗"**，不把骰面发给客户端再回收选择：服务端保持唯一掷骰者，且"挑最低"是玩家看到骰面后的理性选择。取舍写在 `_damage_roll` 的 docstring 里。
2. **Subtle 只保留"有价格/被消耗的 M"**，其余成分全部抹掉；反制窗口读同一个 `effective_components`。
3. **Twinned 不扣高环槽**：只把有效环阶 +1 用于目标数。

## 5. 下一步

§3 只剩 Twinned 的两种数据形态一条，不阻塞本切片交付。十项、容器、升级通道（选择 + 替换）、
Innate 的适用面（判定 + 对账 + 视图）、前端面板均已接好并有回归。

## 6. 第四轮（2026-11-24）：独立审一遍，按真实目录数据核对

前几轮的用例大多用人造 `spell` / `effect` 字面量，因此"字段读错"这类缺陷不会
现形。本轮改成拿目录里的真实数据回放，发现三处只读了半个答案的地方：

| 症状 | 根因 | 处理 |
|---|---|---|
| Extended Spell 对专注法术完全失效：`as_condition_text()` 返回空串，既不翻倍也不保留"由专注结束" | Hex 的 effect 只写 `condition_duration: "concentration"`，长度在 spell 的 "up to 1 hour" 里；`base()` 读到 code 就短路，`effective()` 重建 `Duration` 时又丢掉 `raw` | 三个字段各答各的问题：`duration_rounds` = 推进单位、`condition_duration` = 结束规则、spell 的 duration 文本 = 上限；`effective()` 透传 `raw` |
| Fog Cloud 的区域只有 10 轮（原文 "up to 1 hour"），提前五十九分钟消失 | `duration_rounds: 10` 被当成总长度 | `base()` 在 rounds 分支里与 spell 的上限取长，`Duration` 新增 `total_rounds` 统一三单位 |
| Distant Spell 对 Self 法术扣 1 点、什么也没变 | 数据里 Self 也带 `range` 数值（Burning Hands 锥长 15、Shield 5），`range_min_ft: 5` 放行；结算期又因原文是 Self 原样返回 | 门禁先按原文排除 Self |

另外去掉 `rounds // 10 or 1`：三轮被写成 `1_minute`，条件会多撑两个回合。现在只有
整十轮才有等值的分钟写法。

### 6.1 一条踩过的边界：`as_condition_text()` 不能"长度优先"

修完前两条之后，"补上限"看起来应该一路补到 `as_condition_text()`——让 Hex 的
`condition.applied.duration` 从 `concentration` 变成 `1_hour`。**这是错的**，而且
第一批回归当场抓住：

```
test_dnd2024_hold_person:  assert '1_minute' == 'concentration'
test_dnd2024_stoneskin:    assert '1_hour'   == 'concentration'
```

那个字符串是 reducer 判断"条件何时消失"的依据（`duration in
_TURN_SCOPED_DURATIONS`、`== "combat"`、`== "target_turn_start"`）。写成
`1_hour`，条件就会在专注断开之后继续站着——把"由专注结束"降级成"一小时后结束"。

所以三者各归各位：`as_condition_text()` 答**怎么结束**（`raw` 优先），`hours` /
`total_rounds` 答**能持续多久**，区域推进读 `rounds`。两条失败用例各留了一条断言
锁住这个顺序。

### 6.2 本轮未修，需要单独决策

| 项 | 说明 |
|---|---|
| **Twinned 的"有效环阶 +1"** | §4.3 记的是"只把有效环阶 +1 用于目标数"。但任务书 §4.4 第 10 条要求"由既有升环规则计算目标和**所有有效等级效果**"。两者矛盾：当前 Cure Wounds 被 Twinned 后治疗量不变。要修得让"有效环阶"贯穿伤害骰与升环时长，属于独立一刀，不宜搭在修 bug 的提交里。 |
| **十选项清单有两份** | `spells/metamagic.OPTIONS` 与 `progression/advancement.METAMAGIC_OPTION_IDS` 各硬编码一遍，后者注释还写着"运行时权威在 OPTIONS"。这正是 `ENGINEERING_RULES` §25 要消除的形态。 |
| **2/10/17 各给 2 个选项** | 数据、`metamagic_choices_at`、测试三处一致，所以不是内部矛盾；但总数 6 与我对 2024 PHB 的印象（各 +1，总数 4）不符，需对 SRD 5.2 原文确认。 |
| `_check_requires` 的死参数 | 收了 `cast_level_one_plus_this_turn` 只为 `del` 掉它。 |
| `Plan._parameters` 的字段位置 | 声明在所有方法之后、靠第 4 个位置参数传入；行为正确但下次加字段必踩。 |

## 8. 第五轮（2026-11-25）：Innate 的入口从专用意图换成 capability

第四轮修完时长与射程之后，`Innate Sorcery` 的形态仍是个缺口：它走专用意图
`innate_sorcery`，因此不在 `combat_capabilities` 投影里，**面板上没有瓦片**。这与
本片对 `#836` / `#838` 的判定是同一件事——引擎有实现、玩家点不到。

本轮把它迁到 `class_capability`（详见 `S0F28_SPEC_REVISION` §7）：

- 补两个门禁字段：`exhausted_resource_id`（池必须为空）与 `requires_feature_id`
  （第二条价格挂在另一条特性上）。`combat_capabilities` 与 `_validate_class_capability`
  各自读同一份声明，不复制判据。
- 删 `_validate_innate_sorcery`、`_innate_sorcery_events` 里的两笔成本事件、
  `INTENT_TYPES` 的那一条、以及 engine 的分派。
- 新增一条用例锁住"入口在投影里"：还有次数时只出"花一次"的瓦片，次数归零后换成
  "2 点术法点"的瓦片，且旧意图类型不再出现在入口清单里。

代价与边界：

| 项 | 说明 |
|---|---|
| 基线 | `DECLARED_INTENT_TYPES` 减一条；新增 `resolution.py` 的 `kind_branch: innate_sorcery`；`validation.py` 的 `has_feature:innate_sorcery` 变成 stale 条目并删除。 |
| 其余四个特性 | Wild Shape / Wild Resurgence / Nature Magician / Nature's Sanctuary 仍是专用意图，属于另一个切片（capability 迁移），登记在 §25.1。 |
