# 切片 P2 任务书：Hold Person（失能族行动门控 + 每回合豁免 tick）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 P1（invisibility）、P5（stoneskin）之后执行。P1 提供 conditions 注册表骨架（`attack_modes` 校验）、`upcast_target_count` 升环目标数、`ends_on` 提前终止；P5 追加注册表键 `resist_damage_types`。P1/P5 合入后本任务书引用的行号会偏移，描述以语义位置为准，不要按行号机械对照。catalog 校验的"允许键集合"以已执行切片的并集为准（P1 ∪ P5 ∪ 本切片新增四键）。

---

## 1. 目标

为 D&D 2024 规则集实现 **hold_person（定身类人，2 环）** 的完整闭环：目录数据 → 施法（感知豁免、升环多目标、类人限定）→ 麻痹的机械后果（失能族行动门控、速度归零、力敏豁免自动失败、近战自动重击、失能打断专注）→ 每回合结束的重复豁免 tick → 测试。

本切片同时沉淀四项通用原语：

1. **condition 语义标志（数据驱动）**：注册表条目可声明 `implies_incapacitated` / `speed_zero` / `auto_fail_saves` / `crit_on_hit_within_5`，引擎各消费点（校验、结算、反应）统一经 `condition_flags` 聚合读取——失能族（incapacitated / paralyzed / stunned / unconscious）从此具备真实机械后果，sleep 施加的 `incapacitated` 同步生效；
2. **每回合豁免 tick**：法术效果声明 `save_repeat: "<ability>"`，condition 元数据携带 `{ability, dc}` 快照，目标在**自己回合结束**时重复豁免，成功即移除该 condition；
3. **豁免自动失败**：`auto_fail_saves` 覆盖的属性豁免不掷骰直接失败；
4. **生物类型限定**：法术效果声明 `target_creature_types`，校验目标 view 的 `creature_type`。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 定身类人：二环惑控（吟游诗人、牧师、德鲁伊、术士、魔契师、法师）。施法时间动作；施法距离 60 尺；成分 V、S、M；持续时间专注，至多 1 分钟。指定施法距离内一个你能看见的**类人**生物。该目标必须进行一次**感知豁免**，豁免失败则其在法术持续时间内陷入**麻痹**状态。**目标在其每回合结束时可以重新进行这次豁免，豁免成功则终止其身上该法术的效应。** 升环：使用三环或更高法术位施展时，法术位每比二环高一环，就可以额外指定一个类人作为目标。
- 失能（Incapacitated）：①无法行动——你无法执行任何**动作、附赠动作以及反应**；②无法专注——你的专注将被打断；③无法说话；④措手不及——失能期间投掷先攻具有劣势。
- 麻痹（Paralyzed）：①陷入失能状态；②速度归零，且无法被增加；③自动失败于**力量与敏捷**豁免检定；④以你为目标的攻击检定具有优势；⑤若攻击者位于你 5 尺内，其任何**命中**你的攻击检定都会变为**重击**。
- 震慑（Stunned）：陷入失能状态；自动失败于力量与敏捷豁免；以你为目标的攻击检定具有优势。
- 昏迷（Unconscious）：陷入失能与倒地状态；速度归零；以你为目标的攻击检定具有优势；自动失败于力量与敏捷豁免；攻击者位于 5 尺内时任何命中变为重击；无法感知周遭。
- 睡眠术（Sleep，1 环）：5 尺半径球状区域内每个你选择的生物感知豁免失败则直到其下个回合结束陷入失能状态；**在其下个回合结束时，它必须重复此豁免**；第二次豁免失败则陷入昏迷状态；受到伤害或被摇醒则法术结束。

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/2环.htm` L224-225（定身类人）；
- `.rulebooks/dnd5r/玩家手册2024/法术详述/1环.htm` L184-185（睡眠术）；
- `.rulebooks/dnd5r/玩家手册2024/术语汇编/状态.htm` L75-80（失能）、L87-92（麻痹）、L127-131（震慑）、L133-140（昏迷）、L105-106（中毒，本切片不实现）。

### 1.1 必须锁定的规则边界

1. **失能只门控"动作"，不禁移动**：失能原文是"无法执行任何动作、附赠动作以及反应"——移动不是动作。纯粹失能（sleep）的单位**仍可移动**；只有 `speed_zero`（麻痹/昏迷）才门控 `move`。不得把 move 加进失能门控。
2. **死亡豁免不门控**：昏迷者的死亡豁免是其 0 HP 时唯一可做的事；`death_save` 永不门控，`end_turn` 同样永不门控（否则回合卡死）。
3. **重复豁免的归属**：tick 发生在**目标自己回合结束**（`end_turn` 意图推进回合时），不是施法者回合。豁免成功只终止**该目标身上**的效应（`condition.removed`）；多目标升环下法术对其他目标继续，**不得**发 `dnd2024.concentration.ended`，施法者专注保持。
4. **豁免 DC 是施放时快照**：condition 元数据存 `{ability, dc}`，dc 取施法当刻 `actor["spell_save_dc"]`；之后施法者属性变化不影响已挂 condition 的 tick。
5. **自动失败不掷骰**：`auto_fail_saves` 命中时**不消耗 d20**（SequenceRng 测试可验证骰队列未被吃），check 事件以标记表达失败（见 §3.4 C）。狂暴的力量豁免优势、bless 加骰对自动失败无意义——自动失败检查在掷骰之前。
6. **自动重击要求"命中"**：`crit_on_hit_within_5` 只把**已经命中**的攻击升级为重击（`natural == 1` 的必然失手、或未达 AC 的失手都不升级）；距离判定用攻击者到目标的 `self._distance(...) <= 5`。武器攻击与 spell_attack（法术攻击也是攻击掷骰）都适用。
7. **失能打断专注**：施加 `implies_incapacitated` condition 时，若目标（预结算视图）持有 `concentration_owner == 目标自身` 的 condition，发一条 `dnd2024.concentration.ended`（owner=目标），由既有 reducer 级联移除。新施加的 condition 自身的 `concentration_owner` 是施法者，不受级联影响。
8. **麻痹者不跳过回合**：`_end_turn_events` 的下一活动者跳过清单只有 dead / stable / HP≤0 敌人；失能族单位照常占回合——由 GM（敌人/托管席）或玩家提交 `end_turn` 触发 tick。**不得**把 paralyzed 加进跳过清单。
9. **类人限定按目标生物类型**：`target_creature_types` 只比较效果声明与目标 view 的 `creature_type`；它叠加在既有敌我校验之上，不替代。
10. **伤害不解除麻痹**（2024 麻痹无"受击解除"条款，与束缚/魅惑不同）；本切片也不实现通用"受伤打断专注"的体质豁免（既有 `_concentration_events` 行为不变）。

### 1.2 本切片的产品策略与裁剪

- 失能的"措手不及"（失能期间投先攻劣势）：先攻在战斗开始已掷，惊喜轮/先攻重掷是独立大件，推迟（与 P1 对隐形"出其不意"的裁剪一致）。
- 失能的"无法说话"与昏迷的"无知觉"：无言语/感知系统，不实现。
- 昏迷的"陷入倒地、掉落持握物"：倒地（prone）的移动限制与距离条件优劣势（5 尺内优势/外劣势）需要注册表 schema 支持距离条件，**prone 整体推迟**；`unconscious` 注册表条目不自动附加 prone。
- 中毒的"攻击检定与属性检定劣势"：属攻击模式家族（actor 侧 disadvantage）而非失能族，留给毒系切片，本切片不声明 `poisoned` 注册表条目。
- 石化（petrified）：失能+速度归零+力敏自败+伤害全抗+中毒免疫，但当前无任何施法来源会施加它，声明是死数据，留给石化来源切片。
- 睡眠术只补第一步：数据加 `save_repeat: "wis"` 使"下回合结束重复豁免"生效（比现行"专注期间永久失能"更接近 RAW）；**二次失败转昏迷、伤害/摇醒终止、不需要睡眠的生物免疫**三件事推迟，`condition_duration: "concentration"` 的近似保留，如实记录偏差。
- `available_intents` 等客户端投影**不**做失能过滤：服务端校验是权威边界，UI 可能仍显示将被拒绝的意图——记录为已知偏差，不在本切片扩投影。
- 失能单位（尤其 AI 托管/敌人）的自动 `end_turn` 代点属 server automation 策略，不在本切片；GM 手动提交即可触发 tick。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `attack_sources` / `mode_from_sources` / `attack_mode`：P1 改造后的注册表版；`attack_mode(engine, instance, actor, target, range_disadvantage, use_grant)` | `src/rulesets/dnd2024/combat/check_modes.py` L18-44（P1 后以语义为准） |
| `_attack_events`：`distance = self._distance(combat, actor_id, target_id)` 玩家 L113 / 敌人 L156 两分支都有；`critical = natural == 20` L170；`hit = natural != 1 and (critical or total >= target_ac)` L171；check_event L175-182；命中分支 `attack_damage_node(..., critical=critical, ...)` L197-202（critical 直通骰翻倍）；`_consume_attack_conditions` 调用 L231 | `src/rulesets/dnd2024/combat/resolution.py` L98-232 |
| `_spell_events`：cost/专注 L246-265、目标循环 L267、spell_attack 分支 L271-307（`"critical": roll == 20` L288、翻倍 L295-296、**分支内无 distance 计算**）、saving_throw 分支 L308-349（DC=`actor["spell_save_dc"]` L318、狂暴力量优势 L311-314、bless L315、`target["saving_throws"]` L316） | `resolution.py` L233-419 |
| condition 应用尾部：`condition_applies = (saving_throw 且 not succeeded) or (spell_attack/automatic_damage 且 succeeded) or (buff/debuff)`；payload 键 `duration` / `source_actor_id` / `concentration_owner`（P1 已追加 `ends_on`） | `resolution.py` L393-405 |
| `_end_turn_events(self, instance, combat)`：**无 rng 参数**；胜利早退 L597-601、全灭早退 L602-615；下一活动者跳过清单 = 敌人/队友 HP≤0、dead、stable（**不含失能族**）L623-629；返回单个 `dnd2024.turn.advanced`（economy 给下一活动者）L633-638 | `resolution.py` L595-638 |
| `_end_turn_events` 全部调用点：意图分发 `engine.py` L650（`end_turn` 分支，分发函数签名里有 rng）；死亡豁免不致死不苏醒时 `resolution.py` L670（`_death_save_events` 有 rng） | `engine.py` L638-658；`resolution.py` L640-670 |
| `_distance(combat, first, second)` / `_require_range(...)` | `engine.py` L1092 / L1100 |
| `_validate` 意图总校验：actor view L273；HP 门 L274-280（0 HP 只许 death_save，stable 例外许 end_turn）；动作经济门 L281-292（attack/dash/dodge/disengage/stabilize）；move 距离门 L293-298；类型分发 L299-309 | `src/rulesets/dnd2024/combat/validation.py` L115-309 |
| `_validate_spell`：目标列表化/去重/`target_count` 上限 L435-442（P1 后含 `upcast_target_count`）；敌我校验 L449-455（saving_throw 属 offensive，须敌对目标）；射程 L463-464；`damage_type_choice` 先例 L465-467 | `validation.py` L396-467 |
| 敌人档案 `creature_type` 校验（canonical 集合）；enemy view 暴露 `creature_type`；`CREATURE_TYPES` frozenset | `validation.py` L45-46；`view.py` L84；`src/rulesets/dnd2024/content/contracts.py` L30 |
| 反应决策两入口的排除清单：`"reaction_blocked" in conditions or "shield" in conditions` | `src/rulesets/dnd2024/combat/reaction_spells.py` L45（`_reaction_spell_choices`）、L83（`_reaction_feature_choices`，uncanny dodge） |
| `condition.applied` 的全部事件键（除 type/target_id/condition）进元数据；`condition.removed` 只 pop（多余键无害）；`concentration.ended` 清专注位并级联移除 `concentration_owner == owner` 的全部 condition | `src/rulesets/dnd2024/combat/reducer.py` L208-213、L221-224、L268-276 |
| 玩家 HP 归零时 reducer 直接写入 `conditions["unconscious"] = {"source": "zero_hp"}`（不经 condition.applied 事件）——本切片的 `unconscious` 注册表条目因此**立即活效**（对倒地玩家的攻击优势、近战自动重击、speed_zero 门控），不是死数据 | `reducer.py` `_apply_hp_change` L348 |
| `INTENT_TYPES` 白名单：attack / cast_spell / move / dash / dodge / disengage / end_turn / death_save / stabilize / decision.resolve / class_capability | `src/rulesets/dnd2024/combat/primitives.py` L13-20 |
| 目录加载校验（P1 conditions 节 + P5 `resist_damage_types` 扩展后的 fail closed 模式） | `src/rulesets/dnd2024/combat/catalog.py` L26-93 |
| 现行 sleep 条目：`{"mode": "saving_throw", "save": "wis", "range": 60, "condition": "incapacitated", "condition_duration": "concentration", "concentration": true}`（**无 save_repeat**） | `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` L53 |
| 敌人档案可携带 conditions 直透（`_actor_view` 直读 `enemy.get("conditions")`），是感官/失能族注入测试点 | `view.py` L68-86；`tests/rulesets/dnd2024_monk_common.py` `goblin()` L145-155（**当前无 creature_type**） |
| hold_person 法术元数据已存在，无需新增 | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json` L2196 附近 |
| 测试基建：`SequenceRng` / `start_combat`（断言玩家先攻第一）；施法职业卡构造先例（paladin_sheet：monk_draft + `class_ref` + `class_spell_choices` 覆盖 + `equipment_package:wizard_a`）；既有 sleep 回归测试 | `tests/rulesets/dnd2024_monk_common.py` L174-184；`tests/rulesets/test_dnd2024_smite.py` L15-56；`tests/rulesets/test_dnd2024_combat.py` L359-379 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

