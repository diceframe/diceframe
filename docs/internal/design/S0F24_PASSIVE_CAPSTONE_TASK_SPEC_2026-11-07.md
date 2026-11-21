# S0-F24 任务书：属性、熟练与总值替换被动

| 项 | 值 |
|---|---|
| 切片编号 | S0-F24（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `primal_champion`、`body_and_mind`、`slippery_mind`、`indomitable_might` |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Barbarian p30、Monk p52、Rogue p63；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | S0-C 被动派生与 Draconic Resilience；F1 被动检定；F3 失败 D20 窗口及重掷后的 total 重算 |
| 建议分支 | `feat/dnd2024-s0f24-passive-capstones` |
| 状态 | 已由 #784 实现并合并（本文件保留原始契约，状态见 `SLICE_STATUS.md`） |

## 1. 执行纪律与范围锁定

1. 从 S0-C/F1/F3 实际集成提交开工；属性派生、proficiency 来源和 check 事件接口未就绪即停止。
2. 四项特性必须改变真实角色派生或检定结果；不得只增加 summary，也不得把 Indomitable Might 改成天然骰下限。
3. 属性增加、熟练来源与检定替换均由 canonical 特性和 owner 驱动，不接受客户端提交加值、熟练或最终 total。
4. 不实现其他 capstone、Disciplined Survivor、Epic Boon 或通用建卡重构；只使用官方 PDF。
5. 保持 builder、逐级 advance、装备协调、存档/加载和战斗重放一致，不在战斗内临时写死属性。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p30，Barbarian：

> **Level 18: Indomitable Might.** If your total for a Strength check or Strength saving throw is less than your Strength score, you can use that score in place of the total.
>
> **Level 20: Primal Champion.** Your Strength and Constitution scores increase by 4, to a maximum of 25.

SRD 5.2.1 p52，Monk：

> **Level 20: Body and Mind.** Your Dexterity and Wisdom scores increase by 4, to a maximum of 25.

SRD 5.2.1 p63，Rogue：

> **Level 15: Slippery Mind.** You gain proficiency in Wisdom and Charisma saving throws.

以上“total”是完整检定总值，不是 d20 natural；“scores increase by 4”是在现有最终属性上增加并受 25 上限约束，不是把属性直接设为 25。

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 四个 id 已在 progression 表按 Barbarian 18/20、Monk 20、Rogue 15 列出，canonical 定义尚不存在 | `content/progression_catalogs/srd_classes.json`；class feature catalog |
| builder 以 `base_abilities + background_ability_bonuses` 得到 abilities，再计算 saving throws、HP、initiative、AC | `character/derivation.py::derive` |
| saving throw 熟练来自 class `saving_throw_refs`，modifier+PB 只加一次 | `character/derivation.py::derive` |
| 特性解析可读取实际拥有声明和数值，S0-C 是被动派生扩展边界 | `features/resolver.py::features_for`、`feature_value`、`feature_views` |
| `derive_armor_class()` 读取最终 Dex/Con/Wis 等属性；Draconic Resilience 派生不得被覆盖 | `character/derivation.py::derive_armor_class` |
| 战斗 actor view 使用 canonical abilities、proficiency 和 derived saving throws | `combat/view.py::_player_view`；`combat/resolution.py` |
| 先攻、攻击、法术 DC 和技能都依赖派生属性，不能仅改展示 sheet | `character/derivation.py::derive`；`combat/resolution.py::_start_combat_event` |
| `check.resolved` 事件保留 natural/modifier/total，重放只应用事件不重掷 | `combat/resolution.py`；`combat/reducer.py::_reduce_event` |
| 升级和 live projection 有独立协调入口，必须避免属性或 max HP 双算 | `progression/advancement.py`；`character/reconciliation.py::reconcile` |

## 4. 精确行为与数据契约

### 4.1 特性声明与属性增加

