# 切片 D3b 执行报告：充能耗尽销毁（Destroy on Empty）

- 任务书：`D3B_CHARGE_DESTROY_TASK_SPEC_2026-11-03.md`（作者本地施工资料，未入库；下文 §3/§7 引用的章节号均指该任务书）
- 开发基线：`e7b7080f`（PR #653 复审修复后的头）；上游 PR #687 已 rebase 到合并 #653 之后的 `main`
- 状态：实现与测试完成；集成时补了加载校验与匿名行清理，见 §8

## 1. 落地内容

| 文件 | 改动 |
|---|---|
| `src/rulesets/dnd2024/items/charges.py` | `DESTROY_KEYS`、`validate_destroy_declaration`、`should_check_destroy`、`destroy_check_events` |
| `src/rulesets/dnd2024/combat/resolution.py` | `_activate_item_events` 末尾追加判定；新增 `_destroy_check_events` / `_canonical_for_actor` |
| `src/rulesets/dnd2024/exploration/engine.py` | 同一判定接入探索层激活；apply 段清理 live 行 |
| `src/rulesets/dnd2024/combat/reducer.py` | 两个新事件登记进 `_ITEM_EVENT_FIELDS`；`destroyed` 归约摘除 ref |
| `src/rulesets/dnd2024/combat/engine.py` | apply 段对 `destroyed` 清理 live 背包/装备行 |
| `src/commands/state_items.py` | 新增 `remove_destroyed_item`（按 ref 删行，幂等） |
| `tests/rulesets/test_dnd2024_item_destroy.py` | 新增，18 例 |

## 2. 语义

- 触发条件：`destroy_on_empty` 已声明 **且** 本次激活扣减后恰好为 0（`charges_of() - charge_cost == 0`）。扣减读的是激活前的 canonical 值，因为 `charge_spent` 要等归约才落地。
- 判定：`rng.randint(1, 20)`，`roll <= threshold` 即销毁。`threshold` 默认 1，取值 1–20；`die` 固定 `1d20`，未知键 fail closed。
- 顺序：判定排在 `dnd2024.item.activated` 之后，治疗已结算，销毁不回滚。
- 事件：`dnd2024.item.destroy_check{actor_id,item_ref,roll,threshold,destroyed}`；销毁时追加 `dnd2024.item.destroyed{actor_id,item_ref}`。
- 归约：从 `equipment.item_refs`、`equipment.item_state[ref]`、`attunement.refs` 摘除该 ref，随后 apply 段删掉 live 背包/装备行。只按 ref 匹配，同名不同身份的另一件不受影响。

## 3. 白名单符合性与三处偏差

任务书 §3 白名单包含五个源文件 + 一个新测试文件。实际多动了两个源文件、并在白名单文件里增加了一个函数，逐条说明：

### 偏差 1：`items/charges.py` 多一个 `destroy_check_events`

任务书只要求 `validate_destroy_declaration` 与 `should_check_destroy`。若两个引擎各自拼事件，事件形状与判定顺序会出现两份实现并逐渐漂移，所以把「掷骰 + 组事件」收成一个纯函数，两处调用。`should_check_destroy` 仍然独立存在并单独测试。

### 偏差 2：两个新事件都带 `actor_id`

任务书 §3.2 的事件形状只列 `item_ref/roll/threshold/destroyed`。但 `dnd2024.item.*` 家族其余三个事件（`consumed`/`activated`/`charge_spent`）都带 `actor_id`，归约也需要它定位持有者（队伍有多名玩家时无法只凭 ref 反查）。因此补上，形状与家族一致。

### 偏差 3：动了 apply 段（`combat/engine.py`、`exploration/engine.py`）与 generic `commands/state_items.py`

任务书 §7 把「销毁需要移除非 canonical 的第二处状态副本」列为停止条件。这里没有停，理由与边界如下：

- live `sheet["inventory"]` / `sheet["equipment"]` 确实是 canonical 之外的第二处副本；只改 canonical 的话，下一次 `_annotate_row` + reconcile 会把刚删掉的行原样写回 canonical，销毁等于没发生。
- 这条路径**不是新权威**：`dnd2024.item.consumed` 早已在同样的 apply 段用 `take_from_inventory` 清 live 背包（`combat/engine.py:1045`、`exploration/engine.py:791`）。本次只是复用既有落账点，新增的 `remove_destroyed_item` 是 generic 的「按 ref 删行」工具，不含 D&D 语义。
- 若维护者认为这仍应留到状态权威收敛之后再处理，可退回只改 canonical 的版本，但需要同时接受「销毁后 live 行仍在、下次 reconcile 复活」这个已知缺陷，并登记为新债。

## 4. `.rulebooks` 行号

