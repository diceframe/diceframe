# S0-G2 任务书：Light 额外攻击、Nick 与 Dual Wielder

| 项 | 值 |
|---|---|
| 切片编号 | S0-G2（一份契约，分 A/B/C 三个执行提交，不覆盖旧 SP） |
| 验收载体 | Light 武器的额外攻击；Nick 把它的支付改挂 Attack action；Dual Wielder 放宽第二把武器的属性要求 |
| 通用能力 | 额外攻击来源声明、每回合记账、攻击动作武器账本、额外攻击的属性调整值判定点 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Light 与武器属性 p90；Nick 属 mastery 段 p91–p92；Dual Wielder 在 feats 段 p87–p88，**逐条页码须执行时从 PDF 读出后填入 `source_ref`，不得猜** |
| 代码基线 | S0-G1 合入后的集成链头（G1 交付前不得开工） |
| 硬前置 | **S0-G1**（双槽落位 + 物品实例身份）。没有实例身份就无法判定「a different weapon」 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律与 A/B/C 提交边界

1. 从 S0-G1 合入后的实际提交新建干净工作树。基线**没有任何代码读 `light`** 去触发额外攻击，也没有「本回合用过哪把武器」的记录——两条都已定点核实（§3）。
2. **提交 A（Light 额外攻击）**：intent 增加额外攻击来源声明、`attack.spent` 携带武器引用、每回合账本、属性调整值判定点。A 完成后双持两把 Light 武器可多打一次并扣 Bonus Action。
3. **提交 B（Nick）**：把这次额外攻击的支付改挂 Attack action，不扣 Bonus Action，仍每回合一次，且必须与 A 走同一条结算链。
4. **提交 C（Dual Wielder）**：专长声明与选项路径，把第二把武器的属性要求从「Light」放宽为「近战且非 Two-Handed」。
5. **Two-Weapon Fighting 战斗风格留在 S0-F8**。本切片只负责提供**唯一**的属性调整值判定点，F8 补一条声明消费即可，不得在 G2 里顺手实现。
6. 所有攻击次数、命中、伤害、动作经济与每回合记账由服务端结算；客户端不得申报「这次该扣什么」或「我还剩几次」。

## 2. 官方英文关键原文与语义边界

### 2.1 Light（p90）

> **Light.** When you take the Attack action on your turn and attack with a Light weapon, you can make one extra attack as a Bonus Action later on the same turn with a different Light weapon. You don't add your ability modifier to the extra attack's damage unless that modifier is negative. For example, you can attack with a Shortsword in one hand and a Dagger in the other.

### 2.2 Nick（p91–p92，mastery 段）

> **Nick.** When you make the extra attack of the Light property, you can make it as part of the Attack action instead of as a Bonus Action ... only once per turn.

### 2.3 Dual Wielder（feats 段）

> **Dual Wielder.** *General Feat (Prerequisite: Level 4+, Strength or Dexterity 13+)*
> **Enhanced Dual Wielding.** When you take the Attack action on your turn and attack with a weapon that has the Light property, you can make one extra attack as a Bonus Action later on the same turn with a different weapon, which must be a Melee weapon that lacks the Two-Handed property. You don't add your ability modifier to the extra attack's damage unless that modifier is negative.
> **Quick Draw.** You can draw or stow two weapons that lack the Two-Handed property when you would normally be able to draw or stow only one.

**勘误提示（必须记录进执行报告）**：2024 PHB 初印本写作「when you are holding a Weapon with the Light property in one hand, you can treat a non-Light Weapon in your other hand as if it had the Light property」。SRD 5.2.1 采**修正后**措辞：首击仍必须是 Light 武器，放宽的是**第二把**武器（近战且非 Two-Handed，不必是 Light）。按本仓库纪律（§5 统一执行纪律第 5 条）以 SRD 原文为准。

由此得出两条必须钉死的机械结论：

