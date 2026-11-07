# S0-F9 任务书：无甲防御、速度 track 与特殊移动

| 项 | 值 |
|---|---|
| 切片编号 | S0-F9（独立新任务，不覆盖旧 SP） |
| 验收载体 | Barbarian/Monk `unarmored_defense`、`fast_movement`、`unarmored_movement`、`roving`、`second_story_work`、`acrobatic_movement` |
| 通用能力 | AC 候选、装备门控速度、Climb/Swim、Dex 跳跃、垂直面/液面移动 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Barbarian p29；Monk p50–52（Acrobatic 在 p51）；Ranger p59；Thief p64；High Jump p183；Long Jump p184–185 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-C 已合入；无甲 AC 候选、装备重算与历史重建接口可用 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得只修改角色卡静态 AC/Speed 而不处理装备与真实移动。
2. 六项载体的全部官方子效果都实现，包括 Roving 的 Climb/Swim、Thief 的跳跃属性替换和 Monk 的特殊移动。
3. AC、速度、装备、移动额度、跳跃距离、落地检定与坠落由服务端裁决；客户端不得提交派生结果。
4. 多来源加值按各自条件独立聚合，不把 bonus 写进 base Speed；装备变化时立即重算。
5. 垂直面/液面必须来自权威环境事实；若基线没有该模型，按停止条件上报，不接受客户端自报表面类型。

## 2. 官方英文关键原文与语义边界

SRD p29、p50：

> **Barbarian Unarmored Defense.** While you aren’t wearing any armor, your base Armor Class equals 10 plus your Dexterity and Constitution modifiers. You can use a Shield and still gain this benefit.

> **Monk Unarmored Defense.** While you aren’t wearing armor or wielding a Shield, your base Armor Class equals 10 plus your Dexterity and Wisdom modifiers.

SRD p29、p50–51、p59：

> **Fast Movement.** Your speed increases by 10 feet while you aren’t wearing Heavy armor.

> **Unarmored Movement.** Your speed increases by 10 feet while you aren’t wearing armor or wielding a Shield. This bonus increases ... as shown on the Monk Features table.

> **Roving.** Your Speed increases by 10 feet while you aren’t wearing Heavy armor. You also have a Climb Speed and a Swim Speed equal to your Speed.

SRD p64、p51：

> **Second-Story Work.** Climber. You gain a Climb Speed equal to your Speed. Jumper. You can determine your jump distance using your Dexterity rather than your Strength.

> **Acrobatic Movement.** While you aren’t wearing armor or wielding a Shield, you gain the ability to move along vertical surfaces and across liquids on your turn without falling during the movement.

SRD p183–185：

> **High Jump.** ... 3 plus your Strength modifier (minimum of 0 feet) if you move at least 10 feet on foot immediately before the jump ... standing High Jump ... only half that distance ... each foot of the jump costs a foot of movement.

> You can reach a distance equal to the height of the jump plus 1 1/2 times your height.

> **Long Jump.** ... a number of feet up to your Strength score if you move at least 10 feet immediately before the jump ... standing Long Jump ... only half that distance ... each foot you jump costs a foot of movement.

> If you land in Difficult Terrain, you must succeed on a DC 10 Dexterity (Acrobatics) check or have the Prone condition.

语义边界：Barbarian 可用盾，Monk 不可；Roving 是 +10 而非 +5；Second-Story Work 是用 Dexterity 替代 Strength 决定跳跃距离，不是额外加 Dex modifier；Acrobatic Movement 不是永久飞行、游泳或站空能力。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| AC 有统一消费函数，S0-C 的无甲候选必须在这里接入 | `character/derivation.py::derive_armor_class` |
| 装备资格已有 `unarmored`、`no_shield` 和 canonical item 类型判定 | `features/equipment.py::Dnd2024EquipmentCatalog.requirement_met`、`Dnd2024EquipmentCatalog.requirement_met` |
| armor/shield/weapon 的真实类型来自 item 与 combat catalog，不靠名称猜测 | `features/equipment.py::Dnd2024EquipmentCatalog.from_bundle` |
| 升级统一重算派生值，速度/AC 不能只在初次构建写入 | `progression/advancement.py::_update_derived` |
| 有效速度已有共享聚合，Slow 等状态不得被本切片绕过 | `combat/check_modes.py::effective_speed` |
| 移动与 Opportunity Attack pending 由既有事件入口处理 | `combat/resolution.py::_movement_events` |
| Monk 装备/战斗已有测试，可作为盾牌和无甲门控回归 | `tests/rulesets/test_dnd2024_monk_equipment.py`、`test_dnd2024_monk_combat.py` |

