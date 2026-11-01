# 切片 P6 任务书：Misty Step（传送原语 + 速度修改数据驱动）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 P1（invisibility）、P5（stoneskin）、P2（hold_person）、P3（scorching_ray）、P4（false_life）之后执行。P1 提供 conditions 注册表骨架；P2 提供注册表键 `speed_zero` 与 `check_modes.condition_flags`；P4 已把 `temp_hp` 加入 `SPELL_MODES` 与 `_validate_spell` 盟友集合。前置切片合入后本任务书引用的行号会偏移，描述以语义位置为准，不要按行号机械对照。catalog 校验的"允许键集合"以已执行切片的并集为准。

---

## 1. 目标

为 D&D 2024 规则集实现 **misty_step（迷踪步，2 环）** 的完整闭环：目录数据 → 附赠动作施法（自身、二环位）→ 1D 带符号位移结算（不消耗移动力、不触发借机攻击）→ 测试。

本切片同时沉淀两项通用原语并偿还一项债：

1. **传送原语**：新效果模式 `"teleport"`——intent 携带带符号位移距离（`teleport_distance`），结算为一条 `forced` 的 `dnd2024.position.changed` 事件（无 `movement_cost`）：不消耗移动力经济、不受剩余移动力限制、不触发借机攻击、零 RNG 消耗；
2. **速度修改数据驱动**：conditions 注册表条目可声明 `speed_modifier`（整数尺数修正），`check_modes.py` 新增纯函数 `effective_speed(registry, conditions, base_speed)` 聚合"速度归零压倒一切修正，否则求和后向下取整"；
3. **偿还速度债**：`engine._fresh_economy` 的 `slowed_10` 硬编码删除，改走注册表；P2 显式推迟的"speed_zero → 回合开始 economy movement=0 投影"在本切片落地（P2 只做了 validation 门控）。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 迷踪步：二环咒法（术士、魔契师、法师）。施法时间**附赠动作**；施法距离**自身**；成分 V；持续时间立即。你短暂地被银白的雾气所笼罩，传送到至多 30 尺内一个你能看见且未被占据的空间。
- 冷冻射线（速度修正的既有来源）：戏法（术士、法师）。远程法术攻击；命中时目标受 1d8 寒冷伤害，且其速度减少 10 尺，直到施法者的下一回合开始为止。
- 借机攻击：当一个你可见的生物离开你的触及范围时你可以发动一次借机攻击（消耗反应，徒手打击或近战武器攻击，发生于对方离开触及前一刻）。**解释依据（已裁决，参照贤者谏言判据）**：借机攻击只由"使用速度的移动"触发；法术传送、被迫位移（推开/抱走）均**不**触发。撤离动作（"回合余下时间里你的移动不会引发借机攻击"）的措辞同样以"移动"为锚，与此解释互证。
- 麻痹 / 昏迷：速度归零，且无法被增加（P2 已注册 `speed_zero`，本切片把它投影进回合开始的 economy）。

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/2环.htm` L251-252（迷踪步）；
- `.rulebooks/dnd5r/玩家手册2024/法术详述/0环.htm` L82-83（冷冻射线，速度 -10 的唯一既有施加来源）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/近战攻击.htm` L18（借机攻击触发措辞）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/动作.htm` L14（撤离动作）；
- `.rulebooks/dnd5r/玩家手册2024/术语汇编/状态.htm` L87-92（麻痹）、L133-140（昏迷）。

### 1.1 必须锁定的规则边界

1. **传送不是移动**：不消耗 movement 经济（事件无 `movement_cost` 键，reducer 现状即不扣）、不受剩余移动力与速度归零限制（先 move 30 耗尽移动力后仍可传送）、**不触发借机攻击**（§1 解释依据；引擎 push 路径同理不触发，两者是同一解释的一致实现）。
2. **位移由 intent 提供带符号整数**：`teleport_distance` 必须是非 bool 的非零 int，且 `abs(teleport_distance) <= 效果 range`（30）。事件复用 push 先例：`{"type": "dnd2024.position.changed", "actor_id": 施法者, "distance": teleport_distance, "forced": True}`。方向正负即 1D 前后。
3. **teleport 模式只能以施法者为唯一目标**（RAW 施法距离自身）：`target_ids == [施法者 actor_id]`，对其他任何目标（盟友或敌人）一律拒绝。
4. **传送零 RNG**：不掷任何骰、不发 `check.resolved`、不请求 bless 等任何加骰（SequenceRng 可断言队列零消耗）。
5. **economy 快照先于过期（迁移等价性）**：`_fresh_economy` 读的是**预结算视图**（含 `target_turn_start` 期的 condition）；reducer 在 `turn.advanced` 里**先** `_expire_conditions` **后**安装 economy。ray_of_frost 场景的现行行为（目标下一回合 economy 已减 10、且回合开始后 slowed_10 已过期）必须逐事件保持——registry 化是纯重构，不得重排任何顺序。
6. **speed_zero 压倒修正**：任一活动 condition 声明 `speed_zero` → 速度为 0，忽略一切 `speed_modifier`；否则 `max(0, base + Σ modifier)`。
7. **economy 的 `speed` 与 `movement` 同源**：回合开始两者都等于 `effective_speed` 的返回值（`speed` 是本回合上限、`movement` 是剩余，现状语义不变）。
8. **速度修改只在回合开始的 economy 刷新生效**：condition 只会在其他 actor 的回合施加到目标身上（施法者施法、攻击方攻击）；当前 economy 拥有者中途被施加速度 condition 的路径不存在（反应/触发链不发 condition.applied）。**不得**在 reducer 的 `condition.applied` 里加 mid-turn economy 改写。
9. **附赠动作与法术位走既有经济**：`casting_time: "Bonus Action"` → `bonus_action` 成本；二环位消耗 + `slot_spell_cast` 每回合一位上限；misty_step 无专注。全部复用现行机制，不新增分支。
10. **`teleport_distance` 双向 fail closed**：效果模式是 teleport 时 intent 必须携带；效果模式不是 teleport 时 intent 携带该字段必须被拒绝（防客户端写任意位移，照 P5 `resist_type` 先例）。

### 1.2 本切片的产品策略与裁剪

- **"未被占据的空间"**：1D theater 位置模型没有占用语义——普通 move 同样不校验终点占用（两 actor 可共点）。只对传送加占用校验会在相邻敌人共享位置时误拒，制造不一致；整体推迟到位置模型升级（2D/占用）时统一实现。如实记录偏差。
- **"你能看见"**：无视觉系统（与 P1 对 Concealed 的裁剪一致），不实现传送终点的可见性门控。
- **前端适配滞后**：`teleport_distance` 需客户端随 intent 提供（与 P5 `resist_type` 同款选择字段）。本切片引擎先行、服务端权威校验；战斗面板的距离选择器是后续前端任务——面板适配前 misty_step 可经 intent 直交（GM 控制台/测试）。已知偏差，照 P2"投影滞后"先例处理。
- **其他传送法术**（雷鸣步、任意门、回返术等）：本切片只交付模式与 misty_step 数据；其余等数据补录后即插即用，不在本切片落数据。
- **速度提升类**（加速术、大步流星等）：注册表 schema 支持正 `speed_modifier`，但当前无消费者，不落数据、不实现 haste 的额外动作/AC 等整套机制。
- **借机攻击其余规则**（如战技专长阻拦的速度归零 rider、守护者借机等）：与传送无关，不在本切片。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `_fresh_economy` 是 `@staticmethod`，硬编码 `if "slowed_10" in actor.get("conditions", {}): speed = max(0, speed - 10)`；返回 `{actor_id, action, bonus_action, reaction, movement, speed, attacks_remaining, disengaged}` | `src/rulesets/dnd2024/combat/engine.py` L1005-1014 |
| `_fresh_economy` 全部调用点（均经 `self`，改实例方法零调用方改动）：combat.started 的首任 economy、turn.advanced 的下一活动者 economy | `src/rulesets/dnd2024/combat/resolution.py` L89、L637 |
| reducer `turn.advanced`：**先** `_expire_conditions`（含 `actor_turn_start`/`target_turn_start` 期 condition 的 pop）**后** `combat["economy"] = deepcopy(event["economy"])` | `src/rulesets/dnd2024/combat/reducer.py` L277-285；`engine.py` `_expire_conditions` L1143-1154 |
| `_end_turn_events`：下一活动者跳过清单（敌人/队友 HP≤0、dead、stable）后 `next_view = self._actor_view(...)` 取**预结算视图**喂 `_fresh_economy` | `resolution.py` L595-638 |
| ray_of_frost 条目（`slowed_10` 的唯一施加来源）：`{"mode": "spell_attack", "damage": "1d8", "damage_type": "cold", "range": 60, "condition": "slowed_10", "condition_duration": "target_turn_start"}` | `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` L30 |
| push 事件先例（`forced: True`、无 `movement_cost` 键）：`{"type": "dnd2024.position.changed", "actor_id": target, "distance": direction * push, "forced": True}` | `resolution.py` L406-411 |
| reducer `position.changed`：`positions[actor_id] += distance`；`movement_cost` 只在有值时扣（不足抛 `EventBatchError`）——传送事件无该键即免费 | `reducer.py` L193-204 |
| `_movement_events`（借机攻击决策挂起 + `movement_cost` 扣减，**传送不得走此路径**）；`_decision_events` 的 opportunity_attack 决策恢复（movement payload） | `resolution.py` L421-448、L698-735 |
| move 意图校验先例（非零 int、`abs <= movement`）——`teleport_distance` 校验照此风格 | `validation.py` L293-298 |
| `_validate_spell`：成本 `casting_time.startswith("Bonus Action")` → bonus_action L416-418；法术位/`slot_spell_cast` L419-434；目标数上限 L435-442；**盟友集合** L449（P4 后为 `{healing, buff, stabilize, temp_hp}`）+ dead 拒绝 L452-453；敌方 elif L454-455；hp≤0 拒绝 L461-462；射程 L463-464；`damage_type_choice` 选择字段先例 L465-467 | `validation.py` L396-467 |
| `_spell_events` 主循环：`_spell_cost_events` → 专注（misty_step 无）→ `dnd2024.spell.cast` → 目标循环 mode 分发（spell_attack/saving_throw/automatic_damage/healing…）→ condition 应用尾部（teleport 无 condition 自然跳过）→ push 块（要求 `succeeded`，teleport 不置真自然跳过）→ 胜利判定 | `resolution.py` L233-419 |
| `SPELL_MODES` frozenset 现集合（P4 后含 `temp_hp`） | `src/rulesets/dnd2024/combat/catalog.py` L16-19 |
| catalog 逐效果校验：mode ∈ SPELL_MODES、range ∈ [5,600]（对 teleport 同样适用） | `catalog.py` L58-93 |
| P2 建立的 `condition_flags(registry, conditions)`（返回含 `speed_zero` 的四标志聚合，纯函数） | `src/rulesets/dnd2024/combat/check_modes.py`（P2 后；与 `attack_sources` 同级） |
| resolution 内 `from .check_modes import attack_mode` 是函数内导入先例（若 engine.py 顶层导入 `effective_speed` 引发循环依赖，照此处理） | `resolution.py` L272 |
| misty_step 法术元数据已存在（level 2 / conjuration / sorcerer+warlock+wizard / **Bonus Action** / Self / V / Instantaneous / 无专注），无需新增 | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json` L2429-2446 |
| 前端 `slowed_10` 状态标签已存在（无需改动） | `frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue` L461 |
| 测试基建：`SequenceRng` / `start_combat`（断言玩家先攻第一）；施法职业卡构造先例（paladin_sheet：monk_draft + `class_ref` + `class_spell_choices` 覆盖 + `equipment_package:wizard_a`，推到 3 级拿二环位） | `tests/rulesets/dnd2024_monk_common.py` L174-184；`tests/rulesets/test_dnd2024_smite.py` L15-56 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

