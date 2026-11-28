# S0-SP2 执行报告：`condition` 的检定维度与二选一

| 项 | 值 |
|---|---|
| 状态 | **完成**（3 条落地，4 条退回） |
| 门禁 | SP2 用例 7 条通过；`spell_coverage --check` 通过（53）；ruff/mypy 干净 |
| 结论 | `condition` 现在能表达"按属性限定的检定劣势"与"二选一"，三条法术端到端 |

## 1. 落地

| 法术 | 用到的扩展 | 验收 |
|---|---|---|
| `mind_spike` | 无（`save: "wis"` + `3d8` + `half_on_success`） | ✅ 成功半伤 7 点、失败满伤 15 点，两个分支都断言具体数值 |
| `blindness_deafness` | `condition` 对象形态（`{choices, picked}`） | ✅ 失败时施加 `blinded`（声明值），成功时无任何条件 |
| `ray_of_enfeeblement` | `condition.check_modes` | ✅ 失败施加 `enfeebled`；力量豁免双骰取低、敏捷不做双骰 |

## 2. 两处 schema 扩展

**`condition.check_modes`** — 条件对检定的优劣势，按 `surface` 与 `abilities` 限定。

`enfeebled` 声明三条（`ability_check` / `skill_check` / `saving_throw` 各一，`abilities: ["str"]`），因为 "Strength-based D20 Tests" 同时覆盖三类检定。消费点在三个入口：`_skill_check_events`（按技能默认属性匹配）、`_ability_check_events`（原本是单骰，新增双骰路径）、`_saving_throw_check`（挂进既有的 `save_sources`）。

**`spell_effects.condition` 允许对象** — `{choices, picked}`。

只服务二选一。`picked` 是**声明值**：本片不支持"由施法者在意图里选"，那要在 intent 上加参数；等真有第二个二选一法术再说。装载期校验候选项必须已登记，`picked` 必须落在范围内。

## 3. 关键发现：重复豁免早就存在

我原本设计了 `ongoing.save_repeat`，写到一半发现 **`save_repeat` 是既有机制**——`hold_person` 就用它，`catalog.py:368` 读、`_save_repeat_rows` 在目标回合结束时消费。

`ongoing` 因此只保留了"每回合自动伤害"的位置，而那一项本片也没做（见 §4）。**差点造一个平行机制**，是查 `_save_repeat_events` 时拦下的。

## 4. 退回（4 条）

| 法术 | 缺什么 | 归属 |
|---|---|---|
| `phantasmal_force` | 每回合自动 2d8；需要"施法者回合开始 + 仍在法术内的目标"的追踪 | GAP-SP4 |
| `ray_of_enfeeblement` 的 −1d8 | 伤害骰减值要动 `hit_resolution` 的骰池，会牵连暴击翻倍逻辑 | GAP-SP4（任务书 §7 停止条件） |
| `heat_metal` | 附赠动作再伤害（授予动作，不是 tick）+ 掉落持握物 | GAP-SP5 |
| `vampiric_touch` | 魔法动作再攻击 + 施法者回一半 | GAP-SP5 |

`ray_of_enfeeblement` 因此只落地了一半：**检定劣势生效，伤害减值没有**。执行报告必须说清楚，否则读者会以为整条法术完成。

## 5. 门禁

```
pytest tests/rulesets/test_dnd2024_sp2_condition_depth.py   7 passed
python scripts/spell_coverage.py --check                    退出 0（53/53）
```

覆盖率：1–3 环 34 → 37 条（22% → 24%）。