## 4. 精确数据与行为契约

### 4.1 AC 与装备重算

1. Barbarian 1 级无甲时提供 base AC 候选 `10+Dex+Con`，Shield 可继续使用并按既有规则加盾；穿任意 Light/Medium/Heavy armor 后该候选失效。
2. Monk 1 级在无甲且不 wield Shield 时提供 `10+Dex+Wis`；穿甲或持盾立即失效，不得把 Wis 加到普通 armor AC。
3. 多职业或其他 AC 计算方式按 S0-C 选择/候选规则取一种 base AC，不得把 Con 与 Wis 两个无甲公式相加；Shield/Defense 等修饰只按各自规则应用一次。
4. AC 与速度均从当前 canonical 装备实时推导，穿脱/持盾、等级和属性变化、存档/加载与历史重建结果一致。

### 4.2 速度 track、Climb/Swim 与跳跃

1. Fast Movement（Barbarian 5）在未穿 Heavy armor 时 Speed +10；Light/Medium/无甲可获得，Shield 不影响。
2. Unarmored Movement（Monk 2）在无甲且不持盾时按 Monk 等级提供：2–5 级 +10，6–9 级 +15，10–13 级 +20，14–17 级 +25，18–20 级 +30。
3. Roving（Ranger 6）在未穿 Heavy armor 时 Speed +10，并始终提供等于当前 Speed 的 Climb Speed 与 Swim Speed；Heavy 只关闭 +10，不错误删除后两者。
4. 各来源 bonus 独立记录且同名不重复；有效 Speed 经 `effective_speed` 聚合 Slow 等状态，不能为负。Climb/Swim 使用各自当前速度，不再付“无特殊速度时每尺额外花费”的攀爬/游泳惩罚。
5. Second-Story Work（Thief 3）提供等于当前 Speed 的 Climb Speed；与 Roving 同时存在时不叠加为两倍，只取相同速度的合法来源。
6. Thief 的跳跃距离以 Dexterity 替代 Strength：Long Jump 用 Dexterity score，High Jump 用 `3+Dexterity modifier`（最低 0）；不是再加一份 Dex，其他生物仍用 Strength。
7. 助跑 Long/High Jump 要求紧接跳跃前以步行移动至少 10 尺；立定跳为对应距离一半。每跳 1 尺消耗 1 尺 movement，超过可用 movement 不可执行，距离精度不得擅自向上取整。
8. High Jump 触及高度为跳跃高度加 `1.5×身高`；Long Jump 落入 Difficult Terrain 时必须真实做 DC10 Dexterity (Acrobatics)，失败获得 Prone。GM 可选低障碍规则不在本切片自动虚构。

### 4.3 Acrobatic Movement

1. Monk 9 级、无甲、不持盾且在自己的 turn 内，才可用当前合法 movement 沿权威 vertical surface 或 across liquid 移动，并在该移动期间不坠落。
2. 该能力不授予 Climb/Swim/Fly Speed，不允许在自己回合外使用，也不能让穿甲/持盾 Monk 通过客户端声明绕过。
3. 移动结束不创造永久悬浮/站立资格；回合结束或能力失效后若仍无支撑，按既有重力/环境规则处理。事件记录表面、来源、路径、消耗与免坠落作用域。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`combat_catalogs/srd_combat_core.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/character/derivation.py`、`progression/advancement.py`、`features/equipment.py`、`features/resolver.py`。
3. `src/rulesets/dnd2024/combat/check_modes.py`、`primitives.py`、`validation.py`、`resolution.py`、`reducer.py`、`view.py`、`engine.py`。
4. 新建 `tests/rulesets/test_dnd2024_derived_defense_movement.py`；可追加既有 Monk 装备/战斗回归。
5. 需要新增通用地图/重力系统、前端私有跳跃公式或白名单外文件时停止。

## 6. 编号测试矩阵

