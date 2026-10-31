# 切片 P4 任务书：False Life（临时生命值）

版本：v2（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 P1、P5、P2、P3 之后执行。无强机制依赖（不用 upcast_target_count / repeat_targets），但 P1-P3 已改 `catalog.py` / `validation.py` / `resolution.py`，行号会偏移，描述以语义位置为准。

---

## 1. 目标

为 D&D 2024 规则集实现 **false_life（虚假生命，1 环）** 的完整闭环：目录数据 → 自我施放（含升环固定加值）→ 临时生命值授予（keep-max）→ 受伤时优先吸收 → 测试。

本切片沉淀一项通用原语：

- **临时生命值（temp HP）资源**：新效果模式 `temp_hp` 授予；reducer 端 keep-max 语义 + 伤害优先吸收。后续 heroism、armor of agathys 等只需目录数据化。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 虚假生命：一环死灵（术士、法师）。施法时间动作；施法距离自身；成分 V、S、M；持续时间立即。**你获得 2d4+4 点临时生命值。** 升环施法：使用的法术位每比一环高一环，其给予的临时生命值就增加 5。
- 临时生命值通则：
  - **优先失去临时生命值**：如果你在有临时生命值时受到伤害，会优先扣除这些值，余下的伤害才会扣到你的生命值上（有 5 临时生命值受到 7 伤害 → 失去全部临时生命值再失去 2 生命值）；
  - **持续时间**：持续直至它们被消耗或你完成一次长休；
  - **不叠加**：已有临时生命值时获得更多，你决定保留原有值或获得新值（10 与 12 二选一，不能是 22）；
  - **既不是治疗也不是生命值**：不能加在生命值上；治疗效果不能恢复它们；获得临时生命值不视为被治疗；满生命值的生物也可以获得；生命值为 0 时获得临时生命值不能令你恢复意识，只有真正的治疗能救你。

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/1环.htm` L113-114（虚假生命）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/临时生命值.htm` L7-21（通则四条）。

### 1.1 必须锁定的规则边界

1. **keep-max 简化，如实记录偏差**：规则书原文是"你决定保留原有值或获得新值"（玩家二选一）。引擎确定性原则下简化为**保留较高者**——两者中较高者永远是理性选择，偏差只存在于玩家故意保留较低值的无意义场景。执行报告必须记录此偏差。
2. **优先吸收发生在 reducer**：伤害事件（`resource.changed`，负 delta）应用时先扣 `temp_hp` 再扣 `hp`；事件本身的 `amount`（打出的伤害）不变，吸收反映在状态上。
3. **temp HP 不是治疗**：授予不清除 `unconscious` / `stable`、不重置死亡豁免、不被 `cannot_regain_hp`（颤栗之触）阻挡、`hp` 与 `max_hp` 不因授予改变。
4. **0 HP 目标拒绝**：`temp_hp` 模式不进"0 HP 目标豁免集合"（healing/buff/stabilize）——引擎一贯拒绝无意义操作（RAW 允许给 0 HP 目标赋 temp 但不恢复意识，引擎直接拒绝，记录偏差）。0 HP 者自我施法本就被意图门挡死（只许 death_save），此边界只影响未来对他人赋 temp 的法术。
5. **升环是固定值**：每比一环高一环 +5（`2d4+4` → 2 环位 `2d4+9`、3 环位 `2d4+14`）。`spell_formula_node` 的 `upcast` 参数要求同面骰公式，**不得**用它表达 flat 加值——用本切片的 `upcast_flat` 字段在分支内自行加常数节点。（v1 举例把「高几环」少算一级，v2 按规则书原文修正，见 §9。）
6. **满血可授予**：`hp == max_hp` 时授予照常（temp 不受 max_hp clamp）。
7. **死亡豁免失败累积看吸收后伤害**：`_apply_hp_change` 现有"`before == 0 and delta < 0` 时累积失败"逻辑使用**吸收后**的 delta——temp 吸收了全部伤害（delta 变 0）就不加失败。

