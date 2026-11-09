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

## 11. 执行报告（2026-11-09）

- **实际基线**：`b86eec6a`（`feat/dnd2024-s0f3-failed-d20-windows` 链头）；祖先基线 `integration/dnd2024-srd@35653f81`
- **分支 / 提交**：`feat/dnd2024-s0f4-spell-grants`（stack 在 F3 链头上，不覆盖旧 SP）
  1. `4735c726` 七条授予声明（五组表 + Land 四地形 + 两个 Bard 选择特性）
  2. `2c6c7b39` 授予行 / 常驻准备视图 / 跨列表来源 / Land 长休事务 / 升级接线 / 自动化状态
  3. `4d979003` 31 条矩阵测试（24 项 + Land 两处四地形参数化）
- **改动文件**：`templates/.../content/class_feature_catalogs/srd_class_features.json`（+148，七条声明与表数据）、`src/rulesets/dnd2024/features/resolver.py`（`spell_grant_rows()` 与 `spell_grant_choices()`，`actor_projection` 合入常驻授予）、`src/rulesets/dnd2024/spells/selection.py`（常驻视图、容量豁免、列表扩张、授予槽校验、`automation_status()`）、`src/rulesets/dnd2024/resting/engine.py`（Land 地形事务）、`src/rulesets/dnd2024/progression/advancement.py`（按升级后等级解析归属、写回授予选择）、`src/rulesets/dnd2024/combat/view.py`（授予行与 `automation_status`）、新建 `tests/rulesets/test_dnd2024_spell_grants.py`（31 条）

审计结论先行：七项特性在基线里**只有 locale 标签和子职目录里的 id，没有任何声明**，因此既没有表数据也没有窗口；`spell_grants` 这条投影管线已经存在（Paladin 的 `granted_spell_id` 与 Magic Initiate 专长），本切片**复用**它而不是另建一条。战斗侧的两个边界也已经在了：`_available_spells` 跳过无效果法术，`_validate_spell` 在效果缺失时直接拒绝。所以本轮做的是"补声明 + 补来源与容量 + 补长休事务 + 把自动化状态挂到授予行上"，没有改任何法术效果。

### 五组表与 Land 四地形

七个声明全部按"当前职业等级累积"的语义写：每行是 `granted_level <= 当前等级` 才生效，低于最低等级、没有选出该子职、或子职属于别的职业时一行都不授予（矩阵 1）。表的编码是扁平字符串 `"<level>:spell:<id>"`（Land 是 `"<terrain>:<level>:spell:<id>"`），原因是特性 `parameters` 在 `features/models.py` 里只接受标量与其列表（`_require_scalar`），而该文件不在本切片白名单内。解析失败的行被丢弃而不是抛错——目录笔误不该让整场战斗崩掉，真实防线是矩阵 2–6 的逐项断言。

Draconic 的 p70 表逐项核对过：`alter_self`、`chromatic_orb`、`command`、Dragon's Breath 四项在 3 级同行，其中 Dragon's Breath 在官方目录里的 canonical id 是 **`dragon_s_breath`**（2 环，Sorcerer/Wizard，locale 已是 "Dragon's Breath"），所以**没有新增法术条目**，也没有为了对齐表而伪造一条空 effect。其余四组同样全部命中既有 canonical id：Life 10 条、Oath 10 条（3/5/9/13/17，**没有 7 级节点**）、Draconic 10 条、Fiend 10 条，Land 四地形各 6 条、合计 24 条。

### 授予的来源、聚合与撤销

`Dnd2024ClassFeatureResolver.spell_grant_rows()` 是唯一产出授予的地方，每行带 `owner`、`feature_id`、`spell_ref`、`granted_level`、`terrain`、`choice_id`、`spell_ability`。`spell_ability` 取**授予者所属职业**的 `spellcasting_ability`，不是法术被印在哪个列表上的属性：Land Druid 的 Fire Bolt 是 Sorcerer/Wizard 法术，但它按 Wisdom 结算（矩阵 9 用 Wis 15 / Int 8 的夹具把两个加值区分开）。

