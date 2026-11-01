# 切片任务书 S-P10：条件检定加值统一管线（Archery +2 / Bless 1d4 泛化）

- 阶段：1C 共用原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1C 切片表 S-P10 ——"条件攻击加值（平坦值或骰式）｜Archery +2、Bless +1d4；区别于 advantage/disadvantage 模式，必须进入统一攻击加值与骰迹管线"
- 前置依赖：无硬切片依赖；与 S-P1 的 resolver 目录校验风格保持一致。1B 法术原语（P 系列）与本文档并行推进时，所有 combat 基线读取以 `git show HEAD` / `git grep ... HEAD` 为准，行号以基线提交为准，改动前先重读工作区版本核对漂移。
- 状态：未开工

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **箭术战斗风格（Archery）**：
   - 出处：`.rulebooks/dnd5r/玩家手册2024/专长/战斗风格专长.htm` L11-13 ——"箭术 Archery，战斗风格专长（先决：战斗风格特性）你使用远程武器进行的攻击检定获得+2加值。"
   - 授予入口：`.rulebooks/dnd5r/玩家手册2024/角色职业/战士/战士.htm` L68-69 ——战士 1 级"战斗风格"特性：获得一项战斗风格专长（先决条件即该特性本身）。
   - 范围限定：加值仅作用于**远程武器**的攻击检定；投掷一把近战武器（`ranged_use` 但非 `is_ranged_weapon`）**不**适用，与本仓库 resolution 既有注释（"真正的远程武器 vs 近战武器被投掷……属性选择只应被前者排除"）的语义划分一致。
2. **祝福术（Bless）加值范围**：
   - 出处：`.rulebooks/dnd5r/玩家手册2024/法术详述/1环.htm` L33 ——"在法术终止前，受术目标进行的每次**攻击检定与豁免检定**都将获得**1d4**的加值。"
   - 结论：加值范围 = 攻击检定（含法术攻击检定）与豁免检定（含回合结束重复豁免、专注豁免），**不含属性检定**。现有 6 处消费点全部落在合法范围内，泛化**不引入行为收窄**。
3. 统一管线要求（主计划原文）："区别于 advantage/disadvantage 模式，必须进入统一攻击加值与骰迹管线"：
   - 平坦加值并入聚合加值（modifier），不新增 advantage/disadvantage 分支；
   - 骰式加值必须有可审计骰迹（每枚加值骰独立记录），RNG 消耗顺序锁定。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **裁剪：升期战斗风格选择**。paladin 2 级、ranger 2 级的 fighting_style 特性与 champion 7 级 additional_fighting_style 是升级期获得（`srd_classes.json` L156/L174 progression、`srd_subclasses.json` L36），升期 choices 协议扩展不在本切片。本切片只做**建卡期 fighter 1 级**的选择落账。
2. **裁剪：前端建卡表单**。draft 新键的后端协议（validation + derivation + 测试）本切片完成；建卡 UI 的选择控件列为迁移债。
3. **裁剪：其余三个战斗风格专长的机械**。advancement catalog 现有 4 个 fighting_style 条目（archery/defense/great_weapon_fighting/two_weapon_fighting）。本切片只为 archery 加机械参数并打通消费；其余三个的 parameters 与消费路径归各自归属切片（defense → AC 管线即 S-P5 族；two_weapon_fighting → 攻击经济；great_weapon_fighting → S-P2 骰面操纵）。它们可以随后续切片在**同一参数 schema** 上扩展，不需要新的管线。
4. **裁剪：骰式加值多源叠加去重**。同一 condition 在 conditions 映射中是单实例（id → payload），同一加值源不会重复掷骰；不同源（如未来新增骰式加值 condition）按目录顺序逐源掷骰求和，不设上限截断。
5. **保留现状：blessed 不含属性检定**。ability check 路径（hide 隐匿检定等）不消费骰式加值；`"ability_check"` 语义 kind 在目录 schema 中预留但本切片无消费点，等 P10 属性检定阶段再接。
6. **升级期 general/epic feat 选择不涉及本切片**：`_advancement_feat` 的 `expected_category` 只认 `general`/`epic_boon`，fighting_style 类别在升级期本就不可选（现状正确），不改动。
7. **平攻与骰式的事件字段**：Archery +2 并入 `modifier`（先例：武艺 ability_choice 影响 modifier、rage_damage 并入伤害 AST，均无独立 provenance 字段）；骰式加值泛化为 `check_bonus`（总额）+ `check_bonus_rolls`（骰迹），**移除 `bless_bonus` 字段**（波及清单见 §3.11/§3.12）。

