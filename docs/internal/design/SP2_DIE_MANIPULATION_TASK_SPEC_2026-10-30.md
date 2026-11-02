# 切片任务书 S-P2：骰面操纵（Die Manipulation）

- 阶段：1C 共用原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1C 切片表 S-P2 ——"骰面操纵：最大化、掷后加骰、从敌方总数减骰｜Supreme Healing、Overchannel、Dark One's Own Luck、Cutting Words；统一骰迹与 RNG 顺序"
- 前置依赖：**S-P1（子职特性所有权 + 特性参数消费 API）必须先合入**——本切片两个端到端载体（极效治疗、超限导能）均为子职特性，`has_feature` 在 S-P1 合入前对子职特性恒为 False，切片无法端到端验证。S-P3（检定反应中断）反向依赖本切片的事件契约，必须在本切片之后合入。基线：`4ef1d33d`（P6 misty_step 已合入，工作区干净）；与 1B 后续切片（P7+）并行推进时，resolution.py / validation.py 改动前先重读工作区核对行号漂移。
- 状态：未开工

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **极效治疗（Supreme Healing，生命领域 17 级）**：
   - 出处：`.rulebooks/dnd5r/玩家手册2024/角色职业/牧师/生命领域.htm` L60-61 ——"当你需要用一道法术或引导神力掷一枚或多枚骰子，以决定为一个生物恢复的生命数值时，你无需掷骰，直接为每个骰子取最高值。例如，一道法术为某一生物恢复2d6生命值，则其结果直接取12。"
   - 语义拆解：
     - 作用对象是**骰节点**："为每个骰子取最高值"——属性修正与固定加值（`heal_ability_modifier_node` 的常量节点）照常附加，不参与最大化；
     - 2024 版为 **17 级**（2014 版为 14 级，不沿用 2014 等级与数值），与 `srd_subclasses.json` L24 `"17": ["supreme_healing"]` 一致；
     - "为一个生物"：现有 healing 分支本就在 `target_ids` 循环内逐目标掷骰（锚点 A7），语义天然一致；
     - 临时生命值不是"恢复生命值"：`temp_hp` 分支（false_life 族）不受影响；
     - "法术**或引导神力**"：当前目录的引导神力治疗（维持生命 Preserve Life）为 5×牧师等级固定值、无骰，天然不受影响；本切片不建引导神力管线。
   - 实现语义：**骰节点变换**——AST 中每个 `NdM` 骰节点替换为常量 `N×M`；RNG 零消耗；骰迹（rolls）为空；事件追加 `maximized: true` 标记（沿 SP1 `crit_threshold` "仅变化时携带字段"先例）。

2. **超限导能（Overchannel，塑能师 14 级）**：
   - 出处：`.rulebooks/dnd5r/玩家手册2024/角色职业/法师/塑能师.htm` L43-44 ——"当你用一个一至五环的法术位施展一个会造成伤害的法师法术时，你能在施展它的回合中令其造成的伤害取最大值。你首次这么做时不会承受任何负面效应。但如果你在完成一次长休前再次使用该特性，则你会在施展法术后立即受到每环阶2d12暗蚀伤害。该伤害无视任何抗性或免疫。你在完成一次长休前每多使用此特性一次，每环阶造成的伤害就提升1d12。"
   - 语义拆解：
     - "你能" = 施法者选择：以 `cast_spell` 意图参数 `maximize_damage: true` 表达（`resist_type` P5 / `teleport_distance` P6 先例，锚点 A13）；
     - 门控 = 特性所有权（塑能师 14 级）+ **1–5 环法术位**（戏法 slot 0 不适用）+ "会造成伤害的法术"（当前伤害承载 mode：`spell_attack` 带 damage / `saving_throw` 带 damage / `automatic_damage`；`triggered_damage` 是命中后 rider 的独立路径，不在此门控内）；
     - 效果 = 该次施法的**全部伤害骰**最大化（多目标、多 beam 逐份均最大化）；
     - "每环阶 2d12 暗蚀伤害" = `2 × slot_level` 枚 d12，"无视任何抗性或免疫" = 直接落账、**不经 `_resist_damage`**；自伤若打断施法者自己的专注（例如先给自己套了石肤术再超限导能施法），照常触发专注豁免——伤害触发专注检定是通用规则；
     - **本切片显式裁剪**："首次免费"与"每多一次递增 1d12"需要长休作用域的使用计数，当前无此原语（见 §1.2 裁剪 2）——本切片每次使用一律收取 `2 × slot_level` d12（偏离方向 = 对玩家更严，fail-safe 无平衡漏洞）。

