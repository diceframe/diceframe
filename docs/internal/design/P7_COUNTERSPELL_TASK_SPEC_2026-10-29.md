# 切片 P7 任务书：Counterspell（施法反应窗口：打断施法）

版本：v1.1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 P1、P5、P2、P3、P4、P6、P8 之后执行。P2 提供 `condition_flags`（auto_fail_saves）与豁免事件形态；P6/P8 已扩展 `SPELL_MODES` 与 `_validate_spell`。前置切片合入后本任务书引用的行号会偏移，描述以语义位置为准。

---

## 1. 目标

为 D&D 2024 规则集实现 **counterspell（法术反制）的引擎面**：玩家/队友施展带成分法术时的**反制决策窗口**——敌方消耗反应迫使其体质豁免，失败则法术消散（动作浪费、法术位不消耗），成功则法术完整结算。

引擎当前敌人不能施法（`_validate_spell` 拒绝 enemy 施法者），因此玩家侧"反制敌人施法"无落地对象；本切片交付的是**通用原语**：

1. **施法反应窗口（反应管线换触发源）**：`cast_spell` 意图在结算前可被挂起为 GM 决策（照借机攻击的挂起/恢复架构）——施法的成本、法术位、`spell.cast` 全部延迟到决策结算期，反制失败路径只消耗动作不发法术事件；
2. **敌方反制能力（档案字段）**：敌人档案声明 `counterspell_dc`（0-30，缺省 0 = 无能力）；存在能力、反应与 60 尺距离时，玩家施放带 V/S/M 任一成分的法术即开窗。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 法术反制：三环防护（术士、魔契师、法师）。施法时间**反应，当你看见 60 尺内一名生物施展一道具有言语、姿势或材料成分的法术时可用**；施法距离 60 尺；成分 S；持续时间立即。你试图打断一名正在施法的生物。该生物进行一次**体质豁免**。豁免失败则法术消散且毫无效果，并且用于施展法术的**动作、附赠动作或反应随之浪费**。如果那道法术是使用法术位施展的，则**法术位并不会被消耗**。（2024 版无升环条款。）

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/3环.htm` L95-96（法术反制）。

### 1.1 必须锁定的规则边界

1. **反制失败的完整语义**：法术"消散且毫无效果"——不发 `dnd2024.spell.cast`、不发任何法术效果/专注事件；施法动作浪费（按被反制法术的 `casting_time` 发 `dnd2024.action.spent`，resource=action 或 bonus_action）；**法术位不消耗**（不发 `spell.slot_spent`；免费施放资源同样不消耗）。反制成功（豁免通过）则法术**完整**结算（成本、法术位、效果照常）。
2. **触发条件**：施法者是 player/companion；法术带 V、S、M **任一**成分（成分列表非空交集）；60 尺内存活敌人且其档案 `counterspell_dc > 0` 且有反应预算。**排他**：效果模式 `triggered_damage`（Divine Smite，与命中决策纠缠）与 `reaction_defense`（Shield，走命中反应管线）不开窗。
3. **挂起架构**：开窗的 `cast_spell` 意图**只**产生一条 `dnd2024.decision.pending` 事件——`cast_intent` 深拷贝嵌入决策（服务端构造，非客户端可写）；pending 期间除 `decision.resolve` 与 `combat.message` 外一切意图被拒（照既有挂起阻塞模式）。decline 与 counter-成功都经 `self._spell_events(instance, combat, cast_intent, rng)` 原样重放。
4. **豁免构造**：施法者单 d20 + `saving_throws["con"]` + bless d4（`blessed` condition 时）；DC = 敌人 `counterspell_dc`；P2 `auto_fail_saves` 覆盖 con 时自动失败（不掷骰，当前数据只会有 str/dex，架构对齐即可）。check 事件 `kind: "saving_throw"`、`ability: "con"`、审计键 `"counterspell": True`。
5. **反应经济**：开窗要求威胁有反应；选 counter 才发 `dnd2024.reaction.spent`（reason="counterspell"）；decline 不消耗。
6. **单威胁简化**：多个合格敌人只列一个（actor_id 排序取第一）；RAW 允许多敌各自反应但结果冗余，记录简化。
7. **专注时序**：被反制的新专注法术从未施放——**不发** `concentration.ended`，施法者旧专注保持；豁免成功后的正常结算里"新专注终止旧专注"照旧。豁免发生在 `_spell_events` 之前，bless 加骰天然可用（专注换新法尚未发生）。
8. **敌人施法禁令保持**：`_validate_spell` 对 enemy 施法者的拒绝（"enemy spell profiles are not enabled"）不动——本切片只做敌方反制玩家施法这一方向。

### 1.2 本切片的产品策略与裁剪

- **玩家侧 counterspell 法术不落数据**：敌人不施法，玩家反制无对象；等敌人施法档案落地后按同一管线反向接入。`srd_spells.json` 的 counterspell 元数据已存在，但**不新增** `spell_effects` 条目。
- **预设遭遇的敌人档案**：`counterspell_dc` 只接入 GM 临时遭遇列表（combat.start 的 intent enemies）；encounter preset 目录 schema 支持该字段是后续数据工作。
- **"你看见"**：无视觉系统，不开可见性判定（与前序切片一致）。
- **无成分法术的过滤分支**：目录中若不存在无成分法术（全量 grep `components`），该分支记录为不可测，不造假数据。
- **挂起无超时**：GM 不决策则施法者等待——与借机攻击现状一致，不另建超时/放弃机制。
- **多威胁、反制反制（counter the counter）**：反制是敌人反应不是施法，不递归开窗。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| 意图分发 `cast_spell` 分支直接 `self._spell_events(...)`；`pending` 变量在批末从事件里自动提取 `dnd2024.decision.pending`（引擎自动挂起机制） | `src/rulesets/dnd2024/combat/engine.py` L642-643、L660-662 |
| 挂起决策阻塞：HIT_SPELL_DECISION / HIT_REACTION_DECISION 两条既有规则各限制允许的意图集——P7 的第三条照此模式 | `src/rulesets/dnd2024/combat/validation.py` L209-212 |
| `_decision_events`：按 `decision_id` 取 pending 决策 → 按 kind 分发（HIT_SPELL / HIT_REACTION / 其余按借机攻击的 movement payload 恢复）；借机分支形态：cleared → threat 攻击事件（index 0 替换为 `reaction.spent`）→ position.changed 恢复 | `src/rulesets/dnd2024/combat/resolution.py` L698-735 |
| `reaction.spent` 事件形态 `{"type": "dnd2024.reaction.spent", "actor_id", "reason"}`；reactions 预算存 `combat["reactions"]`，`turn.advanced` 时给新行动者重置 1 | `resolution.py` L725-728；`reducer.py` L285 |
| `_spell_cost_events(actor, spell, slot_level, *, spend_action=True)`：`dnd2024.action.spent`（resource 按 casting_time）+ `dnd2024.spell.slot_spent` / `dnd2024.feat_spell.free_cast_spent`——反制失败路径手写等价 action.spent、跳过 slot/free 部分 | `src/rulesets/dnd2024/combat/triggered_spells.py` L97-127 |
| 敌人档案数值校验循环 `("hp",1,10000), ("armor_class",1,40), ("speed",0,200), ("position",…), ("initiative_modifier",…)` + 默认值模式——`counterspell_dc` 照此追加（0-30，默认 0） | `validation.py` L47-58 |
| `_start_combat_event` 敌人 dict 逐键构造（hp/max_hp/armor_class/speed/abilities/saving_throws/attacks/conditions/creature_type）——`counterspell_dc` 的入态点 | `resolution.py` L60-77 |
| 敌人 view 逐键投影（`_actor_view` enemy 分支）——`counterspell_dc` 的出视图点 | `src/rulesets/dnd2024/combat/view.py` L68-86 |
| `_validate_spell` 拒绝 enemy 施法者（本切片保持不动） | `validation.py` L400-401 |
| 法术成分元数据 `spell["components"]`（如 misty_step `["V"]`、fire bolt `["V","S"]`）；`_distance(combat, a, b)` | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json`；`engine.py` L1092 |
| P2 `condition_flags(registry, conditions)`（auto_fail_saves 聚合）；bless 加骰先例（saving_throw 分支的 `bless_bonus`） | `src/rulesets/dnd2024/combat/check_modes.py`；`resolution.py` L315 |
| reaction_spells.py 的决策常量与 offer/finish 模式（`HIT_REACTION_DECISION`、`_hit_reaction_offer`、`_finish_hit_reaction`）——`COUNTER_SPELL_DECISION` 与窗口/结算函数的同居地 | `src/rulesets/dnd2024/combat/reaction_spells.py` L38-164 |
| counterspell 法术元数据已存在（level 3 / abjuration / sorcerer+warlock+wizard / Reaction / 60 feet / S）——**不新增 spell_effects** | `srd_spells.json` L2959 附近 |
| 测试基建：`SequenceRng` / `start_combat`；施法职业卡先例（`class:wizard` + `equipment_package:wizard_a`） | `tests/rulesets/dnd2024_monk_common.py`；`tests/rulesets/test_dnd2024_smite.py` L15-56 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `src/rulesets/dnd2024/combat/validation.py`

