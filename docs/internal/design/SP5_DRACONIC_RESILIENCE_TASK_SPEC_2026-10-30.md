# 切片 S-P5 任务书：Draconic Resilience（防御计算修改：无甲 AC 公式 + 生命值上限加成）

版本：v1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：
1. **依赖 S-P1（子职特性所有权）已合入**——`features_for` 必须已并入子职目录合并，否则 `has_feature("draconic_resilience")` 恒为 False。若 S-P1 尚未合入，停止。
2. 本切片不触碰 `combat/` 任何文件，与 1B P6 无文件冲突，但仍须在 P6 合入后基于干净基线开工。
3. 专长侧同族需求（Tough 的每级 +2 HP、Defense 战斗风格的 +1 AC）**不在本切片**：它们的声明面在 feat 目录而非 class feature catalog，本切片只把可复用的参数约定与公式沉淀好，专长消费留待专长批次。
本任务书锚点基于 `1009be03`，执行侧须自行重核行号。

---

## 1. 目标

为 D&D 2024 规则集打通 **龙族体魄（Draconic Resilience，龙族术法 3 级）** 的完整闭环：特性声明 → 角色派生（建卡 / 升级 / 装备变更）→ 无甲 AC 公式与生命值上限按 2024 原文生效 → 测试。

本切片沉淀一项通用原语：

**防御计算修改（特征参数 → 派生期消费）**：class feature 声明两种封闭参数——无甲 AC 公式加成属性与生命值上限加成公式——由**角色派生层**（唯一的 AC/HP 计算权威，`derive_armor_class` 与 max_hp 推导"只存在一次"）在三个既有消费点（建卡派生、升级增益、装备协调）统一消费。后续消费者：Barbarian 无甲防御（10+敏+体）、Tough 专长（每级 +2 HP）、Defense 战斗风格（+1 AC），复用同一参数面。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 龙族体魄（3 级）："你的生命值上限提升 3，此后每提升一个术士等级，都将再次提升 1。你部分皮肤覆盖着龙鳞样式的柔鳞。**未着装护甲时**，你的基础 AC 等于 10 + 你的敏捷调整值 + 你的魅力调整值。"
- 基础 AC 通用规则：所有生物基础 AC = 10 + 敏捷调整值；某些法术和职业特性能提供多种不同的 AC 计算方式，拥有多个计算方式的角色必须选择使用哪种，**一个生物只能有一个基础计算方式生效**。
- 护甲表规则：AC 列给出穿某护甲时的基础 AC；盾牌独立于护甲（+2，一次一面）。

出处：

