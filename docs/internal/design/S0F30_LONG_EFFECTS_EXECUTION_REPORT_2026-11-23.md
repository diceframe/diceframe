# S0-F30 执行报告：跨日、位面与长时状态特性

| 项 | 值 |
|---|---|
| 切片 | S0-F30（五个特性：Intimidating Presence / Quivering Palm / Holy Nimbus / Dragon Wings / Hurl Through Hell） |
| 基线 | `dc34bd84` 之后的 `feat/dnd2024-s0f30-long-effects` |
| 状态 | 五个特性全部完成，含资源恢复与主动结束 |

## 1. 交付

| 交付 | 位置 |
|---|---|
| 角色级长时效果 | `features/resources.py`：`long_effects` / `long_effect_active` / `fly_speed_from` |
| 世界时钟读取 | `combat/primitives.py::world_minutes`（与 `ruleset_rest._rest_tick` 同口径） |
| 效果事件 | `combat/reducer.py`：`dnd2024.long_effect.applied` / `.ended` |
| 特性自身使用次数 | `FeatureCapability.uses_per_long_rest` + validation 拒绝 |
| 光环的激活门控 | `combat/engine.py::_projected_auras` 读 `requires_long_effect` |
| 五特性定义 | `class_feature_catalogs/srd_class_features.json` + zh-CN 标签 |
| 通用恢复/结束 | `restore_feature_use`（扣槽或资源、回使用计数）与 `end_long_effect`（无成本） |
| 用例 | `tests/rulesets/test_dnd2024_long_effects.py`（44 条） |

## 2. 为什么是"角色级效果"而不是条件

战斗结束时 reducer 会清掉按回合计时的条件（`dnd2024.combat.ended` 分支明写"战斗结束后没有回合"）。而四小时的特性的时长是 10 分钟、1 小时、 Monk 等级天数——它们必须跨战斗、跨存档。所以状态落在 `ruleset_character.long_effects`，与 F25 的 `forms` 同层。

到期用世界时钟的绝对分钟：施加时记 `expires_at = world_minutes(instance) + duration`，读取时惰性比较。休息本就推进时钟（`_advance_rest_time`），因此"10 分钟 / 1 小时 / N 天"在休息后自然结束，不需要后台定时器，也不往 `scheduled_events` 里塞（那条通道的结算仍标注未实现）。

## 3. 五个特性

### 3.1 Intimidating Presence（Berserker 14）

- Bonus Action，30 尺灵光，DC = 8 + Str 调整 + 熟练（不是法术 DC）
- 失败者恐惧一分钟，每个受影响者在自己回合末重复豁免（`save_repeat` 快照）
- 免疫在条件落地前回答，不靠事后过滤
- 使用次数记在 `feature_usage`，长休清零

### 3.2 Dragon Wings（Draconic 14）

- Bonus Action，60 尺飞行一小时，写入 `long_effects.detail.fly_speed`
- 视图 `fly_speed` 每来源取最高，不叠加；到期后归零
- `_player_view` 显式接收 `now_minutes`，不自己摸 instance

### 3.3 Holy Nimbus（Devotion 20）

- Bonus Action，30 尺灵光十分钟，`requires_long_effect` 决定光环是否投影
- 敌人回合开始受 `Cha 调整 + 熟练` 光耀伤害；友军、光环外、他人回合不触发
- 熟练加值按等级算：`_feature_character` 的切片不含 `derived`
- Holy Ward 挂进豁免中心函数，来源生物类型从权威视图读
- **Bright Light 半径已投影；`sunlight` 不声明**——没有消费者（见 §6）

### 3.4 Quivering Palm（Open Hand 17）

- 徒手命中后支付 4 Focus，写入 `long_effects`，持续天数为武僧等级
- 结束是一次动作：Con 豁免，10d12 力场，成功减半，随后效果移除
- "必须有振动"在校验层拒绝，客户端拿 `ok=false`
- **同一位面不判定**：事件记 `plane_check: guided`，不默认成功

### 3.5 Hurl Through Hell（Fiend 14）

- 每回合一次的命中 rider：Cha 豁免对抗施法 DC
- 失败受 8d10 心灵（邪魔免疫这份伤害）并失能到你的下回合结束
- 成功也消耗这次使用
- **消失与返回不做**：事件记 `banishment: guided`，只落失能与时长

## 4. 验证

```
pytest tests/rulesets/test_dnd2024_long_effects.py   44 passed
pytest tests/rulesets -n 6                           见 §7
ruff / mypy                                          干净
```

## 5. 资源恢复与主动结束

初版报告把这几项列为"未做"，理由是"没有可复用的槽位接口"——**这个判断是错的**。`dnd2024.spell.slot_spent` 就是通用路径，`font_of_inspiration` 早就在用它做"花一个槽换回一次使用"。补齐如下：

