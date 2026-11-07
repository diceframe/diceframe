# S0-F4 任务书：常驻准备、跨列表来源与长休法术授予

| 项 | 值 |
|---|---|
| 切片编号 | S0-F4（独立新任务，不覆盖旧 SP） |
| 验收载体 | `life_domain_spells`、`oath_of_devotion_spells`、`draconic_spells`、`fiend_spells`、`circle_of_the_land_spells`、`magical_discoveries`、`magical_secrets` |
| 通用能力 | 来源化常驻准备、容量豁免、跨列表选择、地形长休切换、自动化状态 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Bard p32–35；Life p40；Land p46–47；Oath p56；Draconic p69–70（表在 p70）；Fiend p76 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-B 已合入，支持 owner-scoped 多选、升级替换与来源追踪 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得把法术名写入普通 prepared 列表后就算完成。
2. 五组表必须逐级、逐法术准确；Land 四地形都实现长休选择与旧来源撤销。
3. “常驻准备”和 Magical Secrets 的“普通准备但可跨列表”严格区分；后者不获得额外容量。
4. 法术目录元数据只证明法术存在，不证明效果可结算；无实际 effect/专用处理器的条目标记 `not_automated`。
5. 本切片不实现法术效果，不用空事件、直接扣槽或仅展示名称冒充施法成功。

## 2. 官方英文关键原文与语义边界

SRD p32 的通用容量规则：

> If another Bard feature gives you spells that you always have prepared, those spells don’t count against the number of spells you can prepare with this feature, but those spells otherwise count as Bard spells for you.

SRD p40、p56、p70、p76 的五组授予均使用以下语义：

> When you reach a [class] level specified in the ... Spells table, you thereafter always have the listed spells prepared.

SRD p46：

> Whenever you finish a Long Rest, choose one type of land: arid, polar, temperate, or tropical. Consult the table below that corresponds to the chosen type; you have the spells listed for your Druid level and lower prepared.

SRD p35、p33：

> **Magical Discoveries.** You learn two spells of your choice. These spells can come from the Cleric, Druid, or Wizard spell list or any combination thereof ... A spell you choose must be a cantrip or a spell for which you have spell slots.

> You always have the chosen spells prepared, and whenever you gain a Bard level, you can replace one of the spells with another spell that meets these requirements.

> **Magical Secrets.** Whenever you reach a Bard level ... and the Prepared Spells number ... increases, you can choose any of your new prepared spells from the Bard, Cleric, Druid, and Wizard spell lists, and the chosen spells count as Bard spells for you ... whenever you replace a spell prepared for this class, you can replace it with a spell from those lists.

语义边界：Land 每次长休选择当前地形，低等级表累积；切换不是叠加四套。Magical Discoveries 恰好两项常驻选择；Magical Secrets 只扩大普通准备/替换的列表来源，不增加 Prepared Spells 数字。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 普通 cantrip、prepared、spellbook 与容量由统一选择器验证/配置 | `spells/selection.py::requirements`、`options`、`validate`、`configure` |
| `prepared_capacity` 只描述普通职业准备容量，常驻授予必须带来源并绕开该计数 | `spells/selection.py` 的容量计算与验证段 |
| 升级会更新 spellcasting、槽位和准备列表 | `progression/advancement.py::_update_spellcasting` |
| 战斗可用法术只投影实际可自动化条目，目录存在不等于可施放 | `combat/view.py::_available_spells` |
| 无自动化效果的法术在战斗校验中 fail closed | `combat/validation.py::_validate_spell` |
| 长休有统一入口，可接入 Land 选项事务而非另造休息流程 | `resting/engine.py::complete_long_rest` |
| 法术目录中多条目标法术有名称/等级/class_refs 但 `effect` 为空 | `content/spell_catalogs/srd_spells.json`；消费点仍为上述 view/validation |

## 4. 精确表数据

以下名称均对应官方法术目录 canonical spell id；不得因翻译或同名而重复创建法术。

