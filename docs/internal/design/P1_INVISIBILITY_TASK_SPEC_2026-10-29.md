# 切片 P1 任务书：Invisibility（attack_sources 数据驱动 + 感官修正）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片在 1A 切片 5（Sneak Attack）之后执行。若切片 5 已合入，`resolution.py` 的命中分支行号会偏移、`attack_damage_node` 已是多数骰参数；本任务书对命中分支的描述以语义位置为准，不要按行号机械对照。

---

## 1. 目标

为 D&D 2024 规则集实现 **invisibility（隐形术，2 环）** 的完整闭环：目录数据 → 施法（含升环多目标）→ 隐形条件的攻击优劣势结算 → 施法者/目标行为提前终止 → 感官对抗（识破隐形）→ 测试。

本切片同时沉淀三项通用原语：

1. **condition → 攻击模式注册表（数据驱动）**：condition 在战斗目录中声明它对攻击掷骰的优劣势贡献（挂在攻击者身上还是目标身上、方向、被哪些感官 condition 抵消、是否攻击后消耗），`check_modes.py` 不再硬编码来源清单——偿还"特性/法术无法注册新来源"的债；
2. **升环目标数**：`target_count` 随施法环阶增长（`upcast_target_count`），供 invisibility / hold_person 等共用；
3. **专注法术提前终止触发**：法术效果声明 `ends_on`（目标进行攻击检定 / 造成伤害 / 施展法术后整道法术终止）。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 隐形术：二环幻术（吟游诗人、术士、魔契师、法师）。施法时间动作；施法距离触碰；成分 V、S、M；持续时间专注，至多 1 小时。你触碰的一个生物进入隐形状态并维持至法术终止。**如果目标进行攻击检定、造成伤害或施展法术，则此法术提前终止**。升环：使用的法术位每比二环高一环，就可以额外指定一个生物作为目标。
- 隐形状态（Invisible 条件）：以你为目标的攻击检定具有劣势，而你进行的攻击检定具有优势。如果一个生物能以某种方式看见你，那么你在面对该生物时不会获得这一增益。（另两条效应见 §1.2 裁剪。）
- 高等隐形术：四环，触碰，专注至多 1 分钟，目标直到法术结束前具有隐形状态——**无**提前终止条款、无升环目标。
- 朦胧术（Blur）：二环，自身，专注至多 1 分钟，所有生物对你发动的攻击检定具有劣势；若攻击者能通过盲视或真实视觉观测你，则其免疫于此法术的效应。
- 识破隐形（See Invisibility）：二环，自身，1 小时，你能看见任何处于隐形状态的生物和物件，如同其处于正常可见状态一样。
- 优势/劣势通用规则：若有不同因素导致一次掷骰同时具有优势和劣势，该掷骰既无优势也无劣势，只掷一个 d20——**即使劣势因素有多个而优势因素只有一个，也是如此**。

出处（引用而非记忆，执行侧可自行核对）：

