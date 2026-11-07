# S0-C 任务书：被动角色推导 + Draconic Resilience

版本：v2（2026-11-07，取代 2026-10-30 的 v1）

状态：待执行；本文是执行契约，不表示功能已实现。

执行侧须知：

1. 本任务书是本切片的唯一需求来源。不要读取其他施工计划或旧切片任务书来扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. **前置依赖**：S0-A 与 S0-B 必须已依次完成并合入 `integration/dnd2024-srd`。从 S0-B 执行报告读取最终提交，以该提交新建干净工作树和分支 `feat/dnd2024-s0c-draconic-resilience`；不要猜提交号，不要使用已有 `df-int` 工作树。
3. 本任务只实现 Draconic Sorcery 3 级 Draconic Resilience，以及它必需的无甲 AC / 最大 HP 共享参数。不是补齐术士或 Draconic Sorcery。
4. v1 的 `.rulebooks` 来源、p52 页码、“直接建 3 级角色”和“不读取 subclass_ref”均已废止。官方原文在 p69；当前建卡只允许 1 级，高等级必须经升级或历史重建。
5. 不使用 open5e 或 2014 数据作为规则依据；下方引用的官方 SRD 5.2.1 原文即本任务规则依据。

---

## 1. 目标与完成定义

打通以下端到端链路：

```text
Sorcerer 实际选择 subclass:draconic_sorcery
→ 3 级获得 Draconic Resilience
→ 最大 HP 在 3 级 +3，之后每个 Sorcerer 等级再 +1
→ 未穿甲时基础 AC 候选为 10 + DEX + CHA
→ 穿甲时该候选完全退出，盾牌仍按既有规则独立相加
→ 升级、ASI、历史重建与 live 换装保持一致
```

沉淀两个后续可复用的封闭参数：

1. `unarmored_ac_ability_bonus`：提供一个无甲基础 AC 候选，不与护甲公式混用。
2. `max_hp_bonus_first` / `max_hp_bonus_per_level_after`：按拥有特性的职业等级计算最大 HP 加成。

完成不等于 Resolver 显示一个特性。必须同时验证实际子职选择、升级增量、历史重建、中英文投影、穿脱护甲和既有 AC/HP 回归。

## 2. 官方规则事实（SRD 5.2.1）

官方基准：`SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`。Draconic Sorcery，印刷页 69：

> **Level 3: Draconic Resilience**
>
> The magic in your body manifests physical traits of your draconic gift. Your Hit Point maximum increases by 3, and it increases by 1 whenever you gain another Sorcerer level.
>
> Parts of you are also covered by dragon-like scales. While you aren’t wearing armor, your base Armor Class equals 10 plus your Dexterity and Charisma modifiers.

必须锁定的规则边界：

1. 3 级获得时最大 HP +3；4 级再 +1，5 级再 +1。单职业 Sorcerer 等级 `N >= 3` 时累计加成为 `N`，不是 `3N`，也不是从 1 级开始每级 +1。
2. 只有实际选择 `subclass:draconic_sorcery` 的 Sorcerer 获得该特性；未选子职、其他职业或伪造其他 owner 均不获得。
3. “未穿甲”是硬前提。穿甲时使用既有护甲公式，不得因为 `10 + DEX + CHA` 更高就与护甲值取最大。
4. 无甲时，普通 `10 + DEX` 与 Draconic `10 + DEX + CHA` 是基础 AC 候选，确定性选择较高者；负 CHA 时回退普通无甲值。不得把多个候选的属性调整值累加。
5. 盾牌不是护甲。无甲持盾时，在选定的无甲基础 AC 上继续加既有盾牌 bonus。
6. HP 加成只改变最大 HP 的推导与当次升级增量，不额外治疗，不改变既有 CON 回顾公式。
7. 不使用 2014 的 `13 + DEX` 或“从 1 级起每级 +1”公式。
8. 多职业不在本切片内；当前引擎只支持单职业，公式中的等级就是该 Sorcerer 的等级。

## 3. 基线现状（已核实）