### 1.2 本切片的产品策略与裁剪

- **长休清理推迟**：规则书规定 temp 持续至长休；战斗引擎没有长休钩子，temp 持续存在直至被消耗或被更高的新值覆盖。遗留记录，不在本切片。
- **敌人无 temp HP**：敌方无任何 temp 来源（enemy 分支 fail closed），不实现敌人侧 temp。GM 注入敌人 `resources.temp_hp` 不被支持（敌人档案没有 resources 投影路径）。
- **其它 temp 来源不做**：heroism（专注光环持续赋 temp）、armor of agathys（temp + 反伤）、瞬发 temp 的战斗外授予（aid 的 max_hp 提升是另一机制）——全部推迟，本切片只做 false_life 所需的最小原语。
- **战斗结束不清 temp**：combat.ended 不触碰角色资源（现状行为保持）。
- **不做 temp 的 UI 投影改造**：`_player_view` 暴露 `temp_hp` 字段即完成服务端契约；前端如何展示不在本切片。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `resource.changed` 事件无条件分发到 `_apply_hp_change`（`event["delta"]` 必须存在） | `src/rulesets/dnd2024/combat/reducer.py` L205-207 |
| `_apply_hp_change`：player/companion 走 `character["resources"]`，`hp = max(0, min(max_hp, before + delta))`；hp≤0 → `unconscious` condition + 移除 `rage` + `death_saves`（`before == 0 and delta < 0` 时按 critical 加 1/2 失败，满 3 → `dead`）；delta>0 → 弹出 `unconscious`/`stable`、重置 `death_saves`；enemy 走 `combat["enemies"][raw_id]`，`defeated` condition | `reducer.py` L332-369 |
| `spell_formula_node(base, upcast, spell, slot_level, actor)`：base 必须匹配骰公式正则；upcast 必须是**同面骰**且按级加骰/加值；戏法按等级乘骰数 | `src/rulesets/dnd2024/combat/action_adapter.py` L89-118 |
| `_spell_events` 模式分支与尾部：healing 分支（公式 → `roll_damage_node` → `resource.changed` 带 `healing: True`、`cannot_regain_hp` 阻挡）、condition 应用尾部、`succeeded` 语义 | `src/rulesets/dnd2024/combat/resolution.py` L366-405（P2/P3 后以语义为准） |
| `_validate_spell`：有益目标集合 `{healing, buff, stabilize}` 要求盟友 + 非 dead；其余模式要求敌对；`hp <= 0 and mode not in {healing, buff, stabilize}` → "spell target is not active" | `src/rulesets/dnd2024/combat/validation.py` L449-462 |
| `SPELL_MODES` 白名单与 `spell_effects` 逐字段 fail closed 校验 | `src/rulesets/dnd2024/combat/catalog.py` L16-19、L26-93 |
| `_player_view` 投影 `hp` / `max_hp`（resources dict，缺省 0 容错模式） | `src/rulesets/dnd2024/combat/view.py` L123-126 |
| false_life 法术元数据已存在（level 1、necromancy、sorcerer/wizard、Action、Self、V/S/M、Instantaneous），无需新增 | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json` L993-1012 |
| 测试基建：`SequenceRng` / `goblin()` / `start_combat`；施法职业卡构造先例（`class:wizard` + `class_spell_choices` + `equipment_package:wizard_a`） | `tests/rulesets/dnd2024_monk_common.py`；`tests/rulesets/test_dnd2024_smite.py` L15-56 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

`spell_effects` 新增一条：

```json
"false_life": {"mode": "temp_hp", "range": 5, "target_count": 1, "temp_hp": "2d4+4", "upcast_flat": 5}
```

语义约定：`mode: "temp_hp"` = 授予临时生命值；`temp_hp` = 基础骰公式；`upcast_flat` = 每升一环的固定加值。`target_count: 1` + 盟友校验覆盖"自身"（客户端传自己的 actor_id）。

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

1. `SPELL_MODES` 追加 `"temp_hp"`；
2. `spell_effects` 校验追加：`mode == "temp_hp"` 时 `temp_hp` 字段必填且必须是字符串（公式合法性由 `spell_formula_node` 在结算时 fail closed）；`upcast_flat` 若存在必须是 `0 < int <= 50`。

### 3.3 `src/rulesets/dnd2024/combat/validation.py`

有益目标集合追加 `temp_hp`：`if effect["mode"] in {"healing", "buff", "stabilize", "temp_hp"}:`（盟友 + 非 dead）。**不**加入 0 HP 豁免集合（L461 的 `not in {"healing", "buff", "stabilize"}` 保持不含 temp_hp）。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

`_spell_events` 模式分支追加（healing 分支旁）：

```python
elif mode == "temp_hp":
    node = spell_formula_node(str(effect["temp_hp"]), None, spell, slot_level, actor)
    levels = slot_level - int(spell["level"])
    flat = int(effect.get("upcast_flat", 0) or 0)
    if flat and levels > 0:
        node = {"op": "add", "args": [node, {"op": "constant", "value": flat * levels}]}
    amount, rolls = roll_damage_node(node, rng)
    events.append({
        "type": "resource.changed", "resource": "temp_hp", "target_id": target_id,
        "delta": amount, "amount": amount, "rolls": rolls,
    })
    succeeded = True
