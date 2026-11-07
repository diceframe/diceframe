# S0-B 任务书：特性选项主干 + Champion Additional Fighting Style / Defense

版本：v1（2026-11-07）

状态：待执行；本文是执行契约，不表示功能已实现。

执行侧须知：

1. 本任务书是本切片的唯一需求来源。不要读取其他施工计划或旧切片任务书来扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. **前置依赖**：S0-A《子职授予主干 + Champion 暴击阈值》必须已经完成并合入 `integration/dnd2024-srd`。从 S0-A 执行报告读取最终提交，以该提交新建干净工作树和分支 `feat/dnd2024-s0b-feature-choices`。不要猜提交号，不要直接使用已有 `df-int` 工作树。
3. 本任务只实现“新获得一个带选项的特性时，要求并持久化一个合法选择”，用 Champion 7 级 Additional Fighting Style 选择 Defense 验收。不是补齐战斗风格，不是重做建卡，不是设计通用规则 DSL。
4. 不使用 `.rulebooks`、open5e 或 2014 数据作为规则依据；下方引用的官方 SRD 5.2.1 原文即本任务规则依据。
5. S0-A 已建立的 `(owner_ref, local_id)` 特性身份是本任务的硬前置。选项必须绑定完整 owner，不得退回全局短 id。

---

## 1. 目标与完成定义

打通以下端到端链路：

```text
Champion 6→7 级
→ 获得 subclass:champion / additional_fighting_style
→ preview 返回 Fighting Style 专长选项 requirement
→ 提交 feat:defense
→ 持久化特性选项与 feat:defense 所有权
→ 穿 Light / Medium / Heavy Armor 时 AC +1
→ 未穿甲不加成，换装重算不丢失
→ 升级历史重建与中英文投影保持一致
```

同时沉淀后续特性选项复用的最小主干：

1. **声明与选择分离**：catalog 声明某特性的选项类别；角色只持久化 canonical ref，不持久化本地化名称。
2. **owner-scoped 选项身份**：`(owner_ref, feature_id, choice_id)` 唯一确定一个选项槽。
3. **服务器校验后授予**：只有当前等级真实获得的特性才能提交对应选择；不能借 `feature_choices` 或 `feat_refs` 输入凭空获得专长。
4. **选项与效果分离**：Fighting Style 的选择管线不负责写 Defense 公式；Defense 的 AC 效果由既有唯一 AC 推导函数消费。

完成不等于“能提交一个 feat_ref”。必须同时覆盖 preview、apply、历史重建、Resolver 投影、非法输入、AC 推导和 live 装备 reconciliation。

## 2. 官方规则事实（SRD 5.2.1）

官方基准：`SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`。

Champion，印刷页 49：

> **Level 7: Additional Fighting Style**
>
> You gain another Fighting Style feat of your choice.

Defense，印刷页 88：

> **Defense**
>
> Fighting Style Feat (Prerequisite: Fighting Style Feature)
>
> While you’re wearing Light, Medium, or Heavy armor, you gain a +1 bonus to Armor Class.

必须锁定的规则边界：

1. Additional Fighting Style 在 Champion 7 级授予一次，要求选择一个 Fighting Style feat。
2. 本切片只实现 `feat:defense` 的完整效果。Archery、Great Weapon Fighting、Two-Weapon Fighting 可以出现在选项投影中，但必须显式标记 `effect_not_implemented`、不可提交、不可持久化；不得制造“已选择但没有效果”的假完成。
3. Defense 只在实际穿着 `item_type == "armor"` 的 Light、Medium 或 Heavy Armor 时提供 +1 AC。
4. 未穿甲、只持盾、把护甲放在背包但未装备时均不加成。
5. 多件非法同时装备的护甲仍按既有规则取一件合法基础值，Defense 只加一次；重复 `feat:defense` 不叠加。
6. 本任务不改变盾牌、魔法护甲、Unarmored Defense、DEX cap 或既有 AC 公式。
7. “另一个 Fighting Style”不允许选择角色已经拥有且 `repeatable: false` 的同一专长。

## 3. 基线现状（已核实）

