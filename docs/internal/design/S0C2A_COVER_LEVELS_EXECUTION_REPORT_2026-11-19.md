# S0-C2A 执行报告：掩护等级

日期：2026-11-19
分支：`feat/dnd2024-s0c2a-cover-levels`（基线 `origin/main@9ab8389c`）

## 1. 交付

| 能力 | 位置 |
|---|---|
| 等级闭集 `none/half/three_quarters/total` 与加值（0/2/5/5） | `combat/cover.py` |
| 合并：取最高、永不叠加 | `cover.py::best_cover` |
| 效应轴向 `horizontal` / `ignored{above,ground,explicit}` | `cover.py::cover_axis` |
| GM 声明意图 `combat.set_cover` + 事件 `dnd2024.cover.declared` | `primitives.py`、`validation.py`、`engine.py`、`reducer.py` |
| 战斗状态 `combat.cover = {actor_id: {level, source}}` | `reducer.py` |
| 查询 `cover_for()` / `cover_level()` | `engine.py` |
| 消费者：攻击与法术攻击 AC、Dex 豁免 | `resolution.py`、`reaction_spells.py::effective_armor_class` |
| 投影：actor view 的 `cover` | `view.py` |

## 2. 与任务书的偏差

1. **基线不同**：任务书 §3 写的是"复用既有 `_cover_record`"，实际 `_cover_record` 只存在于未合入的 S-P9 栈上；`origin/main` 没有任何掩护代码。本切片因此在 main 上从零实现查询与两个消费者，`_cover_record` 的合并语义（取最好、不叠加）由 `best_cover` 承担。
2. **光环来源未接**：Smite of Protection 那道灵光同样在未合入的栈上，本切片只做声明式来源；`cover_for` 的合并分支留好了位置（无声明时按等级合并派生来源，当前派生集合为空）。
3. **6 条区域法术未显式标 `cover_interaction`**：缺省即 `horizontal`，显式标注不改变行为。等第一条 `ignored` 效应（冰风暴/焰击/地震/圣火术）落地时再一起标，避免此刻造出一批"看起来有语义、实际没人读"的字段。
4. **Total 不可被选为目标未做**：属 §7 非目标，需要改目标解析器，留到下一切片。

## 3. 测试

`tests/rulesets/test_dnd2024_cover_levels.py`，**12 passed**：

| # | 覆盖 |
|---|---|
| 1 | 等级与加值映射、未知等级按 none、`cover_row` 形状 |
| 2 | 永不叠加：`half + three_quarters = three_quarters` |
| 3 | 轴向三种 `ignored` 理由 + 拼错回默认 |
| 4 | 声明往返：状态、视图、加值 |
| 5 | 撤销（`none`）删键而非留墓碑 |
| 6 | 只有 GM 能声明、非法等级/不存在目标被拒 |
| 7 | ¾ 掩护让攻击目标 AC 12 → 17 |
| 8 | `cover_level()` 给 F17 用的查询 |
| 9 | 再次声明覆盖旧值 |
| 10 | 新战斗从空 cover 开始 |
| 11 | 声明是带 source 的事件，不是隐藏状态 |
| 12 | 未声明时数值与今天一致（AC 12） |

## 4. 未决

1. **Dex 豁免的整合测试缺失**：`_saving_throw_check` 已按轴向与 `dex` 计入掩护，但没有一条端到端用例驱动真实法术豁免（需要一条会打 Dex 豁免且可施放的法术路径）。当前只有单元级覆盖（`cover_axis` + `cover_for`），这条缺口写在 §5。
2. **`effective_armor_class` 的 cover 参数**是新增可选参数，非 call site 默认 0；两处攻击已接入，若以后有第三个调用点必须同样显式传值，否则会静默少算。
3. **声明的作用域是战斗**：战斗结束后 `combat.cover` 随状态重建而消失，未做跨战斗复用（也不应该）。
