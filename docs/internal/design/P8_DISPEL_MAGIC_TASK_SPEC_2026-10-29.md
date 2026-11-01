# 切片 P8 任务书：Dispel Magic（魔法 condition 移除 + 法术来源快照）

版本：v1.1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 P1（invisibility）、P5（stoneskin）、P2（hold_person）、P3（scorching_ray）、P4（false_life）、P6（misty_step）之后执行。P2 提供 condition 元数据的 `save_repeat` 快照先例与 `condition_flags`；P6 已把 `teleport` 加入 `SPELL_MODES` 与盟友集合。前置切片合入后本任务书引用的行号会偏移，描述以语义位置为准。catalog 校验的"允许键集合"以已执行切片的并集为准。

---

## 1. 目标

为 D&D 2024 规则集实现 **dispel_magic（解除魔法，3 环）** 的完整闭环：目录数据 → 施法（任意阵营目标）→ 依目标身上每个法术来源 condition 的环阶判定（≤ 施法环阶自动终止；更高环阶施法属性检定 DC 10+环阶）→ 专注法术整道终止（级联）→ 测试。

本切片同时沉淀两项通用原语：

1. **法术来源快照**：`_spell_events` 的 condition 应用尾部为每份 `condition.applied` 追加 `source_spell_ref`（法术 ref）与 `source_spell_level`（施放时实际环阶：intent 的 `slot_level`，免费施放取法术基础环阶）——condition 从此携带"我来自哪道法术、几环"的机械可判 provenance；无该元数据的 condition（rage、dodging、stable、zero_hp unconscious 等）天然不可解除（fail closed）；
2. **魔法移除模式**：新效果模式 `"dispel"`——目标可任意阵营（第三类目标校验，既非盟友必需也非敌对必需）；对目标 conditions 逐个判定：`source_spell_level <= slot_level` → 终止；否则施法属性检定（d20 + 施法属性修正 vs 10 + 来源环阶），成功才终止。专注法术的 condition 终止时发 `dnd2024.concentration.ended`（owner=该法术施法者，reason=dispelled），由既有 reducer 级联移除该 owner 的全部专注 condition（整道法术跨目标终止，RAW"法术即告终止"）；非专注来源发 `condition.removed`（reason=dispelled）。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 解除魔法：三环防护（吟游诗人、牧师、德鲁伊、圣武士、游侠、术士、魔契师、法师）。动作；施法距离 120 尺；成分 V、S；持续时间立即。在施法距离内指定**一名生物、一个物件或一处魔法效应**。所有影响该目标的**三环或更低环阶法术即告终止**。每个影响目标的四环或更高环阶法术都需要以你的**施法属性进行一次属性检定（DC 10+目标法术环阶）**，检定成功时目标法术终止。**升环施法**：你直接终止影响目标的小于等于该施法环阶的法术效应。
- 关键措辞差异（与 P2 的重复豁免对照）：解除魔法写"**法术**即告终止"（整道法术，含专注施法者的专注与全部目标）；定身类人的重复豁免写"终止**其身上该法术的效应**"（仅该目标）。两者是不同的 RAW 语义，不得混用。

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/3环.htm` L104-106（解除魔法）。

### 1.1 必须锁定的规则边界

1. **"法术终止"是整道终止**：被解除的是专注法术 condition 时，发 `dnd2024.concentration.ended`（actor_id=owner、reason="dispelled"），reducer 级联移除 `concentration_owner == owner` 的全部 condition（所有目标）——不用逐目标 `condition.removed`；同一 owner 在一次 dispel 内去重只发一次。非专注来源（如 ray_of_frost 的 slowed_10）只发 `condition.removed`（target_id、condition、reason="dispelled"）。
2. **判定阈值是施放环阶**：自动终止条件 `source_spell_level <= slot_level`（intent 的施法环阶，含升环）；更高环阶走检定 `d20 + 施法属性修正 vs 10 + source_spell_level`。基础三环位即 `slot_level == 3`，升环公式天然成立，无独立 upcast 字段。
3. **来源环阶是施放时快照**：`source_spell_level` 取施放当刻的 `slot_level`（用 4 环位施放的 hold_person 是 4 环法术）；免费施放（`slot_level == 0` 的非戏法）取 `spell["level"]`；戏法为 0。
4. **只有带来源元数据的 condition 可被解除**：判定循环跳过元数据缺 `source_spell_ref` 的条目（rage、dodging、stable、death_saves、zero_hp unconscious、GM 注入的裸 condition）。不新增任何"哪些 condition 是魔法"的硬编码清单。
5. **检定是属性检定，不是豁免**：单 d20 + `ability_modifier(actor["abilities"][actor["spell_ability"]])`；**不吃 bless**（bless 只加攻击检定与豁免检定）、不吃狂暴优势、不适用 P2 的 `auto_fail_saves`（那是豁免自动失败，不覆盖属性检定）。check 事件 `kind: "ability_check"`（新 kind 值）、`ability: spell_ability`。
6. **目标可任意阵营**：dispel 豁免"offensive spells require a hostile target"分支（可解除盟友身上的敌方 debuff，也可解除敌人身上的增益）；但 **dead 目标拒绝**（ dying 未死目标允许——身上的 hex 等仍可解除）。
7. **逐 condition 判定、插入序处理**：目标身上多个法术来源 condition 各自独立判定（各有各的检定）；遍历顺序 = conditions dict 插入序（确定性，照 P2 tick 先例）。
8. **dispel 自身是普通施法**：动作 + 3 环位 + `slot_spell_cast` 上限；无专注；`spell.cast` 事件先行；被 dispel 的施法者专注是否因此中断只由事件流决定（本切片不发额外 concentration.ended 给 dispel 施法者）。
9. **RNG 消耗可预测**：每个高于阈值的来源 condition 消耗恰好一个 d20；≤ 阈值的不消耗。

### 1.2 本切片的产品策略与裁剪

- **物件与魔法效应目标**：只支持生物（actor）目标——引擎没有物件实体与独立魔法效应对象。如实记录裁剪。
- **reaction 路径的来源快照缺口**：`_finish_hit_reaction`（shield 的 reaction_defense）等非 `_spell_events` 主循环的 condition 应用点**不**加快照——shield 条目只存续一轮，解除无意义。记录为已知缺口，不在本切片扩。
- **不可解除的非魔法 condition**：按 §1.1 边界 4 的元数据判定，不维护"可解除清单"。
- **区域/持续效应的"魔法效应"目标**（如对一处 web 效应施放）：无区域效应实体，裁剪。
- **greater restoration / remove curse 家族**（终止诅咒、麻痹、恐慌等 condition 通用移除）：`removes_conditions` 效果字段（P5 已交付）覆盖"按清单移除"，本切片的 dispel 是"按法术来源移除"，两原语并存；greater restoration 等数据后续补录。
- **前端**：dispel 无新增 intent 选择字段（无 resist_type 类选择），面板按既有 cast_spell 流程可用（目标选择器需允许任意阵营目标——服务端已放行，面板若过滤为已知偏差，照 P2"投影滞后"先例）。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `_spell_events` 主循环：`actor = self._spell_caster(self._actor_view(...), spell_ref)`（专长法术的施法属性包装）→ cost/专注 → `spell.cast` → 目标循环 `target = self._actor_view(...)` → mode 分发 | `src/rulesets/dnd2024/combat/resolution.py` L233-267 |
| condition 应用尾部（P8 的快照注入点）：`condition_applies = (saving_throw 且 not succeeded) or (spell_attack/automatic_damage 且 succeeded) or (buff/debuff)`；payload 现有键 `duration` / `source_actor_id` / `concentration_owner`（P1 `ends_on`、P2 `save_repeat` 在此追加） | `resolution.py` L393-405 |
| `_validate_spell` 目标循环：盟友集合 L449（P6 后 `{healing, buff, stabilize, temp_hp, teleport}`）+ dead 拒绝 L452-453；敌方 elif L454-455；hp≤0 拒绝集合 L461-462；射程 L463-464；模式级特判先例（P2 teleport 自身限定、P6 `teleport_distance`） | `src/rulesets/dnd2024/combat/validation.py` L445-467 |
| `SPELL_MODES` frozenset（P6 后含 `teleport`） | `src/rulesets/dnd2024/combat/catalog.py` L16-19 |
| `condition.removed` reducer 只 pop（`reason` 等多余键无害）；`concentration.ended` 清 owner 专注位并级联移除全部 `concentration_owner == owner` 的 condition | `src/rulesets/dnd2024/combat/reducer.py` L221-224、L268-276 |
| `condition.applied` 的全部事件键（除 type/target_id/condition）进元数据——快照键由此天然入元数据 | `reducer.py` L208-213 |
| 玩家 view 的 `spell_ability`（str，如 "int"；经 `_spell_caster` 包装后是专长法术的实际施法属性）与 `abilities` | `src/rulesets/dnd2024/combat/view.py` L129、L154、L170-172 |
| `ability_modifier(abilities[key])` | `src/rulesets/dnd2024/character/builder.py`（resolution.py 已导入） |
| 敌人档案 conditions 直透（`_actor_view` 直读 `enemy.get("conditions")`）——敌人侧带元数据 condition 的注入测试点 | `view.py` L68-86 |
| 免费施放判定（`slot_level == 0` 时 `free_cast_resource`）与 `slot_level` 语义 | `validation.py` L425-434 |
| dispel_magic 法术元数据已存在（level 3 / abjuration / 八职业 / Action / 120 feet / V,S / Instantaneous / 无专注），无需新增 | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json` L3019 附近 |
| 测试基建：`SequenceRng` / `start_combat`；施法职业卡构造先例（paladin_sheet 模式 + `class:wizard` + `equipment_package:wizard_a`，推到 5 级拿三环位）；多玩家 instance 先例（P1 测试 #9） | `tests/rulesets/dnd2024_monk_common.py` L174-184；`tests/rulesets/test_dnd2024_smite.py` L15-56；`tests/rulesets/test_dnd2024_invisibility.py` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

