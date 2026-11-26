# GAP-43 任务书：主动行动的声明与成本（Activation + kind 注册表）

| 项 | 值 |
|---|---|
| 缺口编号 | GAP-43 |
| 验收载体 | `FeatureCapability` 的 38 条（含 8 条空成本）、`_class_capability_events` 的 24 个 kind 分支、`attack_hit` 组的 6 个意图布尔 |
| 规则基准 | SRD 5.2.1 的动作成本原文 |
| 代码基线 | **等 #836 / #838 / #839 / #841 合并后**，基于当时的 main |
| 前置任务 | #515（shrink-only 护栏）、SP3、F14、F20、F21、F28、F30 |
| 状态 | **A–H 已执行**（2026-11-25，执行记录见 §12）；2026-11-24 按维护者在 #843 的口径修订 |
| 修订 | 初稿把 trigger/window 并入同一类型、给 effects 设闭集；两处均被否，已改 |

## 1. 范围与完成定义

只统一**玩家主动发起**的那一类：capability 与 operation 合并成一条 `Activation`，执行侧改成 **kind 注册表**。

trigger / window **保持现状**：它们有 offer → decision → resolve 的生命周期和反应经济，`WINDOW_REGISTRY` 的现有纪律是对的，继续做独立注册表；与 `Activation` 共用 `Cost`、`targets`、`requires` 的**写法**，但不共用类型。

完成定义：

1. `Cost` 拆成 `slot` + `resources` + `timing`，`slot` 只管动作槽，`turn_start` 作为独立 timing 保留。
2. 8 条空成本条目的现状先用测试固定，再改模型；其中 4 条 `*_restore` 的真实成本（`restore_slot_level` / `restore_resource` / `restore_resource_amount`）搬进 `cost.resources`。
3. `_class_capability_events` 的 24 个 kind 分支改成注册表：每种 kind 一个 handler（validate / resolve / project），自己注册。
4. `attack_hit` 组的 6 个意图布尔迁成声明（迁移目标见 §6）。
5. 中央路径只做四件事：查声明 → 校验前提 → `_pay_cost()` → 派发。
6. `economy[cost]` 的 7 处散落索引**是可用性检查，不是支付**，收敛到 `_slot_available(slot, economy)`；支付是另一个函数 `_pay_cost()`，两者成对但分开。

## 2. 现状（维护者核对过的版本）

- `FeatureCapability` 38 条，其中 **8 条 `cost: {}`**。
- `WINDOW_REGISTRY` 17 条 / 5 个 surface，生命周期是 offer → decision → resolve。
- `attack_hit` 组在 main 上是 4 个意图布尔：`stunning_strike`、`brutal_strike`、`activate_sacred_weapon`、`light_extra_attack`；#841 另加 2 个（`hurl_through_hell`、`quivering_palm`），迁移时按合入后的实际数量算。
- `_class_capability_events` 约 290 行、**24 个 `action.kind` 分支**，其中 11 个是单特性 kind（`quivering_palm_end`、`holy_nimbus`、`dragon_wings` 等），另有 `feature_id == "step_of_the_wind"`、`"feature_kind": "intimidating_presence"` 这类具体 id。
- `economy[cost]` 实际 7 处：`validation.py:1285 / 2726 / 3131 / 3212`、`engine.py:755`、`view.py:794 / 1004`（行号以合并后的 main 为准）。七处都是 `< 1` 的可用性判断，没有一处是支付——所以它们的目标是 `_slot_available()`，不是 `_pay_cost()`。

## 3. 精确契约

### 3.1 三类价格，不是一类

| 价格 | 字段 | 语义 |
|---|---|---|
| 动作槽 | `cost.slot` | `action` / `bonus_action` / `reaction` / `""`（空 = operation，不占经济） |
| 专属资源 | `cost.resources` | 法术位、Focus、使用次数、资源池 |
| 时机窗口 | `cost.timing` | `""` / `turn_start`（Superior Defense：只能在回合开始窗口支付，不占任何槽） |

