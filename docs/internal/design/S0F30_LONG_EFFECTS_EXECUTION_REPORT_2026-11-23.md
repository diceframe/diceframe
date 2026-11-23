# S0-F30 执行报告：跨日、位面与长时状态特性

| 项 | 值 |
|---|---|
| 切片 | S0-F30（五个特性：Intimidating Presence / Quivering Palm / Holy Nimbus / Dragon Wings / Hurl Through Hell） |
| 基线 | `dc34bd84` 之后的 `feat/dnd2024-s0f30-long-effects` |
| 状态 | 五个特性的核心机制完成；**两处资源恢复入口未做**（见 §5） |

## 1. 交付

| 交付 | 位置 |
|---|---|
| 角色级长时效果 | `features/resources.py`：`long_effects` / `long_effect_active` / `fly_speed_from` |
| 世界时钟读取 | `combat/primitives.py::world_minutes`（与 `ruleset_rest._rest_tick` 同口径） |
| 效果事件 | `combat/reducer.py`：`dnd2024.long_effect.applied` / `.ended` |
| 特性自身使用次数 | `FeatureCapability.uses_per_long_rest` + validation 拒绝 |
| 光环的激活门控 | `combat/engine.py::_projected_auras` 读 `requires_long_effect` |
| 五特性定义 | `class_feature_catalogs/srd_class_features.json` + zh-CN 标签 |
| 用例 | `tests/rulesets/test_dnd2024_long_effects.py`（23 条） |

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
pytest tests/rulesets/test_dnd2024_long_effects.py   23 passed
pytest tests/rulesets -n 6                           见 §7
ruff / mypy                                          干净
```

## 5. 未完成（明确列出）

1. **Holy Nimbus 的 level 5 法术位恢复**：需要"消耗一个 5 环位"的权威入口，现有法术位扣减散在施法路径里，没有可复用的 capability 接口；本片不做，避免在通用路径上开一个只为一条特性存在的洞。
2. **Hurl 的"契约法术位恢复"**：同上，且要求"普通槽不可替代"。
3. **Dragon Wings 的 3 点术士点恢复**：`sorcery_points` 资源存在，但恢复入口同样未接。
4. Holy Nimbus 的主动结束（无动作）、Dragon Wings 的主动结束：需要"按效果 id 结束"的通用入口，与恢复入口一并留待下一片。

## 6. 与 F29 同一类判断：没有消费者的字段不做

`sunlight` 在内容与代码里都没有消费者（没有光敏生物、没有日光弱点）。加一个字段不会有任何代码读它，形状也只能靠猜。本片只投影 Bright Light 半径，等首个消费者出现时再按它的读法定 `light_kind`。

## 7. 全量结果

```
pytest tests/rulesets -n 6 --dist loadfile    2818 passed（16m03s）
ruff check src/ tests/                         All checks passed
mypy                                           102 source files, no issues
```

## 8. 自查清单（本项目固定六条）

- **等价性**：既有 2818 条全绿；玩家侧派生未改，`fly_speed` 是新增字段而非改写 `speed`。
- **多消费者一致性**：`world_minutes` 与 `ruleset_rest._rest_tick` 同一口径；`long_effect_active` 是唯一的到期判定，视图、光环门控、结束入口都走它。
- **跨特性交互**：光环门控只读 `requires_long_effect`，没有光环的特性不受影响；形态与条件系统未被改动。
- **递归/回路**：`long_effects` 只由事件写入、由读取函数消费，没有自我触发路径。
- **条件生命周期**：Hurl 的失能用 `turns_remaining` 记录，`TURNS_SCOPED_DURATIONS` 已登记；到期由既有回合边界处理。
- **测试是否安慰剂**：断言落在事件流（`check.resolved` / `resource.changed` / `long_effect.*`）与最终数值上，不落在中间结构。

## 9. 偏差与未决

1. 任务书 §4.5 写"敌人在 Aura of Protection 内开始其回合"，但 Holy Nimbus 的 SRD 原文是 30 尺灵光，Aura of Protection 是 10 尺。本片按 30 尺实现，以 SRD 原文为准，任务书该句应更正。
2. 任务书 §4.4.3 原写 `fly_speed` 落 `derivation.py`；`derive()` 是建卡时的一次性计算，拿不到运行时状态，改为角色级 `long_effects` + 统一读取函数，任务书已修订。
3. `sunlight` 无消费者，本片不声明；`plane` 与 `banishment` 标 `guided`。