conditions 注册表新增四条（P1/P5 条目不动）：

```json
"incapacitated": {"implies_incapacitated": true},
"paralyzed": {"attack_modes": [{"side": "target", "mode": "advantage"}], "implies_incapacitated": true, "speed_zero": true, "auto_fail_saves": ["str", "dex"], "crit_on_hit_within_5": true},
"stunned": {"attack_modes": [{"side": "target", "mode": "advantage"}], "implies_incapacitated": true, "auto_fail_saves": ["str", "dex"]},
"unconscious": {"attack_modes": [{"side": "target", "mode": "advantage"}], "implies_incapacitated": true, "speed_zero": true, "auto_fail_saves": ["str", "dex"], "crit_on_hit_within_5": true}
```

`spell_effects` 新增一条、修订一条：

```json
"hold_person": {"mode": "saving_throw", "save": "wis", "range": 60, "target_count": 1, "upcast_target_count": 1, "condition": "paralyzed", "condition_duration": "concentration", "concentration": true, "save_repeat": "wis", "target_creature_types": ["humanoid"]}
```

sleep 条目追加 `"save_repeat": "wis"`（其余键不动）。

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

1. conditions 条目允许键在 P1（`attack_modes`）∪ P5（`resist_damage_types`）之上追加四个，任何违规抛 `CombatCatalogError`：
   - `implies_incapacitated` / `speed_zero` / `crit_on_hit_within_5`：必须是 bool；
   - `auto_fail_saves`：必须是列表且元素 ⊆ `{"str", "dex", "con", "int", "wis", "cha"}`；