3. **掷后加骰 / 从敌方总数减骰（契约面，本切片定契约、S-P3 落地接线）**：
   - 载体原文：
     - 黑暗强运（Dark One's Own Luck，邪魔宗主 6 级）：`.rulebooks/dnd5r/玩家手册2024/角色职业/魔契师/邪魔宗主.htm` L36-37 ——"当你进行**属性检定或豁免检定**时，你可以使用该特性为此次掷骰增添一个d10骰。你可在看到掷骰结果后，其结果生效前使用该特性。你可以使用此特性的次数等于你的魅力调整值（至少一次），但你在一次检定中只能使用此特性一次。你在完成一次长休后恢复所有已消耗的次数。"（**2024 版不含攻击检定**——2014 版是属性检定+攻击检定；执行侧不得沿用 2014 语义。）
     - 语出惊人（Cutting Words，逸闻学院 3 级）：`.rulebooks/dnd5r/玩家手册2024/角色职业/吟游诗人/逸闻学院.htm` L18-19 ——"当一名你可见的位于你60尺内的生物进行伤害掷骰、成功于一次属性检定或攻击检定时，你能够以反应消耗一次诗人激励使用次数，并掷诗人激励骰，然后从该生物的掷骰结果中减去诗人骰的骰值，这将降低其造成的伤害或可能使这次检定的成功变为失败。"（2024 版触发条件含"成功于"——检定类窗口在成功判定后、伤害类在掷骰时。）
     - 超凡技艺（Peerless Skill，逸闻学院 14 级，同文件 L26-27）：失败后自身加骰——主计划已归 P10 属性检定域（主计划 L147），本切片不处理。
   - 这两个载体的共同结构 = **"d20/伤害掷骰已产生、结果生效前"的决策窗口（掷出后、结算前）**。窗口本身（谁获得决策、何时弹 decision、消耗反应还是资源池）是 **S-P3 的原语**（主计划 L136："扩展切片 3 反应注册表到检定窗口，区别于 P7 施法反应"）；本切片交付窗口内发生的**骰面算术与事件契约**（§3.8），S-P3 按契约逐字落地。

4. **RNG 顺序锁定（"统一骰迹与 RNG 顺序"，本切片核心交付，S-P2/S-P3/S-P10 共同遵守）**：
   1. d20 检定骰（含优劣势第二枚——既有顺序不变）；
   2. 条件驱动的骰式加值骰（bless d4——S-P10 契约，既有位置不变）；
   3. 窗口决策骰（DOOL d10 / CW 诗人骰 / Lucky 追加 d20——S-P3，决策确定后按事件顺序逐枚）；
   4. 后续结算骰（伤害骰 / 治疗骰；被最大化则零消耗）；
   5. 超限导能自伤骰（整道施法全部目标结算完毕、胜利判定之后，一次性）。

5. **骰面操纵家族形态学（共享地图，禁止执行者扩大）**：
   - 最大化 maximize：AST 骰节点 → 常量（本切片落地，治疗面 + 法术伤害面）；
   - 掷后加骰 add：检定 total + 新掷骰（算术契约本切片 §3.8；窗口与资源接线 S-P3）；
   - 减骰 subtract：total / 伤害 − 新掷骰（同上）；
   - 重掷 reroll：Savage Attacker / 巨武器战斗风格 / Healer 治疗骰重掷 1（主计划 L173-174 归 S-P2）——**本切片显式裁剪**（§1.2 裁剪 3），未来在 `_rng_dice_roller` 骰源包装层实现，与本切片 maximize 同居 `action_adapter.py`、不进通用引擎；
   - 优劣势双骰与暴击阈值**不是**本家族成员（归属 `check_modes.py` / S-P1），禁止混流。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **裁剪：决策窗口与载体接线**。DOOL / Cutting Words / Lucky 的窗口、反应与资源池消耗（诗人激励池、幸运点池）全归 S-P3；本切片不产生任何新的 `decision.pending` 类型、不改 `_hit_reaction_offer` 家族（锚点见 S-P3 任务书）。§3.8 的字段名、符号语义、RNG 位次对 S-P3 是规范性契约。
