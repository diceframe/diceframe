# S0-F26 任务书：Circle of the Land 主动区域与抗性

| 项 | 值 |
|---|---|
| 切片编号 | S0-F26（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `lands_aid`、`natures_ward`、`natures_sanctuary` |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Circle of the Land p46、Cover p179；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | F4 当前 land 与 Circle Spells 来源；F12 动态抗性；F16 AoE 缺失停止边界；F25 Wild Shape 资源；区域/掩蔽无既有前置 |
| 建议分支 | `feat/dnd2024-s0f26-land-active` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 从前置任务实际集成提交开工；当前 land、Sphere/Cube、地面、掩蔽、Half Cover 或 Wild Shape 支付接口缺失即停止。
2. 三项特性必须真实结算区域目标、增骰、抗性、持续区域、分享与移动，不能只显示文案或仅扣资源。
3. **区域或掩蔽系统不完整时停止，不用“与施法者距离”冒充 Cube/Sphere，不采用第三方异常文本。**
4. 不实现 Circle Spells/Natural Recovery、其他 Druid 特性、通用地形编辑器或 2D 改版；只使用官方 PDF。
5. 客户端只提交合法点、目标和能力选择；区域成员、伤害、治疗、抗性与持续时间均由服务端判定。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p46，Circle of the Land：

> **Circle of the Land Spells.** Whenever you finish a Long Rest, choose one type of land: arid, polar, temperate, or tropical.
>
> **Level 3: Land’s Aid.** As a Magic action, you can expend a use of your Wild Shape and choose a point within 60 feet of yourself. Vitality-giving flowers and life-draining thorns appear for a moment in a 10-foot-radius Sphere centered on that point. Each creature of your choice in the Sphere must make a Constitution saving throw against your spell save DC, taking 2d6 Necrotic damage on a failed save or half as much damage on a successful one. One creature of your choice in that area regains 2d6 Hit Points.
>
> The damage and healing increase by 1d6 when you reach Druid levels 10 (3d6) and 14 (4d6).
>
> **Level 10: Nature’s Ward.** You are immune to the Poisoned condition, and you have Resistance to a damage type associated with your current land choice ... Arid Fire; Polar Cold; Temperate Lightning; Tropical Poison.
>
> **Level 14: Nature’s Sanctuary.** As a Magic action, you can expend a use of your Wild Shape and cause spectral trees and vines to appear in a 15-foot Cube on the ground within 120 feet of yourself. They last there for 1 minute or until you have the Incapacitated condition or die. You and your allies have Half Cover while in that area, and your allies gain the current Resistance of your Nature’s Ward while there.
>
> As a Bonus Action, you can move the Cube up to 60 feet to ground within 120 feet of yourself.

SRD 5.2.1 p179，Cover：Half Cover 提供 +2 AC 与 +2 Dexterity saving throws；只应用最有利的一种掩蔽。p187，Resistance：每次伤害减半向下取整且仅应用一次。

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 三个 id 已在 Land 子职表按 3/10/14 级列出，canonical 定义尚不存在 | `content/subclass_catalogs/srd_subclasses.json`；class feature catalog |
| Wild Shape 池有真实 current/maximum 与休息恢复，F25 是支付接口前置 | `content/rest_catalogs/srd_recovery.json`；`features/resources.py` |
| 法术豁免、Necrotic damage、治疗和抗性有统一战斗结算先例 | `combat/resolution.py::_spell_events`、`_resist_damage`；`combat/reducer.py::_apply_hp_change` |
| 条件注册表可声明抗性，选择型抗性也可写条件元数据 | `content/combat_catalogs/srd_combat_core.json`；`combat/resolution.py::_resist_damage` |
| 基线位置是单一 theater 数值，不能据此虚构 10 尺 Sphere 或 15 尺 Cube 成员 | `combat/resolution.py::_start_combat_event`；`combat/engine.py::_distance` |
| 当前条件只有有限 attack modes，没有通用 Half Cover AC/Dex save 查询 | `combat/check_modes.py::attack_sources`；`combat/view.py` |
| F4 必须提供长休后的当前 land；F16 仅确立 AoE 缺失即停止的边界，不提供区域状态 | `resting/engine.py::complete_long_rest` 的 F4 集成边界；F16 §4.3 的 AoE 停止契约 |
| 探索模式只支持有限 healing/buff，持续区域不能只在战斗视图假实现 | `exploration/engine.py::_SUPPORTED_EFFECT_MODES`、`resolve_intent` |
| combat feed 字段白名单需处理区域事件，不公开未许可状态 | `runtime.py::_recent_combat_events` |