| 入口 | 支付 | 恢复 |
|---|---|---|
| Holy Nimbus | 一个 5 环位（`exchange: true`，不占施法位） | 长休使用计数 -1 |
| Dragon Wings | 3 点术士点 | 同上 |
| Hurl Through Hell | 一个 5 环契约槽 | 同上 |
| 主动结束（Holy Nimbus / Dragon Wings） | 无成本 | 发 `long_effect.ended` |

两条纪律：

- **恢复走独立事件 `feature_usage.restored`**：`feature_usage.changed` 只允许 +1，那是"又用了一次"的语义；把减少塞进去会让两条语义共用一个校验。
- **没有已用次数时在校验层拒绝**，不白扣一格槽。
- Hurl 的命中 rider 补上了长休记账：之前只记"每回合一次"，恢复入口因此永远看不到已用次数。

## 6. 与 F29 同一类判断：没有消费者的字段不做

`sunlight` 在内容与代码里都没有消费者（没有光敏生物、没有日光弱点）。加一个字段不会有任何代码读它，形状也只能靠猜。本片只投影 Bright Light 半径，等首个消费者出现时再按它的读法定 `light_kind`。

## 7. 全量结果

```
pytest tests/rulesets -n 6 --dist loadfile    2839 passed（16m00s）
ruff check src/ tests/                         All checks passed
mypy                                           102 source files, no issues
```

## 8. 自查清单（本项目固定六条）

- **等价性**：既有 2839 条全绿；玩家侧派生未改，`fly_speed` 是新增字段而非改写 `speed`。
- **多消费者一致性**：`world_minutes` 与 `ruleset_rest._rest_tick` 同一口径；`long_effect_active` 是唯一的到期判定，视图、光环门控、结束入口都走它。
- **跨特性交互**：光环门控只读 `requires_long_effect`，没有光环的特性不受影响；形态与条件系统未被改动。
- **递归/回路**：`long_effects` 只由事件写入、由读取函数消费，没有自我触发路径。
- **条件生命周期**：Hurl 的失能用 `turns_remaining` 记录，`TURNS_SCOPED_DURATIONS` 已登记；到期由既有回合边界处理。
- **测试是否安慰剂**：断言落在事件流（`check.resolved` / `resource.changed` / `long_effect.*`）与最终数值上，不落在中间结构。

## 9. 偏差与未决

1. 任务书 §4.5 写"敌人在 Aura of Protection 内开始其回合"，但 Holy Nimbus 的 SRD 原文是 30 尺灵光，Aura of Protection 是 10 尺。本片按 30 尺实现，以 SRD 原文为准，任务书该句应更正。
2. 任务书 §4.4.3 原写 `fly_speed` 落 `derivation.py`；`derive()` 是建卡时的一次性计算，拿不到运行时状态，改为角色级 `long_effects` + 统一读取函数，任务书已修订。
3. `sunlight` 无消费者，本片不声明；`plane` 与 `banishment` 标 `guided`。


## 10. 独立审核（2026-11-23 晚）

由独立审查者按任务书 §4–§7 与 `docs/ENGINEERING_RULES.md` 审 `dc34bd84..HEAD`，结论"不能合入"，列 9 处阻塞。逐条核实后：

### 已修（8）

| 编号 | 问题 | 修法 |
|---|---|---|
| 1 | Quivering Palm 的命中声明没有 `has_feature`，伪造 intent 即可用 | 补拥有、每回合一次、无既有振动三道检查 |
| 2 | 第二次施加静默覆盖，`max_targets` 无读者 | 有振动时拒绝施加 |
| 3 | 失能写成 `turns*10`（一分钟），规则是"到你的下回合结束" | 改为回合数 |
| 5 | 三条新伤害绕过 `_damage_instance_event`，抗性全部失效 | 改走同一伤害实例函数 |
| 6 | reducer 不校验 `expires_at`；重放旧事件可续期 | 缺失即拒绝；旧到期时间不覆盖新的 |
| 7 | Intimidating Presence 漏了 Rage 恢复（资源 id 是 `rages` 不是 `rage`） | 补 `restore_resource` 与能力 |
| 9 | 视图原样投出 `long_effects`，过期项不过滤 | 按 `now_minutes` 过滤 |
| 白名单 | `world_minutes` 放在 `primitives.py`（§5 未允许） | 迁到 `features/resources.py` |

### 改判定（1）

阻塞 4（"标 `guided` 但照常结算"）：原先既贴标签又打满伤害，等于偷偷默认成功。现改为**显式断言**——调用方不在 intent 里写 `same_plane: true` 就拒绝，事件记 `plane_asserted`。没有权威位面系统这件事因此写在契约里，而不是藏在标签后面。

