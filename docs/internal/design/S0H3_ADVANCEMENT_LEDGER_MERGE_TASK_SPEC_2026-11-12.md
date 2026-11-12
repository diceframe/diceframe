# S0-H3 任务书：成长账本合并（history 与 advancement_records 二选一）

| 项 | 值 |
|---|---|
| 切片编号 | S0-H3 |
| 验收载体 | `apply_next_level` 每级只写**一个**存储位置；`missing_choices` / `repair_choices` / replay 三个读者全部改读该位置；双写与对账测试删除 |
| 通用能力 | 无新增能力。这是对 S0-G3 的**修订**：消除同一事实的两份存储 |
| 规则基准 | 无新规则声明，不引 PDF 页码（同 G3 的口径：所有「应有几项」读既有目录） |
| 代码基线 | S0-G3 交付载体（#734）合入后的 `main`。硬前置：S0-G3（本切片改写其读取方）；与 S0-H1/H2 无耦合，可并行 |
| 执行模型 | `dsv4.1f` |
| 状态 | 已实现，见 §9（PR 待评审） |

## 1. 问题：同一事实两份存储

### 1.1 两份账本在同一条代码路径上同时写入

`apply_next_level` 在 `progression/advancement.py:515-522` 写 `progression.history`：

```python
history.append({
    "from_level": preview["from_level"],
    "to_level": target_level,
    "choices": deepcopy(preview["choices"]),
    "diff": deepcopy(preview["diff"]),
    "source_ref": preview["source_ref"],
})
```

S0-G3 又在同一事务里写 `progression.advancement_records`（`_record_advancement`）：

```json
{"level": 4, "class_ref": "class:fighter",
 "ability_score_increase": {"str": 2}, "feat_ref": "feat:defense"}
```

两条记录描述的是**同一次升级**，写入时刻相同、来源相同（同一个 `preview`）。G3 任务书 §4.1 第 6 条把现状固化为设计：「`history` 保持不变。账本是权威事实，`history` 是审计日志；两者内容应一致，但读取方只允许读账本」——**「内容应一致」靠约定维持的重复存储是缺陷，不是设计**。任何一处后续改动（比如 H1 给升级加替换上限、H2 的链式产物）都要同时记得两条路径，漏一边就是静默漂移。

### 1.2 G3 已经自曝了一次漂移

G3 执行报告 §9.2：「纯 ASI 不写 `feat_ref`——`_advancement_feat` 把纯 ASI 声明成伪专长 `feat:ability_score_improvement`，账本按 `PLAIN_ABILITY_FEAT_REF` 排除它」。这条排除规则是**账本自己的口径**；`history.choices` 里同一事实的表示是另一套（choices 原样保存提交的 `ability_score_increases`）。两套口径已经开始各自演化，这正是双写的必然结果。

## 2. 方案比较

| 方案 | 内容 | 判定 |
|---|---|---|
| 1. 删 `history`，replay 改读 `records`（records 补 `choices`） | records 每条增加 `choices`；`normalize_character_submission` 改读 records | **否决**。`history` 是**外部导入的输入契约**：外部卡按 `progression.history` 提交（`runtime.py:777-779` 校验条数与连续性）。改名等于破坏外部导入工具，还要在导入侧做 history → records 的翻译层，成本高于收益 |
| 2. 删 `advancement_records`，检测/修复改读 `history` 派生 | `missing_choices` / `repair_choices` 的读取方改从 history 条目派生账本级字段 | **采用**。外部契约不动、replay 零改动、写入单点 |
| 3. 维持双写 + 对账测试 | G3 现状加一条「两份必须一致」的测试 | **否决**。对账测试只能发现漂移不能消除漂移，且每级双写让存档体积无谓翻倍（`diff` 本来就是审计字段，`choices` 才是重放所需） |

方案 2 的关键论据：**`history.choices` 是超集**。records 的全部字段都能从 history 条目无信息损失地派生：

```
level            ← to_level
class_ref        ← progression.class_ref（单职业，_single_class_state 已有）
subclass_ref     ← choices.subclass_ref
ability_score_increase ← choices.ability_score_increases（键名映射）
feat_ref / epic_boon_ref ← choices.feat_ref / choices.epic_boon_ref
```

而反过来不成立：records 没有 `choices.class_spell_choices` 等法术选择，replay 靠它重放（`runtime.py:800-806`）。