- `.rulebooks/dnd5r/玩家手册2024/法术详述/2环.htm` L226-227（隐形术）、L110-111（朦胧术）、L278-279（识破隐形）；
- `.rulebooks/dnd5r/玩家手册2024/法术详述/4环.htm` L180-181（高等隐形术）；
- `.rulebooks/dnd5r/玩家手册2024/术语汇编/状态.htm` L82-85（隐形状态）、L75-79（失能，本切片不实现）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/优势_劣势.htm` L9-11（优劣不叠加、冲突相消）。

### 1.1 必须锁定的规则边界

1. **优劣势冲突相消是 RAW，保持现状**：`mode_from_sources` 的"来源模式不一致则返回空串"正是 2024 规则书原文（优势_劣势.htm L11"即使多个因素导致了劣势，而只有一种因素给予了优势，也是如此：你既没有优势，也没有劣势"）。主计划阶段一总纲曾写"升级为优劣势各自计数"——**该升级与规则书原文冲突，不执行**。本切片不得改变相消语义，只把来源清单数据化。
2. 隐形的攻防两个方向是**同一个 condition 的两条声明**：目标侧（攻击隐形者 → 劣势）与攻击者侧（隐形者攻击 → 优势）。攻击者本人持有时，两条分别对"以他为目标的攻击"和"他做出的攻击"生效。
3. 感官抵消按"感知方"判断：攻击隐形目标时看**攻击者**是否持有所列感官 condition；隐形者攻击时看**目标**是否持有。抵消只豁免对应方向，不是整个 condition 失效。
4. 提前终止的是**整道法术**（所有目标），不只是行动的那个目标：实现上发 `dnd2024.concentration.ended`（owner=施法者），由既有 reducer 级联移除该 owner 的全部专注 condition。
5. 提前终止在**攻击检定发生时**即成立，命中与否无关；被攻击、受到伤害不触发。
6. 施展任何法术都触发提前终止，包括隐形者对自己重刷隐形术——但**新施的法术不受自己施法动作的触发影响**（旧法术终止，新法术完整生效，见 §3.4 C 的快照语义）。
7. 朦胧术的抵消清单是盲视/真实视觉，隐形的是识破隐形/真实视觉——两份数据各自独立，不得共用一个"能看见"清单。
8. `next_attack_advantage` / `next_attack_disadvantage` 的"攻击后消耗"语义保持：无论命中与否，该次攻击检定结算后即移除（现状行为，迁移到数据声明后不得改变）。

### 1.2 本切片的产品策略与裁剪

- 隐形状态的另两条效应明确**不做**，并记录原因：
  - 出其不意（先攻优势）：隐形术在战斗中施放，先攻已掷过；先攻重掷/惊喜轮是独立大件，归后续切片；
  - 隐蔽（Concealed，"需要看见目标的效应不影响你"）：需要逐法术审计"是否需要看见目标"（339 条），且现有 31 条 spell_effects 本就以单目标近似了几何目标——先做成体系的瞄准门控会连带改变既有法术行为。显式推迟，本切片不实现"对隐形目标的法术瞄准限制"。spell_attack 类法术攻击自然通过注册表拿到劣势。
- 隐形术只能以盟友为目标（沿用 buff 模式既有的敌我校验）；"触碰不自愿敌人"需要触碰攻击规则，不做。
- 识破隐形的以太视觉是叙事内容，不做；只实现"抵消隐形增益"的机械面。
- 盲视（blindsight）当前无任何引擎来源，只作为 `negated_by` 数据中的 condition 代码存在（GM/测试可注入），不实现感官系统。
- 本切片不做 mirror_image / mislead / seeming / sanctuary（同属"感官/自保"家族但机制不同：镜像重定向、攻击否决豁免等），留待后续。
- GM 的 `gm_grant` 攻击模式授予保持代码路径不变，不迁入注册表（它不是 condition）。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `attack_sources(actor, target, range_disadvantage)` 硬编码 5 项：`next_attack_advantage`/`faerie_fire`（目标侧优势）、`long_range`（射程劣势，位置性）、`dodging`（目标侧劣势）、`next_attack_disadvantage`（攻击者侧劣势）；返回 `{"code", "mode"}` 列表 | `src/rulesets/dnd2024/combat/check_modes.py` L18-29 |
| `mode_from_sources` 用集合判一致，不一致返回 `""`（RAW 相消，保持） | `check_modes.py` L32-34 |
| `attack_mode(engine, instance, actor, target, range_disadvantage, use_grant)` 聚合 sources + 玩家 `gm_grant`；被武器攻击与 spell_attack 两处调用 | `check_modes.py` L37-44；`resolution.py` L158-160、L272-274 |
| `policy(...)` 是掷前投影端点，也走 `attack_mode` | `check_modes.py` L47-92 |
| `Engine._attack_modes` 是**无调用方的死代码**（src 与 tests 均无引用） | `engine.py` L1041-1047 |
| `_consume_attack_conditions(actor_id, target_id, actor, target)` 硬编码移除攻击者的 `next_attack_disadvantage` 与目标的 `next_attack_advantage`；武器攻击与 spell_attack 都调用 | `engine.py` L1049-1064（staticmethod）；`resolution.py` L231、L307 |
| 战斗目录 dataclass 与加载校验：`spell_effects` 字段、`SPELL_MODES` 白名单、逐字段 fail closed | `src/rulesets/dnd2024/combat/catalog.py` L16-19、L26-44、L58-93 |
| `srd_combat_core.json` 顶层 `weapons` / `unarmed_strike` / `labels` / `spell_effects` 四节；faerie_fire 条目为 `{"mode": "saving_throw", ..., "condition": "faerie_fire", "condition_duration": "concentration", "concentration": true}` | `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` L28-72（faerie_fire L57） |
| 施法主循环：cost → 旧专注终止/新专注开始 → `spell.cast` → 逐目标结算 → buff/debuff 应用 condition（payload 含 `duration`/`source_actor_id`/`concentration_owner`）→ 胜利判定 | `resolution.py` L246-265（cost/专注）、L267（目标循环）、L391-405（buff/debuff 与 condition.applied）、L412-418 |
| `condition.applied` 的全部事件键（除 type/target_id/condition）都存进 condition 元数据 | `reducer.py` L208-220 |
| `dnd2024.concentration.ended` 清空施法者专注并级联移除全部 `concentration_owner == owner` 的 condition | `reducer.py` L268-276；`engine.py` `_remove_concentration_conditions` L1122-1141 |
| 新专注开始前自动终止旧专注（`new_concentration_spell`） | `resolution.py` L252-257 |
| 施法校验：`target_ids` 列表化、`target_count` 上限、去重要求、buff 需盟友目标、射程校验 | `validation.py` L435-442（目标数）、L449-455（敌我）、L463-464（射程） |
| 事件在**预结算视图**上生成、批次结束后才由 reducer 应用：生成期读取的 condition 视图是本意图生效前的状态 | `engine.py` `apply_batch` L775-789 及 resolution 全部 `_actor_view` 用法 |
| 敌人档案由 GM 在 combat.start 提供，`_actor_view` 直接读 `enemy.get("conditions")`——敌方持 condition 是引擎支持的既有路径（测试注入点） | `view.py` L68-86；`tests/rulesets/dnd2024_monk_common.py` `goblin()` L145-155 |
| 法术元数据已存在（含 invisibility L2220-2240、greater_invisibility、see_invisibility、blur），无需新增 | `templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json` |
| 测试基建：`SequenceRng` / `monk_draft` / `monk_instance` / `start_combat`；施法职业卡构造先例（paladin_sheet：monk_draft + `class_ref` + `class_spell_choices` 覆盖 + `equipment_package`） | `tests/rulesets/dnd2024_monk_common.py`；`tests/rulesets/test_dnd2024_smite.py` L15-56；法师装备包 `equipment_package:wizard_a`（`content/classes/wizard.json` L40） |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

新增顶层 `"conditions"` 节（放在 `spell_effects` 之后）：

```json
"conditions": {
  "next_attack_advantage": {"attack_modes": [{"side": "target", "mode": "advantage", "consume_on": "attack_resolved"}]},
  "next_attack_disadvantage": {"attack_modes": [{"side": "actor", "mode": "disadvantage", "consume_on": "attack_resolved"}]},
  "faerie_fire": {"attack_modes": [{"side": "target", "mode": "advantage"}]},
  "dodging": {"attack_modes": [{"side": "target", "mode": "disadvantage"}]},
  "blurred": {"attack_modes": [{"side": "target", "mode": "disadvantage", "negated_by": ["blindsight", "truesight"]}]},
  "invisible": {"attack_modes": [
    {"side": "target", "mode": "disadvantage", "negated_by": ["see_invisibility", "truesight"]},
    {"side": "actor", "mode": "advantage", "negated_by": ["see_invisibility", "truesight"]}
  ]}
}
```

`spell_effects` 新增四条：

```json
"invisibility": {"mode": "buff", "range": 5, "target_count": 1, "upcast_target_count": 1, "condition": "invisible", "condition_duration": "concentration", "concentration": true, "ends_on": ["attack_roll", "damage_dealt", "spell_cast"]},
"greater_invisibility": {"mode": "buff", "range": 5, "target_count": 1, "condition": "invisible", "condition_duration": "concentration", "concentration": true},
"blur": {"mode": "buff", "range": 5, "target_count": 1, "condition": "blurred", "condition_duration": "concentration", "concentration": true},
"see_invisibility": {"mode": "buff", "range": 5, "target_count": 1, "condition": "see_invisibility", "condition_duration": "1_hour"}
```

语义约定（引擎与数据共同遵守）：

- `side: "target"` = condition 挂在受击者身上时生效；`side: "actor"` = 挂在攻击者身上时生效；
- `negated_by` = 对侧（感知方）持有其中任一 condition 时，该条声明不参与本次聚合；
- `consume_on: "attack_resolved"` = 一次攻击检定结算后移除该 condition（替换 `_consume_attack_conditions` 的硬编码）；
- `ends_on` 只属于法术效果数据，会被拷贝进 condition 元数据（见 §3.4）。

### 3.2 `src/rulesets/dnd2024/combat/catalog.py`

1. dataclass 增加字段 `conditions: dict[str, dict[str, Any]]`（带默认值放 `unarmed_strike` 之后，保持既有构造调用兼容）；
2. `from_bundle` 读取 `raw.get("conditions")`：缺失按空 dict（向后兼容）；存在则逐条校验，任何违规抛 `CombatCatalogError`：
   - 每个条目必须是 dict；未知顶层键（除 `attack_modes`）拒绝；
   - `attack_modes` 必须是列表；每项必须含 `side ∈ {"actor", "target"}`、`mode ∈ {"advantage", "disadvantage"}`；
   - `negated_by` 若存在必须是字符串列表；`consume_on` 若存在必须等于 `"attack_resolved"`；除这四个键外的项键拒绝；
3. `spell_effects` 校验追加两条：`upcast_target_count` 若存在必须是 `0 < int <= 10`；`ends_on` 若存在必须是列表且取值 ⊆ `{"attack_roll", "damage_dealt", "spell_cast"}`；
4. 返回时 `conditions=deepcopy(...)`。

### 3.3 `src/rulesets/dnd2024/combat/check_modes.py`

1. `attack_sources` 签名改为 `attack_sources(registry, actor, target, range_disadvantage)`：
   - `registry` 是目录 `conditions` 数据（由调用方传入）；
   - 遍历 registry 的每个 `(condition_id, entry)` 与其 `attack_modes`：`side == "target"` 时查 `condition_id in target["conditions"]`，`side == "actor"` 时查攻击者；命中且未被 `negated_by` 抵消（对侧 conditions 的包含检查）则追加 `{"code": condition_id, "mode": mode}`；
   - `long_range` 位置性劣势保持代码生成，不进注册表；
   - 遍历顺序 = JSON 声明顺序（deterministic，仅影响展示）；
2. `attack_mode` 把 `engine.catalog.conditions` 传给 `attack_sources`（`gm_grant` 逻辑不动）；
3. `policy` 无需改（经 `attack_mode` 间接受益）；
4. `mode_from_sources` **不动**。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py` 与 `engine.py`

