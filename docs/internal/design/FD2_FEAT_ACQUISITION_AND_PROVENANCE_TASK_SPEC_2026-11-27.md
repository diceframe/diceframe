# FD2 任务书：专长的取得、归属与前置（provenance + 池 + requires）

- 日期：2026-11-27（2026-11-28 修订 §3/§4/§6，把 provenance 定为派生视图）
- 状态：**FD2a 已实现**（`requires` 结构化，见 §8，随 PR #902）；**FD2b 进行中**
- 上游依据：`FEAT_DOMAIN_SURVEY_2026-11-26.md` §3、GAP-53/55
- 前置：FD1 已合（PR #889）；FD2a/FD6a 随 PR #902
- 边界：不含"合并两个专长目录"（属 FD3），不含任何专长的实际效果

## 1. 问题陈述（已逐点核实）

### 1.1 `feat_refs` 是扁平字符串，来源信息在派生时被丢掉

`character/derivation.py:609-619` 把三个来源拼成一个列表再 `dict.fromkeys` 去重：

```
background_feat_refs  ← background["feat_ref"]
species_feat_refs     ← draft["species_feat_refs"]
class_feature_feat_refs ← draft["class_feature_feat_choices"]["fighting_style"]
                        （且仅当 "fighting_style" in class_entity["level_1_feature_ids"]）
feat_refs = list(dict.fromkeys([...]))
```

由此产生三个具体后果：

1. **来源不可追溯**：`features.feat_refs` 只有 `"feat:alert"`，读者无法回答"这是背景给的还是 4 级选的"。
2. **重复被静默吞掉**：`dict.fromkeys` 让"背景给 Alert + 种族也给 Alert"变成一条，且不报错。
3. **升级选的专长根本不在这条链上**：第四个来源（`progression.history[i].choices.feat_ref`）不在拼接里——
   它是通过 `progression` 写回 `features` 的另一条路径进来的，两条路径各自去重，合并点不明确。

值得注意的是，**等级信息已经存在**：`progression.history` 每级一条，含 `choices`。因此 provenance
不需要新存档字段，只需要在派生时从 history 还原来源——这是本任务书最重要的一条判断（见 §3.1）。

### 1.2 前置条件是散落的 if，且两处判据不一致

`progression/advancement.py::_feat_options`（1887-1921）用四个 if 判前置：

| 判据 | 依据 | 后果 |
|---|---|---|
| `target_level < minimum_level` | `"level"` | — |
| 已拥有且 `not repeatable` | `"already_owned"` | 依据是 `feat_refs`（扁平，见 1.1） |
| `any_ability_minimum` 有任一项达标 | `"ability_prerequisite"` | **"任一达标"**，不是"全部达标"，易误读 |
| `requires_spellcasting` 且无 `slot_profile` | `"spellcasting"` | — |

而 `requires_feature: "fighting_style"` 在四个战斗风格上都有声明，`_feat_options` **完全不读它**——
它只在 `character/validation.py:289` 被读一次（建卡期那条战斗风格特例通道）。同一前置两个判据、两处实现，
其中一处不完整：升级到 4 级时，一个没有战斗风格槽的职业可以在 `general` 池里看到 Archery 吗？

实测结论：`_feat_options("general", ...)` 按 `category == "general"` 过滤，Archery 的 category 是
`fighting_style`，所以看不到。**当前不构成 bug，但它靠的是"category 恰好不同"这个巧合**，
而不是"前置被判定"。一旦有专长跨 category（Dual Wielder 允许选战斗风格是常见家规），这个巧合就破了。

### 1.3 池的现状：两个专长目录互不相通

| 目录 | 条数 | 谁能看见 |
|---|---|---|
| `advancement_feat_catalogs/srd_advancement_feats.json` | 13（ASI / Grappler / 4 风格 / 7 恩惠） | 升级路径 `_feat_options` |
| `content/feats/*.json` | 7 文件（Alert / Lucky / Skilled / Savage Attacker / 3 × Magic Initiate） | 只有背景 `feat_ref` 与按 id 引用 |