1. **两把长剑仍然不合法**——Dual Wielder 放宽的是第二把，不是首击。首击必须是一把 Light 武器。
2. 原文的「one hand / the other」是举例，**不是**手部条件。本切片不引入任何手部状态；机械条件是「不同的一件武器」。

### 2.4 语义边界

1. Light 的额外攻击**不增加 Attack action 的攻击次数**，不走 `attacks_remaining`。
2. Nick 只改**支付来源**，不改次数、不改伤害、不改「不同武器」要求。
3. Light 与 Dual Wielder 共用同一个「每回合一次」额度，**不叠加**为两次额外攻击。
4. 额外攻击的伤害不加正属性调整值；负值仍加。Two-Weapon Fighting 风格是唯一改写此条的来源，属 S0-F8。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| **`light` 属性在战斗模块零消费**：全仓库唯一读它的地方是「Monk Weapon = Martial Melee 且 Light」 | `features/equipment.py:62` `weapon_matches_kind` |
| 8 件武器带 `light: true`：club、crossbow_hand、dagger、handaxe、light_hammer、scimitar、shortsword、sickle | `content/combat_catalogs/srd_combat_core.json` 的 `weapons` |
| 攻击支付事件只有资源与次数，**不带武器引用** | `combat/engine.py:1309` `_attack_cost_event` |
| 攻击事件的减少逻辑：`attacks_remaining > 0` 则减一，否则扣 `action` 并把 `attacks_remaining` 置为 `attacks_per_action - 1` | `combat/reducer.py:432` `dnd2024.attack.spent` |
| 借机攻击的既有做法是**替换支付事件本身**（`attack.spent` → `reaction.spent`），而不是新增第二套次数 | `combat/resolution.py:2566-2573` |
| 「每回合一次」已有权威账本，按真实攻击者 + `round` + `turn_index` 记账并在回合变化时重置 | `combat/reducer.py:282` `dnd2024.feature.used` |
| 动作经济是 `combat.economy = {action, bonus_action, attacks_remaining, movement}`，`action` 与 `bonus_action` 独立 | 实测（§8 探针） |
| mastery 的**规则可用性**与**效果实现**已拆开：`_mastery_available` 只回答规则问题 | `combat/resolution.py:666` |
| Tactical Master 已能把属性替换为 push/sap/slow，替换后原属性不再触发 | `combat/resolution.py::_weapon_mastery_effect` 与 hit/miss 两条路径 |
| 攻击意图的武器解析是 `_declared_attack`，按 `weapon_ref` 精确匹配（G1 后带实例） | `combat/view.py:388` |
| 专长目录结构支持 `category/minimum_level/any_ability_minimum/ability_choice`；`grappler` 是现成范例 | `content/advancement_feat_catalogs/srd_advancement_feats.json:15-22` |
| 专长选择经 `advancement_feat_ref` 落入 `features.feat_refs`，形如 `feat:<id>` | `progression/advancement.py:479-491` |
| `two_weapon_fighting` 已是 `fighting_style` 类专长，但**没有任何参数或被动效果** | `srd_advancement_feats.json:45-50` |
| `dual_wielder` 在全仓库**零命中** | 全量检索 |
| `attacks_per_action` 由职业表声明驱动，多职业取最大值 | `combat/engine.py:1298` |

## 4. 精确行为契约

### 4.1 额外攻击来源声明（提交 A）

1. 攻击意图新增 `extra_attack_source`，取值闭集 `{"light", "nick", "dual_wielder"}`。字段缺失 = 普通攻击；出现闭集外的值 → 拒绝。
2. 一次攻击只能声明一种来源。同一攻击同时声明两种属于非法请求，不得「取其一」。
3. `nick` 与 `dual_wielder` 不是并列的第四、第五种攻击：`nick` 是 Light 额外攻击的另一种支付方式，`dual_wielder` 是 Light 额外攻击的另一种武器资格。三者在账本上共用同一额度（§4.4）。