## 2. 代码事实锚点（基线 HEAD，行号随工作区漂移需重读核对）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/combat/resolution.py` L200-262 | attack 分支：`weapon = self._declared_attack(actor, intent)`；`is_ranged_weapon = bool(weapon.get("ranged"))`；`ranged_use = is_ranged_weapon or distance > 5`；`attack_bonus = modifier + proficiency`；武器档案含 `finesse`/`ability_choice`/`category`/`unarmed`/`range`/`long_range`/`thrown_range` |
| A2 | resolution.py L256-263 | bless 消费点①：`if "blessed" in actor["conditions"]: bless_bonus = int(rng.randint(1, 4))`；`total = natural + attack_bonus + bless_bonus`；`critical = natural == 20`（crit 阈值是 S-P1 的改动位，注意合并顺序） |
| A3 | resolution.py L280 | attack 事件字段：`"bless_bonus": bless_bonus` 与 `natural/modifier/total/target/success/critical` 同级 |
| A4 | resolution.py L408-437 | bless 消费点②（法术攻击，eldritch blast 多射线逐 beam）：kind=`"spell_attack"`；`_without_consumed_conditions` 剥离已消耗 condition 后判定；注释"Bless 对每一次攻击掷骰各加一枚 d4" |
| A5 | resolution.py L474-510 | bless 消费点③（敌方施法豁免）：kind=`"saving_throw"`；auto_fail 分支 `bless_bonus: 0` 不消耗骰；rage 优势双骰同分支 |
| A6 | resolution.py L966-1008 | bless 消费点④（`_save_repeat_events`，hold person 类重复豁免）：kind=`"saving_throw"` + `"repeat_save": True`；auto_fail 分支 `bless_bonus: 0` |
| A7 | `src/rulesets/dnd2024/combat/engine.py` L1070-1092 | bless 消费点⑤（`_concentration_events` 专注豁免）：kind=`"concentration"`；static 方法，参数 `(combat/target/damage/rng)`，注释"专注维持是一次体质豁免，Bless 对豁免同样生效（1d4）" |
| A8 | `src/rulesets/dnd2024/combat/check_modes.py` L18-51 | `condition_flags(registry, conditions)`：目录遍历 + conditions 成员判定 + 元数据键聚合（`implies_incapacitated`/`speed_zero`/`auto_fail_saves`/`crit_on_hit_within_5`）——新辅助函数的形状先例；L55+ `attack_sources`（attack_modes 消费） |
| A9 | `src/rulesets/dnd2024/combat/catalog.py` L44-140 | `from_bundle`：weapons 与 spell_effects 有详尽校验（DICE_RE、mode ∈ SPELL_MODES、ends_on 触发码集合等），fail closed 抛 `CombatCatalogError`；**conditions 无条目级校验**（直传注册表） |
| A10 | `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` L88-101（content + zh-CN/en locales 三份） | conditions 目录现有条目：`next_attack_advantage`…`unconscious`，元数据键含 `attack_modes`/`resist_damage_types`/`implies_incapacitated`/`auto_fail_saves`/`crit_on_hit_within_5`；**无 `blessed` 条目** |
| A11 | 同文件 L55 附近 | bless 法术 effect：`mode: buff → condition: blessed, concentration`（施加链路已有，本切片不改） |
| A12 | `src/rulesets/dnd2024/progression/advancement.py` L41-52 | `advancement_feats: dict = field(init=False)`；`__post_init__` 读 `bundle.get("advancement_feat_catalog", "srd_advancement_feats")`，labels 覆盖校验后 `deepcopy(feat_catalog["feats"])`；目录缺失/labels 缺失抛 `ProgressionCatalogError`（L13 从 progression/catalog 导入） |
| A13 | advancement.py L345-428 | `_advancement_feat`：只校验 category/minimum_level/repeatable/any_ability_minimum/requires_spellcasting/ability_choice——**无 parameters 概念、无 requires_feature 消费**（升级期不可选 fighting_style，现状正确） |
| A14 | `templates/rulesets/dnd2024_srd/content/advancement_feat_catalogs/srd_advancement_feats.json` L23-46（三份） | archery/defense/great_weapon_fighting/two_weapon_fighting 四条：`{"category": "fighting_style", "source_ref": "srd-5.2.1:p87:archery", "requires_feature": "fighting_style", "repeatable": false}`——纯选择元数据，无机械参数 |
| A15 | `src/rulesets/dnd2024/character/derivation.py` L98-101 | `feat_refs = list(dict.fromkeys([*background_feat_refs, *species_feat_refs]))`——背景+种族专长落账点；升级 feat 由 advancement L290-292 append 到 `features.feat_refs` |
| A16 | derivation.py L162 | **alert 反面教材**：`if "feat:alert" in feat_refs: initiative += prof`——硬编码特性名进派生逻辑；本切片禁止在 combat/derivation 出现 `feat:archery` 字符串字面量 |
| A17 | derivation.py L177-184 | feat 目录参数消费正面先例：magic initiate 读 `self.bundle.get("feat", _ref_id(feat_ref, "feat"))["spell_list"]` 展开进 build spellcasting |
| A18 | `src/rulesets/dnd2024/character/validation.py` L171-177 | 种族专长选择校验先例：`species_feat_choice = (species or {}).get("feat_choice")` → `_validate_choice_refs(draft.get("species_feat_refs"), choice=…, kind="feat", …)`——目录声明驱动、fail closed |
| A19 | validation.py L92-94、L223 | class 侧 draft 键先例：`class_skill_refs` / `class_tool_refs`（数组形状校验 + choice 校验） |
| A20 | `templates/rulesets/dnd2024_srd/content/classes/fighter.json` L53-57 | `level_1_feature_ids: ["fighting_style", "second_wind", "weapon_mastery"]`——建卡期特性判定数据源（paladin/ranger 无 1 级条目，升期选择天然不触发） |
| A21 | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` L117 | fighter progression 1 级 `["fighting_style", "second_wind", "weapon_mastery"]`（resolver `_granted_feature_ids` L497 的数据源） |
| A22 | `src/rulesets/dnd2024/features/resolver.py` L56-80 | resolver 类持有 `bundle`（`__init__`）；L247-271 `actor_projection`：一次性输出 combat 需要的 feature 投影（`spell_grants`/`class_resources`/`unarmed_damage_die`/`unarmed_ability_choice`/`martial_arts_weapon_refs`）——**新增投影字段的 owning module** |
| A23 | `src/rulesets/dnd2024/combat/view.py` L119-176 | 玩家 actor 构造：`"build": deepcopy(character["build"])`（feat_refs 可达）；L166-167 `martial_arts_weapon_refs` 并入点（新字段同段并入） |
| A24 | view.py L208-248 | `_available_attacks`：玩家武器档案 = `equipment_refs` → `catalog.weapons` + item name；`martial_arts_profile()` 是"特性→武器档案变换"先例 |
| A25 | `src/rulesets/dnd2024/runtime.py` L1116-1128 | `_recent_combat_events` 投影 `allowed_fields`：含 `"bless_bonus"`——公开事件契约字段清单（前端不消费 bless_bonus，已 grep 验证 frontend 零命中） |
| A26 | tests | `bless_bonus` 断言仅 3 处（git grep HEAD -- tests 计数）；`SequenceRng` 基建在 `tests/rulesets/rulesets/test_dnd2024_combat.py` L20 与 `test_dnd2024_monk_common.py` L23 |

## 3. 白名单改动规格（逐文件）

### 3.1 `templates/rulesets/dnd2024_srd/content/advancement_feat_catalogs/srd_advancement_feats.json`（×3：content + locales/zh-CN + locales/en）

archery 条目增加机械参数：

```json
"archery": {
  "category": "fighting_style",
  "source_ref": "srd-5.2.1:p87:archery",
  "requires_feature": "fighting_style",
  "repeatable": false,
  "parameters": {"ranged_attack_bonus": 2}
}
```

- 其余三条 fighting_style feat 不加 parameters（§1.2 裁剪 3）。
- locales 两份目录（若含 feats 内容镜像）同步；若 locales 仅为 labels 映射，则只同步存在的部分——执行时先 `git show HEAD` 三份对比结构再落笔。

### 3.2 `src/rulesets/dnd2024/progression/advancement.py`

`__post_init__`（L44-52）在 `deepcopy(feat_catalog["feats"])` 之前新增 parameters 校验：

- 若条目含 `"parameters"`：必须是 object；每个键必须属于封闭键集 `{"ranged_attack_bonus"}`（本切片唯一键；未来切片扩键时扩此集合，同 ACTION_KINDS 演进模式）。
- `ranged_attack_bonus` 值必须为 int 且 `1 <= value <= 10`（bool 排除）。
- 违规抛 `ProgressionCatalogError`（复用 L13 既有导入），消息含 feat id 与键名。fail closed，不留兼容分支。

### 3.3 `src/rulesets/dnd2024/character/validation.py`

新增 draft 键校验（class 侧，先例 A19）：

- 新键 `class_feature_feat_choices`：`{"fighting_style": "feat:archery"}`（特性 id → 专长 ref）。
- 校验逻辑：当 `class_entity["level_1_feature_ids"]` 含 `"fighting_style"`（A20；paladin/ranger 建卡期无此条目，天然跳过）：
  - 键缺失或值非 str → error `fighting_style requires a fighting style feat choice`；
  - ref 必须能在 advancement catalog（`bundle.get("advancement_feat_catalog", "srd_advancement_feats")["feats"]`）解析，且 `category == "fighting_style"` 且 `requires_feature == "fighting_style"`，否则 error（消息含 ref）。
- 当 class 无 fighting_style 1 级特性而 draft 携带该键 → error（防串职业）。
- 不消费 `parameters`（那是消费端契约，见 3.6）；validation 只管选择合法性。

### 3.4 `src/rulesets/dnd2024/character/derivation.py`

- L100 附近：fighting_style 特性在身（判定同 3.3：`class_entity["level_1_feature_ids"]`）时，从 `draft.get("class_feature_feat_choices")` 取 `"fighting_style"` ref，append 进 `feat_refs`（保持 `dict.fromkeys` 去重形状）。
- build 记录：`"class_feature_feat_choices": deepcopy(draft.get("class_feature_feat_choices") or {})` 进 build 选择记录段（L249-258 同段，与 `species_feat_refs`/`feat_choice_answers` 并列）。
- **禁止**出现 `"feat:archery"` / `"archery"` 字面量或任何按专长 id 分支的派生逻辑（A16 alert 反面教材）；derivation 只落账 ref，不解释参数。

### 3.5 `src/rulesets/dnd2024/features/resolver.py`

- `__init__` 之外不动目录加载；`actor_projection`（L247-271）新增输出键 `"ranged_attack_bonus"`：
  - 读 `character["build"]["features"]["feat_refs"]`（升级 feat 由 advancement append，建卡 feat 由 derivation 落账，形状一致）；
  - 对每个 `feat:*` ref 查 `bundle.get("advancement_feat_catalog", "srd_advancement_feats")["feats"]`，累加 `(feat["parameters"] or {}).get("ranged_attack_bonus", 0)`；
  - 返回 int 总额。无特性/无参数 → 0。
- 私有辅助 `_ranged_attack_bonus(character)` 与 `_unarmed_die` 同段放置；docstring 注明"feat 参数在此聚合，combat 不读 feat 目录"。
- resolver 信任 advancement `__post_init__` 的参数校验（3.2），不做二次校验。

### 3.6 `src/rulesets/dnd2024/combat/view.py`

玩家 actor 构造段（L166-167 附近）新增：

```python
"ranged_attack_bonus": int(feature_projection["ranged_attack_bonus"]),
```

- 仅玩家 actor；敌人 actor（L75-85）不加（stat-block attacks 自带 `attack_bonus`，feat 系统是玩家侧契约）。
- `_available_attacks` / `_declared_attack` / 武器档案**零改动**：加值在 resolution 按 `is_ranged_weapon` 消费（A1 的既有布尔），不污染武器档案形状。

### 3.7 `src/rulesets/dnd2024/combat/check_modes.py`

新增模块级函数（与 `condition_flags`/`attack_sources` 并列，纯函数 + rng 参数）：

```python
def condition_check_bonus_rolls(
    registry: dict[str, dict[str, Any]], conditions: dict[str, Any],
    kind: str, rng: Any,
) -> list[dict[str, Any]]:
    """Roll the registry-declared per-check bonus dice for this check kind.

    Each matching condition contributes one die per declared bonus
    (e.g. bless's 1d4). Returns provenance rows: {"condition", "die", "roll"}.
    The caller sums the rolls; the engine never names a condition here.
    """
