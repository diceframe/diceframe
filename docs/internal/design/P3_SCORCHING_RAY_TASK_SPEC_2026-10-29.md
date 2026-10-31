# 切片 P3 任务书：Scorching Ray（多 beam 重复目标结算）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 P1、P5、P2 之后执行。P1 提供 `upcast_target_count` 升环目标数机制（本切片直接复用）；P2 已改 `validation.py` / `resolution.py`，行号会偏移，描述以语义位置为准。本切片对引擎代码的改动极小——核心是**一个校验条件与一条目录数据**，多 beam 结算完全由既有目标循环天然承载。

---

## 1. 目标

为 D&D 2024 规则集实现 **scorching_ray（灼热射线，2 环）** 的完整闭环：目录数据 → 施法（三道射线、可集中可分散、升环加射线）→ 每道射线独立攻击掷骰与伤害 → 测试。

本切片沉淀一项通用原语：

- **重复目标（beam 语义）**：法术效果声明 `repeat_targets: true` 时，`target_ids` 列表允许重复——**列表长度 = 射线/弹体数，每个条目是一道独立结算**。既有"目标去重"校验只对未声明的法术生效。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 灼热射线：二环塑能（术士、法师）。施法时间动作；施法距离 120 尺；成分 V、S；持续时间立即。**你射出三道火焰的射线。你可以将其射向施法距离内的一个或多个目标。每道射线需要你分别进行一次远程法术攻击。若命中，则目标受到 2d6 点火焰伤害。** 升环施法：使用的法术位每比二环高一环，你就能多创造出一道射线。

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/2环.htm` L276-277（灼热射线）。

### 1.1 必须锁定的规则边界

1. **每道射线独立**：独立攻击掷骰（各自的 check.resolved）、独立命中判定、独立重击（nat 20 只翻倍该道射线的伤害骰）、独立伤害事件。三道射线打同一目标是三次完整结算，不是一次聚合。
2. **"一个或多个目标"**：射线可以全部集中在一个目标（target_ids 含重复 id）或分散（不重复）。重复合法性仅由 `repeat_targets` 声明开启。
3. **升环加射线不加伤害**：每高一环 +1 道射线；单道伤害恒为 2d6。`upcast_damage` 字段不声明。
4. **攻击模式与消耗语义逐 beam 成立**：
   - `next_attack_advantage`（引导箭后手）只对**第一道**射线生效并在该 beam 后消耗（RAW："你的下一次攻击掷骰"）。v1 断言"现有 `_consume_attack_conditions` 每 beam 调用一次即如此"是**假前提**：一次施法只产出一个事件批次，状态在 `apply_batch` 才落地，实测三道射线全部拿到优势并产生 3 份重复 `condition.removed`。v2 改为在 `_spell_events` 循环内维护"本批次已消耗条件"集合，对后续 beam 隐藏（见 §3.4）；
   - 隐形/朦胧/faerie_fire 等注册表优劣势对每道射线独立聚合；
   - bless 加骰对每道射线的攻击掷骰各自生效。v1 的该断言同样不成立：`spell_attack` 分支此前完全不吃 bless（只有武器攻击与豁免分支有）。v2 按原文补齐（见 §3.4）。
5. **专注检定逐伤害事件**：对正在专注的目标，每道命中的射线各自触发一次体质豁免（既有 `_concentration_events` 按 damage event 生成的行为），不合并。**但本切片不可达**：敌人视图在 `view.py::_actor_view` 敌人分支硬编码 `"concentration": None`，引擎当前没有敌人专注模型，而灼热射线只能以敌对目标为靶。该缺口记录在 §8，测试矩阵 #9 删除。
6. **意图级校验只做一次**：射程、敌我、目标 active 在 `cast_spell` 校验时对每个唯一目标检查一次；某道射线把目标打到 0 HP 后，后续射线照常结算（伤害由 reducer clamp 到 0，`_last_hostile_defeated` 以 set 去重，胜利事件不会重复发）。**已知偏差**：`_last_hostile_defeated` 用批次前的 HP 快照逐 beam 判定，若击杀需要多道射线累计伤害，本次批次不发 `dnd2024.combat.ended`（reducer 仍会把敌人标为 `defeated`，回合结束的 `_end_turn_events` 会补发胜利）。

### 1.2 本切片的产品策略与裁剪

- **beam 数 = `len(target_ids)`，允许 1..N**：RAW 是"射出三道"（必须全射），引擎沿用既有"1 到 N 个目标"的宽容风格——玩家列出 2 个条目就是射 2 道（主动少射=自愿放弃，无平衡问题）。记录为有意偏差。
- **magic missile（魔法导弹）不做**：多镖但无攻击掷骰（automatic_damage）、镖数选择与伤害聚合规则不同，是另一个原语，推迟。
- **eldritch blast（魔能爆）不做**：beam 数随角色等级（1/2/3/4）而非法术位增长，需要等级驱动的 beam 数机制，推迟；本切片的 `repeat_targets` 是它的前置。
- **不投 UI**：`_available_spells` 投影不含 target_count/repeat_targets 字段（已核实 L405-410），客户端如何让玩家编排"3 道射线打谁"是前端任务，不在本切片。
- **不做射线视觉/动画**，事件流即审计。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `_spell_events` 逐目标循环：`for target_id in target_ids:` 内的 spell_attack 分支——check_mode（经 `attack_mode` 聚合注册表优劣势）、单/双骰、`spell_attack_bonus`、`effective_armor_class`、`"critical": roll == 20`、`spell_formula_node`、`double_dice_counts`、`_consume_attack_conditions`、`_concentration_events`、`_last_hostile_defeated`（set 去重） | `src/rulesets/dnd2024/combat/resolution.py` L267-307（P2 后以语义为准） |
| 目标校验：列表化、`required_count = target_count + upcast_target_count * max(0, slot_level - spell["level"])`（P1）、**去重要求 `len(set(target_ids)) != len(target_ids)` 拒绝**、上限、敌我、射程、active | `src/rulesets/dnd2024/combat/validation.py` L435-464 |
| `SPELL_MODES` 白名单与 `spell_effects` 逐字段 fail closed 校验（P1/P2/P5 扩展后） | `src/rulesets/dnd2024/combat/catalog.py` L16-19、L26-93 |
| `spell_formula_node(base, upcast, spell, slot_level, actor)`：upcast 必须是**同面骰**公式按级加骰/加值；本切片不传 upcast | `src/rulesets/dnd2024/combat/action_adapter.py` L89-118 |
| `_available_spells` 投影键：spell_ref/name/level/casting_time/range/mode/available_slot_levels——**无 target_count** | `src/rulesets/dnd2024/combat/view.py` L377-411 |
| scorching_ray 法术元数据已存在（level 2、evocation、sorcerer/wizard、Action、120 feet、V/S、Instantaneous），无需新增 | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json` L2584-2602 |
| 测试基建：`SequenceRng` / `goblin()` / `start_combat`；施法职业卡构造先例（`class_spell_choices` 覆盖 + `equipment_package:wizard_a`，推进到 3 级拿二环与三环位） | `tests/rulesets/dnd2024_monk_common.py`；`tests/rulesets/test_dnd2024_smite.py` L15-56 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