- `.rulebooks/dnd5r/玩家手册2024/角色职业/术士/龙族术法.htm` L16-17（龙族体魄，2024 版公式：HP +3 起步、此后每级 +1；无甲 AC 10+敏+魅。**不得沿用 2014 的 13+敏 AC 或其它数值**）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/攻击检定.htm` L29-33（基础 AC、仅限一种基础 AC）；
- `.rulebooks/dnd5r/玩家手册2024/装备/护甲.htm` L8（护甲 AC 列）、L21-26（盾牌独立、一次一件）。

### 1.1 必须锁定的规则边界

1. **"未着装护甲时"是硬前提**：穿甲时龙族公式完全不参与计算（不是"取龙族与护甲的较大者"——皮甲术士 AC 必须是皮甲公式，不得因龙族 10+敏+魅更高而保留 15）。盾牌不算护甲：无甲持盾 AC = 龙族公式 + 2。
2. **仅限一种基础 AC**：无甲时基础 AC 在"10+敏"与"10+敏+<加成属性>"候选间取**最优**（确定性化"角色选择"，负调整值时自动回退到 10+敏）。多个特性声明不同加成属性时同样只在候选间取最优，不做属性调整值累加。
3. **HP 上限公式按 2024 原文逐字**：3 级（获得子职时）+3，此后每提升一个术士等级 +1。即术士等级 N≥3 时累计加成 = N。单职业引擎下术士等级 = 主职业等级。
4. **HP 加成只改上限，不治疗**：升级时 `resources.max_hp` 增量包含加成，`resources.hp` 仍按既有 `min(max_hp, hp + gain)` 逻辑恢复（升级本身就有 HP 增加，含在 `hp_gain` 里）。
5. **体质回顾性调整与龙族加成独立**：既有 `retrospective_con_hp`（Con 变更的回溯）公式不动，龙族增量单独叠加进 `hp_gain`。
6. **AC 计算权威只有一处**：`derive_armor_class` 是唯一公式；建卡派生、装备协调两个既有调用点传入加成属性集合，不在战斗层或投影层另行计算。战斗中 AC 恒读 `derived.armor_class`（既有链路，零改动）。
7. **既有"多件护甲取最优"的合法态遏制语义不动**：穿甲状态下 `max(unarmored, best_armor)` 的既有行为保留原样（含其既有的文档化边界：极端敏捷下轻甲可能低于无甲值仍取护甲的既有决定，不因本切片改变——遗留记录，见 §8）。
8. **特性所有权按 S-P1 语义**：`(class_id, level)` 推导（子职目录 3 级授予 draconic_resilience），不读 `features.subclass_ref`。

### 1.2 本切片的产品策略与裁剪

- 龙族术法（draconic）子职的**其余特性不做**：`draconic_spells`（额外法术列表数据，归阶段二内容批）、`elemental_affinity`（6 级，后置）、`dragon_wings`（14 级，飞行）、`dragon_companion`（18 级，P11 召唤大件）。
- 既有角色的**追溯性补发不做**：本切片之前已达到 3 级以上的存量角色不会自动获得 HP 上限加成与 AC 修正（其 `max_hp`/`derived.armor_class` 已成事实状态）。任何存量补发必须走显式的数据迁移流程（`migration correctness > migration completeness`），本切片只在新建/升级/装备协调三个入口生效——遗留记录见 §8。
- ASI 提升魅力后的 AC 重算依赖既有装备协调流程；若执行中发现升级并不触发 AC 重推导（对敏捷 ASI 同样成立的既有缺口），按 §7 停止条件处理，不得自行扩大改动面。
- 多职业（未来）场景下"术士等级"须改读对应 class 的等级——单职业限制下本切片直接用主职业等级，迁移债记录。
- 无甲 AC 公式不进入战斗动态计算：换装发生在叙事层，由装备协调重算，战斗内不响应。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `derive_armor_class(abilities, equipped_item_refs, bundle, *, warnings)` 是唯一 AC 公式；无甲基线 `10 + dex`，护甲取最优、盾牌加成、异常 live state 遏制 | `character/derivation.py` L14-70 |
| 建卡派生调用点：`armor_class = derive_armor_class(...)`、max_hp 推导（hit_die + con + 每级 fixed_hp/con + 种族每级加成） | `character/derivation.py` L148、L150-156 |
| 装备协调调用点：live 装备变更后重算 AC 并镜像到 sheet；**现场构造 `Dnd2024ClassFeatureResolver(self.bundle)` 的先例在此** | `character/reconciliation.py` L321、L344 |
| 升级增益公式：`new_level_hp = max(1, hp_base + new_con + species_hp)`、`retrospective_con_hp`、`resources.max_hp += hp_gain` | `progression/advancement.py` L205-213、L275-280 |
| 升级后 `_update_derived` 只重算熟练/豁免/被动感知/施法参数，**不重算 AC** | `progression/advancement.py` L533-566 |
| `class_state(character)` 只读 `build.class_levels`（单行）——最小代理 `{"build": {"class_levels": [{"class_ref", "level"}]}}` 即可驱动 features_for | `features/resolver.py` L91-117 |
| 特性参数自由标量键 + `_validate_declarations` 校验 | `features/models.py` L266-271；`features/resolver.py` L527+ |
| 战斗层 AC 恒读 `derived.armor_class`（玩家）/敌人档案（敌人） | `combat/view.py` L128、L76 |
| 龙族术法子职：3 级授予 draconic_resilience | `templates/.../content/subclass_catalogs/srd_subclasses.json` L66（sorcerer 段） |
| class feature catalog 现有 5 个职业键（monk/paladin/fighter/barbarian/rogue），无 sorcerer | `templates/.../content/class_feature_catalogs/srd_class_features.json` |
| 测试基建：builder 建卡、`advance(runtime, sheet, level)`、live 装备协调测试 | `tests/rulesets/test_dnd2024_character_builder.py`、`test_dnd2024_sneak_attack.py` L26-32、`test_dnd2024_live_equipment_reconciliation.py` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

新建 `classes.sorcerer`（结构照 fighter：`source_ref` + `features`），追加被动声明（无 `capabilities`）：

```json
{
  "id": "draconic_resilience",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p52:draconic-resilience",
  "summary": "Your Hit Point maximum increases by 3, and it increases by 1 again whenever you gain a Sorcerer level. While you aren't wearing armor, your base Armor Class is 10 plus your Dexterity and Charisma modifiers.",
  "parameters": {
    "unarmored_ac_ability_bonus": "cha",
    "max_hp_bonus_first": 3,
    "max_hp_bonus_per_level_after": 1
  }
}
```

`labels` 追加 `"sorcerer": "Sorcerer"`、`"draconic_resilience": "Draconic Resilience"`。参数语义（封闭枚举）：

- `unarmored_ac_ability_bonus`：无甲基础 AC 的额外加成属性缩写（10 + 敏捷 + 该属性）；声明即定义一个候选公式，多候选取最优不相加；
- `max_hp_bonus_first`：达到特性 `minimum_level` 时的一次性 HP 上限加成；
- `max_hp_bonus_per_level_after`：此后每提升一个该职业等级的 HP 上限加成。

### 3.2 locale 文件（两个）

`locales/en` 与 `locales/zh-CN` 的 `class_feature_catalogs/srd_class_features.json` labels 同步追加；zh-CN 译名：术士、龙族体魄。

### 3.3 `src/rulesets/dnd2024/features/resolver.py`

1. 新增公共 API `unarmored_ac_bonus_abilities(character) -> tuple[str, ...]`：返回已拥有特性声明 `unarmored_ac_ability_bonus` 的属性缩写（去重，目录序）。
2. 新增公共 API `feature_max_hp_bonus(character) -> int`：`class_state` 为 None 返回 0；否则对每个已拥有特性：`bonus = max_hp_bonus_first(若等级 >= minimum_level，features_for 已保证) + max_hp_bonus_per_level_after * max(0, level - minimum_level)`，求和。
3. `_validate_declarations` 追加 fail closed 校验：`unarmored_ac_ability_bonus` 必须是六属性缩写集合内的 str；`max_hp_bonus_first` / `max_hp_bonus_per_level_after` 必须是 int（非 bool）且 ≥ 0、≤ 20。

### 3.4 `src/rulesets/dnd2024/character/derivation.py`

1. `derive_armor_class` 新增仅关键字参数 `unarmored_ability_bonuses: Iterable[str] = ()`：
   - **只在 `best_armor is None`（无任何护甲）时**计算覆写候选：`unarmored = max(10 + dex, *[10 + dex + ability_modifier(abilities[a]) for a in bonuses])`；
   - 有护甲时 `unarmored` 保持 `10 + dex` 不变，后续 `max(unarmored, best_armor) + shield_bonus` 逻辑一行不动。
2. `derive()`（CharacterDerivationMixin）在 L148/L150-156 两处消费：
   - 用最小代理 `{"build": {"class_levels": [{"class_ref": draft["class_ref"], "level": level}]}}` 现场构造 `Dnd2024ClassFeatureResolver(self.bundle)`（先例：reconciliation L344），取 `unarmored_ac_bonus_abilities` 传入 `derive_armor_class`；
   - `max_hp` 基础公式算完后 `+= resolver.feature_max_hp_bonus(同一代理)`。

### 3.5 `src/rulesets/dnd2024/character/reconciliation.py`

L321 的 `derive_armor_class` 调用前，用 canonical（完整角色）构造 resolver，取 `unarmored_ac_bonus_abilities(canonical)` 传入。该文件已在 L344 构造 resolver，**合并为同一实例**，不要构造两次。

### 3.6 `src/rulesets/dnd2024/progression/advancement.py`

`apply_next_level` 的 `hp_gain`（L211-213）追加龙族增量：

- `hp_gain += bonus(target_level) - bonus(from_level)`，其中 `bonus(L)` 用 `feature_max_hp_bonus(代理 {"class_ref", "level": L})`（resolver 现场构造，或注入；2→3 时 +3，此后每级 +1，公式与建卡路径完全同源）；
- `resources.max_hp` / `resources.hp` 的既有应用逻辑不动（升级本来就把 `hp_gain` 加进两者）。

### 3.7 测试

新文件 `tests/rulesets/test_dnd2024_draconic_resilience.py`。测试矩阵见 §4。

回归：`test_dnd2024_progression.py`、`test_dnd2024_advancement_access.py`、`test_dnd2024_live_equipment_reconciliation.py`、`test_dnd2024_class_features.py`、`test_dnd2024_combat.py`、`test_dnd2024_monk_equipment.py`、`test_dnd2024_monk_combat.py`、`test_dnd2024_character_builder.py`（上游既有失败不计）。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`unarmored_ac_ability_bonus` 非法属性 / HP 参数为负数、字符串、布尔、>20 | `ClassFeatureError` |
| 2 | 所有权：advance 到 3 级术士 `has_feature("draconic_resilience")`；2 级 False；3 级法师无 | S-P1 合并生效 |
| 3 | 建卡 3 级术士（敏 14、魅 16） | `derived.armor_class == 15`（10+2+3）；`resources.max_hp == 基础公式 + 3` |
| 4 | 建卡 1/2 级术士 | AC == 10+敏、max_hp 无加成 |
| 5 | 建卡 5 级 / 20 级术士 | max_hp 加成分别 +5 / +20（3 + 每级 1） |
| 6 | 升级 2→3：advance 后 `max_hp` 增量含 +3；3→4 含 +1 | 增量断言（含既有 con/物种项分解） |
| 7 | 建卡 5 级术士与"1 级建卡后逐级 advance 到 5 级"两条路径 | `max_hp` 相同（公式同源不双记） |
| 8 | 魅力调整值为负（极端构造）：无甲 AC | 回退到 10+敏（仅限一种基础 AC，取最优） |
| 9 | reconcile 装备皮甲（item_type=armor） | AC == 皮甲公式（龙族公式不参与）；警告无 |
| 10 | reconcile 无甲持盾 | AC == 龙族公式 + 2 |
| 11 | reconcile 卸甲回无甲 | AC 回到 10+敏+魅 |
| 12 | 战斗视图：3 级术士进战斗 | `armor_class` 恒等于 `derived.armor_class`（既有链路，回归级断言） |
| 13 | 存量角色不追溯：手工构造一个"切片前形态"的 3 级术士（无本特性投影）进战斗 | 行为不因本切片改变（无崩溃、无自动补发） |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_draconic_resilience.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_advancement_access.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_monk_equipment.py tests/rulesets/test_dnd2024_monk_combat.py -q
```