`turn_start` **不能并进 `slot`**：它不是"更便宜的动作"，是"什么时候能付"。并进去会让 `slot` 同时回答两个问题。

### 3.2 Activation

```python
@dataclass(frozen=True, slots=True)
class Activation:
    id: str
    feature_id: str
    kind: str                         # 注册表键，见 3.3
    cost: Cost
    targets: str = "none"
    requires: tuple[str, ...] = ()
    forbids: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
```

`Capability`（`slot` 非空）与 `Operation`（`slot` 为空）是它的两个投影，不再是两个类。

### 3.3 kind 注册表

**不设 effect 闭集。** 29 种 kind 里有相当一部分拆不成通用积木，闭集的结局是一条中央分发的长链。

```python
@dataclass(frozen=True, slots=True)
class KindHandler:
    kind: str
    validate: Callable          # (activation, intent, context) -> None | raises
    resolve: Callable           # (activation, intent, context) -> list[event]
    project: Callable           # (activation, character) -> dict
```

- 中央路径只有四步：查声明 → 校验前提 → `_pay_cost()` → `handlers[activation.kind].resolve()`。
- 新增一个 kind = 写一个 handler + 注册一行，**不改中央文件**。
- 现有 24 个分支逐个搬进 handler；单特性 kind（`quivering_palm_end`、`holy_nimbus` 等）是正常的，它们是这个特性的效果物理，不是"架构债"。

### 3.4 Cost

```python
@dataclass(frozen=True, slots=True)
class Cost:
    slot: str = ""
    resources: tuple[ResourceCost, ...] = ()
    timing: str = ""            # "" | "turn_start"
    uses: int = 0
```

兼容期 `CapabilityCost.action` 保留为读 `slot` 的只读属性；`turn_start: bool` 保留为读 `timing` 的派生属性。写路径一律走新字段。

### 3.5 `*_restore` 的成本必须搬进 resources

`holy_nimbus_restore` / `dragon_wings_restore` / `intimidating_presence_restore` / `hurl_through_hell_restore` 现在 `cost: {}`，实际支付写在特性参数里（`restore_slot_level` / `restore_resource` / `restore_resource_amount`），由 `_validate_class_capability` 读取。

第一步要把它们搬进 `cost.resources`，否则"空成本"只是把成本藏到了另一处——而 GAP-43 的目标之一就是让成本只有一个可读的位置。

`font_slot_exchange` / `font_slot_to_points` 同理，但它们在 F28 的片里，按合并后的实际形状处理。

### 3.6 与 trigger / window 的边界

- 共用：`Cost`、`ResourceCost`、`targets` 闭集、`requires` / `forbids` 的写法与校验函数。
- 不共用：类型、生命周期、注册表。`WINDOW_REGISTRY` 继续独立，它有 offer 阶段，`Activation` 没有。
- 迁移时若发现某条 window 实际"没有 offer 阶段"，那是把它归错类的证据，值得单独讨论，不在这里顺手合并。

## 4. 顺序（维护者定）

```
护栏 → 第一步（Cost）→ kind 注册表 → 迁移散落意图与攻击附带效果
```

**trigger / window 不在本片范围。**

护栏沿用 #515 的 shrink-only baseline：新 PR 不再新增意图布尔、决策 kind、单特性 kind 分支；确需新增的登记并说明理由。

**护栏由维护者添加，不是本片产出。** 它是第一步的硬前置：护栏未落地时，本片不开工——否则统一工作会在没有约束的情况下先把新入口放宽。

## 5. 第一步的四个条件

1. 保留 `turn_start`（作为独立 `timing`，不并进 `slot`）。
2. **先给 8 条空成本逐条写测试固定现状**，尤其"只能在自己回合用"这条隐含语义，再动模型。
3. `action` 保留为只读属性。
4. **等 #836 / #838 / #839 / #841 合并后**，基于当时的 main 做，避免每个 PR 都冲突。

