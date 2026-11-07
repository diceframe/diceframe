# S0-F29 任务书：Divine Intervention、Greater Divine Intervention 与 Words of Creation

| 项 | 值 |
|---|---|
| 切片编号 | S0-F29（独立新任务） |
| 验收载体 | Cleric 10/20 级与 Bard 20 级三个特性 |
| 规则基准 | 官方 SRD 5.2.1；Divine Intervention 正文实际在 p38，Words of Creation 正文实际在 p33 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | F4、F5、D2 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与页码核对

1. 实现 `divine_intervention`、`greater_divine_intervention`、`words_of_creation` 的真实授予、施法限制、资源和第二目标，不扩展到其他 Bard/Cleric 特性。
2. 指定阅读段 Bard p34–35、Cleric p39–40 含法术列表/子职内容；目标正文由同一官方 PDF 实际定位于 Bard p33、Cleric p38。本任务以正文页码为准，并保留此核对记录。
3. F4/F5 必须提供常备法术和特性免费施法的统一主干；D2 的资源/事件纪律自动适用。不得为三个特性各造一套互不相通的免费施法状态。
4. 不使用旧 SP、2014 规则或第三方资料；canonical ref、事件与 source_ref 不本地化。

## 2. 官方英文关键原文与页码

Cleric，SRD p38：

> **Level 10: Divine Intervention**
>
> As a Magic action, choose any Cleric spell of level 5 or lower that doesn’t require a Reaction to cast. As part of the same action, you cast that spell without expending a spell slot or needing Material components. You can’t use this feature again until you finish a Long Rest.

> **Level 20: Greater Divine Intervention**
>
> When you use your Divine Intervention feature, you can choose Wish when you select a spell. If you do so, you can’t use Divine Intervention again until you finish 2d4 Long Rests.

Bard，SRD p33：

> **Level 20: Words of Creation**
>
> You therefore always have the Power Word Heal and Power Word Kill spells prepared. When you cast either spell, you can target a second creature with it if that creature is within 10 feet of the first target.

资格还以 Cleric 法术列表（p38–40）、Bard 法术列表（p33–35）及 Wish/Power Word 法术各自条目为准；不得因特性免材料而免除动作、目标、射程、专注或其他未明确豁免的规则。

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 基线 class feature catalog 没有三个目标特性 | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` |
| Bard/Cleric 的 progression 表分别列有对应职业等级 | `content/progression_catalogs/srd_classes.json`；Resolver `features_for` |
| 法术目录含 Wish、Power Word Heal/Kill，但基线 combat spell effects 对三者均无条目 | `content/spell_catalogs/srd_spells.json`、`content/combat_catalogs/srd_combat_core.json` |
| 施法校验限制已准备/已知、反应模式、法术位、动作、目标和射程 | `src/rulesets/dnd2024/combat/validation.py::_validate_spell` |
| 多目标上限由 `target_count/upcast_target_count` 计算，射程逐目标检查 | 同上 |
| 法术结算和 `dnd2024.spell.cast` 在统一入口生成 | `src/rulesets/dnd2024/combat/resolution.py::_spell_events` |
| 基线免费施法主要绑定 Origin feat grant，不应直接冒充职业特性方案 | `src/rulesets/dnd2024/spells/feat_spells.py`、`combat/triggered_spells.py::free_cast_resource` |
| 长休当前恢复职业资源/法术位，未提供“2d4 次长休”倒计时 | `src/rulesets/dnd2024/resting/engine.py::complete_long_rest` |
| 探索施法另有入口，不能只改战斗而让探索绕过限制 | `src/rulesets/dnd2024/exploration/engine.py::_spell_refs`、`_validate` |

## 4. 精确实现契约

### 4.1 声明与统一免费施法

1. `divine_intervention` 的 owner 为 `class:cleric`、10 级；`greater_divine_intervention` 为同 owner、20 级；`words_of_creation` 为 `class:bard`、20 级。中英文名称、summary 和 p38/p33 source_ref 同步。
2. Divine Intervention 是一个 Magic action 能力，实际选择并施放合法 Cleric 法术；整个行为只消耗该次 Magic action，不额外消耗法术动作的 action，不消耗槽位，不需要 Material components。
3. 合法基础池为 `class_refs` 含 `class:cleric`、等级 0–5、casting time 不要求 Reaction 的法术。不能按名称硬编码允许列表，也不能选择其他列表法术、6+ 环法术或 Reaction 法术。
4. 免费施法仍须满足法术等级、目标、射程、动作经济和真实效果自动化；免材料只移除 M 需求，不移除 V/S，也不让未自动化法术产生空效果。
5. 基础 Divine Intervention 使用一次后进入长休锁定。完成一次 Long Rest 后恢复；Short Rest、战斗结束、升级、读档和中英切换不恢复。

### 4.2 Greater 与 2d4 长休

1. 20 级后使用同一 Divine Intervention 时可额外选择 Wish；Wish 不必属于 Cleric 列表，仍不耗槽、不需材料。19 级或伪造特性不得选择。
2. 选择 Wish 且施法实际完成时，服务端立即掷一次 `2d4` 并把结果作为还需完成的 Long Rest 总数；记录骰迹、初始计数和来源。普通 0–5 环 Cleric 法术仍只需一次长休。
3. 每次完成 Long Rest 将剩余计数减一，只有减到 0 后恢复 Divine Intervention。一次长休只能减一次，Short Rest 不减，读档/重放不重复减少或重掷。
4. 若施法在验证阶段失败或被合法反制而没有完成，不消耗特性、不掷 2d4；已经完成的 Wish 不因后续叙事解释而退款。
5. 本切片不实现 Wish 的开放式叙事愿望。只有官方条目的确定性效果由前置提供时才可开放；否则停止，不得把“选择 Wish”当作已完成。

### 4.3 Words of Creation

1. Bard 20 级起 Power Word Heal 和 Power Word Kill 始终 prepared，不计入普通 prepared 数量；19 级、非 Bard 或丢失所有权时不得由残留客户端输入保留。
2. 两法术仍按正常规则消耗法术位，除非另一真实特性明确免费；Words of Creation 本身不提供免费施法、不免材料、不改变射程或伤害/治疗数值。
3. 施法者可选择第二个不同目标，第二目标与第一目标的距离必须不超过 10 尺；两者仍各自满足法术目标资格。第二目标是可选的，不是强制，也不能重复指定同一生物。
4. available_intents 必须暴露第二目标候选及 10 尺限制，validation 与 resolution 使用同一位置事实；不能只对事件追加目标而跳过验证。
5. 两个目标共享一次施法和一次槽位支付，效果分别结算、事件分别可审计；重放不重复治疗/伤害。若两法术真实效果未自动化，停止对应开放，不得只交付常备列表。

### 4.4 状态与事件

1. 使用状态以 `(owner, feature_id, scope)` 为身份；普通长休锁定和 `remaining_long_rests` 必须能区分，不能用一个布尔值丢失 2d4 信息。
2. 所有扣用、恢复、倒计时和 2d4 骰迹均产生稳定事件；reducer 机械应用，不在 reducer 重新计算法术资格或 RNG。
3. 战斗与探索入口共享 canonical 准备法术、使用计数和位置/目标规则；公开投影不得暴露无权限的角色内部信息。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；必要的 spell/combat/rest catalog 本切片声明；`features/models.py`、`features/resolver.py`、`spells/selection.py`、`spells/feat_spells.py` 或 F4/F5 已建立的同等模块；`combat/view.py`、`validation.py`、`resolution.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；`resting/engine.py`；新建 `tests/rulesets/test_dnd2024_divine_words.py`；本任务书仅追加执行报告。