2. `spell_effects` 校验追加两条：
   - `save_repeat` 若存在必须 ∈ 上述六属性；
   - `target_creature_types` 若存在必须是非空字符串列表。

### 3.3 `src/rulesets/dnd2024/combat/check_modes.py`

新增纯函数（与 `attack_sources` 同级，不含引擎依赖）：

```python
def condition_flags(registry: dict[str, dict[str, Any]], conditions: dict[str, Any]) -> dict[str, Any]:
    """聚合 condition 声明的机械标志：失能族门控、速度归零、豁免自动失败、近战自动重击。"""
```

返回 `{"incapacitated": bool, "speed_zero": bool, "auto_fail_saves": set[str], "crit_on_hit_within_5": bool}`——遍历 registry 中 `condition_id in conditions` 的条目，四个标志分别取"任一为真 / 并集"。现有三个函数不动。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py` 与 `engine.py`

#### A. 意图门控（validation.py `_validate`，HP 门之后、经济门之前插入）

```python
flags = condition_flags(self.catalog.conditions, actor["conditions"])
if flags["incapacitated"] and intent_type in {"attack", "cast_spell", "class_capability", "dash", "dodge", "disengage", "stabilize"}:
    raise CombatIntentError("an incapacitated actor cannot take actions")
if flags["speed_zero"] and intent_type == "move":
    raise CombatIntentError("an actor with speed zero cannot move")