```

- 遍历 registry（catalog.conditions），成员判定 `condition_id in conditions`（与 condition_flags 相同的形状）；
- 每条目读 `check_bonus_dice`：`[{"die": int, "kinds": [str, ...]}, ...]`；`kind`（语义值，见 3.9 映射）在 `kinds` 内才掷 `rng.randint(1, die)`；
- 目录顺序即掷骰顺序（可复现骰迹）；返回 provenance 列表。
- 本函数不进 pre-roll projection（加值骰是结算期事实，不是可用性投影）。

### 3.8 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`（×3）

conditions 目录（A10 段）新增条目：

```json
"blessed": {
  "check_bonus_dice": [
    {"die": 4, "kinds": ["attack", "saving_throw"]}
  ]
}
```

- `kinds` 使用**语义 kind**（规则语言），不是事件 kind 值：`"attack"` = 一切攻击检定（含法术攻击）；`"saving_throw"` = 一切豁免检定（含重复豁免与专注豁免）；`"ability_check"` 预留（§1.2 裁剪 5）。
- 不给 blessed 加其它键（attack_modes/ends_on 等仍由法术 effect 与施法链路管理）。

### 3.9 `src/rulesets/dnd2024/combat/resolution.py` + `src/rulesets/dnd2024/combat/engine.py`（6 处消费点收束）