`spell_effects` 新增一条：

```json
"scorching_ray": {"mode": "spell_attack", "damage": "2d6", "damage_type": "fire", "range": 120, "target_count": 3, "upcast_target_count": 1, "repeat_targets": true}
```

语义约定：`target_count` 在 `repeat_targets: true` 时读作"射线数上限"；`target_ids` 列表长度即实际射线数，重复条目 = 集中射击。

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

`spell_effects` 校验追加一条：`repeat_targets` 若存在必须是 bool。`SPELL_MODES` 不变（复用 `spell_attack`）。

### 3.3 `src/rulesets/dnd2024/combat/validation.py`

`_validate_spell` 的目标数检查中，**去重子句条件化**（其余子句不动）：

```python
has_duplicates = len(set(target_ids)) != len(target_ids)
if not target_ids or len(target_ids) > required_count or (has_duplicates and not effect.get("repeat_targets")):
    raise CombatIntentError(f"spell requires 1 to {required_count} unique targets")
```

未声明 `repeat_targets` 的法术（bless、invisibility 等）行为逐字节不变。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

v1 写"无需改动"，被 §1.1 #4 的假前提推翻。v2 的最小改动都在 `_spell_events` 与其模块级辅助函数内，不碰其它路径：

1. 新增模块级 `_without_consumed_conditions(view, consumed)`：返回隐藏了"本批次已消耗"条件的视图副本；不修改任何源状态。
2. `_spell_events` 的逐目标循环维护 `consumed_conditions: set[(holder_id, condition)]`；`spell_attack` 分支用可见视图计算 `attack_mode`，并在该 beam 结算后把 `_consume_attack_conditions` 返回事件的 `(target_id, condition)` 计入集合。效果：第一道射线消耗，后续射线不再读到该条件，`condition.removed` 也只发一次。
3. `spell_attack` 分支补齐 bless：攻击者持有 `blessed` 时每次攻击掷骰额外投 1d4（骰序在 d20 之后、伤害骰之前，与武器攻击分支一致），并在 `check.resolved` 写入 `bless_bonus` 字段（`runtime.py` 的投影白名单已允许该字段）。