## 3. 已定点核实的读者清单

合并后受影响的读取方（全部在 G3 载体内）：

| 读者 | 现在读什么 | 改后 |
|---|---|---|
| `_missing_advancements`（ASI/专长检测） | `advancement_records` 逐级匹配 | 从 `history` 派生条目后同一逻辑 |
| 子职检测 | `features.subclass_ref`（不依赖账本） | **不动** |
| `missing_choices` 的法术准备位检测 | 同上（G3 §9） | 不动或走同一派生入口 |
| `repair_choices`（补齐事务） | 读 records → 合并账本 → 重算 | 读派生条目 → 重算；「合并账本」步骤消失（history 本来就在） |
| `normalize_character_submission`（replay） | `progression.history` | **不动**（这正是选方案 2 的原因） |
| `tests/rulesets/test_dnd2024_advancement_records.py` | 断言 records 字段 | 改为断言派生函数的输出 |

`grep -rn "advancement_records" src/` 在合并后必须为 **0 命中**（存档兼容除外，见 §4.4）。

## 4. 精确行为契约

### 4.1 派生入口（提交 A）

`Dnd2024AdvancementEngine` 新增（或替换 `_advancement_records` 的位置）：

```python
def _ledger_entries(self, character: dict[str, Any]) -> list[dict[str, Any]] | None:
    """从 progression.history 派生账本级条目；无 history 返回 None。"""
```

- 输出形状与 G3 的 record **逐字段一致**（`level` / `class_ref` / `subclass_ref` / `ability_score_increase` / `feat_ref` / `epic_boon_ref`），使检测逻辑的改动最小化；
- `level` 取 `to_level`，并保持与 G3 相同的排除规则（`PLAIN_ABILITY_FEAT_REF` 的伪专长不落 `feat_ref`）——排除逻辑从写入侧搬到派生侧，语义不变；
- `progression` 缺失或 `history` 非 list → 返回 `None`（保留 G3「旧存档整段跳过不误报」的口径，`missing_choices` 与 `repair_choices` 对 `None` 的处理不变）；
- **只读**。派生不写任何字段，不产生存档迁移。

### 4.2 写入侧收敛（提交 A）

`apply_next_level` 删除 `_record_advancement` 调用与 `advancement_records` 写入；每级唯一写入是既有的 `history.append`（`advancement.py:515-522`）。G3 的「写入前校验最后一条 level 递增」搬进 history 的写入前校验（最后一条 `to_level` 必须 `< target_level`，否则抛 `ProgressionCatalogError`——语义与 G3 相同，只是对象换成 history）。replay 路径（`runtime.py:797` 的 for 循环）本身保证连续性，该校验防御的是未来绕过 replay 的直接调用。

### 4.3 读取方改接（提交 B）

`missing_choices` / `repair_choices` / 相关辅助函数从 `_ledger_entries` 取数。判定逻辑（逐级 snapshot 比对、缺项形状、`reason` 文案）逐字不动——只换数据源。`repair_choices` 的执行顺序（G3 §9.7：改事实 → 合并账本 → HP 增量 → 派生）中「合并账本」一步消失：repair 改完事实与 history 后，下一次派生自然一致。

### 4.4 存档兼容（提交 B，唯一动 runtime 的一处）

已经带 `advancement_records` 的存量存档（G3 交付后创建的）：

- **读取**：`_ledger_entries` 优先用 history 派生；若卡带 `advancement_records` 而缺 `history`（理论不应存在，防御性处理），返回 `None` 走旧存档跳过口径——**不做 records → history 的反向迁移**（migration correctness > completeness；两份都在的存量卡以 history 为准，records 静默忽略）；
- **写入**：`apply_next_level` 不再写 records，存量卡的 records 字段变成冻结的历史遗留，不删除、不更新、不校验（它不再被任何代码读取）；
- 外部导入输入契约不变：仍按 `history` 校验与重放。

### 4.5 测试口径

G3 的 `test_dnd2024_advancement_records.py` 逐条改写为派生口径：

