# S0-G3 任务书：成长记录与缺项补齐

| 项 | 值 |
|---|---|
| 切片编号 | S0-G3（交付载体并入 S0-A 的 #734，不新开 PR） |
| 验收载体 | 子职、专长、ASI、法术准备位四类「应选项」的缺失可被检测、可被补齐；升级事务在角色不完整时不再放行 |
| 通用能力 | `progression.advancement_records` 成长账本；`missing_choices()` 缺项检测；补齐事务与派生重算 |
| 规则基准 | **不新增任何内容声明**。所有「应该有几分/几点/几项」一律从既有 progression 目录读取，因此本切片不引入新的规则数字，也不需要 PDF 页码 |
| 代码基线 | `feat/dnd2024-s0a-subclass-critical` 当前头（S0-A 已交付） |
| 硬前置 | 无。S0-A 的「子职只来自显式选择」是本切片的前提，已在该分支上 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律与 A/B/C 提交边界

1. 本切片修改的是 **S0-A 已开 PR #734 的判定语义**，不是新切片。合入后 #734 的标题与描述须同步重画为「显式选择 + 缺项补齐」，Impact 需补 Persisted Data 与 Compatibility。
2. **提交 A（成长账本）**：`apply_next_level` 写入 `progression.advancement_records`。纯新增字段，不改变任何现有读取路径，不产生行为变化。
3. **提交 B（缺项检测）**：新增 `missing_choices()`，覆盖子职、专长/ASI、法术准备位四类；把 `preview_next_level` 的子职判定从「本级是否授予」改为「角色是否已有」。B 完成后，缺项角色无法通过升级事务。
4. **提交 C（补齐事务）**：独立于升级的补齐入口与派生重算。C 完成后，缺项角色可在任意时刻补齐，包括 20 级。
5. 三个提交必须按 A → B → C 顺序，每个提交单独跑聚焦测试。A 是存档格式变更，必须先落地并稳定。

## 2. 为什么必须有这一层

### 2.1 升级是事务，不是一次性动作

网团的形态是「DM 要求交一份升好级的卡，没填好会被退回」；电子游戏的形态是「进入升级界面，选完并确认才应用」。两者的共性是：**升级是一个可以停在「未完成」状态的事务**，未完成时不产生任何效果。

本仓库的升级已经是这个形态：`apply_next_level` 先跑 `preview_next_level`，不 ok 就抛；前端「确认升级」按钮挂在 `preview.ok` 上；`apply_next_level` 只能 +1 级，不能跳级；建卡强制 1 级。

### 2.2 但事务只检查「本级增量」，不检查「角色缺口」

这是本切片要修的东西。当前判定是：

```python
if any(feature.endswith("_subclass") for feature in after["gained_feature_ids"]):
```

它回答的是「本级是否授予子职」，而不是「角色是否已有子职」。在「每张卡都从 1 级长起来」的前提下两者等价；一旦卡来自外部，两者分叉，而分叉的后果是**静默**的：不报错、不提示、升级照常成功。

外部卡是真实路径，不是边角：

- `character_card_import.py` 全文没有任何 `level` / `subclass` 校验；
- `character_cards.py` 的注释自己写着 `cards.json has no storage-level invariant`；
- 早于 `e4bc185d` 的存档没有 `subclass_ref` 写入逻辑，当时靠「按等级兜底授予」运行。

### 2.3 补齐不是补丁，是事务的另一半

「子职只能来自显式选择」这条规则一旦成立，就必须回答「没选怎么办」。只做前半句，等于把一个可修复的数据状态变成了永久规则否定。

本切片把判定改为**读角色状态**，并给出补齐通道。这样：

- 事务的职责从「处理本级增量」变成「保证升级后的角色完整」；
- 外部卡在下次升级时被强制补齐，事务自愈；
- 补齐入口让玩家不必等到升级，DM 也能随时要求补交。

## 3. 已定点核实的代码事实

以下全部在 `feat/dnd2024-s0a-subclass-critical` 的当前头上实测。

### 3.1 判定与拒绝

| 事实 | 位置 |
|---|---|
| 子职 requirement 只在 `after["gained_feature_ids"]` 含 `*_subclass` 时抛出 | `progression/advancement.py:208` |
| 非授予级提交 `subclass_ref` 被显式拒绝：`subclass_ref can only be selected when the class grants a subclass` | `progression/advancement.py:223-224` |
| 该错误串全仓只有这一处，**没有任何测试钉住** | `grep` 结果 |

