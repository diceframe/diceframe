# S0-F17 任务书：Cunning Strike（偷袭骰池预算与六种效果）

| 项 | 值 |
|---|---|
| 切片 | S0-F17（原停工报告已作废，见 §1.3） |
| 规则基准 | `.rulebooks/dnd5r/玩家手册2024/角色职业/游荡者/游荡者.htm` L133–159（5 级三种、11 级双效果、14 级再加三种，DC = 8 + 熟练加值 + 敏捷调整值） |
| 代码基线 | `b0496921`（`origin/main`，含 GAP-43 全栈 #848/#853–#866） |
| 直接前置 | GAP-43 A–H（`ATTACK_DECLARATIONS` 已冻结）、S0-F6（#868，同一条 `rest`/资源记账链路的近邻） |
| 在途冲突 | **#869 正在改 `combat/resolution.py` / `combat/validation.py` / `tests/architecture/combat_coupling_baseline.py`**：F17 的实现工作树等它合入后再开 |
| 状态 | 计划（F17a–F17e 未开工） |
| 关联 GAP | GAP-48（声明没有面向玩家的投影与 UI）、GAP-49（借机攻击没有声明通道） |

## 1. 规则基准与现状

### 1.1 原文（要点摘录）

> **5 级：诡诈打击 Cunning Strike** 你学会了如何使你的偷袭更加狡诈的方法。当你造成偷袭伤害时，你可以选择下列一种诡诈打击的效果加入到本次偷袭中。每种效果都需要花费一定数量的偷袭伤害骰才能生效。**你必须在投掷伤害前扣除所花费的偷袭的伤害骰**，而效果会在这次攻击的伤害产生后立即发生。……如果一种诡诈打击的效果要求目标进行豁免，其 **DC 为 8 + 你的熟练加值 + 你的敏捷调整值**。

| 效果 | 环 | 花费 | 豁免 | 结果 | 额外前提 |
|---|---|---|---|---|---|
| 淬毒 Poison | 5 | 1d6 | 体质 | 中毒 1 分钟；目标可在**每次自己回合结束时**重豁免，成功即终止 | 必须携带一套制毒工具 |
| 摔绊 Trip | 5 | 1d6 | 敏捷 | 倒地 | 目标为**大型及以下** |
| 撤步 Withdraw | 5 | 1d6 | — | 攻击后立刻移动至多**速度的一半**，**不引发借机攻击** | — |
| 恍惚 Daze | 14 | 2d6 | 体质 | 目标**下一回合**只能在「移动 / 一个动作 / 一个附赠动作」中选一项 | — |
| 击昏 Knock Out | 14 | 6d6 | 体质 | 昏迷 **1 分钟或受到任何伤害**；每在自己回合结束时可重豁免 | — |
| 眩目 Obscure | 14 | 3d6 | 敏捷 | 目盲，直到**其下个回合结束** | — |

> **11 级：进阶诡诈打击** 可以一次性选用**两种**效果，每种独立扣除对应的偷袭伤害骰。
> **14 级：凶狡打击** 上述三种效果加入选项。

偷袭骰数由 `progression_catalogs/srd_classes.json` 的 `classes.rogue.tracks.sneak_attack_dice` 给出（20 级 `[1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10]`），`sneak_attack` 通过 `damage_dice_track` + `damage_dice_format`（`{die}d6`）读它，`_sneak_attack_dice()` 返回形如 `"3d6"` 的字符串。

### 1.2 现状（本任务书逐条核实过，基线 `b0496921`）

**三层注册表已成型，诡诈打击属于第二层。**

| 层 | 位置 | 形状 | 诡诈打击的适配性 |
|---|---|---|---|
| 主动行动 | `features/kinds.py::KIND_HANDLERS`（29 个 kind） | `Activation` + `cost` + handler | ✗ 它没有动作槽、不占反应、不付资源池 |
| **命中声明** | `features/attack_declarations.py::ATTACK_DECLARATIONS`（6 条） | `id` / `surfaces` / `plan_condition` / `on_hit` / `validate_use`；意图键 `attack_declarations: [id…]` | ✓ 命中后才生效、改「这一击产生什么」 |
| 反应窗口 | `combat/check_windows.py::WINDOW_REGISTRY`（17 条） | offer → decision → resolve | ✗ 没有 offer 阶段，不是响应别人 |

**六种效果对现有载体（逐条核实，不靠记忆）：**

| 效果需要的东西 | 现成载体 | 结论 |
|---|---|---|
| 骰池预算 | `resolution.py:2038 _sneak_attack_dice()` 返回 `"3d6"`；`action_adapter.py:203 attack_damage_node()` 在其后拼 AST | 需新增「按颗数截短」的纯函数 |
| 目标豁免 | `_saving_throw_check(...)`；rider 的 `save{ability,dc}` | 直接用 |
| DC 公式 | `martial_actions.py:325-328` 现在把 `8 + PB + WIS` **写死** | 必须改成逐行 `save_dc_ability`，否则 combat 里会长出第二条 DC 公式 |
| 体型门禁 | `primitives.py:28 size_at_most()`（docstring 里已点名 Trip） | 直接用（放在提交校验） |
| 四个条件 | `srd_combat_core.json` 一共 14 个条件，`prone` / `blinded` / `poisoned` / `unconscious` 都在其中且有机械条目（攻击模式、`implies_incapacitated`、`speed_zero`、`crit_on_hit_within_5`）；`exhaustion` 不在目录里，本片不需要它 | 直接用 |
| 「受伤即结束」 | `reducer.py:1318` 读条件元数据上的 `ends_on_damage` | 直接用，无需新分支 |
| 「到其下回合结束」 | `engine.py:3168` 的 `target_turn_end` + `expires_after` | 直接用 |
| 「1 分钟 + 每回合重豁免」 | **已存在**：`condition.applied` 携带 `save_repeat{ability,dc}`，`resolution.py:5227 _save_repeat_events()` 在目标自己回合结束时逐条重豁免（Abjure Foes / Intimidating Presence 已走这条路，`ongoing_effects.py:469-477`） | **不需要新原语** |
| 「下回合只能做一件事」 | **已存在**：条件元数据 `turn_choice_limit`，`validation.py:1735 _validate_turn_choice()` 按**实际花掉的经济**而不是意图类型判定（Abjure Foes 在用，内容见 `srd_class_features.json` 的 `abjure_foes.parameters.turn_choice_limit`） | **不需要新原语**，需要载体条件（见 §3.4） |
| 撤步的「半速、不引发借机」 | `dnd2024.movement.granted` + `economy.crit_move` / `economy.brutal_move` 两条一次性额度（`reducer.py:855-893`、`resolution.py:4252-4289`） | 复用，但要先把两条额度归一（见 §3.5） |
| 制毒工具在身 | 物品 `poisoners_kit`（`content/items/poisoners_kit.json`、`equipment_catalogs/srd_tools.json`）；`character/attunement.py:214 owned_ref_counts()` 统计 equipment + inventory；先例 `validation.py:2791 _validate_ammunition()` | 直接用 |
| 面向玩家的投影 | `engine.py:658 _attack_state_options()` 已投影 reckless / brutal / studied | 扩展它 |