1. 四特性按真实职业和等级授予，source_ref 分别为 p30/p52/p63；中英文 locale 同步，mechanics 键保持 canonical。
2. `primal_champion` 声明 `ability_bonus_str=4`、`ability_bonus_con=4`、`ability_score_cap=25`；`body_and_mind` 同样声明 Dex/Wis +4。非法属性、bool、负数或上限不是 25 时 fail closed。
3. S0-C 的派生顺序必须先得到包含背景、ASI 和其他合法来源的属性，再逐项应用本特性：每属性独立 `min(25, before+4)`；21→25、22→25、24→25、25→25，低值仍只增加 4。
4. 增加写入派生后的 canonical abilities，而不是篡改 `build.base_abilities` 或背景答案；撤销/未授予特性后不得残留。重复 derive、builder 直建、逐级 advance 和存档加载结果一致。
5. 属性变化只计算一次，并驱动 ability modifier、技能、先攻、AC、被动感知、法术 DC、攻击与 saving throws；Con 对 max HP 的影响沿用 S0-C 已验收契约，当前 HP 不另造治疗规则。
6. Dex/Wis/Con 改变后，装备协调和 Draconic Resilience 使用新属性重算；不得将旧派生快照与新 abilities 混用。

### 4.2 Slippery Mind 熟练

1. `slippery_mind` 声明 `saving_throw_proficiencies=["wis","cha"]`，仅允许这两个 canonical ability id；数组重复、其他属性、字符串代替数组均 fail closed。
2. 在实际拥有时向 `proficiencies.saving_throw_refs` 合并 `ability:wis`、`ability:cha`，并在 `build.grants_with_sources` 记录该特性来源；不得修改职业基础熟练表。
3. Wis/Cha saving throw modifier 为当前属性修正+当前 PB，已有熟练时不重复加 PB；其他四属性不变。
4. 升级改变 PB、属性改变、Wild Shape 保留熟练或任何 F3 重派生路径都按同一来源集合计算，不在战斗事件中永久写死 bonus。

### 4.3 Indomitable Might 总值替换

1. `indomitable_might` 声明 `strength_total_replacement=true`，仅作用于 Strength ability checks 和 Strength saving throws；不作用于攻击、先攻、伤害、Dex/Con 豁免或死亡豁免。
2. 先按 F1/F3 正常掷骰、选择优劣势天然骰并累加全部合法 modifier/bonus，再计算 `ordinary_total`。若 `ordinary_total < 当前 Strength score`，最终 total 可替换为该 score；否则保持 ordinary_total。
3. 因替换没有不利结果，服务端可确定性地采用较高 total，但事件必须同时记录 `ordinary_total`、`replacement_score`、`total_replaced=true` 和 `feature_id`，证明替换确实发生。
4. **不得修改天然骰**：`natural` 和 `rolls` 始终是实际骰面；例如天然 3+2、Strength 18 的事件仍为 natural=3、ordinary_total=5、total=18，不得写成 natural=18 或重掷。
5. 替换发生在全部加值之后，不是 `max(natural,score)+modifier`，也不是天然骰 10 下限；Aura、Bless、条件惩罚和其他合法加减值先进入 ordinary_total。
6. 成功/失败使用替换后的 total 与 DC 比较。任何独立的天然 1/20 特殊规则仍按原天然骰处理，本特性不制造 critical、不取消 auto-fail，也不影响 Reliable Talent 的独立规则。
7. 20 级 Primal Champion 提升 Strength 后，replacement score 使用提升后的真实值；18/19 级使用当时值。存档后重算当前 score，不把旧 18 固化到角色。

### 4.4 可观察性与持久化