### 未修（明确列出）

1. **Quivering Palm 的第二个致命入口**（Attack action 中放弃一次攻击）未实现，**已确认属于切片之外的重构**（见 §11）。测试 5 仍靠注入 `long_effect` 构造状态。
2. **Hurl 的"消失/返回"**未实现，失能直接施加在原位目标上；`banishment: guided` 只是标注，不改变"目标仍在原地"的事实。按 §7 这本是停止条件，需产品侧决定是补位置语义还是整特性下线。
3. **探索侧未接**：`exploration/engine.py` 没有 `class_capability` 入口，Dragon Wings / Holy Nimbus 只能在战斗内激活；`fly_speed` 目前只展示、不参与移动结算。§6.12 不成立。
4. **若干"应该改"**：Quivering Palm 的豁免未走 `_saving_throw_check`（无 auto-fail / 光环 / 掩护）；恢复入口不校验槽位余额；`holy_ward_creature_types`、`max_targets`、`duration_days_per_level`、`damage_type` 是死参数；Hurl 在次数耗尽时静默不给错。
5. ~~测试缺口（§6 的 3/4/6/9/13/14/15/16/17）未逐条补齐~~ —— 已补七条：振动天数、第二目标不声明、无同位面断言即拒、低环槽冒充、友军不受光环伤害、不声明 `light_kind`、长休账耗尽即失败。`assert damage["delta"] <= -8` 仍是范围断言，但 8d10 的下限 8 是可证明的下界，保留。

### 复核时又发现并修掉的

| 问题 | 修法 |
|---|---|
| Quivering Palm 的豁免是手写 `rng.randint`，丢掉自动失败/光环/掩护/`roll_id` | 改走 `_saving_throw_check`（用例断言 `roll_id` 与 `aura_bonus` 存在） |
| 恢复入口不校验余额，零槽会冒泡成 `EventBatchError` | 在校验层挡下，返回 `ok=false`；槽表从视图 `slots` 读，`_feature_character` 的切片不带施法块 |
| Holy Ward 的生物类型与 Quivering Palm 的伤害类型写死在代码里 | 改读特性参数 |
| Hurl 在"未拥有/本回合已用/账已满"时静默降级成普通命中 | 在校验层明确拒绝 |
| `max_targets`、`duration_days_per_level` 无读者 | 删除，避免目录读者误以为生效 |

## 11. 攻击经济：为什么"放弃一次攻击"没有在本片实现

### 现状

`economy` 是一组计数器：`action`、`bonus_action`、`reaction`、`surge_action`、`attacks_remaining`。`attacks_remaining` 只属于 Attack action，只在 `dnd2024.attack.spent` 里消费（`reducer.py:703`）：

- `resource == "bonus_action"` 或额度已为 0 → 扣 `economy[resource]`，若扣的是 action 则把额度重置为 `attacks_per_action - 1`
- 否则额度直接减一

校验侧对应 `validation.py:1067`：`attack` 意图在 `attacks_remaining >= 1` 时不需要 `has_action`。`attacks_per_action` 来自 Extra Attack，多职业取最大。

**这个事实是存在的，只是只有 `attack` 意图能消费它。**

### 为什么本片容纳不下

`CapabilityCost` 只有三种价格：`action`（字符串）、`resources`、`turn_start`。"一次攻击"既不是经济槽也不是资源，没有位置。

三个候选结构：

| 方案 | 优点 | 缺点 |
|---|---|---|
| attack 意图加 `substitute` | 复用额度与校验 | 要允许"没有 target 的 attack"；`second_target_of` 已是同类特例，会继续长 |
| 独立 `attack_substitute` 意图 | 语义干净 | 额度消费要写第二份，两份规则迟早分叉 |
| **`CapabilityCost` 支持一次攻击** | Grapple / Shove / Quivering Palm 共用同一套声明，是终局解 | 碰的是所有 capability 的最热路径 |

### 尝试过，被更底层的问题挡住

`CapabilityCost.action` 是**一个字符串同时承担两个语义**：既是成本种类，又被当成经济槽的名字直接索引。`economy[cost]` 散在 `validation.py:1224 / 2814 / 2897`，错误信息还用它拼字符串。

往 `ACTION_COST_KINDS` 加 `"attack"` 后，报出 `the attack has already been spent` —— 某处拿 `"attack"` 去索引 `economy` 的结果。这不是加一个分支能解决的。

### 结论：先做重构，再谈特性

1. `CapabilityCost` 拆成 `slot: str`（经济槽）+ `attacks: int`（攻击次数）+ `resources` + `turn_start`
2. 支付收敛成一个函数 `_pay_capability_cost()`，由它决定发 `action.spent` 还是 `attack.spent`
3. 散落的 `economy[cost]` 全部改走该函数或 `_slot_available()`