**内容侧缺口（三处，都必须补）：**

1. `srd_class_features.json` 的 `classes.rogue.features` 只有 12 条，**`cunning_strike` / `improved_cunning_strike` / `devious_strikes` 三条定义都不存在**；`progression_catalogs/srd_classes.json` 的 5 / 11 / 14 级**已经授予**这三个 id。于是 `has_feature()` 成立、`feature_value()` 全部走默认值——这正是「看起来实现了、其实没有任何参数」的那种假实现。
2. `features/validation.py::_RIDER_PARAMETER_KEYS` 是**逐 feature 的闭集**，未登记的参数键会让整个 bundle 装载失败；新参数必须同时登记键集与取值校验。
3. 条件 `dazed` 不存在，而 `turn_choice_limit` 必须挂在某个条件元数据上（见 §3.4）。

### 1.3 停工理由作废

`SLICE_STATUS.md` 给 F17 的 ⛔ 理由是「七种效果的前提无权威来源」。这条现已不成立：规则原文在 `.rulebooks`（§1.1），前提逐条可读；载体在 §1.2 里逐条落实。原停工报告不再作为依据。

## 2. 形状决策（先定，再写代码）

### 2.1 一个声明 + 一个载荷列表，不是六个声明

六个效果**不各注册一个 canonical id**。理由与 Brutal Strike 同构（`intent["brutal_strike_effects"]`）：效果集合是**内容**（14 级才加入后三种），而 `ATTACK_DECLARATIONS` 是**代码里的静态字典**——注册成六条就等于把「哪些效果已解锁」写进代码，`devious_strikes` 的内容改动还要连带改字典。

```json
"attack_declarations": ["cunning_strike"],
"cunning_strike_effects": ["trip"]
```

- `attack_declarations` 只报**声明 id**（闭集校验在 `selection_error`）；
- `cunning_strike_effects` 只报**效果 id**（闭集来自内容 `effect_options` + `additional_effect_options`）；
- **代价不报**：每颗骰的价钱、豁免属性、条件、时长全部来自内容表，客户端多报/少报都无效。

### 2.2 声明在掷骰之前提交，扣骰在命中之后

规则写的是「投掷伤害前扣骰」，而 intent 在攻击检定之前就提交了。**接受这个更早的声明点**，理由是另一条路更贵：

- 新开一个「命中已定、伤害未掷」的决策窗口 = 新增 decision kind + 把 weapon/ability/modifier/damage_formula 打包进 `pending_decisions` + 给 `/auto` 加自动弃权分支（否则 AI 席位的盗贼一攻击就卡住）+ 一张前端决策卡。系统里现有窗口（Shield / Uncanny Dodge / 至圣斩）**全部在伤害之后**，这是唯一一个要在伤害之前开的。
- 声明式只多一个信息差：「这一击到底算不算偷袭」。而它**不需要**在提交时就知道——结算时判，判不成立就不扣骰、不发效果（§3.6 的交互表）。

### 2.3 扣骰位置：`_sneak_attack_dice()` 之后、`roll_damage_node()` 之前

`resolution.py:2000 _hit_settlement_events()` 的现状顺序是：

```
2038  sneak_dice = self._sneak_attack_dice(...)
2041  if sneak_dice: extra_dice.append(sneak_dice)
2061  damage, rolls = roll_damage_node(damage_node, rng)
2109  conditions = self._hit_condition_declarations(...)   ← 太晚
```

`_hit_condition_declarations()` 必须**上移到 2041 之前**（它不做任何掷骰，只是读内容与 `used_this_turn`，移动无副作用），然后用它返回行的 `dice_cost` 之和截短 `sneak_dice`。

**必须显式处理的坑**：现有代码是 `if sneak_dice:` 才挂 `feature_use`（2075-2083）。截短到 **0 颗**时若把 `sneak_dice` 置空，`feature_use` 会跟着丢——等于凭空多给一次偷袭。因此：

- 用 `sneak_qualified = sneak_dice is not None` 决定是否记账；
- 剩余 `0` 颗时**不要把 `"0d6"` 塞进 AST**：`action_adapter.py:27 _NDM_RE` 要求骰数 ≥ 1，`double_dice_counts()` 在重击时会直接抛 `CombatIntentError`；
- 正确写法是「剩余 > 0 才追加一条 `Nd6`，否则不追加」，`feature_use` 仍按 `sneak_qualified` 记。

## 3. 精确契约

### 3.1 声明 id 与行为

`features/attack_declarations.py`：

```python
CUNNING_STRIKE = AttackDeclaration(
    "cunning_strike", "cunning_strike", ("attack",),
    plan_condition=_cunning_strike_condition,
)
```