conditions 注册表追加一条（P1/P5/P2 既有条目不动）：

```json
"slowed_10": {"speed_modifier": -10}
```

`spell_effects` 追加一条：

```json
"misty_step": {"mode": "teleport", "range": 30, "target_count": 1}
```

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

1. `SPELL_MODES` 追加 `"teleport"`；
2. conditions 条目允许键在前置切片并集之上追加 `speed_modifier`：必须是非 bool 的 int、`-600 <= v <= 600`、`v != 0`，任何违规抛 `CombatCatalogError`；
3. teleport 模式无新增逐字段校验（range 的既有 [5,600] 检查天然覆盖）。

### 3.3 `src/rulesets/dnd2024/combat/check_modes.py`

新增纯函数（与 `attack_sources` / `condition_flags` 同级，不含引擎依赖）：

```python
def effective_speed(registry: dict[str, dict[str, Any]], conditions: dict[str, Any], base_speed: int) -> int:
    """回合开始的速度上限：speed_zero 压倒一切修正；否则叠加全部 speed_modifier，向下取整于 0。"""
```

实现：先经 `condition_flags(registry, conditions)["speed_zero"]` 判归零（命中返回 0）；否则对 registry 中 `condition_id in conditions` 的条目求 `speed_modifier` 之和，返回 `max(0, int(base_speed) + total)`。现有函数不动。

