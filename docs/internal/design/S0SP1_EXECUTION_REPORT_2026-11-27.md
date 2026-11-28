# S0-SP1 执行报告：1–2 环直填法术

| 项 | 值 |
|---|---|
| 状态 | **完成**（2 条落地，6 条退回） |
| 门禁 | 4 条用例通过；`spell_coverage --check` 通过（50）；ruff/mypy 干净 |
| 结论 | 2 条法术，1 条端到端、1 条只有声明；其余 6 条按 §5/§7 退回 |

## 1. 实际新增

| 法术 | mode | 验收 |
|---|---|---|
| `ray_of_sickness` | `spell_attack` | ✅ 命中 2d8 毒素（断言 `amount == 10`）+ `poisoned` 到「你的下回合结束」；d20=1 时不产生伤害 |
| `dissonant_whispers` | `saving_throw` | ⚠️ 只有声明用例。Bard 专属，共享建卡助手造不出「要三个工具熟练」的职业（GAP-SP3） |

## 2. 三条规则是我写错的（审查抓出）

任务书 §2 的表格是**开工前凭记忆写的**，执行时照着错的写，三条全错。审查逐条对 `srd_spell_descriptions.json` 抓出：

| 法术 | 我写的 | 原文 |
|---|---|---|
| `ray_of_sickness` | 中毒 `1_minute`、无升环 | 中毒到「你的下回合结束」；升环 **+1d8** |
| `dissonant_whispers` | 成功无伤害 | 成功**半伤**（"half as much damage only"） |
| `phantasmal_force` | 智力豁免 + `1d6` 心灵 | **没有初始伤害**；只有「你的每回合 2d8」 |

前两条已按原文修正（`condition_duration: "caster_next_turn_end"`、`upcast_damage: "1d8"`、`half_on_success: true`）。第三条无法在这一片表达——`phantasmal_force` 根本没有"一次掷骰 + 一个 damage"的形状，它需要每回合重复结算，因此退回 §5。

**教训**：`templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spell_descriptions.json` 里有 339 条法术的完整原文。下次写内容前先读它，不要先用记忆写、等审查来抓。

## 3. 退回清单（6 条）

| 法术 | 缺什么 |
|---|---|
| `phantasmal_force` | 每回合重复伤害（原文无初始伤害） |
| `heat_metal` / `mind_spike` | 检定劣势通道：`attack_modes` 只管攻击，`condition_check_bonus_rolls` 只管加骰 |
| `ray_of_enfeeblement` | 能改数值的条件（"力量做的事减半"），`condition_flags` 没有承载位 |
| `blindness_deafness` | 二选一条件（目盲/耳聋），schema 没有 `condition_choices` |
| `vampiric_touch` | 自身回血的消费者（`healing` 只对目标生效） |

## 4. 三个 GAP

**GAP-SP1 — 错误文案把目标报成行动者。** `target_ids` 缺 `enemy:` 前缀时，`_actor_view` 解析失败却抛 `actor_id is invalid`，让人去查行动者。建议改成 `target id is invalid: <id>`，不改行为。

**GAP-SP2 — 重建重置法术位。** `normalize_character_submission` 后 `slots_current` 回到 `slots_max`（3/4 → 4/4）。要先界定这条路径是"角色卡重新提交"还是"存档读回"；若前端每次改卡都走它，那是丢状态。本片没修，用例只断言"重建不抛"。

**GAP-SP3 — 共享建卡助手造不出 Bard。** `monk_draft` 换 class 后，Bard 要三个工具熟练、三个技能，且不能与背景重复。`dissonant_whispers` 因此只有声明用例。需要一份 Bard 版建卡助手（不只服务这一条法术）。

## 5. 门禁

```
pytest tests/rulesets/test_dnd2024_sp1_direct_fill.py    4 passed
python scripts/spell_coverage.py --check                 退出 0（50/50）
ruff check src/ tests/                                   passed
```

覆盖率：1–3 环 32 → 34 条（21% → 22%）。
