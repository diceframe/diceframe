# S0-MM1 执行报告：怪物事实层

日期：2026-11-18
分支：`feat/dnd2024-mm1-monster-facts`（叠在 `feat/dnd2024-s0f39-target-selection@498cce90` 之上）
基线：`498cce90`

---

## 1. 交付内容

一条 MM stat block 的事实层现在能装进 `enemy` 契约，并且**未知字段不再静默消失**。

| 字段 | 形状 | 消费者 | 状态 |
|---|---|---|---|
| `size` | 闭集六档 | 无（F17/F32 的 Trip、Repelling） | **装而未用** |
| `speeds` | `{walk,fly,swim,climb,burrow,hover}`；旧标量 `speed` 归一化为 `{walk}` | 移动预算只读 walk | **部分装而未用** |
| `resist/immune/vulnerable_damage_types` | `DAMAGE_TYPES` 子集，三组互斥 | `_resist_damage`（免疫 0、易伤 ×2、抗性半，仍只结算一次） | 已接线 |
| `condition_immunities` | 条件码数组 | 意图批次收尾的免疫过滤（产出 `condition.blocked`） | 已接线 |
| `senses.blindsight` | 0–300 | `observer_senses` / `vision_codes` | 已接线 |
| `senses.truesight` | 0–300 | 进 `sense_declarations`，但条件注册表没有以 `truesight` 为 `negated_by` 的条目 | **装而未用** |
| `senses.darkvision/tremorsense` | 0–300 | 无（F30 光照 / MM4） | **装而未用** |
| `cr` | 闭集 `"0" … "30"`（含 1/8、1/4、1/2） | 投影 | 已投影 |
| `xp` | 0–355000，需与 `cr` 同时出现 | 投影 | 已投影 |
| `proficiency_bonus` | — | 无消费者 | **未实现** |

## 2. 关键发现：字段有**两个**丢弃点，不是一个

开工前的审阅只发现了校验层的静默丢弃。实际实现中发现第二处、也是更隐蔽的一处：

```
resolution.py::_combat_start_events 里敌人是显式白名单装配
"attacks": ..., "creature_type": ..., "counterspell_dc": ...   ← 到此为止
```

也就是说，即使 `validate_enemy_profiles` 放行、事件也带着新字段，**开局落账时仍被丢掉**，视图自然读不到。本次两处都补了：

1. `validation.validate_enemy_profiles`：未知字段拒绝（错误串列出全部未知键）；
2. `resolution._combat_start_events`：新字段逐个落账（`size/speeds/senses/三组防御/条件免疫/cr/xp`）。

这条值得记进 MM2 的入口检查：**新字段至少要过"校验 → 落账 → 视图"三关**，只补一关会出现"测试里能读到、实战里没有"。

## 3. 失败关闭边界

| 情形 | 行为 |
|---|---|
| 未知字段（`traits`/`sizes`/`cr_xp`…） | 拒绝，错误串列出全部未知键 |
| `size` 未知值（`big`、大写、空串、null） | 拒绝 |
| 旧数据缺 `size` | 装载成功，记为未知；`size_at_most()` 一律 `False`（不默认 medium） |
| `speeds` 全 0 / `hover` 无 `fly` / 未知档位 / 字符串 | 拒绝 |
| 三组防御类型重叠或非 `DAMAGE_TYPES` | 拒绝 |
| `cr` 非闭集（`2.5`、`31`、`CR 3`） | 拒绝 |
| 给了 `xp` 却没给 `cr` | 拒绝 |
| `senses` 未知档位 / 非整数范围 | 拒绝 |

## 4. 测试 1–12 逐项结果

文件：`tests/rulesets/test_dnd2024_monster_facts.py`，**13 passed**。

