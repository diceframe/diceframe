# D3 物品激活、充能与弹药 执行报告

- 日期：2026-11-04
- 分支：`feat/dnd2024-3d-item-activation`，基线 `531a8539`（D2 merge head，已同步上游 main `81a56f16`）
- 任务书：`D3_ITEM_ACTIVATION_TASK_SPEC_2026-11-03.md`
- 状态：实现与验收完成（T1–T20 均有对应覆盖）；D3 债务见 §8

## 1. 基线与行号漂移

任务书锚点以 `9c2ca59d` 为参照，执行分支基于 D2（`531a8539`），D1/D2 与上游前端提交使行号整体下移，但无结构性漂移：

| 锚点 | 实际落点（当前分支） | 漂移 |
|---|---|---|
| A1 | `combat/primitives.py` `INTENT_TYPES` | 仅行号 |
| A2 | `combat/validation.py` `_validate` 与 `_validate_item_activation` | 仅行号 |
| A3 | `combat/engine.py` `apply_batch` / 结算分发 `activate_item` 分支 | 仅行号 |
| A4/A5 | `combat/resolution.py` `_activate_item_events`、既有治疗管线 `formula_node_from_dice_formula` + `roll_damage_node` | 未改治疗管线本身 |
| A6 | `combat/reducer.py` `dnd2024.item.charge_spent` 写回 canonical | 新增事件白名单 |
| A7/A8 | `exploration/engine.py` 意图集合、`_validate_activate_item`、`_reduce_event`/`_apply_hp_change` | 仅行号 |
| A9 | `resting/engine.py` 短休 / 长休末 `_recharge_items` | 新增挂接 |
| A10 | `character/attunement.py` `attuned_refs` | 只读复用 |
| A11 | `src/commands/player_state_applier.py:_use_inventory_item` | 未修改 |
| A12 | `combat/catalog.py:from_bundle` 弹药引用校验 | 新增 |
| A13 | `character/reconciliation.py` `_PROJECTED_ITEM_TYPES` 与充能初始化 | 新增初始化 |

`.rulebooks` 在本执行环境不存在，无法回填 DMG2024 激活/充能/回充原文行号；未编造行号。

## 2. 实现摘要

### 2.1 新增 owning module `src/rulesets/dnd2024/items/`

- `activation.py`：
  - `validate_activation_declaration`：`cost ∈ {action, bonus_action}`、`target ∈ {self, ally}`、`effect.mode` 仅 `healing`、治疗骰式必须匹配既有 `DICE_RE`、可选 `charge_cost` 正整数；未知键 fail closed。
  - `validate_consumable_declaration`：`consumable` 只允许 bool，缺失视为 false。
- `charges.py`：
  - `validate_charges_declaration`：`charges` 仅 `max` 正整数；`max` 保持 bundle 静态权威。
  - `validate_recharge_declaration`：`on ∈ {dawn, short_rest, none}`；`amount` 为正整数或合法骰式，缺省 1；`on: none` 携带 `amount` 直接拒绝。
  - `charges_of` / `spend_charges`（纯计算）/ `initialize_item_state`（缺失才写 `current=max`，从不重置）/ `recharge_for_rest`（只处理仍持有物品，固定值或掷骰，封顶 max，回充骰式记录进事件）。
- 运行时唯一可变充能权威是 `canonical.equipment.item_state[item_ref].charges.current`，未复制 `max`。

### 2.2 战斗层

- `combat/primitives.py`：`INTENT_TYPES` 新增 `activate_item`；未复用 engine `ACTION_KINDS` 的 `consumable` 空壳。
- `combat/validation.py`：
  - 失能集合加入 `activate_item`；
  - 持有数量、同调门控（`attuned_refs`）、动作经济、充能初始化/余额、`charge_cost` 与 `charges` 配对、同 ref 多件拒绝、目标 `self`/存活同阵营、死亡目标拒绝；
  - 远程武器攻击新增弹药前置（仅玩家；companion 保持旧行为，见 §8）。
- `combat/resolution.py`：
  - `_activate_item_events` 原子批次：动作经济 → `item.charge_spent` → `item.consumed` → 复用既有治疗骰管线（`rolls`，`max(1)`）→ `item.activated` 审计；
  - 远程攻击在武器声明 `ammunition_item_ref` 时追加 `item.consumed`。
- `combat/reducer.py`：登记三类物品事件字段白名单；`charge_spent` 写回 canonical 并拒绝越界/不足/未知字段；`consumed`/`activated` 为审计 no-op（数量由 live 背包权威接口落账）。
- `combat/engine.py`：`apply_batch` 先构建投影并通过 `take_from_inventory` 扣减 live 背包，成功后才 `replace_state`；扣减失败抛 `EventBatchError`，不产生半状态。

### 2.3 探索层

- `EXPLORATION_INTENT_TYPES` 新增 `exploration.activate_item`；只允许 `item_type=potion` 且 `target=self` 的治疗激活。
- 战斗外不消耗动作经济，但仍扣数量/充能；同调、同 ref 多件守卫与战斗一致。
- 治疗事件与战斗同构，走既有 `_apply_hp_change`；`apply_batch` 同样先扣 live 背包后提交状态。