`spell_effects` 追加一条：

```json
"dispel_magic": {"mode": "dispel", "range": 120, "target_count": 1}
```

conditions 注册表无需新增（无新 condition）。

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

`SPELL_MODES` 追加 `"dispel"`。无新增逐字段校验（range [5,600] 既有检查覆盖 120）。

### 3.3 `src/rulesets/dnd2024/combat/validation.py`（`_validate_spell` 目标循环）

1. 敌我分支：`elif target_side == actor_side and effect["mode"] != "dispel":` 保持既有拒绝——dispel 任意阵营放行（§1.1 边界 6）；
2. hp≤0 拒绝集合追加 `"dispel"`（dying 目标允许）；
3. dispel 模式级特判（照 P2/P6 模式级先例）：

```python
if effect["mode"] == "dispel" and "dead" in target.get("conditions", {}):
    raise CombatIntentError("dead targets cannot be dispelled")
```

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

#### A. 来源快照（condition 应用尾部）

`condition.applied` payload 追加两个服务端构造键（有 condition 应用时始终追加，不只 dispel 场景）：

```python
"source_spell_ref": spell_ref,
"source_spell_level": int(slot_level) if int(slot_level) > 0 else int(spell["level"]),
```

#### B. dispel 分支（目标循环 mode 分发，与 healing 等同级）

