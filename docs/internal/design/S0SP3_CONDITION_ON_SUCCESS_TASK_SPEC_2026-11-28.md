# S0-SP3 任务书：成功分支的条件（GAP-54）

| 项 | 值 |
|---|---|
| 切片编号 | S0-SP3（接 S0-SP2） |
| 缺口 | GAP-54：`spell_effects` 不能"成功时也施加条件" |
| 验收载体 | `ray_of_enfeeblement` 的成功豁免真的给"下次攻击劣势" |
| 前置 | S0-SP2（#899，`condition` 可对象、`check_modes` 已存在） |
| 状态 | 待执行 |

## 1. 为什么单独一片

`spell_resolution` 施加条件的判定是：

```python
condition_applies = (
    (mode in {"spell_attack", "automatic_damage"} and succeeded)
    or (mode == "saving_throw" and not succeeded)
    or mode in {"buff", "debuff"}
)
```

`saving_throw` 只在**失败**时施加。而原文里有一类法术成功豁免后仍有后果，最典型的是 `ray_of_enfeeblement`：

> "On a successful save, the target has **Disadvantage on the next attack roll it makes until the start of your next turn**."

现在的实现下，目标豁免成功 = 法术完全空放。这是 SP2 明确记下的缺口。

## 2. 原文（这一片要对的字）

`ray_of_enfeeblement`（p157，`srd_spell_descriptions.json`）：

- **失败**：Disadvantage on Strength-based D20 Tests + subtracts 1d8 from damage rolls + **每回合结束重复豁免**
- **成功**：Disadvantage on the **next attack roll** it makes **until the start of your next turn**

两半的条件**不同**，时长**也不同**。所以不能只加一个布尔开关。

## 3. 设计：`condition_on_success`

```json
"ray_of_enfeeblement": {
  "mode": "saving_throw", "save": "con", "range": 60,
  "condition": "enfeebled", "condition_duration": "1_minute",
  "concentration": true, "save_repeat": "con",
  "condition_on_success": {"condition": "next_attack_disadvantage",
                           "duration": "caster_next_turn_end"}
}
```

要点：

- **只接受对象**，不接受裸字符串。理由：成功分支的时长几乎总是与失败分支不同（这里是"到你下回合开始"），若允许裸字符串就得再想一个"时长从哪来"的规则。
- 键集合严格 `{condition, duration}`，`duration` 必须是引擎认识的时长码（与 `condition_duration` 同一套）。
- 施加逻辑与失败分支**走同一条路**：免疫检查（`_condition_immunity_blocker`）、`source_spell_ref` 快照、`concentration_owner` 都要一致。不得复制一份应用代码。
- **专注**：成功分支也施加条件时，`concentration_owner` 仍按 `effect.concentration` 决定——这里是 true，所以成功分支的条件也归本次施法的专注。这条要在测试里落一条。

## 4. 条件已经存在（开工前核实过）

`next_attack_disadvantage` **已在 `condition_effects` 里**：

```json
{"kind": "effect", "source_ref": "...",
 "attack_modes": [{"side": "actor", "mode": "disadvantage", "consume_on": "attack_resolved"}]}
```

取值是 `"attack_resolved"`（不是 `"attack_roll"`），消费者在 `engine.py` 的消耗逻辑里。所以本片**不新增条件**，直接引用。

要核实的是：它生效时 `source_ref` 指向别处——成功分支复用它时，`source_ref` 会记成 `ray_of_enfeeblement`（施加路径会写 `source_spell_ref` 快照），条件条目自己的 `source_ref` 不影响结算。这一点在用例里覆盖。

## 5. 验收

1. **成功分支**：`rolls=[20]` → 目标拿到 `next_attack_disadvantage`，且**没有** `enfeebled`；
2. **失败分支不回归**：`rolls=[1]` → 拿到 `enfeebled`，**没有** `next_attack_disadvantage`；
3. **用完即清**：目标做一次攻击后，条件消失（若 §4 的 `consume_on` 不生效，这条会红，那时补消费而不是改断言）；
4. **免疫路径一致**：给目标加一个免疫该条件的来源，成功分支也不落地，且事件里有 `blocked_condition`。

## 6. 不做

- `ray_of_enfeeblement` 的 −1d8（GAP-57，要动伤害拼池）；
- `enfeebled` 的攻击劣势（GAP-53，要动攻击属性来源）；
- 其他任何"成功分支"法术——先只让这一条跑通，schema 的通用性等第二条出现再说。

## 7. 停止条件

- `consume_on: "attack_roll"` 没有消费者，且补它要动 `attack_resolution` 主路径 → **停**，把成功分支记回 GAP-54，不要为一个条件改攻击结算；
- 成功分支的条件需要与失败分支**不同**的专注归属 → 停，先定"一次施法只能有一份专注"的语义。

## 8. 交付物

- `condition_on_success` 的 schema + 校验 + 消费（与失败分支共用施加路径）；
- `next_attack_disadvantage` 条件条目；
- `ray_of_enfeeblement` 的 effect 更新；
- 4 条验收用例；
- 执行报告 + `SLICE_STATUS` 的 SP3 行；GAP-54 关闭或降级。