1. 六项特性在 Barbarian1/5、Monk1/2/9、Ranger6、Thief3 正确授予，低等级/错误子职不可用。
2. Barbarian 无甲 AC 恰为 `10+Dex+Con`，持 Shield 仍适用且盾加值不重复。
3. Barbarian 穿 Light/Medium/Heavy 后无甲候选失效，不回退为错误裸甲公式。
4. Monk 无甲无盾 AC 恰为 `10+Dex+Wis`；穿甲或持盾任一即失效。
5. 多无甲来源不叠加 Con+Wis，按 S0-C 选择候选，存档/重建保持同一合法结果。
6. 属性、装备和等级变化后 AC 立即重算，事件/视图与角色卡一致。
7. Fast Movement 在无甲/Light/Medium 时 +10，Heavy 时无加值，Shield 不影响。
8. Monk Unarmored Movement 在 2/6/10/14/18 级分别 +10/+15/+20/+25/+30，中间等级 track 正确。
9. Monk 穿甲或持盾立即失去 Unarmored Movement bonus，卸下后恢复且不写入 base Speed。
10. Roving 非 Heavy +10、Heavy 无加值；两种情况均有等于当前 Speed 的 Climb/Swim。
11. 多来源速度 bonus 各加一次，Slow 等减益经统一聚合且有效 Speed 不为负。
12. Roving 的 Climb/Swim 消除对应普通移动额外花费；其他无这些速度的角色不获得。
13. Second-Story Work 的 Climb Speed 等于当前 Speed，与 Roving 并存不叠倍。
14. Thief 助跑 Long Jump 用 Dexterity score 而非 Strength；立定距离为一半且每尺扣 movement。
15. Thief 助跑 High Jump 用 `3+Dex modifier` 最低 0，立定一半，触及高度加 `1.5×身高`。
16. 非 Thief 跳跃仍使用 Strength；不得把 Dex 替代错误实现成额外加 modifier。
17. 助跑不足 10 尺只能立定跳；非步行移动不能伪作高跳“on foot”助跑。
18. 跳跃超过可用 movement、负距离或非法路径均拒绝且状态不变。
19. Long Jump 落入 Difficult Terrain 做真实 DC10 Dexterity (Acrobatics)，失败 Prone、成功无 Prone。
20. High Jump 不被错误追加 Long Jump 的 Difficult Terrain 落地检定。
21. Acrobatic Movement 仅 Monk9 无甲无盾且自己回合可用，穿甲、持盾或回合外均拒绝。
22. 合法垂直面/液面移动期间不坠落并消耗真实 movement；客户端自报 surface 不被信任。
23. Acrobatic Movement 不授予 Climb/Swim/Fly Speed，回合结束后无支撑时恢复既有重力规则。
24. 非法环境、重复移动事件、版本冲突均原子拒绝，不重复扣 movement 或触发 OA。
25. 升级、装备变更、存档/加载与重放后 AC、速度、跳跃和特殊移动状态一致。
26. 既有 Monk 装备/战斗、Slow、移动、OA、S0-C AC 与角色推导测试无回归。

## 7. 非目标与停止条件

### 非目标

- 完整二维/三维地图、GM 可选低障碍、所有 Hazards、飞行/游泳生态或被动探索 UI。
- Draconic Resilience 本身、Defense 风格新实现、Slow Fall、其他 Monk/Rogue 高阶特性。
- 把 Roving 写成 +5、把 Thief 写成额外加 Dex，或以静态角色卡字段替代移动结算。

### 停止条件

1. S0-C 未合入，或 AC 候选/装备重建无法保持唯一来源语义。
2. 权威状态没有 vertical/liquid/Difficult Terrain 或支撑事实，且白名单无法形成最小闭环。
3. 跳跃/特殊移动无法与 movement、OA、检定和重力使用同一事务。
4. 需要白名单外文件或客户端提交派生结果才能完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_derived_defense_movement.py -q
python -m pytest tests/rulesets/test_dnd2024_monk_equipment.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_monk_resources.py tests/rulesets/test_dnd2024_advancement_access.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/character src/rulesets/dnd2024/progression src/rulesets/dnd2024/features src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_derived_defense_movement.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Barbarian/Monk AC 与盾牌差异：
- Fast/Unarmored/Roving 速度 track：
- Climb/Swim 与装备门控：
- Thief Dex 长跳/高跳及落地：
- Acrobatic 环境、回合与免坠落：
- 白名单文件对照：
- 测试 1–26 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重建、重放与 RNG 证据：
- 偏差、停止原因与未决事项：
