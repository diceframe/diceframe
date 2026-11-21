# S0-F25 执行报告：变形地基（面板 / 状态 / 接线）

| 项 | 值 |
|---|---|
| 切片 | S0-F25 的地基部分（GAP-21 / GAP-23；GAP-22 见 §4） |
| 基线 | 叠在 #829（MM2）之上 |
| 状态 | 地基完成，**玩家暂不可用**（无形态攻击数据，见 §4） |

## 1. 交付

| 交付 | 位置 |
|---|---|
| 统一面板 + 四种合并策略 | `forms/panel.py`（`StatPanel` / `compose` / `WILD_SHAPE_POLICY` / `POLYMORPH_POLICY`） |
| 变形状态与限制 | `forms/state.py`（`FormState` / `assume_form` / `restrictions` / `wild_shape_state`） |
| 角色级持久化 | `combat/reducer.py`：`dnd2024.form.assumed` / `.reverted` 写入 `ruleset_character.forms` |
| 视图合成 | `combat/view.py::_shaped_view`（找形态 → 合成 → 回填既有键） |
| 技能派生修正 | #829 的 `fix` 提交（D-1） |
| 裸属性检定 | `ability_check` 意图，成本 `action` / `bonus_action` / `none` |

## 2. 为什么是"策略表"而不是分支

Wild Shape 与 Polymorph 只差**哪些槽被替换**：前者保留 HP / HD / 心智三项 / 类型 / 语言，后者全换。四种策略（`keep` / `replace` / `max` / `union`）表达完所有情况，引擎只有一条合成路径；新增 Shapechange 是第三张表。

**未在表里出现的槽一律 `keep`**：默认替换会让一次变形悄悄多给一个没人审过的槽。

## 3. 验证

```
form_panel                                                          23 passed
全量 tests/rulesets                                      2683 passed（14:21）
ruff / mypy                                                        干净
```

端到端（6 级德鲁伊 → 狼）：`STR 14 / DEX 15 / CON 12` 来自形态，`WIS 16` 保留，`HP 39` 保留，`AC 15`（取高），`attacks = [bite-0]`，`spellcasting_allowed = False`。`ruleset_character.forms.active` 落库。

## 4. 未完成（明确记录）

1. **形态没有可用的攻击**：`attacks[]` 只能表达"单次攻击"，而野兽真正的玩法依赖特质与动作（Multiattack / 豁免型 / rider）。这一项属 MM3（特质与动作），在此之前 F25 对玩家**仍不可用**。
2. **装备三态仅记录不消费**：`equipment_mode` 已存，`merge` 时尚未真正屏蔽装备加值。
3. **时长到期未处理**：`duration_hours` 已存，没有"休息/时间推进时自动恢复原形"的消费者。
4. **读档路径未验证**：`forms` 落在 `ruleset_character`，但存档 schema 是否保留未知键尚未验证——需要一条真实的 save/load 往返用例。
5. **`actor_view` 只覆盖玩家**：companion 与敌人的形态入口未接（当前也没有这种效果）。

## 5. 自查清单（本项目固定六条）

- 等价性：玩家侧派生未改数值（既有 2683 条全绿）
- 多消费者一致性：`restrictions()` 是唯一施法门控来源
- 跨特性交互：`form` 与 `condition` 分层——条件在最上层，变形不清 buff
- 递归/回路：Fleet Step 那类自授予未涉及；形态不会再变形（无嵌套入口）
- 条件生命周期：`steady_aim_slow` 等由既有机制处理，未新增
- 测试是否安慰剂：断言落在 `actor_view` 的最终数值与来源表，不落在中间结构