1. 敌人档案数值校验循环追加 `("counterspell_dc", 0, 30)`，默认 0（缺省即无能力）；
2. 挂起阻塞追加第三条（L209-212 模式）：

```python
if any(row.get("kind") == COUNTER_SPELL_DECISION for row in combat.get("pending_decisions", [])) and intent_type not in {"decision.resolve", "combat.message"}:
    raise CombatIntentError("resolve the counter spell decision first")
```

### 3.2 `src/rulesets/dnd2024/combat/resolution.py`

1. `_start_combat_event` 敌人 dict 追加 `"counterspell_dc": int(raw.get("counterspell_dc", 0) or 0)`；
2. `_decision_events` 在 HIT_SPELL / HIT_REACTION 分支之后、movement 恢复之前追加：

```python
if decision.get("kind") == COUNTER_SPELL_DECISION:
    return self._finish_counter_spell(instance, combat, decision, intent, rng)
```

### 3.3 `src/rulesets/dnd2024/combat/view.py`

敌人 view 追加 `"counterspell_dc": int(enemy.get("counterspell_dc", 0) or 0)`。

### 3.4 `src/rulesets/dnd2024/combat/reaction_spells.py`

1. 模块常量 `COUNTER_SPELL_DECISION = "counter_spell"`（与 `HIT_REACTION_DECISION` 同级）；
2. 新增 `_counter_spell_window(self, instance, combat, intent) -> dict | None`：
   - `spell = self.spells.get(spell_ref)`、`effect = self.catalog.spell_effects.get(...)`；`effect["mode"] in {"triggered_damage", "reaction_defense"}` → None；
   - `set(spell.get("components") or []) & {"V", "S", "M"}` 为空 → None；
   - 威胁集合 = 存活（hp>0）且 `counterspell_dc > 0` 且 `combat["reactions"][actor] > 0` 且 `_distance(combat, 施法者, 敌人) <= 60` 的敌人，按 actor_id 排序取第一；
   - 无威胁 → None；有则返回：

