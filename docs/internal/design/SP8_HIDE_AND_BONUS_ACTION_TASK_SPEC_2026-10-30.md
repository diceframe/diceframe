# 切片 S-P8 任务书：Hide 动作与附赠动作路由（Cunning Action / Fast Hands）

版本：v1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片改动 `combat/resolution.py` / `combat/validation.py` / `combat/engine.py`，与 1B 进行中的 P6（misty_step）同文件，**必须在 P6 合入、工作区干净后开工**。**硬依赖 S-P1**：fast_hands 是子职表特性，其机械可见性依赖 `features_for` 的子职所有权合并；且 `srd_class_features.json` / `test_dnd2024_class_features.py` 与 S-P1 及第一批各切片同文件。S-P4 / S-P5 / S-P7 与本切片触碰同类文件，串行执行，不得并行开改。本任务书锚点基于 `1009be03`，执行侧须对受 P6 影响的文件自行重核行号。

---

## 1. 目标

为 D&D 2024 规则集打通 **躲藏动作（Hide）与附赠动作路由** 的完整闭环：hide 作为通用战斗动作 intent → 隐匿检定 → 隐形状态（复用 P1 invisibility 全部既有机制）→ 游荡者灵巧动作（Cunning Action）以附赠动作执行疾走/撤离/躲藏 → Thief 快手（Fast Hands）特性声明 → 测试。

本切片沉淀两项通用原语：

1. **Hide 动作（hide intent）**：全职业动作（动作表成员），结算为一次敏捷（隐匿）检定，成功施加 `invisible` condition。后续一切"躲藏类"内容（刺客子职、Skulker 专长）复用同一 intent 与结算，不另建管线。
2. **附赠动作路由（bonus-action action routing）**：`ACTION_KINDS` 扩展 `hide` 后，capability 声明 `cost: {"bonus_action": 1}` + `actions: [...]` 即可把**任意已注册规范动作**路由到附赠动作槽——经济扣减走既有 `dnd2024.action.spent` 事件通用落账。Cunning Action 是该原语的首个三动作消费者；后续（如 Fast Hands 的物件使用、其它职业的附赠动作族）只扩目录不扩引擎。

**规则语义（2024 版中文规则书原文，已核实）**：

- 动作表·躲藏（动作.htm L17）："进行一次**敏捷**（**隐匿**）检定。"
- 附赠动作（附赠动作.htm L8）："只有在某个特殊能力、法术或游戏中其他明确了你能以附赠动作来做某事的特性的允许下，你才能执行附赠动作。否则，你没有可以执行的附赠动作。你只能在自己的回合执行一个附赠动作……任何能够让你无法执行动作的效应，同样会使你无法执行附赠动作。"
- 灵巧动作（游荡者.htm L105-106，2 级）："你精妙的思维与身手使你的行动无比迅速。在你的回合内，你能够以附赠动作执行疾走，撤离或躲藏动作之一。"
- 快手（盗贼.htm L16-25，3 级）："你可以通过附赠动作来进行下列行为中的一个：**巧手 Sleight of Hand**——进行一次敏捷（巧手）检定来利用盗贼工具解开一个锁或解除陷阱，或是使用这个检定进行扒窃；**使用物件 Use an Object**——执行操作动作，或是执行魔法动作来使用一个要求该动作的魔法物品。"

出处：