### 3.4 `src/rulesets/dnd2024/combat/validation.py`（`_validate_spell`）

1. 盟友集合追加 `"teleport"`（即 `{healing, buff, stabilize, temp_hp, teleport}`）——传送以自身为目标必须通过敌我校验，不能落进"offensive spells require a hostile target"分支；
2. 目标循环内新增模式级规则：`effect["mode"] == "teleport"` 且 `target_ids != [actor["actor_id"]]` → `CombatIntentError`（措辞自定，语义＝传送法术只能以施法者为目标）；
3. `damage_type_choice` 先例旁追加 `teleport_distance` 双向校验（L465-467 之后）：

```python
if effect["mode"] == "teleport":
    distance = intent.get("teleport_distance")
    if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
        raise CombatIntentError("teleport distance must be a non-zero integer")
    if abs(distance) > int(effect["range"]):
        raise CombatIntentError("teleport distance exceeds the spell range")
elif intent.get("teleport_distance") is not None:
    raise CombatIntentError("teleport distance is not valid for this spell")
```

射程检查（L463-464）对自身目标距离为 0 自然通过，保持不动；hp≤0 检查同理（自身存活由 HP 门保证）。

### 3.5 `src/rulesets/dnd2024/combat/resolution.py`（`_spell_events` 目标循环）