```python
{
    "decision_id": f"decision:{intent['intent_id']}",
    "kind": COUNTER_SPELL_DECISION,
    "assigned_to": str(getattr(instance, "gm_uid", "") or ""),
    "options": ["counter", "decline"],
    "threat_actor_ids": [threat_actor_id],
    "cast_intent": deepcopy(intent),
}
```

3. 新增 `_finish_counter_spell(self, instance, combat, decision, intent, rng)`：
   - 首事件 `dnd2024.decision.cleared`（decision_id、option）；
   - option != "counter"（decline）→ `self._spell_events(instance, combat, decision["cast_intent"], rng)` 原样重放并返回；
   - counter：`reaction.spent`（threat、reason="counterspell"）→ 施法者 con 豁免（§1.1 边界 4：单 d20 + con 修正 + bless d4；auto_fail_saves 命中时不掷骰、`rolls: []`、`auto_failed: True`、total 0）→ check.resolved（kind="saving_throw"、ability="con"、source_actor_id=threat、target=dc、审计键 `"counterspell": True`）；
   - 豁免成功 → `self._spell_events(...)` 完整重放并返回；
   - 豁免失败 → 按被反制法术 casting_time 发 `dnd2024.action.spent`（resource=action/bonus_action，amount 1；**不发** slot/free_cast 事件）+ 审计事件 `{"type": "dnd2024.spell.countered", "actor_id": 施法者, "spell_ref": cast_intent 的 spell_ref, "countered_by": threat}`。