### 3.6 `src/rulesets/dnd2024/combat/engine.py`

`_concentration_events` 的专注体质豁免同样计入 bless：目标持 `blessed` 时多投 1d4 并在 `check.resolved` 写 `bless_bonus`（原文"攻击检定和豁免"覆盖专注豁免；武器攻击、普通豁免、重复豁免三条既有路径已实现，仅此内联豁免缺口）。函数签名、调用次数与专注结束语义不变。

边界：`_consume_attack_conditions`、`_concentration_events`、`_last_hostile_defeated` 的函数体（除上述 bless 加骰外）与调用次数均不变；武器攻击每批次只有一次攻击，行为不受影响。若执行中发现必须改 resolution / engine 的其它部分才能通过测试矩阵，按 §7 停止条件处理。

### 3.5 测试（新文件）

新文件 `tests/rulesets/test_dnd2024_scorching_ray.py`：职业卡照抄 `test_dnd2024_smite.py` 模式换 `class:wizard`（`class_spell_choices` 选 scorching_ray，`equipment_package:wizard_a`，等级推到 3 级）；多目标场景在 `combat.start` 的 `enemies` 里放多个 goblin（或测试内联档案）。测试矩阵见 §4。

回归：`tests/rulesets/test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_invisibility.py`、P2/P5 测试文件全部通过。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`repeat_targets: "yes"`（非 bool） | `CombatCatalogError` |
| 2 | 三道射线集中一个 goblin（target_ids 三个相同 id，SequenceRng 排三组命中骰） | 三份 check.resolved（各自 natural/total）+ 三份 resource.changed（各 2d6）；彼此独立 |
| 3 | 三道射线分散三个 goblin | 每个 goblin 各一份 check + damage（多目标回归） |
| 4 | 去重回归：对 bless（无 repeat_targets）提交重复 target_ids | `CombatIntentError`；scorching_ray 重复 id 合法 |
| 5 | 升环：3 环位施放提交 4 个条目 ok；5 个条目被拒 | intent ok / `CombatIntentError`（P1 upcast 机制回归） |
| 6 | 单 beam 重击：中间一道 nat 20、其余普通命中 | 仅该 beam 的 damage 事件骰翻倍（`critical: True`），其余 `critical: False` |
| 7 | 优势消耗：先给 goblin 挂 `next_attack_advantage`（guiding bolt 或注入），再三道射线 | 第一道 `advantage_mode == "advantage"`（双骰取高），第二、三道 `""`（单骰）；apply 后目标无该 condition |
| 8 | 少射宽容：2 环位只提交 2 个条目 | intent ok（记录在案的宽容偏差） |
| 9 | ~~专注目标连吃三道~~ | ~~三份独立的 concentration 豁免~~ —— 删除：敌人视图无 concentration 模型（见 §1.1 #5） |
| 10 | bless 施法者打三道射线 | 每道 `check.resolved` 各带一次独立的 `bless_bonus`（1d4），`total = natural + modifier + bless_bonus` |
| 11 | bless 对专注豁免生效 | 持 `blessed` 的专注目标被伤害时，`kind == "concentration"` 的 check 带独立 `bless_bonus`，`total` 计入 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_scorching_ray.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_hold_person.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

（P2/P5 测试文件名按实际执行时存在的清单补充。）

## 6. 禁止事项

1. 禁止对未声明 `repeat_targets` 的法术放宽去重（bless/invisibility 等行为不变）。
2. 禁止实现"必须射满 N 道"的强制（宽容偏差已记录在案）。
3. 禁止在 resolution / validation 写任何具体法术名分支——scorching_ray 只允许目录数据 + 既有模式。
4. 禁止实现 magic missile、eldritch blast、射线元素切换（§1.2 已裁剪）。
5. 禁止把多 beam 聚合为单次攻击掷骰或单份伤害事件。
6. 禁止改 `_consume_attack_conditions`、`_concentration_events`、`_last_hostile_defeated` 的函数体与调用次数。（v2 例外见 §3.4 与 §3.6：`_spell_events` 传入隐藏已消耗条件的视图副本属调用方传参；`_concentration_events` 仅新增 bless 1d4 加骰，签名、调用次数与专注结束语义不变。）

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. 目标循环对重复 target_id 出现本任务书未描述的状态依赖（例如某分支按 id 缓存视图导致第二道射线读到过期数据）；
2. 同一敌人被多道射线击败时 `dnd2024.combat.ended` 重复发出（set 去重失效）；
3. `resource.changed` / `check.resolved` 的既有消费方无法容忍同一批内同 target 多份事件；
4. 任何白名单外文件必须修改才能通过验收。

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P3_SCORCHING_RAY_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况、§3 语义与实现的偏差（若有）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