本机无 `.rulebooks`，无法定位 DMG2024 原文页码/行号。按任务书要求**不编造**，此处留空待回填：

| 规则 | 原文出处 |
|---|---|
| 耗尽销毁判定 | 待回填（DMG2024 第 7 章，充能物品通用的 "when the last charge is expended, roll a d20; on a 1 the item is destroyed"） |

`threshold <=` 的语义即来自 "on a 1"：默认 1 时只有掷出 1 才销毁；把阈值放宽只是为第三方内容留出口，不改变默认行为。

## 5. 测试矩阵结果

`tests/rulesets/test_dnd2024_item_destroy.py`：**18 passed**（含 7 个参数化非法声明）。

| # | 用例 | 测试 | 结果 |
|---|---|---|---|
| T1 | 未声明销毁、耗尽 | `test_undeclared_item_never_consumes_a_destroy_roll` | ✅ 只有治疗骰一次，`StrictRng` 多掷即失败 |
| T2 | 声明销毁但仍有充能 | `test_declared_item_with_charges_left_does_not_roll` | ✅ 不掷骰，`current` 3→2 |
| T3 | 恰好耗尽、掷出 2 | `test_empty_with_a_roll_above_threshold_keeps_the_item` | ✅ `destroyed:false`，物品保留 |
| T4 | 恰好耗尽、掷出 1 | `test_empty_on_threshold_destroys_and_unbinds` | ✅ 移除 ref、清同调、清 item_state、清 live 行 |
| T5 | 顺序 | `test_healing_settles_before_the_destroy_check` | ✅ `resource.changed` 在 `destroy_check` 之前 |
| T6 | 探索层 | `test_exploration_uses_the_same_event_shape` | ✅ 事件形状一致（探索层只允许药水类，用带充能药水验证） |
| T7 | 非法声明 | `test_destroy_declaration_fails_closed`（7 例）+ `test_destroy_declaration_defaults_and_bounds` | ✅ 全部 `ChargesError` |
| T8 | 回放幂等 | `test_replaying_the_same_batch_does_not_reroll_or_duplicate` | ✅ 第二次 `duplicate=True`、不重掷、不重复删 |
| T9 | 同名他物 | `test_destroying_one_ref_leaves_a_same_named_copy_alone` | ✅ 另一 ref 的行与充能不变 |
| T10 | 回归 | D1–D3 聚焦 8 文件 | ✅ 201 passed |

附加：`test_should_check_only_when_exactly_emptied`、`test_destroy_check_events_are_pure` 覆盖纯函数的边界（`None`、剩余 >0、未声明）。

## 6. 验证命令与结果

```bash
python -m pytest -q tests/rulesets/test_dnd2024_item_destroy.py          # 18 passed
python -m pytest -q <D1-D3b 聚焦 8 文件>                                  # 201 passed
ruff check <改动文件>                                                     # clean
```

## 7. 迁移债

| # | 债务 | 归属建议 |
|---|---|---|
| D3b-1 | 销毁时的附加效果（爆炸/反噬/召唤）未落 | 独立切片（任务书已裁） |
| D3b-2 | 非充能物品的耐久/破损未落 | 不排期，等内容需要 |
| D3b-3 | 官方内容目前无任何 `charges` 声明，端到端只能靠测试 fixture | 随 I0–I3 魔法物品分区解除 |
| D3b-4 | 探索层激活仅允许药水类，因此带充能药水才能走探索路径 | D3 既有裁剪，非本切片引入 |
| D3b-5 | 前端与叙述还不展示 `destroy_check` / `destroyed`，玩家只看到物品消失 | 随物品 UI 切片 |

## 8. 集成复审补丁（上游 #687 → 集成 PR）

- **声明校验接到加载边界**：原实现只在 `destroy_check_events` 里校验，写错的 `destroy_on_empty` 会让每次激活（不只最后一发）在 `resolve_intent` 中途抛出 `ChargesError`。现在 `validate_item_declarations` 在加载时拒绝坏声明和「没有 `charges` 却声明销毁」；战斗与探索的激活意图校验也各补一道，绕过加载校验的坏声明只得到 `INVALID_INTENT`，不消耗随机数。
- **匿名行一起清理**：`remove_destroyed_item` 原先只认显式 `item_ref`。#686 合并后取件按 `item:<item_key>` 认匿名行，销毁也改为同一口径（共用 `_anonymous_row_ref`），否则留下的匿名行会在 reconcile 时把已销毁的 ref 写回。
- 新增测试：`test_bundle_load_rejects_a_bad_or_chargeless_destroy_declaration`、`test_a_bad_destroy_declaration_is_rejected_before_any_roll`、`test_destroyed_cleanup_takes_the_anonymous_row_the_counts_derive`（均在补丁前失败）。