### 3.5 `src/rulesets/dnd2024/combat/engine.py`

`cast_spell` 分支改为：

```python
elif intent_type == "cast_spell":
    window = self._counter_spell_window(instance, combat, intent)
    if window is not None:
        events.append({"type": "dnd2024.decision.pending", **window})
    else:
        events.extend(self._spell_events(instance, combat, intent, rng))
```

（pending 由 L660-662 的自动提取挂起；导入 `COUNTER_SPELL_DECISION` 供 validation 侧引用。）

### 3.6 测试

- 新文件 `tests/rulesets/test_dnd2024_counterspell.py`：施法者照 paladin_sheet 先例构造 `class:wizard` + `equipment_package:wizard_a`，`class_spell_choices` 覆盖 fire bolt（戏法）/ misty_step（附赠动作）等，等级推到 3-5 级；敌方用 `goblin()` 基础上加 `"counterspell_dc": 15`。测试矩阵见 §4。
- 回归：`test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_invisibility.py`、`test_dnd2024_hold_person.py`、`test_dnd2024_misty_step.py`、`test_dnd2024_dispel_magic.py` 及 P3/P4/P5 产物测试文件全部通过（`test_dnd2024_character_builder.py` 上游豁免失败不计）。

### 3.7 `src/rulesets/dnd2024/combat/reducer.py`（v1.1 裁决追加）

忽略事件集追加**一项** `"dnd2024.spell.countered"`——纯审计事件，reducer 不改任何状态：

```python
if event_type in {
    "intent.submitted", "check.resolved", "dnd2024.spell.cast",
    "dnd2024.combat.message", "dnd2024.spell.countered",
}:
    return
```

允许范围**仅此一处**：不改任何其它事件类型的处理、不新增任何状态写入（无计数器/标记/conditions/economy 变更）、不动 unknown-type 的 fail-closed 抛错（`unsupported event type`）、不改 `src/rulesets/events.py` 的 `apply_event_batch` / `validate_batch`。

裁决理由（2026-10-30，审核端）：忽略集合是审计事件的既有注册机制（`spell.cast` 本身就是纯审计先例，机械效果全靠其它事件承载）；该事件不进忽略集则 `EventBatchError` 整批拒绝（矩阵 #5 无法交付），并入 `spell.cast` 违反 §1.1#1、并入 `check.resolved` 违反矩阵 #5 的独立事件要求。可观测性已核实：进忽略集后事件在 `apply_batch` 返回的 `event_batch`（`deepcopy(batch)`）、事件 ledger（`next_ledger.append(deepcopy(batch))`）与 AI 叙述者上下文（`latest_event_batch` 读原始 ledger 批次）中完整可见且持久；玩家侧 `recent_combat_events` 的 `meaningful` 白名单未含该类型，静默跳过不报错——展示层滞后照 P2/P8 先例记入执行报告遗留，不阻塞。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 敌人档案校验：`counterspell_dc` 为 bool / 负数 / >30 | combat.start `CombatIntentError`；缺省 0 合法 |
| 2 | 窗口创建：wizard 施 fire bolt（V,S），goblin（dc 15、有反应）60 尺内 | 批内**唯一**事件 `decision.pending`（kind=counter_spell、assigned_to=GM、options=[counter,decline]、threat_actor_ids、cast_intent 深拷贝）；无 cost/slot/spell.cast；apply 后 slots 与 economy 不变、pending_decisions 含该决策 |
| 3 | 挂起阻塞：pending 期间提交 move / attack / 第二个 cast_spell | 三者 `CombatIntentError`；decision.resolve 与 combat.message 放行（代码路径验证） |
| 4 | decline → 完整重放 | cleared + fire bolt 的全部事件（cost、spell.cast、伤害）；决策清空 |
| 5 | counter + 豁免失败（低骰） | 事件序：cleared → reaction.spent(goblin) → check.resolved（con、success=False、counterspell=True）→ action.spent(wizard, action) → spell.countered；**无** spell.cast / slot_spent / 伤害；slots 不变 |
| 6 | counter + 豁免成功（高骰） | cleared → reaction.spent → check.resolved(success=True) → fire bolt 完整结算（含法术位/成本/伤害） |
| 7 | bless：先一回合施 bless（专注），下一回合施 fire bolt 被反制 | 豁免含 bless d4（SequenceRng 骰序：d20 后跟 d4）；豁免期 bless 仍在（专注未换） |
| 8 | 附赠动作法术被反制：misty_step | action.spent 的 resource=bonus_action；无 slot 事件（二环位不消耗） |
| 9 | 窗口条件排除：goblin position 100（>60 尺）；reaction 已耗尽；dc 缺省 0 | 三种均无 decision.pending，法术直接结算 |
| 10 | 专注保持：法师专注 blur 中施 fire bolt 被反制 | 无 concentration.ended；blurred 保留 |
| 11 | 排除路径：triggered_damage（smite）与 reaction_defense（shield）施放 | 无 counter 决策挂起 |
| 12 | 敌人施法禁令回归：GM 提交 enemy 的 cast_spell | 仍 `CombatIntentError`（L400-401 不变） |
| 13 | 无成分法术过滤（条件）：grep 目录确认是否存在无成分法术；有则验证不开窗，无则记录不可测 | 执行报告注明 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_counterspell.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_misty_step.py tests/rulesets/test_dnd2024_dispel_magic.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