| 事实 | 位置 |
|---|---|
| 子职表在 Sorcerer 3 级授予 `draconic_resilience`，子职 id 为 `draconic_sorcery` | `content/subclass_catalogs/srd_subclasses.json` |
| S0-A 后子职特性必须经 `features.subclass_ref` 和 owner-scoped key 授予 | `features/models.py`、`features/resolver.py` |
| 建卡校验只允许 1 级，高等级角色通过 advancement history 重建 | `character/validation.py`、`character/builder.py` |
| `derive_armor_class()` 是唯一 AC 公式，建卡、升级与 live 换装均应复用 | `character/derivation.py`、`character/reconciliation.py`、`progression/advancement.py` |
| S0-B 后 AC 推导接受 `feat_refs`，升级 `_update_derived()` 会重算 AC | 同上 |
| 建卡最大 HP 使用 hit die、CON、等级和 species HP bonus | `character/derivation.py` |
| 升级 HP 使用 `new_level_hp + retrospective_con_hp`，再更新 max/current HP | `progression/advancement.py` |
| `feature_views()` 已支持被动特性和 owner 投影 | `features/resolver.py` |
| class feature catalog 当前没有 Sorcerer 条目 | `content/class_feature_catalogs/srd_class_features.json` |
| 战斗玩家 AC 读取角色推导结果，不需要战斗层新增公式 | `combat/view.py` |

行号不是契约。若前置任务合入后的结构与上述边界不同，按 §7 停止，不要建立第二套推导路径。

## 4. 精确实现契约

### 4.1 内容声明

在 `classes` 中新建 `sorcerer`，class source_ref 使用 `srd-5.2.1:p64:sorcerer-features`，追加：

```json
{
  "id": "draconic_resilience",
  "owner": "subclass:draconic_sorcery",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p69:draconic-resilience",
  "summary": "Your Hit Point maximum increases by 3, and it increases by 1 whenever you gain another Sorcerer level. While you aren't wearing armor, your base Armor Class is 10 plus your Dexterity and Charisma modifiers.",
  "parameters": {
    "unarmored_ac_ability_bonus": "cha",
    "max_hp_bonus_first": 3,
    "max_hp_bonus_per_level_after": 1
  }
}
```

标签：英文 `Draconic Resilience`；中文 `龙族体魄`。同时补齐 Sorcerer / 术士的目录 label。canonical 与中英文 locale 同步更新。

### 4.2 参数校验与 Resolver API

在 `features/models.py` / `features/resolver.py` 中：

1. `unarmored_ac_ability_bonus` 只能是六属性 canonical 缩写 `str/dex/con/int/wis/cha` 之一；其他类型或值 fail closed。
2. `max_hp_bonus_first` 与 `max_hp_bonus_per_level_after` 必须是非 bool 的 int，范围 `0..20`；负数、字符串、布尔、浮点数均 `ClassFeatureError`。
3. 新增 `unarmored_ac_bonus_abilities(character) -> tuple[str, ...]`：只扫描当前实际拥有且符合既有活性规则的特性，按目录序返回去重属性。
4. 新增 `feature_max_hp_bonus(character) -> int`：无有效单职业状态返回 0；对每个当前拥有且声明 HP 参数的特性计算：

```text
first + per_level_after × max(0, owner_class_level - minimum_level)
```

本切片只有 Draconic Resilience 一个消费者，但实现不得写死 feature id。
5. 两个 API 都必须尊重实际 `features.subclass_ref`。不得构造只含 build 的代理来推断 3 级角色的子职；用于目标等级的代理必须同时带目标 `subclass_ref`。
6. `FeatureView.values` 沿用既有标量参数投影；owner、source_ref 和 labels 正常出现，无 choice 字段。

### 4.3 无甲 AC 共享消费点

在 `character/derivation.py` 中：

1. `derive_armor_class()` 在 S0-B 的参数基础上新增仅关键字 `unarmored_ability_bonuses: Iterable[str] = ()`，默认空，保持旧调用兼容。
2. 只有没有选中 armor 时，才计算候选：

```text
10 + DEX
10 + DEX + ability_modifier(每个声明属性)
```

取最大候选作为无甲基础 AC；随后继续复用既有 shield bonus 与 Defense feat bonus 逻辑。
3. 选中 armor 时，Draconic 候选不参与 `max()`；既有 armor 选择、DEX cap、盾牌、魔法物品与 Defense 行为不变。
4. 重复属性、非法顺序或 iterable 顺序不得让同一特性叠加；Resolver 已去重，推导层也不得把同一属性重复累加。
5. 建卡 1 级通常没有本子职特性，但仍须通过同一 Resolver/AC 路径，不得为建卡另写公式。
6. `character/reconciliation.py` 复用同一个 Resolver 实例取得无甲属性并传给 AC 推导，保证换装后及时更新。
7. `progression/advancement.py::_update_derived()` 在 S0-B 的 AC 重算调用中传入当前角色的无甲属性，覆盖 3 级获得特性以及 DEX/CHA ASI 后的重算。

### 4.4 最大 HP 的建卡、升级与历史重建

