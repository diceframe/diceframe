# S-P3 任务书：d20 检定反应中断（掷出后、结算前）

| 项 | 值 |
|---|---|
| 切片编号 | S-P3 |
| 主计划出处 | `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` L136（原语行）、L172（Lucky 消费者行） |
| 原语 | d20 检定反应中断：掷出后、结算前；扩展切片 3 反应注册表到检定窗口，区别于 P7 施法反应 |
| 代表特性 / 验证载体 | Cutting Words（语出惊人，端到端代表）；Dark One's Own Luck 与 Lucky 作为窗口另两个消费者同批接线 |
| 编写日期 | 2026-10-30 |
| 代码基线 | commit `4ef1d33d`（HEAD）。工作区存在 P7/P8 在途未提交改动，行号映射见 §2.2 |
| 依赖契约 | SP1（特性声明/所有权模式、资源池交付模式）；SP2 §3.8（骰迹与 RNG 顺序契约）；SP10 §3（统一攻击加值与骰迹管线，字段演进登记见 §8） |
| 状态 | 待执行 |

---

## §1 规则语义与原文出处（2024 版，显式裁剪）

### 1.1 Cutting Words（语出惊人）——本切片端到端代表

原文：`.rulebooks/dnd5r/玩家手册2024/角色职业/吟游诗人/逸闻学院.htm` L18-19：

> 当一名你可见的位于你60尺内的生物进行伤害掷骰、成功于一次属性检定或攻击检定时，你能够以反应消耗一次诗人激励使用次数，并掷诗人激励骰，然后从该生物的掷骰结果中减去诗人骰的骰值，这将降低其造成的伤害或可能使这次检定的成功变为失败。

机械拆解：

- 触发三面：**伤害掷骰面**（进行伤害掷骰时）、**属性检定面**（成功时）、**攻击检定面**（成功时）。
- 成本：一次反应 + 一次诗人激励使用次数（池消耗）。
- 效果：掷诗人骰，从该生物的掷骰结果中减去骰值；对检定面可能把成功变为失败；对伤害面降低伤害。
- 前提：该生物对诗人可见、位于诗人 60 尺内（theater-of-mind 距离原语）。
- 诗人骰面：`bardic_die` 轨道（`.rulebooks/dnd5r/玩家手册2024/角色职业/吟游诗人/吟游诗人.htm` L75：5 级 d8、10 级 d10、15 级 d12；1 级 d6，L48/L70）。
- 授予等级：3 级（逸闻学院，同文件 L16-19 上下文；`srd_subclasses.json` college_of_lore `"3": [bonus_proficiencies, cutting_words]`）。
- 反应预算：诗人一回合只有一个反应；一次检定/一次攻击最多花一面。

### 1.2 Dark One's Own Luck（黑暗强运）

原文：`.rulebooks/dnd5r/玩家手册2024/角色职业/魔契师/邪魔宗主.htm` L36-37：

> 当你进行属性检定或豁免检定时，你可以使用该特性为此次掷骰增添一个d10骰。你可在看到掷骰结果后，其结果生效前使用该特性。
> 你可以使用此特性的次数等于你的魅力调整值（至少一次），但你在一次检定中只能使用此特性一次。你在完成一次长休后恢复所有已消耗的次数。

机械拆解：

- 范围：**属性检定或豁免检定**（原文明确，不含攻击检定）。
- 窗口语义：看到掷骰结果后、结果生效前（掷后窗口的标准措辞）。
- 效果：+1d10 到该次掷骰结果；一次检定限一次。
- 池：魅力调整值（至少 1），长休恢复全部，短休不恢复。
- 授予等级：6 级（邪魔宗主，同文件 L36 上下文；`srd_subclasses.json` fiend_patron `"6": [dark_ones_own_luck]`）。

### 1.3 Lucky（幸运，起源专长）

原文：`.rulebooks/dnd5r/玩家手册2024/专长/起源专长.htm` L56-63：

> **幸运点。** 你拥有一些幸运点，其数量等同于你的熟练加值。……你在完成一次长休时重新获得所有已消耗的幸运点。
> **优势。** 当你为了一次 D20 检定而投掷 d20 时，你可以花费一个幸运点来给予这次检定优势。
> **劣势。** 当一个生物为了一次对你发动的攻击检定而投掷 d20 时，你可以花费一个幸运点来为这次检定施加劣势。

机械拆解：

- 池：熟练加值点，长休恢复全部。
- 优势面（自身）：自己进行 D20 检定掷 d20 时花 1 点给予该检定优势。2024 的 "D20 检定" 含攻击检定、属性检定、豁免检定。实现为**掷骰前声明**（优势=掷两枚取高，必须掷前生效）。
- 劣势面（敌方）：敌对己攻击检定掷 d20 时花 1 点施加劣势。主计划 L172 裁决为**掷后窗口**（"敌攻击掷骰后施加劣势"）。施加劣势的 RAW 机械（PHB2024 D20 检定规则）：额外掷一枚 d20 取低。
- 该专长是起源专长（background/species 赠送），不是通用专长；canonical id `feat:lucky`（`features.feat_refs`，见 §2.1 derivation 锚点）。

### 1.4 Bardic Inspiration 池（既有，本切片仅接线消耗）

原文：`.rulebooks/dnd5r/玩家手册2024/角色职业/吟游诗人/吟游诗人.htm` L70-76：

- L72：附赠动作激励 60 尺内可见生物；一个生物同一时间只能拥有一枚激励骰；**授予面**（把骰子给队友、队友 D20 检定失败时附加）是另一个原语，不在本切片。
- L73-74：使用次数 = 魅力调整值（最少 1），长休恢复全部；5 级起（激励之源）短休恢复全部。

池声明已在 `srd_recovery.json` L34（`maximum_ability_modifier: "cha"`、`minimum: 1`、`short_all_from_level: 5`、`long: "all"`，2024 语义）。**本切片对诗人激励池零 schema 改动**，只接战斗侧消耗（Cutting Words 花池）。