## 6. 禁止事项

1. 禁止在战斗层（combat/）新增任何 AC 或 HP 上限计算——派生层是唯一权威，本切片零战斗文件改动。
2. 禁止让龙族公式参与穿甲状态的 max()——"未着装护甲"是硬前提（§1.1 第 1 条）。
3. 禁止把魅力调整值与敏捷调整值"累加"实现多特性组合——只能多候选取最优。
4. 禁止在 `derive_armor_class` 之外复制一份 AC 公式（包括 builder/advancement 投影层）。
5. 禁止修改既有 `retrospective_con_hp`、`max(unarmored, best_armor)`、盾牌遏制逻辑。
6. 禁止对存量角色做自动 max_hp/AC 追溯补发（含升级路径上"顺手补历史等级加成"的变体——`bonus(target) - bonus(from)` 只算增量，不回溯 from 之前）。
7. 禁止实现 draconic_spells / elemental_affinity / dragon_wings / dragon_companion。
8. 禁止用 2014 数值（AC 13+敏、HP 每级 +1 从 1 级起算）替代 2024 原文公式。

## 7. 停止条件

1. S-P1 未合入（子职特性所有权不存在）；
2. `advance` 升级路径发现 `hp_gain` 无法只加增量（例如升级引擎对 max_hp 有其它隐式重算）且与本任务书公式冲突；
3. 魅力 ASI 后 AC 不随任何既有协调流程重算——该缺口对敏捷 ASI 同样存在，报告既有缺口并等待裁决，不得自行给 `_update_derived` 加 AC 重算；
4. `derive_armor_class` 的调用点超出 §2 列举的两个（发现第三个调用方意味着有未评估的消费路径）；
5. builder 的建卡校验/契约要求 class feature catalog 必须覆盖全部职业的特定键，新建 `sorcerer` 键触发了未预期的目录级校验冲突。

## 8. 执行报告模板

执行完成后在本文件追加"执行报告"节：变更文件清单、测试矩阵逐项结果、偏差与遗留（特别是：①存量角色不追溯的影响面与运维建议；②"极端敏捷下轻甲低于无甲仍取护甲"的既有决定是否被评审要求重开；③多职业化后 `class_state` 单行假设的迁移债确认；④ASI 触发 AC 重算的实际链路结论）。