- `.rulebooks/dnd5r/玩家手册2024/进行游戏/动作.htm` L17（躲藏动作）、L23（操作动作）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/附赠动作.htm` L8（附赠动作经济）；
- `.rulebooks/dnd5r/玩家手册2024/角色职业/游荡者/游荡者.htm` L63（特性表 2 级灵巧动作）、L105-106（灵巧动作全文）；
- `.rulebooks/dnd5r/玩家手册2024/角色职业/游荡者/盗贼.htm` L16-25（快手全文）。

### 1.1 必须锁定的规则边界

1. **Hide 的动作本体只有一份结算**：无论消耗动作还是附赠动作，躲藏的检定与隐形施加语义完全一致；成本差异是路由层（`_basic_action_events` / capability cost）的事，不在 `_hide_events` 内部分叉。
2. **隐匿检定**：`actor["skill_values"].get("stealth", 0)`——玩家技能加值由角色卡 `proficiencies.skill_values` 投影（含属性调整值与熟练），引擎不重复计算敏捷加成，不臆造熟练。
3. **DC 采用被动察觉的数据代理**：`DC = 10 + max(存活敌方单位的 wis 豁免调整值)`（敌对 wisdom saving throw modifier），无存活敌方单位时 DC = 10。2024 RAW 是 DM 裁决、通常对抗被动察觉（10 + 感知（察觉）加值）；战斗视图没有察觉熟练数据，wis 豁免调整值是最接近的既有权威数据。此近似**在本任务书内声明**，不留给执行侧猜测；改进空间记录为迁移债（§8）。
4. **成功即 `invisible`，复用 P1 机制，零新状态**：`condition.applied` 施加 `invisible`，元数据 `ends_on: ["attack_roll", "spell_cast"]`（与 invisibility 法术同款触发码）、`duration: "combat"`（描述性值：无回合到期处理，靠 ends_on 终止，战斗结束自然丢弃）、`source_actor_id` 为自己。攻击优劣势（打人优势、被打劣势、`negated_by` see_invisibility/truesight）全部由 P1 的 `attack_modes` 数据驱动，本切片零引擎改动。
5. **失败无惩罚**：只有检定事件，无 condition.applied，无负面状态。
6. **失败/成功都不消耗移动**，且躲藏不要求掩护——"环境是否适合躲藏由 DM 决定"（躲藏.htm L8）属叙事层裁决，战斗引擎以检定为唯一入口，不做掩护/视线系统。
7. **失能门控对齐既有动作族**：`incapacitated` 的 intent 拒绝集合加入 `hide`；hp=0 只能死亡豁免的既有约束自动覆盖。附赠动作与动作同受此门控（附赠动作.htm L8 原文）。
8. **每回合一个附赠动作**：economy 既有 `bonus_action: 1` 池 + reducer 通用落账 + capability availability 校验，零新机制。Cunning Action 三个能力各是独立 capability（一特性多 capability 的既有先例：monk 的 step_of_the_wind_focus / patient_defense_focus）。
9. **Cunning Action 只路由三个既有动作**：疾走（dash）、撤离（disengage）、躲藏（hide）。规则原文不含回避（dodge）。
10. **隐形不叠加**：已 `invisible` 时再次躲藏成功，condition meta 被 reducer 既有行为覆盖刷新（`ends_on` 续期），不产生双状态。测试锁定该行为；若 reducer 对同名重复施加的行为与预期冲突，触发停止条件（§7）。

### 1.2 本切片的产品策略与裁剪

- **Fast Hands 只做所有权声明 + 标签，capability 显式推迟**：快手的两个选项——巧手检定（解绑/解除陷阱/扒窃）与使用物件（操作动作/魔法物品魔法动作）——在当前战斗引擎内**均无可结算的消费对象**（无锁具/陷阱管线、无物件/消耗品动作管线，后者是 1D 物品原语的范围）。本切片声明 `fast_hands`（3 级 thief 子职表已有 id）使所有权与标签可见，其 capability 注册推迟到 1D 物品管线落地（`utilize` / `sleight_of_hand` 两个 ACTION_KIND 与消费对象一起引入），迁移债记录于 §8。**禁止在本切片臆造巧手检定的战斗内 DC 或效果。**
- **敌对单位不使用 hide**：hide intent 不做 kind 门控（与 dash/dodge 一致对任意 actor 开放），但敌人数据 `skill_values` 为空 dict、AI 决策层不产生 hide intent——不为敌人新增隐匿数据，不做敌人躲藏 AI。
- 躲藏的"被找到后重躲""离开视线才可重躲"等 DM 裁决细节不做：战斗引擎的隐形终止完全由 `ends_on` 触发码决定。
- UI 侧零新增交互面：hide 作为一个 hosted choice 按钮出现，与 dodge/dash 同款。
- 稳定瞄准（Steady Aim）、诡诈打击家族、梁上君子、神行太保等游荡者其它特性不在本切片；`next_attack_advantage` condition 的既有 `consume_on` 机制与本切片正交，不动。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| intent 分发：`elif intent_type in {"dash", "dodge", "disengage"}: events.extend(self._basic_action_events(...))`，各 intent 分支依次为 class_capability / cast_spell / move / 基础动作 / end_turn / death_save / stabilize / decision | `combat/engine.py` L640-657 |
| hosted choices 恒定动作选项 `for kind in ("end_turn", "dodge", "disengage", "dash"):`，逐个 validate 后入列 | `combat/hosted_choices.py` L32 |
| validation intent 门控三集合：失能禁 `{"attack", "cast_spell", "class_capability", "dash", "dodge", "disengage", "stabilize"}`；经济校验 `{"attack", "dash", "dodge", "disengage", "stabilize"}` 要求 action 或 surge_action 余量；kind 白名单 | `combat/validation.py` L280-300 区域 |
| 施法的附赠动作先例：`cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"` | `combat/validation.py` L427、`combat/view.py` `_available_spells` |
| economy 结构：`{"action": 1, "bonus_action": 1, ...}` 每回合刷新 | `combat/engine.py` L1006-1012 |
| `dnd2024.action.spent` reducer 通用落账：按 `event["resource"]` 扣对应经济池，余量不足 `EventBatchError`（fail closed） | `combat/reducer.py` L132-139；池 id 列表 L15（action/bonus_action/reaction/movement/surge_action） |
| `_spend_action_resource`：非魔法动作的付费池（action 优先，surge_action 兜底） | `combat/resolution.py` L803-813 |
| `_basic_action_events(combat, intent_type, actor_id, spend_action=True)`：action.spent + dash 授予移动力 / dodge·disengage 施加 condition（duration `actor_turn_start`） | `combat/resolution.py` L815-841 |
| capability 结算批：`cost.action` 透传为 action.spent 的 resource；`actions[]` 分发——`unarmed_strike` 逐次调 `_attack_events(spend_attack_action=False)`，`kind in {"dash", "dodge", "disengage"}` 调 `_basic_action_events(spend_action=False)` | `combat/resolution.py` L843-900（`_class_capability_events`） |
| P1 隐形机制：`invisible` condition `attack_modes`（target 劣势 + actor 优势，negated_by see_invisibility/truesight）；invisibility 法术 `ends_on: ["attack_roll", "damage_dealt", "spell_cast"]`；`ends_on` 元数据随 condition.applied 落账（L611-612）；`_spell_end_trigger_events` 从 pre-resolution 视图读 meta，无 concentration_owner 的 condition 单独移除；触发点在攻击分支 L273 与施法分支 L376 | `templates/.../combat_catalogs/srd_combat_core.json` L73、L90-93；`combat/resolution.py` L119-157、L273、L376、L611-612 |
| condition duration 无封闭值域校验：法术 `condition_duration` 是自由字符串直传 condition.applied 的 duration；reducer 只对已知值（`actor_turn_start`、`until_healed` 等）做到期处理 | `combat/catalog.py` L117-120（ends_on 校验）；`combat/resolution.py` L606；`combat/reducer.py` L332；`combat/engine.py` L1157 |
| 玩家 `skill_values` 从角色卡 `proficiencies.skill_values` 投影；敌人 `skill_values` 为空 dict；敌人豁免调整值在 `saving_throws`（法术豁免分支的消费先例 L480） | `combat/view.py` L85、L126-132；`combat/resolution.py` L480 |
| 检定事件先例：`check.resolved kind="medicine"`（stabilize：rolls/natural/modifier/total/target/success 六件套 + skill_values 取加值 + 固定 DC 10） | `combat/resolution.py` L1101-1113 |
| `ACTION_KINDS = {"unarmed_strike", "dash", "dodge", "disengage", "heal", "rage", "rage_sustain", "action_surge"}`；`ACTION_COST_KINDS = ("bonus_action", "action")`；cost 单动作、值必须 1；未知 kind fail closed | `features/models.py` L27-31、L306-360（`_parse_capability`） |
| capability JSON 先例（bonus 动作成本 + 多 capability 单特性）：monk `flurry_of_blows` / `patient_defense` / `step_of_the_wind` 系列，`cost: {"bonus_action": 1, "resources": [...]}` | `templates/.../class_feature_catalogs/srd_class_features.json` L69-120 |
| 游荡者主职业表 2 级已含 `cunning_action` id（progression granted）；rogue 现有声明仅 sneak_attack + uncanny_dodge | `templates/.../progression_catalogs/srd_classes.json` L189-190；`templates/.../class_feature_catalogs/srd_class_features.json` L207-233 |
| Thief 子职表 3 级已含 `fast_hands` id（子职目录零改动） | `templates/.../subclass_catalogs/srd_subclasses.json` L60 |
| capability availability：`features.combat_capabilities(character, economy, ...)` 已按 cost 与 economy 判可用（monk bonus 系列在工作即证） | `combat/view.py` L364-378；`features/resolver.py` |
| 测试基建：`SequenceRng` 序列控骰；`advance(runtime, sheet, level)` 高等级构造；战斗搭建 `from tests.rulesets.test_dnd2024_combat import _instance, _start, SequenceRng` | `tests/rulesets/dnd2024_monk_common.py` L23；`test_dnd2024_sneak_attack.py` L26-32；`test_dnd2024_narrative_health.py` L12 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

