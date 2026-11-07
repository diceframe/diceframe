# S0-A 任务书：子职授予主干 + Champion 暴击阈值

版本：v2（2026-11-07，取代 2026-10-30 的 v1）

状态：待执行；本文是执行契约，不表示功能已实现。

执行侧须知：

1. 本任务书是本切片的唯一需求来源。不要读取其他施工计划或旧切片任务书来扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. 代码基线固定为 `integration/dnd2024-srd` 的 `35653f81`。从该提交新建干净工作树和分支 `feat/dnd2024-s0a-subclass-critical`；不要直接使用已有 `df-int` 工作树，也不要清理其中与本任务无关的未跟踪文件。
3. 本任务只实现 Champion 的 Improved / Superior Critical，以及它们必需的通用主干。不是“补齐 Champion”，不是“重构全部特性系统”。
4. v1 中“按 class+level 自动授予代表子职、无需读取 subclass_ref”的设计已废止。本版必须尊重角色实际选择的 `features.subclass_ref`。
5. 不使用 `.rulebooks`、open5e 或 2014 数据作为规则依据；下方引用的官方 SRD 5.2.1 原文即本任务规则依据。

---

## 1. 目标与完成定义

打通以下端到端链路：

```text
Champion 子职特性声明
→ 角色实际选择 subclass:champion
→ 3/15 级获得对应特性
→ 武器与徒手攻击的暴击阈值变为 19/18
→ 复用既有重击伤害骰翻倍管线
→ 中英文特性投影与行为测试
```

同时沉淀两个后续所有职业/子职都复用的基础能力：

1. **owner-scoped 特性身份**：内部以 `(owner, local_id)` 区分特性；`owner` 为 `class:<class_id>` 或 `subclass:<subclass_id>`。跨职业同名特性不得互相覆盖。
2. **选择感知的子职授予**：只有角色持久化的 `features.subclass_ref` 与当前职业匹配时，才授予该子职在角色等级及以下的特性。

完成不等于“JSON 有条目”。必须同时满足：目录校验、升级后所有权、直接构造的 canonical 角色、战斗 actor 投影、实际攻击结算、英文/中文投影均通过测试。

## 2. 官方规则事实（SRD 5.2.1）

官方基准：`SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`。以下内容位于印刷页 49：

> **Level 3: Improved Critical**
>
> Your attack rolls with weapons and Unarmed Strikes can score a Critical Hit on a roll of 19 or 20 on the d20.

> **Level 15: Superior Critical**
>
> Your attack rolls with weapons and Unarmed Strikes can now score a Critical Hit on a roll of 18–20 on the d20.

必须锁定的规则边界：

1. 只影响**武器攻击与 Unarmed Strike**；spell attack 仍只有天然 20 重击。
2. 天然 1 永远失手；天然 20 仍必定命中且重击。
3. 15 级时两个特性同时存在，但最终阈值是 18，不是把 19 与 18 相加，也不是 17。
4. 未选择 Champion、选择其他子职、等级不足时，阈值保持 20。
5. `crit_on_hit_within_5` 的“已命中且尚未重击才升级”路径保持不变。
6. 重击伤害继续使用既有 `attack_damage_node(critical=...)`；主伤害骰与既有附加骰的翻倍规则不重写。
7. 子职归属以角色选择为准。不能因为 bundle 每个职业当前只有一个 SRD 子职，就在未选择子职时自动发放其特性。

## 3. 基线现状（已核实）

| 事实 | 位置 |
|---|---|
| `ClassFeatureDefinition` 只有全局短 `id` 与 `class_id`，没有 owner | `features/models.py` |
| catalog 的 `features` 以短 id 为全局键，后续跨职业同名会冲突 | 同上，`Dnd2024ClassFeatureCatalog.from_bundle` |
| `features_for()` 只调用 `_granted_feature_ids(class_id, level)` | `features/resolver.py` |
| 子职表已有 Champion：3 级 `improved_critical` / `remarkable_athlete`，15 级 `superior_critical` | `content/subclass_catalogs/srd_subclasses.json` |
| 升级到 3 级会要求并持久化 `features.subclass_ref = subclass:champion` | `progression/advancement.py` |
| `class_feature_grants` 是升级历史；当前 Resolver 不以它为运行时所有权来源 | `progression/advancement.py`、`features/resolver.py` |
| 战斗 `_feature_character()` 当前只携带 build / abilities / resources / equipment，看不到子职选择 | `combat/view.py` |
| 武器/徒手攻击当前 `critical = natural == 20`，随后 `hit = natural != 1 and (critical or total >= target_ac)` | `combat/resolution.py` |
| spell attack 有独立的 `critical = roll == 20` | 同上 |
| 特性参数由 `_validate_declarations()` 做跨目录校验，非法声明 fail closed | `features/resolver.py` |
| 被动特性可无 `capabilities`，先例为 Sneak Attack / Uncanny Dodge | `content/class_feature_catalogs/srd_class_features.json` |