`Dnd2024SpellSelection.prepared_view()` 把普通准备与全部授予聚合成"每个法术一条有效项"，来源并列保留。普通准备与常驻授予同时提供一个法术时仍然只有一条，撤销常驻来源后普通来源还在（矩阵 10、13）。容量拆成四个字段：`prepared`（职业表的数量）、`prepared_used`（普通准备实际用了几格）、`granted`（常驻授予的去重条数）、`cantrip`/`cantrip_used`；常驻授予既不计入 `prepared_used`，也不扩大 `prepared`（矩阵 7、8）。

### Land 的四地形与长休事务

地形选择落在 `spellcasting.class.spell_grant_choices.circle_of_the_land_spells.terrain`。长休先算"计划"再落账：`_plan_land_terrain()` 只做校验（非法地形直接 `RestError`，此时 HP、槽位、资源一个都没动），`_apply_land_terrain()` 在资源恢复之后才写回并产事件。切换是**替换**而不是叠加：同一槽位改一个字符串，旧地形那套表下一读就不再生效，因此不会残留旧戏法（矩阵 11）。只撤本特性的地形槽，不碰普通 Druid 准备、也不碰别的子职授予（矩阵 13）。

同一地形再选一次是合法且幂等的选择（`changed: false`）；不提交选择时**保持原地形**且不产事件；从没选过又没提交时，产一个显式的 `spell_grant_terrain_missing`，授予为空而不是替玩家挑一种（矩阵 12）。

### Magical Discoveries 与 Magical Secrets

Magical Discoveries 是"两个常驻选择"：`spell_choice_count: 2`、`spell_choice_replacement: 1`、`spell_choice_lists` 指向 Cleric/Druid/Wizard 三个列表。校验逐条拦住：数量、重复、非法 id、列表外（Hex 是 Warlock 的）、当前没有槽的环阶（Bard 6 的 `polymorph` 是 4 环）、以及"每次 Bard 升级最多换掉其中一项"（矩阵 15、16）。槽位不重提交时保留原样，不被当成"清空"。

Magical Secrets 只做一件事：`prepared_list_expansion` 把 Bard/Cleric/Druid/Wizard 四个列表并入**普通准备**的候选项。它不增加 `prepared_spell_count`（Bard 9→10 只按表加 1），不把旧法术变成常驻，也不给戏法开新列表（戏法不是 prepared spells，第一版漏了这条，矩阵 17 把戏法选项钉住）。选中的四列表法术仍是 `class_list` 来源，按 Bard 的 Charisma 结算（矩阵 17、18）。

### `not_automated` 与 fail closed

`automation_status` 只由"这个法术在 `spell_effects` 里真的有可结算效果"派生，两处同源：战斗的 actor 投影（`spell_grants[].automation_status`）与角色表投影（`prepared_view()` 的每一条）。Life 3 级里 `bless`／`cure_wounds` 是 `automated`，`aid`／`lesser_restoration` 是 `not_automated`（矩阵 19）。施放 `aid` 在校验阶段就被拒绝，槽位、资源、版本号一个都没动，也不产生任何事件（矩阵 20）；而被授予的 `fire_bolt`（Land—arid 的戏法，早就有 `spell_attack` 效果）照常结算，本切片没有碰它的效果（矩阵 21）。

### 存档、重建与重放

授予是**读时重算**的：等级、子职、地形三者都在规范状态里，`spell_grant_rows()` 每次从它们重新展开表。因此 JSON 往返后行逐条相同、同一状态连续两次投影完全相同、把等级字段抬高一级后地形授予仍然成立、走真实升级路径（Druid 9→10）时 `spell_grant_choices` 被保留而不是重建（矩阵 22）。`configure()` 只合并本次提交的授予槽，不会把已经选好的地形写成"未选"（同一矩阵）。

### 白名单文件对照