```

`death_save` / `end_turn` / `decision.resolve` / `combat.message` 不门控（§1.1 边界 2；hit 决策挂起期间敌方无法施法，条件应用与挂起决策不可共存，decision.resolve 无需门控）。

#### B. 生物类型限定（validation.py `_validate_spell` 目标循环内，射程检查旁）

```python
allowed_types = effect.get("target_creature_types")
if allowed_types and str(target.get("creature_type") or "") not in allowed_types:
    raise CombatIntentError("the spell target's creature type is not eligible")
```

#### C. 豁免自动失败（resolution.py saving_throw 分支，掷骰之前）

`save_ability` 取定后先查 `condition_flags(self.catalog.conditions, target["conditions"])["auto_fail_saves"]`；命中则**不消耗任何骰**，直接构造：

```python
{"type": "check.resolved", "kind": "saving_throw", "actor_id": target_id,
 "source_actor_id": actor_id, "ability": save_ability,
 "rolls": [], "natural": 0, "modifier": modifier, "bless_bonus": 0,
 "total": 0, "target": actor["spell_save_dc"], "success": False,
 "auto_failed": True}
```

（`modifier` 仍填真实豁免修正，信息性。）伤害逻辑照旧走 `not succeeded` 分支（全额，`half_on_success` 不触发）。未命中自动失败清单时行为与现状逐字节一致。

#### D. 近战自动重击（`_attack_events` 与 `_spell_events` 的 spell_attack 分支）

两处相同模式——命中判定后、check_event 构造前：

```python
# _attack_events（distance 两分支已计算）
if hit and not critical and condition_flags(self.catalog.conditions, target["conditions"])["crit_on_hit_within_5"] and distance <= 5:
    critical = True