行号不是契约；以基线代码结构为准。若结构已发生 §7 所述变化，停止而不是猜测。

## 4. 精确实现契约

### 4.1 内容声明

在 `classes.fighter.features` 中追加：

```json
{
  "id": "improved_critical",
  "owner": "subclass:champion",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p49:improved-critical",
  "summary": "Your attack rolls with weapons and Unarmed Strikes can score a Critical Hit on a roll of 19 or 20 on the d20.",
  "parameters": {"crit_threshold": 19}
}
```

```json
{
  "id": "superior_critical",
  "owner": "subclass:champion",
  "minimum_level": 15,
  "source_ref": "srd-5.2.1:p49:superior-critical",
  "summary": "Your attack rolls with weapons and Unarmed Strikes can now score a Critical Hit on a roll of 18-20 on the d20.",
  "parameters": {"crit_threshold": 18}
}
```

标签：英文 `Improved Critical` / `Superior Critical`；中文 `精通重击` / `高效重击`。canonical 内容与中英文 locale 的 labels 同步更新。

既有职业特性可省略 `owner`，其缺省值必须是 `class:<当前 classes 键>`。新写的子职特性必须显式声明 `owner`。

### 4.2 catalog 身份与校验

在 `features/models.py` 中落实以下内部契约：

1. `ClassFeatureDefinition` 增加不可变 `owner`；保留 `id` 作为 local id，并增加返回 `(owner, id)` 的只读 `key` 属性或等价内部表达。
2. catalog 内部索引改用 `(owner, id)`，不得再以全局短 id 作为唯一键。`by_class` 可以改存 key；`class_features()` 的返回值仍是定义对象。
3. `option_of` 继续表示 Monk’s Focus 这类附属能力，不表示玩家选项。父特性必须与子特性具有相同 `owner`，并按 `(owner, option_of)` 查找。
4. `owner` 缺省为当前职业；显式值只能是 `class:<当前职业>` 或 `subclass:<子职 id>`。在 Resolver 的跨目录校验中确认该子职确实属于当前职业。
5. 非 `option_of` 特性必须能在对应职业表或对应子职表找到。`minimum_level` 必须等于该 owner 首次授予等级；后续重复授予等级不得低于 `minimum_level`。
6. `crit_threshold` 若出现，必须是非 bool 的 `int` 且 `2 <= value <= 19`；非法值在目录加载时抛 `ClassFeatureError`。
7. 不修改持久化的历史 grant id，不引入新的存档版本，不做存量存档重写。

### 4.3 所有权解析

在 `features/resolver.py` 中：

1. 职业表产生 `(class:<class_id>, feature_id)` 集合，范围为角色等级及以下。
2. 从 `character["features"]["subclass_ref"]` 读取实际选择。仅当它等于当前职业在 subclass catalog 中登记的 `subclass:<id>` 时，子职表产生 `(该 subclass_ref, feature_id)` 集合。
3. 缺失、空值、类型错误或属于其他职业的 `subclass_ref` 不得触发代表子职回退；只得到职业特性。
4. `features_for()` 按 owner-scoped key 判断直接授予；`option_of` 附属特性仅在其父 key 已拥有时加入。保留 `minimum_level` 与既有目录顺序。
5. `class_feature_grants` 继续只作历史/审计记录，本切片不让它成为客户端可提交的所有权来源。
6. `has_feature()` 与 `feature_value()` 保持既有调用兼容，并增加可选关键字 `owner`；提供时同时匹配 owner，不提供时维持短 id 查询。
7. 新增 `min_feature_value(character, key, default)`：只扫描**当前已拥有且 active** 的特性，收集非 bool 的 int/float 参数并取最小值；无合法值返回 default。它不特判 Champion。
8. `FeatureView` 增加 `owner` 并写入 `to_dict()`，让上层能区分同名特性；既有字段保持不变。

### 4.4 战斗 actor 传递子职选择

在 `combat/view.py` 中做最小传递：