（第二行按执行时实际存在的 P3/P4/P5 测试文件名补充，以各任务书测试节为准。）

## 6. 禁止事项

1. 禁止给敌人开施法能力或改动 enemy 施法拒绝（validation L400-401）。
2. 禁止落玩家侧 counterspell 的 `spell_effects` 数据（无敌人施法可反制）。
3. 禁止在窗口/结算里新增具体法术名分支——只认成分、模式、档案字段。
4. 禁止让反制消耗施法者的法术位或免费施放资源（2024：位不消耗）。
5. 禁止给 pending 期间的施法者放行 `decision.resolve`/`combat.message` 之外的任何意图。
6. 禁止把 `cast_intent` 做成客户端可写或可影响的字段（服务端深拷贝，决策载荷不可注入）。
7. 禁止实现多威胁多重反制（单威胁，§1.1 边界 6 已记录简化）。
8. 禁止改动 HIT_SPELL_DECISION / HIT_REACTION_DECISION 的既有行为与借机攻击恢复路径。
9. 禁止把窗口触发源扩到移动/受击等其他事件（那是后续反应源扩展）。
10. 禁止顺手实现敌人通用施法档案或 spell_save_dc 体系（`counterspell_dc` 是最小能力字段）。

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. `_decision_events` 存在本任务书未列出的第四种 kind 分支，或新分支与 movement 恢复路径结构冲突；
2. `decision.resolve` 的选项校验不覆盖新 kind（"counter"/"decline" 被既有选项白名单拒绝）；
3. 既有前端/投影对 `dnd2024.spell.countered` 事件或 check 事件的 `counterspell` 键无法容忍；
4. `cast_intent` 深拷贝在决策持久化/reducer 路径中被突变或丢失，导致重放失败；
5. 反应预算重置时机（`turn.advanced`）与窗口假设冲突（如敌人回合边界反制预算异常）；
6. 任何白名单外文件必须修改才能通过验收。（2026-10-30 裁决例外：`reducer.py` 忽略集合追加 `dnd2024.spell.countered` 已授权并升 v1.1 写入 §3.7——纯审计事件走既有注册机制，不构成白名单突破先例；除此之外本条不变。）

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P7_COUNTERSPELL_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况（含 #13 的条件性结论）、§3 语义与实现的偏差（若有，尤其玩家侧反向接入的后续路径）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

## 9. 修订记录