```

spell_attack 分支当前不计算距离，需补 `distance = self._distance(combat, actor_id, target_id)`，随后对 `succeeded` 应用同款升级（`roll == 20` 之外的 critical 来源）。升级后的 `critical` 进 check_event 与伤害翻倍路径（`attack_damage_node(critical=...)` / `double_dice_counts`），不新增事件键。

#### E. condition payload 追加 `save_repeat` + 失能打断专注（condition 应用尾部）

1. 效果声明 `save_repeat` 时，`condition.applied` payload 追加：

```python
"save_repeat": {"ability": str(effect["save_repeat"]), "dc": int(actor["spell_save_dc"])}
```

2. `condition.applied` 追加后，若 `self.catalog.conditions` 中该 condition 声明 `implies_incapacitated`，且**预结算视图 target** 的 conditions 里存在 `meta.get("concentration_owner") == target_id` 的条目，则追加一条：

```python
{"type": "dnd2024.concentration.ended", "actor_id": target_id, "reason": "incapacitated"}
```

（同一目标去重只发一次；级联清理由既有 reducer 完成。）

#### F. 每回合豁免 tick（`_end_turn_events`）

1. 签名改为 `_end_turn_events(self, instance, combat, rng)`；两个调用点（`engine.py` 意图分发 end_turn 分支、`resolution.py` 死亡豁免路径）传入在位的 rng。
2. 新增辅助 `_save_repeat_events(self, instance, combat, actor_id, rng)`：
   - 取 `_actor_view(instance, combat, actor_id)`（预结算视图），遍历其 conditions；元数据是 dict 且含 `save_repeat` dict（`ability` 非空、`dc > 0`）的才处理；
   - 掷骰逻辑与 saving_throw 分支同构：先查 taker 自身的 `auto_fail_saves`（命中→不掷骰，事件带 `auto_failed: True`、`success: False`）；否则单 d20 + `saving_throws[ability]`（狂暴且 ability=="str" 时优势双骰取高；blessed 时 d4），`success = total >= dc`；
   - 每次都发 check 事件（键同 C 的结构，`actor_id`=taker、`source_actor_id`=元数据 `source_actor_id`、另加 `"repeat_save": True` 审计标记）；
   - 成功时追加 `{"type": "condition.removed", "target_id": actor_id, "condition": condition_id, "reason": "save_repeat"}`——**不发** concentration.ended（§1.1 边界 3）。
3. `_end_turn_events` 在两个早退路径（胜利/全灭）**不** tick（战斗已结束，重复豁免不再改变结果）；正常路径在计算下一活动者之前，对**正结束回合的 actor**（`order[int(combat["turn_index"])]`）调用辅助，tick 事件排在 `dnd2024.turn.advanced` 之前。conditions 遍历顺序 = dict 插入顺序（确定性）。

### 3.5 `src/rulesets/dnd2024/combat/reaction_spells.py`

`_reaction_spell_choices`（L45）与 `_reaction_feature_choices`（L83）的排除清单各追加一项：`condition_flags(self.catalog.conditions, defender["conditions"])["incapacitated"]` 为真 → `return []`。其余逻辑（反应预算、shield/reaction_blocked）不动。

### 3.6 测试

- `tests/rulesets/dnd2024_monk_common.py`：`goblin()` 增加 `"creature_type": "humanoid"`（纯新增键，既有档案校验 L45-46 允许 canonical 值，全部既有测试不受影响）；
- 新文件 `tests/rulesets/test_dnd2024_hold_person.py`：职业卡照抄 `test_dnd2024_smite.py` 的 paladin_sheet 模式换 `class:cleric` 或 `class:wizard`（hold_person 在其职业表内，`class_spell_choices` 覆盖选中，`equipment_package:wizard_a`/对应装备包，等级推进到 3 级拿二环与三环位）；非人类敌人在测试内联构造（如 `"creature_type": "beast"` 的野兽档案）。测试矩阵见 §4。

回归：`tests/rulesets/test_dnd2024_combat.py`（含 sleep 回归 L359-379）、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_invisibility.py`（P1 产物）、P5 的 stoneskin 测试文件全部通过（`test_dnd2024_character_builder.py` 的上游豁免失败不计）。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`implies_incapacitated` 非 bool、`auto_fail_saves` 含非法属性、`save_repeat` 非六属性、`target_creature_types` 非列表/空列表 | `CombatCatalogError` |
| 2 | 法师/cleric 对 goblin 施 hold_person（SequenceRng 低骰） | check.resolved（saving_throw, wis, success=False）→ condition.applied（paralyzed）；元数据含 `concentration_owner`=施法者、`save_repeat={"ability": "wis", "dc": <期望 DC>}`、`duration: "concentration"` |
| 3 | 同场景高骰豁免成功 | 无 condition.applied，目标无 paralyzed |
| 4 | 门控：paralyzed goblin 回合内 GM 提交 attack / cast_spell / move | 三者均 `CombatIntentError`（失能禁动作 / 速度归零禁移动） |
| 5 | 失能不禁移动：sleep 豁免失败的 goblin 回合内 GM 提交 move | intent ok（RAW：失能不禁移动，速度未归零） |
| 6 | tick 成功：paralyzed goblin 回合 GM 提交 end_turn（高骰） | 事件序 check.resolved（repeat_save=True, success=True）→ condition.removed（paralyzed, reason=save_repeat）→ turn.advanced；apply 后 state 中 goblin 无 paralyzed |
| 7 | tick 失败：低骰 end_turn | check.resolved success=False；paralyzed 保留；施法者专注未被终止（无 concentration.ended） |
| 8 | 升环：3 环位 hold_person 指定 2 个 goblin（enemies 列表两条目），各自独立豁免；2 环位指定 2 目标 | 两份独立 check.resolved + 各自 condition；低环多目标被 `CombatIntentError` 拒（P1 upcast 机制回归） |
| 9 | 类人限定：对 `creature_type: "beast"` 敌人施 hold_person | `CombatIntentError`；对 humanoid goblin 正常（#2 已覆盖） |
| 10 | 自动失败：goblin 档案注入 `{"conditions": {"paralyzed": {...}}}`，玩家对其施放任一 `save ∈ {"str","dex"}` 的 saving_throw 法术（执行侧 grep 目录选定，如 fireball/burning hands） | check.resolved `rolls == []`、`success=False`、`auto_failed=True`；SequenceRng 队列中**无 d20 被请求**（只有伤害骰请求） |
| 11 | 近战自动重击（武器）：玩家 adjacent（position 5）以非 nat20 命中 paralyzed goblin | check.resolved `critical=True`、伤害事件骰翻倍（`critical=True`）；同一目标在 position 30 用远程武器命中 → `critical=False` |
| 12 | 近战自动重击（法术攻击）：adjacent spell_attack（如 fire bolt）命中 paralyzed goblin | `critical=True`（roll != 20 时也成立）；远距施放 → `critical=False` |
| 13 | 失能打断专注：goblin 档案注入 `{"conditions": {"blessed": {"concentration_owner": "enemy:goblin-1", ...}}}`，sleep 豁免失败 | 批内出现 `dnd2024.concentration.ended`（actor_id=goblin，reason=incapacitated）；apply 后 blessed 移除、incapacitated 存在 |
| 14 | sleep 补全：sleep 失败的 goblin 回合 end_turn 高骰 | `condition.removed`（incapacitated）；低骰保留 |
| 15 | 反应排除：参考 `test_dnd2024_uncanny_dodge.py` 的注入方式，给防御者注入失能族 condition 后被攻击 | 无 reaction 决策挂起（offer 为空，不出现 decision.pending） |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_hold_person.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