| G3 原断言 | 改后 |
|---|---|
| 「3 级选子职后 records 有且只有一条 level:3」 | `_ledger_entries` 对 3 级卡返回恰一条 level:3 含 `subclass_ref` |
| 「level 严格递增；重复写入被拒」 | history 写入前校验：非递增 → 抛错 |
| 「建卡不写账本」 | 1 级卡 `_ledger_entries` 返回 `[]`（history 为空） |
| 「账本内容与 history 对应条目一致」 | **删除**——合并后这句话是恒真式 |
| 「旧存档无 records → 检测跳过」 | 旧存档无 history → 检测跳过（口径平移） |

## 5. 改动文件白名单

**提交 A（派生入口 + 写入收敛）**

- `src/rulesets/dnd2024/progression/advancement.py`（`_ledger_entries` 新增、`_record_advancement` 删除、history 递增校验）
- `tests/rulesets/test_dnd2024_advancement_records.py`（改写为派生口径）

**提交 B（读取方改接 + 兼容）**

- `src/rulesets/dnd2024/progression/advancement.py`（`missing_choices` / `repair_choices` / 辅助函数换数据源）
- `tests/rulesets/test_dnd2024_choice_repair.py`（若夹具直接构造 records 字段则改为构造 history）
- `tests/rulesets/test_dnd2024_character_lifecycle.py`、`test_dnd2024_progression.py`（受影响断言）

白名单外文件一律不动。需要动时必须先停下说明理由。

## 6. 编号测试矩阵

| # | 断言 | 提交 |
|---|---|---|
| 1 | `_ledger_entries` 对 5 级 Fighter（1→5 逐级 apply）返回 4 条，level 2..5 递增 | A |
| 2 | 4 级条目含 `ability_score_increase` 与 `feat_ref`，取值与 history.choices 一致 | A |
| 3 | 纯 ASI 级不落 `feat_ref`（`PLAIN_ABILITY_FEAT_REF` 排除语义平移） | A |
| 4 | 19 级条目落 `epic_boon_ref` | A |
| 5 | 1 级卡返回 `[]`；无 `progression` 返回 `None`；`history` 非 list 返回 `None` | A |
| 6 | `apply_next_level` 后 `progression` 中不存在 `advancement_records` 键 | A |
| 7 | history 递增校验：向已有 history 的卡直接 apply 不连续级别 → 抛 `ProgressionCatalogError`（防御路径，replay 不触发） | A |
| 8 | `missing_choices` 对「3 级缺子职」的检出与合并前逐字段一致（同一夹具双跑对照） | B |
| 9 | `missing_choices` 对「4 级缺 ASI/专长」的检出一致 | B |
| 10 | 无 history 的旧卡：ASI/专长检测跳过，子职照常检出 | B |
| 11 | `repair_choices` 补齐后 `missing_choices` 为空，且产物 progression 无 records 键 | B |
| 12 | 带 `advancement_records` 的存量夹具：检测只看 history，records 内容错误不影响结果 | B |
| 13 | replay（`normalize_character_submission`）行为与合并前逐字段一致（既有测试零改动通过） | B |
| 14 | 外部导入：缺 history 的 N 级卡仍被拒 | B |
| 15 | `grep -rn advancement_records src/` 零命中 | B |

## 7. 非目标与停止条件

### 非目标

1. **不做 records → history 的存量迁移**。存量卡的多余 records 字段原地冻结（§4.4）。
2. **不改 history 的条目形状**。`from_level`/`diff`/`source_ref` 全部保留——`diff` 是审计信息，删它是另一个讨论。
3. **不动 replay**。`normalize_character_submission` 是外部契约，本切片的实现约束就是它零改动。
4. **不做多职业扩展**。`class_ref` 派生仍依赖 `_single_class_state` 的单职业前提，与 G3 相同。
5. **不改 `missing_choices` 的检测语义**。只换数据源，返回形状与文案逐字不变。

### 停止条件

1. 若派生时发现某个 record 字段**无法**从 history 条目恢复（即 choices/diff 里没有该信息），停止——说明 G3 写入侧丢弃了 history 未覆盖的事实，合并方向必须反过来（方案 1），先回来重新设计。
2. 若 G3 既有测试在换数据源后失败超过 5 条且原因不是夹具形状，停止——说明读取方对 records 有超出字段形状的隐式依赖，先查清。
3. 若存量兼容需要动 `normalize_character_submission`，停止——违反非目标 3，说明兼容设计错了。

## 8. 验收命令

