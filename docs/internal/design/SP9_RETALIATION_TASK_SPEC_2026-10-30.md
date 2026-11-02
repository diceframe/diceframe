# S-P9 任务书：受击反应攻击（Retaliation，受击反应注册表的攻击效果扩展）

| 项 | 值 |
|---|---|
| 切片编号 | S-P9 |
| 主计划出处 | `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` L142（原语行：受击反应攻击｜Berserker Retaliation；切片 3 反应注册表的攻击效果扩展） |
| 原语 | 受击反应攻击：伤害落地后，防御者以反应对攻击者发动一次完整近战攻击；受击反应注册表从单效果（减伤）扩展为多效果 kind 分发 |
| 代表特性 / 验证载体 | 报偿 Retaliation（狂战士道途 10 级，端到端唯一载体） |
| 编写日期 | 2026-10-30 |
| 代码基线 | commit `4ef1d33d`（HEAD）。工作区存在 P7/P8 在途未提交改动，行号映射见 §2.2 |
| 依赖契约 | SP3（HIT_REACTION 窗口形状与 `_reaction_feature_choices` 注册表化迁移债的承接——本切片交付该注册表化）；SP2 §3.8（骰迹与 RNG 顺序契约——反击骰位于窗口 resolve 之后）；切片 3（HIT_REACTION 决策流本体，本切片不改窗口形状） |
| 状态 | 待执行 |

---

## §1 规则语义与原文出处（2024 版，显式裁剪）

### 1.1 Retaliation（报偿）——本切片端到端代表

原文：`.rulebooks/dnd5r/玩家手册2024/角色职业/野蛮人/狂战士道途.htm` L15-17：

> **10级：报偿 Retaliation**
> 当一名位于你5尺内的生物对你造成伤害时，你能够以反应使用武器或徒手打击对其发动一次近战攻击。

机械拆解：

- **授予等级 10**（2014 版为 14 级，不沿用 2014 等级；`srd_subclasses.json` berserker 段 `"10": ["retaliation"]` 幽灵 id 已在位）。
- **触发条件：位于你 5 尺内的生物"对你造成伤害"**——是伤害造成（damage dealt），不是被命中（hit）。这与同窗口的 Shield（被击中时改判 AC）和 Uncanny Dodge（被攻击命中时减半）不同：Retaliation 不能改变、不能减免原始伤害，原始伤害必然全额落地，反击是伤害落地之后的独立攻击。伤害为 0（如全额抗性免疫后归零）时"没有造成伤害"，不触发。
- **攻击载体：使用武器或徒手打击的一次近战攻击**——完整攻击结算：d20 攻击检定 vs 攻击者 AC（优劣势来源、bless 等通用检定管线照走）→ 命中后掷武器伤害骰 → 抗性结算 → 落账。远程武器（长弓、弩）不是近战攻击的合法载体；徒手打击天然可用。
- **成本：仅一次反应**，无使用次数池、无短休/长休恢复语义（与 rage 池无关）。
- **与狂暴的交互（全部走既有通用结算，本切片零新逻辑）**：狂暴中用力量武器反击命中 → +狂暴伤害加值（通用 rage damage 结算自然生效）；反击发生在**攻击者的回合**，不延长狂暴（2024 狂暴延长限定"你的回合"的攻击检定，现有 `economy.actor_id` 守卫天然正确）；若反击者隐形，反击的攻击检定照常终止其隐形（通用 `spell_end_trigger "attack_roll"`）。
- **与偷袭/魔化等 per-turn rider 的交互**：反击是攻击者回合内的一次合法攻击，hex（魔化挂目标）命中加骰、sneak attack（若反击者多职游荡者）按该回合记账一次性生效——均为既有结算的自然结果，RAW 正确。
- **濒死守卫**：原始伤害把反击者打到 0 HP → 反击者倒地（unconscious → incapacitated），不能再花反应反击；反应预算不消耗。

### 1.2 窗口归属与效果 kind 分发（本切片核心工程语义）