| 白名单条目 | 实际改动 |
|---|---|
| 1. `class_feature_catalogs/srd_class_features.json` 及 locale | 只改内容文件；七条特性的 locale 标签基线已有（en／zh-CN 各一条），未新增标签，未改 `srd_spells.json` |
| 2. `spells/selection.py`、`progression/advancement.py`、`features/resolver.py` | 三个文件均有改动，见上 |
| 3. `resting/engine.py`（仅 Land 长休选择事务） | 只加 `_plan_land_terrain()`／`_apply_land_terrain()` 与 `complete_long_rest()` 的一个可选参数 |
| 4. `combat/view.py`、`validation.py`（仅自动化投影与 fail closed） | 只改 `view.py` 的授予行投影；`validation.py` 未改（既有 `_validate_spell` 已经 fail closed） |
| 5. 新建 `tests/rulesets/test_dnd2024_spell_grants.py` | 31 条 |

未新增法术效果、未改 `combat_catalogs`、未改 `features/models.py`、未改 `runtime.py`、未改任何白名单外的职业文件。

### 第一版回归：未回答的授予槽挡住了整条升级路径

第一版把 `_validate_grants()` 写成"拥有槽位却没选 = 错误"，第一次全量于是报了 **17 failed / 1659 passed / 62 errors**。按文件归因：`test_dnd2024_failed_d20_windows.py` 43 ERROR、`test_dnd2024_bardic_inspiration.py` 19 ERROR + 4 FAILED、`test_dnd2024_d20_skills.py` 1 FAILED、`test_dnd2024_cutting_words.py` 1 FAILED（剩下 11 FAILED 是 `test_dnd2024_character_builder.py` 的 F2 已知基线）。报错信息统一是 `spell grant magical_discoveries requires 2 spells`——所有经过 Bard 6 级的旧夹具都被一个新特性挡住了。

先按 F3 的教训怀疑过一次环境假象，但**单跑同一批 id 完整复现了 62 个 ERROR**，所以是真回归，不是并发假象（这次也确实没有并发跑两份全量）。修法是把两条边界分开：

- **"还没选"不是"提交了非法内容"**。槽位继续以 `selected_refs: []` 出现在 `grant_slots()`，升级预览里 `class_spell_choices.required` 仍为 `true`、`grant_slots` 仍列出 `count`，客户端照旧被告知"这里有两项要选"；但只提交 `cantrip_refs`/`prepared_spell_refs` 的调用方不会被判错，也不会被凭空授予任何法术。
- **"提交了却不完整"仍然硬失败**。`{"magical_discoveries": ["spell:shield"]}` 依旧得到 `must contain exactly 2 spells`，数量、重复、列表外、无槽环阶、替换上限的校验一条没松。

修复后单跑这四份文件是 68 + 90 passed。新增 `test_matrix_24_an_unanswered_grant_slot_never_blocks_a_level_up` 把这两条边界都钉住（不回答可升级、槽位照实开放、`spell_grant_choices` 不被写脏、授予零行）；原来的 `test_matrix_24_a_bard_without_magical_secrets_keeps_the_bard_list` 改名为 `test_matrix_17b_*`——它验的是第 17 条后半段（9 级 Bard 仍只限 Bard 列表），挂了第 24 条的名字属于误标。

### 矩阵 1–24 证据表