### 3.2 实测行为（5 级 Fighter，删去 `subclass_ref`）

| 场景 | `requirements` 含 `subclass_ref` | 结果 |
|---|---|---|
| 升 6 级，不提交 | **否** | `ok: True`（补齐 ASI + 专长后），`subclass_ref` 仍为 `None` |
| 升 6 级，提交 `subclass:champion` | — | `ok: False`，报上述错误 |

即：前端不渲染该字段，玩家无从提交；主动提交则被拒。**静默缺失，且补不上。**

### 3.3 目录事实（不猜，直接从 `srd_classes.json` 读）

| 项 | 值 |
|---|---|
| ASI 等级 | 12 职业均为 `4/8/12/16`；Fighter 另加 `6/14`；Rogue 另加 `10` |
| Epic Boon 等级 | 全部 19 |
| 子职授予等级 | 全部 3（`subclass["feature_ids_by_level"]` 的最小 key） |
| `features_by_level` 结构 | `list[list[str]]`，index 0 == 1 级 |

### 3.4 记录现状

| 事实 | 位置 |
|---|---|
| `features.feat_refs` 是平铺 ref 数组，**没有等级** | `advancement.py:481` |
| `features.feature_choices` 的记录键被 `FEATURE_CHOICE_KEYS` 封闭为 `{owner, feature_id, choice_id, selected_refs}`，**没有等级**；硬加字段会被校验拒 | `advancement.py:31`、`progression/advancement.py:693` |
| `features.class_feature_grants` **只写不读**，全仓无消费者 | `advancement.py:495-505` |
| `progression.history` 有每级 `choices` 与 `diff`，但那是审计日志，不是权威事实 | `advancement.py:515-522` |
| 建卡期属性来源已存在：`build.base_abilities`、`build.background_ability_bonuses` | `character/builder.py:469-470` |
| 升级期 ASI 直接并入数值：`new_abilities[ability] += increase` | `advancement.py:362-363` |

### 3.5 派生重算的既有口径

| 事实 | 位置 |
|---|---|
| `feature_max_hp_bonus` 返回**绝对值**：`first + per_level * (level - minimum_level)` | `features/resolver.py:891` |
| 升级时 CON 提升按 `current_level * (new_con - old_con)` 回溯补 HP | `advancement.py:371` |
| `_update_derived` 重算熟练、豁免、技能、被动感知、AC，**不含 max_hp** | `advancement.py:1060-1105` |
| `_granted_feature_keys` 按 `raw_level <= level` 扫子职表，因此写入 `subclass_ref` 会**立刻**让全部既有等级子职特性生效 | `features/resolver.py:1597-1607` |

### 3.6 并发与幂等

| 事实 | 位置 |
|---|---|
| `apply_card` 已有 `operation_id` 幂等 + `expected_revision` 乐观锁 + 提交时重跑 preview | `webui/services/ruleset_advancement.py:201-265` |

## 4. 精确数据与行为契约

### 4.1 成长账本（提交 A）

在 canonical 角色的 `progression` 下新增 append-only 数组，键名固定为 `advancement_records`：

```json
"progression": {
  "advancement_records": [
    {"level": 3, "class_ref": "class:fighter", "subclass_ref": "subclass:champion"},
    {"level": 4, "class_ref": "class:fighter", "ability_score_increase": {"str": 2}, "feat_ref": "feat:defense"},
    {"level": 6, "class_ref": "class:fighter", "ability_score_increase": {"str": 1}, "feat_ref": "feat:dual_wielder"},
    {"level": 19, "class_ref": "class:fighter", "epic_boon_ref": "feat:boon_of_combat_prowess", "ability_score_increase": {"str": 1}}
  ]
}
```

契约：