- Retaliation 复用既有 `hit_reaction_spell` 决策窗口（切片 3）：触发面相同（被敌方攻击命中、伤害已掷未落），payload 零新增字段（`damage_event` 已携带预掷伤害值，决策信息完备）。
- 效果差异由 **choice 的 `effect_kind`** 表达：`damage_multiplier`（UD 减半：伤害落地前生效）与 `counterattack`（反击：原始伤害**全额落地之后**生效）。resolve 侧按 kind 分发，而非新增决策种类。
- **时序偏离声明（fail-safe 方向）**：RAW 的反击决策点在伤害落地之后；本实现 offer 仍在伤害落地前（与 Shield/UD 同窗）。信息等价（伤害值已掷出、payload 可见，选择反击不改变它），落地顺序严格 RAW（先全额落地、后反击）。唯一可观察差异是玩家"提前"在伤害落地前表态，无平衡漏洞。
- 与 SP3 check_windows（check_window / damage_window 家族）的关系：本切片**不新增**窗口家族、不碰 SP3 的 `check_windows.py`；反击是 HIT_REACTION 决策 resolve 后的**效果结算扩展**，不是新决策窗口。

### 1.3 受击反应注册表化（承接 SP3 §8 迁移债，本切片交付）

现状：`reaction_spells.py` L70-114 `_reaction_feature_choices` 为 uncanny_dodge 单条硬编码（SP3 已登记、显式留给本切片）。本切片把它重构为**模块级效果注册表**：

- 条目 = 特性 id → {effect_kind, 参数名, per-kind 守卫}；
- 特性参数值仍从目录声明经 `feature_value` 读取（UD `damage_multiplier: 0.5` 先例；Retaliation 新声明 `counterattack_range: 5`），fail closed：参数缺失/非法即不 offer；
- UD 的注册表化**行为不变**（现有 UD 全套测试是回归守护）；
- 未来受击反应新特性（伤害吸收反应 Spirit Shield / Song of Defense，主计划 L153）加注册表行 + 目录声明即可，不改函数体。

### 1.4 显式裁剪清单（本切片不做，全部登记 §8 迁移债）

1. **Frenzy（狂战士 3 级）**：狂暴期间鲁莽攻击回合的力量首次命中 +「狂暴伤害加值数量的 d6」——条件伤害 rider 族（hex/sneak 同族，主计划归切片 5 域），不在本切片。
2. **Mindless Rage（6 级）**：狂暴中魅惑/恐慌免疫——条件免疫族（S-P5/S-P6 相邻域），不在本切片。
3. **Intimidating Presence（威慑之姿 14 级）**：附赠动作 30 尺光环 + 感知豁免 + 恐慌 + 长休恢复或耗狂暴重置——光环（S-P6）+ 随机表 + 豁免 tick 的组合大件，归阶段二批次；本切片零触碰。
4. **反击武器玩家选择**：不弹武器选择子决策。确定性规则：`_available_attacks` 中第一件射程 ≤5 的档案（装备武器在前、徒手打击殿后），与借机攻击同款。
5. **counterattack 与 next_decision（Divine Smite held）并存**：offer 层 fail-safe——`next_decision` 非空时不产出 counterattack 条目（时序矛盾：Smite 未 resolve 则伤害未落地，触发前提不满足）。当前阵容该组合不可达（Smite=圣武士、Retaliation=狂战士，攻守同侧不成立），登记债 D1。
6. **反击的嵌套反应**（反击命中后再触发攻击者的 Shield/UD/反击）：当前 kind 体系下防守方恒为敌方（`enemy` kind 无 reaction choices），天然不可能；以不变式测试守护，不实现递归。
7. **敌方反击能力**：enemy kind 不进受击反应注册表（现有公共守卫 kind 过滤不变），敌方法术反制/特性反应归阶段二敌方能力批次，登记债 D3。
8. **前端与 feed 投影零改动**：决策卡片沿现有 features 列表渲染新 choice（纯增量键）；effect_kind 投影缺失若影响展示质量，归后续 UI 批次。

---

## §2 代码事实锚点表

### §2.1 锚点（基线 4ef1d33d；未注明者即 HEAD）