```

condition 应用尾部不含 `temp_hp` 模式（false_life 无 condition），天然不应用。

### 3.5 `src/rulesets/dnd2024/combat/reducer.py`

1. `resource.changed` 分发按 resource 键分流：

```python
if event_type == "resource.changed":
    if str(event.get("resource") or "hp") == "temp_hp":
        self._apply_temp_hp_change(snapshot, event)
    else:
        self._apply_hp_change(snapshot, event)
    return
```

2. 新增 `_apply_temp_hp_change`：仅 player/companion——`resources["temp_hp"] = max(int(resources.get("temp_hp", 0) or 0), int(event["amount"]))`（keep-max）；enemy 或其他 kind → `EventBatchError("temp HP target actor is invalid")`（fail closed）。不触碰 `unconscious`/`stable`/`death_saves`/`hp`。
3. `_apply_hp_change` 的 player/companion 分支，负 delta 在 clamp 之前先吸收：

```python
temp = int(resources.get("temp_hp", 0) or 0)
if delta < 0 and temp > 0:
    absorbed = min(temp, -delta)
    resources["temp_hp"] = temp - absorbed
    delta += absorbed
```

死亡豁免失败累积（`before == 0 and delta < 0`）与 hp clamp 使用吸收后的 delta。enemy 分支不动。

### 3.6 `src/rulesets/dnd2024/combat/view.py`

`_player_view` 投影追加 `"temp_hp": int(resources.get("temp_hp", 0) or 0)`（`max_hp` 旁，同款缺省容错）。

### 3.7 测试（新文件）

新文件 `tests/rulesets/test_dnd2024_false_life.py`：职业卡照抄 `test_dnd2024_smite.py` 模式换 `class:wizard`（`class_spell_choices` 选 false_life，`equipment_package:wizard_a`，1 级即可）。测试矩阵见 §4。

回归：`tests/rulesets/test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_invisibility.py`、P2/P3/P5 测试文件全部通过。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`temp_hp` 模式缺 `temp_hp` 字段 / `upcast_flat: "5"`（非 int）/ 超 50 | `CombatCatalogError` |
| 2 | 1 级法师自我施放 false_life（SequenceRng 两骰 2,3 → 2d4=5+4=9） | `resource.changed`（`resource: "temp_hp"`, amount=9, rolls=[2,3]）；apply 后 state `temp_hp == 9`；`hp` 与 `max_hp` 不变 |
| 3 | keep-max：已注入 temp 10 再获 9 → 仍 10；temp 8 再获 12 → 12 | state 断言（用两次施放或注入首次 temp） |
| 4 | 优先吸收：hp 20 + temp 5，受 7 伤害 | state：`temp_hp == 0`、`hp == 18` |
| 5 | 全吸收：hp 20 + temp 7，受 5 伤害 | state：`temp_hp == 2`、`hp == 20`；无 `unconscious` |
| 6 | 满血授予：hp == max_hp 时施放 | `temp_hp` 照常增加，`hp` 不变（不受 max_hp clamp） |
| 7 | 升环：2 环位施放（2d4+4+5） | SequenceRng 两骰 1,1 → amount=11；3 环位（2d4+4+10）→ 16 |
| 8 | 敌我校验：对 goblin 施 false_life | `CombatIntentError`（temp_hp 属有益集合，需盟友） |
| 9 | 非治疗：自挂 `cannot_regain_hp`（注入或颤栗之触）后施 false_life | 授予照常（对照 healing 被挡的既有行为不回归） |
| 10 | 死亡豁免不受吸收外伤害：0 HP + 注入 temp 5 的玩家受 3 伤害 | `death_saves.failures` 不增加（吸收后 delta=0）、hp 仍 0 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_false_life.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_scorching_ray.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

（已执行切片的测试文件名按实际存在清单补充。）

## 6. 禁止事项

1. 禁止 temp 叠加（必须 keep-max）；禁止把 keep-max 改成玩家可选。
2. 禁止把 temp HP 当治疗处理：不清 `unconscious`/`stable`、不重置 `death_saves`、不受 `cannot_regain_hp` 影响、不走 `_apply_hp_change` 的 `delta > 0` 分支。
3. 禁止用 `spell_formula_node` 的 `upcast` 参数表达 flat 加值（同面骰限制）；flat 用 `upcast_flat`。
4. 禁止给 enemy 侧实现 temp HP（fail closed）。
5. 禁止实现长休清理、战斗结束清理（§1.2 已裁剪）。
6. 禁止 heroism / armor of agathys / aid 等其它 temp 或 max_hp 机制。
7. 禁止在 resolution / validation 写具体法术名分支——false_life 只允许目录数据 + `temp_hp` 模式。

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. `resource.changed` 事件的既有消费方（投影/审计/前端契约测试）假设 `resource` 恒为 `"hp"` 且无法容忍 `"temp_hp"`；
2. `character["resources"]` 结构在既有写入路径上没有缺省容错（`temp_hp` 键不存在导致 KeyError 而非按 0 处理）；
3. 0 HP + temp 吸收与死亡豁免累积的交互无法同时满足 §1.1 边界 7 与既有死亡豁免测试；
4. 任何白名单外文件必须修改才能通过验收。

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P4_FALSE_LIFE_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况、§3 语义与实现的偏差（尤其 keep-max 与 0 HP 拒绝两条）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

## 9. 修订记录

- v1（2026-10-29）：初版；§1.1#5 与矩阵 #7 的升环举例把「高几环」少算一级（等价 `slot_level - 2`，会使 2 环位错误地 +0），与 §1 规则书原文及 §3.4 代码（`flat * (slot_level - spell_level)`）矛盾。
- v2（2026-10-29）：审核修正——§1.1#5 举例改为 2 环 `2d4+9`、3 环 `2d4+14`；矩阵 #7 改为 2 环位 → 11、3 环位 → 16。实现与测试（`test_upcast_adds_five_per_level_above_the_first`：2 环位 11、3 环位 16）自始按规则书原文，无需改动；§10.3.3 记录的即本处矛盾。

---

## 10. 执行报告（2026-10-29）

### 10.1 变更文件清单

`git status --short` 结果（严格落在 §3 白名单内，无其它阶段残留）：

```text
 M src/rulesets/dnd2024/combat/catalog.py
 M src/rulesets/dnd2024/combat/reducer.py
 M src/rulesets/dnd2024/combat/resolution.py
 M src/rulesets/dnd2024/combat/validation.py
 M src/rulesets/dnd2024/combat/view.py
 M templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json