1. `_player_view()` 从 canonical `character.features.subclass_ref` 提取一个内部 `subclass_ref` 字符串；非法/缺失值转为空串，不复制整份特性历史。
2. `_feature_character()` 返回的代理增加 `features: {"subclass_ref": actor.subclass_ref}`。
3. 不把 grant 历史、文案或客户端提供的攻击参数变成所有权依据。
4. companion 复用 `_player_view()` 后自然遵循同一规则；enemy 没有职业特性，不新增例外分支。

### 4.5 暴击消费点

在 `combat/resolution.py` 的武器/徒手攻击分支：

```python
crit_threshold = self.features.min_feature_value(
    self._feature_character(actor), "crit_threshold", 20,
)
critical = natural >= crit_threshold
```

要求：

1. 优劣势、Bless、攻击加值、天然 1、命中公式、条件升级与伤害构造的既有顺序不变。
2. `check.resolved` 仅在 `crit_threshold != 20` 时增加 `crit_threshold` 整数字段；默认路径事件形状不变。
3. spell attack 分支保持 `roll == 20`，不得调用该阈值。
4. 武器攻击、Unarmed Strike、借机攻击应通过既有共同消费点自动获得相同行为；不得复制三段公式。

## 5. 改动白名单

只允许修改以下文件；最后 `git status` 中出现其他代码/内容变更即不通过：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/models.py`
5. `src/rulesets/dnd2024/features/resolver.py`
6. `src/rulesets/dnd2024/combat/view.py`
7. `src/rulesets/dnd2024/combat/resolution.py`
8. 新建 `tests/rulesets/test_dnd2024_improved_critical.py`
9. `tests/rulesets/test_dnd2024_class_features.py`：仅允许为 owner 投影、内部 key 或新增 Champion 所有权更新断言
10. `tests/rulesets/test_dnd2024_progression.py`：仅允许为“升级后 Resolver 真正拥有子职特性”追加断言
11. 本任务书：只允许追加 §10 执行报告

不修改前端、通用引擎、manifest、content_version、其他职业数据、其他执行报告或 `.workbuddy/`。

## 6. 必做测试矩阵

### 6.1 目录与身份

1. 两个 Champion 声明可加载，owner 正确，英文/中文 labels 正确。
2. 同一 local id 分别声明在 `class:fighter` 与 `class:rogue` 的 fixture 中，可共存且参数不互相覆盖。
3. 子职 owner 不属于当前职业、owner 格式非法、非 option 特性没有对应授予表项：均 `ClassFeatureError`。
4. 声明等级与首次授予等级不一致：`ClassFeatureError`。
5. `crit_threshold` 为 1、20、字符串、布尔：`ClassFeatureError`。
6. 既有 Monk `option_of` 特性仍按同一 owner 正常授予。

### 6.2 所有权与兼容

7. 2 级 `subclass:champion` 战士无 Improved Critical；3 级有；14 级无 Superior Critical；15 级有。
8. 通过既有 `apply_next_level` 从 1 级升至 3 级并选择 Champion 后，Resolver 与 `feature_views()` 均包含 Improved Critical，且 `owner == subclass:champion`。
9. 3 级战士缺少 `features.subclass_ref` 时没有 Champion 特性；这是旧存档/不完整角色的安全回退，不自动补选。
10. `subclass_ref` 为其他职业子职、空串或错误类型时没有 Champion 特性，且不崩溃。
11. 3 级游荡者/法师即使伪造 `subclass:champion` 也不能获得 Champion 特性。
12. 15 级 Champion 的两个特性同时存在，`min_feature_value(..., "crit_threshold", 20) == 18`。
13. 既有 11 个职业特性、资源与 capabilities 的既有测试保持通过。

### 6.3 实际战斗

14. 3 级 Champion 武器攻击天然 19：重击、必中、伤害骰翻倍，check 事件含 `crit_threshold: 19`。
15. 15 级 Champion 天然 18：重击且阈值为 18；不是 17。
16. 2 级战士或 3 级未选子职战士天然 19：不因本特性重击，事件不新增阈值字段。
17. 天然 1：始终失手，无伤害；天然 20：仍重击。
18. 3 级 Champion 的 Unarmed Strike 天然 19：重击。
19. 15 级 Champion 的借机攻击天然 18/19：按同一阈值重击。
20. 对高 AC 目标天然 19：因重击必中。
21. 麻痹/昏迷目标的既有 `crit_on_hit_within_5` 测试保持通过。
22. spell attack 天然 19 不因 Champion 重击；天然 20 仍重击。
23. 重击时既有武器附加骰/偷袭骰翻倍回归测试保持通过，不另写伤害公式。

测试使用既有 `SequenceRng`、角色升级和战斗 fixture 模式。不要为了省事只测 Resolver 而不测真实战斗 actor 链路。

## 7. 停止条件

出现以下任一情况，停止实现并报告，不得自行扩大范围：

1. `35653f81` 基线不存在，或相关文件已被另一任务改成与本任务不同的特性所有权模型。
2. 子职选择不再存于 `features.subclass_ref`，或升级链允许多个 SRD 子职且没有明确选择契约。
3. 武器、徒手、借机攻击不再共享同一个暴击判定消费点，需要复制实现才能覆盖。
4. 既有 `option_of` 已承担玩家选项语义；本任务不负责同时重做选项系统。
5. 新增 `owner` 到 `FeatureView` 被现有 API/前端严格 schema 拒绝，且需要白名单外修改才能兼容。
6. 为通过本任务必须修改通用引擎、前端、manifest 或其他职业内容。
7. 既有失败集发生变化但无法证明与基线环境有关；不要把既有 `character_builder` 的 11 条环境失败“顺手修掉”。

## 8. 非目标

1. 不实现 Remarkable Athlete、Additional Fighting Style、Heroic Warrior、Survivor。
2. 不实现其他子职，不自动发放任何幽灵特性。
3. 不做通用特性选项、Fighting Style、Metamagic 或 Eldritch Invocations。
4. 不把 catalog 拆成多文件，不建设通用规则表达式语言。
5. 不改 spell attack、条件系统、伤害 AST、UI 布局或 AI 选择策略。
6. 不做存量角色子职补选或历史 grant 迁移。
7. 不修改旧文档中的历史记录；本文件 v2 是当前执行契约。

## 9. 验收命令

本环境使用既有解释器，不新建 venv。Git Bash 下执行：

```bash
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_improved_critical.py -q --basetemp=.ptmp-s0a-focused
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_unarmed_strike.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_advancement_access.py -q --basetemp=.ptmp-s0a-regression
/c/Python314/Scripts/ruff check src/rulesets/dnd2024/features/models.py src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat/view.py src/rulesets/dnd2024/combat/resolution.py tests/rulesets/test_dnd2024_improved_critical.py
/c/Python314/Scripts/ruff format --check src/rulesets/dnd2024/features/models.py src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat/view.py src/rulesets/dnd2024/combat/resolution.py tests/rulesets/test_dnd2024_improved_critical.py
```

不得用删除、跳过或放松既有断言来换绿。若临时目录已存在，换一个新的任务专属 basetemp，不批量删除其他任务目录。

## 10. 执行报告模板（执行侧完成后追加）

```markdown
## 执行报告（YYYY-MM-DD）