| 文件 | 行号 | 事实 |
|---|---|---|
| `src/rulesets/dnd2024/combat/reaction_spells.py` | **全文未修改（=HEAD）** | 本切片主改动文件 |
| 同上 | L22 | `HIT_REACTION_DECISION = "hit_reaction_spell"`（复用，零新增决策种类） |
| 同上 | L39-68 | `_reaction_spell_choices`（Shield 路径，不动） |
| 同上 | L70-114 | `_reaction_feature_choices`：公共守卫 L87-93（kind∈{player,companion}、hp>0、反应预算≥1、reaction_blocked/shield、incapacitated）；UD 专属守卫 L95-98（blinded 防守者、invisible 攻击者）；L99-108 feature_value 读 `damage_multiplier` fail closed；L109-114 单条 choice 构造 |
| 同上 | L116-146 | `_hit_reaction_offer`：L121 side 检查；L124 调 choices（签名扩展点①）；L130-146 payload（decision_id/kind/actor_id/attacker_id/assigned_to/options/attack_total/armor_class/critical/damage_event/spells/features/next_decision） |
| 同上 | L148-179 | `_validate_hit_reaction`：L170-177 feature 分支重算 choices 按 feature_id 查找（签名扩展点②：传 decision 的 damage_event / next_decision） |
| 同上 | L181-268 | `_finish_hit_reaction`：L186-189 cleared 事件；L191-218 feature（UD）分支（减半 + next_decision held 同步减半 + 直通 `_submit_hit_damage` + 专注）；L219-257 cast（Shield）分支（不动）；L258-268 cast 尾段（不动）。**counterattack 分支插入点** |
| 同上 | L270-299 | `_automatic_hit_reaction` AI 阶梯：L285-289 useful cast；L296-298 `features[0]` 直接取第一条（升级为 effect_kind 分发） |
| `src/rulesets/dnd2024/combat/resolution.py` | L189-355 | `_attack_events` 全体（工作区=HEAD，P7/P8 改动在 L593+，见 §2.2）。反击结算**直接复用本函数** |
| 同上 | L270-275 | 事件头：`spell_end_trigger("attack_roll")` + L274 `self._attack_cost_event(...)`（产生 `dnd2024.attack.spent`——反击的替换目标） |
| 同上 | L284-293 | rage sustained 守卫：`economy.actor_id == actor_id` 才延长（反击在攻击者回合 → 天然不延长，不变式） |
| 同上 | L294-341 | 命中结算体（hex/sneak/暴击/rage_damage/抗性/damage_event/feature_use 延迟记账）——反击复用全链 |
| 同上 | L342-348 | offer 链：`_hit_spell_offer` + `_hit_reaction_offer(next_decision=offer)` |
| 同上 | L350-353 | 直通尾段：`_submit_hit_damage` → `_concentration_events` → `_last_hostile_defeated` victory |
| 同上 | L741-758 | `_submit_hit_damage`（静态：落地 + feature_use 弹出转 `dnd2024.feature.used`） |
| 同上 | L760-781 | `_feature_used_this_turn`（round+turn_index 账本；反击回合=攻击者回合，语义正确） |
| 同上 | L815-845 | `_sneak_attack_dice`（反击复用时守卫链自然生效） |
| 同上 | L1167-1224 | `_decision_events` movement 分支 = **借机攻击先例**：L1186-1202 attack_intent 构造（近战档案选择：`_available_attacks` 中 range≤5 第一件，enemy 用 attack_id / 玩家用 weapon_ref）；L1206 `self._attack_events(...)` 复用；L1210-1217 **cost 事件替换模式**（定位 `dnd2024.attack.spent` 替换为 `dnd2024.reaction.spent` reason="opportunity_attack"，注释明确法术提前终止事件不可被覆盖） |
| `src/rulesets/dnd2024/combat/engine.py` | L1031-1039 | `_attack_cost_event`（`dnd2024.attack.spent` 事件源） |
| 同上 | L1098-1100 | `_distance(combat, first, second)`（theater 1D 距离；5 尺守卫用） |
| 同上 | L160-335 | 决策分发与 `next_automatic_intent`（SP3 锚点，零改动） |
| `src/rulesets/dnd2024/combat/view.py` | L208-246 | `_available_attacks`：装备武器在前、徒手打击殿后（L209-223 docstring 明确"第一件可用档案"约定）；enemy 返回 stat-block |
| 同上 | L282-309 | `_unarmed_strike`（canonical 徒手档案，range=5，恒在 `_available_attacks` 尾部——反击载体永不落空） |
| 同上 | L337-362 | `_feature_character`（静态切片：build/abilities/resources.class/equipment.item_refs） |
| `src/rulesets/dnd2024/combat/triggered_spells.py` | L63-95 | `_hit_spell_offer`：L69 `self._current_actor(combat) != actor["actor_id"]` 时 None——反击发生在攻击者回合，反击者的 Smite offer **天然关闭**（不变式，无需新代码） |
| 同上 | L97-130 | `_spell_cost_events`（`spend_action=False` 先例，不动） |
| `src/rulesets/dnd2024/combat/reducer.py` | L106 / L342 | `reaction.spent`（扣反应预算）/ `decision.cleared`（清 pending）——既有通用处理，零改动 |
| 同上 | L32 | 投影事件清单含 check.resolved——反击的 `check.resolved kind="attack"` 沿用，零 reducer 改动 |
| `src/rulesets/dnd2024/combat/check_modes.py` | L99-111 | `attack_mode`（`use_grant` scoped 语义；反击经 `_attack_events` 默认路径，行为与借机攻击完全一致，不引入新差异） |
| `src/rulesets/dnd2024/combat/validation.py` | L210-213 / L225-249 | HIT_REACTION 门控与 decision.resolve 校验（调 `_validate_hit_reaction`）——校验重算 choices 自动覆盖新注册条目，**validation.py 零改动** |
| `src/rulesets/dnd2024/features/resolver.py` | L476-525 | `feature_value` / `_track_value`（`<name>_track` 与直接参数解析，SP3 锚点，零改动） |
| `templates/rulesets/dnd2024_srd/content/subclass_catalogs/srd_subclasses.json` | L8-13 | berserker 段：`feature_ids_by_level {"3": [frenzy], "6": [mindless_rage], "10": [retaliation], "14": [intimidating_presence]}`（幽灵 id 在位，零改动） |
| `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` | L7-32 | labels 平铺区（若含 `uncanny_dodge` 键，则同步追加 `retaliation` 键；choice.name 的 `catalog.label` fallback 已有） |
| 同上 | L178-207 | barbarian 段：rage 条目（`parameters: {"rage_damage_track": "rage_damage"}` + rages 池 + capabilities）——**retaliation 新条目插入点**（该段 features 数组追加） |
| 同上 | L209-232 | rogue 段形状先例：L213-221 sneak_attack、L223-228 uncanny_dodge（`minimum_level: 5` + `parameters: {"damage_multiplier": 0.5}`） |
| `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` | L59 | `rage_damage` 轨道（1A 切片 2 产物，反击的狂暴加值经 `_track_value` 读取，零改动） |
| `tests/rulesets/test_dnd2024_uncanny_dodge.py` | L116-239 | UD 全套（offer/减半/decline/耗尽/AI/双 offer/等级门控/盲守卫）；**L127-132 choice 全等断言需同步加 `effect_kind` 键**；fixture 形状（`_rogue_sheet`→`advance(runtime, sheet, level)`、`monk_instance`、`start_combat`、`SequenceRng`、`_enemy_attack`、`_pending`、`_resolve`）是新测试文件的搭建基准 |
| `tests/rulesets/test_dnd2024_sneak_attack.py` | L549 | UD 减半 + sneak 记账组合（回归守护） |
| `tests/rulesets/test_dnd2024_stoneskin.py` | L575 | stoneskin + UD 双半叠加（回归守护；反击分支不触碰） |
| `tests/rulesets/test_dnd2024_origin_feat_spells.py` | L143 / L216 | Shield 反应路径 + Shield/UD 双 offer（options 断言 ["cast","feature","decline"]，回归守护） |
| `tests/rulesets/test_dnd2024_combat.py` | L224 | 借机攻击决策（`_decision_events` movement 分支未动的守护） |
| `tests/rulesets/test_dnd2024_hold_person.py` | L612 | incapacitated 守卫（公共守卫回归） |
| `tests/rulesets/test_dnd2024_class_features.py` | L583-610 | UD 目录声明三件套测试（no_capability/等级门控/locale 标签）——retaliation 声明测试先例 |