### 4.2 攻击动作武器账本（提交 A）

1. `dnd2024.attack.spent` 事件新增 `weapon_ref`（G1 的规范形，含实例），用于「本回合 Attack action 用过哪把武器」。
2. reducer 维护 `combat.attack_ledger`，结构 `{actor_id: {"round", "turn_index", "weapon_refs": [...]}}`，按 `round` + `turn_index` 不匹配即重置——与 `feature_usage` 同一模式，不新造第二套回合语义。
3. 该账本由事件派生，因此重放得到相同结果；不得读时钟或客户端提交。
4. 借机攻击（支付已换成 `reaction.spent`）**不写入**该账本：它不是 Attack action。

### 4.3 三种来源的准入条件（提交 A 与 B、C 分摊）

| 来源 | 前提 | 支付 | 本切片引入 |
|---|---|---|---|
| 普通攻击 | 无 | `attacks_remaining` → `action`（不变） | — |
| `light` | ① 本回合 Attack action 用过一把 Light 武器；② 本次武器是**不同实例**且也是 Light；③ 本次武器已装备 | 扣 `bonus_action` 1 点 | A |
| `nick` | 同 `light` 的 ①②③，**外加**本次武器的 mastery 为 `nick` 且 `_mastery_available` 为真 | **不扣** `bonus_action`，**不动** `attacks_remaining` | B |
| `dual_wielder` | ① 本回合 Attack action 用过一把 Light 武器；② 本次武器是不同实例、是**近战**武器、且**不带 `two_handed`**；③ 拥有 `feat:dual_wielder` | 扣 `bonus_action` 1 点 | C |

补充约束：

1. 「不同实例」按 G1 的 `instance_id` 判定；缺实例时按 G1 的兼容规则（唯一匹配才成立）降级，不得猜。
2. 三种来源都**不消耗** `attacks_remaining`，也**不增加** `attacks_per_action`。
3. `bonus_action` 不足时拒绝并指出是 Bonus Action 不足，而不是「攻击次数不足」。
4. 未装备的武器（只背在背包里）不能作为首击或额外攻击的武器。

### 4.4 每回合一次（提交 A）

1. 用**既有** `dnd2024.feature.used` 账本记账，`feature_id` 取 `light_extra_attack`，三者共用这一个 id——因为 SRD 的「once per turn」是 Light 额外攻击这个**事件**的上限，不是三种来源各一次。
2. 已用过后再声明任一来源 → 拒绝，理由说明本回合的 Light 额外攻击已用。
3. 不新造 `combat.light_extra_used` 之类的平行状态字段；若确实需要可观测投影，只做派生，不做第二份账本。

### 4.5 属性调整值判定点（提交 A，F8 消费）

1. 新增**唯一**判定点 `extra_attack_ability_modifier(actor, weapon, source) -> int`：默认返回 `min(0, modifier)`，即「不加正调整值、负值仍加」。
2. `feat:two_weapon_fighting` 存在时返回完整 `modifier`。**S0-G2 不写这条分支**，只留判定点与「默认值」测试（矩阵 9）；S0-F8 补该分支后由 F8 自己的矩阵验收。
3. 除该判定点外，额外攻击的伤害计算与普通攻击**完全同路径**（伤害骰、伤害类型、抗性、暴击、on-hit 词条一致），不得另写一条简化管线。

### 4.6 Nick 的支付替换（提交 B）

1. 参照借机攻击的既有做法：**替换支付事件本身**，而不是「先扣再退」。`nick` 的额外攻击不产生扣 `bonus_action` 的事件。
2. 仍然消耗 §4.4 的每回合额度。
3. 与 Tactical Master 的交互：`mastery_override` 把 `nick` 换成 push/sap/slow 后，本次武器**不再具有** `nick`，因此不能并回 Attack action，必须按 `light` 的路子扣 Bonus Action。此条对应 S0-F7 矩阵 13。
4. `nick` 不能用于首击；它只对「Light 的额外攻击」这一击生效。