- v1（2026-10-29）：初版。
- v1.1（2026-10-30）：审核端裁决——§3 白名单追加 `reducer.py`（§3.7：忽略集合加 `dnd2024.spell.countered`，纯审计不改状态），§7.6 记录例外。触发原因：矩阵 #5 的审计事件在 reducer 无处理会触发 `EventBatchError` 整批拒绝，不发事件违反矩阵 #5、并入 `spell.cast`/`check.resolved` 违反 §1.1#1 与独立事件要求。可观测性结论：事件在返回批次、事件 ledger、叙述者上下文完整可见；玩家侧 combat feed 白名单滞后为展示层遗留。

## 10. 执行报告（2026-10-30）

### 10.1 变更文件清单

生产代码（6 个文件，均在 §3 白名单内；`reducer.py` 为 §3.7 v1.1 授权）：

- `src/rulesets/dnd2024/combat/reaction_spells.py`：导入 `enemy_actor`；新增 `COUNTER_SPELL_DECISION = "counter_spell"`；新增 `_counter_spell_window`（L271-314，触发条件：非 `triggered_damage`/`reaction_defense`、有 V/S/M 任一成分、hp>0 且 `counterspell_dc>0` 且有反应且 60 尺内的敌人，多个按 actor_id 排序取第一）与 `_finish_counter_spell`（L316-379，cleared → 可选 `reaction.spent` → con 豁免 → 成功重放 / 失败 `action.spent` + `spell.countered`）。既有 HIT_REACTION 路径未动。
- `src/rulesets/dnd2024/combat/engine.py`：仅 `cast_spell` 分支（L643-648）先探测窗口，命中则只发 `dnd2024.decision.pending`，否则照旧 `_spell_events`。
- `src/rulesets/dnd2024/combat/validation.py`：敌人档案数值循环加 `("counterspell_dc", 0, 30)`、缺省 0；挂起阻塞新增 COUNTER 条款（只放行 `decision.resolve` / `combat.message`）。敌人施法拒绝（L411-412）未动。
- `src/rulesets/dnd2024/combat/resolution.py`：combat.start 敌人入态写入 `counterspell_dc`（缺省 0，L95）；`_decision_events` 增加 COUNTER 分支（L1179-1180）。movement / 借机攻击恢复路径未动。
- `src/rulesets/dnd2024/combat/view.py`：敌人 actor view 暴露 `counterspell_dc`（L85），供 `_finish_counter_spell` 读取 DC。
- `src/rulesets/dnd2024/combat/reducer.py`：仅忽略集合追加 `dnd2024.spell.countered`（L33）。未改其它事件分支、未新增任何状态写入、unknown-type 的 `unsupported event type` fail-closed 抛错未动。

测试（1 个新文件）：`tests/rulesets/test_dnd2024_counterspell.py`（13 个用例，覆盖 §4 矩阵 #1-#13）。

文档：本任务书 v1.1（§3.7 / §7.6 / §9）与本节。

未改动：`src/rulesets/events.py`、`src/rulesets/dnd2024/runtime.py`、`src/rulesets/dnd2024/combat/catalog.py`、`check_modes.py`。

### 10.2 测试矩阵结果（13/13 通过）

