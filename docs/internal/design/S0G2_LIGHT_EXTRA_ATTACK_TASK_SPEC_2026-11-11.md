# S0-G2 任务书：Light 额外攻击、Nick 与 Dual Wielder

| 项 | 值 |
|---|---|
| 切片编号 | S0-G2（一份契约，分 A/B/C 三个执行提交，不覆盖旧 SP） |
| 验收载体 | Light 武器的额外攻击；Nick 把它的支付改挂 Attack action；Dual Wielder 放宽第二把武器的属性要求 |
| 通用能力 | 额外攻击来源声明、每回合记账、攻击动作武器账本、额外攻击的属性调整值判定点 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Light 与武器属性 p90；Nick 属 mastery 段 p91–p92；Dual Wielder 在 feats 段 p87–p88，**逐条页码须执行时从 PDF 读出后填入 `source_ref`，不得猜**（本轮未取得 PDF，`dual_wielder` 取 p87 是区间夹逼，见 §9.3） |
| 代码基线 | S0-G1 合入后的集成链头（G1 交付前不得开工） |
| 硬前置 | **S0-G1**（双槽落位 + 物品实例身份）。没有实例身份就无法判定「a different weapon」 |
| 执行模型 | `dsv4.1f` |
| 状态 | **已交付**：A `41fcbefe`、B `8e497158`、C `f4ab1d75`、投影修正 `c54c486b`；执行报告见 §9 |

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
2. reducer 维护 `combat.attack_ledger`，结构 `{actor_id: {"round", "turn_index", "weapon_refs": [...]}}`，按 `round` + `turn_index` 不匹配即重置——与 `feature_usage` 同一模式，不新造第二套回合语义。列表按攻击发生的先后追加，记录本回合 Attack action 用过的**每一把**武器；判定「不同」时对它做存在性检查（§4.3 补充约束 2），**不取末项、也不做集合比较**。列表顺序只用于重放与可观测投影。
3. 该账本由事件派生，因此重放得到相同结果；不得读时钟或客户端提交。
4. 借机攻击（支付已换成 `reaction.spent`）**不写入**该账本：它不是 Attack action。

### 4.3 三种来源的准入条件（提交 A 与 B、C 分摊）

| 来源 | 前提 | 支付 | 本切片引入 |
|---|---|---|---|
| 普通攻击 | 无 | `attacks_remaining` → `action`（不变） | — |
| `light` | ① 本回合 Attack action 中有一次 Light 武器攻击（补充约束 3）；② 存在一次 Light 武器攻击，其所用武器 ≠ 本次武器（补充约束 2 的存在性判定），且本次武器是 Light、与之为不同实例；③ 本次武器已装备 | 扣 `bonus_action` 1 点 | A |
| `nick` | 同 `light` 的 ①②③，**外加**本次武器的 mastery 为 `nick` 且 `_mastery_available` 为真 | **不扣** `bonus_action`，**不动** `attacks_remaining` | B |
| `dual_wielder` | ① 同 `light` 的 ①；② 存在一次 Light 武器攻击，其所用武器 ≠ 本次武器，且本次武器是**近战**、**不带 `two_handed`**、不同实例；③ 拥有 `feat:dual_wielder` | 扣 `bonus_action` 1 点 | C |

补充约束：