1. 公共事件仅在原 check 字段基础上增加许可的替换审计字段，不公开 build 秘密或其他角色完整属性；权威账本保留来源。
2. 所有派生是确定性的，不消耗 RNG；Indomitable 只改变 total，不增加骰、重掷或移动 RNG 顺序。
3. 升级、直接建卡、装备变更、save/load 和 apply/replay 后 abilities、proficiencies、derived stats 与 check 结果一致。
4. 不新增 content_version 或迁移；旧角色只有在真实满足等级/职业授予时才获得效果，不能按存档里伪造的特性 id 授予。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resolver.py`：仅参数校验和被动查询
5. `src/rulesets/dnd2024/character/derivation.py`
6. `src/rulesets/dnd2024/character/reconciliation.py`：仅 live 重派生一致性
7. `src/rulesets/dnd2024/combat/resolution.py`：仅 Strength check/save 总值替换
8. `src/rulesets/dnd2024/combat/view.py`：仅必要派生字段投影
9. `src/rulesets/dnd2024/runtime.py`：仅事件审计白名单
10. 新建 `tests/rulesets/test_dnd2024_passive_capstones.py`
11. 本任务书：只允许追加执行报告

不改 progression/subclass catalog、builder 输入 schema、reducer、前端、通用 check engine 或其他职业文件；需要时停止。

## 6. 编号测试矩阵

1. 四特性按 Barbarian 18/20、Monk 20、Rogue 15 真实授予，低一级和其他职业不获得。
2. Primal Champion 分别将 Str/Con 17→21、21→25、24→25、25→25，不影响其他四属性。
3. Body and Mind 只对 Dex/Wis 同样 +4 且上限 25；builder 直建和逐级 advance 结果相同。
4. 基础属性与背景答案不被重写，重复 derive/reconcile 不叠加 +4；撤销资格后无残留。
5. Str/Dex/Wis modifier、技能、先攻、AC、被动感知、法术 DC 和 saving throws 随新属性正确更新。
6. Con 提升后的 max HP 按 S0-C 契约只变化一次，当前 HP 不被本切片当治疗重复增加；save/load 不双算。
7. Draconic Resilience 和装甲/无甲 AC 在新 Dex/Wis/Con 下重算，装备协调不保留旧快照。
8. Slippery Mind 增加且仅增加 Wis/Cha 熟练，grants_with_sources 来源正确，中英文 catalog 一致。
9. Wis/Cha 已有熟练时不双倍 PB，其他四豁免不变；PB 升级后按新 PB 重算。
10. 非法属性 id、bool/负数 bonus、非 25 cap、重复熟练和未知参数均 fail closed。
11. Indomitable 对 Strength check 的 ordinary total 小于 score 时替换 total，天然骰和 rolls 不变。
12. ordinary total 等于或大于 score 时不替换，`total_replaced=false`，原 modifier 和 success 不变。
13. 示例天然 3+2、Str18 得到 ordinary_total=5、total=18、natural=3，绝不生成 natural=18 或额外 RNG。
14. 加值后 ordinary_total 仍低于 score 时才替换；Aura/Bless/惩罚先合成，不能变成 max(natural,score)+modifier。
15. Strength saving throw 同样生效；Dex/Con save、攻击、先攻、伤害和死亡豁免完全不读取该参数。
16. 替换后 total 决定 DC success；天然 1/20 的既有特殊分支仍按天然骰，不能借高 total 改写。
17. 18/19 级使用当时 Str，20 级 Primal 后使用新 Str；旧存档不缓存 replacement score。
18. Indomitable 与优势/劣势只改最终 total，不重掷、不改选中骰、不增加骰数，RNG 与基线一致。
19. Wild Shape 或其他保留 class feature 的形态按 F3 当前能力值和保留规则计算，不读显示名。
20. 非本人、伪造 feature id、伪造 total/replacement_score 或跨职业提交均不能影响权威 check。
21. check 事件含 ordinary_total/replacement/source 审计，公共 feed 只显示许可字段。
22. apply/replay 和 save/load 后 check 结果、属性、熟练、HP/AC 与派生值一致。
23. Rage Strength save、Survivor death save、Aura of Protection、Bless、Monk AC 和 Draconic Resilience 回归通过。
24. character builder/lifecycle、progression、class features、combat、全量 rulesets、ruff 和 diff 检查通过。

## 7. 非目标与停止条件

### 7.1 非目标

- 其他 capstone、Disciplined Survivor、Epic Boon、通用多职业或建卡改版。
- 将 Indomitable 扩展为所有 D20 Test、天然骰替换或自动成功。
- 前端数值展示改版、content migration 或新属性上限系统。

### 7.2 停止条件

1. S0-C/F1/F3 未合入，或派生顺序无法保证所有既有属性来源先合成。
2. Con/AC/HP 重派生会双算，或 builder/advance/live reconcile 不能共享同一实现。
3. 熟练无法以来源集合合并，导致重复 PB 或撤销后残留。
4. check 管线只有 natural 没有完整 ordinary_total，或下游把 total 误当天然骰。
5. 核心测试 5、9、13–16 需要伪造属性、特性、检定事件或修改白名单外文件。
6. 基线或并行分支已有同责实现，存在属性、熟练或 total 双写。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_passive_capstones.py -q
python -m pytest tests/rulesets/test_dnd2024_draconic_resilience.py tests/rulesets/test_dnd2024_character_builder.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_survivor.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/character src/rulesets/dnd2024/combat src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_passive_capstones.py
git diff --check
git status --short
```

