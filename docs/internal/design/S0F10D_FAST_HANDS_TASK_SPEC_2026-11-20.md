# S0-F10D 任务书：Fast Hands ①③（工具存在性 + 队伍借用 + 标准动作→附赠动作）

| 项 | 值 |
|---|---|
| 切片编号 | S0-F10D（S0-F10 拆分后；依赖 F10B 的检定收口） |
| 验收载体 | 工具存在性（含借用）、Sleight of Hand 走通用检定、激活成本从标准动作改附赠动作 |
| 规则基准 | SRD 5.2.1 p64 Fast Hands；p93 工具表（`srd_tools.json` 的 `utilize` 行） |
| 代码基线 | `origin/main@e376f292` + F10B（`_skill_check_events`）合入之后 |
| 前置任务 | **F10B**（本片复用它的检定函数；B 未合入则停止） |
| 状态 | 待执行（等 B） |

## 1. 范围与完成定义

Fast Hands 的三项里，本片做 ① 与 ③，② 归 F10E：

1. **① Sleight of Hand**：开锁 / 拆陷阱 / 扒窃。工具**不加属性**——工具表已有 `utilize: [{task, dc, ability}]`（`thieves_tools`: Pick a lock DC 15、Disarm a trap DC 15、ability dex）。引擎只回答"他有没有这件工具"，DC 与属性从表里读。
2. **③ 魔法物品**：把"需要用**标准动作**激活的魔法物品"改由**附赠动作**支付。不引入"魔法动作"这个概念——只区分标准动作与附赠动作。
3. **② Utilize** 不在本片（GAP-16 未解，见 F10E）。

## 2. 关键决定（与旧任务书相反）

旧任务书 §4.2.5 写"仅以 **Magic action** 使用明确要求 Magic action 的魔法物品"。**本片按用户裁定删除"魔法动作"这一层**：5r 把魔法物品激活归入 Magic action 的写法在引擎里没有对应物，而 Fast Hands 的实际效果只是**动作类型降级**（标准 → 附赠）。

因此 `ACTIVATION_COSTS` **不新增 `magic`**；Fast Hands 的实现是"对 `cost == "action"` 的激活动作，改由 `bonus_action` 支付"。

## 3. 工具存在性与借用

1. 存在性判定读**队伍**而不是单人：`party_has_item(instance, item_ref)`——自己背包优先，其次队友背包。命中队友时，事件记 `borrowed_from: <uid>`。
2. 不做"递东西"这个动作（SRD 里递接属自由交互，且本引擎没有自由交互模型）；借用是**校验层的一次检索**，不产生状态。
3. 缺工具时拒绝，理由 `"requires thieves' tools"`；`available_intents` 用同一个判定（与 F10B 的 `unavailable_reason` 同形）。
4. 工具表里没有对应 `task` 时拒绝，不得用 GM 文本兜底。

## 4. 改动白名单

1. `templates/.../class_feature_catalogs/srd_class_features.json` + locales（`fast_hands` 补 capabilities：`fast_hands_sleight` / `fast_hands_activate`）
2. `src/rulesets/dnd2024/combat/resolution.py`（Sleight of Hand 走 `_skill_check_events`；激活动作的成本替换）
3. `src/rulesets/dnd2024/combat/engine.py`（`party_has_item`、可用性）
4. `src/rulesets/dnd2024/items/activation.py`（**不改枚举**；只在读取处允许"成本被能力改写"）
5. `tests/rulesets/test_dnd2024_fast_hands.py`（新建）

不得改：`ACTIVATION_COSTS`、`INTENT_TYPES`、物品实体（`content/items/*.json`）、工具表数据、前端。

## 5. 逐项编号测试

1. 有工具 + 有目标 → Sleight of Hand 检定按表里 DC 15、ability dex 掷出，事件含 `source="fast_hands"`。
2. 无工具（全队都没有）→ 拒绝，理由 `requires thieves' tools`，不花动作。
3. **自己没工具、队友有** → 允许，事件 `borrowed_from` 指向队友。
4. 工具表缺该 task → 拒绝。
5. 只花一次附赠动作：`bonus_action` 归零，`action` 不动。
6. 拾荒/开锁失败仍消耗附赠动作（RAW）。
7. ③ 一件 `cost: "action"` 的魔法物品，盗贼用 Fast Hands 激活只扣附赠动作。
8. ③ 非盗贼（无 Fast Hands）激活同一物品仍扣标准动作。
9. ③ 物品 `cost` 是 `bonus_action` 时，Fast Hands 不叠加、不额外扣动作。
10. 队伍里两个人都带工具时不重复计算借用来源（取自己优先）。
11. 重放与存档往返：`borrowed_from` 与资源扣减一致。

## 6. 非目标与登记缺口

**非目标**：Utilize（F10E）、GM 裁定原语（GAP-16）、物品属性、工具加值、被动察觉。

**停止条件**：

1. F10B 的 `_skill_check_events` 未合入 → 停止（不得复制一份掷骰）。
2. 若"成本替换"必须改 `ACTIVATION_COSTS` 或物品数据才能表达 → 停止并报告：那说明替换点选错了。
3. 若借用需要新增"递接"动作或队伍共享容器 → 停止。

## 7. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_fast_hands.py -q
python -m pytest tests/rulesets/test_dnd2024_cunning_action.py tests/rulesets/test_dnd2024_hide_preconditions.py -q
ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_fast_hands.py
mypy
```

## 8. 执行报告模板

- 基线 / 分支：
- 工具表读取点与 `party_has_item` 的检索顺序：
- 成本替换的实现位置（为什么不动枚举）：
- 测试 1–11 逐项结果：
- 偏差与未决：