（第二行按执行时实际存在的 P1/P5 测试文件名补充；P5 文件名以 P5 任务书 §3.6 为准。）

## 6. 禁止事项

1. 禁止把 `move` 纳入失能门控（失能只禁动作/附赠动作/反应；速度归零才禁移动）。
2. 禁止给 save_repeat tick 发 `concentration.ended`，或以任何方式终止施法者专注（多目标法术对其他目标继续）。
3. 禁止把 paralyzed / incapacitated / stunned / unconscious 加进 `_end_turn_events` 的回合跳过清单。
4. 禁止在自动失败路径消耗 d20 或请求 bless 骰。
5. 禁止给未命中（nat 1 或未达 AC）的攻击升级 critical。
6. 禁止把 `target_creature_types` 实现为敌我校验的替代；禁止对未声明该字段的效果做任何生物类型检查。
7. 禁止实现 prone、中毒劣势、石化、sleep 二次失败转昏迷/伤害唤醒/免疫清单（§1.2 已裁剪）。
8. 禁止在 resolution / validation / reaction_spells 新增具体法术名分支——hold_person 与 sleep 只允许目录数据 + 既有模式。
9. 禁止顺手改 `_fresh_economy` 的速度计算（速度归零的 economy 投影属 P6 速度修改数据驱动化范围；本切片只在 validation 门控）。
10. 禁止把 `save_repeat` / `target_creature_types` 做成客户端可写的 intent 字段；它们只能来自目录效果。

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. `check.resolved`（saving_throw）事件的既有消费方（投影/前端契约测试）无法容忍 `rolls: []`、`auto_failed` 或 `repeat_save` 键；
2. 目录中不存在任何 `save ∈ {"str","dex"}` 的 saving_throw 法术效果，测试 #10 无法搭建（报告候选清单，不要临时发明法术数据）；
3. 除 `engine.py` L650 与 `resolution.py` L670 外发现 `_end_turn_events` 的第三个调用方；
4. tick 所需的 taker view 缺 `saving_throws` 投影（玩家或敌人任一侧）；
5. 既有 sleep 测试（test_dnd2024_combat.py L359-379）因 `save_repeat` 数据变更失败且无法用 RAW 语义解释；
6. 任何白名单外文件必须修改才能通过验收。

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P2_HOLD_PERSON_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况、§3 语义与实现的偏差（若有，尤其 sleep 近似与 §1.2 各裁剪项）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

## 9. 修订记录

- v1（2026-10-29）：初版。
- v1.1（2026-10-29）：§2 补注 `_apply_hp_change` 零 HP 直接应用 unconscious 的既有路径——注册表条目对倒地玩家立即生效。

---

## 执行报告（2026-10-29，v1.1）

### A. 变更文件清单