### §2.2 工作区在途改动与行号偏移（P7/P8 会话，未提交）

现状（2026-10-30 核实）：M `catalog.py`（1 行）/ M `resolution.py`（+39）/ M `validation.py`（+5/-2）/ M `srd_combat_core.json`（+1）/ 未跟踪 `tests/rulesets/test_dnd2024_dispel_magic.py`。偏移规则：

- `resolution.py`：HEAD ≤L593 无偏移（本切片全部锚点 ≤L593，直接读工作区即可）；HEAD ≥L615 = 工作区行号 −39；
- `validation.py`：HEAD ≤L470 无偏移，其后 +3；
- `reaction_spells.py` / `view.py` / `reducer.py` / `check_modes.py` / `triggered_spells.py` / `engine.py` / 两个目录 JSON：未修改（=HEAD）；
- P7/P8 意图是 counterspell/dispel_magic（施法反应），与 HIT_REACTION 家族无交叠；执行侧开工时先 `git status` + `git diff -U0` 重测偏移，若 P 系列已合入或偏移失效 → §7 停止条件。

---

## §3 白名单逐文件改动规格

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

barbarian 段 `features` 数组（L180-205）追加 retaliation 条目，形状对齐 rogue/uncanny_dodge（L223-228）：

```json
{
  "id": "retaliation",
  "minimum_level": 10,
  "source_ref": "srd-5.2.1:p30:retaliation",
  "summary": "When a creature within 5 feet of you deals damage to you, you can use your Reaction to make one melee attack against it using a weapon or an Unarmed Strike.",
  "parameters": {"counterattack_range": 5}
}
```