### 2.4 休息与对账

- 短休/长休末尾调用 `recharge_for_rest`：`dawn` → 长休、`short_rest` → 短休、`none` 不回充；事件 `recharge_item` 携带 `before/after/rolls`。
- `reconciliation` 首次见到声明 `charges` 且 `item_state` 缺失的持有物品时初始化 `current=max`；既有 `current` 不被休息、换装或重复 reconcile 重置，并纳入幂等短路比较。

### 2.5 数据

- 新增 `content/items/bolts.json` 与 en/zh-CN locale（对照既有 arrows）。
- `srd_combat_core.json`：shortbow/longbow → `item:arrows`；三种弩 → `item:bolts`。
- `combat/catalog.py` 装载期校验：引用必须 `item:` 前缀、武器必须 ranged、目标必须存在且 `item_type=ammunition`。
- 治疗药水未写入 bundle：`docs/rulesets/dnd2024/SOURCE_MAP.md` 无魔法物品来源映射且 `.rulebooks` 缺失，无法提供可核对 `source_ref`；按任务书要求改用测试 fixture，未伪造来源（§8）。

## 3. 测试矩阵结果

新增 `tests/rulesets/test_dnd2024_item_activation.py`，共 17 个用例：

| # | 结果 | 覆盖 |
|---|---|---|
| T1 | ✅ | 战斗内药水：动作消耗、数量 -1、治疗事件带 `rolls`、HP 不超上限 |
| T2 | ✅ | 探索药水：不消耗动作经济、数量 -1、HP 上升、事件同构 |
| T3 | ✅ | 未持有与 `qty: 0` 均拒绝，零状态变更 |
| T4 | ✅ | wand fixture 首次初始化 `current=max`；按 `charge_cost` 扣减并写回 canonical |
| T5 | ✅ | 充能不足整体拒绝，动作与充能不扣 |
| T6 | ✅ | reducer 越界/不足/未知字段拒绝 |
| T7 | ✅ | `requires_attunement` 未同调拒绝，同调后可激活 |
| T8 | ✅ | 动作已花拒绝，数量不变 |
| T9 | ✅ | `on: dawn` 映射长休；固定值与 `1d6+4` 掷骰、封顶、事件带 `rolls` |
| T10 | ✅ | `on: short_rest` 只在短休触发 |
| T11 | ✅ | `on: none` 不回充；未知键/`none+amount` 装载 fail closed |
| T12 | ✅ | 重复 apply 同批次不重掷、不重复扣充能/数量 |
| T13 | ✅ | 弓扣 arrows、弩扣 bolts；空弹药在 validation 拒绝；近战不耗弹药 |
| T14 | ✅ | 非法 cost/target/mode/骰式/charge_cost/未知键均 fail closed |
| T15 | ✅ | 战斗与探索激活均不改法术位、无 `spell.slot_spent`/`counterspell` |
| T16 | ✅ | 激活事件无 `movement_cost`、无 `opportunity_attack` |
| T17 | ✅ | 药水 fixture 无叙事 `effect` 文本，治疗仍由新管线产生，未经 `_use_inventory_item` 文本解析 |
| T18 | ✅ | rulesets 全量回归通过（见 §6） |
| T19 | ✅ | 同一 charged ref 多件在战斗与探索均拒绝，不扣动作 |
| T20 | ✅ | 重复 reconcile 不重置 `current`；`item.consumed` 只扣目标 ref |

## 4. 白名单符合性与偏差裁决

1. **测试文件合并**：任务书 §3.10 要求新增 `test_dnd2024_item_charges.py`，实际合并为单个 `test_dnd2024_item_activation.py`（17 用例），避免重复 fixture；覆盖面不缩水。
2. **数量扣减落点**：任务书 §3.5 写“经既有 character/economy 权威接口扣 backpack qty”。reducer 只处理 canonical 快照，无法访问 live sheet；实际在 `combat/engine.py` 与 `exploration/engine.py` 的 `apply_batch` 中、`replace_state` 之前调用 `take_from_inventory`，失败即 `EventBatchError`。方向与 D2“reducer 状态清理 + engine 补事件”先例一致。
3. **额外导出**：`validate_consumable_declaration`、`item_charges_max`。前者用于把非 bool 的 `consumable` fail closed；后者是 `validate_charges_declaration` 的只读别名，供消费者使用。
4. **事件白名单重复**：战斗与探索各有独立 reducer，`_ITEM_EVENT_FIELDS` 就地声明，不跨层共用，避免 generic path 耦合。
5. **审计事件语义**：`item.consumed` / `item.activated` 在 reducer 中为 no-op，只进入权威 ledger；`item.charge_spent` 是唯一写 canonical 的物品事件。
6. **`recharge_item` 字段**：固定值回充时 `rolls` 为空列表，保持事件形状稳定。
7. **探索范围**：严格限定 `item_type=potion` 且 `target=self`，非药水/非 self 在 validation 拒绝，未扩大为通用物品使用。
8. **未改**：`player_state_applier._use_inventory_item`、engine `ACTION_KINDS.consumable`、法术位/反制法术/准备法术路径、移动/借机攻击事件。