#### A. 删除死代码

`engine.py` 删除 `_attack_modes`（L1041-1047，无任何调用方）。

#### B. `_consume_attack_conditions` 数据驱动（engine.py）

从 staticmethod 改为读 `self.catalog.conditions` 的实例方法（或加 registry 参数）：扫描 registry 中带 `consume_on == "attack_resolved"` 的条目，`side == "actor"` 查攻击者、`side == "target"` 查目标，命中即发 `{"type": "condition.removed", ...}`。行为必须与现状等价（现有两个 next_attack_* 的移除路径不变），调用点 `resolution.py` L231、L307 不动。

#### C. 提前终止触发（resolution.py 新增辅助 + 四个调用点）

新增辅助（建议名 `_spell_end_trigger_events(actor, trigger)`）：

- 输入：预结算视图 `actor`、触发码 `trigger ∈ {"attack_roll", "damage_dealt", "spell_cast"}`；
- 扫描 `actor["conditions"]`：元数据是 dict 且 `trigger in (meta.get("ends_on") or [])` 的条件；
- 对每个命中条件：有 `concentration_owner` → 发 `{"type": "dnd2024.concentration.ended", "actor_id": owner, "reason": "spell_ended_early"}`（同一 owner 去重只发一次）；无 owner → 发 `condition.removed`；
- 不消耗 RNG。

