# S0-C2A 任务书：掩护等级（声明式事实 + 来源合并 + 效应轴向）

| 项 | 值 |
|---|---|
| 切片编号 | S0-C2A（Cover 前置第 1 片，F17/F26 的共同前置） |
| 验收载体 | `cover_for()` 单一查询、GM 声明式掩护、光环来源、效应轴向 |
| 规则基准 | 官方 SRD 5.2.1 Cover：half `+2 AC / +2 Dex`、three-quarters `+5 / +5`、total 不可被直接选为目标 |
| 代码基线 | `origin/main@9ab8389c` |
| 前置任务 | 无（复用既有 `_cover_record` 查询位与条件来源） |
| 状态 | 待执行 |

## 1. 范围与完成定义

1. 掩护是**有序等级**（`none < half < three_quarters < total`），不是一个加值。加值由等级推出，消费者只读等级与派生加值。
2. 本切片做三种事实来源里的两种：**GM 声明**（剧场模式，DM 口述"他躲在掩体后，半身掩护"）与**能力来源**（既有 Smite of Protection 那道灵光）。几何来源（谁挡住了谁）不做。
3. 效应侧要能声明**轴向**：水平掩护对"从上方落下"（冰风暴、焰击）与"脚下发生"（地震）的效应无效。声明缺失时按 `horizontal`。
4. 消费者接两处已有边界：攻击/法术攻击的 AC（已读 `_cover_record`）、Dex 豁免（`_cover_save_bonus`）。并向 F17 提供 `cover_level(combat, actor_id)` 供"回合结束时处于 ¾ 或 Total"判定。
5. 声明值必须服务端权威：客户端只能提交等级枚举与目标，不能提交加值；等级非法、目标不存在、非 GM 提交一律拒绝。

## 2. 规则依据

- half cover：AC 与 Dex 豁免 `+2`；three-quarters：`+5`；total：不能被攻击或法术直接选为目标（本切片不实现该拦截，见 §7）。
- 掩护对豁免生效的前提是"效应源与目标被障碍隔开"；剧场模式没有几何，本切片**不做异侧判定**，声明即视为对该效应成立（除轴向声明为忽略者）。
- 从上方落下或被地面效应覆盖的场合，水平掩护不参与——这是效应自身的性质，不需要几何。

## 3. 定点代码事实（基线已核实）

| 事实 | 位置 |
|---|---|
| 已有唯一掩护查询位，返回 `armor_class_bonus/dex_save_bonus/sources`，且"多来源不叠加、取最好" | `combat/engine.py::_cover_record` |
| 豁免侧的读取入口 | `combat/engine.py::_cover_save_bonus` |
| 攻击与法术攻击各自调用 `_cover_record` | `combat/resolution.py:1335`、`:2858` |
| 豁免调用 `_cover_save_bonus` | `combat/resolution.py:3001`、`:4963` |
| 现有唯一来源是圣武士灵光（按条件声明半径与加值） | `_cover_record` 内 `SMITE_OF_PROTECTION_CONDITION` |
| 战斗状态在 `combat.started` 一次性建立 | `combat/reducer.py` |
| 意图闭集与分支 | `combat/primitives.py::INTENT_TYPES`、`combat/engine.py::resolve_intent` |

## 4. 精确实现契约

### 4.1 等级与派生

1. 闭集：`none` / `half` / `three_quarters` / `total`。
2. 派生：`half → 2`、`three_quarters → 5`、`total → 5`（AC 与 Dex 同值）；`none → 0`。
3. `cover_row(level, sources)` 返回 `{level, armor_class_bonus, dex_save_bonus, sources}`；`sources` 是来源标签数组（`gm` / `aura:<holder_id>`），按贡献顺序。

### 4.2 声明式来源

1. 意图：`combat.set_cover {target_id, level}`，仅 GM 可提交；`level=none` 表示撤销声明。
2. 事件：`dnd2024.cover.declared {target_id, level, source: "gm"}`。
3. 状态：`combat.cover = {actor_id: {level, source}}`；`none` 时从状态中删除该 actor 的键（不写 `{level: none}` 的墓碑）。
4. 校验：目标必须在当前战斗里（`player:*` / `enemy:*` / companion）、等级必须在闭集内、战斗必须 active。
5. 声明是**持久事实**，随战斗生灭；战斗结束不落盘到角色卡。

### 4.3 合并规则

