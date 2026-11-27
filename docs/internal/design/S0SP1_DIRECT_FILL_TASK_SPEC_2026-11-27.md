# S0-SP1 任务书：1–2 环直填法术

| 项 | 值 |
|---|---|
| 切片编号 | S0-SP1（`S0SP_PLAN.md` 的 S1） |
| 验收载体 | 8 条 1–2 环法术真的能在战斗里放出来并结算 |
| 规则基准 | 官方 SRD 5.2.1；页码见 §2，逐条来自 `srd_spells.json` 的 `source_ref` |
| 前置 | `#619`（P1–P9 原语已合）、F14（命中附加效果）、`scripts/spell_coverage.py`（`#894`） |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与完成定义

1. 本切片只做**现有 14 个 `mode` 已经能表达**的法术，不新增 `mode`、不改 `spell_effects` 的字段集合。
2. 范围是 `S0SP_PLAN.md` 的 S1，但**只有其中 8 条真正可直填**；§5 列出了 16 条被排除的法术与原因，它们不是"没做完"，是"这一片的机制装不下"。
3. 验收单位是**法术条数**，不是代码行数：每一条都要能施放、有事件、有数值断言、存档往返不丢（§6）。
4. 不扩展 3 环以上、不动几何与区域、不碰 `condition` 目录之外的战斗状态。

## 2. 逐条清单（A 组：可直填）

`mode` 用现有 14 个之一；"新增字段"一栏只允许使用 `spell_effects` 已有的键
（`damage` / `damage_type` / `save` / `half_on_success` / `condition` /
`condition_duration` / `concentration` / `target_count` / `target_scope` /
`upcast_damage` / `range`）。

| # | 法术 | 环 | 页 | mode | 效果要点 | 依赖 |
|---|---|---|---|---|---|---|
| 1 | `dissonant_whispers` | 1 | p124 | `saving_throw` | 感知豁免，失败 3d6 心灵；目标须用反应尽量远离 | 反应内移动**不做**，只结算伤害 |
| 2 | `heat_metal` | 2 | p139 | `saving_throw` | 体质豁免，失败 2d8 火焰；失败者在其回合开始前攻击/检定有劣势 | 劣势挂 `condition`，见 §4 |
| 3 | `mind_spike` | 2 | p149 | `saving_throw` | 智力豁免，失败 3d8 心灵；失败者智力检定有劣势，持续 | 同 #2 |
| 4 | `ray_of_enfeeblement` | 2 | p157 | `spell_attack` | 命中 2d6 暗蚀；目标力量检定/攻击用力量时减半（取整向下） | 减半挂 `condition`，见 §4 |
| 5 | `ray_of_sickness` | 1 | p158 | `spell_attack` | 命中 2d8 毒素；目标中毒 | `condition: poisoned`（目录已有） |
| 6 | `vampiric_touch` | 3 | p171 | `spell_attack` | 命中 3d6 暗蚀，自己回等量一半 | `healing` 通道已有，见 §4 |
| 7 | `blindness_deafness` | 2 | p113 | `saving_throw` | 体质豁免，失败目盲或耳聋（施法者选一） | `condition: blinded` / `deafened`（已有） |
| 8 | `phantasmal_force` | 2 | p151 | `saving_throw` | 智力豁免，失败 1d6 心灵，且每回合可再豁免 | 持续伤害在本片**只结算首轮**，见 §4 |

## 3. 为什么是这 8 条

其余 16 条在"1–2 环直填桶"里，但**直填的意思是数据，不是勉强**。判断标准是一条：

> 这条法术的结算是否能写成「一次掷骰 + 一个 damage + 一个 condition」？

- 能写的进 §2；
- 需要"在别人的回合里发生第二件事"（延时伤害、反应触发、每回合重复）的，进 §5 的 B 组；
- 需要"改一个既有数值的公式"（AC、武器加值、属性减半之后的**判定方式**）的，§4 里说明它落在哪。

`ray_of_enfeeblement` 是最贴近边界的一条：伤害与命中都是 `spell_attack`，但它写的是"用力量做的事减半"。本片把它落成 `condition`（`enfeebled`），减半的**计算**由既有条件消费者负责；若执行时发现条件目录里没有能承载"减半"的语义，则**退回 §5**，不要为它临时造一个只服务一条法术的原语。

## 4. 三个允许的边界动作

三件小事可能要在本片做掉，都不是新 `mode`：