调用点（全部基于**预结算视图**，新法术应用的条件天然不在视图里，"重刷隐形术"因此正确）：

1. `_attack_events`：对**攻击者**以 `"attack_roll"` 触发；生成的事件插在返回列表**头部**（`_attack_cost_event` 之前）。借机攻击（`spend_attack_action=False`）同样触发。
2. `_spell_events`：对施法者以 `"spell_cast"` 触发；插在事件列表**头部**（cost 事件之前）。与 L252-257 的 `new_concentration_spell` 终止存在重叠时（施法者正是旧专注的 owner），**去重**：本批已发过该 owner 的 `concentration.ended` 则跳过 L253 的重复事件。
3. `_finish_hit_reaction` 的 `cast` 分支（reaction_spells.py）：对施放 Shield 的防御者以 `"spell_cast"` 触发，事件插在 `decision.cleared` 之后、其余事件之前。
4. `_triggered_spell_events`（Divine Smite）：对施放者以 `"spell_cast"` 触发，同样插在 `decision.cleared` 之后。

`"damage_dealt"` 触发码在当前引擎中被攻击/施法路径完全包含（所有伤害都源于这两类意图），保留为数据位与辅助参数，不单设调用点——在代码注释中写明这一点。

#### D. buff 分支携带 `ends_on`（resolution.py L399-405）