所以"升级路径没有 origin 池"更准确的说法是：**origin 专长根本没有进池**，因为它们不在被遍历的那个目录里。
这不是加一个 `origin` category 就能解决的，得先合并目录（FD3）。本片只做"给定条目已在池中之后"的事。

## 2. 决策点

### 2.1 同一专长被两个来源给出时怎么办 —— 已定：A（2026-11-27 批准）

现状：`dict.fromkeys` 静默去重，两行变一行，无告警。三个选项：

| 方案 | 行为 | 代价 |
|---|---|---|
| **A 按 `repeatable` 判定，否则拒绝**（推荐） | `repeatable: false` 的专长出现在两个来源 → 加载期/取得期拒绝，报错列出两个来源；`repeatable: true` 允许并存 | 需要把"取得"做成显式操作（不能靠列表去重）；旧档若已有重复需一次性清理 |
| B 保留现状的静默去重，只在派生视图里保留第一个来源 | 零迁移 | "来源"这个词就失去意义——记下的来源可能是错的那个 |
| C 全部允许并存，重复即叠加 | 与 SRD 冲突（除 repeatable） | 会让 Alert 的先攻加值叠两次 |

**决定：A。** 它同时回答了 §1.1 的第 2 条：`dict.fromkeys` 本身就是"用一个不表达规则的数据结构
掩盖了一个规则问题"。B 与 C 否决。

A 的一条落地细节：拒绝发生在 `acquire()`（取得期），**不是**在派生期。派生期只做归一
（`dict.fromkeys` 的替代品），不负责判合法性——派生被重放与审计反复调用，在那里抛错会让
一张旧档永远打不开。

### 2.2 `requires` 声明化：现在做还是随 FD1 的 `grants` 一起 —— 已改判为"现在就迁"（2026-11-27）

四类前置里，`minimum_level` / `repeatable` 是条目属性（保留为顶层字段），`ability_minimum` /
`requires_feature` / `requires_spellcasting` 是真正的"前置表达式"。后者应该合并成：

```
requires: [
  {"kind": "ability_minimum", "any_of": {"str": 13, "dex": 13}},
  {"kind": "feature", "id": "fighting_style"},
  {"kind": "spellcasting"},
]
```

**改判（2026-11-27，第二次）**：原定"只落形状、数据随 FD1 的 `grants` 一起迁"，理由是
`requires` 与 `grants` 是同一对前提/结果。实际做下来这个理由不成立——迁移面只有 6 条，
而"两处判据不同"是**正在发生的分歧**（升级路径不读 `requires_feature`，建卡路径不读属性与
施法），多等一片就多一片时间让两条路径继续分叉。已在本片迁完，见 §8。

**原决定（已被上条取代）**：本片只落形状与校验，不迁数据**：加载期接受 `requires` 列表并 fail closed 未知 kind，
`_feat_options` 仍读旧字段（两者并存一个版本），数据迁移留给 FD1 的 `grants` 一起做——
因为 `requires` 与 `grants` 是同一对"前提/结果"，分开迁会让同一批条目改两遍。

如果否决推迟，则本片必须同时改 `_feat_options` 的四条 if 与 `character/validation.py:289`，
并把五个战斗风格的 `requires_feature` 迁进 `requires`；验收矩阵相应加 4 条。

## 3. 契约

### 3.1 provenance 是派生视图：纯函数，不落盘

`features.feat_refs` **保持 `list[str]` 不变**。它是 persisted 字段（`derivation.py` 的输出写进
`ruleset_character`，`reconciliation.py` 也读它），改元素类型就是改存档形状；而来源本来就能从
现存字段还原，没有理由付迁移的代价。

