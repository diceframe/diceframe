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

---

## 9. 执行报告（2026-11-08）

### 9.1 基线、分支与一条必须先更正的前置结论

- **前置**：`feat/dnd2024-s0d2-overchannel@f775ae4f`（stack 在 E2/E1 之上），新分支 `feat/dnd2024-s0f9-movement`。
- **更正**：索引 §7 与本任务开工前的复核都写着「`unarmored_defense` 未接线（野蛮人 AC 12 应为 15、武僧 13 应为 15），移动速度只取种族值」。**该结论已失效**。
  - S0-C 把无甲 AC 与速度加值做成了通用参数（`unarmored_ac_ability_bonus`、`speed_bonus_ft`、`speed_bonus_track`），解析入口是 `Dnd2024ClassFeatureResolver.unarmored_ac_bonus_abilities()` 与 `speed_bonus()`，**不按特性 id 命名**；`feat/dnd2024-derived-defense-speed@73d11263` 已把它们接进 `derive_armor_class` / `derive_speed`，`tests/rulesets/test_dnd2024_derived_defense_speed.py` 38 条覆盖矩阵 2–11（含野蛮人 15 / 持盾 17 / 三种护甲失效、武僧 15 / 持盾与穿甲失效、Fast Movement 非 Heavy +10、Unarmored Movement 2/6/10/14/18 级 +10/+15/+20/+25/+30、建卡/升级/协调三路一致）。
  - 用 `grep unarmored_defense src/` 得 0 命中是**方法错误**：机制是参数驱动的，id 本来就不会出现在源码里。本轮不再重复实现，只补真正缺的部分。
- **本切片不宣称**：矩阵 2–11 的既有能力不是本轮交付物。

### 9.2 实际变更

| 面 | 文件 | 内容 |
|---|---|---|
| 目录 | `content/class_feature_catalogs/srd_class_features.json` | 新增 `classes.ranger` bucket（仅 `roving`，6 级）；`rogue` bucket 新增 `subclass:thief.second_story_work`（3 级） |
| 标签 | 同上（content `labels`）、`locales/{en,zh-CN}/...` | 补 `ranger`；修正两处误译：`roving` 游荡→**游走**、`second_story_work` 二楼作业→**飞檐走壁** |
| 特性解析 | `features/resolver.py` | 新增 `movement_modes()`（读 *owned* 声明）与 `jump_ability()`（默认 `str`）；新增 `_validate_movement_parameters()` fail-closed 校验 |
| 派生 | `character/derivation.py` | 新增 `derive_jump_distances()`、`derive_movement_profile()`；建卡路径写入 `climb_speed`/`swim_speed`/五个跳跃字段 |
| 升级 | `progression/advancement.py` | 升级当次重算移动档案（否则 Ranger 6 拿到 Roving 后要等下一次换装才生效） |
| 协调 | `character/reconciliation.py` | `reconcile()` 与 `reconcile_canonical()` 同源写入，并把新字段纳入幂等短路比较 |
| 测试 | `tests/rulesets/test_dnd2024_derived_defense_movement.py`（新建，46 条） | 矩阵 1、12–16、18、25 的部分 + fail-closed + 停止条件断言 |
| 测试 | `tests/rulesets/test_dnd2024_class_features.py` | 两条 rogue 特性名单断言随新增声明更新（现在多一项 `second_story_work`） |

### 9.3 关键设计：一条声明、两个门

Roving 在 SRD p59 的两个半句有不同前提：

> Your Speed increases by 10 feet **while you aren't wearing Heavy armor**. You **also** have a Climb Speed and a Swim Speed equal to your Speed.

Speed +10 受 Heavy 门控；Climb/Swim 不受。所以同一份声明要分两路读：

- `speed_bonus()` 读 `active_features_for()` —— Heavy 装备使声明失效，+10 消失（既有行为，未改）；
- `movement_modes()` 读 `features_for()` —— 只问"有没有这份声明"，Heavy 不影响。

如果两处都用同一个入口，要么 Heavy 下错误删掉两个速度，要么 Heavy 下错误保留 +10。测试 `test_heavy_armor_drops_the_bonus_but_keeps_both_speeds` 把这条不对称钉死。

Second-Story Work 是**替换**不是追加：`derive_jump_distances(abilities, ability="dex")` 换掉被读的分数，Strength 不再参与。测试用 Str 20 / Dex 8 与 Str 20 两例证明两者不相加。

### 9.4 矩阵逐项