`condition.applied` payload 追加 `"ends_on": deepcopy(effect.get("ends_on"))`（仅当效果声明了该字段）。

#### E. 升环目标数（validation.py L440）

`required_count = target_count + upcast_target_count * max(0, slot_level - spell["level"])`。未声明 `upcast_target_count` 的法术行为不变（bless 仍是 3 目标封顶）。`_available_spells` / `_spell_statuses` 投影无需变更。

### 3.5 测试（新文件 + 既有回归）

新文件 `tests/rulesets/test_dnd2024_invisibility.py`（模式照抄 `test_dnd2024_smite.py` 的 sheet/instance/intent 构造，职业换 `class:wizard` + `equipment_package:wizard_a`，推进到 3 级拿二环位；多角色场景在 `instance.players` 里放第二个玩家）。测试矩阵见 §4。

回归：`tests/rulesets/test_dnd2024_combat.py`、`test_dnd2024_spells.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monk_combat.py` 全部通过（`test_dnd2024_character_builder.py` 的 11 个上游豁免失败不计）。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：conditions 节缺失 → 空 registry；恶意条目（坏 side/mode、未知键、坏 ends_on） | `CombatCatalogError` |
| 2 | 法师对盟友施放隐形术（2 环位） | 事件序：concentration.started → spell.cast → condition.applied（`invisible`，含 `concentration_owner` 与 `ends_on` 三项） |
| 3 | 敌人攻击隐形盟友（GM 提交 enemy attack intent，SequenceRng 两骰 15/8） | check_event `advantage_mode == "disadvantage"`、sources 含 `{"code": "invisible", ...}`、`natural == 8`（取低） |
| 4 | 隐形盟友攻击敌人（两骰 8/15） | `advantage_mode == "advantage"`、`natural == 15`（取高）；同一事件批头部出现 `dnd2024.concentration.ended`（owner=施法者）；批后 `invisible` condition 已从盟友与全部目标移除；**未命中同样触发**（低骰故意 miss 的用例） |
| 5 | 隐形者施放任意法术（如 fire bolt） | 批头部 `concentration.ended`；法术本身正常结算 |
| 6 | 隐形者对自己重刷隐形术 | 旧法术终止一次（不重复发 `new_concentration_spell` 的第二份 ended）；新 `invisible` condition 存活且带 `ends_on` |
| 7 | 升环：3 环位施放隐形术可指定 2 个目标；2 环位指定 2 个目标被 validation 拒绝 | intent ok / `CombatIntentError` |
| 8 | 高等隐形术：目标攻击后 `invisible` **不**移除（无 `ends_on`） | condition 仍在 |
| 9 | 感官抵消（目标侧）：敌人档案带 `{"conditions": {"see_invisibility": {...}}}` 注入 combat.start，该敌人攻击隐形盟友（15/8） | `advantage_mode == ""`、单骰 `rolls == [15]` |
| 10 | 感官抵消（攻击者侧）：持 `see_invisibility` 的玩家攻击带 `invisible` condition 的敌人（同法注入） | `advantage_mode == ""` |
| 11 | 朦胧术：自身施放 → 敌人攻击有劣势；带 `truesight` 的敌人攻击无劣势 | 同 #3/#9 模式 |
| 12 | 注册表回归：guiding bolt 命中后目标的 `next_attack_advantage` 仍会在下一次攻击（任意结果）后移除；faerie_fire/dodging 优劣势不变 | 既有行为断言 |
| 13 | RAW 相消单测：sources 同含优势与劣势 → `mode_from_sources == ""`（引用优势_劣势.htm L11） | 直接构造 sources 列表断言 |
| 14 | 双持场景：隐形盟友（目标侧劣势）同时被 `faerie_fire`（目标侧优势）标记，敌人攻击 | `mode == ""`（相消）且 sources 两条都在 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_invisibility.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_action_adapter.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