| 来源 | 精确累积表 |
|---|---|
| Life（Cleric） | 3：Aid, Bless, Cure Wounds, Lesser Restoration；5：Mass Healing Word, Revivify；7：Aura of Life, Death Ward；9：Greater Restoration, Mass Cure Wounds |
| Oath of Devotion（Paladin） | 3：Protection from Evil and Good, Shield of Faith；5：Aid, Zone of Truth；9：Beacon of Hope, Dispel Magic；13：Freedom of Movement, Guardian of Faith；17：Commune, Flame Strike |
| Draconic（Sorcerer，p70） | 3：Alter Self, Chromatic Orb, Command, Dragon’s Breath；5：Fear, Fly；7：Arcane Eye, Charm Monster；9：Legend Lore, Summon Dragon |
| Fiend（Warlock） | 3：Burning Hands, Command, Scorching Ray, Suggestion；5：Fireball, Stinking Cloud；7：Fire Shield, Wall of Fire；9：Geas, Insect Plague |
| Land—arid | 3：Blur, Burning Hands, Fire Bolt；5：Fireball；7：Blight；9：Wall of Stone |
| Land—polar | 3：Fog Cloud, Hold Person, Ray of Frost；5：Sleet Storm；7：Ice Storm；9：Cone of Cold |
| Land—temperate | 3：Misty Step, Shocking Grasp, Sleep；5：Lightning Bolt；7：Freedom of Movement；9：Tree Stride |
| Land—tropical | 3：Acid Splash, Ray of Sickness, Web；5：Stinking Cloud；7：Polymorph；9：Insect Plague |

## 5. 精确行为契约

### 5.1 来源化授予与容量

1. 每项授予记录 `(owner, feature_id, spell_ref, granted_level, choice_id/terrain)`；有效 prepared 视图由普通准备与所有有效授予聚合，不靠无来源的平面字符串猜测归属。
2. 五组表按当前职业等级累积，低等级/错误子职不授予；常驻授予不占普通准备容量，并按各自职业通用规则视为该职业法术。
3. 同一法术由普通准备或两个来源同时提供时保留独立来源；撤销一个来源不得删除其他来源仍提供的可用性。
4. Land 的 3 级条目含 cantrips 时也必须真实授予，不能因字段叫 prepared 而遗漏 Fire Bolt、Ray of Frost、Shocking Grasp、Acid Splash。
5. 等级提升、子职变更、存档/加载及历史重建都从 canonical choices 重算授予，不保留过期地形或重复行。

### 5.2 Land 长休与升级选择

1. Land Druid 3 级起每次完成 Long Rest 必须从 arid/polar/temperate/tropical 记录一个选择；保持原地形也是明确选择，缺少选择时休息预览不得静默替换。
2. 切换在同一事务撤销旧 `circle_of_the_land_spells` 地形来源并添加新来源；旧地方法术若另有合法来源则保留，否则从 prepared 视图消失。
3. 只撤销本特性旧地形，不清空普通 Druid 准备、其他子职授予或免费施法使用状态。
4. Lore 6 级 Magical Discoveries 从 Cleric/Druid/Wizard 任意组合选两项；cantrip 或当前有槽环阶才合法，常驻且不占容量。每次获得 Bard level 最多替换其中一项，新选仍须合法。
5. Bard 10 级起 Magical Secrets 只在“Prepared Spells 数字增加而获得的新普通准备”及后续普通替换中开放 Bard/Cleric/Druid/Wizard 四列表；不增加容量、不将旧法术全部变成常驻，也不绕过可施放环阶。

### 5.3 自动化边界

1. 每个授予/准备投影提供由真实执行能力派生的 `automation_status=automated|not_automated`；不得手写 `automated` 掩盖空效果。
2. `not_automated` 法术可以合法出现在角色准备/授予记录，但战斗可用列表和校验不得把它当作可结算施法；施放请求继续拒绝且不扣槽、不产生空效果。
3. 本切片只验证法术来源、准备和自动化状态；已有专用施法处理器可计入真实能力，单纯 `class_refs`、等级或描述文本不可。

## 6. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`spell_catalogs/srd_spells.json` 及对应 en、zh-CN locale；法术文件仅修元数据缺口，不新增空 effect。
2. `src/rulesets/dnd2024/spells/selection.py`、`progression/advancement.py`、`features/resolver.py`。
3. `src/rulesets/dnd2024/resting/engine.py`：仅 Land 长休选择事务。
4. `src/rulesets/dnd2024/combat/view.py`、`validation.py`：仅自动化投影与 fail closed。
5. 新建 `tests/rulesets/test_dnd2024_spell_grants.py`；本任务书仅可追加执行报告。
6. 需要新增法术效果、改动通用世界引擎或白名单外职业文件时停止。