记录实际 Python 路径、已知基线失败对照和测试 1–24 映射；必须单独展示 25 上限、熟练不双倍、natural 不变和 total 后替换证据。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、派生顺序和事件 schema。
2. 展示 builder/advance/live 三条路径的同值证据，以及 HP/AC 不双算证据。
3. 报告 RNG 不变、重放/存档、公共 feed 与全量回归结果。
4. 不得把其他 D20 Test 或天然骰替换写成 Indomitable 的规则。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 属性 +4/25 与派生重算：
- Slippery Mind 熟练来源：
- Indomitable total 替换与天然骰证据：
- 改动文件对照白名单：
- 测试 1–24 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与权限证据：
- 与契约的偏差及原因：
- 未决事项（不得把非目标列为已实现）：

## 11. 执行报告

- 实际基线：`47c82fb7`（S0-F23 执行报告提交），分支 `feat/dnd2024-s0f20-combat-start`
- Python：`E:/Github/diceframe/.venv/Scripts/python.exe`（3.13）；ruff/mypy：`C:/Python314/Scripts/`（读 pyproject，锁 3.11）
- 提交：`e2e5770e`（feat）/ `8b6c965f`（test）/ 本文件同批（docs）

### 11.1 四项声明与真实授予

四条 `class_feature_catalog` 声明，`source_ref` 分别 `srd-5.2.1:p30:indomitable-might`、`p30:primal-champion`、`p52:body-and-mind`、`p63:slippery-mind`；`progression_catalogs/srd_classes.json` 里 Barbarian 18/20、Monk 20、Rogue 15 原本就列了这四个 id，本切片只补 canonical 定义，没有改 progression。

| 特性 | owner | 等级 | parameters |
|---|---|---|---|
| `indomitable_might` | `class:barbarian` | 18 | `strength_total_replacement: true` |
| `primal_champion` | `class:barbarian` | 20 | `ability_bonus_str: 4`、`ability_bonus_con: 4`、`ability_score_cap: 25` |
| `body_and_mind` | `class:monk` | 20 | `ability_bonus_dex: 4`、`ability_bonus_wis: 4`、`ability_score_cap: 25` |
| `slippery_mind` | `class:rogue` | 15 | `saving_throw_proficiencies: ["wis", "cha"]` |

- 授予只由等级表决定：`has_feature` 在阈值那一级为真、低一级为假，别的职业到 20 级也不获得。往 `build.feature_ids` 里写四个 id 的伪造卡不获得任何效果（`test_a_forged_feature_id_on_the_sheet_grants_nothing`）。
- 中英文标签两条 locale 里原本就有（`Primal Champion`/原初冠军、`Body and Mind`/身心合一、`Slippery Mind`/圆滑心智、`Indomitable Might`/不屈之力），本切片未改 locale 文件。

### 11.2 属性 +4 与 25 上限：净替换记账

`apply_feature_ability_bonuses(abilities, grants, applied)` 是唯一入口：先把上次给的点数减掉得到 `base`，再加这次的点数，`record[ability] = raised - base` 写进 `derived.ability_bonus_applied`。

- 每属性独立 `min(25, base+4)`：17→21、21→25、24→25、25→25、8→12（`test_primal_champion_adds_four_within_the_cap`）。
- 上限只约束被声明的属性，`str` 到 25 不影响另外四项（`test_the_cap_only_bounds_the_score_it_names`）。
- 净替换带来三条性质，全部有用例：同一张卡重复应用不再加（`test_applying_the_bonus_twice_changes_nothing`）、失去资格把点数收回（`test_losing_the_feature_gives_the_points_back`）、中间拿过的 ASI 不被吞掉（`test_an_asi_and_the_feature_do_not_eat_each_other`）。
- `build.base_abilities` 与 `build.background_ability_bonuses` 原样保留（`test_the_build_record_is_never_rewritten`）。
- 发放点在 `preview_next_level` 的 `ability_increases` 之后、Con 进入 HP 公式之前；`diff["ability_bonus_applied"]` 随预览返回，`apply_next_level` 落到 `derived`，空字典时删除键。