## 6. 禁止事项

1. 禁止改变 `mode_from_sources` 的相消语义（含"各自计数"）。
2. 禁止实现隐形状态的 Concealed 法术瞄准门控与先攻优势（§1.2 已裁剪）。
3. 禁止把 `gm_grant` / `long_range` 迁入注册表或改变其行为。
4. 禁止在 `check_modes.py`、`resolution.py`、`reaction_spells.py` 中新增具体法术名分支——新法术只允许目录数据 + 既有模式。
5. 禁止给 condition 元数据新增客户端可写的键：`ends_on` 只能由服务端从目录效果拷贝。
6. 禁止顺手重构 `_expire_conditions`、Rage 生命周期或 sneak attack 已有结构。
7. 禁止用 `ranged_use` 等无关字段冒充感官判断；感官只认 `negated_by` 声明的 condition 代码。

## 7. 停止条件

遇到以下情况立即停止并报告，不要自行发明语义：

1. `attack_sources` 存在本任务书未列出的调用方（src/tests 全量 grep 后仍新增）；
2. 目录 `conditions` 节与既有 `condition.applied` 元数据结构冲突（如已有同名机制）；
3. 预结算视图假设不成立（例如发现某路径在生成期内二次读取已应用状态）；
4. 测试 #6（重刷隐形术）无法同时满足"旧终止、新存活"与事件序约束；
5. 任何白名单外文件必须修改才能通过验收。

## 8. 执行报告模板

执行完成后在 `docs/internal/design/P1_INVISIBILITY_TASK_SPEC_2026-10-29.md` 追加"执行报告"节：变更文件清单、每个测试矩阵项的通过情况、§3 语义与实现的偏差（若有）、遗留问题。同规则：除本任务书文件的执行报告追加外，不产生其他文档。

## 9. 修订记录

- v1（2026-10-29）：初版。

---

## 执行报告（2026-10-29）

### 变更文件清单

白名单内：

1. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：新增 `conditions` 注册表与 `invisibility` / `greater_invisibility` / `blur` / `see_invisibility` 四条 spell_effects。
2. `src/rulesets/dnd2024/combat/catalog.py`：dataclass 增 `conditions` 字段；`from_bundle` 校验 `upcast_target_count`、`ends_on` 与 `_validated_conditions`。
3. `src/rulesets/dnd2024/combat/check_modes.py`：`attack_sources` 改为接收注册表并支持 `side` / `negated_by`；`long_range` 仍由代码生成；`mode_from_sources` 未动。
4. `src/rulesets/dnd2024/combat/engine.py`：删除 `_attack_modes` 死代码；`_consume_attack_conditions` 改为读取目录注册表的实例方法。
5. `src/rulesets/dnd2024/combat/resolution.py`：新增 `_spell_end_trigger_events`；`_attack_events` / `_spell_events` 头部接入；施法与攻击结算确认去重；buff/debuff 的 `condition.applied` 携带 `ends_on`。
6. `src/rulesets/dnd2024/combat/reaction_spells.py`：`cast` 分支在 `decision.cleared` 之后接入 `spell_cast` 提前终止。
7. `src/rulesets/dnd2024/combat/triggered_spells.py`：`_finish_hit_spell` 在 `decision.cleared` 之后、伤害之前接入 `spell_cast` 提前终止。
8. `src/rulesets/dnd2024/combat/validation.py`：`required_count` 计入 `upcast_target_count * max(0, slot_level - spell level)`。
9. 新建 `tests/rulesets/test_dnd2024_invisibility.py`（16 个用例，覆盖 §4 全部 14 项矩阵）。