```python
if mode == "dispel":
    ended_owners: set[str] = set()
    for condition_id, meta in list(target["conditions"].items()):
        if not isinstance(meta, dict) or not meta.get("source_spell_ref"):
            continue
        source_level = int(meta.get("source_spell_level", 0) or 0)
        if source_level > slot_level:
            modifier = ability_modifier(actor["abilities"][actor["spell_ability"]])
            roll = int(rng.randint(1, 20))
            success = roll + modifier >= 10 + source_level
            events.append({
                "type": "check.resolved", "kind": "ability_check",
                "actor_id": actor_id, "target_id": target_id,
                "ability": actor["spell_ability"], "rolls": [roll],
                "natural": roll, "modifier": modifier, "bless_bonus": 0,
                "total": roll + modifier, "target": 10 + source_level,
                "success": success, "condition": condition_id,
            })
            if not success:
                continue
        owner = str(meta.get("concentration_owner") or "")
        if owner and owner not in ended_owners:
            events.append({
                "type": "dnd2024.concentration.ended", "actor_id": owner,
                "reason": "dispelled",
            })
            ended_owners.add(owner)
        elif not owner:
            events.append({
                "type": "condition.removed", "target_id": target_id,
                "condition": condition_id, "reason": "dispelled",
            })
```

不设 `succeeded`、不经过 `_movement_events`；专注 condition 的移除由 reducer 级联完成（本分支不发逐目标 removed，§1.1 边界 1）。`slot_level` 取 intent 施放环阶。

### 3.5 测试