```
features/feat_provenance.py

@dataclass(frozen=True, slots=True)
class FeatProvenance:
    ref: str
    source_kind: str        # background / species / class_feature / advancement / unknown
    source_ref: str
    level: int | None

def feat_provenance(bundle, build, features, progression) -> tuple[FeatProvenance, ...]
```

每个来源对着一个现存字段：

| source_kind | 读取位置 | source_ref | level |
|---|---|---|---|
| background | `build.background_ref` → background 实体的 `feat_ref` | background_ref | None |
| species | `build.species_feat_refs` | species_ref | None |
| class_feature | `build.class_feature_feat_choices`（1 级槽，当前只有 `fighting_style`） | class_ref | 1 |
| class_feature | `features.feature_choices[]`（子职/职业特性槽，含冠军 7 的 `additional_fighting_style`） | record.owner | record.granted_level |
| advancement | `progression.history[].diff.advancement_feat_ref` | `level:<to_level>` | to_level |
| unknown | `features.feat_refs` 中没有可还原来源的 ref（旧档、手工注入） | `""` | None |

**升级专长在 `history[].diff.advancement_feat_ref`，不在 `choices` 里**（`advancement.py:468`）。
初版写"取自 history"没有点明键名，按这条落地。

`unknown` 是第五个取值，只在还原期出现：旧档照常打开，不把"来源查不到"判成损坏。

消费者：

- `derivation.py` 用 `feat_provenance` 的 `ref` 序列替代三来源拼接 + `dict.fromkeys`；
- `_passive_armor_class_bonus` / Alert 仍按 ref 取值，签名不变；
- `_feat_options` / `FeatContext.owned_refs` 继续用 `features.feat_refs`（str 列表）；
- 需要"这条专长哪来的"时调用 `feat_provenance(build, features, progression)`，不落盘。

### 3.2 取得是一个显式操作

`features/feat_provenance.py` 同时提供：

```
def acquire(existing: Sequence[FeatProvenance], ref: str,
            source: FeatProvenance, *, repeatable: bool) -> tuple[FeatProvenance, ...]
```

拒绝条件（fail closed，错误消息列出两个来源）：

- `not repeatable` 且 `ref` 已存在；
- 同一 `(source_kind, source_ref)` 已有同一 `ref`——同一级不能选两次（`repeatable` 也一样）。

调用点：

1. **建卡**：`character/validation.py` 在背景 + 物种 + 1 级槽都组装好之后，对每个 grant 依次
   `acquire`，重复即报错；
2. **升级**：不新增调用点。`progression/choices.py::_advancement_feat` 的 `already_owned`
   （FD2a 的 `feat_unavailable_reasons`）已经挡住"再选一次"，它在取得前判定，语义与 `acquire`
   相同；在写回处再包一层只会得到第二个实现，而它永远返回"通过"。`advancement.py` 的两条
   写回路径保留 `if ref not in owned`，理由是它们负责**写入**而不是判定。

派生期（`derivation`）**不调用** `acquire`：它被重放与审计反复执行，在那里抛错会让一张旧档
永远打不开。

### 3.3 前置判定的唯一入口

`_feat_options` 与建卡期校验共用同一个 `feat_unavailable_reasons(feat, character, target_level) -> list[str]`，
返回 `"level"` / `"already_owned"` / `"ability_prerequisite"` / `"spellcasting"` / `"feature"` 的闭集。
`character/validation.py:289` 不再自己判 `requires_feature`，改为调这个函数。

## 4. 实现落点（白名单）

FD2a 已用掉第 2、3、5 项（`requires`）。FD2b 的实际白名单：

1. `src/rulesets/dnd2024/features/feat_provenance.py`（新建：`FeatProvenance` / `feat_provenance` / `acquire`）
2. `src/rulesets/dnd2024/character/derivation.py`（用 `feat_provenance` 替代三来源拼接 + `dict.fromkeys`；
   `feat_refs` 输出仍是 `list[str]`）