?? tests/rulesets/test_dnd2024_false_life.py
```

逐文件改动（`git diff --stat`：6 files changed, 61 insertions(+), 3 deletions(-)，另有新增测试文件）：

- `srd_combat_core.json`：`spell_effects` 新增 `false_life`（`mode: "temp_hp"`, `range: 5`, `target_count: 1`, `temp_hp: "2d4+4"`, `upcast_flat: 5`）。
- `catalog.py`：`SPELL_MODES` 追加 `temp_hp`；`temp_hp` 模式要求 `temp_hp` 为字符串；`upcast_flat` 若存在必须是 `0 < int <= 50` 的非 bool int。
- `validation.py`：有益目标集合改为 `{"healing", "buff", "stabilize", "temp_hp"}`；0 HP 豁免集合保持不含 `temp_hp`。
- `resolution.py`：`_spell_events` 新增 `temp_hp` 分支（`spell_formula_node(..., None, ...)` + `upcast_flat * levels` 常量节点 → `resource.changed` / `resource: "temp_hp"`）。
- `reducer.py`：`resource.changed` 按 `resource` 分流到 `_apply_temp_hp_change`；新增该方法（player/companion keep-max，其余 `EventBatchError`）；`_apply_hp_change` 玩家/同伴分支在 clamp 前先吸收 temp HP，死亡豁免累积使用吸收后的 delta。
- `view.py`：`_player_view` 追加 `"temp_hp"` 投影（缺省容错同 `max_hp`）。
- `tests/rulesets/test_dnd2024_false_life.py`：新增 12 个用例（含 2 组参数化），覆盖 §4 全部 10 项矩阵。

### 10.2 测试矩阵逐项结果

| # | 用例 | 结果 |
|---|---|---|
| 1 | `test_catalog_rejects_invalid_temp_hp_declarations`（缺 `temp_hp` / 非字符串 `temp_hp` / `upcast_flat: "5"` / `upcast_flat: 51`，并验证合法条目可通过） | 通过 |
| 2 | `test_false_life_grants_temp_hp_from_the_base_formula`（骰 2,3 → amount=9、rolls=[2,3]；`temp_hp=9`，`hp`/`max_hp` 不变） | 通过 |
| 3 | `test_existing_temp_hp_is_kept_unless_the_new_value_is_higher`（参数化：10 对 9 → 10；8 对 12 → 12） | 通过（2 例） |
| 4 | `test_damage_absorbs_temp_hp_before_reducing_hp`（hp 20 + temp 5 受 7 → temp 0、hp 18、无 unconscious） | 通过 |
| 5 | `test_fully_absorbed_damage_leaves_hp_untouched`（hp 20 + temp 7 受 5 → temp 2、hp 20） | 通过 |
| 6 | `test_temp_hp_is_granted_at_full_hp`（满血授予，`hp` 仍等于 `max_hp`，不受 clamp） | 通过 |
| 7 | `test_upcast_adds_five_per_level_above_the_first`（参数化：2 环位骰 1,1 → 11；3 环位 → 16） | 通过（2 例，见 10.3 偏差第 3 条） |
| 8 | `test_false_life_rejects_a_hostile_target`（对 goblin 施放 → 校验 `ok=False`，错误含 allied） | 通过 |
| 9 | `test_cannot_regain_hp_does_not_block_temp_hp`（自挂 `cannot_regain_hp` 后授予照常，`hp` 不变） | 通过 |
| 10 | `test_absorbed_damage_does_not_add_death_save_failures`（0 HP + temp 5 受 3 → temp 2、hp 0、failures 仍为 1） | 通过 |

### 10.3 语义与实现的偏差

1. **keep-max 简化（任务书已声明的偏差）**：RAW 为“你决定保留原值或获得新值”，引擎按确定性原则实现为保留较高者（`max(现有, 新值)`），不向玩家开放二选一。故意保留较低值这一非理性场景不在支持范围。
2. **0 HP 目标拒绝（任务书已声明的偏差）**：RAW 允许给 0 HP 目标赋 temp 但不会恢复意识；引擎在 `_validate_spell` 中对 `hp <= 0` 且模式不在 `{healing, buff, stabilize}` 的目标直接报 “spell target is not active”，即拒绝。该边界只影响未来对他人赋 temp 的法术；0 HP 自我施法本就被意图门挡死。
3. **升环数值自相矛盾（实现以规则书原文为准）**：任务书自身存在一处数值冲突——
   - §1 引用规则书原文“使用的法术位每比一环高一环，其给予的临时生命值就增加 5”，且 §3.4 代码 `flat * (slot_level - spell_level)` 与之一致（2 环位 +5、3 环位 +10、4 环位 +15）；
   - 但 §1.1#5 的举例“3 环 2d4+9、4 环 2d4+14”与矩阵 #7“3 环位（2d4+4+5）、4 环位 → +10”把“高几环”少算了一级（相当于 `slot_level - 2`，会使 2 环位错误地只 +0）。
   
   实现取规则书原文 + §3.4 代码，即每比一环高一环 +5。测试据此用“2 环位 +5（该格恰为矩阵期望的 amount=11）”“3 环位 +10”验证固定加值语义；矩阵 #7 的槽位标签按正确级数修正。
4. **其它实现取舍**：`_apply_temp_hp_change` 读 `resources.get("temp_hp", 0)`，不依赖该键预先存在（§7 停止条件 2 未触发）；`resource.changed` 的公共事件投影允许 `resource` 字段（`runtime.py` 的 `allowed_fields` 已含 `"resource"`），既有消费方不假设恒为 `"hp"`（§7 停止条件 1 未触发）。

### 10.4 验收命令结果

```text
# 1) 新测试
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_false_life.py -q
12 passed

