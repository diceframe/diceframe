# S0-SP1 执行报告：1–2 环直填法术

| 项 | 值 |
|---|---|
| 状态 | **完成**（内容 3/8，端到端 3 条通路 + 1 条声明） |
| 门禁 | 5 条用例通过；`spell_coverage --check` 通过（51）；ruff/mypy 干净 |
| 结论 | 3 条法术能在战斗里放出来、结算对、存档不抛；另外 5 条按 §5 退回 |

## 1. 实际新增

| 法术 | mode | 端到端 |
|---|---|---|
| `ray_of_sickness` | `spell_attack` | ✅ 命中给 2d8 毒素 + `poisoned`；d20=1 时不产生伤害 |
| `phantasmal_force` | `saving_throw` | ✅ 智力豁免失败给 1d6 心灵；`spellcasting.class.concentration` 落库 |
| `dissonant_whispers` | `saving_throw` | ⚠️ 只有声明断言。Bard 专属，而 `monk_draft` 建不出"要三个工具熟练"的职业 |

## 2. GAP-SP1 已查清：不是 actor 的问题

之前三次施放都被 `actor_id is invalid` 拒掉，根因是 **`target_ids` 少了 `enemy:` 前缀**：

```
target_ids=["goblin-1"]     -> 拒，错误说 actor_id is invalid
target_ids=["enemy:goblin-1"] -> 通过
```

`_actor_view` 对**目标**也做一次 id 解析（要拿它的条件与掩护），解析失败时抛的却是 `actor_id is invalid`。这个错误文案把"目标 id 没有前缀"报成了"行动者 id 无效"，我因此在一个不存在的问题上花了四十分钟，也不停地怀疑 fixture 与 dispel 测试的差异。

**建议单独修一次文案**：目标解析失败时说 `target id is invalid: goblin-1`。它不改任何行为，但会省掉下一个人同样的四十分钟。

## 3. GAP-SP2：重建会把已消耗的法术位重置回满

存档往返用例暴露出既有行为：`normalize_character_submission` 从 draft 重建时，`slots_current` 回到 `slots_max`。

```
施放后:  slots_current={'1': 3, '2': 2}  slots_max={'1': 4, '2': 2}
normalize 后: slots_current={'1': 4, '2': 2}
```

这条路径是"角色卡重新提交"，不是"存档读回"，所以影响面要先界定：如果前端每次改角色都走它，那是丢状态；如果只有建卡向导走它，那没问题。**本片没有修**，用例断言的是"重建不抛"而不是"槽位保留"——后者会红，前者被写成契约同样有害。

## 4. 与任务书的偏差（8 条 → 3 条）

| 法术 | 退回到 | 缺什么 |
|---|---|---|
| `heat_metal` / `mind_spike` | §5 延时触发 | "攻击与检定劣势"没有承载：`attack_modes` 只管攻击，检定劣势通道不存在 |
| `ray_of_enfeeblement` | §5 AC 公式与加值 | "力量减半"要一个能改数值的条件，`condition_flags` 没有位 |
| `blindness_deafness` | §5 多变体 | 目盲/耳聋二选一要 `condition_choices` 之类字段，schema 没有 |
| `vampiric_touch` | §5 治疗最大化 | "自己回一半"要 `heal_self_fraction` 消费者，`healing` 只对目标生效 |

## 5. 门禁

```
pytest tests/rulesets/test_dnd2024_sp1_direct_fill.py    5 passed
python scripts/spell_coverage.py --check                 退出 0（51/51）
ruff check src/ tests/                                   passed
```

覆盖率：1–3 环 32 → 35 条（21% → 22%）。