| # | 结果 | 关键断言 |
|---|---|---|
| 1 | 通过 | `counterspell_dc` 为 bool / -1 / 31 时 combat.start 拒绝且错误含 `counterspell_dc`；缺省档案合法 |
| 2 | 通过 | 批内除固定 `intent.submitted` 外唯一 `decision.pending`（kind=counter_spell、assigned_to=GM、options=[counter,decline]、threat_actor_ids=[enemy:goblin-1]、cast_intent 为服务端深拷贝且保留原 expected_version）；无 action/slot/spell.cast；apply 后 slots 与 economy 不变、pending_decisions 含该决策 |
| 3 | 通过 | pending 期间 move / attack / 第二个 cast_spell 均以 `resolve the counter spell decision first` 拒绝；decision.resolve 与 combat.message 放行 |
| 4 | 通过 | decline 事件序 cleared → action.spent → spell.cast → check.resolved(spell_attack) → resource.changed；伤害落地；pending 清空 |
| 5 | 通过 | 事件序恰为 cleared → reaction.spent(goblin, reason=counterspell) → check.resolved(con、success=False、counterspell=True) → action.spent(wizard, action) → spell.countered；无 spell.cast / slot_spent / 伤害；slots 不变；goblin 反应归零 |
| 6 | 通过 | 豁免成功 → fire bolt 完整结算（cost、spell.cast、spell_attack、伤害）；slots 不变（戏法） |
| 7 | 通过 | bless 的 d4 进入反制豁免（骰序 d20 → d4，`bless_bonus=2`）；原始批次无 `concentration.ended`，`blessed` 保留 |
| 8 | 通过 | misty_step 被反制时 `action.spent` 的 resource=bonus_action、无 slot_spent、无 position.changed、slots 不变 |
| 9 | 通过 | 位置 100（>60 尺）/ 反应耗尽 / dc=0 三种情况均无 decision.pending，法术直接结算 |
| 10 | 通过 | blur 专注中施 fire bolt 被反制：无 concentration.ended，`blurred` 保留 |
| 11 | 通过 | `_counter_spell_window` 对 `spell:divine_smite`（triggered_damage）与 `spell:shield`（reaction_defense）均返回 None；同场景普通法术返回非 None（见 10.3 偏差 1） |
| 12 | 通过 | 敌人为当前行动者时 GM 提交其 cast_spell 仍报 `enemy spell profiles are not enabled for this actor` |
| 13 | 条件性通过 | `srd_spells` 的 339 条法术全部声明成分（spell catalog 装载即拒绝空 `components`，见 `src/rulesets/dnd2024/spells/catalog.py`），因此无成分过滤分支当前不可达；测试改为断言"全部法术有成分"，不开不可测的伪造目录 |

### 10.3 与任务书 §3 语义的偏差

1. **#11 采用窗口级验证**：直接调用 `_counter_spell_window`，未构造 smite / shield 的完整施法批次。原因：shield 只作为 `reaction_defense` 受击反应的产物、divine_smite 需在 `melee_hit` 挂起内提交，完整批次会把测试耦合进 P3/P6 既有链路；二者在窗口判定首个 mode 分支即返回 None，语义等价。实现本身无偏差。
2. **#13 分支不可达**（如 10.2 所述），未写入死代码。
3. **单威胁简化**：多个满足条件的威胁按 actor_id 排序取第一（§1.1 边界 6 已声明）。
4. **挂起无超时**（§1.2 裁剪已声明）。
5. **玩家侧 counterspell 未落**：`spell_effects` 无玩家侧反制条目，本切片只覆盖"敌人反制玩家施法"。玩家反向接入需要敌人具备可被反制的施法档案；当前 `_validate_spell` 对非 player/companion 仍 fail-closed，属后续切片。
6. **豁免细节**：使用施法者 `saving_throws["con"]`（含熟练加值），与 P2 saving_throw 分支同构；`auto_fail_saves` 命中 con 时不掷骰（`rolls=[]`、`natural=0`、`total=0`、`auto_failed=True`）。

### 10.4 遗留问题（不属本切片，未修）

1. `src/rulesets/dnd2024/runtime.py`：
   - `recent_combat_events` 的 `meaningful` 白名单未包含 `dnd2024.spell.countered`：玩家侧 combat feed 静默跳过该审计事件（不报错）；
   - 同处 `check.resolved` 白名单仍缺 `ability` 键（P8 已登记）：反制豁免的 `con` 属性名在玩家投影中被剥离。
   三处权威可见性（`apply_batch` 返回批次、事件 ledger、叙述者上下文）完整，属展示层决策，按裁决未改 `runtime.py`。
2. `counterspell_dc` 只接入 `combat.start` 临时遭遇（含预设展开后的敌人档案）；内容目录/旧存档若未带该字段一律按 0（不可反制）。
3. 敌人无通用施法档案，敌我双向反制循环尚不可玩。

### 10.5 验收结果

- 新测试：`13 passed`。
- §5 回归子集（9 个文件）：`152 passed`；补 `scorching_ray / false_life / stoneskin` 后回归合计 `188 passed`，与改动前基线一致。
- 全量：`603 passed, 39 deselected, 0 failed`（`--deselect tests/rulesets/test_dnd2024_character_builder.py`，28:17）；590 基线 + 13 新用例。