`classes.rogue.features` 追加两条声明（字段形状以同文件 monk 既有声明为准）：

```json
{
  "id": "cunning_action",
  "minimum_level": 2,
  "source_ref": "srd-5.2.1:p63:cunning-action",
  "summary": "You can take the Dash, Disengage, or Hide action as a Bonus Action.",
  "capabilities": [
    {"id": "cunning_dash", "cost": {"bonus_action": 1}, "requires_hostile_target": false,
     "actions": [{"kind": "dash"}]},
    {"id": "cunning_disengage", "cost": {"bonus_action": 1}, "requires_hostile_target": false,
     "actions": [{"kind": "disengage"}]},
    {"id": "cunning_hide", "cost": {"bonus_action": 1}, "requires_hostile_target": false,
     "actions": [{"kind": "hide"}]}
  ]
}
```

```json
{
  "id": "fast_hands",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p64:fast-hands",
  "summary": "You can use a Bonus Action to make a Sleight of Hand check or to take the Utilize action.",
  "parameters": {}
}
```

（fast_hands 无 `capabilities`——显式迁移债，见 §1.2。）

`labels` 追加 `"cunning_action": "Cunning Action"`、`"fast_hands": "Fast Hands"`。子职目录与主职业 progression 目录零改动。

### 3.2 locale 文件（两个）

