# 切片任务书 D3b：充能耗尽销毁（Destroy on Empty）

- 阶段：1D 物品原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1D 切片顺序表 D3b ——"充能耗尽后的 d20 判定 + 事件记录 + 哨兵骰测试"
- 前置依赖：D3 合入（充能状态、消耗、激活与回充已闭环）。**不得与 D3 同 PR**。
- 锚点基线：D3 合入后的 main；行号以 `9c2ca59d`（PR #646 head）为参照。
- 状态：未开工
- 独立成片的唯一理由：**本切片引入新的随机判定分支**，与 D3 的"只走既有骰式管线"验收边界分开，避免同一 PR 内随机语义混杂。

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **耗尽销毁判定**：
   - 出处：DMG2024 第 7 章相关物品条目（典型为"当你耗尽最后一发充能时，掷 d20；若掷出 1，物品损毁"，执行侧须在 `.rulebooks` 定位原文并回填行号；本机无 `.rulebooks`，禁止编造行号）。
   - 语义：只有**因激活而恰好耗尽到 0** 时才触发判定；物品声明 `destroy_on_empty` 才参与，未声明的物品耗尽后只是不可激活。
   - 判定为**服务端 d20**，结果写入事件；掷出阈值（默认 1）→ 物品损毁。
2. **损毁的语义**：物品从持有中移除、同调结束、不可再激活；损毁不是"损坏待修"，不保留残骸状态。
3. **顺序约束**：先完成本次激活的全部效果结算，再做销毁判定——销毁不得回滚已经生效的治疗效果。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **只覆盖"激活耗尽"这一条触发路径**。回充到 0、被动流失、外部伤害导致的损毁不在本切片。
2. **裁剪：损毁时的附加效果**（爆炸、反噬伤害、召唤等）。销毁只产生移除与同调结束，附加效果另立切片。
3. **裁剪：非充能物品的耐久度/破损系统**。不引入耐久字段。
4. **裁剪：诅咒物品的强制保留**。若未来诅咒物品声明"不可销毁"，由诅咒切片处理，本切片不预留。
5. **RNG 只此一处**：本切片除销毁 d20 外不得新增任何随机分支；治疗骰等仍走 D3 既有管线。
6. **不改 D3 的激活语义**：只在激活结算末尾追加判定，不重写 `_activate_item_events` 的成本与效果顺序。

## 2. 代码事实锚点（行号以 `9c2ca59d` 为参照）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/combat/engine.py` L606 起 | `rng` 由引擎注入到各 `_*_events`；销毁判定必须使用注入的 rng，禁止新建随机源 |
| A2 | `src/engine/dice_rng.py:roll_die` L33-37 | 骰值入口 `random.randint`，**无播种**；可复现性只能靠事件记录骰值，不能靠重掷 |
| A3 | `src/rulesets/dnd2024/combat/reducer.py` L211-223 | 资源消耗写回 canonical 的范式：单一权威、不得为负 |
| A4 | `src/rulesets/dnd2024/combat/reducer.py` L359-360、L449 | 死亡/状态写入点先例：状态变更必须伴随事件，不允许静默改 |
| A5 | `src/rulesets/dnd2024/character/attunement.py`（D2） | 销毁后必须清除该 ref 的同调；同调读取只走 `attuned_refs` |
| A6 | `src/rulesets/dnd2024/exploration/engine.py` L443-528 | 探索层事件归约与 HP 应用；探索层激活耗尽同样要判定 |
| A7 | 既有测试先例（P5/P6 审核记录） | "哨兵骰 999 未被弹出"：用固定 rng 断言**不该掷骰时一个随机数都不消费**；本切片反向使用——声明了销毁的物品才允许消费恰好一次 d20 |
| A8 | `src/rulesets/dnd2024/items/charges.py`（D3） | 充能读取与扣减的唯一入口；销毁判定必须读扣减后的 `current == 0`，而不是猜 |