用 `condition_check_bonus_rolls` 替换 6 处硬编码，语义 kind 映射：

| 消费点 | 事件 kind 值 | 传入辅助的语义 kind |
|---|---|---|
| resolution attack（A2） | `attack` | `"attack"` |
| resolution spell_attack（A4） | `spell_attack` | `"attack"` |
| resolution saving_throw（A5） | `saving_throw` | `"saving_throw"` |
| resolution save_repeat（A6） | `saving_throw` | `"saving_throw"` |
| engine concentration（A7） | `concentration` | `"saving_throw"` |

替换形状（以 attack 为例）：

```python
bonus_rolls = condition_check_bonus_rolls(
    self.catalog.conditions, actor["conditions"], "attack", rng,
)
check_bonus = sum(row["roll"] for row in bonus_rolls)
total = natural + attack_bonus + check_bonus
```

- auto_fail 分支（A5/A6 的 `bless_bonus: 0`）：**不调用**辅助、不消耗任何骰，事件字段 `check_bonus: 0, check_bonus_rolls: []`（保持"不掷骰"现状）。
- `_without_consumed_conditions` 剥离后的视图继续作为 conditions 入参（A4 现状）。
- **attack 分支同时加 Archery 消费**（3.6 的投影字段）：

```python
if is_ranged_weapon:
    attack_bonus += int(actor.get("ranged_attack_bonus") or 0)
```