| # | 测试函数 | 钉住的事实 |
|---|---|---|
| 1 | `test_matrix_1_tables_grant_only_to_their_own_subclass_at_level` | 五组只在自身上级等级授予；低一级、错子职、跨 owner 零行 |
| 2 | `test_matrix_2_life_domain_rows_and_level_nine_accumulation` | Life 3/5/7/9 逐项，9 级累积 10 条 |
| 3 | `test_matrix_3_oath_rows_have_no_seventh_level_node` | Oath 3/5/9/13/17，没有 7 级节点，13/17 不被漏掉 |
| 4 | `test_matrix_4_draconic_uses_the_page_seventy_table` | Draconic p70 表，3 级同行四项（含 `dragon_s_breath`） |
| 5 | `test_matrix_5_fiend_table_reuses_the_canonical_spells` | Fiend 3/5/7/9，`command` 复用既有 canonical 条目 |
| 6 | `test_matrix_6_land_tables_match_each_terrain`（×4 地形） | 四地形 3/5/7/9 逐项，9 级累积本地形全部 |
| 7 | `test_matrix_7_land_cantrips_are_granted_and_cost_no_capacity`（×4 地形） | 四地形 3 级戏法真实授予且不占 cantrip/prepared 容量 |
| 8 | `test_matrix_8_grants_never_consume_prepared_capacity` | `prepared`/`prepared_used` 与无授予的同级角色相同，`granted` 单列 |
| 9 | `test_matrix_9_grants_cast_with_the_granting_class_ability` | Wis 15 / Int 8 夹具下按授予者职业（Wis）结算 |
| 10 | `test_matrix_10_one_effective_entry_per_spell_and_sources_stay_separate` | 双来源只出一条有效项，`sources` 并列，撤销隔离 |
| 11 | `test_matrix_11_each_terrain_switch_revokes_the_previous_list` | 逐地形切换撤销旧授予，不累积四套、不留旧戏法 |
| 12 | `test_matrix_12_keeping_the_terrain_is_idempotent_and_missing_never_switches` | 同地形幂等、不提交不切换、从未选过时授予为空 |
| 13 | `test_matrix_13_a_second_source_survives_the_terrain_switch` | 旧地方法术另有来源时切地形只移除 Land 那一路 |
| 14 | `test_matrix_14_an_illegal_terrain_leaves_the_whole_rest_untouched` | 非法地形整休报错且 HP/槽位/资源全未动，无半套授予 |
| 15 | `test_matrix_15_magical_discoveries_is_exactly_two_always_prepared_picks` | Lore 6 恰两项、三列表任意组合、`granted_level` 6、不占容量 |
| 16 | `test_matrix_16_magical_discoveries_level_and_replacement_limits` | 拒 4 环（Bard 6 无槽）、拒列表外、每级最多替换 1 项、不重提交保留 |
| 17 | `test_matrix_17_magical_secrets_widens_lists_without_widening_capacity` + `test_matrix_17b_*` | 10 级四列表扩张、`prepared` 只按表 +1、戏法不开列表、9 级仍限 Bard |
| 18 | `test_matrix_18_secrets_are_ordinary_bard_spells_not_always_prepared` | 选中项是 `class_list` 来源而非 `class_feature`，不套常驻语义 |
| 19 | `test_matrix_19_grants_report_automation_from_the_real_effects` | 战斗投影与角色表投影同源，`aid`/`lesser_restoration` 为 `not_automated` |
| 20 | `test_matrix_20_an_unautomated_grant_cannot_be_cast_and_costs_nothing` | 施放被拒，槽位/资源/版本不变，无事件 |
| 21 | `test_matrix_21_a_granted_automated_spell_still_resolves` | 已有 `spell_attack` 效果的授予法术照常结算（`dnd2024.spell.cast`） |
| 22 | `test_matrix_22_rebuild_and_round_trip_keep_sources_and_terrain` | JSON 往返、连续投影、抬高等级、真实升级路径均保留来源与地形 |
| 23 | `test_matrix_23_illegal_grant_choices_are_rejected_without_state_change` | 伪造 `granted_level`、未知槽、列表外、超额替换均拒绝且状态不变 |
| 24 | `test_matrix_24_an_unanswered_grant_slot_never_blocks_a_level_up` + 套件级全量 | 未回答的槽不挡升级；既有 spell/advancement/resting/combat 测试无回归 |

### 验收数字（§9 五条命令）