### 4.7 Dual Wielder 专长（提交 C）

1. `srd_advancement_feats.json` 新增：

   ```json
   "dual_wielder": {
     "category": "general",
     "source_ref": "srd-5.2.1:<PDF 实读页码>:dual-wielder",
     "minimum_level": 4,
     "repeatable": false,
     "any_ability_minimum": {"str": 13, "dex": 13},
     "ability_choice": {"allowed": ["str", "dex"], "points": 1, "pattern": "1", "maximum": 20},
     "parameters": {"enhanced_dual_wielding": true, "quick_draw": true}
   }
   ```

2. `any_ability_minimum` 的语义是「任一满足即可」，与 `grappler` 同构，复用既有校验，不新写准入逻辑。
3. 选择路径复用 `advancement_feat_ref` → `features.feat_refs`，不新建专长选择通道。
4. **Quick Draw 是非目标**：本仓库没有「取出/收起武器」的交互层。声明里保留 `quick_draw: true` 作为原文记录，但必须在报告里标记为 `not_automated`，且**不得**开一个假装能用的入口。

### 4.8 与其他机制的边界

1. **Extra Attack**：`attacks_per_action` 不变。19 级 Fighter 的 `attacks_remaining` 逻辑不受影响。
2. **Cleave（F7）**：Cleave 的第二击不是 Light 额外攻击，**不消耗** §4.4 的额度，也不写攻击动作账本以外的状态。
3. **Tactical Master（F7）**：见 §4.6.3。
4. **盾牌 / 双手武器**：额外攻击的武器必须是已装备的一件单手武器（G1 保证 `Two-Handed` 武器占满两手，因此不可能存在「双手武器 + 另一把」）。若状态被外部破坏成该形态，校验拒绝而不是取其一。
5. **徒手打击**：`unarmed_strike` 没有 `light`、也没有实例，不能作为 Light 额外攻击的任一武器；Dual Wielder 也不放宽这一点（原文要求 Melee **weapon**）。

### 4.9 视图（提交 A 与 C）