| 事实 | 位置 |
|---|---|
| 子职表已有 Champion 7 级 `additional_fighting_style` | `content/subclass_catalogs/srd_subclasses.json` |
| S0-A 后 Resolver 按实际 `subclass_ref` 和 owner-scoped key 授予子职特性 | `features/models.py`、`features/resolver.py` |
| `ClassFeatureDefinition` 当前没有玩家选项声明；`option_of` 只表示 Monk’s Focus 附属能力 | `features/models.py` |
| `preview_next_level()` 已用 `requirements` 描述 subclass、ASI、专长和法术选择 | `progression/advancement.py` |
| `apply_next_level()` 已把提交 choices 写入 progression history，并持久化 `features.feat_refs` | 同上 |
| Fighting Style 四个专长已存在于成长专项目录，均为 `category: fighting_style`、`repeatable: false` | `content/advancement_feat_catalogs/srd_advancement_feats.json` |
| `_feat_options()` 已处理等级、重复拥有、属性和施法前提 | `progression/advancement.py` |
| 建卡和 live 换装都调用唯一的 `derive_armor_class()` | `character/derivation.py`、`character/reconciliation.py` |
| 升级后的 `_update_derived()` 当前不重算 AC | `progression/advancement.py` |
| Defense 只有名称和来源元数据，没有 AC 效果声明 | 成长专项目录 |
| 现有 `feat_choice_answers` 是建卡时背景/物种专长内部选项，不等于职业特性选项 | `character/builder.py`、`character/validation.py` |

行号不是契约。若 S0-A 合入后的结构与上述边界不同，按 §7 停止，不要另起一套所有权模型。

## 4. 精确实现契约

### 4.1 特性选项声明

在 `classes.fighter.features` 中追加：

```json
{
  "id": "additional_fighting_style",
  "owner_ref": "subclass:champion",
  "minimum_level": 7,
  "source_ref": "srd-5.2.1:p49:additional-fighting-style",
  "summary": "You gain another Fighting Style feat of your choice.",
  "choice": {
    "id": "fighting_style_feat",
    "kind": "advancement_feat",
    "category": "fighting_style",
    "count": 1,
    "enabled_option_refs": ["feat:defense"]
  }
}
```

英文 label：`Additional Fighting Style`；中文 label：`额外战斗风格`。canonical 内容与中英文 locale labels 同步更新。

`choice` 是本切片新增的唯一玩家选项声明字段，遵守以下约束：

1. 一个特性最多声明一个 `choice`；`id` 使用 canonical local id。
2. 本切片只支持 `kind: advancement_feat` 和 `count: 1`。未知 kind、其他 count、缺 category、未知 category、重复 choice id 均 fail closed。
3. `enabled_option_refs` 是本切片的显式可执行白名单：必须是非空、无重复的 canonical ref 数组，且每个 ref 都属于声明 category；本任务只能包含 `feat:defense`。未列入的目录选项仍用于 UI 投影，但不可提交。
4. `option_of` 语义完全不变，不得用它表示 Fighting Style 选择。
5. 选项身份是 `(owner_ref, feature_id, choice.id)`；不得只用 `feature_id` 或 `choice_id` 作为全局键。
6. catalog 加载和 Resolver 跨目录校验须确认 `additional_fighting_style` 的首次授予等级确为 Champion 7 级，并确认声明的 feat category 存在。

### 4.2 提交与持久化形状

`preview_next_level(payload, choices)` 新增可选输入：

```json
{
  "feature_choices": [
    {
      "owner_ref": "subclass:champion",
      "feature_id": "additional_fighting_style",
      "choice_id": "fighting_style_feat",
      "selected_refs": ["feat:defense"]
    }
  ]
}
```

要求：

1. `feature_choices` 必须是数组；每项只接受上述四个字段。`selected_refs` 本切片必须是长度 1、无重复的字符串数组。
2. preview 对当前等级新获得且带 `choice` 的特性生成 requirement：
   - `id`: `feature_choice:subclass:champion:additional_fighting_style:fighting_style_feat`
   - `kind`: `feature_choice`
   - `required`: `true`
   - `owner_ref` / `feature_id` / `choice_id`: 上述 canonical 值
   - `count`: 1
   - `options`: 复用成长专项目录的 Fighting Style 选项投影，保留 `value`、`name`、`source_ref`、`available`、`unavailable_reasons`；不在 `enabled_option_refs` 中的选项必须 `available: false`，且 `unavailable_reasons` 含 `effect_not_implemented`