- 无 `resource_id`、无 `capabilities`（纯反应特性，成本走 `combat["reactions"]`，与 UD 同形）；
- `summary` 为英文目录语言（对齐现有条目，如 L185 rage / L226 UD）；中文展示走 locales，本切片不新增 locale 键（choicename 的 `catalog.label` fallback 已覆盖英文兜底）；
- labels 平铺区（L7-32）若存在 `uncanny_dodge` 键则同形追加 `"retaliation": "Retaliation"`；不存在则跳过（fallback 生效）。

### 3.2 `src/rulesets/dnd2024/combat/reaction_spells.py`（主改动）

**（a）模块级效果注册表**（新增，置于 `HIT_REACTION_DECISION` 常量附近）：

```python
# 受击反应特性效果注册表：entry = 参数化守卫 + 效果分发。
# 条目之外不存在硬编码特性分支；新受击反应特性 = 注册表行 + 目录声明。
REACTION_FEATURE_EFFECTS: dict[str, dict[str, Any]] = {
    "uncanny_dodge": {
        "effect_kind": "damage_multiplier",
        "parameter": "damage_multiplier",
        "requires_sight": True,
    },
    "retaliation": {
        "effect_kind": "counterattack",
        "parameter": "counterattack_range",
        "requires_sight": False,
    },
}
```

**（b）`_reaction_feature_choices` 重构为注册表迭代**（L70-114 重写，公共守卫 L87-93 原样保留在函数头部）：

- 签名扩展：`(self, combat, defender, attacker=None, damage_event=None, next_decision=None)`——三个调用点同步（见 (c)(d)(e)）；UD 单测直调兼容（默认 None 下 UD 守卫不读新参数）；
- 迭代 `REACTION_FEATURE_EFFECTS`：`has_feature(character, feature_id)` 为 False 跳过；
- 参数读取 fail closed（对齐现有 L102-108 形状）：`damage_multiplier` 须 `0 < value < 1` 的 int/float（bool 排除）；`counterattack_range` 须正 int（bool 排除）；
- per-kind 守卫：
  - `requires_sight`（UD）：blinded 防守者 / invisible 攻击者跳过（原 L95-98 移入）；
  - `counterattack`：`attacker` 非 None 且 `self._distance(combat, defender_id, attacker_id) <= counterattack_range`；`damage_event` 非 None 且 `int(damage_event["amount"]) > 0`；`next_decision` 为 None（§1.4 裁剪 5 的 fail-safe）；
