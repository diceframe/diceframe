# S0-SP3 执行报告：成功分支的条件（GAP-54）

| 项 | 值 |
|---|---|
| 状态 | **完成** |
| 门禁 | SP3 用例 7 条通过（3 条端到端 + 4 条装载期拒绝）；全量见下 |
| 结论 | `ray_of_enfeeblement` 的成功豁免不再空放；GAP-54 关闭 |

## 1. 改了什么

`spell_effects.condition_on_success` —— 成功豁免后仍要施加的条件。

```json
{"condition": "next_attack_disadvantage", "duration": "caster_next_turn_end"}
```

只接受对象、键集严格 `{condition, duration}`。理由：成功分支的时长几乎总与失败分支不同（这里是"到你下回合开始"，失败分支是一分钟），裸字符串会逼出"时长从哪来"的猜测。

施加路径**与失败分支共用**：免疫检查、`source_spell_ref` 快照、`concentration_owner` 一条不少。逻辑上是把"用哪个条件、用哪个时长"提前决定，后面的落地代码一行没复制。

## 2. 条件不用新建

开工前核实发现 `next_attack_disadvantage` **已经在 `condition_effects` 里**，`consume_on: "attack_resolved"`，消费者在 `engine.py`。所以本片没有新增条件，任务书 §4 的"需要一个条件"改成了"条件已经存在"。

这也说明为什么"先核实再写"值得：如果按任务书原样去造一个新条件，会造出第二个"下次攻击劣势"，和既有的那个并存。

## 3. 验收

| 用例 | 断言 |
|---|---|
| 成功豁免 | 只施加 `next_attack_disadvantage`、时长 `caster_next_turn_end`、**没有** `enfeebled` |
| 失败豁免 | 施加 `enfeebled`、**没有** `next_attack_disadvantage` |
| 来源快照 | `source_actor_id` / `source_spell_ref` / `concentration_owner` 三个键都指向施法者 |
| 装载期拒绝 | 四个畸形声明（缺时长 / 缺条件 / 多键 / 裸字符串）都抛 `CombatCatalogError` |

## 4. 仍未做

- `ray_of_enfeeblement` 的 **−1d8**（GAP-57）；
- `enfeebled` 的**攻击劣势**（GAP-53）。

这两条都还在 GAP 册里，这一片只关 GAP-54。

## 5. 门禁

```
pytest tests/rulesets/test_dnd2024_sp3_condition_on_success.py   7 passed
ruff / mypy                                                     干净
```