3. 未提交、提交空数组、选择多个、选择未登记专长、选择不可用专长、owner/feature/choice 不匹配，都使 `preview["ok"] == false` 并给出稳定错误；apply 抛既有 `ProgressionCatalogError`。
4. 非当前获得等级提交该选择、重复提交同一选项槽、提交未知选项槽，均拒绝。不得“忽略多余字段/多余选择”。
5. 已持久化的旧选择自动保留；当前没有新选项特性时不要求重复提交。若已有选项记录损坏，不得用它扩大所有权。
6. `preview` 和 `apply` 仍然不修改输入 payload 或 choices。

apply 成功后写入两处 canonical 状态：

```json
"features": {
  "feat_refs": ["feat:defense"],
  "feature_choices": [
    {
      "owner_ref": "subclass:champion",
      "feature_id": "additional_fighting_style",
      "choice_id": "fighting_style_feat",
      "selected_refs": ["feat:defense"],
      "granted_level": 7
    }
  ]
}
```

1. `feature_choices` 保存选项及来源等级；`feat_refs` 保存实际专长所有权，供既有专长前提和被动效果使用。
2. `feat_refs` 追加前去重；不得由调用方直接提交 `feat_refs` 或带 `granted_level` 的记录来绕过校验。
3. `preview["diff"]` 增加规范化后的 `feature_choices` 与新增 `feat_refs`，供升级历史和审计使用。
4. progression history 继续保存原始提交 choices 和 diff；高等级角色从历史重建时必须重放同一校验与授予逻辑。
5. 不修改 `class_feature_grants` 的历史 id，不新增存档版本，不做旧存档迁移。

### 4.3 Resolver 投影

在 `features/models.py` / `features/resolver.py` 中：

1. 为选项声明增加不可变类型或在 `ClassFeatureDefinition` 中增加等价的只读 `choice` 表达。
2. Resolver 只把与当前已拥有特性的完整 `(owner_ref, feature_id, choice_id)` 匹配、且引用属于声明 category 的记录投影为有效选择。
3. 缺失、重复、错误 owner、错误 choice、错误类型或未知 ref 的记录不崩溃、不显示为有效选择、不授予额外特性。
4. `FeatureView` 增加可选 `selection`。对声明了 choice 的特性，投影形状为：

```json
"selection": {
  "choice_id": "fighting_style_feat",
  "selected_refs": ["feat:defense"]
}
```

未选择时 `selected_refs` 为空数组；无 choice 的既有特性不新增 `selection` 字段，避免无关 API 形状变化。

5. Resolver 不把客户端提交的 `feature_choices` 当作新的特性授予来源；父特性仍只来自职业/子职表。
6. `has_feature()`、`feature_value()`、`min_feature_value()` 的既有行为不变。

### 4.4 Defense 被动效果声明与 AC 消费

在成长专项目录的 `defense` 条目追加：

```json
"effects": [
  {"kind": "armor_class_bonus", "value": 1, "requires_armor": true}
]
```

本切片只消费 `armor_class_bonus`，不为其他专长发明效果字段。

1. `derive_armor_class()` 增加可选关键字 `feat_refs`，默认空集合，保持既有调用兼容。
2. 当有效 `feat_refs` 含 `feat:defense` 且本次推导确实选中了 armor 时，最终结果加 1；无 armor 时加 0。
3. 加成读取上述声明，不在公式里写散落的魔法数字；重复 ref 不叠加。
4. 保持既有 armor/shield 选择和告警顺序。不要用“是否出现 shield”判断 Defense 是否生效。
5. `character/reconciliation.py` 调用 AC 推导时传入 canonical `features.feat_refs`，确保穿脱护甲后 Defense 不丢失，也不会在未穿甲时残留。
6. 升级 `_update_derived()` 使用 canonical 已装备 `equipment.item_refs` 与更新后的 `feat_refs` 重算 `derived.armor_class`，保证选择 Defense 的当次升级立即生效。
7. 建卡路径只通过默认参数保持现状；本任务不开放一级 Fighting Style 建卡选择，不修改 builder 的 draft/schema。

## 5. 改动白名单