## 4. 精确行为与数据契约

### 4.1 授予、资源与当前 land

1. 三特性按 Land Druid 3/10/14 的真实子职授予，source_ref 均为 p46；非 Land Druid 即使拥有 Wild Shape 也不能使用。
2. Land's Aid 与 Nature's Sanctuary 各支付 Magic action+1 `wild_shape`，Sanctuary 移动支付 Bonus Action 但不扣 Wild Shape；资源不足、非自己回合或失能时零事件。
3. 当前 land 只读 F4 的 canonical `arid|polar|temperate|tropical`；抗性映射固定为 Fire/Cold/Lightning/Poison，不能由客户端提交或从法术名称猜测。
4. 两个主动能力不是 spell、不是 Channel Divinity：不写 spell.cast、不消耗法术槽、不触发 Disciple/Blessed Healer，也不被 Supreme Healing 最大化。

### 4.2 Land's Aid

1. 目标点为持有者 60 尺内权威空间中的点，创建瞬时 `10-foot-radius Sphere`；不是以目标生物为中心，也不是 10 尺直线距离列表。
2. 服务端枚举球内生物，客户端只能从中选择要伤害的任意集合及一个治疗目标；治疗目标也必须在同一区域，可与伤害目标重合。
3. 每个伤害目标分别进行 Constitution save，DC 为持有者当前 Druid spell save DC；失败承受 Necrotic 全伤，成功向下半伤，随后处理抗性、HP、专注与击败。
4. 伤害骰按 Druid 3–9/10–13/14–20 为 2d6/3d6/4d6；同一次施放使用一份伤害骰结果应用于所有伤害目标，每目标豁免独立。
5. 治疗目标恢复同等级数量的独立 d6，事件含 `healing=true`、目标、rolls 和 feature 来源；`cannot_regain_hp` 时归零，超过 max HP clamp。
6. RNG 顺序固定为一次区域伤害骰 → 按稳定 actor 顺序的豁免 → 一次治疗骰；区域无伤害目标时不掷伤害骰。区域内有生物时必须选择其中一个治疗目标，只有区域无任何生物时才不掷治疗骰，不得默认改治区域外目标。
7. 区域出现即结算后消失，不留下困难地形、持续遮蔽或每回合伤害；不能添加官方文本外效果。

### 4.3 Nature's Ward

1. 10 级起持有者对 Poisoned condition 免疫：新施加被阻止并记录 `blocked_by="natures_ward"`；已有 Poisoned 在免疫有效期间不产生任何规则效果，但不凭空改写其历史来源。
2. 当前 land 对应伤害抗性为被动事实，接入 `_resist_damage` 的统一查询；每次伤害只减半一次，与 Rage、法术或其他来源不叠加。
3. land 合法改变后，新抗性立即用于后续伤害，旧抗性不再生效；不能同时保留四种，也不能将 Poison damage 抗性与 Poisoned condition 免疫混为一谈。
4. Land's Aid/Sanctuary 的效果读取施放时及后续当前有效 land；无有效 current land 时停止，不默认 arid。

### 4.4 Nature's Sanctuary 持续 Cube

