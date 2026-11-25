# S0-F31 任务书：Use Magic Device、法术书与 Evocation Savant

| 项 | 值 |
|---|---|
| 切片编号 | S0-F31（独立新任务） |
| 验收载体 | `use_magic_device`、`ritual_adept`、`memorize_spell`、`evocation_savant` |
| 规则基准 | 官方 SRD 5.2.1；Thief p64、Wizard p78–79、Evoker p82 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 原基线 `integration/dnd2024-srd@35653f81`（2026-11-07）；**开工基线 `f2d86b1a`（2026-11-25）**，见 §0 修订记录 |
| 前置任务 | F1、F4、F5 |
| 状态 | 执行中（2026-11-25 起）；本文是执行契约，不表示功能已实现 |

## 0. 修订记录（2026-11-25）

原任务书写于 2026-11-07，此后 main 合入了装备在手状态（#785）、物品充能与同调、
S0-F25/F26/F30 等，本文 §3「定点代码事实」里的三条"基线缺失"已有两条**不再成立**。
本次修订只改事实与范围裁定，不改规则原文、不改验收编号：

| 原判断 | 2026-11-25 实测 | 结论 |
|---|---|---|
| 基线无同调容量函数，`attunement_slot_bonus` 不可用 | `character/attunement.py::attunement_capacity()` 已遍历特性读该参数（BASE 3 / MAX 10），只是全仓无一条声明使用 | **可自动化**，UMD 只需声明 |
| 基线无充能扣费/免收管线 | `items/charges.py` 有 `spend_charges` / `should_check_destroy` / `destroy_check_events`；扣费点两处：`combat/resolution.py`、`exploration/engine.py` | **可自动化**，1d6 免收插在扣费前 |
| 基线无 Spell Scroll 物品 | 目录有 `spell_scroll_cantrip` / `spell_scroll_level_1`，但字段只有 `name/desc/cost_gp/weight_lb`：无 `spell_ref`、无等级、无 `consumable`/`activation` | **仍不成立**：卷轴子能力保持 `not_automated` |

另外两条本次探测新增的事实，写在这里以免执行时误判：

- **Ritual 没有任何实现**：全仓 `ritual` 只出现在 `spells/selection.py` 的一句话注释里，
  探索与战斗都没有仪式入口。Ritual Adept 是新建路径，不是打开开关。
- **四条特性都没有定义对象**：`ritual_adept` / `memorize_spell` / `evocation_savant` /
  `use_magic_device` 在 `srd_class_features.json` 里只有 label 键与等级表引用，
  `classes.*.features` 里没有对应对象，因此 `feature_value()` 读不到任何 parameters。
  第 1 步必须是补声明。

## 1. 范围与硬边界

1. 实现 Thief 13 级三项物品能力、Wizard 仪式与短休换准备、Evoker 免费加入法术书；必须覆盖建卡/升级、探索、战斗、rest 和持久化。
2. F1/F4/F5 必须提供真实法术书、常备/准备法术、仪式与免费施法主干；不得复制一套平行的 Wizard 数据。
3. **同调容量与充能管线已具备（见 §0），卷轴身份仍缺失。** Use Magic Device 的同调与充能子能力按 §4.1 自动化；卷轴子能力按 §4.2 保持 `not_automated`，不显示可执行入口、不消耗任何资源。不得自造简化卷轴、把 Potion 改名、或只测不存在的 `item:spell_scroll`。Ritual Adept、Memorize Spell 与 Evocation Savant 须独立验收，不因卷轴缺失而跳过。
4. 不使用旧 SP、2014 规则或第三方资料；canonical id、物品身份、source_ref 和事件字段不本地化。

## 2. 官方英文关键原文与页码

Thief，SRD p64：