1. `character/derivation.py` 的既有最大 HP 基础公式完成后，加上 `resolver.feature_max_hp_bonus(character_or_proxy)`。当前直接建卡只允许 1 级，因此该路径主要保证公式同源和安全回退，不得放宽建卡等级限制。
2. `preview_next_level()` 计算 HP 时增加：
   - `feature_hp_before = feature_max_hp_bonus(当前角色)`
   - `feature_hp_after = feature_max_hp_bonus(带 target_level 与 selected_subclass_ref 的目标代理)`
   - `feature_hp_gain = after - before`
3. 目标代理至少包含正确的单职业 `build.class_levels` 与 `features.subclass_ref`。2→3 选择 Draconic Sorcery 时 before=0、after=3；3→4 时 before=3、after=4。
4. `hp_gain = 既有 new_level_hp + retrospective_con_hp + feature_hp_gain`。不得改动既有 CON 回顾项，不得把 after 全量再加一次。
5. `preview["diff"]["hp"]` 增加 `feature_bonus_before`、`feature_bonus_after`、`feature_bonus_gain`，便于审计；既有字段不变。
6. apply 沿用既有 `max_hp += hp_gain` 与 `hp = min(max_hp, hp + hp_gain)`；不额外写一次特性 bonus。
7. 高等级提交经既有 history 重建时，逐级重放上述增量，最终结果必须与逐级升级一致。
8. 不做独立存量存档迁移或一次性“补偿历史遗漏 HP”。发现旧角色事实状态与重放结果不一致时记录，不在本切片自动修复。

## 5. 改动白名单