### 1.5 掷骰先行窗口的一般架构语义（与切片 3、SP2、P7 的关系）

- 切片 3（1A）已确立"掷骰先行 → `dnd2024.decision.pending` → `decision.resolve` → finisher"的窗口形状（HIT_SPELL / HIT_REACTION：攻击 total 与 damage_event 进决策 payload，掷骰先于决策）。S-P3 把该形状扩展到**检定窗口**：d20 已掷、结果可预判、但伤害尚未掷、不可逆事件尚未落地。
- 与 P7 施法反应（reaction_defense 法术）的区别：P7 消费"被命中"这一已结算事实（AC 改判）；S-P3 消费"检定已掷、结算未落"的更早窗口。两者共用 pending/resolve 机制，但不共用触发时机与决策种类。
- RNG 顺序契约（对齐 SP2 §3.8）：直通路径（无人花点）RNG 序列 = d20 first/second → bless d4 → 伤害骰，与 HEAD 完全一致；花点路径的窗口骰（Lucky 劣势面第二枚 d20、CW 诗人骰、DOOL d10）插在 bless 之后、伤害骰之前（SP2 §3.8 的"第 3 位"）；CW 伤害面骰在其所修改的伤害掷骰**之后**（掷后减除语义）。防御者 Shield/UD（既有 HIT_REACTION）的 RNG 序列不变。
- 2024 通用掷后语义（"看到掷骰结果后、生效前"）：决策 payload 携带已掷骰面与预判结果（命中/伤害概要），窗口主人据此做完全信息决策；AI 侧用同一 payload 跑确定性阶梯。

### 1.6 显式裁剪清单（本切片不做，全部登记 §8 迁移债）

1. CW **法术伤害面**：法术伤害的批暂停机械；本切片 CW 伤害面仅接武器攻击直通路径。
2. CW **敌方属性检定面**：P10 之前无敌方属性检定触发源。
3. DOOL **save_repeat tick 面**与**以玩家为目标的法术豁免面**：批暂停机械；且当前 enemy 只有 attacks 档案、不施法，"敌方法术豁免"触发源不存在。
4. DOOL 窗口本切片只接两处：攻击直通路径尾段的专注豁免、死亡豁免。其余 `_concentration_events` 调用点（共 4 处 resolution + 2 处 reaction_spells）不动。
5. Lucky 优势面声明通道只开三个意图：`attack` / `death_save` / `cast_spell`（法术攻击检定）。专注豁免、save_repeat 无声明通道。
6. Bardic Inspiration 授予面（给队友骰子）是另一原语，不在本切片。
7. `_reaction_feature_choices` 中 uncanny_dodge 的注册表化泛化归 S-P9 评估，本切片不重构（见 §3.6）。
8. Lucky 劣势面只接"敌对己攻击检定"一个触发源；敌方对召唤物/其它目标不触发（防守者定义 = 攻击目标本身）。

---

## §2 代码事实锚点表

### §2.1 锚点（基线 4ef1d33d）