1. 「不同实例」按 G1 的 `instance_id` 判定；缺实例时按 G1 的兼容规则（唯一匹配才成立）降级，不得猜。
2. **「不同」是存在性判定，不与末项比、也不与集合比。** 原文 `a different Light weapon` 的参照点是「触发这次额外攻击的那次攻击」，而 Attack action 里**每一次** Light 武器攻击都能触发它，因此玩家可以任选其中一次作为参照。判定式为：

   ```
   允许 ⇔ ∃ w ∈ 本回合 Attack action 的各次攻击所用武器：
           w 是 Light 武器 且 w ≠ 本次额外攻击的武器
   ```

   - 依据 `Sage Advice Compendium`：「The only requirement for the Light property's extra attack is that it's made with a **different** Light weapon.」（同条还明确「攻击一次 Light 武器后从另一只手拔出第二把武器即可满足」）。
   - **双持两把 Light 武器、且 Extra Attack 两击各用一把**时，额外攻击**用哪一把都成立**：用 A 则参照 B，用 B 则参照 A。这是多击角色最常被误判的配置。
   - **参照与被参照不对称**：`w`（参照武器）必须在账本里，即本回合 Attack action 真的用它打过；本次额外攻击用的那把**不必**在账本里，只需已装备且是 Light（SAC 同条即为此：「攻击一次 Light 武器后从另一只手拔出第二把武器即可满足」）。所以 1 级角色用 shortsword 打一击、用从未打过的 dagger 做额外攻击是合法的；反过来用 shortsword 做额外攻击不合法，因为账本里没有第二把可参照的武器。
   - **同一把武器不能作为它自己的参照**。原因不是「打了两次」，而是账本里没有一把**不同于本次**的 Light 武器：1 级角色（`attacks_per_action = 1`）用 shortsword 打一击后，额外攻击用 shortsword 一样被拒；5 级角色两击都用 shortsword 时结论完全相同。**攻击次数不进入判定**，账本里同一武器出现多次也不产生第二个参照点。
   - 「先 dagger(Light) 后 longsword(非 Light)」时，enabling 攻击只有 dagger 那一次，故额外攻击不能用 dagger，只能用**另一把** Light 武器——条件里的「w 是 Light 武器」正是为此存在，缺了它会把非 Light 的 longsword 当成合法参照。
   - **不采用「不在本回合已用集合里」的读法**：那会让上述双持多击角色彻底无武器可选，等于把双持从所有多击职业里删掉。
   - **不采用「与末项不同」的读法**：那会错误禁止「用第一击的武器做额外攻击」，而 SAC 明确允许。
3. 准入条件 ①（「attack with a Light weapon」）同样取存在性：只要账本里有一把 Light 武器即可，是条件 ② 的必要条件，不独立成立。
4. **手别不是状态**。玩家第一回合用左手武器攻击、第二回合换成右手，两回合各自成立：账本按 `round` + `turn_index` 重置，实现不得从上一回合的 `weapon_refs` 推导任何「这只手本回合已用过」之类的持久结论。
5. 三种来源都**不消耗** `attacks_remaining`，也**不增加** `attacks_per_action`。
6. `bonus_action` 不足时拒绝并指出是 Bonus Action 不足，而不是「攻击次数不足」。
7. 未装备的武器（只背在背包里）不能作为首击或额外攻击的武器。

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
2. **Cleave（F7）**：Cleave 的第二击不是 Light 额外攻击，**不消耗** §4.4 的额度。它用的是触发它的**同一把**武器，因此写入账本不会引入新的武器，§4.3 的存在性判定结果不变；实现上允许写入，但不得因此改变额外攻击的准入结果。
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
30. **左右手互换的两回合**：回合 1 用 shortsword 首击、dagger 做 `light` 额外攻击；回合 2 用 dagger 首击、shortsword 做 `light` 额外攻击。两回合都成立，且额度互相独立（每回合各扣 1 点 `bonus_action`、各用 1 次 `light_extra_attack` 额度），任何一方都不因为「上一回合用过」而被拒。
31. **多击、两击各用一把**：5 级 Fighter 用 shortsword 打第一击、dagger 打第二击。`light` 额外攻击**用 shortsword 成立**（参照 dagger），**用 dagger 也成立**（参照 shortsword）。这是存在性判定与「集合/末项」两种错误读法的分歧点：集合读法会两把都拒，末项读法会拒掉 dagger，两者都错。
32. **同一把武器不能作为自己的参照，与攻击次数无关**：1 级 Fighter（`attacks_per_action = 1`）用 shortsword 打一击，`light` 额外攻击**用 shortsword 被拒**——账本里唯一的武器就是它自己；改用 dagger 成立。5 级 Fighter 两击都用 shortsword 时结论**完全相同**，证明拒绝的原因不是「打了两击」而是「账本里没有一把不同于本次的 Light 武器」：实现不得把 `attacks_per_action` 或账本条目数读进判定，同一武器在账本里出现多次也不构成第二个参照点。
33. **非 Light 武器不是合法参照**：先 dagger（Light）后 longsword（非 Light），`light` 额外攻击用 dagger 被拒（enabling 攻击只有 dagger 那次），换第二把 Light 武器才成立——条件里的「w 是 Light 武器」缺了就会把 longsword 误当成合法参照。
34. 回合 2 尚未进行 Attack action 时直接声明 `light` 被拒：读账本必须做 `round` + `turn_index` 比较，不得读到回合 1 的残留而误放行。
35. 同一意图同时声明两种来源被拒，不取其一。
36. 视图 `extra_attack.allowed` 与实际准入一致；被拒来源在 `blocked` 里有可读原因。
37. A 提交后矩阵 1–11、27–36 通过（矩阵 30–34 属 A 提交的账本语义，一并验收；31 需要 5 级 Fighter 夹具，32 需要 1 级与 5 级两组对照，33 需要能装备非 Light 武器）；B 提交后 12–17 通过；C 提交后 18–26 通过。
38. 既有武器精通、Tactical Master、战斗、方向、装备测试无回归。（矩阵 9 只断言默认值，TWF 分支留给 S0-F8。）
39. 存档/加载与重放后：每回合额度、攻击动作账本、动作经济三项与重放前一致。

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