- choice 输出：现有键（feature_id / name / mode="reaction_defense" / 特性参数键）+ 新键 `"effect_kind"`；UD 条目输出不变量 = 现四键 + effect_kind（damage_multiplier 值保留）。

**（c）`_hit_reaction_offer`**（L116-146）：L124 调用改为传入 `damage_event` 与 `next_decision`（payload 已有，零 payload 结构变化）。

**（d）`_validate_hit_reaction`**（L148-179）：L170-177 feature 分支调用改为传 `decision["damage_event"]` 与 `decision.get("next_decision")`——校验逻辑零变化（重算 choices 按 feature_id 查找，注册表条目自动覆盖；counterattack 与 next_decision 并存的非法组合因 offer 层排除而在重算中自然不存在，无需新校验分支）。

**（e）`_finish_hit_reaction`**（L181-268）feature 分支按 `choice["effect_kind"]` 分发：

- `damage_multiplier`：L191-218 现逻辑原样保留（含 next_decision held 减半同步）；
- `counterattack`（新增分支），事件序列严格如下：
  1. `dnd2024.decision.cleared`（复用 L186-189 头部）；
  2. **原始伤害全额落地**：`events.extend(self._submit_hit_damage(decision["damage_event"]))`（不减半、不改 delta 符号；内含攻击者 feature_use 弹出记账）；
  3. `_concentration_events(defender, amount, rng)`（防守者受伤专注豁免，与 UD 分支 L217 对称）；
  4. **存活预判**：`defender_hp - amount > 0`（落地后状态预判；hp 恰到 0 = 倒地不能反应）→ 不满足则直接返回（**未发 reaction.spent、反应预算保留、无反击事件**）；
  5. **反击意图**：`melee_attacks = [a for a in self._available_attacks(defender) if int(a.get("range") or 0) <= 5]`（借机攻击 L1194-1202 同款；defender 恒为 player/companion，徒手打击殿后保证非空，仍保留空防御守卫 fail closed）；`attack_intent = {intent_id: f"{decision['decision_id']}:retaliation", actor_id: defender_id, target_id: attacker_id, weapon_ref: ...}`；
  6. `attack_events = self._attack_events(instance, combat, attack_intent, rng)`——内部全部通用行为天然正确：`spell_end_trigger("attack_roll")`（隐形反击者终止隐形）、check.resolved kind="attack"（优劣势/bless 通用管线）、rage sustained 不触发（economy 属攻击者回合）、hex/sneak/暴击/rage_damage/抗性结算、`_hit_spell_offer` 因非本人回合返回 None、`_hit_reaction_offer` 因目标 kind=enemy 返回 None → 直通落地 + 被反击者专注豁免 + victory 检查；
  7. **cost 替换**（借机攻击 L1210-1217 模式）：定位 `dnd2024.attack.spent` 事件替换为 `{"type": "dnd2024.reaction.spent", "actor_id": defender_id, "reason": "retaliation"}`（不覆盖列表头部——法术提前终止事件在支付之前）；`events.extend(替换后的 attack_events)`；
  8. return。

**（f）`_automatic_hit_reaction`**（L270-299）AI 阶梯升级（L296-298）：

- damage_event.amount > 0 前提保留；
- features 中第一条 `effect_kind == "counterattack"` 条目优先（确定性规则：反击期望收益高于减半，且存活守卫在 resolve 内兜底，AI 不会白花反应）；
- 无 counterattack 条目时保持现行为（features[0]）；无 features → decline。

### 3.3 `tests/rulesets/test_dnd2024_uncanny_dodge.py`

仅 L127-132 的 choice 全等断言追加 `"effect_kind": "damage_multiplier"` 键（注册表化行为不变守护）；其余断言与用例零改动。

### 3.4 `tests/rulesets/test_dnd2024_retaliation.py`（新建）

fixture 对齐 `test_dnd2024_uncanny_dodge.py` 搭建（`SequenceRng` / `advance` / `monk_instance` / `start_combat`，来自 `dnd2024_monk_common`）：