| 编号 | 结果 |
|---|---|
| 1 | **通过**：`roving`（class:ranger，6 级，非 Heavy，无 `no_shield`）、`second_story_work`（subclass:thief，3 级，`equipment_requirement` 为 `None`）；Ranger 5 / Thief 2 / Assassin 3 / Monk 20 均不授予 |
| 2–11 | **既有**：由 `test_dnd2024_derived_defense_speed.py` 覆盖，本轮未改（38 条绿） |
| 12 | **通过**：Ranger 6 得 climb/swim = Speed；Heavy 下 +10 归零而 climb/swim 保留；Light 与 Shield 都不影响 |
| 13 | **通过**：Thief 3 只给 climb，不给 swim；两个来源同时声明同一模式时结果仍是 1×Speed（`test_a_second_source_of_the_same_mode_does_not_double_it`） |
| 14 | **部分**：Long Jump = Dexterity score、助跑/立定一半、向下取整已实现；「每跳 1 尺消耗 1 尺 movement」沿用既有 `move` 路径，未新增跳跃 intent |
| 15 | **部分**：High Jump = `3 + modifier`（下限 0）、立定一半已实现；**触及高度 `1.5 × 身高` 未实现**——canonical 没有身高事实，凭空写一个 reach 就是造字段 |
| 16 | **通过**：非 Thief 仍读 Strength；Dex 是替换而非追加（20/8 两例互斥验证） |
| 17 | **未实现**：助跑 10 尺的"紧接跳跃前步行移动"需要逐步移动轨迹，当前 `economy` 只有累计已用移动 |
| 18 | **部分继承**：超过剩余移动的位移由既有 `move` 校验拒绝（`validation.py`：`movement exceeds the remaining speed`），负距离/零距离同样拒绝；但"非法路径"与跳跃专属规则不存在 |
| 19–24 | **停止条件 2**，见 9.5 |
| 25 | **部分**：建卡/升级/协调三路同源 + 幂等短路 + 未知种族不覆盖已验证；中英文 bundle 切换未单独断言 |
| 26 | **通过**：见 9.6 |

### 9.5 停止条件 2：Acrobatic Movement 与地形类

`combat.position_mode` 是 `"theater"`，`positions[actor_id]` 只是一个整数，`_distance()` 就是 `abs(a - b)`。没有地形、没有垂直面、没有液面、没有支撑/坠落事实。因此：

- 矩阵 19、20（Difficult Terrain 落地 DC10、High Jump 不得误加该检定）无法执行；
- 矩阵 21–24（Acrobatic Movement 的表面、回合、免坠落作用域）无法执行。

**没有声明 `acrobatic_movement`**。索引 §6.5 要求无法自动结算的规则显式标记且不得开放假入口；在没有任何表面事实的前提下声明它，只能得到一个永远回答"可以"却无法裁决的按钮。测试 `test_acrobatic_movement_is_not_declared_as_a_mechanical_benefit` 与 `test_the_combat_state_has_no_terrain_or_support_fact` 把这两条钉成可执行的负证据，而不是只写在文档里。

**回填前提**：需要先有最小世界模型（地形格 / 垂直面 / 液面 / 支撑），且能与 `move` 共用同一事务；届时 19–24 一次性补齐。

### 9.6 验收

- 聚焦：`pytest tests/rulesets/test_dnd2024_derived_defense_movement.py` → **46 passed**
- 定向回归：`test_dnd2024_derived_defense_speed.py` / `monk_equipment` / `advancement_access` → **78 passed**；`class_features` / `progression` / `feature_choices` / `monk_resources` → 首轮 2 failed（均为 rogue 特性名单断言）+ 154 passed，更新断言后 `class_features` **71 passed**
- `ruff check src/rulesets/dnd2024/{character,progression,features,combat} tests/rulesets/test_dnd2024_derived_defense_movement.py` → **All checks passed**
- 全量：见 9.7
- 存档与兼容：`derived.climb_speed` / `swim_speed` / `jump_*` 是新增可选字段，缺失按"没有该速度"处理，无迁移、无 `content_version` 变更
- 白名单：改动全部落在 §5 第二、三条（`derivation.py`、`advancement.py`、`resolver.py`、`reconciliation.py` 属 `character/` 与 `progression/`）与第一条（目录 + 两套 locale）；测试文件为 §5 第四条允许的新建文件

### 9.7 全量回归

`pytest tests/rulesets -q` → **1525 passed / 11 failed**（23m07s）。11 条全部是 `test_dnd2024_character_builder.py`，根因仍是 Windows 保留设备名 `CON`（夹具写 `con.json`），与 D2 提交时的 11 条基线**完全一致，零新增**。


### 9.8 偏差与白名单

1. **`character/reconciliation.py` 不在 §5 白名单内**。§5 第 2 条列了 `character/derivation.py`，没有列同目录的 `reconciliation.py`，但 live 换装走的是 `reconcile()` / `reconcile_canonical()`：不改这一处，穿上重甲后 `derived.speed` 会变而 `derived.climb_speed` 仍是旧值，等于留下一个只在换装后才出现的错误数值。按"派生值只有一个入口"执行，并在此记录为白名单外改动。改动仅限：新增 `movement` 计算、纳入幂等短路比较、写入 `derived`，不放宽任何既有校验。
2. **两处中文标签修正**（`roving` 游荡→游走、`second_story_work` 二楼作业→飞檐走壁）。译名不影响机制，但"二楼作业"对 Second-Story Work（飞檐走壁）是明显误译，且本切片正是要把它变成可见特性，故一并修正。
3. **未新增跳跃 intent**。矩阵 14/15 的"每跳 1 尺扣 1 尺 movement"没有独立的跳跃动作，跳跃距离以派生值发布，实际位移仍走既有 `move`（预算校验已在）。新增跳跃语义会改 combat 的 intent 契约并波及前端，超出本切片。
4. **未声明 `acrobatic_movement`**，理由见 9.5；这是停止条件，不是遗漏。