1. **每级一条，`level` 单调递增**（`apply_next_level` 只能 +1 级，所以天然递增；写入前仍校验最后一条的 `level < target_level`，否则抛 `ProgressionCatalogError`）。
2. **只写该级实际做出的选择**，不写空对象。没有 ASI 的等级就没有 `ability_score_increase` 键。
3. **`class_ref` 必写**。当前是单职业，加它的成本为零；将来多职业时缺它会再来一次迁移。
4. **`feat_ref` 与 `epic_boon_ref` 分开**，与 `choices` 的键名保持一致，便于和 `history` 对账。两者都是「该级的专长来源」。
5. **建卡期（1 级）不写账本**。1 级属性来源是 `build.base_abilities` + `build.background_ability_bonuses`，已经是完整来源，重复记录会制造两个真相。
6. **`history` 保持不变**。账本是权威事实，`history` 是审计日志；两者内容应一致，但读取方只允许读账本。

### 4.2 缺项检测（提交 B）

在 `Dnd2024AdvancementEngine` 新增：

```python
def missing_choices(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """角色当前缺少的必选项，按等级升序。"""
```

返回值与 `requirements` **同构**，额外两个字段：

| 字段 | 含义 |
|---|---|
| `level` | 该选项应在哪一级完成 |
| `reason` | 面向玩家的一句话原因 |

四类判定：

#### 4.2.1 子职

```
grant_level = min(int(lv) for lv in subclass["feature_ids_by_level"])
若 current_level >= grant_level 且 features.subclass_ref 为空 → 缺失
```

`grant_level` **必须从目录读**，不得写死 3。

#### 4.2.2 专长与 ASI

```
对 level ∈ [1, current_level]：
  若 progression 表该级含 ability_score_improvement 或 epic_boon：
    若 advancement_records 无 level 匹配的记录 → 缺失
    若记录存在但 ability_score_increase 与 feat_ref/epic_boon_ref 全空 → 记录不完整
```

#### 4.2.3 法术准备位

只查**数量**，不查合法性：

```
required = snapshot["slot_profile"]
actual   = 角色 spellcasting.class 下对应 ref 数组长度
cantrip_count / spellbook_minimum / prepared_spell_count 逐项比对
```

明确排除：`free_cast` 槽与特性授予的常驻准备法术。它们不在 `prepared_spell_refs` 里，天然不被计入，符合「这部分不必严谨」的边界。

#### 4.2.4 旧存档：fail open

若角色**没有** `progression.advancement_records` 字段（或不是 list），**整个 ASI/专长检测跳过**，不产生任何缺项。

理由：没有账本就无法区分「玩家真的没选」与「这个字段还不存在」。误报会让每一张旧卡都被判为不完整，那是比漏报更糟的失败模式。

写入侧相反，**fail closed**：`apply_next_level` 必须写账本，写不出就抛错。审计宽松、写入严格，两边不混。

### 4.3 子职判定替换（提交 B）

`advancement.py:208` 的条件替换为：

```python
pending_subclass = any(
    item["id"] == "subclass_ref" for item in self.missing_choices(character)
)
if pending_subclass:
    ...抛出 requirement...
```

`advancement.py:223-224` 改为：

```python
elif choices.get("subclass_ref") and selected_subclass_ref != feature_state.get("subclass_ref"):
    errors.append("subclass_ref cannot be changed once selected")
```

即：**保留「不可改选」，去掉「不可补选」**。

`_subclass_features` 增加分支：

| 情形 | 取哪些特性 |
|---|---|
| 角色原本已有 `subclass_ref`（正常升级） | 仅 `== target_level` |
| 角色原本无 `subclass_ref`，本次补选 | 所有 `<= target_level` |

原因：补选时角色第一次获得子职，低等级的子职特性对它是全新的；只取目标等级会让 3 级的选项特性（如 `cutting_words`）永远不被询问。

### 4.4 专长等级（提交 B）

不新增字段。专长等级由账本派生：

```python
def feat_grants(self, payload) -> list[dict]:
    # [{"ref": "feat:defense", "level": 4, "source": "advancement"}]
```

`feat_refs` 保持原样，作为所有权索引，现有读取方一行不改。

建卡期来源（`origin` / `species` / `class_feature`）不从账本派生，从 `build` 读取；检测只针对 `advancement` 来源的专长槽。

### 4.5 ASI 来源（提交 B）

不改变 `abilities` 的权威地位。来源是**并行账本**，`abilities` 仍是派生结果，所有读取方不动。

补齐 ASI 时必须用与升级相同的回溯公式：