## 3. 白名单改动规格（逐文件）

### 3.1 `src/rulesets/dnd2024/items/charges.py`

- 新增 `validate_destroy_declaration(item)`：`destroy_on_empty` 只允许 `{"die": "1d20", "threshold": 1}` 形状；`die` 固定 `1d20`，`threshold` 为 1–20 整数；未知键 fail closed。
- 新增 `should_check_destroy(item, remaining_charges) -> bool`：仅当声明存在且 `remaining == 0` 时为真。

### 3.2 `src/rulesets/dnd2024/combat/resolution.py` 与 `exploration/engine.py`

- 在激活结算**末尾**（效果事件之后）追加：满足 A8 条件时 `rng.randint(1,20)` 一次，产出事件：
  - `{"type":"dnd2024.item.destroy_check","item_ref":...,"roll":n,"threshold":t,"destroyed":bool}`
  - 若 destroyed：追加 `{"type":"dnd2024.item.destroyed","item_ref":...}`，并在 D2 同调段中移除该 ref。
- 未声明销毁的物品：一次随机数都不得消费（哨兵断言）。

### 3.3 `src/rulesets/dnd2024/combat/reducer.py`

- 登记两个新事件类型与字段白名单；`item.destroyed` 的归约：从 canonical 持有/装备与同调中移除该 ref，记操作日志；**不得**删除同名其它物品。

### 3.4 tests

- 新增 `tests/rulesets/test_dnd2024_item_destroy.py`；既有 D3 测试只允许追加。

## 4. 测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| T1 | 未声明销毁的物品耗尽 | 无 `destroy_check` 事件；哨兵骰未被弹出 |
| T2 | 声明销毁、激活后仍有剩余充能 | 不掷骰、不判定 |
| T3 | 声明销毁、恰好耗尽且掷出 2 | 物品保留、事件 `destroyed:false`、roll 记录为 2 |
| T4 | 恰好耗尽且掷出阈值 1 | 物品移除、同调清除、`destroyed:true`、不可再激活 |
| T5 | 销毁顺序 | 治疗效果先结算且不被回滚；销毁事件在其后 |
| T6 | 探索层激活耗尽 | 与战斗内同一判定语义与事件形状 |
| T7 | 非法声明 | `die` 非 1d20、threshold 越界、未知键全部 fail closed |
| T8 | 回放同一批次 | 不重掷、不重复移除、事件幂等 |
| T9 | 同名他物 | 销毁只影响该 ref，不误删背包其它物品 |
| T10 | 回归 | D1/D2/D3 目标测试零断言改动通过 |

## 5. 验收命令

```bash
python -m pytest -q tests/rulesets/test_dnd2024_item_destroy.py
python -m pytest -q tests/rulesets -k "item or attunement or combat or exploration"
python -m pytest -q --cov=src
```

## 6. 禁止事项

- 禁止在效果结算之前做销毁判定。
- 禁止用 `random` 模块直接取数而不走注入的 rng。
- 禁止新增耐久度、损坏待修、爆炸反噬等本切片外语义。
- 禁止把销毁写成"数量 -1"而不处理同调与装备引用。
- 禁止让未声明销毁的物品消耗任何随机数。

## 7. 停止条件

- 某物品的销毁附带额外效果（伤害/召唤） → 停止，另立切片。
- 销毁需要移除非 canonical 的第二处状态副本 → 停止，先收敛状态权威。
- 销毁判定需要在探索层之外（如战役层）触发 → 停止并登记触发面。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

- 基线 commit、`.rulebooks` 行号回填、T1–T10 结果、哨兵骰证据、白名单符合性。

### 迁移债登记

| # | 债务 | 归属建议 |
|---|---|---|
| D3b-1 | 销毁时的附加效果（爆炸/反噬）未落 | 独立切片 |
| D3b-2 | 非充能物品的耐久/破损未落 | 不排期，等内容需要 |