- 新文件 `tests/rulesets/test_dnd2024_dispel_magic.py`：施法者照 paladin_sheet 模式换 `class:wizard` + `class_spell_choices` 含 `spell:dispel_magic` + `equipment_package:wizard_a`，推进到 5 级拿三环位；升环验证（矩阵 #8 的 4 环位）需要 7 级——full 施法档案 L5 为 `{1:4,2:3,3:2}`（无 4 环）、L7 才有 `{4:1}`——测试统一推进到 7 级。敌人侧带元数据 condition 用档案注入；玩家/盟友侧用真实施法（hold_person/blur 等）或双玩家 instance。测试矩阵见 §4。
- 回归：`test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_invisibility.py`、`test_dnd2024_hold_person.py`、`test_dnd2024_misty_step.py` 及 P3/P4/P5 产物测试文件全部通过（`test_dnd2024_character_builder.py` 上游豁免失败不计）。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：mode 不在 SPELL_MODES 的效果照拒；`dispel` 条目通过加载 | `CombatCatalogError` / 正常 |
| 2 | 来源快照：法师 2 环位施 hold_person 命中 goblin | `condition.applied` 元数据含 `source_spell_ref == "spell:hold_person"`、`source_spell_level == 2`、`concentration_owner`；ray_of_frost（戏法）的 slowed_10 元数据 `source_spell_level == 0` |
| 3 | 免费施放快照：free_cast 路径施放非戏法（Magic Initiate 专长的免费 1 环法术；smite 家族是 `triggered_damage` 且不施加 condition，不可作载体——v1 原举例有误） | `source_spell_level == spell["level"]`（slot_level 0 时取基础环阶） |
| 4 | 自动终止（≤ 阈值）：3 环 dispel 对带 slowed_10（level 0）的 goblin | `condition.removed`（reason=dispelled）；无 check.resolved；SequenceRng 零消耗；rage/dodging 等无元数据条目不动 |
| 5 | 专注整道终止：法师单次 3 环 upcast hold_person 同时定身 goblin-1/goblin-2（v1 原描述「先 2 环再升环两次施法」与专注 RAW 冲突——第二次专注会终止第一次），3 环 dispel 打 goblin-1 | `dnd2024.concentration.ended`（owner=法师、reason=dispelled）**各一次**；apply 后**两个** goblin 的 paralyzed 都移除（级联）；法师专注位清空 |
| 6 | 高环检定失败：goblin 注入 `{"paralyzed": {"source_spell_ref": "spell:hold_person", "source_spell_level": 4, "concentration_owner": "enemy:goblin-1"}}`，3 环 dispel 低骰 | `check.resolved`（kind=ability_check、ability="int"、target=14、success=False）；paralyzed 保留；无 concentration.ended |
| 7 | 高环检定成功：同场景高骰 | check success=True → `concentration.ended`（owner=enemy:goblin-1）→ 级联移除 |
| 8 | 升环：4 环位 dispel 对 level 4 来源 | 无检定直接终止（`source_level <= slot_level`） |
| 9 | 目标任意阵营：dispel 敌方 goblin（注入敌方来源 condition）、dispel 己方盟友（法师自己 blur 后另一玩家 dispel） | 两者均 intent ok |
| 10 | dead / dying：对 dead 玩家施 dispel 被拒；对 0 HP 未死（dying）盟友施 dispel 放行 | `CombatIntentError` / ok |
| 11 | 多来源：goblin 同时带 slowed_10（level 0，自动）+ level 4 专注 condition（检定） | 事件序：condition.removed → check.resolved → （成功时）concentration.ended；插入序确定 |
| 12 | 经济：dispel 消耗动作 + 三环位 + `slot_spell_cast` 置位 | economy / slots 断言 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_dispel_magic.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_misty_step.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

（第二行按执行时实际存在的 P3/P4/P5 测试文件名补充，以各任务书测试节为准。）

## 6. 禁止事项