### 11.3 Slippery Mind：合并同一集合，来源单独记

- `_update_derived` 把 `ability:wis`/`ability:cha` 并进已有的 `proficiencies.saving_throw_refs` 集合，PB 仍只在算加值时加一次，所以职业本来就给的豁免不会翻倍（`test_a_proficient_save_never_doubles_the_bonus`：dex/int 不变、wis/cha 恰好 `mod + PB`）。
- 来源写在 `derived.saving_throw_proficiency_sources = {"wis": "slippery_mind", "cha": "slippery_mind"}`；建卡路径 `derive()` 同一条规则，并把这两条 ref 的来源写成特性 id 而不是 `class:rogue`（`build.grants_with_sources`）。
- 直接建卡只支持 1 级，所以 `derive()` 那一段在真实建卡里走不到 15 级；保留它是为了让"谁能给熟练"只有一个答案，不留一条只有升级才存在的规则。

### 11.4 Indomitable Might：替换总值，不动骰面

`_strength_total_replacement(target, ability, total)` 在 `combat/resolution.py`，返回 `(用来判定的 total, 审计字段)`：

- 只在 `ability == "str"` 且角色真实拥有该特性时介入；`ordinary_total >= Str score` 时原样返回并记 `total_replaced=false`，否则返回 Str score 并记 `total_replaced=true`。
- 审计字段固定四个：`ordinary_total`、`replacement_score`、`total_replaced`、`feature_id`。`natural`、`rolls`、`modifier` 一个都不写，所以天然 3 + 2 的事件仍是 `natural=3 / rolls=[3] / ordinary_total=5 / total=21`（`test_the_natural_face_is_never_rewritten`）。
- 顺序是"先合成再替换"：天然 1 加上一串加值到 22 就保持 22，不会回落到 `max(natural, score) + modifier`（`test_modifiers_are_summed_before_the_replacement`）。
- 不掷骰、不加骰、不重掷：审计字段里没有骰面也没有掷骰记录，RNG 顺序与基线一致（`test_no_die_is_rolled_for_the_replacement`）。
- 替换后的 total 直接参与 `success = total >= DC`；替换不制造 `critical`，也不取消天然骰的既有分支（死亡豁免是另一条函数，见 11.7 的用例）。
- 接入点：`_hit_condition_events`（Stunning Strike）、`_open_hand_events`（Open Hand Push 的 Str save）、`_spell_events` 的 `saving_throw` 分支、`_channel_events` 的 divine_spark / turn_undead / abjure_foes、`_repeat_save_check`。auto-fail 分支（麻痹/昏迷自动失败）不经过替换，`total=0` 保持 `success=False`。
- 公共 feed：`runtime._recent_combat_events` 的 `allowed_fields` 增加三个数字字段，`feature_id` 本来就在白名单里（`test_the_public_feed_keeps_the_audit_fields` 通过真实投影验证）。

### 11.5 改动文件对照白名单

| 白名单 | 实际改动 |
|---|---|
| 1 `content/class_feature_catalogs/srd_class_features.json` | 四条声明 |
| 2/3 `locales/en|zh-CN/...` | 未改：四个标签基线已存在 |
| 4 `features/resolver.py` | `saving_throw_proficiency_sources`、`ability_bonuses`、`strength_total_replacement`、两个校验 + passive flag 元组 |
| 5 `character/derivation.py` | `apply_feature_ability_bonuses`；`derive()` 合并单项豁免熟练与来源 |
| 6 `character/reconciliation.py` | 未改：`reconcile` 只读已存 `abilities`，不会再从 base 重算（`test_armor_class_is_derived_from_the_new_scores_not_a_snapshot` 验证换装协调后 AC 仍按新属性） |
| 7 `combat/resolution.py` | `INDOMITABLE_MIGHT` 常量、`_strength_total_replacement`、六处豁免结算点接线 |
| 8 `combat/view.py` | 未改：actor view 已投影 `abilities`/`skill_values`/`saving_throws`，没有新增字段 |
| 9 `runtime.py` | `_recent_combat_events` 白名单三字段 |
| 10 `tests/rulesets/test_dnd2024_passive_capstones.py` | 新建，54 例 |
| 11 本任务书 | 追加本节 |