mode 分发新增 teleport 分支（与 healing 等分支同级的 elif/等价结构）：

```python
if mode == "teleport":
    events.append({
        "type": "dnd2024.position.changed", "actor_id": target_id,
        "distance": int(intent["teleport_distance"]), "forced": True,
    })
```

不设 `succeeded`、不消耗 RNG、不经过 `_movement_events`、事件不带 `movement_cost`。condition 应用尾部与 push 块对 teleport 天然跳过（无 `condition` 键、`succeeded` 为假）。胜利判定不动。

### 3.6 `src/rulesets/dnd2024/combat/engine.py`（`_fresh_economy`）

1. 从 `@staticmethod` 改为实例方法 `def _fresh_economy(self, actor)`（两个调用点已是 `self._fresh_economy(view)`，零改动）；
2. 删除 `slowed_10` 硬编码，改：

```python
base = max(0, int(actor.get("speed", 0) or 0))
speed = effective_speed(self.catalog.conditions, actor.get("conditions") or {}, base)
```

`movement` 与 `speed` 都填 `speed`。`from .check_modes import effective_speed` 顶层导入（若循环依赖则照 resolution.py L272 函数内导入先例）。

### 3.7 测试

- 新文件 `tests/rulesets/test_dnd2024_misty_step.py`：施法者照 `test_dnd2024_smite.py` paladin_sheet 模式换 `class:wizard` + `class_spell_choices` 含 `spell:misty_step` + `equipment_package:wizard_a`，推进到 3 级拿二环位。测试矩阵见 §4。
- 回归：`tests/rulesets/test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_invisibility.py`、`test_dnd2024_hold_person.py` 及 P3/P4/P5 产物测试文件（以各任务书 §3 测试节为准）全部通过（`test_dnd2024_character_builder.py` 的上游豁免失败不计）。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`speed_modifier` 为 bool / 非 int / 0 / 超出 ±600；teleport 效果 range 越界 | `CombatCatalogError` |
| 2 | 法师（3 级）附赠动作施放 misty_step，`teleport_distance=25` | 事件序：法术位/成本事件 → `spell.cast`（slot_level=2、target_ids=[自身]）→ `position.changed`（forced=True、distance=25、**无 movement_cost 键**）；无 concentration 事件、无 check.resolved；apply 后 `positions[自身] == 25`；economy `bonus_action == 0`、`movement` 不变；`slots["2"]` 减 1；`available_intents` 投影含 misty_step（mode=teleport） |
| 3 | `teleport_distance` 缺失 / 0 / 非 int / bool / abs > 30 | 五种均 `CombatIntentError` |
| 4 | 目标校验：`target_ids=[另一盟友]`；`target_ids=[敌人]`；`target_ids=[自身]` | 拒 / 拒（敌我分支）/ 过 |
| 5 | 非 teleport 法术的 intent 携带 `teleport_distance` | `CombatIntentError`（fail closed） |
| 6 | 借机不触发：goblin position 5（相邻、有反应），misty_step +25 离开触及 | 无 `decision.pending`、无攻击事件、goblin reaction 仍 1、positions 更新；对照：同场景 move +25 → opportunity_attack 决策挂起（既有行为回归） |
| 7 | 迁移等价：ray_of_frost 命中 goblin（slowed_10, target_turn_start）→ 玩家 end_turn | `turn.advanced` 的 economy `movement == 20`、`speed == 20`（30-10）；batch apply 后 goblin 无 slowed_10（快照先于过期，§1.1 边界 5） |
| 8 | 向下取整：speed 10 的自定义敌人档案注入 `slowed_10` | 其回合 economy `movement == 0`（不为负） |
| 9 | speed_zero 投影：敌人档案注入 `paralyzed`（P2 注册表条目）→ 玩家 end_turn | economy `movement == 0` 且 `speed == 0`；该敌人随后提交 move 意图被 `CombatIntentError` 拒（P2 门控联动） |
| 10 | `effective_speed` 纯函数单测：speed_zero 压倒负修正；两个负修正求和；floor 0；空 conditions 返回 base | 返回值断言 |
| 11 | 移动力耗尽仍可传送：先 move 30（movement → 0），再 misty_step +25 | intent ok；positions 更新；不受剩余移动力限制 |
| 12 | 附赠动作经济：misty_step 后 `action == 1`、`bonus_action == 0`；同回合第二个附赠动作法术被拒；SequenceRng 在 start_combat 之后零消耗（队列恰好只含先攻骰） | economy 断言 + `CombatIntentError` + 队列零消耗 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_misty_step.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_hold_person.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