## 7. 编号测试矩阵

1. 五组特性在正确职业/子职和等级授予，低一级、错误子职、跨 owner 均不授予。
2. Life 的 3/5/7/9 每行名称与数量准确，9 级累积全部四级授予。
3. Oath 的 3/5/9/13/17 行准确，不把 7 级误作表节点或漏掉 13/17。
4. Draconic 使用 p70 表，3 级恰含 Alter Self、Chromatic Orb、Command、Dragon’s Breath，后续行无遗漏。
5. Fiend 的 3/5/7/9 表准确，Command 不与其他来源重复创建 canonical spell。
6. Land 四地形 3/5/7/9 每行逐项匹配官方表，9 级累积本地形全部法术。
7. 四种 Land 的 3 级 cantrip 均真实授予且不占普通 cantrip/准备容量。
8. 常驻授予不占普通 prepared capacity；普通准备在满容量时仍按原规则拒绝超额。
9. 常驻授予按对应职业计算施法属性/列表归属，不沿用原列表的错误施法属性。
10. 同一法术有普通与常驻两个来源时只显示一个有效可用项，撤销常驻后普通来源仍保留。
11. Land 长休切到每一地形都撤销旧授予；连续切换不累积四套或留下旧 cantrip。
12. 保持同一 Land 地形需明确选择且结果幂等；缺失地形选择不静默切换。
13. 旧地方法术同时由另一特性提供时，地形切换只移除 Land 来源。
14. Land 切换与普通 Druid 准备、休息资源恢复在同一原子流程，失败无半套授予。
15. Magical Discoveries 在 Lore 6 恰好两项，可来自 Cleric/Druid/Wizard 任意组合且不占容量。
16. Magical Discoveries 接受 cantrip/有槽环阶，拒绝无槽高环；每次 Bard 升级最多替换一项。
17. Magical Secrets 不增加容量；仅在容量增加的新准备及普通替换中开放四列表。
18. Magical Secrets 选择计为 Bard spells；Magical Discoveries 的常驻语义不被错误套用给它。
19. 无实际 effect/处理器的授予法术明确为 `not_automated`，不能仅靠目录元数据变为可施放。
20. `not_automated` 施放请求 fail closed，不扣槽、不消费资源、不产生假成功事件。
21. 已有真实自动化法术在获得授予后仍可正常施放，本切片不改变其效果。
22. 升级、降级/重建、存档/加载与重放后来源、容量和地形选择一致。
23. 非法 spell_ref、列表外选择、伪造 granted_level 或重复来源均拒绝且状态不变。
24. 既有 spell selection、advancement、resting 与 combat spell 校验测试无回归。

## 8. 非目标与停止条件

### 非目标

- 本任务不实现任何新法术伤害、召唤、问答或持续效果；后续任务按各自契约处理。
- Favored Enemy、Words of Creation、其他子职法术表、魔法物品或前端施法动画。
- 把全部授予复制进普通 prepared 数组、把 Land 四地形叠加或以目录存在验收。

### 停止条件

1. S0-B 未合入，或选择槽无法保存来源、 granted level 与地形身份。
2. 常驻来源无法与普通准备聚合并保证撤销隔离。
3. 长休不能原子完成 Land 换选，或自动化状态需要伪造 effect 才能通过。
4. 需要白名单外文件或实现新法术效果才能满足本切片。

## 9. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_spell_grants.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_advancement_access.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/spells src/rulesets/dnd2024/progression src/rulesets/dnd2024/features src/rulesets/dnd2024/resting src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_spell_grants.py
git diff --check
```

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 五组表逐项核对及 Draconic p70 证据：
- Land 四地形切换与来源撤销：
- Magical Discoveries/Secrets 容量边界：
- `not_automated` 投影和 fail closed：
- 白名单文件对照：
- 测试 1–24 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重建与重放证据：
- 偏差、停止原因与未决事项：