`locales/en/class_feature_catalogs/srd_class_features.json` 与 `locales/zh-CN/class_feature_catalogs/srd_class_features.json` 的 labels 同步追加；zh-CN 用规则书译名：灵巧动作、快手。

### 3.3 `src/rulesets/dnd2024/features/models.py`

`ACTION_KINDS` 追加 `"hide"`。其余（fail closed 校验、cost 解析）不动。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

1. 新增私有 `_hide_events(self, instance, combat, intent, rng, *, spend_action=True) -> list[dict]`：
   - `spend_action=True` 时发 `dnd2024.action.spent`（`resource: self._spend_action_resource(combat)`）；
   - `stealth = int(actor["skill_values"].get("stealth", 0) or 0)`（actor 经 `_actor_view`）；
   - `dc = 10 + max(存活敌方单位 view 的 saving_throws["wis"], default=0)`——敌方单位取 `combat["enemies"]` 中 hp>0 者的 `_actor_view`；无存活敌方单位时 DC 为 10；
   - `roll = int(rng.randint(1, 20))`；`success = roll + stealth >= dc`；
   - 检定事件（medicine 先例六件套 + `ability: "stealth"`）：`{"type": "check.resolved", "kind": "hide", "actor_id": ..., "rolls": [roll], "natural": roll, "modifier": stealth, "total": roll + stealth, "target": dc, "success": success}`；
   - `success` 时追加：`{"type": "condition.applied", "target_id": actor_id, "condition": "invisible", "duration": "combat", "source_actor_id": actor_id, "ends_on": ["attack_roll", "spell_cast"]}`；失败只有检定事件。