```python
con_delta = new_con - old_con
hp_gain += current_level * con_delta
```

否则补 4 级 ASI 到 CON 会少补 4×1 点 HP。

### 4.6 补齐事务（提交 C）

#### 接口

```
GET  /api/character-cards/{card_id}/choices/pending
POST /api/character-cards/{card_id}/choices/complete
```

写操作复用 `apply_card` 的两件东西，不新造并发语义：

- `operation_id` 幂等（重复提交返回 `duplicate: true`）
- `expected_revision` 乐观锁（不匹配返回 `STALE_CHARACTER_REVISION`）

#### 补齐后的重算顺序（固定，不可调换）

1. 写入对应事实（`subclass_ref` / `advancement_records` / 法术 ref 数组）
2. `_update_derived`（技能、豁免、AC、被动感知）
3. HP 增量：
   - 子职：`feature_max_hp_bonus(after) - feature_max_hp_bonus(before)`
   - ASI：`current_level * con_delta`
   - 两者同时补齐时**先算子职再算 ASI**，因为 ASI 的 CON 变化会改变 `feature_max_hp_bonus` 里按等级计的每级加值
4. `max_hp` 与 `hp` 同步增加（`hp` 取 `min(max_hp, hp + gain)`）
5. reconcile 投影
6. `ruleset_revision + 1`，写 `operation_log`

#### 不可补齐的项

不在 `missing_choices` 里出现的项一律不补。特别地：

- 已选子职不可更换（只允许从空到有）
- 已记录的 ASI 不可重算（数值已并入 `abilities`，没有来源就不能反推）
- 法术只补数量，不校验学派/等级合法性

### 4.7 视图与前端（提交 C）

三处读同一个 `missing_choices`：

| 位置 | 表现 |
|---|---|
| 升级面板顶部 | `missing_choices` 非空时先渲染补齐区块，「确认升级」继续灰着 |
| 角色卡详情 | 提示条「还有 N 项待完成」+ 逐项入口 |
| GM 面板 | 同源，DM 能看到谁的卡缺项 |

## 5. 改动文件白名单

**提交 A**

- `src/rulesets/dnd2024/progression/advancement.py`（写账本）
- `tests/rulesets/test_dnd2024_advancement_records.py`（新建）

**提交 B**

- `src/rulesets/dnd2024/progression/advancement.py`（`missing_choices`、判定替换、`_subclass_features` 分支）
- `tests/rulesets/test_dnd2024_missing_choices.py`（新建）
- 受影响的既有测试：`test_dnd2024_progression.py`、`test_dnd2024_improved_critical.py`（若断言依赖旧触发条件）

**提交 C**

- `src/rulesets/dnd2024/progression/advancement.py`（补齐事务）
- `src/webui/services/ruleset_advancement.py`（两个服务函数）
- `src/webui/routes/character_cards.py`（两条路由）
- `frontend-v2/src/api/rulesets.ts`、`frontend-v2/src/api/types.ts`
- `frontend-v2/src/features/rulesets/dnd2024/progression/Dnd2024AdvancementPanel.vue`
- `frontend-v2/src/features/rulesets/ProfessionalCharacterCenter.vue`
- `tests/rulesets/test_dnd2024_choice_repair.py`（新建）

白名单外文件一律不动。需要动时必须先停下说明理由。

## 6. 编号测试矩阵