## 9. 执行报告（执行侧填写）

### 9.1 基线与提交

- 基线：`872e165a`，分支 `feat/dnd2024-s0f7-weapon-mastery`。G2 依赖的 G1 已先落地：`8a888d4e`（两只手 + Two-Handed）、`80d6ad46`（物品实例身份）。
- **G2-A** `41fcbefe`：来源闭集与 `extra_attack_source` 声明、`dnd2024.attack.spent` 携带 `weapon_ref`、`combat.attack_ledger`、每回合额度、属性调整值判定点、actor 视图 `extra_attack` 投影。
- **G2-B** `8e497158`：Nick 的支付替换（改成 `attack_action`，不产生扣 Bonus Action 事件）。
- **G2-C** `f4ab1d75`：Dual Wielder 专长声明 + 第二把武器资格放宽 + `features.feat_refs` 投影。
- **顺带修正** `c54c486b`：`_extra_attack_projection` 的拒绝理由取**第一个**档案，而不是最后一个（见 §9.13 第 3 条）。

### 9.2 勘误取舍：以 SRD 修正措辞为准

采用 SRD 5.2.1 的修正措辞：**首击仍必须是 Light 武器，放宽的是第二把**。2024 PHB 初印本的「把另一手的非 Light 武器视为具有 Light」不作为实现依据——它会让两把长剑合法，与 SRD 原文冲突。

由此钉死两条：矩阵 22（两把长剑即使有专长也非法，因为账本里没有 Light 武器）、矩阵 18（第二把只需近战且不带 `two_handed`，不必是 Light）。§4.3 的「不同 Light 武器」判定按 SAC 的存在性读法实现，不用集合读法、不用末项读法，理由与反例见 §4.3 补充约束 2。

### 9.3 页码：本切片的偏差项（未从 PDF 实读）

**没有从 PDF 实读 Dual Wielder 的页码。** 工作树内不存在 SRD 5.2.1 的 PDF，`.rulebooks` 目录也不存在，本轮无法取得。

`source_ref` 取 `srd-5.2.1:p87:dual-wielder`，依据是同属 General Feats 块的**两个既有 p87 锚点**：

| 条目 | 现有 `source_ref` |
|---|---|
| `ability_score_improvement` | `srd-5.2.1:p87:ability-score-improvement` |
| `grappler` | `srd-5.2.1:p87:grappler` |

Dual Wielder 按字母序严格落在两者之间（A < D < G），且三者在同一块连续排版，因此 p87 是区间夹逼的结果而非猜测。**但这不是实读**：取得 PDF 后须逐字复核，若页码不符只需改这一处 `source_ref`，不影响任何机械行为。原模板要求的「实读页码」一项标记为**未完成**。

### 9.4 `attack.spent` 的新载荷与账本结构

```json
{"type": "dnd2024.attack.spent", "actor_id": "player:gm",
 "weapon_ref": "item:shortsword@eq-1"}
```

reducer 在 `dnd2024.attack.spent` 末尾调用 `_record_attack_weapon`，维护：

```json
"attack_ledger": {"player:gm": {"round": 1, "turn_index": 0,
                                "weapon_refs": ["item:shortsword@eq-1"]}}
```

`round` + `turn_index` 不匹配即重置为本次的值（与 `feature_usage` 同一模式）。列表按发生顺序追加、**保留重复**——同一把武器打两次仍只有一个参照点（矩阵 32）。借机攻击走 `reaction.spent`，不写入该账本（矩阵 30 / §4.2.4）。