3. `src/rulesets/dnd2024/character/validation.py`（建卡取得期的重复来源检查）
4. `src/rulesets/dnd2024/progression/advancement.py`（写回 `feat_refs` 前用 `acquire` 确认）
5. `src/rulesets/dnd2024/progression/choices.py`（`_advancement_feat` 的重复确认）
6. `pyproject.toml`（新文件进 mypy `files` 清单）
7. `tests/rulesets/test_dnd2024_feat_provenance.py`（新建）

**不改**：`combat/`、`features/resolver.py`（`_feat_refs` 读 str 列表，形状不变）、`content/feats/*.json`
（属 FD3）、存档 schema 版本、`progression.history` 的条目形状。

## 5. 非目标与停止条件

**非目标**：合并两个专长目录（FD3）；新增 origin 池（依赖 FD3）；任何专长的实际效果；
Dual Wielder（S0-G4）；F8 的战斗风格槽（F8 修订）。

**停止条件**：
- 发现 provenance 必须落盘才能满足某个消费者时停止——说明 §3.1 的"派生视图"判断不成立，需重新评估；
- 需要改 `combat/` 任何文件时停止；
- `progression.history` 的条目形状需要变化时停止（它是重放与审计的锚，改动属独立切片）；
- 需要改存档 schema 版本时停止——本片明确不碰 persisted 形状。

## 6. 验收矩阵（FD2b）

1. 背景 soldier（实际给 `feat:savage_attacker`）：一条 `{feat:savage_attacker, background, background:soldier, None}`；
   Human 的物种专长再给一条 `{feat:alert, species, species:human, None}`。
2. 4 级选 Grappler：一条 `{advancement, level:4, 4}`，来源是 `history[].diff.advancement_feat_ref`，`history` 不变。
3. 冠军 7 的额外风格槽：一条 `{class_feature, subclass:champion, 7}`，来源是 `features.feature_choices`。
4. `repeatable: false` 的专长同时来自背景与物种：建卡校验拒绝，消息同时列出两个 `source_ref`。
5. 同一 `(source_kind, source_ref)` 出现两次：拒绝（`repeatable: true` 也一样）。
6. 旧档（`feat_refs` 有 ref、但 history/choices 都查不到）：该条 `source_kind: "unknown"`，**不报错**，
   且 `feat_refs` 原样保留。
7. 形状不变：派生输出的 `features.feat_refs` 仍是 `list[str]`，存档版本号不变，旧档无需迁移。
8. `derivation.py` 不再对 `feat_refs` 用 `dict.fromkeys`（`grep -c` 为 0）。
9. 顺序稳定：同一角色两次派生，provenance 序列逐项相等（来源遍历顺序固定，不依赖 dict 迭代）。
10. `feat_unavailable_reasons` 的闭集是六值（FD2a 已含 `proficiency`）；任一不在闭集的返回值 → 测试失败。

> 原矩阵的 7、8 条（`fighting_style` 理由来自 `feature`、`validation.py` 无 `requires_feature`）已由 FD2a
> 完成并在 §8.6 记录，不在本片重复。

## 7. 验收命令

```
ruff check src/
/c/Python314/python.exe -m mypy
pytest tests/rulesets -n 6 --dist loadfile --basetemp=E:/pytest-tmp/<新名>
```

## 8. FD2a：前置条件的结构化承载（2026-11-27 实现）

本片先做"能不能取得"这一半。归属/provenance（原 §3.1）仍未开始。

### 8.1 契约

`features/feat_requirements.py`（新建，约 300 行）：

```
FeatRequirement   {kind, any_of, feature_id, family, proficiency_id}
FeatContext       {level, abilities, owned_refs, slot_profile, feature_ids,
                   proficiency_refs}
feat_unavailable_reasons(definition, context) -> tuple[str, ...]
```

- `kind` 闭集：`ability_minimum` / `feature` / `proficiency` / `spellcasting`；
  `level` 与 `already_owned` 仍由顶层 `minimum_level` / `repeatable` 判，但**同一个函数**判，
  所以"一个入口"是完整的。
