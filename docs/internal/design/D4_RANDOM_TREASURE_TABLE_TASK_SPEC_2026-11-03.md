# 切片任务书 D4：随机宝物表（Random Treasure Tables）

- 阶段：1D 物品原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1D 切片顺序表 D4 ——"表定义（嵌套 + 防环）；稳定选取；事件记录掷值与落点；归属探索/结算层"
- 前置依赖：无数据依赖，可与 D1–D3 并行；但**建议排在 D3 之后提交**，避免两条在途 PR 同时改 `exploration/engine.py`。
- 锚点基线：`9c2ca59d`（PR #646 head）；若与 D3 并行，执行侧必须重读 exploration 引擎漂移。
- 状态：未开工

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **随机表原语**：
   - 出处：DMG2024 随机魔法物品 / 宝藏 / 宝石 / 艺术品等随机表（执行侧在 `.rulebooks` 定位并回填；本机无 `.rulebooks`，禁止编造行号）。
   - 语义：一次"滚表"= 按表声明的骰式掷一次 → 落入唯一区间 → 产出结果；结果可以是物品、数量、或**另一张表**（嵌套）。
2. **归属层**：滚表是 GM 在探索/结算层的操作，**不是战斗内动作**，不进 `combat.INTENT_TYPES`。
3. **奖励权威**：滚表只决定"给什么"，**不直接写背包**；结果必须走既有奖励桥与 proposal/GM 闸门（`content/rewards.py`），与模块奖励同一条权威路径。
4. **可复现**：掷值与嵌套路径全部写入事件；回放同一事件批次不得重掷、不得得到不同结果。
5. **与 Wild Magic 的关系**：只共用"表"这一抽象（掷骰→区间→结果）；**不共用意图、触发点或事件**。Wild Magic 是战斗内触发，本切片是 GM 结算动作。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **许可裁剪：不导入 DMG 表内容**。本切片只交付**表机制**与测试用表；DMG2024 的宝物堆 A–I、随机魔法物品表等内容归阶段二 I0–I3，且必须先核对 CC-BY/SRD 5.2.1 覆盖范围，不得把本地规则书文本当分发源。
2. **裁剪：加权无骰表**。本切片只支持"骰式 + 区间覆盖"的表（DMG 形式）；纯权重表（无骰、按 weight 抽取）不落，避免两套选取语义。
3. **裁剪：多结果并掷与"掷两次取高"等修饰**。一张表一次只掷一次骰式。
4. **裁剪：玩家主动滚表**。滚表仅 GM；玩家侧不新增意图与按钮。
5. **裁剪：前端表格编辑器**。表数据用 JSON 交付，前端只在既有 GM 动作区增加一个滚表入口与结果文本展示。
6. **裁剪：战斗内随机触发**（如 Wild Magic 涌动表本身）。本切片不实现 Wild Magic，只保证表原语可被它日后复用。
7. **裁剪：货币与价格的自动生成**。结果里的货币数量只是固定数值声明，不接商店/价格系统。

## 2. 代码事实锚点（基线 `9c2ca59d`，行号需重读核对）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/exploration/engine.py` L35 | `EXPLORATION_INTENT_TYPES={"exploration.cast_spell"}`；新意图注册点 |
| A2 | exploration/engine.py `resolve_intent` L275-300 | `rng` 已注入；`intent.submitted` 审计事件先例；结算产出 EventBatch |
| A3 | exploration/engine.py `_reduce_event` L443 / `apply_batch` L410-422 | 探索层事件归约入口；新事件类型必须在此登记 |
| A4 | `src/rulesets/dnd2024/content/rewards.py` L45 `reward_intent_from_ref` / L85 `reward_intents_from_outcome` | 奖励桥 API：ContentRef → reward intent，**绝不直接写 inventory**；`filter_unconfirmed_purchase_grants` 等闸门不变 |
| A5 | `src/rulesets/dnd2024/campaign/engine.py` L18-29、L404-405 | 战役意图集合含 proposal/party_decision 族；`resolve_intent(..., rng)` 当前 `del rng`——**本切片不往战役层加随机**，避免把随机引进一个声明为确定性的引擎 |
| A6 | `src/rulesets/dnd2024/combat/primitives.py` L15-24 | 战斗意图集合；本切片明确**不碰** |
| A7 | `src/engine/dice_rng.py:roll_die` L33-37、`roll` L42-53 | 无播种；`DiceResult` 带 `rolls`/`total`；滚表复用 `roll(formula)` 以获得可审计骰迹 |
| A8 | `src/rulesets/bundle.py:_load_entities` L190-224、`_validate_references` L225-256 | 新表实体必须带 `schema_version/kind/id/source_ref/automation_level`；`table_ref`/`item_ref` 这类 `_ref` 字段会被强制解析，**嵌套引用必须真实存在，否则装载即失败** |
| A9 | `src/rulesets/dnd2024/content/contracts.py` L175 起 | content-pack item 只承载身份；滚表结果引用物品时用 canonical ref，不携带机械 |

## 3. 白名单改动规格（逐文件）

### 3.1 新增 `src/rulesets/dnd2024/treasure/`（`__init__.py`、`tables.py`）

随机表的唯一 owning module：

- 表 schema（`kind: "treasure_table"`）：
  - `roll`：骰式字符串（如 `1d100`），必须能被 `dice_rng.parse_dice_formula` 解析；
  - `entries`：数组，每项 `{"from": int, "to": int, "result": {...}}`；
  - `result` 只允许三种形状之一：`{"table_ref": "treasure_table:<id>"}`、`{"items": [{"item_ref": "item:<id>", "qty": int}]}`、`{"coins_gp": int}`；未知键 fail closed。