| # | 断言 | 提交 |
|---|---|---|
| 1 | 3 级选子职后，`advancement_records` 有且只有一条 `level: 3` 且含 `subclass_ref` | A |
| 2 | 4 级选 ASI+专长，记录同时含 `ability_score_increase` 与 `feat_ref` | A |
| 3 | 未做选择的等级不写记录（无空对象） | A |
| 4 | `advancement_records` 的 `level` 严格递增；重复写入被拒 | A |
| 5 | 建卡（1 级）不写账本 | A |
| 6 | 账本内容与 `progression.history` 对应条目一致 | A |
| 7 | 5 级无子职 → `missing_choices` 含 `subclass_ref`，`level == 3` | B |
| 8 | 6 级无子职 → 仍含 `subclass_ref`（不会因为没有授予就消失） | B |
| 9 | 已选子职 → 不再出现在 `missing_choices` | B |
| 10 | 缺子职的卡升级被拒，`errors` 含 `subclass_ref is required` | B |
| 11 | 补选子职时 `gained_feature_ids` 含 3 级与目标等级的全部子职特性 | B |
| 12 | 已有子职再提交别的值 → 拒绝，错误串为 `cannot be changed once selected` | B |
| 13 | 缺 4 级 ASI → `missing_choices` 含 `ability_score_increases`，`level == 4` | B |
| 14 | Fighter 6 级缺 ASI 也被检出（非通用 4/8/12/16 等级） | B |
| 15 | Rogue 10 级缺 ASI 也被检出 | B |
| 16 | 19 级缺 Epic Boon 被检出 | B |
| 17 | 旧存档无 `advancement_records` → ASI/专长检测全部跳过，不误报 | B |
| 18 | 旧存档无 `advancement_records` 但缺子职 → 子职仍被检出（子职不依赖账本） | B |
| 19 | 法术准备位未填满 → `missing_choices` 含对应项，`actual < required` | B |
| 20 | 法术准备位已满 → 不出现该项 | B |
| 21 | 特性授予的常驻法术不计入准备位数量 | B |
| 22 | 补齐子职后，5 级 Sorcerer 的 `max_hp` 增加 `3 + 2×1 = 5` | C |
| 23 | 补齐 4 级 ASI 到 CON，`max_hp` 增加 `current_level × 1` | C |
| 24 | 补齐后 `derived`（技能/豁免/AC）同步重算 | C |
| 25 | 同 `operation_id` 重复补齐返回 `duplicate: true` 且不二次加 HP | C |
| 26 | `expected_revision` 不匹配返回 `STALE_CHARACTER_REVISION` | C |
| 27 | 补齐后 `missing_choices` 为空 | C |
| 28 | 20 级角色也能补齐（不依赖升级动作） | C |

## 7. 非目标与停止条件

### 非目标

1. **不做全量重放审计**。旧存档没有可靠的逐级选择历史，重放会与玩家的真实选择产生无法区分的分歧。
2. **不做子职改选**。写入后不可更换，只有 GM 显式重置才有入口（另一切片）。
3. **不做法术合法性审计**。只比对数量，不校验学派、等级、来源。
4. **不改变 `abilities` 的权威地位**。来源账本并行存在，不替换数值读取路径。
5. **不引入 XP / 里程碑**。本切片不改变「玩家主动发起升级」这一触发方式。
6. **不动 `class_feature_grants`**。它只写不读，清理属于独立议题。

### 停止条件

1. 若发现 `advancement_records` 与 `history` 在任何既有测试路径上不一致，停止并先查清哪一个是错的。
2. 若 `missing_choices` 在既有测试夹具上产生大面积误报（超过 20 条），停止——这说明有某条正常路径不写账本，必须先补写入侧而不是放宽检测。
3. 若补齐 ASI 会改变既有角色的 `abilities` 语义（即有人依赖「无来源也可自由加属性」），停止并上报。
4. 若前端改动超出白名单两份文件，停止并拆分。

## 8. 验收命令

```bash
pytest tests/rulesets -q -n auto --dist loadfile --basetemp=C:/pt-h3
```

- A 完成后：聚焦 `test_dnd2024_advancement_records.py` + `test_dnd2024_progression.py`
- B 完成后：聚焦 `test_dnd2024_missing_choices.py` + `test_dnd2024_progression.py` + `test_dnd2024_improved_critical.py`
- C 完成后：聚焦 `test_dnd2024_choice_repair.py` + 全量回归
- 全量失败集合必须与基线逐条相同（当前基线 11 条 `test_dnd2024_character_builder.py` 的 `ability:con in background:soldier`）
- `ruff` 未安装，用 `python -m py_compile`；`git diff --check` 必须干净

## 9. 执行报告（执行侧填写）

### 9.1 基线与提交

### 9.2 账本结构与写入点

### 9.3 四类缺项判定的实现位置

### 9.4 子职判定的替换对照（旧条件 / 新条件）

### 9.5 旧存档 fail open 的实测证据

### 9.6 派生重算顺序与 HP 增量验证

### 9.7 白名单对照

### 9.8 测试矩阵 1–28 通过情况

### 9.9 #734 描述需要同步修改的部分

### 9.10 偏差、停止原因与未决事项
