# S0-F19 补片（6）：猎人 7 级的两个防御战术选项

| 项 | 值 |
|---|---|
| 编号 | S0-F19 / part 6（补做 F19 的矩阵 11–12） |
| 依据 | `S0F19_HUNTER_SUBCLASS_TASK_SPEC_2026-11-07.md` §33 原文、§4.3 第 4–5 条、矩阵 11–12 |
| 代码基线 | `origin/main@b6fb7c82`（W1 之上） |
| 状态 | **已交付** |

## 1. 原文（F19 §33）

> **Defensive Tactics.** … **Escape the Horde.** Opportunity Attacks have Disadvantage against you. **Multiattack Defense.** When a creature hits you with an attack roll, that creature has Disadvantage on all other attack rolls against you this turn.

两者都**不是条件**（不是"某生物处于某状态"）：

- Escape the Horde 是"这一掷是哪一类掷骰"的事实；
- Multiattack Defense 是"这个攻击者这一回合已经打中我"的成对事实。

## 2. 契约

1. **Escape the Horde**：攻击者是借机攻击、且防御者已选该选项时，攻击骰加一个 `escape_the_horde` 劣势来源；普通攻击不受影响；与其它来源照常相消（`mode_from_sources`）。
2. **Multiattack Defense**：攻击骰**命中**后，在 `combat.attack_marks.multiattack_defense[attacker][defender]` 立一条标记（行上带 `round` / `turn_index`）；同一攻击者本回合后续对同一防御者的攻击骰加 `multiattack_defense` 劣势来源。
   - 第一击**不追溯**（标记在掷骰结算之后才写）；
   - 同一攻击者重复命中不叠加骰数（同一个来源）；
   - 判定用行上的 `round` + `turn_index` 与当前值相等，回合一变即失效；
   - 行以 `turns_remaining: 0` 建立，下一次该攻击者回合开始的清扫直接删除。
3. 两项都只对**持有该选项的人**成立；未拥有、未选择、选择了另一个选项都不产生任何来源。
4. 借机攻击的标志由机会攻击路径写入攻击意图（`opportunity_attack: true`），与"支付改成 reaction"是同一条路径的两面。
5. 战斗结束时清空 `attack_marks`（与 `hands` / `light_attacks` 同组）。这条同时修掉 Studied Attacks 的跨战斗残留：新战斗 round 1 / turn 0 会与新标记同形。

## 3. 白名单

`combat/resolution.py`（来源合成、标记读写、机会攻击标志）、`combat/reducer.py`（战斗结束清理）、`tests/rulesets/test_dnd2024_defensive_tactics.py`。

## 4. 矩阵与用例

| # | 场景 | 用例 |
|---|---|---|
| 1 | 借机攻击对持有者劣势 | `test_escape_the_horde_disadvantages_opportunity_attacks` |
| 2 | 普通攻击不受影响 | `test_escape_the_horde_leaves_ordinary_attacks_alone` |
| 3 | 选了另一项就不生效 | `test_the_other_option_does_not_disadvantage_opportunity_attacks` |
| 4 | 端到端：移动 → 决策 → 反应攻击带劣势 | `test_an_opportunity_attack_against_the_holder_rolls_with_disadvantage` |
| 5 | 第一击不追溯 | `test_the_first_hit_is_not_retroactively_disadvantaged` |
| 6 | 同一攻击者后续攻击劣势 | `test_the_same_attacker_gets_disadvantage_after_hitting` |
| 7 | 回合结束即失效 | `test_the_mark_does_not_survive_the_turn` |
| 8 | 未选该项不留标记 | `test_a_hunter_who_chose_the_other_option_leaves_no_mark` |
| 9 | 7 级以下两个选项都不存在 | `test_a_hunter_below_seven_carries_neither_option` |

## 5. 非目标

- 不改借机攻击本体（威胁表、反应消耗、近战档案筛选）。
- **法术攻击面未覆盖**：标记只在武器/徒手攻击命中时写入、也只在武器攻击的准入里读；猎人的攻击掷骰法术基本都骑在武器攻击上，先把武器链闭合，法术链登记为已知边界。
- 不做短休/长休的选项替换（F19 §114 仍未实现，属另一补片）。
- 不改条件注册表：两项都不是条件。

## 6. 停止条件

1. 需要把"这一掷是哪一类"写进条件注册表 → 停（条件注册表只表达状态）。
2. 需要新一套回合账本 → 停（复用 `attack_marks`）。
3. 选项未经 `feature_option_choice` 就读 → 停（未选择必须等于不生效）。

## 7. 验收

```bash
python -m pytest tests/rulesets/test_dnd2024_defensive_tactics.py -q
python -m pytest tests/rulesets/test_dnd2024_hunter.py tests/rulesets/test_dnd2024_hunters_defense.py tests/rulesets/test_dnd2024_attack_state.py -q
python -m ruff check src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_defensive_tactics.py
python -m mypy
```