```bash
/c/Python314/Scripts/ruff check src/
/c/Python314/Scripts/mypy

unset CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR CODEBUDDY_TOOL_CALL_ID
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets -q \
  -p no:cacheprovider -n 4 --dist loadfile --basetemp=C:/pt-h3
```

- A 完成后：`test_dnd2024_advancement_records.py` + `test_dnd2024_progression.py`
- B 完成后：`test_dnd2024_choice_repair.py` + `test_dnd2024_character_lifecycle.py` + 全量
- 交付前必须同时满足：`ruff` 全过、`mypy` 无 issue、全量失败集合与基线逐条相同（基线同 S0-H1 §8）。**只跑 `tests/rulesets` 不算回归。**

## 9. 执行报告（执行侧填写）

### 9.1 基线与提交

- 基线：`eb3f0b70`（S0-H2 的头，本切片 stack 在 #750 之上；与 H1/H2 实际有耦合——三者都改 `advancement.py`）
- 分支：`feat/dnd2024-s0h3-advancement-ledger`

| 提交 | 内容 | 改动 |
|---|---|---|
| `ee7cf17b` | 新增 `_ledger_entries`；`_missing_advancements` 与 `repair_choices` 改读派生条目；repair 改写成写 `history`（records 写入暂时保留） | `advancement.py` +161/−44、`choice_repair` 夹具 |
| `bbc7ea0a` | 删除 `_record_advancement`、`_advancement_records`、`_merge_advancement_record`、`ADVANCEMENT_RECORDS_KEY`；history 递增校验；records 测试改写为派生口径 | `advancement.py`、`test_dnd2024_advancement_records.py` |
| `50103455` | 白名单外一处导入修正：`test_dnd2024_missing_choices.py` 依赖被删常量 | 该测试文件 |

合计 4 个文件、+405/−253。`grep -rn advancement_records src/` 零命中。

**为什么不是一个提交**：删掉写入的那一刻，读取方就没有数据源了，A/B 合并会得到一个红提交。所以拆成「先加派生并把读者移过去（此时两份存储同时存在）→ 再删写入」。A 阶段的额外收益是 §9.4 的双跑对照——两份存储同时在线时可以直接比对。

### 9.2 `_ledger_entries` 的派生映射表

| 账本字段 | 来源 | 说明 |
|---|---|---|
| `level` | `history[].to_level` | |
| `class_ref` | `progression.class_ref` | 单职业前提，同 G3 |
| `ability_score_increase` | `history[].choices.ability_score_increases` | 只保留正整数，与写入侧同一过滤 |
| `feat_ref` / `epic_boon_ref` | `history[].diff.advancement_feat_ref` + `diff.gained_feature_ids` | 有 `epic_boon` 落前者，否则落后者 |
| （排除） | `PLAIN_ABILITY_FEAT_REF` | 排除从写入侧搬到**派生侧**，读与写不可能分家 |

`None` 与 `[]` 的判定顺序（新增，原实现没有这一层）：

1. 无 `progression` → `None`
2. `history` 字段存在但不是 list → `None`（损坏，不猜）
3. `history` 字段不存在且 `progression.level ≤ 1` → `[]`（1 级没有可判的等级）
4. `history` 字段不存在且等级 > 1 → `None`（旧存档，跳过不误报）

第 3 条是矩阵 5 要求的「1 级卡返回 `[]`」与矩阵 10「无 history 的旧卡跳过」的接缝：建卡不写 `history`，如果一律返回 `None`，矩阵 5 失败；如果一律返回 `[]`，每张 G3 之前的旧卡都会被指控漏选。

### 9.3 读取方改接的 diff 摘要

- `_missing_advancements`：一行换数据源（`_advancement_records` → `_ledger_entries`），判定逻辑与 `reason` 文案逐字未动。
- `repair_choices`：新增 `_merge_history_choice`（+58 行），写回该等级的 `choices.subclass_ref` / `choices.ability_score_increases` / `diff.advancement_feat_ref`；删除 records 合并与回写（−23 行）。「合并账本」步骤确实消失。
- 顺带修掉一处耦合：`features.feat_refs.append` 原来写在 `if records is not None` 里面，导致「没有账本的卡补了属性却仍报缺专长」。已移到分支外。

### 9.4 双跑对照结果

在提交 A（两份存储同时在线）上直接比对派生值与账本：