- **只挂 `attack` 面**：`cast_spell` / `class_capability` 上的攻击不产生偷袭骰，没有预算可用。
- **不用 `on_hit`**：撤步虽然无豁免，但它照样花骰、照样受「偷袭成立」门控，走 `plan_condition` 才能与其他五种共用同一条预算与同一条跳过语义。
- **不改 `AttackDeclaration` 的字段**：`plan_condition` 返回的是任意 dict，效果形状由执行器解释（§3.2）。
- **不新增 `validate_use`**：它只能看到 `(features, character)`，拿不到 target / instance / intent。改用独立函数 `validate_cunning_strike(...)`（§3.3），只从 `validation.py::_validate_attack` 调一次。

`plan_condition` 的职责只有「把内容里的声明翻译成执行行」，不做任何合法性判断：

```python
def _cunning_strike_condition(context):
    if not context.features.has_feature(context.character, "cunning_strike"):
        return None
    rows = []
    for effect in content_order(context):     # 内容声明的顺序，与客户端顺序无关
        rows.append({
            "kind": "cunning_strike",
            "effect": effect["id"],
            "feature_id": owner_of(effect),   # cunning_strike / devious_strikes
            "dice_cost": effect["dice_cost"],
            ...                               # 按效果类型展开，见 §3.2
        })
    return {"kind": "cunning_strike", "rows": rows}   # 单一容器行
```

**用单一容器行而不是六行**：`dice_cost` 之和、跳过时的事件都只需要一处；六行会让「同一击的两个效果」看起来像两条独立声明，预算就没法一次算完。

### 3.2 执行行按字段分派（`combat/martial_actions.py::_hit_condition_events`）

执行器从「按 feature id 分支」改成「按行里有什么字段决定做什么」：

| 行里有什么 | 执行 |
|---|---|
| `save_ability` + `on_fail_condition` | `_saving_throw_check(...)` → 失败施加 `on_fail_condition`，成功施加 `on_success_condition`（可缺省）。Trip / Poison / Daze / Knock Out / Obscure 都走这条 |
| `condition` 但无 `save_ability` | 直接施加（本片不用，留着是因为形状已经存在） |
| `granted_move` | 发 `dnd2024.movement.granted`（§3.5）；Withdraw 走这条 |
| `focus_cost` | 照旧发 `class_resource.spent`（Stunning Strike 继续用这个字段） |

**DC 逐行算，不再写死 WIS**：

```python
dc = 8 + attacker_pb + ability_modifier(attacker["abilities"][row["save_dc_ability"]])
```

`save_dc_ability` 由内容声明。这是**删除一条特性专用公式**，不是新增一条分支。

两处例外必须写清，否则会改错：

- `stunning_strike.parameters` 要补 `"save_dc_ability": "wis"`，并同步加进 `_RIDER_PARAMETER_KEYS["stunning_strike"]` 的键集（那是闭集，只补内容不改键集会装载失败）。
- `open_hand` 行由 `_hit_condition_declarations` 在 combat 侧构造，**不经过内容**：调用方仍按 WIS 算出 DC 传给 `_open_hand_events(...)`，此行不参与 `save_dc_ability` 查表。

条件事件上必须原样带出去的元数据（都是现有消费者，无需新增读取）：

| 键 | 消费者 | 用途 |
|---|---|---|
| `duration` | `engine.py::_expire_conditions` | `target_turn_end` / `one_minute` |
| `turns_remaining` | 同上 | 1 分钟 = 10 个自己的回合（`minutes * 10`，与 `ongoing_effects.py:473` 同口径） |
| `save_repeat{ability,dc}` | `resolution.py::_repeat_save_events` | 每目标回合结束重豁免 |
| `ends_on_damage` | `reducer.py:1318` | 击昏「受到任何伤害即结束」 |
| `turn_choice_limit` | `validation.py::_validate_turn_choice` | 恍惚 |

### 3.3 校验（`combat/validation.py::_validate_attack`，紧跟 `validate_weapon_selection`）

按此顺序，逐条 fail closed，错误文案进 `CombatIntentError`：

1. `cunning_strike_effects` 出现但 `attack_declarations` 不含 `cunning_strike` → 拒绝（防陈旧/越权客户端白拿效果）。
2. 特性所有权：`has_feature(character, "cunning_strike")`。
3. 效果 id 闭集：`effect_options` ∪（拥有 `devious_strikes` 时的 `additional_effect_options`）。
4. 个数 ≤ `effects_per_use`，等级 ≥ `upgrade_level` 时用 `effects_per_use_upgrade`（读 `class_state()`，与 Brutal Strike 同一写法）；**不允许重复 id**。
5. 总 `dice_cost` ≤ 本等级偷袭骰数（解析 `sneak_attack` 的 `damage_dice`，`DICE_RE` 已在 `features/validation.py:934` 有跨包先例）。
6. 淬毒：`owned_ref_counts(instance.get_character_sheet(actor["id"]))["item:poisoners_kit"] ≥ 1`。用 `owned_ref_counts` 而不是 `inventory_ref_counts`——起始装备是 `equipment` 行，只看背包会把「带着制毒工具」判成没有。
7. 摔绊：`size_at_most(target["size"], "large")`，未知体型一律不满足（`size_at_most` 的既有语义）。
8. 撤步：`effective_speed(...) // 2 > 0`。

**注意**：`_validate_attack` 对玩家攻击与 `class_capability` 授予的徒手打击都会跑，所以 `_validate_capability_unarmed_strike` 那条路会自动继承校验，不需要第二处实现。

**借机攻击不在此列**：窗口的 `resolve` 分支构造的 `attack_intent` 只有 `option`，没有声明通道（见 GAP-49）。

### 3.4 恍惚的载体条件 `dazed`

`turn_choice_limit` 是**条件元数据**上的字段，没有条件就没有地方挂。而 2024 规则里 Daze 不是一个具名状态，不能借用 `frightened`（它自带攻击劣势）或 `incapacitated`（它禁掉全部动作）。

因此新增一个**纯载体条件** `dazed`：