（第二行按执行时实际存在的 P3/P4/P5 测试文件名补充，以各任务书测试节为准。）

## 6. 禁止事项

1. 禁止把 teleport 接入 `_movement_events`、为其消耗移动力、或触发借机攻击决策（传送、push、拖拽均不触发，§1.1 边界 1）。
2. 禁止给 teleport 的 `position.changed` 事件加 `movement_cost` 键。
3. 禁止改变 reducer `turn.advanced` 的"先 `_expire_conditions` 后装 economy"顺序，或以任何方式让 ray_of_frost 场景行为变化（§1.1 边界 5）。
4. 禁止在 resolution / validation 新增具体法术名分支——misty_step 只允许目录数据 + teleport 模式。
5. 禁止对未声明 teleport 的模式接受 `teleport_distance`（双向 fail closed 之外不存在第三种处理）。
6. 禁止实现占用校验、"你能看见"门控（§1.2 已裁剪）。
7. 禁止顺手落数据或实现其他传送法术、速度提升法术（haste 整套机制明确不做）。
8. 禁止改动 `_apply_hp_change`、死亡豁免、`_movement_events` 与 opportunity_attack 决策恢复本体。
9. 禁止在 reducer 的 `condition.applied` 处理里加 mid-turn economy 改写（速度修改只在回合开始刷新生效，§1.1 边界 8）。
10. 禁止移除 economy 的 `speed` 键或改变 `speed`/`movement` 的现有语义。

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. `_fresh_economy` 出现本任务书未列出的第三个调用方（src/tests 全量 grep 后仍新增）；
2. economy `speed`/`movement` 的既有消费方（投影/前端/测试）无法容忍 registry 化后的值且无法用等价断言改写；
3. teleport 分支与 `spell.cast` 事件序、胜利判定或既有目标循环结构冲突；
4. `check_modes` 顶层导入引发循环依赖且函数内导入也不可行；
5. P2 的 `condition_flags` 未合入或签名与本任务书 §2 描述不符；
6. 任何白名单外文件必须修改才能通过验收。

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P6_MISTY_STEP_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况、§3 语义与实现的偏差（若有，尤其占用/可见性裁剪与投影滞后）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

## 9. 修订记录