- `any_of` 是**一项达标即满足**（"Strength or Dexterity 13+"），不是全部达标。
- 理由码闭集 `REASON_CODES`；措辞留在各通道（`choices.py` 的 `_FEAT_REASON_MESSAGES`），
  并有测试断言两者集合相等——新理由码最容易的失败方式是没人给它文案。
- 加载期：未知 kind / 空 `any_of` / 未知属性 / 未知受训档 / 多余字段全部拒绝；
  `feature` 的引用完整性在 `features/validation.py` 查（那里才有目录）。

### 8.2 三个调用点

| 位置 | 改动 |
|---|---|
| `progression/choices.py::_feat_options` | 循环外建一次 `FeatContext`，循环内取理由码 |
| `progression/choices.py::_advancement_feat` | 同一函数取理由码，经文案表转成原有句子（**文案一字未改**） |
| `character/validation.py::_validate_fighting_style_choice` | 删掉自写的 `requires_feature` 判定，改调同一函数；level 传 1，特性集取自 `level_1_feature_ids`，受训集由 `_draft_proficiencies` 给 |

### 8.3 实现中撞到的两条事实（文档改不到）

1. **`fighting_style` 不是 class feature，是一个槽位。** `srd_class_features.json` 里没有这一
   条定义，它只存在于职业实体的 `level_1_feature_ids` 与职业进度表的 `features_by_level`。
   所以"特性 id 空间"有两个来源，只查目录会让四个战斗风格的先决全部失效——第一版就是这么
   错的，被 `archery.requires names an undeclared feature` 当场抓住。
2. **升级路径拿不到"角色拥有哪些特性"**这种说法本身不够：`features_for` 只返回有定义的特性，
   于是 `fighting_style` 这类槽位在升级路径上"不存在"。`_feat_context` 因此取
   resolver 的owned 定义 ∪ 职业进度表 1..level 的 `gained_feature_ids`。

### 8.4 受训（"xx 受训"）语义与对接点

"中甲受训"在本系统里不是新事实：它就是 `character.proficiencies.armor_category_refs`
包含 `armor_category:medium`，由职业的 `armor_proficiencies` 派生，已有三个读者——
盾牌 AC（`derivation.py:219`）、未受训惩罚（`:602`）、以及本片新增的先决判定。

`proficiency` 这个 kind 因此是读既有事实，而不是为专长另造一套。两点限制：

- `family` 只开 `armor` / `weapon`：SRD 17 条没有"技能/工具受训"先决，开了就是没有读者的
  取值空间。PHB 若出现，`family: skill` 加一行取值集合即可。
- 判定读**派生后**的熟练集合（`proficiencies_from` 的 docstring 写明），不是职业的原始列表：
  PHB 的 Moderately Armored 本身授予中甲受训，一条专长要能成为另一条专长的先决。今天没有
  专长授予受训（P4 的 `grants` 未落地），所以两种读法结果一样；钉在派生值上是为了 P4 落地
  时不需要改判定。

### 8.5 数据迁移（原子，无双读）

```
grappler / archery / defense / great_weapon_fighting / two_weapon_fighting /
boon_of_spell_recall   → requires: [...]
```

`LEGACY_FEAT_FIELDS` 由 5 项降到 2 项（`ability_choice` / `choice_specs`），三个旧键改为
**加载期报错并指路**：静默接受等于让先决凭空消失，"这条专长突然人人可选"不是响亮的失败。
仓外 bundle 若仍用旧键会被拒——这是一次内容格式变更，应该写进 PR 正文。

### 8.6 验收

1. 四个战斗风格在无该槽位的职业上，直接调 `_feat_options("fighting_style", ...)` 时理由为
   `["feature"]`——不再是"category 恰好不同"的巧合。
2. 真实战士角色：建卡接受 Archery，升级路径对同一角色的理由是 `["already_owned"]`
   （说明 `feature` 判定在两条路径上都成立）。