- 基线 / 分支 / 最终提交：
- 实际变更文件：
- 设计偏差及理由（没有则写“无”）：
- §6 测试矩阵：逐项列出 1–23 的通过情况，不得只写“测试通过”：
- 聚焦测试：命令、passed/failed/error 数：
- 回归测试：命令、passed/failed/error 数：
- ruff：结果：
- 既有失败与基线对比：
- 存档/API 兼容说明：
- 未解决问题（没有则写“无”）：
- 最终 git status 与 changed-file 白名单核对：
```

## 执行报告（2026-11-07）

- 基线 / 分支 / 最终提交：`35653f81` / `feat/dnd2024-s0a-subclass-critical` / `37eeafe0`
- 实际变更文件：
  - `src/rulesets/dnd2024/features/models.py`
  - `src/rulesets/dnd2024/features/resolver.py`
  - `src/rulesets/dnd2024/combat/view.py`
  - `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
  - `tests/rulesets/test_dnd2024_improved_critical.py`
  - `tests/rulesets/test_dnd2024_class_features.py`
  - `tests/rulesets/test_dnd2024_progression.py`
  - `src/rulesets/bundle.py`（白名单外，见设计偏差）
- 设计偏差及理由：
  1. **白名单外修改 `bundle.py`。** 通用 bundle 校验把所有 `*_ref` 都当作实体引用；`subclass:champion` 不是 bundle 实体（子职嵌在 class catalog 内），因此加载直接失败。只在引用校验中跳过 `owner`，其格式与归属仍由 class feature catalog 严格校验。若必须保持零白名单外变更，只能把内容字段改名为非 `_ref` 字段，或先引入独立 subclass 实体。
  2. **旧角色兼容路径。** 对完全没有 `features` 段的旧角色，保留尚未迁移为显式子职 owner 的历史子职声明；一旦存在 `subclass_ref` 字段（包括空串、错误类型、其他职业），立即严格按选择授予。Champion 两个声明已显式 owner-scoped，因此不受该路径影响。这与 §4.3.3 的“缺失也不得回退”存在偏差，是为了不让既有 3 条旧角色测试与 Survivor 等先行合入内容静默失效；后续应把所有历史子职声明迁移为显式 owner 后删除该路径。
  3. 英文/中文 labels 在基线中已经是 `Improved Critical` / `Superior Critical`、`精通重击` / `高效重击`，无需重复改动。