### 9.5 三种来源的准入与拒绝对照

| 来源 | 支付 | 武器资格 | 额外前提 | 拒绝理由（原文串） |
|---|---|---|---|---|
| `light` | `bonus_action` 1 | 本次武器是 Light | — | `the extra attack must be made with a Light weapon` |
| `nick` | **不扣**（`attack_action`） | 本次武器是 Light | mastery 可用且属性为 `nick` | `weapon mastery is not available to this actor` / `this weapon's mastery property is not Nick` |
| `dual_wielder` | `bonus_action` 1 | 本次是近战**武器**、不带 `two_handed`；非 Light 时必须持有专长 | — | `this extra attack must be made with a melee weapon` / `this extra attack cannot be made with a Two-Handed weapon` / `this extra attack needs the Dual Wielder feat` |

三者共用前置：`the Light extra attack was already used this turn`、`the Attack action has not attacked with a Light weapon this turn`、`the extra attack needs a different weapon than the one that enabled it`、`no Bonus Action is left for the extra attack`。检查顺序即玩家提问顺序，报的是第一个真正缺的东西。

### 9.6 Nick 的支付替换

按借机攻击的既有做法**替换支付事件本身**：`nick` 产出的仍是 `dnd2024.attack.extra`，但 `payment` 为 `attack_action`，reducer 在该分支只写账本、不扣 `economy.bonus_action`（矩阵 12–13 直接断言批次里不存在扣 Bonus Action 的记录，而不是「先扣再退」）。矩阵 17：`mastery_override` 把 `nick` 换成 `push` 后，本次武器不再具有 `nick`，改按 `light` 声明则扣 `bonus_action`。

### 9.7 属性调整值判定点与 TWF 留白

`extra_attack_ability_modifier(actor, weapon, source, *, modifier=None)` 是**唯一**判定点，默认 `min(0, modifier)`（保留负值、丢弃正值）。攻击管线把已解析的 `modifier` 传进来，两个调用点（`settlement["modifier"]` 与 `_hit_settlement_events(..., modifier=...)`）因此不会各算一遍。矩阵 9 只断言默认值；Two-Weapon Fighting 的「返回完整 `modifier`」分支**故意不写**，留给 S0-F8，避免在本切片里凭空造一个没有验收的例外。

### 9.8 与 Tactical Master / Cleave / Extra Attack 的边界

- **Tactical Master**：矩阵 17 已覆盖（属性替换先验完再判额外攻击来源）。
- **Cleave**：矩阵 27 改成钉住「普通攻击不写 `light_extra_attack` 额度」。原矩阵要求「先 Cleave 再 `light` 仍成立」，但 Cleave 武器（greataxe / greatsword / halberd / glaive）全部带 `two_handed`，G1 之后它们占满两只手，另一手不可能再有 Light 武器——该组合在规则上无法构成。实现上 Cleave 第二击允许入账，但用的是同一把武器，存在性判定结果不变。
- **Extra Attack**：矩阵 28 断言额外攻击前后 `attacks_per_action` 与 `attacks_remaining` 都不变；额外攻击不走 `attacks_remaining`。

### 9.9 Quick Draw 的 `not_automated` 记录

Quick Draw（`You can draw or stow two weapons that lack the Two-Handed property...`）标记为 **`not_automated`**：本仓库没有「取出/收起武器」的交互层，也没有对应的意图或命令，因此**不开任何入口**。

**与原任务书的偏差**：§4.7.1 的 JSON 契约要求写 `parameters: {"enhanced_dual_wielding": true, "quick_draw": true}`，**未按此实现**。原因有二：

1. 载入期 `_validate_feat_parameters` 的参数词表是**封闭**的（`ADVANCEMENT_FEAT_PARAMETER_KEYS` 当前只有 `ranged_attack_bonus`），且要求参数值是 **1–10 的整数**。布尔值 `true` 会直接被拒，写进去角色一构造就失败。
2. 更重要的是纪律问题：这两个 flag 都**没有消费者**。Enhanced Dual Wielding 的机械效果由战斗准入函数实现，不读目录参数；Quick Draw 完全没有实现。声明一个没人读的参数，会让「已声明」看起来像「已支持」——正是仓库明令禁止的「把可选、可展示计为已支持」。