- `_barbarian_sheet`：`runtime.finalize_character` 后 `advance(runtime, sheet, 10)`；quick_presets 中无野蛮人预设时手工 draft `class:barbarian`（执行侧核实 builder 预设清单后取最短路径）；
- 9 级对照用 `advance(..., 9)`。

用例清单见 §4。

### 3.5 `tests/rulesets/test_dnd2024_class_features.py`

对齐 L583-610 UD 三件套，追加 retaliation 声明守护：`test_retaliation_declares_no_capability_and_keeps_parameters`（无 capabilities、parameters 恰 `{"counterattack_range": 5}`）与 `test_retaliation_is_not_owned_below_level_ten`（minimum_level 10）。

### 3.6 明确零改动文件

`validation.py`、`reducer.py`、`engine.py`、`view.py`、`check_modes.py`、`triggered_spells.py`、`resolution.py`（**本切片不改 `_attack_events` 与借机攻击路径**）、`srd_subclasses.json`、`srd_classes.json`、`srd_recovery.json`（无新池）、前端与 feed 投影、`check_windows.py`（SP3 产物，不碰）。

---

## §4 测试矩阵

### 4.1 新用例（`test_dnd2024_retaliation.py`）

| # | 用例 | 断言要点 |
|---|---|---|
| R1 | 10 级狂战士被 5 尺内敌方命中 | pending kind=HIT_REACTION、options=["feature","decline"]、features 全等于 [{feature_id: retaliation, name, mode: reaction_defense, effect_kind: counterattack, counterattack_range: 5}]、damage_event.amount 为预掷全额、反应预算未扣 |
| R2 | resolve feature(retaliation) 命中 | 事件序：cleared → 原始 resource.changed 全额落地（防守者 hp 减全额）→ check.resolved kind=attack（反击 d20，target=攻击者 AC）→ dnd2024.reaction.spent（reason=retaliation）→ 反击 resource.changed 落地（攻击者 hp 减）→ 防守者 reactions=0 |
| R3 | 反击骰落空 | 原始伤害照常全额落地；有 check.resolved(success=false)；无反击 damage 事件；反应已扣（RAW：反击是完整攻击，失手也花了反应） |
| R4 | 原始伤害致死预判 | 预构造 hp 使 amount ≥ hp：cleared + 原始伤害落地 + 无 reaction.spent、无反击 check.resolved、防守者 reactions 保持 1 |
| R5 | 9 级狂战士被命中 | 无 decision offer（幽灵未授予，直通全额伤害） |
| R6 | 反应预算已耗尽 | 无 offer（公共守卫），直通 |
| R7 | 攻击者位于 10 尺（远程档案命中） | 无 retaliation 条目、无 offer（无 UD 的角色）；构造 rogue5/barbarian10 多职表验证同一窗口 UD 在而 retaliation 不在，则按 fixture 成本降级为 `_reaction_feature_choices` 直调断言（白名单允许） |
| R8 | 狂暴中反击命中 | 反击 damage 事件含 rage_damage 加值（骰值断言）；事件流无 `dnd2024.condition.sustained`（不延长狂暴） |
| R9 | AI 控制玩家被命中 | `_automatic_hit_reaction` 返回 option=feature/retaliation；resolve 事件流同 R2 |
| R10 | damage_event.amount = 0（全抗归零场景） | 无 offer / 无 counterattack 条目（"没有造成伤害"不触发） |

### 4.2 回归守护（现有文件，除 3.3 外零改动、预期全绿）

`test_dnd2024_uncanny_dodge.py`（全套，含 L127-132 键扩展后同步）、`test_dnd2024_sneak_attack.py` L549、`test_dnd2024_stoneskin.py` L575、`test_dnd2024_origin_feat_spells.py` L143/L216、`test_dnd2024_combat.py` L224（借机攻击）、`test_dnd2024_hold_person.py` L612、`test_dnd2024_class_features.py`（现有全套 + 新增声明守护）。

---

## §5 验收命令（PowerShell，无 `&&`）

```powershell
python -m pytest tests/rulesets/test_dnd2024_retaliation.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_stoneskin.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_class_features.py -q
```