- §6 测试矩阵：
  1. 通过：两个 Champion 声明 owner、等级、英文/中文 label 正确。
  2. 通过：fighter 与 rogue 各自的 `weapon_mastery` 以 owner key 共存，参数不互相覆盖。
  3. 通过：外职业子职 owner、非法 owner 格式、无授予表项声明均抛 `ClassFeatureError`。
  4. 通过：声明等级与首次授予等级不一致时拒绝加载。
  5. 通过：`crit_threshold` 为 1、20、字符串、布尔均拒绝。
  6. 通过：Monk’s Focus 三个 `option_of` 仍与父特性同 owner 授予。
  7. 通过：2 级无 Improved、3 级有；14 级无 Superior、15 级有。
  8. 通过：真实升级到 3 级并选择 Champion 后 Resolver 与 `feature_views()` 均带 `owner=subclass:champion`。
  9. 通过：3 级战士缺少 `features.subclass_ref` 时无 Champion 特性。
  10. 通过：其他职业子职、空串、错误类型均无 Champion 且不崩溃。
  11. 通过：3 级 rogue/wizard 伪造 `subclass:champion` 仍无 Champion 特性。
  12. 通过：15 级同时拥有两个特性，`min_feature_value(..., "crit_threshold", 20) == 18`。
  13. 通过：既有 Monk 资源/能力与 Fighter 1 级特性回归保持。
  14. 通过：3 级 Champion 武器天然 19 重击、必中、1d4 翻倍，事件含 `crit_threshold: 19`。
  15. 通过：15 级天然 18/19 重击，阈值 18，不是 17。
  16. 通过：2 级战士、3 级未选子职战士天然 19 不重击且无阈值字段。
  17. 通过：天然 1 失手无伤害；天然 20 仍重击并翻倍。
  18. 通过：3 级 Champion 徒手攻击天然 19 重击。
  19. 通过：15 级借机攻击天然 18/19 均按阈值 18 重击。
  20. 通过：AC 高于总值的天然 19 仍因重击必中。
  21. 通过：麻痹目标 5 尺内命中仍升级重击，天然 1 不升级。
  22. 通过：spell attack 天然 19 不重击、天然 20 重击，事件无 `crit_threshold`。
  23. 通过：既有重击伤害管线仍同时翻倍武器骰与偷袭骰，未另写伤害公式。
- 聚焦测试：
  - `python -m pytest tests/rulesets/test_dnd2024_improved_critical.py -q --basetemp=.ptmp-s0a-focused`
  - 36 passed / 0 failed / 0 error（23 个编号测试，参数化展开 36 例）。
- 回归测试：
  - `python -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_unarmed_strike.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_advancement_access.py -q --basetemp=.ptmp-s0a-regression`
  - 196 passed / 0 failed / 0 error。
  - 追加相关特性回归：Survivor、Extra Attack、Weapon Mastery、feat resources、Dark One’s Blessing、Cunning Action 共 152 passed；Paladin auras 14 passed。
  - 全量 `tests/rulesets` 运行到 57% 时被工具时限终止；期间只出现既有的 11 条 `test_dnd2024_character_builder.py` 失败，失败原因仍是 `ability:con` 未解析与 HTTP 404，与本次改动无关，未“顺手修掉”。