- 装载校验（全部 fail closed）：
  1. 区间整数、`1 <= from <= to <= 骰面`；
  2. 区间**不重叠且完整覆盖** 1..骰面（有洞或有叠都拒绝，避免"掷出无结果"）；
  3. `entries` 按 `from` 排序存储，选取用**整数比较**，禁止浮点累加与 dict 迭代序依赖；
  4. 嵌套 `table_ref` 必须可解析（A8），递归深度 ≤ 8，且做**环检测**（A→B→A 直接拒绝）。
- `resolve_table(table_id, roll_value) -> result`：纯函数，不掷骰、不写状态；掷骰由调用方完成并传入，便于测试与回放。

### 3.2 `src/rulesets/dnd2024/exploration/engine.py`

- `EXPLORATION_INTENT_TYPES` 新增 `"exploration.roll_treasure"`；仅 GM（`submitted_by == instance.gm_uid`）。
- 结算：`roll(table.roll)`（A7）→ `resolve_table` → 嵌套时逐层掷骰并记录路径 → 产出：
  - `{"type":"dnd2024.treasure.rolled","table_ref":...,"path":[{"table_ref":...,"roll":n,"from":a,"to":b}...],"result":{...}}`
  - 结果随后转换为 reward intent（A4），走既有 proposal/GM 闸门；**本切片不产生任何直接背包写入**。
- 战斗进行中拒绝滚表（与探索层既有约束一致）。

### 3.3 数据（仅机制验证，不导入 DMG 内容）

- 新增 `templates/rulesets/dnd2024_srd/content/treasure_tables/test_gemstones.json`：一张小型测试表（1d6，区间完整覆盖），其中至少一项嵌套指向第二张测试表。
- 表内物品引用只允许指向仓库已存在的 item（含 D1 新增物品）；不新增任何 DMG 专有物品。
- `automation_level: deterministic`；source_ref 标注为本地测试表，**不得伪造 SRD 页码**。

### 3.4 前端（最小）

- GM 动作区一个"掷随机宝物表"入口 + 结果文本（表名、掷值、嵌套路径、结果）。
- 不做表编辑器、不做玩家侧入口。

### 3.5 tests

- 新增 `tests/rulesets/test_dnd2024_treasure_tables.py`；既有探索/奖励测试只允许追加。

## 4. 测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| T1 | 区间完整覆盖校验 | 有洞、有叠、越界、非整数全部装载 fail closed |
| T2 | 边界落点 | 掷出 `from` 与 `to` 都落入该区间；相邻区间不串 |
| T3 | 嵌套表 | 逐层掷骰、路径完整记录、结果正确 |
| T4 | 环检测 | A→B→A 装载即拒绝；深度 > 8 拒绝 |
| T5 | 选取确定性 | 同一掷值多次解析结果一致；与 entries 书写顺序无关 |
| T6 | 回放 | 同一事件批次重放不重掷、结果一致、不重复发奖 |
| T7 | 奖励权威 | 结果走 `reward_intent_from_ref` 与既有闸门；无直接 inventory 写入；未确认购买闸门不被绕过 |
| T8 | 权限 | 非 GM 提交拒绝；战斗中拒绝 |
| T9 | 非法结果形状 | 未知 result 键、非法 item_ref、qty 非正整数均 fail closed |
| T10 | 审计 | 事件含表引用、每层 roll、命中区间与最终结果 |
| T11 | 与战斗隔离 | `combat.INTENT_TYPES` 无新增；不新增战斗内触发 |
| T12 | 许可 | 仓库内无 DMG 专有表内容；测试表 source_ref 不伪造 SRD 页码 |
| T13 | 回归 | 探索/战役/战斗套件零断言改动通过 |

## 5. 验收命令

```bash
python -m pytest -q tests/rulesets/test_dnd2024_treasure_tables.py
python -m pytest -q tests/rulesets -k "exploration or campaign or rewards or combat"
python -m pytest -q --cov=src
```

## 6. 禁止事项

- 禁止把滚表做成战斗意图或战斗内触发。
- 禁止直接写 inventory/equipment，或绕过 proposal 闸门。
- 禁止导入 DMG 宝物表内容；禁止把本地规则书文本作为分发数据。
- 禁止用浮点权重累加或依赖 dict 顺序的选取实现。
- 禁止无环检测的递归表引用。
- 禁止在战役引擎里引入随机（A5 的 `del rng` 语义不被本切片改变）。
- 禁止为实现 Wild Magic 而顺手改战斗触发器。

## 7. 停止条件

- 表需要"无骰纯权重"或"多掷取高"语义 → 停止，回到主计划补表原语。
- 结果需要表达"玩家三选一"等决策 → 停止，那属于 proposal 决策链而非滚表。
- 嵌套表需要跨规则集引用 → 停止，source-aware 解耦（阶段二第 5 项）未落地前不做。
- 奖励桥无法承接结果类型 → 停止，不另建奖励通道。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

- 基线 commit、T1–T13 结果、嵌套与环检测证据、奖励闸门验证、许可自查结论、白名单符合性。

### 迁移债登记

| # | 债务 | 归属建议 |
|---|---|---|
| D4-1 | DMG 宝物表内容未导入（只交付机制） | 阶段二 I0–I3，先核对 CC-BY 覆盖 |
| D4-2 | 无骰纯权重表未支持 | 有真实内容需要时 |
| D4-3 | Wild Magic 涌动表未接战斗触发 | 独立 Wild Magic 切片，可复用本表原语 |
| D4-4 | 前端仅文本结果，无表编辑器 | 不排期 |