放在 `attack_bonus = modifier + proficiency` 之后、bless 掷骰之前；事件 `modifier` 字段自然含 +2（§1.2 裁剪 7）。仅玩家侧分支（`_declared_attack` 路径）；敌人分支零改动。

### 3.10 事件字段迁移（6 处事件构造点）

- `"bless_bonus": <int>` → `"check_bonus": <int>, "check_bonus_rolls": [{"condition": str, "die": int, "roll": int}, ...]`。
- `total` 计算式同步替换。
- **完整移除 `bless_bonus`**：git grep 全库（src/templates/frontend/tests）零残留后才算完成。

### 3.11 `src/rulesets/dnd2024/runtime.py`

`_recent_combat_events.allowed_fields`（A25）：删除 `"bless_bonus"`，加入 `"check_bonus"` 与 `"check_bonus_rolls"`（dict 值沿投影链 deepcopy，与 `advantage_sources` 同等待遇）。

### 3.12 测试

- 新建 `tests/rulesets/test_dnd2024_fighting_style.py`：fighter 建卡选择 → 战斗行为的端到端 + fail closed 校验 + bless 回归（见 §4 矩阵）。
- 既有测试最小更新：`bless_bonus` 断言 3 处（A26）改 `check_bonus`；涉及 attack `modifier` 数值的既有用例若因 archery 投影字段默认 0 不受影响（未选专长时 `ranged_attack_bonus == 0`），禁止顺手改无关断言。
- 复用既有基建：`_instance`/`_start`/`SequenceRng`（test_dnd2024_combat.py L20）、`advance()`。