1. 创建 15 尺 Cube 于 120 尺内地面上，权威区域状态含 `area_id`、`shape="cube"`、`size=15`、`origin`、`ground_ref`、`source_actor_id`、`expires_at` 和 feature id。
2. 持续 1 分钟或持有者失能/死亡，先到者立即结束；不是 Concentration，不因普通受伤进行专注豁免，也不能用“直到战斗结束”替代。
3. 持有者和盟友只有身处 Cube 内才获得 Half Cover，统一提供 +2 AC/+2 Dex saves；离开即失去，与其他掩蔽只取最高，不叠加。
4. 盟友在 Cube 内获得持有者 Nature's Ward 的**当前**伤害抗性；敌人不获得。持有者自己仍由 Nature's Ward 获得抗性，不能把“allies”误写成只有自己或所有人都包括敌人。
5. 抗性随当前 land 和成员位置动态求值，不把一种类型永久写入盟友角色；离开 Cube 后立即失去分享来源，其自身其他抗性不受影响。
6. Bonus Action 可将同一 Cube 沿合法地面移动最多 60 尺，终点仍须在持有者 120 尺内；不重建区域、不刷新一分钟、不再次扣 Wild Shape。
7. 移动后按新区域重新计算成员与掩蔽/抗性，事件含 from/to、distance 和同一 area_id；客户端不能提交成员列表或超距传送。
8. 战斗与探索必须共用同一权威区域权限和到期时钟；F4/F16 均不提供该模型，已列前置仍缺失时停止，不在本切片新建平行区域引擎。存档/加载、重放及视角查询不得复制 Cube。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅封闭条件/掩蔽声明
5. `src/rulesets/dnd2024/features/resolver.py`
6. `src/rulesets/dnd2024/combat/resolution.py`
7. `src/rulesets/dnd2024/combat/check_modes.py`
8. `src/rulesets/dnd2024/combat/engine.py`
9. `src/rulesets/dnd2024/combat/reducer.py`
10. `src/rulesets/dnd2024/combat/validation.py`
11. `src/rulesets/dnd2024/combat/view.py`
12. `src/rulesets/dnd2024/exploration/engine.py`
13. `src/rulesets/dnd2024/runtime.py`
14. 新建 `tests/rulesets/test_dnd2024_land_active.py`
15. 本任务书：只允许追加执行报告

不改 progression/subclass/rest/spell catalog、F4 的 land 选择、F25 的变形状态或前端；需要新增区域引擎文件时停止并报告系统缺口，不得错派给 F4/F16。

## 6. 编号测试矩阵