白名单外没有改动。

### 11.6 测试 1–24 逐项

| # | 覆盖用例 | 结果 |
|---|---|---|
| 1 | `test_each_feature_arrives_exactly_at_its_own_level`(4)、`test_another_class_never_owns_the_feature`(4) | 通过 |
| 2 | `test_primal_champion_adds_four_within_the_cap`(5)、`test_the_cap_only_bounds_the_score_it_names`、`test_primal_champion_raises_strength_and_constitution` | 通过 |
| 3 | `test_body_and_mind_only_moves_dexterity_and_wisdom` | 通过 |
| 4 | `test_the_build_record_is_never_rewritten`、`test_applying_the_bonus_twice_changes_nothing`、`test_losing_the_feature_gives_the_points_back`、`test_an_asi_and_the_feature_do_not_eat_each_other` | 通过 |
| 5 | `test_the_new_scores_reach_every_derived_number`、`test_a_dexterity_feature_moves_initiative_armor_class_and_skills` | 通过（法术 DC 见 11.9） |
| 6 | `test_constitution_raises_hit_points_once` | 通过 |
| 7 | `test_armor_class_is_derived_from_the_new_scores_not_a_snapshot` | 通过 |
| 8 | `test_slippery_mind_adds_wisdom_and_charisma` | 通过 |
| 9 | `test_a_proficient_save_never_doubles_the_bonus`、`test_the_two_new_saves_follow_the_ability_and_the_bonus` | 通过 |
| 10 | `test_malformed_ability_bonuses_fail_closed`(6)、`test_malformed_proficiency_grants_fail_closed`(5)、`test_a_truthy_replacement_flag_fails_closed`、`test_a_cap_without_a_bonus_fails_closed` | 通过 |
| 11 | `test_a_low_total_becomes_the_strength_score` | 通过 |
| 12 | `test_a_total_at_or_above_the_score_is_left_alone` | 通过 |
| 13 | `test_the_natural_face_is_never_rewritten` | 通过 |
| 14 | `test_modifiers_are_summed_before_the_replacement` | 通过 |
| 15 | `test_only_strength_reads_the_replacement`、`test_an_attack_roll_never_reads_the_replacement`、`test_a_death_save_never_reads_the_replacement` | 通过 |
| 16 | `test_the_natural_face_is_never_rewritten`（DC 15 由 fail 转 success，未生成 `critical`） | 通过 |
| 17 | `test_the_replacement_score_follows_the_current_strength` | 通过 |
| 18 | `test_no_die_is_rolled_for_the_replacement` | 通过 |
| 19 | — | 未覆盖，见 11.9 |
| 20 | `test_a_character_without_the_feature_keeps_its_total`、`test_a_forged_feature_id_on_the_sheet_grants_nothing` | 通过 |
| 21 | `test_the_public_feed_keeps_the_audit_fields` | 通过 |
| 22 | `test_the_sheet_round_trip_keeps_the_new_scores`、`test_the_advancement_is_deterministic` | 通过 |
| 23 | 聚焦 54 + 邻接 170 + 全量 2404 | 通过 |
| 24 | 见 11.7 | 通过 |

### 11.7 门禁输出

```
python -m pytest tests/rulesets/test_dnd2024_passive_capstones.py -q
54 passed, 3 warnings in 13.07s

python -m pytest tests/rulesets/test_dnd2024_draconic_resilience.py \
  tests/rulesets/test_dnd2024_character_builder.py tests/rulesets/test_dnd2024_progression.py \
  tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_survivor.py -q
170 passed, 5 warnings in 200.15s

python -m pytest tests/rulesets -q -n 6 --dist loadfile    # 最终树
2404 passed, 73 warnings in 489.39s (0:08:09)
python -m pytest tests/rulesets -q          # 串行，同一份代码的对照
2404 passed, 46 warnings in 2453.30s (0:40:53)

ruff check（resolver / character / combat / runtime / progression / 新测试）
All checks passed!
mypy
Success: no issues found in 102 source files
git diff --check
（无输出，exit 0）
```