> **Level 13: Use Magic Device**
>
> **Attunement.** You can attune to up to four magic items at once.
>
> **Charges.** Whenever you use a magic item property that expends charges, roll 1d6. On a roll of 6, you use the property without expending the charges.
>
> **Scrolls.** You can use any Spell Scroll, using Intelligence as your spellcasting ability for the spell. If the spell is a cantrip or a level 1 spell, you can cast it reliably. If the scroll contains a higher-level spell, you must first succeed on an Intelligence (Arcana) check (DC 10 plus the spell’s level). On a successful check, you cast the spell from the scroll. On a failed check, the scroll disintegrates.

Wizard，SRD p78：

> **Level 1: Ritual Adept** — You can cast any spell as a Ritual if that spell has the Ritual tag and the spell is in your spellbook. You needn’t have the spell prepared, but you must read from the book to cast a spell in this way.

> **Copying a Spell into the Book.** … For each level of the spell, the transcription takes 2 hours and costs 50 GP.

Wizard，SRD p79：

> **Level 5: Memorize Spell** — Whenever you finish a Short Rest, you can study your spellbook and replace one of the level 1+ Wizard spells you have prepared for your Spellcasting feature with another level 1+ spell from the book.

Evoker，SRD p82：

> **Level 3: Evocation Savant** — Choose two Wizard spells from the Evocation school, each of which must be no higher than level 2, and add them to your spellbook for free. In addition, whenever you gain access to a new level of spell slots in this class, you can add one Wizard spell from the Evocation school to your spellbook for free. The chosen spell must be of a level for which you have spell slots.

## 3. 定点代码事实（2026-11-25 按 `f2d86b1a` 实测）

| 事实 | 文件与函数 |
|---|---|
| 同调容量已存在，且已读 `attunement_slot_bonus`；全仓无一条声明使用它 | `character/attunement.py::attunement_capacity`（BASE 3 / MAX 10） |
| 充能与销毁检查已存在；`dnd2024.item.charge_spent` 有两处发出点 | `items/charges.py::spend_charges` / `should_check_destroy` / `destroy_check_events`；`combat/resolution.py::_activate_item_events`、`exploration/engine.py` |
| 充能事件字段集被两处冻结，加字段必须同时改，否则 apply 拒绝 | `combat/reducer.py` 与 `exploration/engine.py` 各自的 `item.charge_spent` 字段表 |
| 卷轴只有描述性条目：无 `spell_ref`、无等级、无 `consumable`/`activation` | `equipment_catalogs/srd_adventuring_gear.json` 的 `spell_scroll_cantrip` / `spell_scroll_level_1` |
| 四条特性没有定义对象，只有 label 键与等级表引用 | `class_feature_catalogs/srd_class_features.json` 的 `classes.*.features` |
| Wizard 已有 `spellbook_refs`，准备法术必须是其子集 | `spells/selection.py::Dnd2024SpellSelection.validate`、`configure` |
| 升级时法术书最低数量为 `6+2*(level-1)`，preview/apply 保存选择 | `spells/selection.py::requirements`；`progression/advancement.py::preview_next_level`、`apply_next_level` |
| 短休已有显式 payload、资源和事件返回，并已有五个同形可选参数 | `resting/engine.py::complete_short_rest`（attunement / free_cast / recovery / fiendish_resilience / hunter_options） |
| 探索施法只读 cantrips/prepared；`ritual` 在代码里零实现（仅一句注释） | `exploration/engine.py::_spell_refs`、`_validate`；`spells/selection.py` |
| 基线 combat effects 不含本任务所有相关法术的自动效果 | `content/combat_catalogs/srd_combat_core.json` |

## 4. 精确实现契约

### 4.1 Use Magic Device：同调与充能

以下两条仅在前置已经提供真实同调/充能系统时开放自动化；当前祖先基线不满足，必须输出明确 `guided` 状态和负例，不得生成伪按钮或消费事件。