1. 三特性按 Land 3/10/14 授予，低一级、其他子职和普通 Druid 无能力；中英文 source_ref 正确。
2. Land's Aid 支付 Magic action+1 Wild Shape，不扣法术槽、不写 spell.cast；资源不足零事件。
3. 60 尺点和 10 尺半径 Sphere 成员由服务端计算，区域外目标、伪造成员或伪造距离被拒绝。
4. 每个所选目标独立 Con save，失败 2d6 Necrotic、成功半额向下；未选目标不豁免不受伤。
5. Druid 10/14 级伤害与治疗分别增为 3d6/4d6，其他等级不增；两个公式同步变化。
6. 同次所有伤害目标共用一份伤害骰，治疗另掷一份同数量骰，RNG 顺序可重放。
7. 一个区域治疗目标可与自己伤害目标重合；区域外治疗、`cannot_regain_hp` 和 max HP clamp 正确。
8. 区域伤害经过抗性、HP、专注和击败；无持续地形/遮蔽/后续回合效果，Supreme Healing 不最大化。
9. Nature's Ward 阻止新 Poisoned 且已有状态在免疫期间无效果；其他 condition 不被误免疫。
10. arid/polar/temperate/tropical 分别提供 Fire/Cold/Lightning/Poison 抗性，非对应类型全额。
11. 当前 land 合法改变后只使用新抗性；与其他抗性只减半一次，不把 Poison 状态免疫当 Poison damage 免疫。
12. Nature's Sanctuary 支付 Magic action+1 Wild Shape，在 120 尺内地面创建 15 尺 Cube，状态字段完整。
13. Cube 内持有者和盟友 Half Cover，AC/Dex save +2；Cube 外和敌人无此收益，更高掩蔽不叠加。
14. Cube 内盟友获得当前 Ward 抗性，离开即失去；敌人不分享，持有者自身 Ward 始终按自身规则计算。
15. 分享来源不与盟友既有抗性叠加，当前 land 改变时不保留四种或旧类型。
16. Bonus Action 移动 Cube 最多 60 尺且终点在 120 尺内地面，不扣 Wild Shape、不刷新期限、area_id 不变。
17. 移动 61 尺、终点超 120 尺、无地面或伪造成员时拒绝；移动后掩蔽和抗性按新成员重算。
18. Sanctuary 持续一分钟准确到期，失能/死亡立即结束；普通受伤不触发 Concentration save。
19. Sanctuary 不是法术/Channel，不触发治疗 rider 或 Supreme Healing；移动也不是重新施放。
20. 战斗/探索区域成员、地面和到期一致，缺少区域或掩蔽系统时能力 fail closed 且不扣资源。
21. apply/replay、save/load 后区域 origin、期限、当前 land、成员收益和 RNG 一致，重复 view 不复制区域。
22. Rage、Stoneskin、Protection from Energy、Survivor、Wild Shape、Land spells 和装备派生回归通过。
23. 非本人、非 Land、伪造 land/area_id/origin/resistance 或过期区域操作均被服务端拒绝。
24. 全量 rulesets、ruff、diff 通过；任何以 theater 距离伪装 Cube/Sphere 或采用第三方文本的实现判失败。

## 7. 非目标与停止条件

### 7.1 非目标

- Circle Spells/Natural Recovery、Wild Shape 本体、其他 Druid 子职和通用地形编辑。
- 2D 光照、区域绘图 UI、困难地形或第三方 Sanctuary 异常文本。
- 把 Half Cover 只写成事件标签而不改变 AC/Dex save。

### 7.2 停止条件

1. F4/F12/F16/F25 未合入，或 current land、Wild Shape 支付和主动能力接口不一致。
2. 没有权威 Sphere/Cube、地面、成员、移动和持续时钟，或 theater 距离无法表达官方区域。
3. Half Cover 不能统一进入 AC/Dex save，或区域抗性必须写入盟友永久角色状态。
4. 一分钟到期、失能/死亡清理和战斗外时间无法一致，需新增白名单外区域所有者。
5. 核心测试 3–8、12–18 只能只扣资源、伪造区域/掩蔽或手写预期事件通过。
6. 基线或并行分支已有同责区域实现，存在成员、抗性、事件或资源双写。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_land_active.py -q
python -m pytest tests/rulesets/test_dnd2024_wild_shape.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_survivor.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat src/rulesets/dnd2024/exploration/engine.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_land_active.py
git diff --check
git status --short
```

记录实际 Python 路径、F4 current land、区域系统实际来源与测试 1–24 映射，单独展示增骰、四种 land、Cube 分享/移动和失能结束证据；F16 不作为区域提供者。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、区域状态、成员判定和掩蔽数据流。
2. 明确区域/掩蔽缺口停止结果，不得以 theater 距离近似冒充验收。
3. 报告 RNG、资源原子性、重放/存档、战斗/探索一致性和公开投影。
4. 不得把第三方文本、持续地形或未实现区域系统写成已完成。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 当前 land 与 Wild Shape 支付：
- Land's Aid 区域、增骰、伤害/治疗：
- Nature's Ward 抗性：
- Sanctuary Cube、Half Cover、分享、移动、到期：
- 改动文件对照白名单：
- 测试 1–24 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与区域完整性证据：
- 与契约的偏差及原因：