- `srd_combat_core.json` 的 `conditions.dazed = {"kind": "condition"}`（无任何机械标志）；
- `locales/{en,zh-CN}/combat_catalogs/srd_combat_core.json` 补 `condition.dazed` 与 `condition.dazed.summary`；
- 只由诡诈打击施加，时长 `target_turn_end`，元数据带 `turn_choice_limit: ["move", "action", "bonus_action"]`。

同时把 `validation.py:1778` 的错误文案从「while frightened this way…」改成中性描述（该串在前端无匹配，`grep` 只命中服务端；Abjure Foes 的现有用例需同步改断言）。

### 3.5 撤步：两条一次性移动额度归一成一条

现状是**两个专用键**（`reducer.py:855-893`）：

- `economy.crit_move`：整数额度，免借机，无方向（Remarkable Athlete）；
- `economy.brutal_move`：`{amount, target_id}`，免借机，且只能朝目标走（Forceful Blow）。

`RIDER_MOVES` 的注释自己写着「第三个出现时再搬进 catalog」。撤步就是第三个，但它的形状和 `crit_move` **完全一样**（半速额度、无方向），再开一个 `withdraw_move` 就是三份同样的判定。

改成一条：

```python
economy["move_allowance"] = {"amount": int, "reason": str, "target_id": ""}
```

- `resolution.py:4252-4274` 的两处 `covered_by_*`（`crit` / `brutal`）合并为一次判定：额度足够、且在 `target_id` 存在时方向一致；
- `position.changed` 的标记键从 `crit_move` / `brutal_move` 收敛为 `move_allowance`，`reducer.py:883-893` 一次性清除；
- **兼容读**：只在这一片里保留对旧键的读取（存档中可能还挂着旧额度），写路径一律写新键；旧键的读取在后续片里删（shrink-only 的方向）。

不改名也可以，但那样 F17c 要写第三份同样的判定，并且 `crit_move` 这个名字会永久掩盖「它其实是通用额度」。

### 3.6 与既有机制的交互

| 情形 | 行为 |
|---|---|
| 未命中 | 不扣骰、不发效果（`plan` 只读内容，扣骰在命中结算里） |
| 命中但偷袭不成立（无优势/无盟友/有劣势/武器非灵巧） | `dnd2024.cunning_strike.skipped`（`reason: "no_sneak_attack"`），**不扣骰、不改伤害** |
| 总花费 > 可用骰（理论上校验已挡） | 同上，`reason: "budget"`，**绝不出现负骰** |
| Shield 把命中改判落空 | 骰与效果一起不落地（伤害事件整条被丢弃，沿用既有延迟提交） |
| Uncanny Dodge 减半 | 条件照常保留（它只改伤害数值） |
| 两个效果各自要求豁免 | 按**内容声明顺序**逐个结算，各自独立（顺序与客户端提交顺序无关，保证 RNG 轨迹可复现） |
| 重击 | 偷袭骰翻倍，**预算不变**（`dice_cost` 是颗数，不是结果）；本片不改这条语义，测试固定它 |

`dnd2024.cunning_strike.skipped` 是**审计事件**（状态不变），必须加进 `reducer.py:98-108` 的无状态白名单，否则整批 apply 会失败——这与 `dnd2024.rider.skipped` 当初的坑是同一个。

**跳过事件要随伤害事件走，不能直接返回**：`_hit_settlement_events` 在开 Shield / Uncanny Dodge / 至圣斩窗口时会提前 `return [{"type": "dnd2024.decision.pending", ...}]`（2159-2160），此时直接 append 的事件会被丢掉。正确做法与 `feature_use` / `hit_riders` 一致：先把跳过行挂在 `damage_event["cunning_strike_skipped"]` 上，由提交路径（`_submit_hit_damage` / `_resolve_committed_hit`）转成事件；命中被改判落空时整条伤害事件作废，跳过事件也随之消失（那一击根本没造成伤害，没有什么要解释的）。

## 4. 内容声明（写全，不要照抄形状再猜键名）

`srd_class_features.json`，`classes.rogue.features` 追加三条：

```json
{
  "id": "cunning_strike",
  "minimum_level": 5,
  "source_ref": "srd-5.2.1:p62:cunning-strike",
  "summary": "When you deal Sneak Attack damage, spend Sneak Attack dice on one effect before rolling damage.",
  "parameters": {
    "effect_options": ["poison", "trip", "withdraw"],
    "effects_per_use": 1,
    "upgrade_level": 11,
    "effects_per_use_upgrade": 2,
    "save_dc_ability": "dex",
    "poison_dice_cost": 1,
    "poison_save_ability": "con",
    "poison_condition": "poisoned",
    "poison_duration": "one_minute",
    "poison_repeat_save": true,
    "poison_requires_item": "poisoners_kit",
    "trip_dice_cost": 1,
    "trip_save_ability": "dex",
    "trip_condition": "prone",
    "trip_max_target_size": "large",
    "withdraw_dice_cost": 1,
    "withdraw_speed_divisor": 2,
    "withdraw_provokes_opportunity": false
  }
},
{
  "id": "improved_cunning_strike",
  "minimum_level": 11,
  "source_ref": "srd-5.2.1:p62:improved-cunning-strike",
  "summary": "You may use two Cunning Strike effects on the same Sneak Attack, paying for each.",
  "parameters": {}
},
{
  "id": "devious_strikes",
  "minimum_level": 14,
  "source_ref": "srd-5.2.1:p62:devious-strikes",
  "summary": "Daze, Knock Out and Obscure join your Cunning Strike options.",
  "parameters": {
    "additional_effect_options": ["daze", "knock_out", "obscure"],
    "daze_dice_cost": 2,
    "daze_save_ability": "con",
    "daze_condition": "dazed",
    "daze_duration": "target_turn_end",
    "daze_turn_choice_limit": ["move", "action", "bonus_action"],
    "knock_out_dice_cost": 6,
    "knock_out_save_ability": "con",
    "knock_out_condition": "unconscious",
    "knock_out_duration": "one_minute",
    "knock_out_repeat_save": true,
    "knock_out_ends_on_damage": true,
    "obscure_dice_cost": 3,
    "obscure_save_ability": "dex",
    "obscure_condition": "blinded",
    "obscure_duration": "target_turn_end"
  }
}
```