报告追加：本文件。

白名单外（`git status` 可见，但与本次执行无关，未触碰）：

- `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` 的既有本地修改；
- `docs/internal/design/P2_*` ~ `P9_*` 既有未跟踪任务书。

### 测试矩阵通过情况

| # | 用例 | 结果 |
|---|---|---|
| 1 | `test_catalog_accepts_missing_conditions_and_rejects_malicious_entries` | 通过 |
| 2 | `test_invisibility_applies_invisible_condition_with_early_end_metadata` | 通过 |
| 3 | `test_enemy_attack_against_invisible_target_has_disadvantage_and_keeps_low_die` | 通过 |
| 4 | `test_invisible_attacker_has_advantage_and_the_whole_spell_ends`（命中/未命中共 2 参数） | 通过 |
| 5 | `test_invisible_caster_casting_another_spell_ends_the_spell_first` | 通过 |
| 6 | `test_recasting_invisibility_ends_the_old_spell_once_and_keeps_the_new_one` | 通过 |
| 7 | `test_third_level_slot_upcasts_invisibility_to_two_targets` / `test_second_level_slot_rejects_two_invisibility_targets` | 通过 |
| 8 | `test_greater_invisibility_survives_the_target_attacking` | 通过 |
| 9 | `test_see_invisibility_negates_the_target_side_disadvantage` | 通过 |
| 10 | `test_see_invisibility_negates_the_attacker_side_advantage` | 通过 |
| 11 | `test_blur_imposes_disadvantage_unless_the_attacker_has_truesight` | 通过 |
| 12 | `test_consumed_conditions_still_follow_one_attack_roll` | 通过 |
| 13 | `test_advantage_and_disadvantage_cancel_even_when_counts_differ` | 通过 |
| 14 | `test_invisible_and_faerie_fire_cancel_out_for_a_two_source_attack` | 通过 |

### 验收结果

- `pytest tests/rulesets/test_dnd2024_invisibility.py -q`：16 passed。
- `pytest tests/rulesets/test_dnd2024_combat.py test_dnd2024_spells.py test_dnd2024_smite.py test_dnd2024_uncanny_dodge.py test_dnd2024_monk_combat.py test_dnd2024_action_adapter.py -q`：91 passed。
- `pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py`：495 passed，39 deselected。

### 与 §3 语义的偏差（均为测试写法，非行为偏差）

1. §3.5 / #12：任务书写“guiding bolt 命中后”，但 guiding_bolt 仅牧师可得；为遵守“法师职业 + 既有基建”的白名单约定，改为直接注入 `next_attack_advantage` / `dodging` / `next_attack_disadvantage`，断言完全对应既有消费与优劣势行为。
2. #1 的目录校验用轻量 bundle 包装器改写 `combat_catalog`，不新造完整内容包；照样覆盖 section 缺失、坏 side/mode、未知键、坏 `negated_by`/`consume_on`、坏 `ends_on`/`upcast_target_count`。
3. #4 的事件序断言以“`dnd2024.concentration.ended` 早于 `attack.spent` 与 `check.resolved`”表达“列表头部”，因为批次首事件固定是引擎的 `intent.submitted`。
4. #8 用“向法师 prepared 列表追加 `greater_invisibility` + 注入 4 环位”取得该法术；这是既有测试常用的 canonical 注入方式，不改任何生产校验。
5. `triggered_spells._finish_hit_spell` 在 slice 5 之后已经使用 `_submit_hit_damage`（保留 `feature_use` 契约），提前终止仍按任务书插在 `decision.cleared` 之后、伤害落地之前。

### 遗留问题

- `damage_dealt` 只是数据位与辅助参数，当前无独立调用点（所有伤害都来自攻击检定或施法，已先触发），与 §3.4 C 一致。
- 法师 3 级只有 1/2 环位；`greater_invisibility` 的 4 环位与 `invisibility` 的 3 环升环用例依赖测试注入（见上）。
- 隐形状态的 Concealed 瞄准门控、先攻优势按 §1.2 继续推迟。