| 文件 | 行号（HEAD） | 事实 |
|---|---|---|
| `src/rulesets/dnd2024/combat/resolution.py` | L165-355 | `_attack_events` 全体 |
| 同上 | L249-262 | 掷骰区：L249-251 `attack_mode(...)`；L253 `first`；L254 `second`（**仅 advantage/disadvantage 时掷，平攻为 None**）；L255 `natural`；L256-258 bless d4；L259 `total`；L260 `target_ac`；L261 `critical`；L262 `hit` 预判 |
| 同上 | L270-283 | 事件头（spell_end_trigger + attack cost）与 `check.resolved`（rolls 含 bless_bonus 字段） |
| 同上 | L294-322 | 命中结算体：hex/sneak extra_dice、attack_damage_node、rage_damage、**L322 `roll_damage_node`（伤害骰掷点）** |
| 同上 | L323-341 | damage_event 构造 + 抗性 + sneak feature_use 延迟记账 |
| 同上 | L342-348 | `_hit_spell_offer` → `_hit_reaction_offer(next_decision=offer)` → pending 或直通 |
| 同上 | L350-353 | 直通尾段：`_submit_hit_damage` → `_concentration_events` → victory |
| 同上 | L354-355 | `_consume_attack_conditions` + return |
| 同上 | L1109-1140 | `_death_save_events`：掷骰后 20→hp1、1→failures+2、≥10→successes+1、3 稳定/3 死亡；check.resolved(kind="death_save") + death_save.resolved + end_turn |
| 同上 | L1167-1177 | `_decision_events`：按 kind 分发（HIT_SPELL / HIT_REACTION / else 移动决策） |
| 同上 | L953 | `_save_repeat_events`（DOOL 债位，本切片不动） |
| 同上 | L887-961 | `_class_capability_events`；**L913-917 `dnd2024.class_resource.spent` 事件先例** |
| 同上 | L741-758 | `_submit_hit_damage`（静态；feature_use 弹出转 `dnd2024.feature.used`） |
| `src/rulesets/dnd2024/combat/reaction_spells.py` | 全文 | **未修改（=HEAD）**。L22 `HIT_REACTION_DECISION`；L39-68 `_reaction_spell_choices`；L70-114 `_reaction_feature_choices`（uncanny_dodge 单条硬编码）；L116-146 `_hit_reaction_offer`（决策 payload 形状先例：decision_id/kind/actor_id/attacker_id/assigned_to/options/attack_total/armor_class/critical/damage_event/spells/features/next_decision）；L148-179 `_validate_hit_reaction`；L181-268 `_finish_hit_reaction`（feature 分支减半 + next_decision held damage 同步减半 L203-211；cast 分支 Shield 全链 L219-257）；L270-299 `_automatic_hit_reaction`（AI 阶梯先例） |
| `src/rulesets/dnd2024/combat/engine.py` | L160-335 | **未修改**。L174-183 HIT_REACTION assigned 决策 → decision.resolve；L189-192 任意 assigned pending → decision.resolve 携全部；L281-335 `next_automatic_intent`（L304-322 按 kind 分发）；L656-663 intent 分发 → `_decision_events` |
| 同上 | L1067-1092 | `_concentration_events`（@staticmethod：dc=max(10,damage//2)、con modifier、bless、check.resolved kind="concentration"、失败 concentration.ended）；调用点 resolution 4 处 + reaction_spells 2 处。**本切片不改此函数** |
| `src/rulesets/dnd2024/combat/validation.py` | L210-213 | 决策门控：HIT_SPELL pending → 只允许 cast_spell/decision.resolve/combat.message；HIT_REACTION pending → 只允许 decision.resolve/combat.message |
| 同上 | L225-249 | decision.resolve 校验：decision 存在、option ∈ options、kind ∈ {HIT_SPELL, HIT_REACTION} → expected_owner + `_validate_hit_reaction`；resolve 选项需 threat 反应预算 |
| 同上 | L386-405 | `_validate_attack`：目标敌我/hp/武器档案/射程（Lucky 优势面参数校验落点） |
| 同上 | L407-426 | `_validate_spell`（cast_spell 意图校验；resist_type 先例在 HEAD L496-499） |
| `src/rulesets/dnd2024/combat/check_modes.py` | L68-96 | `attack_sources`（注册表声明 sources，actor/target 侧，negated_by 按 observer） |
| 同上 | L99-111 | `mode_from_sources`（混合 → "" 平攻）与 `attack_mode`（sources + gm_grant scoped） |
| `src/rulesets/dnd2024/features/resolver.py` | L476-495 | `_value`：直接参数优先 → `<name>_track` 经 `_track_value` → `<name>_format` 模板格式化 |
| 同上 | L508-525 | `_track_value`：从 (class_id, level) 调 progression.snapshot 重算轨道值（**resolver 持有进度目录，战斗边界无需带 progression**） |
| 同上 | L558-564 | `_validate_declarations`：`*_track` 必须真轨道，fail closed |
| `src/rulesets/dnd2024/features/resources.py` | L40-160 | ClassResourceDeclaration 校验（恰一个 maximum 字段、source_ref `srd-5.2.1:`、minimum_level 1-20）；L133-143 `resource_state(character, resource_id)` 读 `resources.class.<id>` |
| `src/rulesets/dnd2024/resting/engine.py` | L41-45 | class_resources 桶键与 progression.classes **精确全等**校验（feat 池不得塞进此桶） |
| 同上 | L96-124 | `sync_resources`：写 `character["progression"]` + `resources.class.<id>`，resize 策略默认 preserve_spent |
| 同上 | L231-241 | `_maximum` 三形态：maximum_track / maximum_fixed / maximum_ability_modifier+minimum |
| 同上 | L243-268 | `_recover_class_resources`：short 按 `short_all_from_level` 或 `{"amount": N}`；long `"all"` |
| `src/rulesets/dnd2024/combat/view.py` | L337-362 | `_feature_character`（静态投影：build/abilities/resources.class/equipment.item_refs；**不带 feat_refs，但带池状态**——池存在即所有权的消费依据） |
| `src/rulesets/dnd2024/runtime.py` | L621-630 | `_sync_class_resources` 委托 `Dnd2024RestEngine(bundle).sync_resources(character)` |
| `src/rulesets/dnd2024/character/derivation.py` | L98-100 / L288 | `features.feat_refs` 汇聚（background + species） |
| 同上 | L162 | `"feat:alert" in feat_refs` 先攻消费先例 |
| 同上 | L294 | `derived.proficiency_bonus` |
| `src/rulesets/dnd2024/combat/reducer.py` | L185-197 | `dnd2024.class_resource.spent` → 写 `resources.class.<id>`（**新池零 reducer 改动**） |
| `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json` | L29-70 | class_resources 12 桶；L34 bardic_inspiration（已在位）；L64-66 warlock 桶仅 magical_cunning |
| `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` | L7-32 | labels 平铺 id→英文标签 |
| 同上 | L33-45 / L209-232 | monk 段先例；rogue 段：L213-221 sneak_attack（`damage_dice_track` + `damage_dice_format`）、L223-228 uncanny_dodge（`damage_multiplier: 0.5`）。**无 bard/warlock 段** |
| `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` | L63-79 | bard 段：L75 `bardic_die` 轨道 `[6,6,6,6,8,8,8,8,8,10,10,10,10,10,12,12,12,12,12,12]` |
| `templates/rulesets/dnd2024_srd/content/subclass_catalogs/srd_subclasses.json` | L14-19 | college_of_lore `"3": [bonus_proficiencies, cutting_words]`（幽灵 id 已在位） |
| 同上 | L68-73 | fiend_patron `"6": [dark_ones_own_luck]`（幽灵 id 已在位） |
| `templates/rulesets/dnd2024_srd/content/feats/alert.json` | L7 | `"initiative_proficiency": true`（feat 机制字段先例） |

### §2.2 工作区在途改动与行号偏移（P7/P8 会话，未提交）

现状（2026-10-30 核实）：M `catalog.py`（1 行）/ M `resolution.py`（+39）/ M `validation.py`（+5/-2）/ M `srd_combat_core.json`（+1）/ 未跟踪 `tests/rulesets/test_dnd2024_dispel_magic.py`。这是并行法术会话（P 系列）的半成品，**不得触碰、不得回滚、不得并入本切片提交**。

偏移映射（`git diff -U0` 测定）：

- `resolution.py`：hunks `@@ -593,0 +594,34 @@` 与 `@@ -614,0 +649,5 @@` → **HEAD ≤L593 与工作区一致（零偏移）**；HEAD L594-614 = 工作区 L628-648；**HEAD ≥L615 = 工作区行号 -39**。§2.1 中 `_attack_events`（≤L593）读工作区即 HEAD；`_decision_events`/`_death_save_events` 等高区段按 -39 折算。
- `validation.py`：hunks `@@ -471 @@`、`@@ -475,0 +476,3 @@`、`@@ -481 +484 @@` → **HEAD ≤L470 零偏移**；其后 +3。`_validate_attack`（L386-405）与决策门控（L210-249）在零偏移区。
- 其余锚点文件（reaction_spells.py / engine.py / check_modes.py / view.py / resolver.py / resources.py / resting/engine.py / runtime.py / derivation.py / reducer.py / 三个模板 json）**均未修改，Read 即 HEAD**。

开工纪律：执行时若工作区在途改动已变化（P 系列会话推进或已提交），先重测 `git diff -U0` 并重算偏移，再核对 §2.1 锚点；锚点漂移超出行号容差（±3 行且语义不符）→ 触发 §7 停止条件。

---

## §3 白名单逐文件改动规格

只允许改动下列文件。任何白名单外文件出现 diff → 触发 §7。

### 3.1 `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json`

1. warlock 桶（L64-66）追加 DOOL 池 spec：

```json
{"id": "dark_ones_own_luck", "minimum_level": 6, "maximum_ability_modifier": "cha", "minimum": 1, "short": "none", "long": "all", "source_ref": "srd-5.2.1:<执行时核实页码>:dark-ones-own-luck"}
```

- `short: "none"`：原文只给长休恢复（邪魔宗主.htm L37）。
- `source_ref` 页码执行时从原文核实（`srd-5.2.1:p70:dark-ones-own-luck` 仅为候选，禁止猜测落盘：先查 PHB2024 邪魔宗主页码）。

2. 新增顶层 `feat_resources` 区段（与 `class_resources` 平级）：

```json
"feat_resources": {
  "lucky": [
    {"id": "lucky_points", "minimum_level": 1, "maximum_proficiency_bonus": true, "minimum": 1, "long": "all", "source_ref": "srd-5.2.1:<执行时核实页码>:lucky"}
  ]
}
```

- 桶键 = feat id（不带 `feat:` 前缀的 canonical 形式，与 `class_resources` 桶键用 class id 的惯例一致）；条目内不带 feat 资格（资格由 feat_refs 门控，见 3.3）。
- `minimum_level: 1`：起源专长在 1 级即可持有。
- **禁止**把 lucky 条目塞进 `class_resources` 任何桶：rest 引擎 L41-45 桶键精确全等校验会拒绝。
- 既有 12 桶的键集合不得变化。

### 3.2 `src/rulesets/dnd2024/features/resources.py`

1. `ClassResourceDeclaration`（L40-160）扩展第四 maximum 形态：`maximum_proficiency_bonus`（布尔，true）。"恰一个 maximum 字段"互斥规则不变，四选一。
2. 新增 `feat_resources` 目录解析与校验（形状沿 class_resources：每个桶对应一个 feat id，桶内条目沿用 ClassResourceDeclaration 字段 + 恢复字段）。校验规则：
   - 桶键必须是 feats 内容目录中真实存在的 feat id（`content/feats/lucky.json` 必须存在；当前缺该文件时需同步新建，见 3.4 末尾）。
   - 条目恢复字段规则与 class_resources 同构（short/long 必填其一语义，执行时以现有校验代码为准对齐）。
3. `resource_state` 不改（读 `resources.class.<id>`，路径通用）。

### 3.3 `src/rulesets/dnd2024/resting/engine.py`

1. `_maximum`（L231-241）新分支：`maximum_proficiency_bonus` → 读 canonical 角色表 `derived.proficiency_bonus`（derivation.py L294；sync 输入即 canonical），低于 `minimum` 时取 `minimum`。
2. `sync_resources`（L96-124）：class_resources 路径零改动；新增 feat_resources 同步分支：
   - 资格判据：桶键 feat id ∈ `character["features"]["feat_refs"]`（derivation L288；ref 形如 `feat:lucky`，比较时归一前缀）。
   - 有资格：写 `resources.class.<resource_id> = {current, maximum, source_ref}`（与 class 池同构落位，reducer/战斗侧读取路径零改动）；maximum 用 `_maximum` 新分支。
   - 无资格：确保对应键缺省（清残留，防止删专长后旧池启用——对齐工作区规则"删除引用时同步清理"）。
   - resize 默认 `preserve_spent`（PB 升级池变大时 current += 增量，RAW 正确：长休恢复全部，进行中不回溯）。
3. `_recover_class_resources`（L243-268）：feat 池同构恢复（short/long 按 spec；lucky 为 long all）。资格判据同 2（恢复时同样按 feat_refs 门控）。
4. L41-45 键校验只作用于 class_resources；feat_resources 的桶键校验在 3.2 侧目录层完成，两套校验互不掺和。

### 3.4 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

1. `labels`（L7-32）追加：`"bard": "Bard"`、`"cutting_words": "Cutting Words"`、`"warlock": "Warlock"`、`"dark_ones_own_luck": "Dark One's Own Luck"`。
2. `classes` 新增 `bard` 段（沿 rogue 段 L209-232 形状）：

```json
"bard": {
  "source_ref": "srd-5.2.1:p31:bard-features",
  "features": [
    {"id": "cutting_words", "minimum_level": 3, "source_ref": "srd-5.2.1:p34:cutting-words",
     "summary": "When a creature you can see within 60 feet makes a damage roll or succeeds on an ability check or attack roll, use your Reaction and spend one Bardic Inspiration use to roll your Bardic Inspiration die and subtract it from the roll.",
     "parameters": {"die_sides_track": "bardic_die"}}
  ]
}
```

3. `classes` 新增 `warlock` 段：

```json
"warlock": {
  "source_ref": "srd-5.2.1:p69:warlock-features",
  "features": [
    {"id": "dark_ones_own_luck", "minimum_level": 6, "source_ref": "srd-5.2.1:p70:dark-ones-own-luck",
     "summary": "After you make an ability check or a saving throw and see the result but before it takes effect, spend one use to add 1d10 to the roll; once per roll; uses equal to Charisma modifier (min 1); regain all on a long rest.",
     "parameters": {"die_sides": 10}}
  ]
}
```

- `summary` 为英文 canonical 描述（沿现有目录惯例）；`source_ref` 页码执行时核实，禁止猜测定稿。
- 声明等级与子职表授予等级**精确全等**（cutting_words=3 对 college_of_lore L14-19；dark_ones_own_luck=6 对 fiend_patron L68-73），由 resolver `_validate_declarations`（L558-564 同族校验）fail closed。
- CW 骰面走既有 `*_track` 机制：`die_sides_track: "bardic_die"` → resolver `_value` L476-495 → `_track_value` L508-525 按 (class_id=bard, level) 读轨道，**零新 API**；DOOL 固定 d10 直参。
- `content/feats/lucky.json` 若不存在则新建（沿 `alert.json` 形状；机制字段先例见 alert L7；Lucky 本身无需机制字段——资格由 feat_refs 表达，池由 feat_resources 表达）。

### 3.5 `src/rulesets/dnd2024/combat/check_modes.py`（小改）

1. `attack_mode`（L104-111）新增可选参数 `extra_sources: tuple[dict, ...] = ()`：在 `attack_sources` 产出之后、`mode_from_sources` 归一之前并入 sources 列表。语义 = 来源追加，优劣势抵消由既有归一自动生效。
2. 不新增 `attack_sources` 扫描规则；`negated_by`/observer 语义不动；gm_grant 路径不动。
3. 调用方（resolution，见 3.6）在 Lucky 优势面声明时传 `extra_sources=({"code": "lucky", "mode": "advantage"},)`。
4. Lucky/DOOL/CW **不是**持续条件源，禁止写进 `attack_sources` 的注册表扫描（它们是意图声明与窗口，见 §6）。

### 3.6 新文件 `src/rulesets/dnd2024/combat/check_windows.py`

窗口注册表 + 检定窗口原语的全部实现。沿 `reaction_spells.py` 的形状与 docstring 纪律（每函数 docstring 说明机械与不变式）：

1. 常量：`CHECK_WINDOW_DECISION = "check_window"`、`DAMAGE_WINDOW_DECISION = "damage_window"`。
2. 窗口注册表：`code → entry`，entry 含 offer 谓词、payload 构造、validate、finish、AI 阶梯四个可调用。首批注册条目：
   - `lucky_disadvantage`（检定面；防守者 = 攻击目标；无反应成本）
   - `cutting_words_check`（检定面；窗口主人 = 有资格的诗人；反应 + 池）
   - `dark_ones_own_luck`（豁免/死亡豁免面；窗口主人 = 掷骰者本人；池）
   - `cutting_words_damage`（伤害面；窗口主人 = 有资格的诗人；反应 + 池；仅当同一诗人未在本次攻击的检定面花过——一次攻击一面）
3. `check_window_offer(...)`：构造 pending decision（沿 `_hit_reaction_offer` L116-146 payload 形状）：`decision_id`（沿既有 decision id 惯例，执行时对照 hit_reaction 的 id 生成）/ `kind` / `actor_id`（攻击者或掷骰者）/ `attacker_id` / `target_id` / `assigned_to`（窗口主人；AI 席位 = gm）/ `window_code` / `options: [{"code": "spend"}, {"code": "decline"}]` / 掷骰上下文（first/second、bless_bonus、attack_bonus、total、target_ac、hit/critical 预判或豁免 roll/dc 概要）/ `next_decision`（串行链）。
4. `validate_check_window(...)`：沿 `_validate_hit_reaction` L148-179 模式：池 `resource_state(...).current >= 1`（池存在即所有权，见 §1.3 与 §6）、`has_feature`（cutting_words / dark_ones_own_luck 经 `_feature_character` + features resolver；lucky 经池存在性，**不新建 has_feat API**）、CW 反应预算（沿 resolve 选项的 threat 反应预算校验）、一次窗口一次。
5. `finish_check_window(...)`：
   - 发池消耗事件 `dnd2024.class_resource.spent`（先例 resolution L913-917；reducer L185-197 零改动）。
   - 掷窗口骰（Lucky 劣势面：新掷一枚 d20；CW：掷 `bardic_die` 面诗人骰；DOOL：掷 d10）。
   - 重判：Lucky 劣势面 = 追加 `{"code": "lucky", "mode": "disadvantage"}` 后重走归一（优劣势抵消自动生效），natural 按新模式取值；CW 检定面 = total 减诗人骰；DOOL = total 加 d10。
   - 产出改写后的 `check.resolved`（rolls 追加窗口骰、window 修饰字段标注来源与消耗），并返回继续信号：攻击检定面 → 仍命中则续伤害链（next_decision），未命中则按 miss 终结（不掷伤害骰、不产 hit_reaction offer）。
   - decline 路径：除 pending 决策消耗外**零事件差异**。
6. `automatic_check_window(...)` / `automatic_damage_window(...)`：AI 阶梯（确定性，payload 同人可见，沿 `_automatic_hit_reaction` L270-299 模式）：
   - Lucky 劣势面：预判命中（含重击）且花点后取低**可能**翻盘 → spend，否则 decline。
   - CW 检定面：total - 诗人骰**最大面值** < target_ac（能翻盘）→ spend，否则 decline。
   - CW 伤害面：伤害 > 诗人骰最小面值（减了有意义）→ spend，否则 decline。
   - DOOL：掷骰失败且 +10 可达 DC → spend，否则 decline。
7. `reaction_spells.py` 不重构（uncanny_dodge 硬编码保留，注册表化归 S-P9；本文件只复用其导出的 `effective_armor_class` 等既有原语，不反向依赖）。

### 3.7 `src/rulesets/dnd2024/combat/resolution.py`

改动集中在 `_attack_events` 与 `_death_save_events`；**不得触碰工作区 L594+ 的 P7/P8 在途区段**（dispel magic 相关），hunks 物理隔离。

1. **尾段提取**：把命中结算体（HEAD L294-353：伤害构造 → damage_event → offer 链 → 直通提交/专注/胜利）提取为共享私有方法（命名建议 `_hit_settlement_events`），参数：instance/combat/actor/target/weapon/check_event 上下文（natural/rolls/check_mode/attack_bonus/critical 等）/intent/rng。直通路径与本切片窗口 finisher 共用。
   - 不变式：无窗口参与者时，产出事件序列与 RNG 消耗与 HEAD **逐字节等价**（既有战斗测试套全绿即守护；本切片不新增直通差异）。
   - 伤害骰（`roll_damage_node`）随之移入该方法——这是"重排"的实现载体：直通路径 RNG 序列不变（窗口候选不存在时先掷伤害骰再判 offer，与 HEAD 顺序一致）；窗口 pending 时伤害骰推迟到 finisher。
2. **检定面窗口插入**（`_attack_events` 掷骰区 L249-262 不动；hit 预判后）：
   - 候选链构造（有序）：`lucky_disadvantage`（防守者侧）→ `cutting_words_check`（诗人侧）。
   - Lucky 劣势面候选条件：防守者 lucky 池 ≥1；攻击检定**未已是劣势**（叠加无意义不触发）；`natural != 1`（必失手无意义；natural 20 **允许**——花点后 min 取低可顶掉重击，payload 携带 critical 预判供决策）。
   - CW 检定面候选条件：hit 预判为真；目标为 party 侧；有资格诗人存在（bard ≥3、cutting_words、池 ≥1、反应预算 ≥1）；distance（诗人↔攻击者与目标按原文取可达判据，执行时以 theater 距离原语核实）≤60；防守者可见性守卫沿 uncanny_dodge 谓词族（blinded 等）。诗人 == 防守者时与 Shield/UD 共享反应预算，一次只能一面。
   - 任一候选存在 → 发 `dnd2024.decision.pending`（kind=check_window，next_decision 串行链），**本函数返回，不掷伤害骰**。
   - 无候选 → 直通 `_hit_settlement_events`（零变化）。
3. **伤害面窗口**（`_hit_settlement_events` 内部）：hit_reaction（Shield/UD）链之后、`_submit_hit_damage` 之前：CW 伤害面候选存在 → kind=damage_window pending（携带 damage_event 概要与诗人骰信息）替代提交；decline → 提交照旧。hit_reaction 已 pending 时经 next_decision 挂接（沿 Divine Smite 先例 reaction_spells L143-145/L211/L260 的 next_decision 链式机制）。
4. **`_death_save_events`（HEAD L1109-1140，工作区 -39 折算）DOOL 窗口**：死亡豁免掷骰后：掷骰者本人有 DOOL（池 ≥1）且掷骰 <10（20 无需救；≥10 已成功无需救）→ kind=check_window pending（window_code=dark_ones_own_luck，payload：roll/dc=10/失败计数预判），return 不落 death_save.resolved 与 end_turn；finisher 花点重判后落事件。死亡豁免"含意图、单暂停"语义：pending 期间该行动者的意图集被门控（§3.8）。
5. **Lucky 自身优势面声明消耗**（三个意图，`spend_luck: true` 参数，缺省缺省行为零变化）：
   - `_attack_events`：`intent["spend_luck"]` → `attack_mode(..., extra_sources=(lucky advantage source,))` + 发 lucky_points spent 事件（无条件消耗，池充足由 validation 保证）。
   - `_death_save_events`：同构（掷两枚取高语义；死亡豁免的 20 特例按 RAW 在取高后判定）。
   - `_spell_events` 法术攻击检定分支（HEAD L356-701 内的 attack 检定处，工作区按偏移折算）：同构。
   - 专注豁免/save_repeat 无声明通道（§1.6 裁剪 5）。
6. **DOOL 专注豁免窗口（仅攻击直通路径尾段）**：`_hit_settlement_events` 内对 `_concentration_events(target, damage, rng)` 的调用改用本文件私有包装（建议 `_concentration_with_dool`）：调用原函数得事件批次 → 若存在失败结果（check.resolved kind=concentration 且失败）且防守者有 DOOL 资格（池 ≥1、warlock ≥6 特性）→ 拆分：保留掷骰信息发 check_window pending（不落 concentration.ended），finisher 花点 +d10 重判后再决定是否落 concentration.ended。**`engine._concentration_events` 本身不改**；其余 5 个调用点保持直调（§1.6 裁剪 4）。
7. 池消耗事件统一用 `dnd2024.class_resource.spent`（`resource` 字段分别为 `lucky_points` / `bardic_inspiration` / `dark_ones_own_luck`），reducer 零改动。

### 3.8 `src/rulesets/dnd2024/combat/validation.py`

改动集中在决策门控（L210-213）、decision.resolve 校验（L225-249）与三个意图校验区（`_validate_attack` L386-405、`_validate_spell` L407-426、death_save 意图校验区——执行时以意图校验分发表定位）。**不得触碰工作区 L471+ 的 P7/P8 resist_type 区段**。

1. 决策门控扩展：`check_window` / `damage_window` pending 存在时 → 只允许 `decision.resolve` 与 `combat.message`（沿 HIT_REACTION 门控形状；门控判据按 kind ∈ 集合，窗口主人 = assigned_to）。
2. decision.resolve 校验扩展：kind ∈ {HIT_SPELL, HIT_REACTION, check_window, damage_window}；新 kind → expected_owner = assigned_to + 委托 `validate_check_window`（3.6）；CW 两面消耗反应 → resolve 选项校验 threat 反应预算（沿既有 HIT_REACTION resolve 校验）。
3. `spend_luck` 参数校验（三处，`_validate_attack` 为锚）：intent 带 `spend_luck: true` 时 → actor `resource_state(..., "lucky_points").current >= 1`，否则 `CombatIntentError("lucky pool is empty")`；池不存在（无 feat:lucky）同样拒绝。参数缺省/为 false → 既有行为零变化。death_save 与 cast_spell 意图校验区同构追加。

### 3.9 `src/rulesets/dnd2024/combat/engine.py`

1. `available_intents` L174-183：assigned 决策过滤集合从 `{HIT_REACTION_DECISION}` 扩展为含两个新 kind（窗口主人不一定是当前行动者，必须走该分支而非 L189-192 的当前行动者分支）。
2. `next_automatic_intent` L304-322 kind 分发：新增两 kind → `automatic_check_window` / `automatic_damage_window`（3.6）。
3. intent 分发 L656-663 不动（`decision.resolve` → `_decision_events` 既有路径）；resolution `_decision_events`（HEAD L1167-1177）内新增两 kind 分发到 check_windows finisher（此改动物理位置在 resolution.py，归属 3.7，但语义上与 engine 分发配套，执行时一并核对）。
4. `_concentration_events`（L1067-1092）不改。

### 3.10 tests（新增/追加）

- 新文件 `tests/rulesets/test_dnd2024_check_windows.py`：窗口原语 + 三特性端到端（§4 矩阵 W 系列）。
- feat 池与目录校验测试：就近追加到既有 rest/recovery/resources 测试文件（执行时 Glob 定位 `tests/rulesets/` 下既有 rest engine 与 class resource 测试文件），或新建 `tests/rulesets/test_dnd2024_feat_resources.py`（§4 矩阵 R 系列）。落位在执行报告 §8 说明。

---

## §4 测试矩阵

每行一个 case；编号进入执行报告。R = 资源池，D = 声明校验，L = Lucky，C = Cutting Words，O = DOOL，G = 门控/直通不变式，A = AI 阶梯。

| # | 场景 | 断言要点 |
|---|---|---|
| R1 | 6 级 warlock DOOL 池 sync | maximum = cha mod（≥1 下限）；5 级不授（minimum_level 6） |
| R2 | DOOL 短休 | `short: "none"` → 不恢复；长休 → 全恢复 |
| R3 | feat:lucky 角色池 sync | maximum = 当前熟练加值；PB 升级 resize preserve_spent |
| R4 | 无 feat:lucky 角色 | `resources.class.lucky_points` 缺省；旧存档残留被清 |
| R5 | `maximum_proficiency_bonus` 校验 | 与其它 maximum 字段并存 → fail closed |
| R6 | class_resources 桶键 | 新增 feat_resources 区段后 12 职业桶校验照常通过（两套校验隔离） |
| D1 | cutting_words 声明等级 | =3 与子职表全等；改成 2 → 目录校验 fail closed |
| D2 | `die_sides_track: "bardic_die"` | 必须真轨道（resolver L558-564）；诗人骰面随等级 6/8/10/12 |
| D3 | dark_ones_own_luck 直参 | die_sides=10；声明等级 =6 全等 |
| L1 | attack + spend_luck | 掷两枚取高；rolls 长 2；advantage_sources 含 lucky source；池 -1 |
| L2 | 平攻 + Lucky 劣势面花点 | 窗口 pending；resolve 掷新 d20；natural=min；重判 hit；池 -1；decline → 与直通逐字节等价 |
| L3 | 优势攻击 + Lucky 劣势面 | 花点后优劣势抵消 → 平攻重判（mode=""） |
| L4 | 攻击已劣势 | 窗口不触发 |
| L5 | natural 20 | 窗口触发且花点可顶掉重击（min 取低后重判 critical=False）；decline 保持重击 |
| L6 | natural 1 | 窗口不触发（必失手） |
| L7 | 池 0 | 劣势面窗口不触发；优势面声明被 validation 拒绝 |
| L8 | death_save/cast_spell + spend_luck | 同构掷两枚取高 + 池 -1 |
| C1 | 敌攻命中我方，诗人花检定面 | 诗人骰减 total → 可能翻盘 miss（miss 终结：无伤害骰、无 hit_reaction offer） |
| C2 | 翻盘不可行 | total - 诗人骰仍 ≥ AC 时 AI decline；玩家侧仍可自由花（客户端决策） |
| C3 | 60 尺外 | 窗口不触发 |
| C4 | 池 0 / 反应预算 0 | 窗口不触发 |
| C5 | 检定面花过的诗人 | 同一攻击伤害面不可再花（一面） |
| C6 | 诗人 == 防守者 | 与 Shield/UD 共享反应预算：一面花后另一面不可花 |
| C7 | 伤害面花点 | hit_reaction 之后掷诗人骰减伤害；下限 0；decline → 提交事件等价 |
| O1 | 死亡豁免掷骰 <10 花点 | +d10 重判；≥10/20 不出窗口；一次豁免一次 |
| O2 | 专注豁免失败花点 | pending 期间不落 concentration.ended；花点达标则专注保持 |
| O3 | DOOL 一次检定一次 | 同窗口 spend 后 decline 不可再花 |
| G1 | 直通不变式 | 无三特性资格的角色跑既有攻击/死亡豁免套件，事件序列与 RNG 消耗与 HEAD 等价（既有全量测试守护 + 至少 1 个显式 RNG 序列断言） |
| G2 | check_window pending 门控 | 其它意图（attack/end_turn/cast_spell）被拒；只允许 decision.resolve/combat.message |
| G3 | 非 owner resolve | 拒绝 |
| G4 | damage_window pending 门控 | 同 G2 |
| A1 | AI Lucky | 预判命中且取低可能翻盘 → spend；否则 decline |
| A2 | AI CW 两面 | 翻盘/减伤可行 → spend；否则 decline（确定性） |
| A3 | AI DOOL | 失败且 +10 可达 DC → spend；否则 decline |

---

## §5 验收命令（PowerShell，无 `&&`）

```powershell
python -m pytest tests/rulesets -x -q
python -m pytest tests/rulesets/test_dnd2024_check_windows.py -q
python -m pytest tests -q
git status
git diff --stat
```

- 第一条为主回归（rulesets 全量）；第二条为新文件；第三条为全量兜底（按 `docs/ENGINEERING_RULES.md` §15 对高风险行为的 Testing 要求——本切片触碰战斗规则与多人决策，属高风险）。
- `git status` / `git diff --stat` 用于核对白名单边界与 P7/P8 在途文件隔离（在途文件不出现在本切片 diff 中）。

---

## §6 禁止事项

1. 禁止触碰 P7/P8 在途改动：`resolution.py` 工作区 L594+ 区段（dispel magic）、`validation.py` 工作区 L471+ 区段（resist_type）、`catalog.py`、`srd_combat_core.json`、未跟踪 `tests/rulesets/test_dnd2024_dispel_magic.py`。不回滚、不"顺手修复"、不并入本切片提交。
2. 禁止新建 has_feat API、禁止把 feat_refs 塞进 `_feature_character`（view.py L337-362 投影形状不变；战斗侧所有权 = 池存在性）。
3. 禁止把 feat 池塞进 class_resources 桶（rest 引擎 L41-45 精确全等校验会拒绝）。
4. 禁止把 lucky/DOOL/CW 写成 `attack_sources` 的持续条件扫描规则（它们是意图声明与掷后窗口，不是持续条件源；check_modes 只接受 `extra_sources` 注入点这一处改动）。
5. 禁止在 resolution/validation 的 generic path 写具体特性 if 分支：窗口逻辑全部走 check_windows 注册表；resolution 只做注册表调用与尾段提取。
6. 禁止修改 `reducer.py`（`class_resource.spent` 既有路径零改动）、禁止修改 `engine._concentration_events`、禁止修改 bardic_inspiration 池声明。
7. 禁止把窗口骰并入既有骰的 RNG 序列（直通不变式 G1 是本切片的生命线；花点路径的新骰必须在窗口 resolve 时单独掷）。
8. 禁止为旧存档写迁移（新 `resources.class.*` 键对旧存档缺省，sync 自动补齐——既有 resize 路径即迁移）。
9. 禁止猜测 source_ref 页码定稿（执行时从原文核实，禁止沿用本任务书示例中的候选页码未经核实落盘）。
10. 禁止使用"补天"一词（项目惯例，含代码注释、文档与执行报告）。
11. 禁止把 `docs/internal/` 内容当作对外契约或从玩家文档引用。

---

## §7 停止条件

触发任一条件 → 停止执行，向维护者报告，等待裁决：

1. **锚点漂移**：§2.1 任一锚点行号与实际代码不符（说明 P 系列已提交新代码、基线不再是 4ef1d33d）→ 停止；重读 git log 与 `git diff -U0`，请示是否按新基线重锚。
2. **在途冲突**：本切片 hunks 与 P7/P8 在途改动在同一文件物理交叠（无法隔离）→ 停止上报。
3. **直通不变式破裂**：尾段提取后，无窗口参与者场景下既有战斗测试出现任何失败 → 停止（说明提取改变行为），不得以"修测试"方式掩盖。
4. **契约冲突**：实现中发现窗口骰的 RNG 位次或事件字段与 SP2 §3.8 / SP10 §3 契约矛盾 → 停止并标注冲突点，不得单方面改契约。
5. **模板数据不符**：cutting_words/dark_ones_own_luck 的声明等级与 `srd_subclasses.json` 子职表不一致，或 `content/feats/lucky.json` 缺失且无法按既有 feat 形状新建 → fail closed 停止。
6. **权限/存档**：任何改动需要触碰存档迁移、多人权限或引擎核心契约 → 超出本切片范围，停止请示。
7. **测试范围外失败**：验收命令发现与本切片无关的既有失败（如 P7/P8 在途代码导致）→ 不修复、不掩盖，记录到执行报告并停止。

---

## §8 执行报告模板 + 迁移债登记

### 8.1 执行报告（完成后按此模板回报）

```
## S-P3 执行报告
- 基线核对：HEAD=<commit>；§2.2 偏移映射复核结果=<一致/漂移+新映射>
- 白名单符合性：改动文件清单 vs §3 白名单 diff=<一致/偏差说明>
- P7/P8 隔离：在途文件 diff 状态=<未触碰/异常>
- source_ref 核实：DOOL=<页码>；lucky=<页码>；cutting_words=<页码>（原文路径+行号）
- §4 测试矩阵：W/R/D/L/C/O/G/A 各项结果（pytest 输出摘要）
- 直通不变式 G1：RNG 序列审计结果
- §1.6 裁剪确认：8 项裁剪均未被越界实现
- 遗留偏差与原因
```

### 8.2 迁移债登记（本切片显式遗留，后续切片/批次承接）

| # | 债 | 承接方 |
|---|---|---|
| 1 | CW 法术伤害面（法术伤害批暂停） | 后续批暂停机械切片 |
| 2 | CW 敌方属性检定面（触发源缺失） | P10 属性检定原语 |
| 3 | DOOL：save_repeat tick 面、以玩家为目标的法术豁免面 | 批暂停机械切片 |
| 4 | DOOL：其余 `_concentration_events` 调用点（reaction_spells 2 处 + resolution 其余 3 处）的窗口化 | 本切片机制稳定后批量接线 |
| 5 | Lucky 优势面：专注豁免/save_repeat 声明通道 | 同 4 |
| 6 | uncanny_dodge 硬编码注册表化（含"伤害吸收反应"扩展需求，主计划 L153） | S-P9 任务书评估 |
| 7 | Bardic Inspiration 授予面（给队友骰 + 队友检定失败附加） | 独立原语，1C 后续批次 |
| 8 | feat_resources 目前仅 lucky 一条目 | 后续 feat 池（Battle Master 战技等同族需求）沿此区段扩展 |
| 9 | check_event 的 bless_bonus 字段演进（SP10 移除为 check_bonus/check_bonus_rolls 时，本切片窗口骰迹字段需同步迁移） | SP10 执行时对齐 |