2. **裁剪：Overchannel 免费次与递增伤害**。"长休前首次免费、每多一次每环阶 +1d12"需要长休作用域的累计计数，现有资源池（rage/second_wind）是消耗型池且走 capability 意图路径，无法承载递增计数。本切片每次使用一律收取每环阶 2d12；免费次与递增登记迁移债 D1。方向说明：对玩家更严（多收费），无"免费无限最大化"的平衡漏洞。
3. **裁剪：重掷形态**（主计划 L173-174）。同家族第 4 形态，等阶段二专长批次（Savage Attacker / GWF / Healer）落地，迁移债 D3。
4. **裁剪："法师法术列表"溯源**。overchannel 门控按特性所有权 + 伤害 mode + 环阶判定，不校验"法术是否来自法师列表"——跨列表来源（magic initiate 类）的目录溯源字段不存在；塑能师借非法师列表法术施放被最大化属可接受近似，迁移债 D2。
5. **裁剪：其余载体特性全部保持幽灵**。生命领域（门徒/领域法术/维持生命/神祝医者）、塑能师（塑能学者/强力戏法/法术塑形/强效塑能）、邪魔宗主（邪魔法术/黑暗赐福——已归 S-P4/邪魔体魄/直坠噩梦）、逸闻学院（附赠熟练/魔法探秘/超凡技艺）——归阶段二内容批次，本切片零触碰。
6. **裁剪：前端与 feed 投影**。`maximized` / `overchannel_cost` / penalty 系列字段进事件存储（reducer 原样全量），`_recent_combat_events` 投影白名单零改动（锚点 A23）；有真实展示需求再白名单（迁移债 D5）。
7. **保留现状：S-P10 事件契约不动**。`bless_bonus → check_bonus/check_bonus_rolls` 更名归 SP10 白名单；本切片对 bless 相关代码零触碰，penalty 字段是纯增量。合并顺序：本切片与 SP10 互不阻塞（不同字段、不同分支），S-P3 依赖两者最终形态。
8. **保留现状：攻击检定分支零改动**。Overchannel 限定"法术"（RAW），武器伤害路径 `_attack_events`（含 `attack_damage_node` 消费）不动；CW 对敌方攻击检定的减骰接线归 S-P3。
9. **不适用面显式排除**：`temp_hp`（false_life 族）、`stabilize` / `dispel` / `buff` / `debuff` / `teleport` 分支零改动；heal 分支 `max(1, healing)` 下限保留（最大化后不可能触底，防御性不动）；`cannot_regain_hp` 门控与最大化无交互（治疗值先最大化、再被阻断，delta 0 语义不变）。