3. `_draft_proficiencies`（建卡期的镜像）与派生后的熟练集合逐项相等。
4. 理由码集合 == 文案表集合。
5. 旧键在 `src/` 与 `templates/` 中零出现（仅退役清单与 docstring 提及）。
6. 全量回归 + ruff + mypy。

## 9. FD2b：来源可追溯与取得显式化（2026-11-28 实现）

### 9.1 落点

`features/feat_provenance.py`（新建）：

- `FeatProvenance {ref, source_kind, source_ref, level}`，`SOURCE_KINDS` 五值（含 `unknown`）；
- `feat_provenance(bundle, character, *, class_slots=None)`：`character` 可以是建卡 `draft`（扁平）
  或派生后的角色卡（`identity` / `build` / `features` / `progression`），`_views` 把两种形状归一；
- `owned_feat_refs(provenance)`：扁平 ref 列表，替代 `dict.fromkeys`；
- `acquire(existing, ref, source, *, repeatable)`：唯一的"第二次取得"判定。

来源位置（实现时校正了初版）：

| source_kind | 读取位置 |
|---|---|
| background | `identity.background_ref` → background 实体的 `feat_ref`（draft 形状时取顶层） |
| species | `build.species_feat_refs` + `identity.species_ref` |
| class_feature（1 级槽） | `build.class_feature_feat_choices`，键必须在职业 `level_1_feature_ids` 里 |
| class_feature（后续槽） | `features.feature_choices[]`（`owner` / `granted_level` / `selected_refs`） |
| advancement | `progression.history[].diff.advancement_feat_ref`——**不在 `choices` 里** |
| unknown | `features.feat_refs` 里无法还原来源的 ref |

### 9.2 偏差

| 计划 | 实际 |
|---|---|
| `feat_provenance(bundle, build, features, progression)` 四参 | 改为 `(bundle, character)`。真实角色卡的 `background_ref` / `species_ref` 在 `identity`、`class_ref` 在 `build.class_levels`，与 draft 形状不同；四参会逼每个调用方各写一份形状适配 |
| 返回值去重 | 不去重。同一 ref 的两个 grant 必须都可见，否则重复检查没有输入；折叠由 `owned_feat_refs` 负责 |
| `advancement.py` 写回前调 `acquire` | 不调。`already_owned`（FD2a）在取得前判的是同一条规则，写回处的第二道只会恒为通过 |
| `grants_with_sources` 的专长条目 | 改为遍历 provenance 的 background/species 子集，行为不变；class_feature 来源仍不进该列表（避免改派生输出） |

### 9.3 验证

- 新增 `tests/rulesets/test_dnd2024_feat_provenance.py` 14 例：三来源、`diff` 位置、子职槽、
  槽白名单、unknown、双来源可见、`acquire` 三条拒绝、存档形状、顺序稳定、建卡重复拒绝；
- `character_builder` + `fighting_style` + `advancement_records` + `chained_advancement`：72 passed；
- `feature_choices` + `feat_requirements`：72 passed；
- `ruff check src/ tests/`：干净；`mypy`（149 文件）：0 错；
- 全量 `pytest tests/ -n 6 --dist loadfile`：**11540 passed / 9 failed / 3 errors**。9 条全是本机既有
  docker 基线（launcher 6、data generation 1、self-update reentry 1、runtime dependencies 1），
  3 errors 是 docker 升级；Linux CI 不复现。没有新增失败。

### 9.4 未决

- `grants_with_sources` 与 `feat_provenance` 对"专长来源"仍是两处视图：前者只覆盖背景/物种、
  服务展示，后者覆盖全部、服务审计。合并它们会改派生输出，留给下一次。
- `progression.history` 只记 add、不记 drop（F8 的风格替换）；被替换掉的 ref 已不在 `feat_refs`
  里，provenance 不会为它编造来源，但"它曾经存在过"没有痕迹——属 F8 的替换账本。