这条重构影响每个 capability，应单独立片、单独一轮全量回归。本片不塞。

现场 patch 留在 `E:/pytest-tmp/f30-attack-cost.patch`（176 行：成本种类、校验绕过、`attack.spent` 结算、JSON 第二入口、两条用例），重构完成后可直接复用。

## 12. 第二次独立审核（2026-11-24）

复审用探针验证，抓到两处**"报告说修了、代码没修"**：

| 阻塞 | 事实 | 修法 |
|---|---|---|
| B1 Hurl 失能到期归属 | `TURNS_SCOPED_DURATIONS` 是"持有者每到自己回合结束减一"，而规则是"到**你的**（邪术师）下回合结束"。探针实证：目标自己回合结束就被清掉 | 改用既有载体 `caster_next_turn_end` + `turns_remaining: 2`；删掉自造的 duration 常量（顺带清了 `primitives.py` 的白名单残留） |
| B2 光耀伤害绕过抗性 | 报告 §10 第 5 条写"力场、心灵、光耀抗性……改走同一个伤害实例函数"，实际只有力场与心灵改了。探针：Cha 15 + PB 6 = 8，打在 radiant 抗性敌人身上仍掉 8 | 固定值也走 `_resist_damage`；新增抗性用例（8 → 4） |

### 同时修的

- Quivering Palm 的伤害类型仍写死 `"force"`（报告声称已改读参数）；现在读 `damage_type`。
- Quivering Palm 的"每回合一次"是**永不生效的假门**：它读的账从不写入。规则也没有这条限制，删除。
- `_player_view(..., now_minutes: int = 0)` 的默认值：任何漏传都会让过期效果复活，改为必填。
- 删掉 `test_a_short_rest_does_not_restore_the_use`：它根本没执行短休，两次断言是同一句话。

### 仍未修

- **Quivering Palm 的焦点余额**没有在校验层拦截：视图的 `class_resources` 在 validation 阶段读不到当前值（试过直接读投影，仍读空），所以仍由 reducer 抛 `EventBatchError`。这是与"零槽"同类的欠账，需要先修资源投影。
- Hurl 无法在法术攻击上触发（只挂在武器/徒手命中路径），Fiend Warlock 的主攻击 `Eldritch Blast` 触发不了。要么补法术路径，要么在契约里写明只支持武器攻击。
- Quivering Palm 结束时的目标若已不在本场战斗，`_actor_view` 会抛异常而非 `ok=false`。
- `_TURN_SCOPED_DURATIONS` 与 `TURNS_SCOPED_DURATIONS` 已漂移（前者不含后者的新成员）。
- `resource.py` 的 `feature_kind: "intimidating_presence"` 写死在通用记账里，Holy Nimbus / Dragon Wings 的使用事件都会带这个错标签（当前无消费者）。

## 13. "攻击命中"这个抽象（2026-11-24 补）

第二轮审核指出 Hurl 触发不了法术攻击。查完两条路径后确认：**武器攻击与法术攻击在命中之后完全分叉**，没有共享的"命中"点。

- 武器：`_attack_events` → `_hit_settlement_events` → `_hit_tail_events`（rider 与 stat-block rider）
- 法术：`_spell_events` 的 `if succeeded:` 自己组 `damage_node` 算伤害，不回骑乘链路

`_hit_tail_events` 的 docstring 写着"every path that can land a hit calls this"，但它只有一个调用点——那句注释是假的。

### 做法

新增 `_attack_hit_events(instance, combat, attacker, target, intent, rng)`：**"用攻击检定命中"之后的一切触发效果**都写在这里，两条路径各自的命中分支各调一次。

- 武器路径：`_resolve_committed_hit`（`_hit_settlement_events` 的收尾，Shield 之后才走到）
- 法术路径：`_spell_events` 的 `if succeeded:` 分支

Hurl 是第一个订阅者（`_hurl_through_hell_hit_events`），但入口本身不认识它：`_attack_hit_events` 只按 `intent` 分派，新增一个"命中触发"效果不需要再碰两条路径。

顺带把 Hurl 从 `_hit_condition_declarations` / `_hit_rider_events` 里摘干净——它本来就不该住在"武器命中附加伤害"那套机制里。

### 验证

`test_a_spell_attack_goes_through_the_shared_hit_entry` 用 spy 盯入口是否被法术路径调用（效果本身由武器路径的用例覆盖，同一份实现）。

没做的是"Eldritch Blast 的完整端到端 Hurl 战斗"：`srd_combat_core.json` 里 `eldritch_blast` 的 effect 只有 `damage: "1d10"`、没有分束定义，骰子序列怎么排都不稳；这一条留给"EB 分束"单独处理。