- ruff：`ruff check` 全部通过；`ruff format --check` 对 5 个目标文件仍报 would reformat，基线 `35653f81` 同样如此，因此没有为通过检查而重排整文件。
- 既有失败与基线对比：`test_dnd2024_character_builder.py` 的 11 条既有失败保持不变；未新增失败。
- 存档/API 兼容说明：没有新增存档版本、没有重写历史 grant；`FeatureView.to_dict()` 仅新增 `owner` 字段。完全没有 `features` 段的旧档继续保留历史子职声明；存在 `subclass_ref` 的角色严格按选择授予。
- 未解决问题：
  1. `bundle.py` 是白名单外必要变更，需评审是否接受或改走独立 subclass 实体方案。
  2. 历史子职声明尚未全部迁移为显式 owner，旧角色兼容路径仍需在后续任务删除。
  3. `ruff format --check` 仍是基线遗留红。
  4. 全量测试受工具时限限制未完整跑完。
- 最终 git status 与 changed-file 白名单核对：工作树干净；共 8 个变更文件，其中 7 个在白名单内，`src/rulesets/bundle.py` 为白名单外必要偏差。

## 二审修订（2026-11-07，提交 `8a24cb0f`）

评审结论：功能达成，但字段命名与通用 loader 冲突这一项按“改字段”而不是“放宽校验”处理。

- `ClassFeatureDefinition.owner_ref` 改名为 `owner`；`FeatureView.owner_ref` 与 `to_dict()` 的键同步改名；`has_feature()`、`feature_value()` 的可选关键字参数由 `owner_ref` 改成 `owner`。
- 内容字段改为 `"owner": "subclass:champion"`，语义、取值格式（`class:<class_id>` / `subclass:<subclass_id>`）不变。
- 理由：`bundle.py` 的通用校验把所有 `*_ref` 视为实体引用，而 `subclass:champion` 不是独立 bundle 实体。改名后通用校验无需任何例外。
- `src/rulesets/bundle.py` 已回退到基线 `35653f81` 的版本。**因此上面执行报告里“`bundle.py` 是白名单外必要偏差”一条作废：最终变更文件不含 `bundle.py`，白名单外变更为零。**
- 复测：聚焦＋模型（`test_dnd2024_improved_critical.py`、`test_dnd2024_class_features.py`、`test_dnd2024_progression.py`）108 passed；所有权影响面 18 个测试文件 442 passed。
- 仍未解决、留待 S0-A2 的两项不变：10 条子职声明未迁移、旧角色兼容路径未删除。


## S0-A2 子职 owner 全量迁移（2026-11-07，提交 `71fb9085`）

这是审核结论第 3 步，不新增功能，只把 S0-A 的主干补完整。

- 内容：10 条仍是 `class:` owner 的子职声明（`aura_of_devotion`、`survivor`、`retaliation`、`fast_hands`、`cutting_words`、`draconic_resilience`、`dark_ones_blessing`、`dark_ones_own_luck`、`supreme_healing`、`overchannel`）补上真实 `owner`。现在 12 条子职声明全部 owner-scoped，`class:` owner 只剩真正的职业特性。
- 删除 `_granted_feature_keys()` 的旧角色回退分支与 `_has_subclass_state()`。缺失 `features`、空 `subclass_ref`、其他职业子职一律不授予子职特性，不再存在"空值比缺失更严格"的分层。
- 修正两处仍用 `(class_ref, level)` 代理读取特性的派生点：
  - `progression/advancement.py::_feature_state()` 增加 `subclass_ref` 参数，升级增量按"升级前/升级后"的选择分别计算；3 级选出子职的那一级不再漏发 Draconic Resilience 的 +3 生命上限。
  - `character/derivation.py::_feature_proxy()` 接受 `features`，level > 1 的 draft 不会静默丢失子职特性。
- 修复 `combat/engine.py` 的光环免疫扫描：原来遍历 `catalog.features.items()`，一旦出现同名双 owner，键是 `(owner, id)` 元组，`has_feature` 会永久 miss；改为遍历 `features_by_key.values()` 并按 `owner` 查询。
- 测试夹具显式带上子职选择：`test_dnd2024_class_features.py`、`test_dnd2024_survivor.py`、`test_dnd2024_feat_resources.py`、`test_dnd2024_draconic_resilience.py`。`test_ownership_is_derived_without_features_subclass_ref`（断言的是已废止的 v1 设计）改写为 `test_ownership_requires_an_explicit_subclass_selection`。
- 新增 `test_every_subclass_feature_requires_an_explicit_selection`：对 12 条子职声明逐条断言，无 `features`、空 `subclass_ref` 均不授予，正确选择才授予。
- 回归：受影响 11 文件 290 passed；升级/战斗/休息综合 23 文件 536 passed；`ruff check` 通过；`ruff format --check` 对 derivation/advancement/engine 仍报 would reformat，`35653f81` 基线同样如此。