1. **`condition` 目录补齐**：`heat_metal`（攻击/检定劣势）、`mind_spike`（智力检定劣势）、`ray_of_enfeeblement`（力量减半）各需要一个条件 id。它们如果只是"带 disadvantage 的 debuff"，复用 `check_modes` 的 `declared` 通道；如果要影响数值（减半），先看 `condition_flags` 有没有承载位，没有就退回 §5。
2. **自身治疗**：`vampiric_touch` 的"回一半"。`healing` mode 已有，运行时对**施法者**结算即可；如果现有 `healing` 只对目标生效，则在本片补一个 `heal_self_fraction` 消费者，并在任务书的执行报告里记明。
3. **持续伤害的首轮结算**：`phantasmal_force` 的每回合重复不在本片。本片只结算施放当次，并在事件里标 `ongoing: true` 之类的**不承诺字段**——不要造一个没人消费的 `repeat` 字段（那正是 §10.3 批评过的"写了没人读"）。

## 5. 明确排除（B 组，16 条）

不是遗漏。每条都写了它属于哪一类，避免下一个切片重新论证。

| 类别 | 法术 | 缺什么 | 归属 |
|---|---|---|---|
| 命中附加 | `searing_smite` p160、`shining_smite` p162、`divine_favor` p125、`ensnaring_strike` p128 | 附在"下一次命中"上的额外伤害与条件 | **先确认 F14 的 `hit_rider` 能否承载法术**；能则并入 SP1b，不能则进 SP4 |
| AC 公式与加值 | `mage_armor` p145、`barkskin` p112、`magic_weapon` p146、`warding_bond` p173 | 无甲 AC 公式替换、AC 加值、武器命中和伤害加值 | SP3（与 P12 的 `character` 字段覆盖同族） |
| 延时/持续 | `acid_arrow` p107（次回合 2d4）、`phantasmal_force` 的每回合重复 | 回合开始的重复结算 | SP4（与 P13 的回合接管同族） |
| 反应触发 | `hellish_rebuke` p140 | 被命中后的反应伤害 | SP2（counterspell 同一条反应管线） |
| 给动作/召唤 | `dragon_s_breath` p126、`flame_blade` p132 | 给目标一个新攻击或一把召唤武器 | SP5（与 P11 召唤同族） |
| 分身/门控 | `mirror_image` p150、`sanctuary` p159 | 攻击失手判定、攻击目标门控 | SP5 |
| 多变体 | `bestow_curse` p112 | 六个诅咒各不同 | SP5 |
| 治疗最大化 | `beacon_of_hope` p112 | 治疗骰取满 | SP3 |

## 6. 验收

每条法术四条，缺一不算点亮：

1. **施放路径走得通**：战斗内 `cast_spell` 结算成功；`phantasmal_force` 与 `blindness_deafness` 另需一条"重复施放不叠加"的用例（condition 已存在时的行为必须明确）。
2. **事件流有内容**：`spell.cast` → `damage.applied` / `condition.applied`，且 `condition.applied` 带 `source_ref`（现有 48 条的形状）。
3. **数值断言**：至少一条用例断言 `sum(rolls)` 或 `total` 的**具体值**，不是"事件里有个 damage 字段"。豁免类要覆盖成功/失败两条分支。
4. **存档往返**：施放后 `normalize_character_submission` 不抛，`spellcasting.class` 的已用槽位与 concentration 状态保留。

另外两个全局门槛：

- `python scripts/spell_coverage.py` 的 1–2 环覆盖率必须上涨；`--check` 的基线随之上调（`BASELINE_EFFECTS` 从 48 起加 8）。
- `pytest tests/rulesets tests/architecture` 全绿；`ruff` / `mypy` 干净。

## 7. 停止条件

遇到以下任一情况**停下来记 GAP**，不要在本片硬做：

- 某条法术要实现的效果需要新的 `mode`；
- 某个条件需要"改变一个既有数值的计算方式"而 `condition_flags` 没有承载位；
- 现有 `spell_effects` 的字段语义与 SRD 原文冲突（例如 `half_on_success` 只支持伤害、而这条法术是别的量）；
- 需要动 `combat/` 的结算主路径（`attack_resolution` / `saving_throws`）才能让一条法术成立。

## 8. 交付物

- 8 条 `spell_effects` 条目；
- 对应的端到端用例（含成功/失败分支与重复施放）；
- 必要的 `condition` 目录补充（§4.1）；
- 执行报告：逐条记"实际用了哪个 mode、有没有偏离、§4 的三个边界动作各落到哪"；
- `SLICE_STATUS.md` 增 SP1 行，证据写 PR 号；
- `scripts/spell_coverage.py` 的 `BASELINE_EFFECTS` 更新。