- v1（2026-10-29）：初版。借机攻击解释依用户裁决（只有使用速度的移动才触发，参照贤者谏言判据）。

---

## 执行报告（2026-10-30）

### 1. 变更文件清单

| 文件 | 状态 | 内容 | 归属 |
|---|---|---|---|
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | M | `spell_effects` 新增 `misty_step`（teleport/30/1）；conditions 新增 `slowed_10: {"speed_modifier": -10}` | 仅 P6 |
| `src/rulesets/dnd2024/combat/catalog.py` | M | `SPELL_MODES` 追加 `"teleport"`；conditions 允许键与被动标志键集合追加 `speed_modifier`；新增 bool/int/0/±600 校验 | 仅 P6 |
| `src/rulesets/dnd2024/combat/check_modes.py` | M | 新增纯函数 `effective_speed(registry, conditions, base_speed)`（`condition_flags` 与 `attack_sources` 之间），既有函数未动 | 仅 P6 |
| `src/rulesets/dnd2024/combat/engine.py` | M | 顶层导入 `effective_speed`；`_fresh_economy` 由 `@staticmethod` 改实例方法并删除 `slowed_10` 硬编码；两个调用点（`resolution.py` combat.started / turn.advanced）已是 `self._fresh_economy(view)`，零改动 | 仅 P6 |
| `src/rulesets/dnd2024/combat/validation.py` | M | 有益集合追加 `teleport`；目标循环内 teleport 只允许施法者自身；`teleport_distance` 双向 fail closed（缺失/0/非 int/bool/超 range；非 teleport 携带即拒） | 仅 P6 |
| `src/rulesets/dnd2024/combat/resolution.py` | M | `_spell_events` 目标循环 `temp_hp` 与 `stabilize` 之间新增 `elif mode == "teleport"`，产出一条 `forced: True`、无 `movement_cost` 的 `dnd2024.position.changed` | 仅 P6 |
| `tests/rulesets/test_dnd2024_misty_step.py` | 新增 | 12 个测试函数（#3 六参数化 → 17 用例），覆盖 §4 矩阵 #1-#12 | 仅 P6 |
| `docs/internal/design/P6_MISTY_STEP_TASK_SPEC_2026-10-29.md` | M | 本节执行报告 | §8 豁免 |

共用文件（catalog / validation / resolution / engine）的逐段改动均只服务于 teleport 与 speed_modifier 两项 P6 原语；reducer / view / 前端未改（P6 不需要，且遵守 §6 禁止项）。

### 2. 测试矩阵逐项结果（全部通过）

| # | 用例 | 结果 | 备注 |
|---|---|---|---|
| 1 | `test_catalog_rejects_invalid_speed_modifier_and_teleport_range` | 通过 | bool / str / 0 / ±601 → `CombatCatalogError`；misty_step range=3 → 拒；既有合法目录仍可构造 |
| 2 | `test_misty_step_teleports_without_movement_cost` | 通过 | 过滤 `intent.submitted` 后事件序恰为 action.spent(bonus_action) → slot_spent(2) → spell.cast(2, 自身) → position.changed(distance=25, forced, 无 movement_cost)；无 concentration / check.resolved；positions=25；bonus_action=0、movement 不变；`slots_current["2"]=1`；`available_intents` 投影 mode=teleport |
| 3 | `test_teleport_distance_must_be_valid[None/0/"25"/True/31/-31]` | 通过 | 六种均 `CombatIntentError`，错误含 "teleport" |
| 4 | `test_teleport_targets_only_the_caster` | 通过 | 敌人 → 拒（先落"beneficial spells require an allied target"敌我分支）；盟友 p2 → 拒（模式级 "teleport spells can only target the caster"）；自身 → 过 |
| 5 | `test_non_teleport_spell_rejects_teleport_distance` | 通过 | magic_missile 携带 `teleport_distance` → "teleport distance is not valid for this spell" |
| 6 | `test_misty_step_does_not_trigger_opportunity_attacks` | 通过 | 无 `decision.pending`、无 `check.resolved`；goblin reaction 保持 1；positions 更新。对照同场景 move +25 仍挂起 opportunity_attack 决策（既有行为回归） |
| 7 | `test_turn_start_economy_snapshots_slowed_10_before_expiry` | 通过 | ray_of_frost 命中施加 slowed_10；end_turn 的 `turn.advanced.economy.movement/speed == 20`；apply 后 goblin 无 slowed_10（快照先于过期） |
| 8 | `test_slowed_10_floors_at_zero_for_slow_enemies` | 通过 | speed 10 + slowed_10 → movement/speed == 0，不为负 |
| 9 | `test_paralyzed_speed_zero_projects_into_economy_and_blocks_move` | 通过 | economy movement/speed == 0；该敌人随后 move 被 "speed zero" 拒（P2 门控联动） |
| 10 | `test_effective_speed_pure_function` | 通过 | 空 conditions=base；负修正求和；floor 0；speed_zero 压倒负修正；正修正叠加 |
| 11 | `test_misty_step_ignores_exhausted_movement` | 通过 | move 30 后 movement=0，仍可 teleport +25，positions 前进且 movement 保持 0 |
| 12 | `test_misty_step_spends_bonus_action_and_blocks_a_second_one` | 通过 | action 保持 1、bonus_action=0；同回合第二个附赠动作法术 → "bonus action" 拒；哨兵骰未被弹出（零 RNG） |