| 命令 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_spell_grants.py -q` | **31 passed**（51 s） |
| `pytest … spells … resting … advancement_access … class_features … dark_ones_blessing … smite -q` | **154 passed**（120 s） |
| `pytest tests/rulesets -q` | **11 failed / 1728 passed**（33:22） |
| `ruff check src/rulesets/dnd2024/{spells,progression,features,resting,combat} tests/rulesets/test_dnd2024_spell_grants.py` | All checks passed |
| `git diff --check` | 干净（只有一处 LF→CRLF 提示） |

全量的 11 条失败**逐条等于 F3 链头上的基线**，全部在 `test_dnd2024_character_builder.py`（建卡派生与 HTTP 契约，F2 起就存在、与本切片无关）；通过数 1697 → 1728 正好是本切片新增的 31 条，**净引入 0 失败、0 错误**。

### 偏差、停止原因与未决事项

1. **两个玩家选择没走 `features.feature_choices`**。`FEATURE_CHOICE_KINDS` 是加载期闭集（`advancement_feat` / `skill_proficiency`），声明一个 `spell_grant` 槽必须改 `features/models.py`，而它不在白名单。因此 Land 地形与 Magical Discoveries 的选中项存在 `spellcasting.class.spell_grant_choices`，由 `spells/selection.py` 校验 owner、数量、来源列表、环阶与替换上限。代价：S0-B 那套 owner-scoped 记录校验**不覆盖**这两个槽；收益是不必为一个容器破坏加载期的闭集。若后续要统一，应加一个新的 choice kind，并把两个校验函数合并——那是一次独立的、有明确迁移面的切片。
2. **缺省地形不报错，只显式报告**。契约 5.2.1 要求"每次长休必须记录一个选择"，但 `runtime.complete_rest()` 在基线上不转发任何长休选项（短休只有 `attunement` 一条通道），而 `runtime.py` 不在白名单。硬性报错会让**每一个** Land Druid 的长休都失败，因此实现为：不静默替换、授予为空、产 `spell_grant_terrain_missing` 事件。要让客户端真正提交地形，需要后续切片在 `runtime.complete_rest()` 上加一条 `land_terrain` 通道（一行转发 + 一层选项校验），本切片已把 `complete_long_rest(payload, land_terrain=...)` 的入口留好，也接受 `payload["land_terrain"]`。
3. **表数据编码为扁平字符串**。见上：`parameters` 只收标量列表。可读性不如嵌套 JSON，但换来"零个白名单外文件"。
4. **`selection.py` 读 `combat_catalog.spell_effects`** 来判断 `automation_status`。这是与战斗执行同一张表（不是第二份真相），代价是法术选择层依赖战斗目录的存在；bundle 没有该目录时一律判 `not_automated`（保守方向）。
5. **Magical Secrets 的"仅新准备"按能力而不是按差分实现**。SRD 第二句明确允许"替换时也可以来自这四个列表"，所以"重选"与"替换"在数据上是同一件事：拥有该特性后，任何普通准备项都可以来自四列表。没有额外追踪"哪一项是升级时新加的"，那需要跨等级的差量状态，而契约 17 只要求"不增加容量、只在容量增加的新准备及普通替换中开放"。
6. **死亡豁免与 F3 的交互未变**。本切片不碰 d20 窗口，`test_dnd2024_failed_d20_windows.py` 全绿；F3 记录的"死亡豁免不开放重掷面"缺口仍然存在，与本切片无关。
7. **"待选"是合法状态，不是错误**。Magical Discoveries 的两项在 6 级必须选，但"还没选"与"提交了错误内容"被分开处理：前者只让槽位保持开放（`grant_slots` 里 `selected_refs: []`，`class_spell_choices.required: true`），后者才硬失败。代价是一个 6 级 Lore Bard 可以在没有这两项的情况下存在，直到客户端补答；收益是任何只提交普通准备的调用方、以及本切片之前的所有升级路径都不被新特性挡住——这正是矩阵 24 的要求，也是第一版 62 个 ERROR 换来的结论。若后续要收紧成"升级时必须回答"，需要同时给全部升级夹具与客户端补上这一项，那是一次有明确迁移面的独立改动。