全绿后附加回归面（若环境已有全量战斗测试基线）：

```powershell
python -m pytest tests/rulesets -q
```

## §6 禁止事项

1. 禁止新增决策种类 / payload 字段 / reaction reason 枚举校验（复用 HIT_REACTION_DECISION；reason 为自由文本先例：opportunity_attack）。
2. 禁止改动 `_attack_events` 掷骰结构、RNG 顺序（SP2 §3.8 契约：反击骰在窗口 resolve 之后的完整攻击结算内，不进窗口骰族）与借机攻击路径。
3. 禁止在注册表之外新增任何硬编码特性分支（fail closed：未知 feature_id / 非法参数即不 offer）。
4. 禁止实现反击嵌套反应递归（不变式守护）；禁止给 enemy kind 开受击反应能力。
5. 禁止实现 Frenzy / Mindless Rage / Intimidating Presence（§1.4 裁剪）。
6. 禁止弹武器选择子决策、禁止改 `validation.py` / `reducer.py` / 前端。
7. 禁止触碰 P7/P8 在途区域（`resolution.py` L593+、`validation.py` L470+）与未跟踪的 `test_dnd2024_dispel_magic.py`。
8. 禁止把 choice 的 `mode` 键改值或删既有键（前端兼容）；effect_kind 只增不改。

## §7 停止条件

1. 开工时 `git status` 与 §2.2 状态不符（P 系列已合入 / 偏移失效 / 新的未跟踪文件出现在本切片白名单内）→ 停止，重读基线并按"并行基线重读"协议更新锚点后再继续。
2. §2.1 任一锚点行号/结构与实际不符（P7/P8 重构了 `_attack_events` / `_finish_hit_reaction`）→ 停止，请示而非猜测。
3. UD 回归（§4.2）出现任何非 L127-132 键扩展所致的红 → 停止（注册表化行为不变是硬约束）。
4. 反击结算需要改动 `validation.py` / `reducer.py` / 新决策种类才能通过测试 → 设计偏离，停止请示。
5. 与 SP3 已落地的 `check_windows.py` 或 SP10 的统一攻击加值管线发生重叠改动 → 停止请示。
6. `srd_class_features.json` barbarian 段结构与 §2.1 锚点不符（P 系列已动过目录）→ 停止重核。

## §8 执行报告模板与迁移债登记

### 执行报告（执行侧填写）

- 改动文件清单（对照 §3 白名单，逐文件：实际 diff 摘要 / 与规格的偏差及原因）；
- §4 矩阵逐用例结果（R1-R10 + 回归守护清单，含 pytest 摘要行）；
- §5 验收命令原样回贴（两次）；
- 行号偏移复核结果（§2.2 是否仍成立）；
- 未尽事项与建议。

### 迁移债登记（本切片产生的显式债务，编号接续 SP 系列）

| 债 | 内容 | 归属 |
|---|---|---|
| D1 | counterattack 与 next_decision（Smite held）并存的反击时序（当前阵容不可达：触发前提要求伤害已落地，而 held 伤害未落地） | 伤害吸收反应 / 命中触发扩展批次 |
| D2 | 反击武器的玩家选择子决策（当前确定性取第一件近战档案，含徒手殿后） | 阶段二 UX 批次 |
| D3 | enemy kind 的受击反应能力注册表化（当前公共守卫 kind 过滤恒排除敌方） | 敌方能力批次（阶段二） |
| D4 | （销账确认）SP3 登记的 `_reaction_feature_choices` 硬编码注册表化——本切片 §3.2(b) 交付，执行报告需显式确认销账 | 本切片 |
| D5 | Intimidating Presence（14 级：30 尺光环 + 感知豁免恐慌 + 长休/狂暴重置）——光环（S-P6）+ 每回合豁免 tick（P2 域）+ 使用次数重置原语的组合 | S-P6 评估后归阶段二批次 |
| D6 | Frenzy（3 级鲁莽回合首次力量命中的狂暴加值骰）与 Mindless Rage（6 级狂暴中魅魅/恐慌免疫）——分别为条件伤害 rider 族与条件免疫族 | 阶段二内容批次 |