1. 禁止给专注 condition 的终止发逐目标 `condition.removed` 来替代 `concentration.ended` 级联（整道法术终止是 RAW；与 P2 save_repeat 的"仅该目标效应终止"是不同语义）。
2. 禁止在判定循环里维护任何"可解除 condition 清单"硬编码——只认 `source_spell_ref` 元数据。
3. 禁止给检定加 bless、狂暴优势或 `auto_fail_saves`（属性检定不是豁免，§1.1 边界 5）。
4. 禁止在 resolution / validation 新增具体法术名分支——dispel_magic 只允许目录数据 + dispel 模式。
5. 禁止实现物件/魔法效应目标、区域效应解除（§1.2 已裁剪）。
6. 禁止把 `source_spell_ref` / `source_spell_level` 做成客户端可写的 intent 字段——只能由服务端在 condition 应用时构造。
7. 禁止在 `_finish_hit_reaction` 等非主循环路径顺手加快照（§1.2 已记录缺口）。
8. 禁止改动 `concentration.ended` 的 reducer 级联行为或 P2 的 save_repeat 语义。
9. 禁止顺手落 greater restoration / remove curse 数据（`removes_conditions` 原语已存在，数据后续补）。

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. `check.resolved` 的既有消费方（投影/前端契约测试）无法容忍新 kind 值 `"ability_check"` 或 `condition` 审计键；
2. condition 应用尾部除 `_spell_events` 主循环外发现其他也应用法术 condition 的路径且测试必须覆盖（如 reaction_defense 被判必须加快照）；
3. 玩家 view 的 `spell_ability` 在某些施法者上为空串（`abilities` 缺键），检定无法构造；
4. 免费施放路径的 `slot_level` 语义与 §1.1 边界 3 冲突（快照取值无法同时满足 smite 与 dispel 测试）；
5. 任何白名单外文件必须修改才能通过验收。

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P8_DISPEL_MAGIC_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况、§3 语义与实现的偏差（若有，尤其物件/魔法效应裁剪与 reaction 路径缺口）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

## 9. 修订记录

- v1（2026-10-29）：初版。
- v1.1（2026-10-30）：审核修正三处任务书自身错误，实现与测试自始正确、未改动——①§3.5「升环验证需要 5 级拿四环位」为笔误（full 施法档案 L7 才有 4 环位，测试统一推进到 7 级）；②矩阵 #3 载体改为 Magic Initiate 免费 1 环法术（smite 是 `triggered_damage` 不施加 condition）；③矩阵 #5 构建方式改为单次 3 环 upcast hold_person（原「两次独立施法」与专注 RAW 冲突）。执行报告 §10.4 记录的即这三处。

## 10. 执行报告（2026-10-30）

### 10.1 变更文件清单（严格白名单）

| 文件 | 变更 |
|---|---|
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | `spell_effects` 追加 `dispel_magic`（mode=dispel、range=120、target_count=1） |
| `src/rulesets/dnd2024/combat/catalog.py` | `SPELL_MODES` 追加 `"dispel"` |
| `src/rulesets/dnd2024/combat/validation.py` | 阵营拒绝分支放行 dispel；dispel dead 特判；hp≤0 拒绝集合追加 dispel |
| `src/rulesets/dnd2024/combat/resolution.py` | condition.applied 追加服务端来源快照；`elif mode == "dispel"` 逐 condition 判定 + 专注整道终止/非专注单目标移除 |
| `tests/rulesets/test_dnd2024_dispel_magic.py` | 新增，12 个用例覆盖 §4 矩阵 |

未改动 `reducer.py` / `view.py` / `engine.py` / `frontend-v2`：级联与元数据写入均为既有契约，`ability_check` 前端有回退文案。白名单外无变更。

### 10.2 测试结果

- 新增测试：`test_dnd2024_dispel_magic.py` **12 passed**（45.83s）。
- 回归子集（combat/spells/smite/uncanny_dodge/monk_combat/invisibility/hold_person/misty_step + scorching_ray/false_life/stoneskin + 新测试）：**188 passed**（529.18s）。
- 全量：`pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py` → **590 passed, 39 deselected, 0 failed**（1669.80s / 27:49）。590 = 578 基线 + 12 新增。

### 10.3 §4 矩阵逐项结果