# 2) 任务书 §5 指定回归子集
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_scorching_ray.py -q
134 passed in 431.59s (0:07:11)

# 3) 全量
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
561 passed, 39 deselected, 35 warnings in 2434.84s (0:40:34)
```

失败 0 条、跳过 0 条；无需要按“11 项已知上游豁免”逐条判定的失败用例。新增 12 例相对 P3 基线（549 passed）正好构成 549 + 12 = 561。

### 10.5 遗留问题

1. **长休清理推迟**：战斗引擎没有长休钩子，`temp_hp` 持续存在直至被消耗或被更高新值覆盖。§1.2 已裁剪，未实现。
2. **敌人无 temp HP**：敌方无任何 temp 来源，reducer 对非 player/companion 的 `temp_hp` 事件 fail closed；敌人档案也没有 resources 投影路径。
3. **其它 temp 来源未做**：heroism（专注光环持续赋 temp）、armor of agathys（temp + 反伤）、aid（max_hp 提升）均推迟；本切片只沉淀 `temp_hp` 最小原语。
4. **战斗结束不清 temp**：`dnd2024.combat.ended` 不触碰角色资源（保持现状）。
5. **UI 投影未改造**：仅 `_player_view` 暴露 `temp_hp` 字段完成服务端契约；`gameplay_view` 等前端聚合层如何展示不在本切片。
6. **对他人赋 temp 的 0 HP 边界**：当前引擎一律拒绝（见 10.3 第 2 条），未来若出现对倒地盟友赋 temp 的法术需重新评估该边界。


### 10.6 勘误（2026-10-30，P1–P9 / R1 提交后修复）

- 本任务书 §10.2 表 #10 与主计划 P4 审核记录中的旧结论「临时 HP 全部吸收伤害时不增加死亡豁免失败」不符合 2024 规则：0 HP 角色受到伤害时，即使伤害被临时 HP 完全吸收，仍视为受到伤害，普通伤害死亡豁免失败 +1、重击 +2。
- 已在 `combat/reducer.py` 的 `_apply_hp_change` 修复：在临时 HP 吸收前保存原始伤害事实，死亡豁免按原始伤害判定。旧测试 `test_absorbed_damage_does_not_add_death_save_failures` 更名为 `test_fully_absorbed_damage_still_adds_a_death_save_failure` 并修正期望；新增重击 +2、部分吸收与穿透伤害用例。
- 原文旧执行结果不重写；本勘误为准。