1. `use_magic_device` 以 `owner=subclass:thief`、13 级授予，声明 `attunement_slot_bonus=1`；唯一容量函数计算基础 3+1=4。12 级、非 Thief 或伪造 owner 不增加。
2. 短休同调交换继续原子化；已有四个时不得新增第五件，解除后再绑定合法。死亡解绑和存档行为不回退。
3. 每次实际使用“会消耗充能的魔法物品属性”时，在扣费前掷一次 1d6；6 则该属性正常生效且本次充能支出为 0，1–5 正常扣除声明费用。
4. 每次属性使用只掷一次，不按充能点数多掷；无充能、不耗充能、非法激活不掷。6 导致未耗充能时不得触发“最后充能耗尽后的销毁检查”。事件保存 d6、是否免收、原费用、实际费用和 item_ref。
5. 该能力只改变充能支付，不绕过动作、目标、同调、持有数量或其他物品前提。

### 4.2 Use Magic Device：卷轴

仅真实 Spell Scroll 及其物品身份、消耗和销毁规则均存在时才开放自动化；否则该子能力为 `not_automated`，不显示可执行入口且不消耗任何资源。

1. 仅真实 Spell Scroll 可进入此路径；从物品读取所含 spell_ref 和法术等级，客户端不得提交法术、等级、DC、Int 修正或结果。
2. 所有卷轴法术以 Intelligence 为施法属性，重算攻击/DC/相关效果；不沿用角色原职业属性。卷轴本身的合法使用门槛以此特性为准，但仍须真实持有并满足法术目标/效果规则。
3. 戏法和 1 环不做 Arcana 检查，可靠施放；2+ 环先进行 Intelligence (Arcana) check，DC=10+法术等级，使用角色真实 Arcana 加值。
4. 成功才进入法术结算；失败不施放且卷轴 disintegrates。正常卷轴消耗按真实物品条目执行，失败销毁与消耗不得双重扣同一物品。所有路径有骰迹、物品事件和 reducer 重放。
5. 未自动化法术不得产生“成功但无效果”；在检定前以 `not_automated` 拒绝且不消耗卷轴。真实 Spell Scroll 不存在时按 §1 停止。

### 4.3 Ritual Adept 与 Memorize Spell

1. Wizard 1 级可把 spellbook 中带 Ritual tag 的法术作为仪式施放，即使未 prepared；必须实际读取法术书，仪式时间与组件不豁免，不消耗槽位。
2. 非 Wizard、非 Ritual、不在 spellbook 或无法读取书时不可走此入口；仪式不能伪装成战斗中的一个普通 Action 来跳过时间。
3. 探索 available/validate/resolve 必须承认合法未准备仪式法术；普通准备施法列表不被无意扩大为所有 spellbook 法术。
4. Wizard 5 级每次完成 Short Rest 可选择将恰好一个已准备的 1+ 环 Wizard 法术换成书中另一个 1+ 环法术；可不换，但不可一次换多个、换成戏法、非 Wizard 或书外法术。
5. 更换在 rest 完成后原子落账，准备总数不变；非法选择不污染已完成休息。Long Rest 原规则、升级历史和中英文投影一致。

### 4.4 Evocation Savant