### 3. 验收命令与结果

1. `pytest tests/rulesets/test_dnd2024_misty_step.py -q` → **17 passed**（12 个测试函数，#3 参数化 6 条）。
2. §5 第二条（含 P3/P4/P5 产物）：`test_dnd2024_combat.py test_dnd2024_spells.py test_dnd2024_smite.py test_dnd2024_uncanny_dodge.py test_dnd2024_monk_combat.py test_dnd2024_invisibility.py test_dnd2024_hold_person.py test_dnd2024_scorching_ray.py test_dnd2024_false_life.py test_dnd2024_stoneskin.py test_dnd2024_misty_step.py` → **176 passed**（= 基线 159 + 新增 17）。
3. 全量 `pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py` → **578 passed, 39 deselected, 0 failed**（= 561 + 17），耗时 28:02。

### 4. §3 语义与实现的偏差

1. **占用 / 可见性裁剪**（任务书 §1.2 既定，非新增偏差）：1D theater 位置模型无占用语义，未校验传送终点占用；无视觉系统，未实现"你能看见"门控。两者均整体推迟。
2. **前端适配滞后**（§1.2 既定）：`teleport_distance` 目前只能由 intent 直交；战斗面板距离选择器属后续前端任务。P4 遗留的 temp_hp 展示偏差不在本切片，未改动。
3. **矩阵 #4 的括号措辞**：敌人目标被拒发生在既有的"beneficial spells require an allied target"敌我分支；盟友目标被拒发生在新增的 teleport 模式级检查；两者语义符合 §1.1 边界 3，仅拒绝路径不同。执行侧未为敌人生成独立分支。
4. **测试 #2 的事件索引**：批次首事件恒为引擎统一前置的 `intent.submitted`（既有契约，§6 未禁止），故断言先过滤该事件再比对 P6 专属事件序；未改引擎事件序。
5. **`effective_speed` 签名换行**：语义与 §3.3 一致，只是按仓库行宽写成多行参数表。
6. **`speed_zero` 投影落地**：P2 显式推迟的"回合开始 economy movement=0"在本切片通过 registry 化一并实现（矩阵 #9 覆盖），未新增 mid-turn economy 改写（遵守 §6 第 9 条）。

### 5. 遗留问题

1. `teleport_distance` 缺前端入口（同 §1.2，待前端任务）。
2. 传送终点占用与可见性待位置模型升级后统一实现。
3. 其余传送法术（雷鸣步、任意门等）与速度提升法术（haste 整套机制）未落数据，留待后续切片。
4. **白名单外残留（非本切片产生）**：收尾 `git status --short` 出现 5 个未跟踪文件 `docs/internal/design/SP1_IMPROVED_CRITICAL_TASK_SPEC_2026-10-30.md`、`SP4_DARK_ONES_BLESSING_TASK_SPEC_2026-10-30.md`、`SP5_DRACONIC_RESILIENCE_TASK_SPEC_2026-10-30.md`、`SP7_SURVIVOR_TASK_SPEC_2026-10-30.md`、`SP8_HIDE_AND_BONUS_ACTION_TASK_SPEC_2026-10-30.md`。执行侧未创建、未修改、未暂存它们，判定为其它任务/AI 工作区残留，请审核端在提交时排除。