1. actor 视图新增 `extra_attack` 投影：`{"allowed": [...], "blocked": {source: reason}}`，让客户端不必自己猜规则。**只读派生**，不参与判定。
2. 每个可用攻击 profile 保留 `instance_id`（G1）与 `light` 真值，`two_handed` 同。
3. 拒绝必须给出可读原因：`bonus action 不足` / `本回合 Light 额外攻击已用` / `本次武器与首击相同` / `首击不是 Light 武器` / `本次武器带 Two-Handed` / `需要 Dual Wielder 专长` / `本次武器 mastery 不是 Nick`。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/advancement_feat_catalogs/srd_advancement_feats.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/engine.py`（`_attack_cost_event`、意图入口）、`validation.py`、`resolution.py`、`reducer.py`、`view.py`、`primitives.py`。
3. 新建 `src/rulesets/dnd2024/combat/extra_attacks.py`（来源闭集、准入判定、属性调整值判定点）。
4. `src/rulesets/dnd2024/features/resolver.py`（仅在需要暴露 `two_weapon_fighting` 判定点时）。
5. 新建 `tests/rulesets/test_dnd2024_light_extra_attack.py`、`tests/rulesets/test_dnd2024_dual_wielder.py`。
6. 既有 `tests/rulesets/test_dnd2024_weapon_mastery.py`、`test_dnd2024_tactical_master.py` 仅可补 Nick 相关断言，不得改写其既有断言。
7. 需要改 `src/engine/**`、前端、或**必须先做 S0-F8** 才能成立时停止。

## 6. 编号测试矩阵

1. 双持 shortsword + dagger（G1 后两者都已装备）：首击后声明 `light` 成功，`bonus_action` 由 1 变 0。
2. `light` 额外攻击不改变 `attacks_remaining`，也不改变 `attacks_per_action`。
3. `bonus_action` 已为 0 时声明 `light` 被拒，理由是 Bonus Action 不足。
4. 首击不是 Light 武器（如 longsword）时声明 `light` 被拒。
5. 本回合没有做过 Attack action 时声明 `light` 被拒。
6. 额外攻击的武器与首击是**同一实例**时被拒（同款两把短剑分别持有时合法，同一把用两次非法）。
7. 额外攻击的武器是 Light 但未装备（在背包里）时被拒。
8. 同一回合第二次声明 `light` 被拒，理由是已用。
9. 额外攻击的伤害不加正属性调整值；属性调整值为负时仍加负值。此条断言的是 `extra_attack_ability_modifier` 的**默认值**，Two-Weapon Fighting 分支由 S0-F8 补。
10. 额外攻击与普通攻击走同一伤害路径：抗性、暴击、伤害类型与普通攻击一致。
11. `nick`：本次武器 mastery 为 nick 且 `weapon_mastery` 可用时，额外攻击**不扣** `bonus_action`，`bonus_action` 保持 1。
12. `nick` 仍消耗每回合额度：同回合再用 `light` 被拒。
13. `nick` 不改变 `attacks_remaining` 与 `attacks_per_action`。
14. 本次武器 mastery 不是 nick 时声明 `nick` 被拒。
15. 无 `weapon_mastery` 特性的角色声明 `nick` 被拒。
16. `nick` 用于首击（没有既有 Light 额外攻击）时被拒。
17. Tactical Master 把 nick 换成 push 后，声明 `nick` 被拒，改按 `light` 声明则扣 `bonus_action`（S0-F7 矩阵 13）。
18. Dual Wielder：持有 `feat:dual_wielder` 且首击为 Light 武器时，可用一把**非 Light 的近战单手**武器做额外攻击。
19. Dual Wielder：第二把是带 `two_handed` 的近战武器时被拒。
20. Dual Wielder：第二把是远程武器时被拒。
21. 无 `feat:dual_wielder` 时，非 Light 第二把武器被拒（回落到 Light 的限制）。
22. **两把长剑**：即使持有 `feat:dual_wielder` 也被拒——首击不是 Light 武器。
23. Light 与 Dual Wielder 共用额度：先用 `light` 再声明 `dual_wielder` 被拒，反之亦然。
24. 专长准入：`dual_wielder` 在 4 级可选、3 级不可选；Str 与 Dex 均 < 13 时不可选；Str 或 Dex 任一 ≥ 13 时可选（`any_ability_minimum` 语义）。
25. 专长准入：`dual_wielder` 的 +1 只能加到 Str 或 Dex，且不超过 20；不可重复选取。
26. Quick Draw 无入口：不存在能取出/收起武器的意图或命令；报告标记 `not_automated`。
27. Cleave 的第二击不消耗 Light 每回合额度：先 Cleave 再声明 `light` 仍成立。
28. Extra Attack：19 级 Fighter 的双持 Light 额外攻击不改变 `attacks_remaining` 的初始值。
29. 攻击动作账本按 `round` + `turn_index` 重置；上一回合用过的武器不再满足首击条件。
30. 同一意图同时声明两种来源被拒，不取其一。
31. 视图 `extra_attack.allowed` 与实际准入一致；被拒来源在 `blocked` 里有可读原因。
32. A 提交后矩阵 1–11、27–31 通过；B 提交后 12–17 通过；C 提交后 18–26 通过。
33. 既有武器精通、Tactical Master、战斗、方向、装备测试无回归。（矩阵 9 只断言默认值，TWF 分支留给 S0-F8。）
34. 存档/加载与重放后：每回合额度、攻击动作账本、动作经济三项与重放前一致。

## 7. 非目标与停止条件

### 非目标

- Two-Weapon Fighting 战斗风格的实现（S0-F8）；本切片只留判定点。
- Quick Draw 的取出/收起交互（无对应交互层，标记 `not_automated`）。
- 手部状态机、主副手身份、多臂生物。
- 任意第二击、Cleave 或 Extra Attack 的属性调整值改写。
- 把 Light 额外攻击做成「额外攻击次数 +1」，或让它消耗 `attacks_remaining`。

### 停止条件

1. S0-G1 未合入，或物品实例身份不可用——无法判定「a different weapon」。
2. 额外攻击必须靠客户端申报支付来源或剩余次数才能成立。
3. 需要改 `attacks_per_action` 或 `attacks_remaining` 的既有语义才能实现。
4. 需要 `src/engine/**` 或前端改动，或必须先交付 S0-F8。
5. 每回合额度需要第二套平行账本（`feature_usage` 不适用）才能表达。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_light_extra_attack.py tests/rulesets/test_dnd2024_dual_wielder.py -q
python -m pytest tests/rulesets/test_dnd2024_weapon_mastery.py tests/rulesets/test_dnd2024_tactical_master.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_extra_attack.py -q
python -m pytest tests/rulesets -q -n auto --dist loadfile
python -m ruff check src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_light_extra_attack.py tests/rulesets/test_dnd2024_dual_wielder.py
git diff --check
```

基线探针（确认 `light` 未被消费、两把 Light 武器都在视野内、动作经济独立）。从仓库根保存为 `.probe-light.py` 后 `python .probe-light.py`；已在 `872e165a` 上实测通过，输出应为 `economy` 含独立的 `action` / `bonus_action`、`attacks_remaining = 0`，两个 Light profile 均为 `True`：

```python
import os
import sys

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "tests/rulesets")
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from dnd2024_monk_common import SequenceRng, monk_instance, with_equipment

rt = Dnd2024Runtime()
preset = [
    i for i in rt.builder_choices(None, {"locale": "en"})["quick_presets"]
    if i["id"] == "stalwart_guardian"
][0]
sheet = rt.finalize_character(None, {**preset["draft"], "locale": "en", "name": "T"})
sheet = with_equipment(rt, sheet, "item:shortsword", "item:dagger")
engine, instance = monk_instance(rt, sheet)
goblin = {
    "id": "goblin-1", "name": "Goblin", "hp": 40, "armor_class": 12, "speed": 30,
    "position": 5, "initiative_modifier": 2,
    "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
    "saving_throws": {"dex": 2, "wis": -1, "con": 0},
    "attacks": [{
        "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
        "damage": "1d6+2", "damage_type": "slashing", "range": 5,
    }],
}
start = engine.resolve_intent(
    instance,
    {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin],
    },
    SequenceRng([20, 1]),
)
assert start["ok"], start
engine.apply_batch(instance, start["event_batch"])
combat = ruleset_runtime.state(instance)["combat"]
actor = engine._actor_view(instance, combat, "player:gm")
print("economy   =", combat["economy"])
print("equipment =", actor.get("equipment_refs"))
print("attacks   =", [(a["weapon_ref"], a.get("light")) for a in engine._available_attacks(actor)])
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / A、B、C 提交：
- 勘误取舍：SRD 修正措辞 vs PHB 初印本，记录依据：
- Dual Wielder 实读页码与 `source_ref`：
- `attack.spent` 的新载荷与账本结构：
- 三种来源的准入与拒绝对照：
- Nick 的支付替换实现（不产生扣 bonus action 事件）：
- 属性调整值判定点与 TWF 留白：
- 与 Tactical Master / Cleave / Extra Attack 的边界证据：
- Quick Draw 的 `not_automated` 记录：
- 白名单文件对照：
- 测试 1–34 / A/B/C 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放与确定性证据：
- 偏差、停止原因与未决事项：