基线对照：F23 收尾时 `tests/rulesets` 为 2350 passed，本切片 +54 例 → 2404 passed / 0 failed，无新增失败。

专门的证据（按要求单独列出）：

- 25 上限：`17→21 / 21→25 / 24→25 / 25→25 / 8→12`，见 `test_primal_champion_adds_four_within_the_cap`。
- 熟练不双倍：Rogue 15 的 dex/int 豁免与 14 级完全一致，wis/cha 恰好 `mod + PB`。
- natural 不变：`natural=3 / rolls=[3] / ordinary_total=5 / total=21 / total_replaced=true`。
- total 后替换：天然 1 + 加值到 22 时保持 22（不替换），到 2 时替换成 21。

### 11.8 与契约的偏差及原因

1. **审计字段名用 `feature_id`，与 channel 效果的 `feature_id` 同键**。本切片的 channel 豁免属性是 Con/Wis，结构上不会走到替换分支，所以不会互相覆盖；若将来出现 Str 属性的引导神力，这个键会冲突，届时应换成 `replacement_feature_id`。
2. **21/24/25 三档只在纯函数上验证**。19 级要拿到 21/24/25 必须靠 Epic Boon 越过 ASI 的 20 上限，而本切片的升级路径没有把 Boon 的属性增长纳入断言范围；`apply_feature_ability_bonuses` 是唯一实现，端到端的 17→21 与 20→24 另有覆盖。
3. **测试 19（Wild Shape）未覆盖**：引擎没有 Wild Shape，F3 的"保留 class feature 的形态"在本仓不存在，写一个占位用例只会假绿。
4. **测试 5 的"法术 DC"未覆盖**：Barbarian/Monk/Rogue 都不是施法者，四个特性也不落在施法职业上；技能（`skill_values`）、先攻、AC、被动感知、豁免都已覆盖。
5. **`derive()` 的 Slippery Mind 合并是防御性代码**：建卡只支持 1 级，15 级角色只能由升级产生，所以真实路径不会经过它；保留的理由是"谁能给熟练"只应有一个答案。
6. **Con +4 对当前 HP 的影响沿用升级既有规则**：受伤角色升级后 HP 增加量等于 `diff.hp.gain`（含 38 点回溯体质），本切片没有新增治疗事件，也没有改动这条规则；`test_constitution_raises_hit_points_once` 断言的是"只加一次、第二次协调不再加"。
7. **`_strength_total_replacement` 的接线点是六个**，不是"所有 check 事件"：`reaction_spells.py` 的 Con 豁免是硬编码属性，`check_windows.py` 的窗口面只服务 Dex/Hide 与条件重复豁免，都不具备 Strength 面；硬接进去只会增加无测试覆盖的分支。

### 11.9 未决事项

1. **Indomitable Might 没有端到端可打的 Strength 检定面**：引擎没有擒抱/推撞/通用 Strength 检定意图，SRD 里有 `save: "str"` 的法术一条也没实现（`spell_effects` 只有 dex/con/wis），Open Hand Push 的 Str save 打的是敌人。所以 11–14、18 走授权函数 + 真实 actor view，15/16 的"攻击、死亡豁免不读它"是真跑出来的。要补端到端，需要先有 Str 检定面（属 F21 之后的新切片）。
2. **`ability_bonuses` 只在升级路径生效**：`derive()` 不重算高级角色的属性（`reconcile` 读已存 `abilities`），所以"直接建 20 级角色"这条路要等建卡支持高等级时再接，届时 `derive()` 必须调用同一个 `apply_feature_ability_bonuses`，否则会出现第二个加值入口。
3. **Epic Boon 的属性增长**：Boon 能越过 20，但 Boon 的声明与结算不在本切片；Primal Champion 与 Boon 同级的顺序（谁先加、上限怎么套）未验证。
4. **`total_replaced=false` 时也写审计四字段**：契约只要求"替换发生时"记录，多记一份"没替换"的证据是为了让公共 feed 能区分"没这特性"和"有特性但没触发"，代价是每个 Str 豁免多四个键。
5. **前端未接**：`replacement_score`/`ordinary_total` 还没有展示位；按 F23 的惯例，展示另开切片。