## 2. 代码事实锚点（基线 `4ef1d33d`，行号随工作区漂移需重读核对）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/combat/action_adapter.py` L45-64 | `formula_node_from_dice_formula`：NdM / NdM+K / 固定值 → AST（dice/constant/add 节点） |
| A2 | action_adapter.py L67-86 | `double_dice_counts`：重击骰数翻倍——**骰节点变换的既有先例**（walk add/subtract/multiply/min/max 的 args，未知 op 原样返回）；`maximize_dice_nodes` 与其同构 |
| A3 | action_adapter.py L89-118 | `spell_formula_node`：法术骰式 + 升环（同面骰数累加）+ 戏法升级（×4/×3/×2）→ AST；healing/upcast_healing 经此入树 |
| A4 | action_adapter.py L121-138 | `_rng_dice_roller`（宿主 rng 包装、骰迹按骰顺序展开）+ `roll_damage_node`（通用求值器求值 → 值 + 按骰顺序明细） |
| A5 | action_adapter.py L164-172 | `heal_ability_modifier_node`：治疗 + 施法属性修正（常量节点，不参与最大化） |
| A6 | action_adapter.py L1-9 模块 docstring | 职责边界：**generic engine（`src/engine/combat_formulas.py`）不出现任何 D&D 分支**——maximize 是 D&D 侧变换，必须落在本层，禁止进通用求值器 |
| A7 | `src/rulesets/dnd2024/combat/resolution.py` L557-574 | healing 分支：`healing_node`（`spell_formula_node` + `add_spell_ability` → `heal_ability_modifier_node`）→ `roll_damage_node` → `max(1, healing)` → `cannot_regain_hp` 门控 → `resource.changed` 事件（`healing: true` / `rolls` / `blocked_by` 条件键，L572 `**({...} if blocked else {})` 条件字段先例）——**Supreme Healing 消费点** |
| A8 | resolution.py L439-445 / L510-515 / L536-540 | 三处法术伤害骰构造（`spell_attack` 命中后（L443-444 crit 翻倍先于求值）/ `saving_throw` / `automatic_damage`）——**Overchannel 消费点 ×3**；锁定顺序：crit 翻倍 → 最大化 → 求值（数学上等价，锁死消除歧义） |
| A9 | resolution.py L694-701 | `_spell_events` 收尾：`living_enemy_ids` 胜利判定（L694-700）→ `return events`（L701）——**Overchannel 自伤插入点**：胜利判定之后、return 之前 |
| A10 | resolution.py L362-369 | `_spell_caster`（起源专长施法属性覆盖）；`slot_level = int(intent.get("slot_level", spell["level"]))`（戏法 = 0）——门控判定数据源 |
| A11 | resolution.py L313-316 | rage 消费先例：`self.features.feature_value(self._feature_character(actor), "rage", "rage_damage", 0)`——**特性 id + 参数键**读取模式，本切片沿用，不建新 resolver API |
| A12 | `src/rulesets/dnd2024/combat/reaction_spells.py` L99-114 | `has_feature` 先例：`self.features.has_feature(character, "uncanny_dodge")` + `feature_value(..., "damage_multiplier", 0)`——被动子职特性消费同构 |
| A13 | `src/rulesets/dnd2024/combat/validation.py` L407-508 | `_validate_spell`：kind → spell/effect → prepared → reaction_defense → trigger → cost → slot → target；**`resist_type` 先例 L496-499**（"效果声明才接受意图参数，否则报错"）——`maximize_damage` 校验的形状与位置先例；函数内 actor 视图与 slot 局部变量均已存在 |
| A14 | validation.py L463-466 | beneficial mode 集合（P6 加 teleport）；伤害承载判定用 `effect["damage"]` 存在性 + mode |
| A15 | `src/rulesets/dnd2024/features/resolver.py`（S-P1 交付后） | `_subclass_feature_ids` 合并进 `features_for`；`has_feature` / `feature_value` 既有；`_validate_declarations` L527-556——SP1 已为其建 **parameters 值校验先例**（crit_threshold，int 域），本切片为 overchannel 参数沿同一模式扩校验 |
| A16 | `src/rulesets/dnd2024/features/models.py` L266-271 | 特性 parameters 任意标量（`_require_scalar`），无封闭键集 |
| A17 | `src/rulesets/dnd2024/combat/view.py` L337-362 | `_feature_character`：战斗边界只携带 build/abilities/class resources/equipment——`has_feature` 在此视图上对子职特性生效（依赖 SP1） |
| A18 | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` | 结构：`labels` L7 / `classes` L33；现有 monk/paladin/fighter/barbarian/rogue 五个 class 段——**cleric / wizard 段不存在**，本切片新增；被动特性声明形状 = id / minimum_level / source_ref / summary（/ parameters）（SP1 §3.1 先例） |
| A19 | `templates/rulesets/dnd2024_srd/content/subclass_catalogs/srd_subclasses.json` L20-25 / L74-79 | life_domain `"17": ["supreme_healing"]`；evoker `"14": ["overchannel"]`——**幽灵 id 已在位**，只缺 catalog 声明；SP1 的"声明等级 = 子职表授予等级"校验对二者天然成立 |
| A20 | `src/rulesets/dnd2024/combat/catalog.py` L15-20 | `SPELL_MODES` frozenset（P6 后含 teleport）——门控集合引用，本切片不改 |
| A21 | `src/rulesets/dnd2024/combat/engine.py` L1070-1092 一带 | `_concentration_events(combat, target, damage, rng)`（SP10 A7 已核，P6 后微移）——Overchannel 自伤触发专注豁免的调用先例（resolution 内 4 处调用：L351/L460/L532/L553） |
| A22 | tests | `SequenceRng` 基建：`tests/rulesets/rulesets/test_dnd2024_combat.py` L20；`advance()` 高等级构造：`test_dnd2024_sneak_attack.py` L26-32；heal 既有断言散在 combat 测试与 P 系列测试 |
| A23 | `src/rulesets/dnd2024/runtime.py` L1119-1130 | `_recent_combat_events` allowed_fields 白名单——本切片零改动；事件存储全量保留新字段 |
| A24 | 事件契约现状 | `check.resolved` 现有字段：`advantage_mode` / `advantage_sources` / `rolls` / `natural` / `modifier` / `bless_bonus` / `total` / `target` / `success` / `critical`（+ SP1 `crit_threshold`、SP10 `check_bonus(_rolls)` 待各自落地）；`resource.changed` 伤害/治疗事件现有字段：`delta` / `amount` / `damage_type` / `critical` / `rolls` / `resisted` / `resistance` / `healing` / `blocked_by` |

## 3. 白名单改动规格（逐文件）

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`（content + locales/en + locales/zh-CN 三份）

新增两个 class 段（A18）：