如需修改其他文件，停止。不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. Cleric 9/10/19/20 级和 Bard 19/20 级授予边界正确；其他 owner 和伪造 ref 无效，en/zh-CN 与 source_ref 正确。
2. Divine Intervention 可选择合法 Cleric 0–5 环非 Reaction 法术；6 环、非 Cleric、Reaction、未自动化和客户端伪造法术均拒绝。
3. 合法使用只消耗一个 Magic action，不耗槽、不需 M，目标/射程/伤害/治疗和专注仍按法术条目执行。
4. 第二次使用在 Long Rest 前拒绝；Short Rest、战斗结束、save/load 不恢复；一次 Long Rest 后恢复。
5. 20 级可选择 Wish，19 级不可；Wish 不耗槽/不需 M，且真实法术效果经过统一结算而非空事件。
6. Wish 完成后只掷一次 2d4；分别覆盖 2、3、4、8 等 SequenceRng 结果，剩余长休数与骰迹准确。
7. 倒计时期间普通 Divine Intervention 也不可用；每次 Long Rest 恰好减一，Short Rest/读档/重放不减，归零后才恢复。
8. 验证失败或施法未完成时不扣使用、不掷 2d4；普通选择和 Wish 的事件顺序、apply/replay 一致。
9. Words 两法术在 20 级始终 prepared、不占普通准备数；19 级和非法 owner 不授予，历史重建后一致。
10. 两法术正常施法仍支付真实槽位；Words 不产生虚假免费次数，也不改变法术原有数值。
11. Power Word Heal/Kill 各覆盖单目标与合法第二目标；第二目标距首目标 10 尺内成功，超过 10 尺、重复目标、非法目标均拒绝。
12. 第二目标只支付一次槽位/动作，两目标分别产生正确效果；available_intents、validation、resolution 和 feed 目标集合一致。
13. 战斗与探索中常备/使用状态一致；存档、加载、升级历史、reducer 重放不重复扣用、恢复或结算。
14. 既有 Cleric/Bard 施法、Counterspell、rest、装备、战斗和探索回归通过，无其他职业获得相同能力。

## 7. 非目标与停止条件

非目标：其他 Divine/ Bard 能力、任意愿望叙事求解、Cleric 全法术自动化、多职业、前端改版、通用资源重构。

停止条件：F4/F5 未合入；Wish 或任一 Power Word 没有真实自动化效果；无法表示 2d4 长休倒计时；第二目标缺少权威位置；需要新造与前置平行的免费施法系统；必须改白名单外文件；测试 5、6、11 只能靠假事件通过。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_divine_words.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_divine_words.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 三特性授予、法术资格和 en/zh-CN：
- 免费施法、长休锁定与 2d4 倒计时证据：
- 两 Power Word 常备及第二目标 10 尺证据：
- 测试 1–14 逐项结果、RNG、apply/replay 和 save/load：
- 白名单、验收命令、偏差与未决事项：