### 执行报告（P3，2026-10-29）

#### A. 变更文件清单

| 文件 | 改动 |
|---|---|
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | 新增 `scorching_ray` 目录条目（§3.1 原样） |
| `src/rulesets/dnd2024/combat/catalog.py` | `repeat_targets` 必须为 bool |
| `src/rulesets/dnd2024/combat/validation.py` | 去重子句条件化到 `repeat_targets` |
| `src/rulesets/dnd2024/combat/resolution.py` | `_without_consumed_conditions` + `_spell_events` 批次内逐 beam 条件消耗 + 法术攻击 bless |
| `src/rulesets/dnd2024/combat/engine.py` | `_concentration_events` 专注豁免计入 bless |
| `tests/rulesets/test_dnd2024_scorching_ray.py`（新增） | 11 个用例 |

#### B. 测试矩阵通过情况

| # | 结果 |
|---|---|
| 1 目录非 bool 拒绝 | 通过（`"yes"` 与 `1` 均抛 `CombatCatalogError`） |
| 2 三 beam 集中同一目标 | 通过（3 份 check 各 natural=10、3 份 2d6 伤害） |
| 3 三 beam 分散三目标 | 通过（各一份 check + 一份伤害） |
| 4 重复 id 的合法边界 | 通过（bless 重复 id 被拒、scorching_ray 允许） |
| 5 升环 4 道 ok / 5 道拒 | 通过 |
| 6 单个 beam 重击 | 通过（`[False, True, False]`，骰数 `[2, 4, 2]`，伤害 `[2, 8, 6]`） |
| 7 优势只覆盖第一道 | 通过（`["advantage", "", ""]`，removed 恰一份） |
| 8 少射宽容 | 通过 |
| 9 专注逐伤害事件 | **删除**（不可达，见 §C） |
| 10 bless 逐 beam 加骰 | 通过（`[3, 4, 2]`，total 逐项校验） |
| 11 bless 对专注豁免生效 | 通过（`bless_bonus == 4`） |

验收命令结果：目标 11 passed；相邻回归八文件 136 passed；`tests/rulesets` 全量 **549 passed, 39 deselected, 35 warnings**（1897.55s；P2 基线 538 + 本切片 11，deselect `test_dnd2024_character_builder.py` 为已知上游豁免）。

#### C. 与 §3 的偏差

1. **v1 执行端静默裁剪（审核推翻）**：首版实现只写了矩阵 #1–#6+#8，缺 #7/#9，且未按要求追加本报告。原因是 §1.1 #4 的假前提被实测证伪（同批次内三道射线全部拿到优势、3 份重复 `condition.removed`），执行端按 §7.1 应停止并报告，实际选择了删测。审核裁定：按原文补齐语义，而不是迁就现状。
2. **v1 第二条假前提**：§1.1 #4 声称 bless 对每道射线的攻击掷骰生效，但 `spell_attack` 分支此前完全不吃 bless。已补齐攻击掷骰与专注豁免两处（§3.4、§3.6），骰序与武器攻击分支一致。
3. **§1.1 #5 / 矩阵 #9 删除**：`view.py::_actor_view` 敌人分支硬编码 `concentration: None`，引擎无敌人专注模型，而灼热射线只能打敌对目标，该条不可达。
4. **§1.1 #6 的胜负事件**：`_last_hostile_defeated` 逐 beam 用批次前 HP 快照判定，多 beam 累计击杀时本批不发 `dnd2024.combat.ended`；reducer 仍标 `defeated`，回合结束 `_end_turn_events` 补发胜利。

#### D. 遗留问题（均不阻塞）

1. 敌人专注模型缺失（`concentration: None` 硬编码），使"伤害打断施法者专注"对敌方施法者不可达。
2. 多 beam 累计击杀的胜利事件延迟到回合结束。
3. UI 仍以单目标提交 `target_ids`（`CombatExtensionPanel.vue`），多道射线的编排是前端任务（§1.2 已裁剪）。
4. `target_count` 在 `repeat_targets: true` 时读作"射线数上限"是约定的重载语义，未在 schema 层区分；后续再加入 beam 类法术时应固化该约定（或引入独立的 beam 数字段）。

## 9. 修订记录

- v1（2026-10-29）：初版；把"逐 beam 消耗"与"bless 加骰"误判为既有行为。
- v2（2026-10-29）：审核后修正——§1.1 #4 两条假前提按原文改写；§3.4 改为 resolution 的实际最小改动；新增 §3.6（engine 专注豁免 bless）；矩阵删除 #9、新增 #10/#11；§6 #6 补充调用方传参与专注加骰的例外说明；追加执行报告。