## 5. `dawn` → 长休等价映射

仓库无“黎明”或世界时钟日概念，唯一“新一天”锚点是长休完成。本切片显式约定：

- `recharge.on: "dawn"` 仅在 `complete_long_rest` 触发；
- `recharge.on: "short_rest"` 仅在 `complete_short_rest` 触发；
- `recharge.on: "none"` 永不回充。

该映射写入 `items/charges.py` 的 `_RECHARGE_TRIGGER_TO_REST`，并由 T9/T10/T11 覆盖。

## 6. 验证命令与结果

```bash
python -m pytest -q tests/rulesets/test_dnd2024_item_activation.py
# 17 passed
python -m pytest -q tests/rulesets
# 1105 passed, 36 warnings in 426.01s
ruff check <changed files>
# All checks passed
```

## 7. 全仓回归

```bash
python -m pytest -q --cov=src --timeout=120
# 8107 passed, 109 failed, 7 skipped, 1124 warnings in 2322.36s (38:42)
```

109 个失败全部为执行环境缺少可选依赖，与 D3 无关：

- `ModuleNotFoundError: No module named 'pydantic_ai'`（含 `tests/test_assistant_*`、`test_agent_*`）
- `ModuleNotFoundError: No module named 'pydantic'`（2 处）
- `ModuleNotFoundError: No module named 'edge_tts'` 及依赖缺失导致的 `Neural` 正则断言失败（`tests/test_tts.py`）

用 `pytest -q --lf` 复跑失败集合：`109 failed in 193.65s`，错误分布不变，无超时用例、无 D3 相关失败。补充：首次不带 `--timeout` 的全仓运行在本沙箱长时间停滞，安装 `pytest-timeout` 后以 `--timeout=120` 完成；该停滞与本次改动无关。

## 8. 迁移债

| # | 债务 | 归属建议 |
|---|---|---|
| D3-1 | 卷轴（施法路径、不耗法术位施法、环阶与职业列表匹配）未落 | 独立卷轴切片 |
| D3-2 | 激活效果仅治疗；伤害/豁免/区域/条件激活未落 | 激活效果扩展切片 |
| D3-3 | 反应成本激活未落 | 反应窗口切片 |
| D3-4 | 耗尽销毁未落，字段未预留 | D3b |
| D3-5 | 多充能变体与阶梯效果未落 | 阶段二内容需要时 |
| D3-6 | 医疗包与 Healer 专长联动未落 | Healer 专长切片 |
| D3-7 | 同 ref 多件充能状态不可区分，暂以数量守卫拒绝 | 实例 ID / slot map 切片 |
| D3-8 | blowgun/sling/火器弹药内容未落，仍无弹药字段 | 对应装备内容需要时 |
| D3-9 | ~~战斗与探索 `available_intents` 未列出物品激活动作；服务端可结算，但前端无入口~~ **已销账（2026-11-05，随 #653 `ef4a5923`）**：`combat/engine.py` 已下发 `activate_item`、`exploration/engine.py` 已下发 `exploration.activate_item`；前端 `Dnd2024CombatPanel.vue` 有 `itemAction` 选择器与发送路径（模板 `v-if="itemAction && itemChoices.length"`） | — |
| D3-10 | 治疗药水未进入 SRD bundle：`SOURCE_MAP.md` 无魔法物品来源映射、`.rulebooks` 缺失，无法提供可核对 `source_ref`；当前仅测试 fixture | 魔法物品来源映射补齐后随内容切片落地 |
| D3-11 | companion 远程攻击不校验/不扣弹药（companion 无 live 背包权威，保持 D3 前行为）；companion 消耗品激活因无背包数量实际不可达 | companion 装备/背包权威切片 |
| D3-12 | `item_type: potion` 没有集中 enum 校验；当前仅由激活消费者按类型分支 | 物品 schema 集中校验切片 |

## 9. 未闭环 / 后续

- PR 已创建：`diceframe/diceframe#653`（head `feat/dnd2024-3d-item-activation`，squash 单提交 `ef4a5923`，目标 `diceframe/diceframe:main`）。**后续（2026-11-06）：三项 CI 已全绿（Backend 32m28s、Browser smoke 6m17s、Frontend 2m45s），等待维护者合并。**
- D4（随机宝物表）已在本分支落地：`treasure/tables.py` + `tests/rulesets/test_dnd2024_treasure_tables.py`；**执行报告仍待补**。
- D3b（耗尽销毁）已完成：`items/charges.py` 落地声明校验与判定，fork #6 合入本地集成分支 `integration/dnd2024-srd`；报告见 `D3B_CHARGE_DESTROY_EXECUTION_REPORT_2026-11-06.md`。其白名单文件依赖本切片的 `charges.current` 权威，因此仍 stacked 在 #653 之上、不回流该 PR。