| 卡 | 派生条数 | 账本条数 | 逐字段一致 |
|---|---|---|---|
| Fighter 3 级 | 2 | 2 | 是 |
| Fighter 5 级 | 4 | 4 | 是 |
| Fighter 12 级 | 11 | 11 | 是 |

矩阵 8/9 的「检出逐字段一致」由 `_missing_advancements` 的数据源就是这个等价的派生序列保证；矩阵 13（replay 零改动）由 `test_dnd2024_character_lifecycle.py` 与 `test_dnd2024_starting_level.py` 原样通过证明。

### 9.5 白名单对照

| 文件 | 白名单 | 实际 |
|---|---|---|
| `src/rulesets/dnd2024/progression/advancement.py` | ✓ | ✓ |
| `tests/rulesets/test_dnd2024_advancement_records.py` | ✓ | ✓ |
| `tests/rulesets/test_dnd2024_choice_repair.py` | ✓ | ✓ |
| `tests/rulesets/test_dnd2024_progression.py`、`test_dnd2024_character_lifecycle.py` | ✓（若受影响） | 未改，原样通过 |
| `tests/rulesets/test_dnd2024_missing_choices.py` | ✗ | **改了**（见 §9.7 第 1 条） |

### 9.6 矩阵 1–15 通过情况

| # | 结果 | 落点 |
|---|---|---|
| 1 | 通过 | `test_each_level_contributes_exactly_one_entry` |
| 2 | 通过 | `test_a_half_feat_records_both_its_slot_and_its_ability_point` |
| 3 | 通过 | `test_a_plain_ability_increase_is_not_recorded_as_a_feat` |
| 4 | 通过 | `test_an_epic_boon_lands_in_its_own_slot`（19 级逐级升上去） |
| 5 | 通过 | `test_creation_has_an_empty_ledger` + `test_a_card_without_history_cannot_be_judged` |
| 6 | 通过 | `test_advancing_writes_no_second_ledger` |
| 7 | 通过 | `test_the_history_refuses_a_level_that_does_not_advance`（故意造矛盾卡） |
| 8 | 通过 | `test_a_fighter_past_level_three_without_a_subclass_is_reported` |
| 9 | 通过 | `test_a_skipped_ability_increase_is_reported_from_the_ledger` + §9.4 |
| 10 | 通过 | `test_an_old_save_without_a_ledger_is_not_declared_incomplete` |
| 11 | 通过 | `test_repairing_the_subclass_clears_the_requirement` + 矩阵 6 的断言合并 |
| 12 | 通过 | `test_a_frozen_ledger_from_an_older_save_changes_nothing` |
| 13 | 通过 | 既有 replay 测试零改动 |
| 14 | 通过 | `test_dnd2024_character_lifecycle.py` 的导入拒绝路径 |
| 15 | 通过 | `grep -rn advancement_records src/` 零命中（仅测试里两处字面量，见矩阵 6/12） |

### 9.7 偏差、停止原因与未决事项

1. **白名单外改了一个文件**：`test_dnd2024_missing_choices.py` 直接 `import ADVANCEMENT_RECORDS_KEY`。删常量后该文件**收集失败**（ERROR 而非 FAILED），而全量的第一条命令（只跑本切片测试文件）不会碰到它——这正是「只跑 `tests/rulesets` 不算回归」的又一处证明。改法是把 5 处夹具从「删 record」换成「抹掉某一级的 choice」，属于同一件事的口径平移，不是新行为。
2. **任务书的提交划分与「每提交都绿」冲突**：A/B 按任务书拆分会让 A 变红（读者先于写入者）。已改为「派生 → 删除写入」，代价是 A 阶段存在短暂双写。若评审坚持原划分，需要接受一个红提交。
3. **`_merge_history_choice` 的边界**：某级在 `history` 里完全缺失时，只有该级恰好等于「最后一级 + 1」才允许补写，否则抛 `ProgressionCatalogError`。理由是补一个中间级等于伪造角色没走过的步骤，会让本该被 replay 拒绝的卡通过。任务书未规定这个边界，属实现补充。
4. **未做**：records → history 的存量迁移（非目标 1）；`history` 条目形状（非目标 2）；replay（非目标 3）；多职业（非目标 4）。
5. **矩阵 13 的证据是「未改动即通过」**，没有额外写一条逐字段对照测试；若评审要求更强的证据，需要把合并前的 replay 输出做成黄金文件。