2. `_class_capability_events` 的动作分发区（基线 L893 附近）：在 `elif action.kind in {"dash", "dodge", "disengage"}` 之后追加 `elif action.kind == "hide": events.extend(self._hide_events(instance, combat, {"actor_id": actor_id}, rng, spend_action=False))`。

### 3.5 `src/rulesets/dnd2024/combat/engine.py`

intent 分发（基线 L647-648）追加分支：

```python
elif intent_type == "hide":
    events.extend(self._hide_events(instance, combat, intent, rng))
```

（签名/调用形状与相邻 `_death_save_events` 分支一致；P6 合入后重核。）

### 3.6 `src/rulesets/dnd2024/combat/hosted_choices.py`

恒定动作选项元组（L32）改为 `("end_turn", "dodge", "disengage", "dash", "hide")`。

### 3.7 `src/rulesets/dnd2024/combat/validation.py`

1. intent kind 白名单（基线 L286）追加 `"hide"`；
2. incapacitated 拒绝集合（基线 L289）追加 `"hide"`；
3. 经济校验集合（基线 L293，要求 action/surge_action 余量）追加 `"hide"`。
4. hide 无 target，不加 `_validate_attack` 类特殊校验。

### 3.8 测试

新文件 `tests/rulesets/test_dnd2024_cunning_action.py`：战斗搭建照 `test_dnd2024_narrative_health.py` L12 先例；游荡者构造照 `test_dnd2024_sneak_attack.py` 的 `advance` 法；隐匿加值经角色卡 `proficiencies.skill_values` 注入（测试直接构造，不为测试改生产投影）。测试矩阵见 §4。

允许的最小既有测试更新：`tests/rulesets/test_dnd2024_class_features.py` 若存在"枚举 owned 特性全集"断言，按本切片预期追加 cunning_action / fast_hands；`tests/rulesets/test_dnd2024_combat.py` 若存在 hosted choices 恒定列表断言，追加 hide。**不得**改动与本切片无关断言的语义。