| # | 用例 | 结果 |
|---|---|---|
| 1 | `test_catalog_rejects_unknown_modes_and_accepts_dispel` | 通过：原目录可加载；未知 mode 与非法 range 均抛 `CombatCatalogError` |
| 2 | `test_condition_snapshot_records_spell_ref_and_cast_level` | 通过：hold_person（2 环位）快照 ref/level=2/owner；ray_of_frost（戏法）level=0 |
| 3 | `test_feat_free_cast_snapshots_the_spell_level` | 通过：Magic Initiate (Wizard) 免费施放 charm_person（slot 0）快照 level=1，并出现 `feat_spell.free_cast_spent` |
| 4 | `test_low_level_source_is_removed_without_consuming_a_roll` | 通过：slowed_10 自动终止（reason=dispelled），无 check，RNG 零消耗，rage/dodging 保留 |
| 5 | `test_concentration_source_ends_the_whole_spell_across_targets` | 通过：恰一条 `concentration.ended`（owner=player:gm、reason=dispelled），两个 goblin 的 paralyzed 均被级联移除，法师专注位清空 |
| 6 | `test_high_level_source_failure_keeps_the_condition` | 通过：`check.resolved(kind=ability_check, ability=int, target=14, success=False)`，paralyzed 保留，无 ended |
| 7 | `test_high_level_source_success_ends_the_concentration` | 通过：check 成功 → `concentration.ended(owner=enemy:goblin-1)` → 级联移除 |
| 8 | `test_upcast_dispel_removes_a_source_matching_the_slot_level` | 通过：4 环位 dispel 对 level 4 来源无检定直接 `condition.removed` |
| 9 | `test_dispel_targets_hostiles_and_allies` | 通过：敌方 goblin 与自己 blur 后的盟友 `player:ally` 均放行并正确移除 |
| 10 | `test_dispel_rejects_dead_targets_but_allows_dying_ones` | 通过：dead 盟友被拒（"dead targets cannot be dispelled"）；0 HP 未死盟友放行 |
| 11 | `test_multiple_sources_are_resolved_in_insertion_order` | 通过：事件序 `condition.removed` → `check.resolved` → `concentration.ended` |
| 12 | `test_dispel_spends_the_action_and_a_slot` | 通过：action 1→0、`slot_spell_cast=True`、3 环位 3→2、`spell.cast.slot_level=3` |

### 10.4 与任务书语义的偏差（均已记录，无未授权扩权）

1. **四环位等级**：§3.5 写「5 级拿四环位」，bundle 实际法师升级表为 L5 `{1:4,2:3,3:2}`、L7 `{1:4,2:3,3:3,4:1}`，四环位在 7 级。测试统一推进到 7 级，矩阵 #8 用真实 4 环位；未改任何数据。
2. **矩阵 #3 载体**：任务书举例 smite 家族，但 smite 是 `triggered_damage` 且不施加 condition，无法产生带快照的 condition。改用 Sage 背景 Magic Initiate (Wizard) 的免费施放 charm_person（1 环），既验证 `slot_level==0` 时快照取法术基础环阶，也顺带覆盖免费施放路径。
3. **矩阵 #5 构建方式**：同一施法者「先 2 环定身 goblin-1，再升环定身 goblin-2」两次专注施法与 RAW 冲突（第二次专注会先终止第一次）。改为单次 3 环 upcast hold_person 同时命中两个 goblin（双方豁免失败），再 3 环 dispel goblin-1，验证一次 `concentration.ended` 对两个目标的级联移除。语义目标（整道法术跨目标终止）不变。
4. **施法属性修正**：Sage 背景能力加成使法师 int=16，dispel 检定修正实测 +3（非先前推算的 +2）。测试以 `ability_modifier(abilities["int"])` 动态计算，不硬编码。
5. **reaction 路径快照缺口**：shield 等 `_finish_hit_reaction` 路径不加快照（§1.2 已声明），未扩。
6. **物件/魔法效应/区域目标**：引擎无对应实体，按 §1.2 裁剪为仅生物目标，未实现。
7. **前端目标选择器**：面板按既有 cast_spell 流程可用，任意阵营目标的服务端已放行；前端是否过滤盟友/敌方未在本切片验证，按 §1.2「投影滞后」记录，未改前端。

§7 停止条件均未触发：`check.resolved` 新 kind 有前端回退文案；主循环外 condition 路径按声明缺口处理；`spell_ability` 对法师为 `"int"`；免费施放 slot_level 语义与快照取值一致；白名单内即可完成验收。

### 10.5 遗留问题

- 树内 8 个 `docs/internal/design/SP*_TASK_SPEC_2026-10-30.md`（SP1/SP2/SP4/SP5/SP7/SP8/SP10/SP12）为其它会话的预研产物：未读、未改、未删、未暂存，不属于本切片。
- 本切片未 commit，等待审核端。