只允许修改以下文件；最后 `git status` 中出现其他代码/内容变更即不通过：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/models.py`
5. `src/rulesets/dnd2024/features/resolver.py`
6. `src/rulesets/dnd2024/character/derivation.py`
7. `src/rulesets/dnd2024/character/reconciliation.py`
8. `src/rulesets/dnd2024/progression/advancement.py`
9. 新建 `tests/rulesets/test_dnd2024_draconic_resilience.py`
10. `tests/rulesets/test_dnd2024_progression.py`：仅允许为 HP diff 字段与历史重建追加必要断言
11. `tests/rulesets/test_dnd2024_class_features.py`：仅允许为 Sorcerer 声明和 Resolver API 追加断言
12. `tests/rulesets/test_dnd2024_live_equipment_reconciliation.py`：仅允许追加无甲/穿甲/盾牌联动回归
13. 本任务书：只允许追加 §10 执行报告

不修改战斗代码、前端、builder draft/validation、manifest、content_version、法术选择器、其他职业/子职数据、其他执行报告或 `.workbuddy/`。

## 6. 必做测试矩阵

### 6.1 声明与所有权

1. Draconic Resilience 可加载，owner 为 `subclass:draconic_sorcery`，minimum_level/source_ref 为 3/p69，中英文 label 正确。
2. 非法无甲属性、HP 参数为负数/字符串/布尔/浮点数/大于 20 均 `ClassFeatureError`。
3. 2 级 Sorcerer 即使伪造 Draconic 子职也没有特性；3 级但未选择子职也没有。
4. 3 级 Sorcerer 实际选择 Draconic Sorcery 后拥有特性；3 级其他职业或伪造其他 owner 不拥有。
5. 两个 HP/AC Resolver API 对无职业、空 features、错误 subclass_ref 安全返回空/0，不自动选择代表子职。

### 6.2 最大 HP 与升级

6. 1 级 Sorcerer 无加成；Resolver 直接构造的 3/4/5/20 级 Draconic 角色累计 bonus 分别为 3/4/5/20。
7. 经真实法术选择流程 2→3 并选择 Draconic Sorcery 时，HP diff 的 feature before/after/gain 为 0/3/3，总 hp_gain 只增加 3。
8. 3→4 时 feature before/after/gain 为 3/4/1；4→5 为 4/5/1。
9. 升级时既有 fixed/rolled HP、CON 和 species HP 分量不变；CON 提升的 retrospective 分量独立计算。
10. 当前 HP 按既有升级规则增加相同 hp_gain 并受新 max_hp 限制，没有第二次“龙族治疗”。
11. 从 1 级逐级升至 5 级与从既有 advancement history 重建的 5 级角色，最大 HP、当前 HP、特性集合和 HP diff 历史一致。
12. 无效子职或缺失选择的 2→3 apply 仍失败且不改变 HP；不得先加 3 再报错。

### 6.3 无甲 AC 与换装

13. 3 级 Draconic Sorcerer，DEX 14、CHA 16，无甲 AC 为 15；同属性 2 级 Sorcerer 为 12。
14. CHA 为负时 AC 回退 `10 + DEX`，不是低于普通无甲值，也不是额外取绝对值。
15. 穿 Light、Medium、Heavy Armor 各一例时只使用既有护甲公式；即使 `10 + DEX + CHA` 更高也不参与取最大。
16. 无甲持盾时 AC 为选定无甲基础值加既有盾牌 bonus；只持盾仍视为无甲。
17. 护甲在背包但未装备时使用无甲公式；装备后立即退出无甲公式；卸下后恢复。
18. “无甲 → 穿甲 → 无甲持盾 → 无甲”连续 reconciliation 后，canonical derived AC 与 legacy armor_class 始终一致。
19. 3 级升级当次返回的 AC 已应用 Draconic 公式；后续 DEX 或 CHA ASI 的升级也立即重算，无需先换装。
20. S0-B Defense 仍在穿甲时 +1、未穿甲时 +0；Draconic 不与 Defense 叠成第二套公式。
21. 多护甲/多盾牌既有遏制、魔法盾同调门槛和 DEX cap 测试保持通过。
22. 战斗玩家 actor 的 AC 等于最新 derived AC；本任务没有修改战斗公式或 spell attack。

测试必须使用既有 Sorcerer 法术选择、升级和 live equipment fixture；不得绕过 1 级建卡限制，不得只调用新 helper 而不跑真实升级/重建/换装链路。

## 7. 停止条件

出现以下任一情况，停止实现并报告，不得自行扩大范围：

1. S0-A 或 S0-B 未合入，或没有可确认的前置最终提交。
2. 子职选择不再位于 `features.subclass_ref`，或目标等级代理无法表达即将选择的子职。
3. 升级 HP 在 preview 与 apply 中存在另一套隐式重算，导致无法只应用一次特性增量。
4. S0-B 尚未提供升级 AC 重算，或 AC 出现 `derive_armor_class()` 之外的第二权威。
5. 为通过测试必须放宽一级建卡限制、重写法术选择、修改战斗代码或做存量迁移。
6. 既有失败集发生变化但无法证明与基线环境有关；不要顺手修 `character_builder` 的既有环境失败。

## 8. 非目标

1. 不实现 Draconic Spells、Elemental Affinity、Dragon Wings、Dragon Companion。
2. 不实现 Barbarian / Monk Unarmored Defense；共享参数虽为其预留，本任务不添加这些职业声明。
3. 不实现 Tough、Defense 或其他专长的新效果；仅保持 S0-B Defense 回归。
4. 不实现多职业、基础 AC 的玩家手选 UI、动态战斗内换装或飞行。
5. 不修改 CON 回顾、 armor/shield 选择、法术攻击、伤害、资源/rest 或 RNG。
6. 不做存量角色补偿迁移，不改变 content_version，不新增存档版本。

## 9. 验收命令

使用前置任务相同的既有解释器，不新建 venv。Git Bash 下执行：

```bash
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_draconic_resilience.py -q --basetemp=.ptmp-s0c-focused
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_feature_choices.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_advancement_access.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_monk_equipment.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_resting.py -q --basetemp=.ptmp-s0c-regression
/c/Python314/Scripts/ruff check src/rulesets/dnd2024/features/models.py src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/character/derivation.py src/rulesets/dnd2024/character/reconciliation.py src/rulesets/dnd2024/progression/advancement.py tests/rulesets/test_dnd2024_draconic_resilience.py
/c/Python314/Scripts/ruff format --check src/rulesets/dnd2024/features/models.py src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/character/derivation.py src/rulesets/dnd2024/character/reconciliation.py src/rulesets/dnd2024/progression/advancement.py tests/rulesets/test_dnd2024_draconic_resilience.py
```

不得用删除、跳过或放松既有断言来换绿。若临时目录已存在，换一个新的任务专属 basetemp，不批量删除其他任务目录。

## 10. 执行报告模板（执行侧完成后追加）

```markdown
## 执行报告（YYYY-MM-DD）

- S0-A / S0-B 前置提交、本任务基线 / 分支 / 最终提交：
- 实际变更文件：
- 设计偏差及理由（没有则写“无”）：
- §6 测试矩阵：逐项列出 1–22 的通过情况，不得只写“测试通过”：
- 聚焦测试：命令、passed/failed/error 数：
- 回归测试：命令、passed/failed/error 数：
- ruff：结果：
- 既有失败与基线对比：
- 升级 HP 各分量与历史重建核对：
- AC 在建卡/升级/ASI/换装各入口的核对：
- 存档/API 兼容说明：
- 未解决问题（没有则写“无”）：
- 最终 git status 与 changed-file 白名单核对：
```