| 文件 | 变更 |
|---|---|
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | conditions 新增 `incapacitated` / `paralyzed` / `stunned` / `unconscious` 四条；`spell_effects` 新增 `hold_person`；`sleep` 追加 `save_repeat: "wis"` |
| `src/rulesets/dnd2024/combat/catalog.py` | 新增模块常量 `SAVE_ABILITIES`；conditions 允许键扩为 P1∪P5∪四个新键，bool 标志与 `auto_fail_saves` 元素校验；`spell_effects` 校验 `save_repeat` 与 `target_creature_types` |
| `src/rulesets/dnd2024/combat/check_modes.py` | 新增纯函数 `condition_flags`（返回 `incapacitated` / `speed_zero` / `auto_fail_saves` / `crit_on_hit_within_5`）；既有三个函数未动 |
| `src/rulesets/dnd2024/combat/validation.py` | `_validate` 在 HP 门后、经济门前插入失能/速度归零门控；`_validate_spell` 目标循环内加入 `target_creature_types` 校验 |
| `src/rulesets/dnd2024/combat/resolution.py` | `_attack_events` 命中后近战自动重击；spell_attack 分支补距离计算与同款重击升级；saving_throw 分支在掷骰前处理 `auto_fail_saves`；condition 应用尾部写入 `save_repeat` 快照并在 `implies_incapacitated` 时追加 `concentration.ended`；新增 `_save_repeat_events`；`_end_turn_events` 签名加 `rng` 并在结算下一活动者前 tick |
| `src/rulesets/dnd2024/combat/engine.py` | `end_turn` 分发调用传入 `rng` |
| `src/rulesets/dnd2024/combat/reaction_spells.py` | 两个反应入口在既有排除清单后追加失能排除 |
| `tests/rulesets/dnd2024_monk_common.py` | `goblin()` 新增 `"creature_type": "humanoid"` |
| `tests/rulesets/test_dnd2024_hold_person.py` | 新增，17 项测试覆盖 §4 全部矩阵项 |

`git status` 仅上述 8 个改动文件 + 新测试文件 + 本任务书报告追加；未提交 git。

### B. 测试矩阵逐项结果

| # | 测试 | 结果 |
|---|---|---|
| 1 | `test_catalog_rejects_invalid_incapacitation_declarations` | 通过：非 bool 标志、非法 `auto_fail_saves` 元素、非法 `save_repeat`、字符串/空列表 `target_creature_types` 均抛 `CombatCatalogError` |
| 2 | `test_hold_person_paralyzes_a_humanoid_with_a_repeat_save_snapshot` | 通过：wis 豁免失败 → `condition.applied(paralyzed)`；元数据含 `duration=concentration`、`concentration_owner=player:gm`、`save_repeat={"ability":"wis","dc":<施法 DC>}` |
| 3 | `test_hold_person_does_nothing_on_a_successful_save` | 通过：高骰成功，无 `condition.applied`，目标无 paralyzed |
| 4 | `test_paralyzed_actor_cannot_attack_cast_or_move` | 通过：attack / cast_spell 报 "an incapacitated actor cannot take actions"，move 报 "an actor with speed zero cannot move" |
| 5 | `test_sleep_incapacitation_does_not_block_movement` | 通过：纯失能单位 5 尺 move 合法，超速 move 仍被既有距离门拒绝 |
| 6 | `test_repeat_save_success_removes_paralyzed_without_ending_concentration` | 通过：事件序 `check.resolved(repeat_save=True, success=True)` → `condition.removed(reason=save_repeat)` → `turn.advanced`；无 `concentration.ended`；施法者专注保留 |
| 7 | `test_repeat_save_failure_keeps_paralyzed_and_the_caster_concentration` | 通过：低骰失败，paralyzed 保留，无 `condition.removed` / `concentration.ended`，专注保留 |
| 8 | `test_upcast_hold_person_saves_each_target_independently` | 通过：3 环位两目标各自独立 check（fail/success），各自 condition；2 环位指定 2 目标被既有 upcast 目标数校验拒绝 |
| 9 | `test_hold_person_rejects_a_non_humanoid_target` | 通过：`creature_type: "beast"` 目标报 "the spell target's creature type is not eligible"；humanoid 正常由 #2 覆盖 |
| 10 | `test_paralyzed_target_auto_fails_dex_saves_without_rolling` | 通过：`burning_hands`（dex，3d6）对 paralyzed goblin：`rolls=[]`、`success=False`、`auto_failed=True`；RecordingRng 记录 `(1,20)` 请求数为 0；伤害全额 9 |
| 11 | `test_melee_hit_on_a_paralyzed_target_becomes_critical` / `test_ranged_hit_on_a_paralyzed_target_is_not_critical` | 通过：相邻非 nat20 命中 → `critical=True` 且 1d4 骰翻倍；30 尺远程命中 → `critical=False` 且不翻倍 |
| 12 | `test_spell_attack_on_a_paralyzed_target_within_5_feet_is_critical` / `test_distant_spell_attack_on_a_paralyzed_target_is_not_critical` | 通过：相邻 fire bolt 非 nat20 命中 → `critical=True`；30 尺施放 → `critical=False` |
| 13 | `test_incapacitation_breaks_the_targets_concentration` | 通过：sleep 失败 → 批内 `dnd2024.concentration.ended(actor_id=goblin, reason=incapacitated)`；apply 后 blessed 级联移除、incapacitated 存在 |
| 14 | `test_sleep_repeat_save_removes_incapacitated_and_keeps_it_on_a_failure` | 通过：高骰移除 incapacitated，低骰保留 |
| 15 | `test_incapacitated_defender_gets_no_reaction_decision` | 通过：注入 paralyzed 的持盾队友被攻击时无 `decision.pending`，伤害直接结算 |