另加：「旧路径事件逐字节一致」的测试必须带上——这是这次机械改动唯一的正确性依据。

## 6. 迁移清单

### 6.1 在途 PR 新增的散落路径（登记，不要求返工）

- #836 / #838 / #839：`form.assume`、`form.revert`、`wild_resurgence`、`nature_magician`、`land_aid`、`nature_sanctuary`——6 个意图，各自手写校验与扣费。
- #841：`hurl_through_hell`、`quivering_palm` 两个意图布尔，外加 restore / end 类 capability。

### 6.2 attack_hit 组（迁移到声明）

`hurl_through_hell`、`quivering_palm`、`stunning_strike`、`brutal_strike`、`activate_sacred_weapon`、`light_extra_attack`。

### 6.3 决策 kind 与单特性 kind

`_class_capability_events` 的 24 个分支逐个搬进 handler；`melee_hit_spell`、`open_hand`、`opportunity_attack` 等另案登记。

## 7. 改动白名单

`features/models.py`、`features/resolver.py`、`features/combat.py`、`features/resources.py`；`combat/validation.py`、`combat/resolution.py`、`combat/view.py`、`combat/engine.py`、`combat/reducer.py`；catalogs（canonical / en / zh-CN）；新建 `tests/rulesets/test_dnd2024_gap43_*.py`；本任务书仅追加执行报告。

新模块（`features/kinds.py` 之类）允许，位置由本任务书指定。

## 8. 逐项测试

1. `Cost` 三字段互不干扰：`slot=""` + `timing="turn_start"` + 非空 `resources` 能共存。
2. 兼容期 `cost.action == cost.slot`；`cost.turn_start` 由 `timing` 派生，两者恒一致。
3. 8 条空成本逐条固定现状：各占什么隐含成本、"只能在自己回合用"的判定来源。
4. `*_restore` 搬进 `resources` 后，拒绝条件与旧路径逐字节一致（零槽、余额不足、重复支付）。
5. `_pay_cost()` 对每种 slot / timing 发出的事件与旧路径逐字节一致；7 处 `economy[cost]` 换成 `_slot_available()` 后，"还能不能用"的答案逐处相同（含 `view.py:690` 的 `reason` 分支）。
6. kind 注册表：24 个 kind 逐个走注册表后行为不变；未注册的 kind 在装载期报错，不在结算期静默。
7. 新增一个 kind 只需一个 handler + 一行注册（用测试里的小 handler 证明中央文件未改）。
8. 6 个 `attack_hit` 布尔迁移后：命中 / 未命中 / 资源不足 / 次数耗尽四条路径行为不变。
9. 既有回归：`tests/rulesets`、`tests/architecture` 全绿。

## 9. 非目标与停止条件

非目标：trigger / window 的合并；non-lethal；多人同步与超时；统一不同 kind 的效果物理。

**auto / ask / off 策略**按维护者意见拆出：它是 owner 级、服务端持有的偏好，还牵涉"反应由哪个玩家决定、AI 托管席位谁来定"。**需单开 issue**，在护栏之后、第一步之前开，避免统一做的过程中反复讨论策略归属。

停止条件：

- 8 条空成本的现状测试未写完就改模型；
- `*_restore` 的成本无法搬进 `resources`（说明成本模型缺一种形状，要先讨论）；
- `turn_start` 被迫并进 `slot`；
- kind 注册表要求中央路径认识具体 kind；
- 第一步未能等满 §5 条件 4 的四个 PR。

## 10. 验收命令

```bash
python -m pytest tests/rulesets/test_dnd2024_gap43_*.py -q
python -m pytest tests/rulesets -q -n 6 --dist loadfile
python -m pytest tests/architecture -q
python -m ruff check src/ tests/
python -m mypy
git diff --check
```

## 11. 合入后追加的规范条款