```json
"cleric": {
  "features": [
    {
      "id": "supreme_healing",
      "minimum_level": 17,
      "source_ref": "srd-5.2.1:p40:supreme-healing",
      "summary": "Whenever you cast a spell or use Channel Divinity to restore Hit Points with a die roll, you can forgo rolling: each die instead contributes its maximum value.",
      "parameters": {}
    }
  ]
}
```

```json
"wizard": {
  "features": [
    {
      "id": "overchannel",
      "minimum_level": 14,
      "source_ref": "srd-5.2.1:p82:overchannel",
      "summary": "When you cast a Wizard spell of level 1-5 that deals damage, you can maximize its damage. After the spell resolves, you immediately take 2d12 Necrotic damage per spell slot level, ignoring Resistance and Immunity.",
      "parameters": {
        "max_slot_level": 5,
        "cost_dice_per_slot": 2,
        "cost_die_sides": 12,
        "cost_damage_type": "necrotic"
      }
    }
  ]
}
```

- `supreme_healing` 无参数——**存在即语义**（被动常开），消费点只判 `has_feature`（§3.4）；
- `overchannel` 的环阶上限与代价数值全部走 parameters（rage_damage 先例，A11），**禁止在 src 出现 5 / 2 / 12 / "necrotic" 字面量**；
- 三份目录的 `labels` 同步：`"supreme_healing": "Supreme Healing"` / `"overchannel": "Overchannel"`；zh-CN 用规则书译名：极效治疗、超限导能。

### 3.2 `src/rulesets/dnd2024/features/resolver.py`

`_validate_declarations`（A15）追加 overchannel 参数值校验（沿 SP1 crit_threshold 模式，fail closed，消息含 feature id 与键名）：

- `max_slot_level`：int 且非 bool 且 `1 <= v <= 9`；
- `cost_dice_per_slot`：int 且非 bool 且 `1 <= v <= 10`；
- `cost_die_sides`：int 且非 bool 且 `2 <= v <= 20`；
- `cost_damage_type`：非空 str；
- 键缺失按缺省值消费（feature_value 的 default 参数），不做"必须四键齐"的强校验——目录漂移只降级不破坏。

其余不动：所有权合并与 `min_feature_value` 由 SP1 交付；本切片**不新增 resolver API**。

### 3.3 `src/rulesets/dnd2024/combat/action_adapter.py`

新增模块级函数（紧邻 `double_dice_counts`，同一变换家族）：

```python
def maximize_dice_nodes(node: Mapping[str, Any]) -> dict[str, Any]:
    """骰面最大化（Supreme Healing / Overchannel）：每个骰节点 → 常量节点。

    D&D 侧 AST 变换，与 double_dice_counts 同构：RNG 零消耗、骰迹为空。
    调用方约定：先 crit 翻倍、后最大化（数学等价，锁死顺序）。
    """
```

- walk 形状与 `double_dice_counts` 完全一致（A2）：`op == "dice"` → `_parse_ndm` → `{"op": "constant", "value": count * sides}`；`op in {"add", "subtract", "multiply", "min", "max"}` → 递归 args；其余 op 原样返回（AST 仅由 combat_formulas 白名单 op 构造，未知 op 不可能出现，行为与 A2 一致）；
- `deepcopy` 入参，返回新树；
- `__all__` 追加 `"maximize_dice_nodes"`。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py` healing 分支（Supreme Healing）

A7 段，`healing_node` 构建完成（含 `heal_ability_modifier_node`）之后、`roll_damage_node` 之前插入：

```python
maximized = self.features.has_feature(self._feature_character(actor), "supreme_healing")
if maximized:
    healing_node = maximize_dice_nodes(healing_node)