只允许修改以下文件；最后 `git status` 中出现其他代码/内容变更即不通过：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `templates/rulesets/dnd2024_srd/content/advancement_feat_catalogs/srd_advancement_feats.json`
5. `src/rulesets/dnd2024/features/models.py`
6. `src/rulesets/dnd2024/features/resolver.py`
7. `src/rulesets/dnd2024/progression/advancement.py`
8. `src/rulesets/dnd2024/character/derivation.py`
9. `src/rulesets/dnd2024/character/reconciliation.py`
10. 新建 `tests/rulesets/test_dnd2024_feature_choices.py`
11. `tests/rulesets/test_dnd2024_progression.py`：仅允许为历史重建与既有 requirement 顺序追加/更新必要断言
12. `tests/rulesets/test_dnd2024_class_features.py`：仅允许为新增 Champion 特性和 selection 投影更新精确列表断言
13. `tests/rulesets/test_dnd2024_live_equipment_reconciliation.py`：仅允许追加 Defense 换装回归
14. 本任务书：只允许追加 §10 执行报告

不修改前端、通用引擎、builder draft、manifest、content_version、其他职业/子职数据、其他执行报告或 `.workbuddy/`。如确需新增独立 `features/choices.py`，必须先停止并记录设计偏差，不得悄悄扩大白名单。

## 6. 必做测试矩阵

### 6.1 声明与目录校验

1. Additional Fighting Style 可加载，owner 为 `subclass:champion`，等级为 7，中英文 label 正确。
2. choice 的 id/kind/category/count 解析正确；`option_of` 的 Monk 既有行为不变。
3. 未知 choice kind、count 不为 1、缺 category、未知 category、非法 choice id、enabled refs 为空/重复/跨 category 均 `ClassFeatureError`。
4. choice 所在特性的 owner 不属于当前职业、声明等级与授予表不一致时，沿用 S0-A 的 fail-closed 行为。
5. Defense 的 effect 声明为 +1 且要求 armor；既有其他成长专长不获得该效果。

### 6.2 preview 与输入校验

6. 未选 Champion 的 6 级战士升 7 级时不产生 Additional Fighting Style requirement；伪造该 feature choice 被拒绝。
7. Champion 6→7 preview 产生且只产生一个本切片 feature choice requirement，包含四个 Fighting Style 选项及中英文名称。
8. 缺省 choices、`feature_choices: []`、缺 `selected_refs`、选择两个专长均 `ok == false`。
9. owner_ref 改为 `class:fighter`、feature_id 改为其他特性、choice_id 错误、提交未知槽均拒绝。
10. 选择 `feat:grappler`、不存在的 `feat:unknown`、非字符串 ref、重复 ref 均拒绝。
11. 角色已拥有 `feat:defense` 时，该选项带 `already_owned` 且不可用；提交它被拒绝。
12. 5→6 或 7→8 时提交 Additional Fighting Style 选择被拒绝；已保存选择不会在后续等级要求重交。
13. preview 不修改输入 character/choices；非法 preview 不产生任何部分授予。

### 6.3 apply、所有权与历史

14. Champion 6→7 选择 Defense 后，Resolver 拥有 `additional_fighting_style`，`selection.selected_refs == ["feat:defense"]`，canonical `feat_refs` 含且只含一个 `feat:defense`。
15. 未选择时 apply 抛 `ProgressionCatalogError`；角色等级、feat_refs、feature_choices、HP 和 history 均不改变。
16. Archery、Great Weapon Fighting、Two-Weapon Fighting 均显示 `effect_not_implemented` 且不可用；提交其中任一项都被拒绝，feat_refs 与 feature_choices 不改变。后续升级不会重复授予 Defense。
17. 从高等级创建提交经既有 `normalize_submission` / advancement history 重建后，选项、feat_refs、selection 和 AC 与逐步升级结果一致。
18. 伪造 feature choice 记录为其他 owner、其他子职或未知 feat 时，Resolver 不显示有效 selection，也不新增特性。
19. S0-A 的 Improved Critical 与既有职业特性、资源、capabilities 测试保持通过。

### 6.4 Defense AC 与 live 换装