1. **声明优先**：存在 `combat.cover[target]` 时，该等级就是答案，其它来源只进 `sources` 备查，不参与取最高。
2. 无声明时，在派生来源（当前只有灵光）中取等级最高者；同等级多个来源只算一次（`sources` 全列）。
3. 灵光来源的等级固定为 `half`（其声明维持 +2；若未来某来源声明 +5，按等级映射，不允许直接给数字绕过等级）。

### 4.4 效应轴向

1. 效应声明新增 `cover_interaction`：缺省 `"horizontal"`；`{"mode": "ignored", "reason": "above" | "ground" | "explicit"}`。
2. 豁免消费者在 `mode == ignored` 时**不读**掩护加值，并在豁免事件里记 `cover_interaction: "ignored"` 与 `reason`，便于审计。
3. 攻击/法术攻击恒按 `horizontal` 读（攻击永远在水平面内发生）。

### 4.5 消费者与投影

1. `cover_level(combat, actor_id) -> str`：供 F17 的"回合结束时处于 ¾ 或 Total"判定，未声明且无来源时返回 `none`。
2. 攻击/法术攻击的 AC 加值继续由 `_cover_record` 提供，行为不变（等级→加值映射后数值与今天相同）。
3. Dex 豁免按 §4.4 过滤。
4. actor view 新增 `cover`：`{level, sources}`，供界面显示"该目标处于半身掩护"。

## 5. 改动白名单

1. 新建 `src/rulesets/dnd2024/combat/cover.py`（等级闭集、派生、合并）
2. `combat/primitives.py`（意图闭集）
3. `combat/validation.py`（`combat.set_cover` 校验）
4. `combat/engine.py`（意图分支、`_cover_record` 改为等级合并、`cover_level`）
5. `combat/reducer.py`（`cover.declared` 状态、`combat.started` 初始化 `cover`）
6. `combat/resolution.py`（豁免侧轴向过滤、事件字段）
7. `combat/view.py`（actor view 的 `cover`）
8. `templates/.../combat_catalogs/srd_combat_core.json`（为 6 条区域法术显式标 `cover_interaction`，其余不动）
9. 新建 `tests/rulesets/test_dnd2024_cover_levels.py`

不得改：几何/移动、生物掩护、Hide、目标可选中性、前端。

## 6. 逐项编号测试

1. 等级闭集与派生：四个等级的 AC/Dex 加值；非法等级拒绝。
2. 声明往返：GM 声明 → 事件 → 状态 → 视图；`none` 撤销后键消失。
3. 目标校验：不存在的 actor、非 active 战斗、玩家提交均拒绝。
4. 声明优先：声明 `three_quarters` 时，即使灵光在范围内也返回声明等级（来源仍列灵光）。
5. 无声明时灵光给出 `half`，且多个灵光不叠加。
6. 攻击 AC：`half` → +2、`three_quarters` → +5，同一目标不重复叠加。
7. 豁免 Dex：`half` → +2；`cover_interaction.mode == ignored` → 0 且事件记 reason。
8. 攻击侧不受轴向影响（`ignored` 的效应不会让攻击少算掩护）。
9. `cover_level()` 供 F17 使用：`three_quarters` / `total` 返回原值，`none` 与缺省返回 `none`。
10. 状态生命周期：战斗结束不残留 `cover`；新战斗从空开始。
11. replay：同一批事件重放得到相同 `cover` 状态（无 RNG、无时间依赖）。
12. 既有回归：未声明掩护的战斗与今天逐项一致（攻击/豁免数值不变）。

## 7. 非目标与停止条件

**非目标**：几何遮挡（谁挡住谁）、生物掩护（盟友挡半身）、Total 阻止被选中、Hide 的视线前提、2D 地图、异侧判定。

**停止条件**：
1. 若 `_cover_record` 的调用点无法只改查询而不改调用签名（例如某个消费者自己算加值），停止并报告，不得复制第二份判定。
2. 若总掩护的"不可被选为目标"无法只作为校验层的一条判断加进去（需要改目标解析器），留到下一切片，不在本切片夹带。
3. 需要改 reducer 之外的状态容器或新增存档迁移时停止。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_cover_levels.py -q
python -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_channel_control.py tests/rulesets/test_dnd2024_invisibility.py -q
python -m pytest tests/rulesets -q
ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_cover_levels.py
mypy
```

## 9. 执行报告模板

- 基线 / 分支 / 提交：
- 等级→加值映射与合并规则的实现位置：
- 声明式来源的意图/事件/状态形状：
- 轴向声明落在哪 6 条法术、豁免事件的字段：
- 测试 1–12 逐项结果：
- 偏差与未决（尤其"总掩护不可被选中"的落点）：