```

- 事件（L568-573）沿 L572 条件键风格追加：`**({"maximized": True} if maximized else {})`；
- 最大化后 `roll_damage_node` 返回 `rolls == []`（无骰可掷）、RNG 零消耗——`max(1, healing)` / `cannot_regain_hp` 门控 / upcast 全不动；
- `maximize_dice_nodes` 从 `.action_adapter` import（既有 import 行追加）。

### 3.5 `src/rulesets/dnd2024/combat/resolution.py` 三处伤害分支 + 收尾（Overchannel）

1. 私有辅助（`_spell_events` 上方模块内）：

   ```python
   def _maybe_overchannel(self, intent: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
       """门控已由 _validate_spell 完成（fail closed）；此处只做变换。"""
       if not intent.get("maximize_damage"):
           return node
       return maximize_dice_nodes(node)
   ```

2. 三处调用点（A8）：`spell_attack`（L439-444，crit 翻倍之后）、`saving_throw`（L511-515）、`automatic_damage`（L536-540），在各自 `roll_damage_node` 之前包装：`damage_node = self._maybe_overchannel(intent, damage_node)`。豁免减半（L516-517）在最大化之后的总额上执行（RAW：先取最大、后减半）。

3. 收尾自伤（A9 位置，胜利判定之后、`return events` 之前）：

   ```python
   if intent.get("maximize_damage"):
       character = self._feature_character(actor)
       per_slot = int(self.features.feature_value(character, "overchannel", "cost_dice_per_slot", 2) or 0)
       sides = int(self.features.feature_value(character, "overchannel", "cost_die_sides", 12) or 0)
       damage_type = str(self.features.feature_value(character, "overchannel", "cost_damage_type", "necrotic") or "")
       cost_node = formula_node_from_dice_formula(f"{per_slot * slot_level}d{sides}")
       cost, cost_rolls = roll_damage_node(cost_node, rng)
       events.append({
           "type": "resource.changed", "resource": "hp", "target_id": actor_id,
           "delta": -cost, "amount": cost, "damage_type": damage_type,
           "overchannel_cost": True, "rolls": cost_rolls,
       })
       events.extend(self._concentration_events(actor, cost, rng))
   ```

   - **不经 `_resist_damage`**（"无视任何抗性或免疫"）；事件无 `resisted` 字段即语义证明；
   - 自伤可致施法者濒死——走 reducer 既有死亡语义，零特判分支；
   - RNG 位次锁定：整道施法所有目标结算 + 胜利判定之后（§1.1-4 第 5 位）；
   - `actor` 用 L362 施法者视图；`slot_level` 用 L369 既有局部变量。

### 3.6 `src/rulesets/dnd2024/combat/validation.py` `_validate_spell`

`resist_type` 块（L496-499）之后追加，同形状（A13）：

```python
maximize = intent.get("maximize_damage")
if maximize is not None:
    if not isinstance(maximize, bool):
        raise CombatIntentError("maximize_damage must be a boolean")
    character = self._feature_character(actor)
    max_slot = int(self.features.feature_value(
        character, "overchannel", "max_slot_level", 5) or 0)
    if not self.features.has_feature(character, "overchannel") or max_slot < 1:
        raise CombatIntentError("overchannel is required to maximize damage")
    if effect["mode"] not in {"spell_attack", "saving_throw", "automatic_damage"} \
            or not effect.get("damage"):
        raise CombatIntentError("this spell does not deal damage")
    if not 1 <= int(slot_level) <= max_slot:
        raise CombatIntentError("maximized casting requires a spell slot of level 1-5")
```

- 消息字符串固定（可审计、可断言）；`max_slot` 上限随目录参数走，消息里的 "1-5" 是规则语义描述（实际边界由参数决定）；
- 戏法（slot_level 0）、6 环及以上、治疗/buff/传送法术、无伤害公式的 saving_throw 法术全部拒绝；
- resolution 侧（§3.5）信任本校验，不做二次防御（既有模式）。

### 3.7 tests

新文件 `tests/rulesets/test_dnd2024_die_manipulation.py`（与 `test_dnd2024_misty_step.py` 同级）：`advance()` 构造 17 级牧师 / 14 级法师（基建 A22）；测试矩阵见 §4。既有测试零修改（§4 T16 回归验证）。

### 3.8 掷后加骰 / 减骰事件契约（规范性文本，无代码；S-P3 按此逐字落地）

1. **减骰（Cutting Words 面）**——`check.resolved` 事件（attack / spell_attack / saving_throw / save_repeat / concentration 五面同构）：
   - 新增字段：`"check_penalty": int`（从 total 减去的骰值总额）、`"check_penalty_rolls": [int]`（骰面明细，按 RNG 位次）；
   - 语义：`total = natural + modifier + check_bonus（若有，SP10 契约） − check_penalty`；`success` 判定用减后 total；
   - 伤害掷骰面：`resource.changed` 事件新增 `"penalty": int` / `"penalty_rolls": [int]`，`amount` 为减后值；
   - 字段仅在实际发生减骰时携带（沿 `crit_threshold` / `maximized` 先例）。
2. **加骰（Dark One's Own Luck 面）**——**不新增字段**：复用 SP10 的 `check_bonus` / `check_bonus_rolls`（rolls 列表按 RNG 位次扩展），窗口骰与 bless 骰同字段、可审计合并。
3. **RNG 位次**：§1.1-4 锁定的第 3 位（bless 之后、伤害/治疗骰之前）；多决策按事件顺序逐枚。
4. 本切片不落任何 penalty / 窗口代码；S-P3 的实现若与本文冲突，以先合入的 SP10 + 本契约共同裁定。

## 4. 测试矩阵

| # | 文件 | 用例 | 断言要点 |
|---|---|---|---|
| T1 | die_manipulation | 目录加载 | 三份 srd_class_features 可读；supreme_healing（17 级）/ overchannel（14 级）声明与子职表等级一致（SP1 校验通过即证） |
| T2 | die_manipulation | 所有权 | 17 级牧师 `has_feature("supreme_healing")` True、16 级 False；14 级法师有 overchannel、13 级 False |
| T3 | die_manipulation | 变换单元 | `maximize_dice_nodes`：`2d8→16`；add 树 / multiply/min/max 递归正确；constant 原样；先 `double_dice_counts` 后 maximize = 面值×2；入参树不被修改 |
| T4 | die_manipulation | 极效治疗 cure_wounds | amount = 16 + 施法属性修正；`rolls == []`；事件 `maximized: true`；SequenceRng 该位零消耗（后续骰不位移） |
| T5 | die_manipulation | 极效治疗升环 | healing_word / cure_wounds 升环后骰数增加且全部最大化 |
| T6 | die_manipulation | 回归：低等级 | 16 级（或更低）牧师 cure_wounds 正常掷骰、事件无 `maximized` 字段 |
| T7 | die_manipulation | temp_hp 排除 | false_life（temp_hp 分支）照常掷骰——既有 false_life 测试回归 + 事件无 maximized |
| T8 | die_manipulation | 校验拒绝 | 非塑能师 / 13 级法师 `maximize_damage: true` → "overchannel is required…"；治疗法术 → "this spell does not deal damage"；戏法（slot 0）与 6 环 → slot 拒绝；非 bool 值 → 类型拒绝；不携带参数的施法零影响 |
| T9 | die_manipulation | saving_throw 面 | thunderwave 1 环 maximize：失败目标伤害 = 骰面最大；成功目标 = 最大值减半（先最大化后减半）；damage 事件 `rolls == []` |
| T10 | die_manipulation | automatic_damage 面 | magic_missile（3d4+3 → 15）+ 多 target_ids 逐份最大化 |
| T11 | die_manipulation | spell_attack 面 | scorching_ray maximize：逐 beam 最大化；天然 20 时先翻倍后最大化（顺序锁定）；check 事件不带 maximized（伤害面专属字段） |
| T12 | die_manipulation | 自伤 | 1 环 maximize 施法：施法者收到 2d12 暗蚀；事件 `overchannel_cost: true`、`damage_type: "necrotic"`、rolls 2 枚；RNG 位次 = 全部伤害结算与胜利判定之后；胜利事件先于自伤事件 |
| T13 | die_manipulation | 自伤无视抗性 | stoneskinned / rage 状态的施法者自伤不减免：事件无 `resisted` 字段、amount = 骰面值 |
| T14 | die_manipulation | 自伤触发专注 | 施法者先给自己石肤术（专注中）→ maximize 施法 → 自伤后出现 concentration 检定事件 |
| T15 | die_manipulation | 自伤濒死 | 低血量施法者 maximize → hp 归零走既有濒死 reducer 语义，无 overchannel 特判分支 |
| T16 | 回归 | 全套 | `tests/rulesets` 全绿：cure_wounds / healing_word / bless 既有断言、P 系列（false_life / misty_step / hold_person / stoneskin / scorching_ray）零改动零漂移 |

## 5. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_die_manipulation.py -x -q
python -m pytest tests/rulesets -x -q
git grep -n "supreme_healing" -- src        # 命中收敛：resolution.py 1 处
git grep -n "overchannel" -- src            # 命中收敛：resolution.py + validation.py
git grep -n "maximize_dice_nodes" -- src    # action_adapter 定义 + resolution 消费
git grep -n "check_penalty" -- src          # 零命中（契约归 S-P3，本切片不落码）
```

## 6. 禁止事项

1. 禁止把 maximize / 任何骰面操纵逻辑放进 `src/engine/combat_formulas.py`（generic engine 零 D&D 分支，锚点 A6）；变换一律落 `action_adapter.py`。
2. 禁止让最大化影响武器伤害路径（`_attack_events` / `attack_damage_node` 零改动——Overchannel RAW 限定法术）。
3. 禁止让 Supreme Healing 影响 `temp_hp` 分支（临时生命值 ≠ 恢复生命值）。
4. 禁止在 src 出现 `"necrotic"`、`5`（环阶）、`2`/`12`（代价骰）字面量——全部经 overchannel parameters 读取（A11 rage 先例）；测试与目录 JSON 除外。
5. 禁止触碰 bless 相关代码（`bless_bonus` 字段与其 5 处消费点归 SP10 白名单；本切片 penalty 契约为纯增量）。
6. 禁止实现 S-P3 的窗口 / 反应 / 资源决策（DOOL、Cutting Words、Lucky、诗人激励池保持幽灵；不新增 decision 类型）。
7. 禁止给 `_recent_combat_events` 白名单加字段（feed 不消费 maximized / overchannel_cost / penalty 系列）。
8. 禁止把优劣势 / crit 阈值与骰面操纵混流（分属 check_modes / S-P1 家族）。
9. 禁止在 `maximize_dice_nodes` 里扩展新 AST op 或对未知 op 抛错（行为与 `double_dice_counts` 保持一致：原样返回）。
10. 既有约定自动适用：未知键值 fail closed、不猜目录、行号漂移以工作区重读为准、只改白名单文件。

## 7. 停止条件

出现以下任一情况，停止并上报，不得带疑继续：

1. S-P1 未合入（`features_for` 不含子职特性）——`has_feature("supreme_healing")` 恒 False，切片无法端到端验证，停止。
2. SP10 已合入且其 `check_bonus` 契约与本任务书 §3.8 假设冲突（字段名 / rolls 合并方式不同）——停止，与 SP10 执行侧协调后再动。
3. `evaluate_formula_trace` 对 constant-only 节点返回非空 rolls 或抛错（通用引擎契约问题，不在本切片修复范围）。
4. healing 分支出现 0 骰公式（纯固定值治疗法术）——当前目录无此形态；出现即数据漂移，停止上报。
5. overchannel 自伤致施法者 hp ≤ 0 时 reducer 路径与既有濒死语义冲突（需要特判才能通过测试）——停止上报。
6. 测试需要 17 级牧师 / 14 级法师而 `advance()` 基建无法构造（升级链在子职强校验处拒绝）——测试基建缺口，停止上报。
7. 工作区 `resolution.py` / `validation.py` 相对 `4ef1d33d` 出现 1B 后续切片（P7+）的未合入修改——重核行号与分支结构后再继续。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

```
## S-P2 执行报告
- 基线：HEAD=<commit>（S-P1 合入后重核行号漂移：<文件:旧行→新行列表>）
- 改动文件清单（须与 §3 白名单一致）：
  - [ ] §3.1 srd_class_features.json ×3
  - [ ] §3.2 features/resolver.py
  - [ ] §3.3 action_adapter.py
  - [ ] §3.4 resolution.py healing 分支
  - [ ] §3.5 resolution.py 伤害分支 ×3 + 收尾
  - [ ] §3.6 validation.py
  - [ ] §3.7 tests/test_dnd2024_die_manipulation.py
- 测试矩阵：T1…T16 逐项 ▢ 通过 / 裁剪原因
- 验收命令输出摘要（5 条命令）
- 停止条件触发记录（无 / 条目号+处理）
- 与 S-P1 / SP10 的合并顺序说明（三者都改 class_feature catalog 与检定事件面）
- §3.8 契约若有偏差，逐条列出与 S-P3 执行侧的确认结论
```

### 迁移债登记（本切片显式产生，禁止在后续切片里"顺手"处理）

| # | 债 | 归属 |
|---|---|---|
| D1 | Overchannel 长休前首次免费 + 每多一次每环阶 +1d12 递增（长休作用域使用计数） | 资源状态原语切片（1D 后段 / 独立小切片） |
| D2 | "法师法术列表"跨列表溯源（magic initiate 类来源的 overchannel 误最大化） | 阶段二内容管线（法术来源目录字段） |
| D3 | 重掷形态：Savage Attacker / Great Weapon Fighting / Healer 治疗骰重掷 1（主计划 L173-174）——`_rng_dice_roller` 包装层 | 阶段二专长批次 |
| D4 | DOOL（+1d10，2024 限定属性/豁免检定）/ Cutting Words（−诗人骰，含伤害掷骰面）/ Lucky 的窗口接线与资源池（诗人激励、幸运点） | S-P3（本切片 §3.8 契约） |
| D5 | `maximized` / `overchannel_cost` / penalty 系列字段进 feed 投影白名单 | 有前端展示需求时 |
| D6 | Peerless Skill（失败后自身加骰，逸闻学院 14 级） | P10 属性检定域（主计划 L147 已定） |
| D7 | 引导神力掷骰治疗（无骰的维持生命之外，未来有骰的 CD 治疗）对最大化的消费 | 阶段二引导神力管线 |
