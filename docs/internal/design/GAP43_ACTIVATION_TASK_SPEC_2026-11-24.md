# GAP-43 任务书：主动行动的声明与成本（Activation + kind 注册表）

| 项 | 值 |
|---|---|
| 缺口编号 | GAP-43 |
| 验收载体 | `FeatureCapability` 的 38 条（含 8 条空成本）、`_class_capability_events` 的 24 个 kind 分支、`attack_hit` 组的 6 个意图布尔 |
| 规则基准 | SRD 5.2.1 的动作成本原文 |
| 代码基线 | **等 #836 / #838 / #839 / #841 合并后**，基于当时的 main |
| 前置任务 | #515（shrink-only 护栏）、SP3、F14、F20、F21、F28、F30 |
| 状态 | 待执行；2026-11-24 按维护者在 #843 的口径修订 |
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
- `attack_hit` 组 6 个意图布尔：`hurl_through_hell`、`quivering_palm`、`stunning_strike`、`brutal_strike`、`activate_sacred_weapon`、`light_extra_attack`。
- `_class_capability_events` 约 290 行、**24 个 `action.kind` 分支**，其中 11 个是单特性 kind（`quivering_palm_end`、`holy_nimbus`、`dragon_wings` 等），另有 `feature_id == "step_of_the_wind"`、`"feature_kind": "intimidating_presence"` 这类具体 id。
- `economy[cost]` 实际 7 处：`validation.py:1219 / 2387 / 2787 / 2870`、`engine.py:683`、`view.py:690 / 891`（行号以合并后的 main 为准）。七处都是 `< 1` 的可用性判断，没有一处是支付——所以它们的目标是 `_slot_available()`，不是 `_pay_cost()`。

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