因此 `dual_wielder` 条目只有 `category` / `source_ref` / `minimum_level` / `repeatable` / `any_ability_minimum` / `ability_choice`，矩阵 26 以「`parameters` 与 `passive_effects` 都不存在」的形式钉住这一取舍。

### 9.10 白名单对照

| 白名单条目 | 实际改动 |
|---|---|
| `srd_advancement_feats.json` 及 en / zh-CN locale | 三份均改：新增 `dual_wielder` 条目与两侧 label |
| `engine.py` / `validation.py` / `resolution.py` / `reducer.py` / `view.py` | A、B 已改前四个；C 只改 `view.py`（`feat_refs` 透传 + `has_dual_wielder`）；`c54c486b` 亦在 `view.py` |
| 新建 `combat/extra_attacks.py` | A 建，B、C 扩充 |
| `features/resolver.py`（仅在需要暴露判定点时） | **未改**：`two_weapon_fighting` 判定点留白给 S0-F8，`dual_wielder` 的所有权从 `features.feat_refs` 读，不需要新入口 |
| 新建两份测试 | `test_dnd2024_light_extra_attack.py`（A/B）、`test_dnd2024_dual_wielder.py`（C） |

白名单之外**没有**任何改动，未碰 `src/engine/**` 与前端。

### 9.11 测试

| 项 | 命令 | 结果 |
|---|---|---|
| G2 聚焦（A/B/C） | `pytest tests/rulesets/test_dnd2024_light_extra_attack.py tests/rulesets/test_dnd2024_dual_wielder.py -q` | **46 passed**（30 + 16） |
| 边界回归 | 上一行再并入 `weapon_mastery` / `tactical_master` / `combat` | **119 passed** |
| 全量 | `pytest tests/rulesets -q -n 4 --dist loadfile` | **11 failed / 1938 passed** |
| 静态检查 | `ruff` **未安装**（`No module named ruff`），改用 `python -m py_compile` | 通过 |
| 空白检查 | `git diff --check 8e497158..HEAD` | 干净 |

全量的 11 条失败**全部**在 `test_dnd2024_character_builder.py`，是已知基线（`unresolved reference 'ability:con' in background:soldier`，已在 clean baseline `872e165a` 的临时 worktree 上复现）。本次没有新增失败，也没有出现此前常见的 `PermissionError: [WinError 5]` 抖动。

### 9.12 存档、重放与确定性

- 账本由事件派生，重放得到相同结果：矩阵 39 断言重新读取后 `attack_ledger` / `feature_usage` / `economy` 三份完全一致。
- 实例身份不使用 uuid / 时间 / 随机 / 进程计数器：`instance_id` 由 `equipment_sequence` 递增铸造，同一操作序列恒得 `eq-n`。
- `extra_attacks.py` 是纯函数层，不读时钟、不读客户端、不读实例。

### 9.13 偏差、停止原因与未决事项

1. **Dual Wielder 页码未实读**（§9.3）。取得 PDF 后复核 `source_ref`。
2. **`parameters` 字段未按原契约写入**（§9.9），有两条独立理由；矩阵 26 已把这一取舍变成断言。
3. **修正了一处 A 遗留的投影语义**（`c54c486b`）：`_extra_attack_projection` 原本保留循环里最后一个档案的拒绝理由，而同一函数的 docstring 承诺的是第一个。持短剑 + 长剑、刚用短剑挥过一击时，玩家会被告知「必须用 Light 武器」——这句话对长剑成立，却掩盖了短剑真正的问题（不能当自己的参照）。改为取第一个后，理由与「玩家最可能想用的那把」一致，也不再随装备顺序漂移。
4. **矩阵 19 / 20 钉在准入函数而非端到端**：G1 之后双手武器占满两手，`greatsword + shortsword` 在实装状态里根本装不成；远程档案会先在弹药闸门被拒。两处都改为直接断言 `extra_attack_blocker` 的返回值，并在测试 docstring 里写明为什么不能走端到端。
5. **未决（不属本切片）**：Two-Weapon Fighting 的完整调整值（S0-F8）、Quick Draw 的取出/收起层、Cleave 与双持并存的规则问题（规则上无法构成，但若未来有单手 Cleave 武器则需重新审视矩阵 27）。