- 键名里 **`knock_out`** 是效果 id，`attack_declarations` 的载荷与前端都用它（不是 `knockout`）。
- `improved_cunning_strike` 的 `parameters` 为空是**故意**的：11 级只抬个数，个数写在自己的老大哥 `cunning_strike` 上（`upgrade_level` / `effects_per_use_upgrade`），与 Brutal Strike 一致，避免同一件事在两处可读。
- 时长 `one_minute` 需要先在 `combat/primitives.py` 新增常量并加进 `TURNS_SCOPED_DURATIONS`（现有三项：`turn_undead` / `abjure_foes` / `intimidating_presence`）。**不复用 `intimidating_presence`**：那个名字是特性的名字，不是时长的名字。
- `source_ref` 沿用同类特性的 `srd-5.2.1:pNN:slug` 形式，装载期只校验前缀（`test_dnd2024_class_features.py:208`），页码以纸书为准。

`features/validation.py` 新增 `validate_cunning_strike_parameters(definition)`，在 `catalog` 校验链（现 206 行 `validate_mark_parameters` 之后）挂一行，检查：

- 三张键集闭集（`cunning_strike` / `improved_cunning_strike` / `devious_strikes`），未知键与缺键都要报错；
- `effect_options` / `additional_effect_options` 是效果 id 闭集（`poison/trip/withdraw/daze/knock_out/obscure`），`additional_effect_options` 不含前三种；
- `*_dice_cost` 正整数；`daze` 2 / `knock_out` 6 / `obscure` 3 **与 SRD 一致**（写死成本会在规则更新时静默变贵）；
- `*_save_ability` ∈ `SAVE_ABILITIES`；`*_condition` ∈ 条件目录；`*_duration` ∈ 本片自己的闭集 `{"target_turn_end", "one_minute"}`（**不要**放宽 `_RIDER_CONDITION_DURATIONS`——那个集合服务的是 rider 的两种时长，顺手加值会连带放宽 Stunning Strike / Brutal Strike）；
- `trip_max_target_size` ∈ `SIZE_CODES`；`withdraw_speed_divisor` 正整数。

`locales/{en,zh-CN}/class_feature_catalogs/srd_class_features.json` 的 `fields.labels` 补三条特性名与六个效果名（前端展示用；`test_dnd2024_class_features.py::test_feature_labels_follow_the_bundle_locale` 断言的是**武僧 2 级**的标签字典，不受影响）。

## 5. 分片

### F17a 声明闭环 + 摔绊（端到端第一条链）

内容三条定义、`ATTACK_DECLARATIONS` 加一条、`validate_cunning_strike`、执行器通用化（DC 逐行）、扣骰与 `feature_use` 修正、`skipped` 事件与白名单、`_attack_state_options` 投影（`effects` / `count` / `costs` / `dice`）。

完成定义：5 级盗贼选 `trip` 打中一个 Medium 敌人 → 伤害里少了 1d6、目标倒地、每回合一次照常记账；打 Huge 目标 → 提交被拒（不是白扣）。

### F17b 淬毒 + 眩目（条件族收口）

`one_minute` 常量与 `TURNS_SCOPED_DURATIONS`；`save_repeat` 与 `target_turn_end` 两条时长路径；制毒工具存在性。

完成定义：中毒 1 分钟；目标每次自己回合结束重豁免，成功即移除；10 回合后自然过期；没有制毒工具时提交被拒。眩目在目标下回合结束时过期（不是立刻、也不是再多一轮）。

### F17c 撤步（一次性移动额度归一）

`economy.move_allowance` 收敛两条旧键、`resolution.py` 的免借机判定合并、撤步行发 `movement.granted`。

完成定义：命中后玩家获得「至多半速、本次免借机」的移动额度；走出 5 尺威胁范围时**不**开借机窗口；额度只覆盖一次移动，第二次移动照常开窗；旧存档里的 `crit_move` 仍被识别。

### F17d 恍惚 + 击昏 + 双效果上限

`dazed` 载体条件与本地化；`turn_choice_limit` 文案中性化；`ends_on_damage`；11 级双效果与 `effects_per_use_upgrade`。

完成定义：14 级盗贼一击选 `knock_out`（6d6）→ 目标昏迷、伤害只剩武器骰（7d6 池被打空也**不能**丢 `feature_use`）；`knock_out` + `withdraw` 用满 7d6 时 `feature_use` 仍记一次；恍惚目标在同一回合内第二次花动作被拒。

### F17e 投影与前端（原名 GAP-48）

面板能选效果、看到每项花费与剩余骰数、在提交前看到非法组合的原因。**按通用「攻击声明」做，不按诡诈打击做**：Reckless Attack / Brutal Strike / Stunning Strike / Sacred Weapon 今天在 `frontend-v2/src` 里同样**零支持**（`grep` 为空），这张面板是它们共同缺的那一块。

## 6. 改动白名单