| # | 覆盖 | 结果 |
|---|---|---|
| 1 | `size` 闭集 + 六种非法值 | pass |
| 2 | 缺 `size` = 未知，`size_at_most` 六个边界 | pass |
| 3 | `speeds` 旧标量 / 结构 / 五种非法 | pass |
| 4 | 防御闭集与三组互斥 | pass |
| 5 | 抗性边界：免疫 0、易伤 ×2、抗性半、未列出不变 | pass |
| 6 | 条件免疫：`condition.applied` → `condition.blocked`，非免疫目标不改道 | pass |
| 7 | 感官：30 内命中、31 外为空、无 `senses` 时行为不变 | pass |
| 8 | `cr` 闭集（含整数归一化）、`xp` 依赖 `cr` | pass |
| 9 | 未知字段拒绝且错误串列出全部键名 | pass |
| 10 | 3 条 SRD 样本带 `size`/`cr` 装载 | pass |
| 11 | AI 草稿保留 `size`/`cr`；确认路径容忍噪声但权威校验仍拒坏 `cr` | pass |
| 12 | 旧敌人（无新字段）照常开战，视图给出未知/空 | pass |

## 5. 门禁

| 命令 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_monster_facts.py` | 13 passed |
| `pytest test_dnd2024_combat / combat_start_features / invisibility / channel_control / monk_combat` | 179 passed |
| `pytest tests/rulesets`（全量，`-n 6`） | **2492 passed / 0 failed**，823s（切片前 2479，+13 即本文件） |
| `ruff check src/rulesets/dnd2024 ...` | 干净 |
| `mypy` | 干净 |

## 6. 白名单与实际改动

| 文件 | 改动 |
|---|---|
| `combat/primitives.py` | `SIZE_CODES` + `size_at_most()` |
| `combat/validation.py` | 已知字段集、五个校验函数、`speeds` 归一化、fail closed |
| `combat/resolution.py` | 开局落账新字段；`_resist_damage` 的 statblock 三档；批次收尾免疫过滤 |
| `combat/view.py` | 敌人视图投影新字段 |
| `combat/check_modes.py` | `sense_declarations` 读 stat block 感官 |
| `director/temporary_encounter.py` | `_passthrough_statblock_facts`（AI 草稿保留 `size`/`cr`/`senses`） |
| `presets/encounters/srd_training_encounters.json` | 3 条样本补 `size`/`cr` |
| `tests/rulesets/test_dnd2024_monster_facts.py` | 新建 |

`reducer.py` **未改**：`condition.blocked` 没有分支即等于忽略（与 P8 的 `spell.countered` 同例）。

## 7. 偏差与未决

0. **一处返工**：`greymoor_encounters.json` 补 `size`/`cr` 的改动被回退——它被
   `test_dnd2024_m5_http::test_available_actions_migrates_known_unreleased_adventure_digest`
   以包摘要钉死，加了两个字段就让"已知未发布冒险"的迁移断言返回 422。该文件保持原样，
   旧数据路径由测试 12 覆盖。

1. **`xp` 改为内容字段，不做 CR→XP 表。** 理由：stat block 自带 `Challenge 3 (700 XP)`，XP 与 hp/AC 同类；SRD 不含 CR→XP 表（在 DMG），代码里硬编码一份就是用记忆造数据。未决：MM3 做遭遇预算时若需要"按 CR 反查 XP"，届时补表并逐行核对。
2. **`proficiency_bonus` 未实现**：没有消费者（怪物无技能检定、无施法 DC 投影）。等 MM4。
3. **`condition_immunities` 放在批次收尾一处**，而不是五个施加点各写一份。代价是审计事件只有 `condition.blocked` 一种，没有"哪个特性试图施加"的结构化字段（只有 `source_actor_id`）。
4. **`truesight` 装了但惰性**：它进了 `sense_declarations`，可条件注册表里没有以 `truesight` 为 `negated_by` 的条目，所以现在不改变任何判定。真要让 Witch Sight / 幻象生效，需要 MM4 的感官语义切片。
5. **Greymoor 冒险包的敌人未补 `size`/`cr`**：该包含一份被 `test_dnd2024_m5_http::test_available_actions_migrates_known_unreleased_adventure_digest` 钉死的摘要；改动内容会让"已知未发布冒险"的迁移断言 422。留待该迁移路径退役后再补。
6. **`size` 在 F17 之前不可用**：投影里有值，但没有任何规则读它。文档与 UI 不得据此宣称"支持尺寸相关能力"。