回归命令涉及：`test_dnd2024_class_features.py`、`test_dnd2024_combat.py`、`test_dnd2024_invisibility.py`（P1 ends_on / attack_modes 回归）、`test_dnd2024_monk_resources.py`（capability bonus 经济回归）、`test_dnd2024_rage.py`、`test_dnd2024_sneak_attack.py`。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录加载：cunning_action / fast_hands 声明 + 三语言 labels | catalog 可读，`has_feature` 前提成立 |
| 2 | 普通动作 hide：游荡者（隐匿 +7）执行 hide intent，SequenceRng 固定骰 | `action.spent` resource=action；`check.resolved kind=hide` 六件套齐、total=骰+7；`condition.applied invisible` + `ends_on` meta 落账；economy.action 减 1、bonus_action 不变 |
| 3 | hide 失败：敌方 wis 高、骰低 | 检定事件 `success=False`；无 `condition.applied` |
| 4 | DC 代理：两个存活敌人 wis 调整值 +0 与 +3 | `check.resolved target == 13`（取最高） |
| 5 | 隐形攻击终止：hide 成功后攻击 | 攻击结算后 invisible 被移除（P1 `ends_on` attack_roll 路径回归）；本次攻击带 advantage（actor 侧 attack_modes 生效） |
| 6 | 隐形施法终止：hide 成功后施法 | invisible 被移除（spell_cast 触发） |
| 7 | 被打劣势：hide 后敌人攻击我 | 敌方攻击事件带 disadvantage（target 侧 attack_modes） |
| 8 | cunning_dash：2 级游荡者用 capability | `action.spent` resource=bonus_action、economy.bonus_action 减 1 且 action 不减、`dnd2024.movement.granted` 发生 |
| 9 | cunning_hide：bonus 版躲藏成功 | bonus_action 减 1；检定 + invisible 同 #2；普通动作未消耗 |
| 10 | 一回合一个附赠动作：cunning_dash 之后 cunning_disengage | validation 拒绝（availability blocked reason 含经济不足） |
| 11 | 等级门控：1 级游荡者 | `has_feature(cunning_action)` False、capability 视图不含三能力；2 级 True/可见 |
| 12 | fast_hands 所有权：advance 到 3 级的游荡者（thief 子职链） | `has_feature(fast_hands)` True；capability 视图无 fast_hands 能力（声明无 capabilities） |
| 13 | 失能门控：paralyzed 单位提交 hide intent | validation 拒绝（incapacitated 集合） |
| 14 | 重复躲藏：已 invisible 再 hide 成功 | 无异常批次；condition meta 刷新（`ends_on` 仍可触发），无双状态 |
| 15 | 职业隔离：战士 18 级 capability 视图 | 无任何 cunning_* 能力 |
| 16 | hosted choices：2 级游荡者回合选项 | 选项列表含 type=hide 的 intent，且 validation 通过 |
| 17 | 经济门控：普通动作已花光（先攻击）后 hide intent | validation 拒绝（action 与 surge_action 皆 0） |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_cunning_action.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_invisibility.py tests/rulesets/test_dnd2024_monk_resources.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_sneak_attack.py -q
```

（`test_dnd2024_character_builder.py` 上游既有失败不计。）

## 6. 禁止事项

1. 禁止为躲藏新建第二个隐形状态、新优劣势机制或新终止机制——`invisible` condition 与 P1 的 `attack_modes` / `ends_on` 是唯一权威。
2. 禁止在 resolution / validation / models 出现 `cunning_action` / `fast_hands` 字符串分支——路由完全由目录声明（cost + actions.kind）驱动。
3. 禁止在本切片给 fast_hands 臆造 capability：不发明巧手检定的战斗内 DC，不实现"使用物件"（1D 物品原语范围）。
4. 禁止给敌人新增 stealth 数据或敌人躲藏 AI——hide intent 对敌人无 skill_values 加成属预期行为。
5. 禁止绕过 economy：cunning_* 必须走既有 capability availability 校验与 action.spent 落账，不得直改 economy。
6. 禁止修改 `_basic_action_events` 的 dodge/disengage 语义与 dash 的移动力授予。
7. 禁止实现稳定瞄准、诡诈打击、梁上君子、极效潜行、使用魔法装置、盗贼反射等其它游荡者特性。
8. 禁止把 hide 的 DC 做成可配置公式或 UI 选项——它是引擎内建的数据代理（§1.1 第 3 条），改进走迁移债流程。

## 7. 停止条件

1. P6 或 S-P1 尚未合入，基线与本任务书锚点无法对齐；
2. reducer 对同名 condition 重复施加的行为不是"覆盖刷新 meta"（测试 #14 暴露报错或状态异常）——报告并等待裁决；
3. `_spell_end_trigger_events` 对无 `concentration_owner` 的 condition 不生效（P1 机制假设失效，测试 #5/#6 失败且根因在既有代码）——报告并等待裁决，不得顺手修 P1；
4. capability availability / validation 的既有形状与锚点描述冲突（如 P6 重构 intent 分发）；
5. hosted choices 对新动作选项存在白名单外契约约束（前端/投影显式拒绝 type=hide 的 intent）；
6. `srd_class_features.json` 的 rogue.features 中 cunning_action / fast_hands 已被其它并行切片声明（工作区冲突信号）。

## 8. 执行报告模板

执行完成后在本文件追加"执行报告"节：变更文件清单、测试矩阵逐项结果、偏差与遗留（DC 代理的最终实现、check.resolved 的 kind 命名、duration 值、hosted choices 断言更新范围）、迁移债确认（Fast Hands capability 待 1D 物品管线引入 utilize/sleight_of_hand 动作；被动察觉代理待敌人技能数据扩展后升级；掩护/视线的 DM 裁决语义属叙事层；48 子职后所有权须 subclass_ref 感知）。