20. 未穿甲时，有无 Defense 的 AC 相同；只装备盾牌时仍无 Defense 加成。
21. 穿着 Light、Medium、Heavy Armor 各一例时，Defense 使对应既有 AC 恰好 +1。
22. 护甲在背包但未装备时不加；装备后立即加；脱下后立即移除。
23. live reconciliation 在“装备护甲 → 卸下 → 再装备”后，canonical `derived.armor_class` 与 legacy `armor_class` 始终一致，Defense 不丢失、不重复。
24. 重复 `feat:defense` 或重复装备非法多护甲时不重复叠加 Defense；既有“取一件合法护甲”告警行为不变。
25. 盾牌、魔法盾同调门槛、DEX cap、无甲和其他既有 AC 测试保持通过。
26. 升级选择 Defense 的同一 `apply_next_level()` 返回中 AC 已更新，不需要等下一次装备变化才生效。

测试使用既有角色升级、canonical 角色和 live equipment fixture 模式。不得只测 `derive_armor_class(feat_refs=...)` 而跳过 preview/apply/history/reconciliation 链路。

## 7. 停止条件

出现以下任一情况，停止实现并报告，不得自行扩大范围：

1. S0-A 尚未合入，或没有可确认的最终提交；实际代码仍使用全局短 id 作为唯一特性身份。
2. Champion 选择不再持久化于 `features.subclass_ref`，或 `additional_fighting_style` 不在 7 级授予表。
3. 现有 advancement history 重建不是通过重新调用本任务的 preview/apply 逻辑，导致需要另写一套选项重放器。
4. 为兼容现有 API 必须修改前端、通用引擎或 builder draft 才能完成本切片。
5. AC 不再由 `derive_armor_class()` 统一计算，或升级/换装需要各自复制一套 Defense 公式。
6. 合法实现要求同时开放一级建卡、多选选项、选项替换或 Blessed Warrior 才能避免破坏既有行为。
7. 既有失败集发生变化但无法证明与基线环境有关；不要顺手修既有 `character_builder` 环境失败。

## 8. 非目标

1. 不实现一级 Fighter 的 Fighting Style 建卡选项，也不开放 Ranger、Paladin 的同类选项。
2. 不实现 Paladin Blessed Warrior、Cleric Divine Order、Druid Primal Order。
3. 不实现 Archery、Great Weapon Fighting、Two-Weapon Fighting 的攻击或伤害效果。
4. 不实现 Metamagic、Eldritch Invocations、多选选项、选项替换或升级时改选。
5. 不实现 Champion 的 Remarkable Athlete、Heroic Warrior、Survivor；S0-A 范围外的特性不补。
6. 不重做背景/物种专长的 `feat_choice_answers`，不把两套选项强行合并成新 DSL。
7. 不修改 spell attack、战斗 RNG、装备槽、前端 UI、AI 选择策略或存档版本。

## 9. 验收命令

使用 S0-A 工作树相同的既有解释器，不新建 venv。Git Bash 下执行：

```bash
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_feature_choices.py -q --basetemp=.ptmp-s0b-focused
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_unarmed_strike.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_advancement_access.py -q --basetemp=.ptmp-s0b-regression
/c/Python314/Scripts/ruff check src/rulesets/dnd2024/features/models.py src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/progression/advancement.py src/rulesets/dnd2024/character/derivation.py src/rulesets/dnd2024/character/reconciliation.py tests/rulesets/test_dnd2024_feature_choices.py
/c/Python314/Scripts/ruff format --check src/rulesets/dnd2024/features/models.py src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/progression/advancement.py src/rulesets/dnd2024/character/derivation.py src/rulesets/dnd2024/character/reconciliation.py tests/rulesets/test_dnd2024_feature_choices.py
```

不得用删除、跳过或放松既有断言来换绿。若临时目录已存在，换一个新的任务专属 basetemp，不批量删除其他任务目录。

## 10. 执行报告模板（执行侧完成后追加）

```markdown
## 执行报告（YYYY-MM-DD）

- S0-A 前置提交 / 本任务基线 / 分支 / 最终提交：
- 实际变更文件：
- 设计偏差及理由（没有则写“无”）：
- §6 测试矩阵：逐项列出 1–26 的通过情况，不得只写“测试通过”：
- 聚焦测试：命令、passed/failed/error 数：
- 回归测试：命令、passed/failed/error 数：
- ruff：结果：
- 既有失败与基线对比：
- 存档/API 兼容说明：
- 未实现选项的明确清单：
- 未解决问题（没有则写“无”）：
- 最终 git status 与 changed-file 白名单核对：
```