## 4. 测试矩阵

| # | 断言 | 级别 |
|---|---|---|
| 1 | fighter 1 级 draft 带 `class_feature_feat_choices.fighting_style = "feat:archery"` → 派生 build `features.feat_refs` 含 `feat:archery`；`build.class_feature_feat_choices` 落账 | 建卡 |
| 2 | 非 fighter（无 1 级 fighting_style 特性职业）携带该键 → validation error；fighter 缺键 → validation error；选 `feat:skilled`（category 不符）→ validation error；选未知 ref → validation error | fail closed |
| 3 | 选了 archery 的 fighter 用远程武器（`ranged: true`）攻击：事件 `modifier` 比无专长基线多 2；`total` 一致性成立 | 战斗 |
| 4 | 同角色用近战武器攻击：`modifier` 不变（无 +2） | 边界 |
| 5 | 同角色投掷近战武器（distance > 5、`is_ranged_weapon == False`）：**不**得 +2（§1.1 范围限定；resolution L204-209 注释语义） | 边界 |
| 6 | 未选 archery 的 fighter 远程攻击：`modifier` 不含 +2；`actor["ranged_attack_bonus"]` 投影为 0 | 战斗 |
| 7 | blessed 角色武器攻击：`check_bonus_rolls == [{"condition": "blessed", "die": 4, "roll": <rng>}]`，`check_bonus` 等于该骰；`total = natural + modifier + check_bonus` | 回归 |
| 8 | blessed 法术攻击（eldritch beam）：逐 beam 各一枚 d4（SequenceRng 锁消耗顺序：每 beam d20→[d20]→d4） | 回归 |
| 9 | blessed 敌方豁免（saving_throw）：+1d4；auto_fail 分支（paralyzed + str 豁免）：不掷 d4，`check_bonus_rolls == []` | 回归 |
| 10 | blessed hold person 重复豁免（save_repeat）：+1d4；auto_fail 分支不掷骰 | 回归 |
| 11 | blessed 专注豁免（concentration 事件）：+1d4 | 回归 |
| 12 | archery + blessed 叠加：远程 blessed 攻击 `total = natural + (modifier+2) + d4`，骰迹完整 | 组合 |
| 13 | 无 blessed 无 archery：`check_bonus == 0`、`check_bonus_rolls == []`，事件字段恒存在（前端契约稳定） | 回归 |
| 14 | combat_catalog `blessed.check_bonus_dice` 坏值（die: 1 / kinds: ["nope"] / 非法类型）→ `CombatCatalogError`；advancement parameters 坏值（未知键 / 0 / 越界 / bool）→ `ProgressionCatalogError` | fail closed |
| 15 | 目录加载正常时 combat/view 玩家 actor 投影含 `ranged_attack_bonus`；敌人 actor 不含 | 投影 |
| 16 | runtime `_recent_combat_events` 输出含 `check_bonus`/`check_bonus_rolls`、不含 `bless_bonus` | 投影 |
| 17 | 骰迹 RNG 顺序锁定：attack 分支 SequenceRng 消耗序列 = [d20, (d20), d4]，测试断言每一步取值 | 骰迹 |
| 18 | grep 验收：`git grep -n bless_bonus` 全库零命中 | 收尾 |