> **主动行动必须声明，执行走 kind 注册表。**
> 玩家主动发起的一件事（capability 或 operation）写成 `Activation`：`kind` 指向注册表，`cost` 用 `slot` / `resources` / `timing` 三个正交字段。
> 新增一种效果 = 一个 handler + 一行注册，不改中央分发路径。
> 成本只有一份实现 `_pay_cost()`；禁止 `economy[cost]` 这类直接索引。
> 触发式行动（trigger / window）仍走各自注册表，与 `Activation` 共用成本与前提的写法，不共用类型。

## 12. 执行记录（2026-11-25）

执行者：维护者侧与 Guan612（按维护者的交接说明）。本节只记事实；§1–§11 保留原计划，不改写。

### 12.1 落地情况

| 步骤 | PR | 结果 |
|---|---|---|
| 第一步前半：Cost 拆成 `slot` / `resources` / `timing` | #848 | `action` / `turn_start` 留为只读别名；7 处 `economy[cost]` 收敛为 `slot_available()`；扣费收敛为 `_pay_cost()`；9 条空成本用测试固定 |
| 第一步后半：`*_restore` 的成本 | #853 | 4 条 restore 的成本搬进 `cost.resources`，新增 `{"spell_slot": N}`；空成本 9 → 5；事件逐字节一致 |
| 第二步：kind 注册表 | #854、#855、#856 | `features/kinds.py::KIND_HANDLERS`，29 个 kind 全部走注册表，旧的 `action.kind` 链删除；未注册的 kind 在装载期报错 |
| 第三步：`attack_hit` 声明化 | #857 | `features/attack_declarations.py`：按 canonical id 声明，旧 flag 只在声明边界读取；保留「声明随被扣住的伤害事件走、不上 wire」的契约 |
| E：dismiss 的前提 | #863（原样并入 #852） | `requires: ["active_long_effect:<id>"]`；投影与提交读同一份按世界时钟过滤后的快照 |
| 第四步：按 kind 拆文件 | #858、#864、#865、#866 | `combat/divine_actions.py`、`ongoing_effects.py`、`martial_actions.py`、`stealth_actions.py`、`support_actions.py` |
| F / G：`plugin_host` | #859、#860 | `manifest_validation.py`、`package_io.py`；`host.py` 2679 → 2299 行 |
| H：`WebAPI` 两簇 | #861、#862 | `webui/content_sources.py`、`webui/rule_loading.py`、`services/adventure_rewards.py`；`api.py` 4290 → 4079 行 |
| S0-F28 的接入 | #847 | `innate_sorcery` 作为注册的 kind 接入 |

### 12.2 执行后的事实（main `7750d501`，#847 合入前）

- `combat/resolution.py` 7624 → 5664 行，其中 `action.kind ==` 比较 **0 处**；`combat/validation.py` 3616 → 3138 行。
- 守卫基线 296 → 243（搬家只改路径键，净值只减）。
- 旧的 `attack_hit` 布尔在 `validation.py` 里的直接读取 **0 处**。
- 新增一个 kind 的成本 = 一个 handler + `KIND_HANDLERS` 里的一行。

### 12.3 与原计划的差异

- 类名保留 `CapabilityCost`，没有改名 `Cost`。
- `Cost.uses` 没有搬：使用次数仍在 `FeatureCapability.uses_per_long_rest`（见 #848 的 Design Note）。
- 目录 JSON 仍以旧写法为主（`action: 1` / `turn_start: true`），加载器同时接受 `slot` / `timing`；内容迁移是独立的一步。
- §3.2 的 `Activation` 没有作为新类型落地：`FeatureCapability` 继续承担声明，`Capability` / `Operation` 的区分由 `slot` 是否为空表达。

### 12.4 未完成 / 后续

- **前置条件有两套写法**：#847 的 `requires_feature_id` / `exhausted_resource_id` 与 #863 的 `requires` 是同一类东西。建议并成 `requires: ["feature:<id>", "exhausted_resource:<id>"]`。
- **I**：守卫基线的纪律（新增条目必须写明由哪一步删除）已按此执行；CI 的行数趋势报告没有做。
- `features/resolver.py` 的其余候选切分（spell grants、passive readers）不在本次范围。
- S0-F28 自己记录的未落地项见 #847：Twinned 的有效环阶只作用于目标数、选项清单两份硬编码、2 / 10 / 17 级的选项数需对 SRD 确认。