| 文件 | 改什么 | 行数额度 |
|---|---|---|
| `features/attack_declarations.py` | 一条声明 + `_cunning_strike_condition` + `validate_cunning_strike` | 244 → 约 400，不受 ratchet 约束 |
| `combat/martial_actions.py` | 执行器按字段分派、DC 逐行 | 506 → 约 600，未被基线收录 |
| `combat/resolution.py` | `_hit_condition_declarations` 上移、扣骰、`skipped`、额度归一 | **5949，上限 +100**：只允许改顺序与替换，不堆代码 |
| `combat/validation.py` | 一个调用点 + 文案 | **3285，上限 +100** |
| `combat/reducer.py` | `skipped` 白名单一行、额度键收敛（`one_minute` 的到期由 `TURNS_SCOPED_DURATIONS` 驱动，不需新分支） | 1350，未被基线收录 |
| `combat/primitives.py` | `ONE_MINUTE_DURATION` + 加入 `TURNS_SCOPED_DURATIONS` | 小 |
| `combat/action_adapter.py` | `reduce_dice_count(formula, by)`（与 `double_dice_counts` 同族） | 小 |
| `features/validation.py` | 三张键集 + 取值校验 + 挂一行 | 小 |
| `content/class_feature_catalogs/srd_class_features.json` | 三条定义 | 数据 |
| `content/combat_catalogs/srd_combat_core.json` | `conditions.dazed` | 数据 |
| `locales/{en,zh-CN}/…` | 特性名、效果名、`dazed` | 数据 |
| `combat/engine.py`（实际改到） | `_attack_state_options` 的 `cunning_strike` 投影（`available` / `effects` / `count` / `costs` / `dice` / `names`） | 小 |
| `frontend-v2/src/api/types.ts` | `RulesetCunningStrikeMenu` + attack 行上的可选字段 | 小 |
| `frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue` | 效果多选、预算提示、`available` 门控、提交载荷 | 约 +40 |
| `frontend-v2/src/features/rulesets/dnd2024/combat/combatCopy.ts` | 面板文案（效果名退为服务端 `names` 的兜底） | 小 |
| `tests/rulesets/test_dnd2024_cunning_strike.py`（新） | 见 §7 | 新文件 |
| `tests/Dnd2024CombatPanel.test.ts` | 三条：载荷、超个数、已用后禁用 | +3 |

**白名单外的必要改动**（都是"形状变了、断言没跟"，不是新行为）：`tests/rulesets/test_dnd2024_d20_skills.py` 与 `test_dnd2024_attack_state.py` 的三处额度断言改成 `move_allowance`；`test_dnd2024_gm_condition.py` 的条件目录条数 14 → 15（`dazed`）。

**不许动**：`combat_coupling_baseline.py`（shrink-only，新增条目必须写明由哪一步删除）。本片的设计恰好**不新增**任何条目：声明在 `features/` 内（不在扫描范围），执行器按行数据分派而不是按 feature id，`cunning_strike_effects` 的读取也在 `features/` 内。**已知盲区**：`scripts/architecture_fitness.py` 只匹配 `X.kind == "literal"`，不匹配 `X.get("kind") == "literal"`，所以执行器新增的 `row.get("kind") == "cunning_strike"` 在语义上是一条 `kind_branch`，只是扫描器看不见（§11.8 第 8 条）。

## 7. 逐项测试

1. **预算**：3d6 池选 1d6 效果 → 伤害比不选少 1d6（断言 `sum(rolls)` 与骰数，不断言视图）；选 2 个（11 级前后）分别被拒/通过。
2. **打空**：14 级 7d6，`knock_out`(6) + `withdraw`(1) → 伤害只剩武器骰，且 `dnd2024.feature.used` **有** `sneak_attack` 一条（这条是「减到 0 仍记一次」的唯一证据）。
3. **偷袭不成立**：无优势无盟友时声明 `trip` → `cunning_strike.skipped`，伤害里没有任何 `d6` 减少，目标不倒地。
4. **不变式**：Shield 改判落空 → 没有 `feature.used`、没有条件、没有半条效果落地（`skipped` 也不发：那一击根本没造成伤害）。
5. **前提 fail closed**：无制毒工具选 `poison` → 提交被拒；Huge 目标选 `trip` → 被拒；重复 id、未知 id、超个数、总花费超池，各一条。
6. **时长**：`poisoned` 在目标第 1 回合结束时豁免失败仍存在、第 2 回合成功即移除、10 回合自然过期（三条都要）。
7. **行动限制**：`dazed` 目标先移动再攻击 → 第二次被拒；只攻击一次 → 通过。
8. **受伤即醒**：`knock_out` 目标受到 1 点伤害 → 条件移除；被完全抗性（0 伤害）时**不**移除（与 `ends_on_damage` 的既有语义一致）。
9. **撤步**：额度内移动不开借机窗口；超额移动照常开窗；额度用掉后第二次移动开窗。
10. **投影**：`_attack_state_options()` 返回 `effects` / `count` / `costs` / `dice`，且 5 级盗贼看不到 14 级的三种。
11. **变异验证**（必做）：把扣骰删掉，第 1 条必须变红；把 `sneak_qualified` 改回 `if sneak_dice:`，第 2 条必须变红；把 DC 改回 WIS，`trip`（dex）在感知 8/敏捷 20 的角色上必须变红。

## 8. 停止条件

- 扣骰必须发生在 `roll_damage_node()` **之后**才能表达（说明声明点选错了，回来讨论）；
- 修 `feature_use` 需要改 `feature_usage` 的账本形状；
- 重豁免或行动限制必须新开一套原语（说明 §1.2 的核实有误，先核对 `Abjure Foes` 那条路）；
- 撤步的额度归一导致既有 `crit_move` / `brutal_move` 用例无法在「只改读取、不删旧键」的前提下通过；
- 执行器通用化后 `open_hand` / `quivering_palm` 的行为发生变化；
- 需要往 `combat_coupling_baseline.py` 加条目。

## 9. 验收命令

```bash
python -m pytest tests/rulesets/test_dnd2024_cunning_strike.py -q
python -m pytest tests/rulesets -q -n 6 --dist loadfile     # 必须与基线同名同数
python -m pytest tests/architecture -q
ruff check src/ tests/
python -m mypy
cd frontend-v2 && npm run test:unit && npx vue-tsc -b && npm run lint && npm run locales:check
```

Windows 上先 `export TEMP=TMP=TMPDIR=E:/pytest-tmp`，`--basetemp` 用从未创建过的新目录名。

## 10. 与规则的三处偏离（必须登记，不许沉默）