1. Evoker 3 级时选择恰好两个 Wizard、Evocation school、等级 1–2 的法术，免费加入 spellbook；0 环和 3+ 环不合格。
2. 此后每当 Wizard 获得新一环阶法术位时，可额外选择一个该环阶或更低、当前拥有槽位的 Wizard Evocation 法术免费加入；没有新开环阶的等级不额外授予。
3. 这些免费加入独立于每级正常两个法术和最低法术书数量，不占抄录时间/GP，不自动 prepared；重复拥有、跨学派/列表和超等级均拒绝。
4. preview 显示正常与免费授予的来源和数量，apply/history 重建得到同一 spellbook；普通抄录仍为每级 2 小时+50 GP，本特性只免本段明确列出的加入成本。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；`features/models.py`、`features/resolver.py`；`character/attunement.py`、`items/charges.py`（同调容量与充能免收的落点）；`spells/selection.py` 与 F1/F4/F5 已建立的同等主干；`progression/advancement.py`；`resting/engine.py`；`combat/view.py`、`validation.py`、`resolution.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；新建 `tests/rulesets/test_dnd2024_spellbook_items.py`；本任务书仅追加执行报告。若真实物品系统由前置提供却位于其他路径，Use Magic Device 仍停止自动化并报告，不在本切片扩大白名单。

不为通过测试新建虚假 Spell Scroll、物品 fixture 目录或另一套物品系统。不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. Thief 12/13 级容量为 3/4；非 Thief 和伪造 owner 不加，短休交换、第五件拒绝、死亡解绑与读档一致。
2. 真实耗充能属性每次先掷 1d6；6 免收且效果正常，1–5 扣原价；不耗充能路径零 RNG。
3. 多充能费用只掷一次；免收不触发空充能销毁，正常耗尽仍走既有销毁检查，apply/replay 不重复扣费。
4. 真实 Spell Scroll 的戏法/1 环可靠施放，以 Int 计算攻击和 DC；不掷 Arcana，目标与效果真实。
5. 2+ 环卷轴进行 Int(Arcana) 且 DC=10+等级；成功施放、失败无效果并 disintegrates，物品数量与骰迹正确。
6. 客户端伪造 spell_ref/等级/DC/结果、非真实卷轴、未持有、未自动化效果均 fail closed；缺真实同调、充能或 Spell Scroll 系统时分别显示 `guided`/`not_automated`，无假按钮、无扣费、无空事件，且不得宣称整体完成。
7. Wizard 可对书中未准备 Ritual 法术仪式施放，不耗槽且阅读/时间/组件保留；非 Ritual、书外和非 Wizard 拒绝。
8. 探索投影区分“已准备普通施法”和“书中可仪式”，战斗不把仪式压缩成普通 Action。
9. Wizard 5 级短休可换一个 1+ 环准备法术；数量不变、新书内法术立即可用，旧法术按规则移除。
10. 一次短休换多个、戏法、书外、非 Wizard、非法 ref 均拒绝；可选不换，历史/读档/中英文一致。
11. Evoker 3 级两个免费 Wizard Evocation 1–2 环法术加入书中；跨学派、跨列表、0/3 环和重复项拒绝。
12. 新开每个槽位环阶时恰好增加一次免费选择，法术等级不超过当前槽位；未开新环阶不增加。
13. 免费加入不占正常升级两个、不自动 prepared、不扣 GP/时间；普通抄录 2 小时+50 GP/级不变。
14. 四特性在 builder/advance/history、战斗/探索/rest、save/load 和 reducer 重放一致；Wizard/Evoker 的 spell、rest 既有回归通过，Use Magic Device 的缺失系统边界有独立负例。

## 7. 非目标与停止条件

非目标：其他 Thief/Wizard/Evoker 特性、通用卷轴内容制作、开放愿望、完整物品自动化、多职业、前端改版。

停止条件：F1/F4/F5 未合入；仪式需要绕过时间；Evoker 免费选择与正常升级数量无法对账；需要白名单外文件或自造假物品。**同调与充能已具备，不再构成停止理由**；仅当真实 Spell Scroll 或其物品身份仍缺失时，停止卷轴子能力并保持 `not_automated`，其余三项照常交付。卷轴失败销毁与正常消耗无法原子区分时同样不得开放卷轴入口。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_spellbook_items.py -q
python -m pytest tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_spells.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_spellbook_items.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 真实 Spell Scroll 与物品身份核验：
- 同调 4、d6 充能、Int 卷轴/失败崩解证据：
- 仪式、短休换准备与 Evoker 免费加入对账：
- 测试 1–14 逐项结果、RNG、apply/replay 和 save/load：
- 白名单、验收输出、偏差与未决事项：