## 13. 下游接入记录：S0-F17 Cunning Strike（2026-11-26）

本节只记「F17 消费本片产物时确认了什么、往骨架上加了什么」。§1–§12 不改写。

### 13.1 结论：诡诈打击落在 `attack_hit` 声明层，不加新层

| 候选层 | 否掉的理由 |
|---|---|
| `KIND_HANDLERS`（Activation） | 它没有动作槽、不付资源池、不占反应；`cost.slot/resources/timing` 三个字段一个都用不上 |
| `WINDOW_REGISTRY` | 没有 offer 阶段，不需要问「要不要响应别人」 |
| **`ATTACK_DECLARATIONS`** | 命中后才生效、改「这一击产生什么」；`stunning_strike` 是现成样板，多选列表天然支持 11 级「一次两种」 |

### 13.2 未新增意图布尔、未新增 decision kind

- 载荷是 `cunning_strike_effects: ["trip"]`（11 级起可为两项），与 `brutal_strike_effects` 同形：**一个声明 id + 一个受内容闭集约束的列表**，不是六个声明 id，也不是六个意图布尔。
- 六个效果**不各注册一个 canonical id**：效果集合是内容（14 级才加入后三种），而 `ATTACK_DECLARATIONS` 是代码里的静态字典；注册六条等于把「哪些已解锁」写进代码。
- 声明在攻击检定之前提交，扣骰在 `roll_damage_node()` 之前。**没有**新开「命中已定、伤害未掷」的窗口——那会是系统里唯一一个伤害之前的窗口，且要给 `/auto` 加自动弃权分支。

### 13.3 为 F17 扩的两处（登记，不是先斩后奏）

1. **`_hit_condition_events` 的 DC 从写死 WIS 改成逐行 `save_dc_ability`**（`combat/martial_actions.py`）。现状 `8 + PB + WIS` 只服务武僧；诡诈打击是 `8 + PB + DEX`。改法是删掉那条特性专用公式、由行携带属性；`stunning_strike` 补 `"save_dc_ability": "wis"` 保持行为不变，`open_hand` 行继续由调用方按 WIS 传 DC。
2. **`plan_condition` 的返回形状扩展**：行可以携带 `dice_cost` / `save_repeat` / `ends_on_damage` / `turn_choice_limit` / `granted_move` / `save_dc_ability`。`AttackDeclaration` 的字段**不变**——它本来就允许裸 dict，扩展的是执行器对行数据的解释，不是声明模型。

两条都不增加 `combat_coupling_baseline.py` 的条目：声明与载荷读取都在 `features/attack_declarations.py`（不在扫描的 combat 包内），执行器按「行里有什么字段」分派而不是按 feature id 分支。

### 13.4 明确不属于本骨架的两件事

- **骰费不是 `cost.resources`**：它是每击临时算出来的、不进任何资源池，`Cost` 三字段不动。
- **`turn_choice_limit`（Daze）与 `save_repeat`（Poison / Knock Out）不是本片产物**：两者都已有别的消费者（Abjure Foes 用前者，Intimidating Presence 与多个法术用后者），F17 只是多一个调用方。这一点在写 F17 任务书时逐条核实过，避免了重造一套原语。

### 13.5 尚未闭合的下游缺口

- **GAP-48**：`attack_declarations` 没有任何面向玩家的投影与 UI（`_attack_state_options()` 只投影 reckless / brutal / studied，`frontend-v2/src` 对四个声明零支持）。GAP-43 把声明化做完了，但玩家侧仍只能靠 API 传。
- **GAP-49**：借机攻击的 answer intent 只有 `option`，没有声明通道，所以诡诈打击与 Brutal Strike 都无法用在借机攻击上。