### C. 与 §3 语义 / 计划的偏差与说明

1. **测试职业等级**：任务书 §3.6 建议 3 级；实际用 **5 级法师**，因为 #8 升环需要 3 环法术位（3 级法师只有 2 环位）。`hold_person` 排在 spellbook 第 9 位，使其同时落在 5 级 spellbook(14) 与 prepared(9) 前缀内；1–4 级的前缀仍全部落在各自最大法术环内。
2. **#11 / #12 拆为两个用例**：相邻与远距分别构造独立战斗，避免同一实例内位置改动污染经济；测试函数总数 17。
3. **#10 法术选择**：目录中 `save ∈ {str,dex}` 的 saving_throw 效果存在（`burning_hands` / `sacred_flame` / `acid_splash`），无停止条件触发；选用 `burning_hands` 以同时验证全额伤害。断言"无 d20"用 RecordingRng 记录请求区间，而非仅凭队列余值。
4. **fire bolt 骰数**：#12 中 5 级法师的 fire bolt 已是 2d10（既有 cantrip 升级路径），因此重击时 4 颗骰、非重击 2 颗；这是既有行为，未新增任何规则。
5. **sleep 近似**（§1.2 既定裁剪）：保留 `condition_duration: "concentration"`，仅新增 `save_repeat: "wis"` 实现"下回合结束重复豁免"；二次失败转 unconscious、受伤/摇醒终止、无需睡眠生物免疫仍未实现。
6. **失能打断专注**：使用预结算视图判断 `concentration_owner == target_id`，应用后追加一条 `concentration.ended`，级联由既有 reducer 完成；未改动既有 `_concentration_events`（受伤打断专注的体质豁免行为不变）。
7. **自动失败位置**：在 saving_throw 分支取定豁免属性后、任何 d20 / bless 请求之前；`modifier` 仍填真实修正，`total` 为 0。未命中清单时行为与现状一致。
8. **自动重击**：只在已命中（`natural != 1` 且达 AC）时升级；spell_attack 分支补 `distance` 计算，重击结果直接作用于既有 check 事件与 `double_dice_counts` / `attack_damage_node(critical=...)`，未新增事件键。
9. **`condition_flags` 聚合**：四个标志按"任一为真 / 并集"聚合；`auto_fail_saves` 返回 `set[str]`，与任务书签名一致。
10. **`unconscious` 条目立即活效**：零 HP 玩家由 reducer 直写 `conditions["unconscious"] = {"source": "zero_hp"}`，不经 `condition.applied`，因此注册表条目对倒地玩家立即产生攻击优势 / 速度归零 / 力敏自败 / 近战自动重击后果（与 §2 备忘一致）。
11. **测试基建**：新测试文件沿用 `dnd2024_monk_common` 的 `SequenceRng`、`monk_draft`、`monk_instance`、`with_equipment`、`goblin`，未改动既有 helper 语义（仅 `goblin()` 加 `creature_type`）。
12. **已知偏差（§1.2 保留）**：`available_intents` 等客户端投影仍不做失能过滤；失能单位的自动 `end_turn` 代点未实现，需 GM 手动提交触发 tick。

### D. 验收命令结果

| 命令 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_hold_person.py -q` | **17 passed** |
| `pytest test_dnd2024_combat / spells / smite / uncanny_dodge / monk_combat / invisibility / stoneskin -q` | **119 passed** |
| `pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py` | **538 passed, 39 deselected, 35 warnings**（2229.17s） |

既有 sleep 回归（`test_dnd2024_combat.py`）在追加 `save_repeat` 后仍通过：该回归未推进失败目标的回合结束，tick 不触发，无需语义解释。

### E. 遗留

1. sleep 的第二阶段（二次失败转 unconscious）、伤害/摇醒终止、免疫清单（§1.2 裁剪）。
2. prone 与"倒地"相关距离条件优劣势、石化、中毒劣势家族（§1.2 裁剪）。
3. 先攻劣势（失能"措手不及"）与惊喜轮（§1.2 裁剪）。
4. `available_intents` 投影未按失能过滤；UI 可能显示将被服务端拒绝的意图（已知偏差）。
5. 失能单位（AI 托管/敌人）的自动回合结束代点未实现（§1.2 裁剪，需 GM 手动提交）。
6. 速度归零对 economy 的投影仍走既有 `_fresh_economy`（§6 禁止项 9），未数据驱动化。