## 5. 验收命令

```powershell
# 目录与校验
python -m pytest tests/rulesets/test_dnd2024_fighting_style.py -q
# 战斗回归（bless 既有用例所在文件族）
python -m pytest tests/rulesets -k "bless" -q
python -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spell_blessed.py -q
# 建卡与升级回归
python -m pytest tests/rulesets -k "advancement" -q
python -m pytest tests/rulesets -k "validation" -q
# 字段移除验收
git grep -n -e bless_bonus -- src templates frontend tests   # 期望零命中
# 全量（提交前）
python -m pytest tests -q
```

（文件名以执行时实际存在的测试文件为准；`-k "bless"` 覆盖 bless 相关全部用例。）

## 6. 禁止事项

1. 禁止在 resolution/engine/view/derivation/validation 出现 `"archery"`、`"feat:archery"`、`"blessed"` 字符串字面量分支（目录声明驱动；`"blessed"` 仅允许出现在目录 JSON 与测试构造中）。
2. 禁止用 advantage/disadvantage 模式实现加值（主计划明确"区别于"）。
3. 禁止给武器档案（`_available_attacks` 输出、catalog.weapons）加 archery 相关字段——加值消费在 resolution 的 `is_ranged_weapon` 分支。
4. 禁止把 `ranged_attack_bonus` 塞进 `derived`（build 派生全局值）——它是条件（远程武器才生效）战斗消费值，owning module 是 features 投影。
5. 禁止保留或双写 `bless_bonus` 兼容字段；禁止在 runtime 投影白名单外私开旁路。
6. 禁止动升期 choices 协议（`_advancement_feat` 的 expected_category 逻辑保持原样）。
7. 禁止改动 bless 法术 effect（A11）与 concentration 链路结构——本切片只换加值计算与事件字段。
8. 禁止在 conditions 校验（3.8 的 catalog.py 改动）里追溯校验既有键（attack_modes 等）——只校验新增键 `check_bonus_dice`，最小改动。
9. 禁止使用"补天"一词。

## 7. 停止条件（执行者遇此即停，回报后待命）

1. 1B 法术原语工作与本切片在同一工作区出现未提交交叠（尤其 resolution.py/engine.py 被 P 系列切片并行修改）→ 停止，报告 HEAD 变化，等待协调。
2. `srd_advancement_feats.json` 或 `srd_combat_core.json` 的工作区版本与基线有未预期 diff（P6/P7 半成品混入）→ 停止核实。
3. 发现 `bless_bonus` 存在 §3.11 白名单之外的消费者（grep 超预期命中）→ 停止，报告完整命中清单，不得擅自扩大移除范围。
4. 发现 conditions 目录已被其它并行切片加过条目级校验（catalog.py 结构漂移）→ 停止对齐。
5. advancement catalog 的 locales 两份与 content 结构不同步（labels-only vs feats 镜像的歧义）→ 停止，报告三份文件的实际结构差异。
6. 测试矩阵 #5（投掷近战不 +2）失败且根因在 `is_ranged_weapon` 既有语义 → 停止报告，不修改既有判定语义（那是 2024 Thrown 规则的实现契约）。

## 8. 执行报告模板

（沿用 SP1 协议：逐文件 diff 摘要 / 测试矩阵逐项结果 / 验收命令输出 / 偏离本任务书的每一处显式列出并附理由 / 停止条件触发记录 / 迁移债清单。）

迁移债登记（执行者确认后原样汇报）：
- paladin 2 级 / ranger 2 级升期 fighting_style 选择、champion 7 级 additional_fighting_style（升期 choices 协议扩展）；
- 建卡 UI 的 fighting_style 选择控件（draft 键协议已就绪）；
- defense / great_weapon_fighting / two_weapon_fighting 的 parameters 与消费（各自归属切片）；
- `"ability_check"` 语义 kind 的消费点（P10 属性检定阶段接入）。