1. **声明早于「投掷伤害前」**：intent 在攻击检定之前提交，规则只要求伤害前。补偿是「偷袭不成立即不扣骰、不发效果」（§3.6），盲声明落空不损失任何东西。
2. **借机攻击上不能用**：OA 的 answer intent 只有 `option`，没有声明通道；这不是规则限制，是通道缺失（GAP-49）。本片不修，测试与文档都要写明。
3. **恍惚用了载体条件 `dazed`**：2024 规则里 Daze 不是具名状态，引擎需要载体来挂 `turn_choice_limit`。副作用是目标的条件列表里会多出一个 `dazed`。

## 11. 执行记录

### 11.1 落地范围

F17a–F17e 一次落地，分成三个实现提交加一个文档提交（后端没有拆成 a/b/c/d 四个提交：六种效果共用同一条预算链，`_hit_condition_events` 的执行器、`plan_row` 的字段与内容表在同一次改动里成型，硬拆只会在同一批函数上切成碎片）。

| 片 | 落地内容 |
|---|---|
| F17a | 内容三条定义、`ATTACK_DECLARATIONS` 加 `cunning_strike`、`validate_cunning_strike`、`_hit_condition_declarations` 上移 + 扣骰、`sneak_qualified` 记账、`cunning_strike.skipped` 与白名单、`_attack_state_options` 投影 |
| F17b | `ONE_MINUTE_DURATION` + `TURNS_SCOPED_DURATIONS`、`save_repeat` 快照、`target_turn_end`、`requires_item` 门禁 |
| F17c | `economy.move_allowance` 归一（`crit_move` / `brutal_move` 只读兼容）、`provokes_opportunity` 由授予方声明 |
| F17d | `conditions.dazed` 载体、`turn_choice_limit` 元数据、`ends_on_damage`、11 级双效果 |
| F17e | `RulesetCunningStrikeMenu` 投影类型、战斗面板效果多选与预算提示、`attack_declarations` 载荷 |

### 11.2 与任务书的四处偏差（都是实现时才看清的）

1. **`HitConditionContext` 加了 `intent`**，而不是 §3.1 伪代码里的 `content_order(context)`。`plan_condition` 只拿到 context，载荷必须从 intent 读；把整个 intent 放进去比再开一个 `payload` 字段更省，而且以后别的声明要载荷时同一个入口就够。
2. **`CunningStrikeEffect` 多一个 `save_dc_ability`**。§3.2 只写了 `save_ability`（目标掷什么），但 DC 是 8 + PB + **敏捷**，两者对 Trip/Obscure 恰好相同、对 Poison/Knock Out/Daze 不同。不分开就会把 DC 算成"目标豁免的那项属性"。
3. **计划行的键叫 `on_fail_condition`**，与 Stunning Strike 的行同形，这样执行器只需要一套"失败施加条件"的读取。
4. **移动额度归一按 `provokes_opportunity: false` 判定，不按 `reason` 白名单**。授予方声明"这份额度免不免借机"，reducer 不再按特性名猜；三处授予（暴击后移动、Forceful Blow 跟步、撤步）各加一个布尔，判定只剩一处。

### 11.3 未决

- **Shield 改判落空的端到端用例没写**：它要求同侧目标 + 构造待定决策（`test_dnd2024_sneak_attack.py` 的 `_held_attack` 手法）。当前实现走的是既有延迟提交（骰与效果都挂在伤害事件上，事件整条作废），语义由 Sneak Attack 的既有用例覆盖；F17 自己的用例覆盖了"偷袭不成立不扣骰"这一条同族语义。
- **借机攻击上仍不能声明**（GAP-49）：窗口的 answer intent 只有 `option`。
- **F17e 的范围收窄成"Cunning Strike 一条"**，不是 §5 原计划的通用声明面板。载荷形状在五种声明之间差异太大（布尔 / 效果列表 / 武器选择），一张卡要先定 schema；GAP-48 因此仍是「部分」。投影带 `names` 让面板不必自带一份效果名，但只有这一个特性受益。
- **`move_allowance` 是单槽**：同回合内"暴击额度 + 撤步"或"Forceful + 撤步"会互相覆盖。纯职游荡者拿不到前两者，多职业支持前不可达。

### 11.4 只看门禁才现形的缺陷（单测全绿时看不见的）

1. **DC 退化成 0，豁免必然成功**。`plan_row` 起初只带 `save_ability`，执行器按 `save_dc_ability` 查表时落回 fallback=0。单测若只断言"行里有豁免属性"会全绿；实际攻击里目标掷 3 也"成功"，Trip 永远不倒地。这是任务书 §3.2 那张"执行行元数据表"漏写一列的直接后果。
2. **`conditions.dazed` 必须带 `attack_modes: []`**。`Dnd2024CombatCatalog` 要求每个条件条目声明攻击模式（哪怕是空数组），只写 `{"kind": "condition"}` 会让整个 bundle 装载失败——这条只有真加载才发现，`_RIDER_PARAMETER_KEYS` 那类键集校验看不到。
3. **11 级偷袭骰是 6d6 不是 4d6**。`sneak_attack_dice` 表是 20 项数组，`index == level - 1`，11 级取到 6。写测试注释时按"每两级一颗"心算成 4 就错了，靠真实骰数纠正。
4. **`target_turn_end` 的到期时刻取决于施加时机**。它读 `previous_actor_id` 与 `expires_after`：在攻击者回合挂上的 Daze 在目标自己回合结束时过期（正确），在目标回合内挂上才需要多等一轮。这条是复用既有实现，没有新写计时器——但**复用必须显式写 `expires_after: 2`**，第一版漏了（见 §11.8 第 4 条）。
5. **旧额度只清新键等于永不失效**。读取侧认识 `crit_move` / `brutal_move`，写路径只写 `move_allowance`，消费侧只 `pop("move_allowance")`——旧存档里的一次性额度会一直免借机到回合结束。三个标记任一出现都要清全部三个（见 §11.8 第 2 条）。
6. **`_activation_payload` 不认敌人**。为了给 companion 也判制毒工具，校验改成对两种 actor 都取 `_activation_payload`；但 `_validate_attack` 是全 actor 共用入口，**敌人攻击也会走到这里**，而 `_activation_payload` 对 `enemy:` 直接调 `_companion()` 并抛「companion actor does not exist」。184 条反应窗口用例同时变红（它们全部由敌人发起攻击）。只看自己的 28 条用例完全看不出来——这是本轮唯一一次靠全量回归才发现的破坏。
7. **`bundle.get()` 是 deepcopy，放进按特性遍历的循环里就是灾难**。`validate_declarations` 每个特性调一次 `(bundle.get("combat_catalog", ...) or {}).get("conditions", {})`，等于每个特性复制一整份战斗目录；每次 bundle 装载多出几百次大字典拷贝，把**每条用例**从 2 秒推到 5 秒。表现为 CI 的 shard 1 在 45 分钟处超时（同批 main 只需 12.5 分钟），而本地全量只是"变慢"、没有任何红灯。同一文件 46 条用例：基座 147.96s / 修复前 246.26s / 修复后 151.87s。
8. **新用例自身的构造开销也会被 CI 放大**。28 条用例每条重建 `Dnd2024Runtime` 并把游荡者从 1 级走到 14 级，12 秒/条；`-n 4 --dist loadfile` 让整文件落在同一个 worker 上串行，于是整文件 346 秒。改成 module 级缓存 runtime 与各级 sheet 后 80 秒（2.9 秒/条）。

### 11.5 变异验证

| 变异 | 应变的用例 | 结果 |
|---|---|---|
| 删掉 `reduce_dice_count` 那一行 | Trip 扣骰、11 级双效果、Knock Out 打空池 | 3 条同时变红 |
| `sneak_qualified` 改回 `if sneak_dice:` | Knock Out 打空池仍记一次偷袭 | 变红 |
| `_declared_save_dc` 改回一律用 WIS | DC = 8 + PB + 敏捷 | 变红 |

### 11.6 门禁

| 项 | 结果 |
|---|---|
| `tests/rulesets/test_dnd2024_cunning_strike.py` | 28 passed（新文件） |
| `tests/rulesets/test_dnd2024_gm_condition.py` | 20 passed（条件目录 14 → 15） |
| `tests/rulesets/test_dnd2024_d20_skills.py` / `test_dnd2024_attack_state.py` | 额度归一后的断言已更新，通过 |
| `tests/architecture` | 162 passed（耦合基线**没有**新增条目） |
| `ruff check src/ tests/` | 通过 |
| `mypy` | 通过（118 files） |
| `vue-tsc -b` / `eslint --max-warnings 0` / `locales:check` | 通过 |
| `tests/Dnd2024CombatPanel.test.ts` | 87 passed（84 → +3） |
| `tests/rulesets` 全量 | 见 §11.7 |

### 11.7 全量回归

| 轮次 | 结果 | 说明 |
|---|---|---|
| 第一轮（实现完成） | 3023 passed / **5 failed** | 5 条红：额度归一的旧键断言（`crit_move` / `brutal_move`，2 条）+ `dazed` 进条件目录（3 条）。都是"形状变了、断言没跟"，已按 §11.8 第 1 条更新 |
| 第二轮（审核修正后） | 2852 passed / **184 failed** | 本轮唯一一次**真回归**：给 companion 取背包时误用了只认 player/companion 的 `_activation_payload`，而 `_validate_attack` 对敌人也跑，敌人攻击全部抛「companion actor does not exist」。见 §11.4 第 6 条 |
| 第三轮 | **3036 passed / 0 failed** | 修正后全绿。3036 = 第一轮 3028 (+5 failed) + 新增 8 条 Cunning Strike 用例 |

`tests/architecture` 三轮都是 162 passed：耦合基线**没有**新增条目（`cunning_strike` 的读取全在 `features/` 包内或 `row.get(...)`，扫描器不记）。

### 11.8 审核修正（2026-11-26，第二轮）

独立审查提出 3 条必须修、7 条应该修。逐条处理：

| # | 问题 | 处理 |
|---|---|---|
| 1 | 额度归一把 `test_d20_skills` / `test_attack_state` 的旧键断言改红 | 更新三处断言到 `move_allowance`（含 `reason`），并把 `gm_condition` 的目录条数 14 → 15 |
| 2 | 旧额度永不被消费（只清新键） | `position.changed` 的三个标记任一出现都清 `move_allowance` / `crit_move` / `brutal_move`；补旧存档用例 |
| 3 | `cunning_strike: true` 这个 legacy flag 能绕过"必须有声明" | `validate_cunning_strike` 只认 `attack_declarations` 列表里出现 `cunning_strike` |
| 4 | `target_turn_end` 在目标回合内挂上时不写 `expires_after` | `_cunning_condition_event` 接收 `currently_acting`，等于目标时写 `expires_after: 2` |
| 5 | 投影没有 `available`，面板会让玩家在偷袭已用后仍选效果 | 投影加 `available`；面板禁用整块并显示原因 |
| 6 | companion 的 Poison 必被拒（只给 player 传 `carried_refs`） | 改用 `_activation_payload`（player 与 companion 各有权威） |
| 7 | Daze 的拒绝文案仍是 "while frightened this way" | 改成中性的「your turn is limited to one of …」；既有用例只匹配 `already chose` 子串，不受影响 |
| 8 | 执行器新增了 `row.get("kind") == "cunning_strike"`，扫描器看不见 | 登记为已知盲区：`scripts/architecture_fitness.py` 只匹配 `X.kind == literal`，不匹配 `X.get("kind")`。等 GAP-43 迁移时和其余 kind 一起收 |
| 9 | F17e 没做成通用面板，六个 locale 键没有前端消费者 | 投影改带 `names`（从 bundle 的 `label` 读），前端优先用服务端名字、`combatCopy` 退为兜底；GAP-48 状态改为「部分」 |
| 10 | 测试缺口：重击×预算、超池、旧键、受伤移除、Daze 到期、撤步超额、同回合第二击 | 前六条各补一个用例；"同回合第二击"对纯职游侠不可达（一次 Attack 动作只有一击），改由 `available=false` 的投影用例覆盖同一语义 |
