# 切片任务书 D2：同调系统（Attunement）

- 阶段：1D 物品原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1D 切片顺序表 D2 ——"canonical `attunement` 段 + 容量派生 + 单一门控访问器 + 短休绑定流程 + 结束条件 + 旧档迁移"
- 前置依赖：D1 合入（物品身份与装备数据已闭环）。本切片是 D3（充能/激活）与 +N 注入的共同前置，**不得与 D3 并行开工**。
- 锚点基线：D1 合入后的 main；文中行号以 `9c2ca59d`（PR #646 head）为参照，执行侧必须重读核对漂移。
- 状态：未开工

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **同调上限**：
   - 出处：DMG2024 第 7 章魔法物品"Attunement"节（执行侧须在 `.rulebooks` 定位原文并回填行号；本机无 `.rulebooks`，禁止编造行号）。
   - 语义：一个生物同时至多同调 3 件魔法物品；第 4 件的绑定请求**拒绝**，不是"绑上但不生效"。
   - 容量不是常量：基础 3 + 特性修正（如工匠类加槽），走 derived 派生。
2. **绑定流程**：
   - 出处：同上——同调在**短休**中通过对该物品的专注完成。
   - 语义落地：本仓库没有"短休内专注"这一时间概念，**短休完成即视为满足专注要求**；"专注 1 小时"留在叙述层，不建计时器。
   - 短休的既有约束原样生效：`_require_conscious`（昏迷不能休息 → 昏迷不能同调）、战斗中不能休息（`runtime.validate_rest_context`）。
3. **同调是效果开关**：声明了 `requires_attunement` 的物品，未同调时**只是一件普通同类物品**——保留其非机械身份与基础属性，但不给魔法加值、不可激活、不给被动效果。本切片只落地其中 AC 加值这一个消费面（见 §1.2 裁剪）。
4. **主动结束**：无需动作、随时可结束；结束后物品魔法效果立即失效。
5. **死亡结束**：角色死亡时同调全部结束；复活后**必须重新同调**，不自动恢复。
6. **换绑**：不是独立机制 = 显式解除 + 新绑定；允许在同一短休 payload 中同时声明解除与绑定，原子完成。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **同款多件的语义（本切片的核心决策）**：canonical 装备当前 `list(dict.fromkeys(refs))` 去重，同款多件会塌成一件；背包行也无实例 ID。因此：
   - 同调状态按 **canonical item ref** 记录；
   - **当角色持有（装备 + 背包合计）同一 `requires_attunement` ref 的数量 > 1 时，拒绝对该 ref 的同调与解绑请求**，给出可读消息——避免"同调一件、三件全部生效"的静默错误；
   - canonical 装备 set → slot map 的改造**不在本切片**，登记为迁移债 D2-1。
2. **裁剪：`attunement_restriction`**（"由施法者同调"、"由某职业同调"等限定）。本切片只落 `requires_attunement: bool`；限定条件待阶段二具体内容需要时补，字段与消费者同切片落地。迁移债 D2-2。
3. **裁剪：离开 100 尺 24 小时自动结束**。需要位置与时间系统，1D 剧场模式无绝对坐标、无世界时钟，不落。
4. **裁剪：战斗内同调/解绑意图**。同调只走短休路径；战斗 `INTENT_TYPES` 不新增同调类型。主动结束走 live 角色操作（非战斗内动作）。
5. **裁剪：诅咒物品、智能物品、神器**。诅咒的"无法主动结束同调"需要额外门控，本切片不预留字段。
6. **裁剪：充能与激活动作的同调门控**。那是 D3 的消费点；本切片提供访问器并在 D3 中接线，不在本切片预留 D3 的调用代码。
7. **裁剪：前端专门 UI**。本切片只产出 canonical 状态、rest 结果事件与派生容量；前端复用既有 rest 结果展示，不新增面板。若前端必须展示同调列表，限只读文本，不加交互。
8. **保留现状：休息时序**。`requires_elapsed_time_confirmation` 语义不变，同调不绕过该确认。
9. **不新增 RNG**。同调全部判定为确定性规则；禁止掷骰。

## 2. 代码事实锚点（行号以 `9c2ca59d` 为参照）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/character/derivation.py:derive_armor_class` L21-89 | **唯一 AC 公式**，建卡与 live 调和共用；数值只来自 bundle item；多甲取最优不相加、盾加值只计一次、未知 item 警告跳过 |
| A2 | derivation.py 同文件调用方 | 建卡（`builder` 链路）与 `reconciliation._armor_rules` 都调用它；新增同调参数必须同时覆盖两个调用点，缺一个就会出现"建卡算 AC、换装不算" |
| A3 | derivation.py canonical 结构 L286-310 | canonical 含 `features`/`spellcasting`/`derived`/`sources`；`derived` 段已有 `armor_class`/`speed`/`saving_throws` 等——容量派生落同段 |
| A4 | `src/rulesets/dnd2024/character/reconciliation.py` L292-326 | `_armor_rules` 组装 `armor_category_refs` → `derive_armor_training_state` + `derive_armor_class`；同调集合在此注入 |
| A5 | reconciliation.py L352-413 | `_reconcile`：注解 live 行 → 投影 item_refs → 算规则 → **幂等短路四元组**（previous_refs / previous_ac / mirror_ac / previous_training）→ 写回 canonical + mirror + revision + 操作日志。新增同调后短路条件必须扩展，否则换绑不重算 |
| A6 | reconciliation.py `_project_equipped_refs` L172-190 | 去重投影；解析不出身份只 warning 不删装备——同调门控不得改变该容错语义 |
| A7 | `src/rulesets/dnd2024/resting/engine.py:complete_short_rest` L172-216 | 短休：`sync_resources` → `_require_conscious` → 生命骰消耗 → 短休资源/法术位恢复 → 返回 `{character, events, rest, source_ref, requires_elapsed_time_confirmation}`；同调事件追加进 `events` |
| A8 | resting/engine.py `complete_long_rest` L218-271 | 长休会清 `temp_hp`、结束带 `source_spell_ref` 的条件、结束专注；**同调不随长休结束**，不得误挂到这里 |
| A9 | `src/rulesets/dnd2024/runtime.py` L832-843 | rest 入口路由与 `_live_projection`；`validate_rest_context` L846-851 战斗中禁止休息 |
| A10 | `src/rulesets/dnd2024/combat/reducer.py` L359-360、L449 | 死亡写入点：`character["conditions"]["dead"] = {...}`（死亡豁免失败 / 0 HP 受伤两处）——同调清理必须两处都覆盖 |
| A11 | combat/reducer.py L78-90 | `dnd2024.character.revived` 处理器：要求 player、清 `dead/unconscious/stable/death_saves`；复活后同调为空是天然结果，不需在此恢复 |
| A12 | `src/rulesets/dnd2024/combat/view.py` L158 | actor `equipment_refs` 从 canonical `equipment.item_refs` 静态投影；本切片不改 actor 投影 |
| A13 | `src/rulesets/dnd2024/features/resolver.py` L125/196/221/245 | `features_for` / `feature_value` / `min_feature_value` / `max_feature_value_source` 既有参数读取入口；容量修正复用，不新写特性遍历 |
| A14 | `src/rulesets/bundle.py:_validate_references` L225-256 | `_ref`/`_refs` 结尾字段会被强制解析；`attunement.refs` 是 canonical 运行时状态不是 bundle 实体，不受此约束，但 **item JSON 里禁止出现未注册的新 `_ref` 字段** |
| A15 | `src/rulesets/dnd2024/combat/reducer.py` 事件白名单 / `runtime.py` L1152-1161 | 事件类型与字段有白名单；新增 `attune`/`unattune` 事件必须登记，纯审计、不携带未声明字段 |

## 3. 白名单改动规格（逐文件）

### 3.1 新增 `src/rulesets/dnd2024/character/attunement.py`

同调的唯一 owning module，导出且只导出：

- `validate_item_declaration(item)`：`requires_attunement` 只允许 bool；非 bool 即 fail closed。
- `attuned_refs(canonical) -> frozenset[str]`：**唯一门控读取入口**，返回已同调 ref 集合；缺失/畸形段按空集处理并记 warning。
- `attunement_capacity(canonical, resolver) -> int`：基础 3 + 特性 `attunement_slot_bonus` 修正；下限 0、上限 10，越界 fail closed。
- `attunement_state_for_projection(canonical)`：供 live 投影/前端只读展示的纯函数。
- 数量守卫：`count_requires_attunement_copies(...)` 供 §3.4 的拒绝判定使用。

禁止把同调逻辑散写进 `resolution.py`、`view.py` 或 `builder.py`。

### 3.2 `src/rulesets/dnd2024/character/derivation.py`

- `derive_armor_class` 新增关键字参数 `attuned_refs: Collection[str] = ()`：
  - 护甲/盾若 `requires_attunement` 且其 ref 不在 `attuned_refs` → **不计入魔法加值**，普通部分（`ac_base`/DEX/`dex_cap`）照常；
  - 魔法加值字段 `ac_bonus`（见 §3.6 验证载体）只在已同调时参与计算；
  - 未同调不报警告、不删装备，只在派生说明里给出 `attunement_pending` 标记供展示。
- `derived` 段新增 `attunement_slots`（容量）与 `attuned_refs` 投影；建卡路径（无同调）容量为 3、集合为空。

### 3.3 `src/rulesets/dnd2024/character/reconciliation.py`

- `_armor_rules` 把 `attuned_refs` 传入 `derive_armor_class`（A2/A4）。
- 幂等短路扩展为五元组：追加 `previous_attuned == attuned_refs`；同调变化必须触发重算与 revision 递增。
- 旧档迁移：canonical 无 `attunement` 段 → 视为**空集**、写回空段、记一条 warning；**不得**自动视为全部已同调，不得删除任何装备。
- live mirror 只新增 `sheet["attunement"]` 只读投影，不改既有字段语义。

### 3.4 `src/rulesets/dnd2024/resting/engine.py`

- `complete_short_rest` 新增可选参数 `attunement: dict | None`，仅接受 `{"attune": "item:<id>", "unattune": "item:<id>"}` 两个键，其余键 fail closed。
- 顺序：先解绑后绑定，同一短休内原子完成；任一步非法则**整次同调变更拒绝**（不影响短休本身的资源恢复，但 events 中必须记录拒绝原因）。
- 绑定校验（全部 fail closed，错误消息可读）：
  1. 目标是 bundle item 且 `requires_attunement` 为 true；
  2. 角色当前持有该物品（装备或背包）；
  3. 同款数量 > 1 → 拒绝（§1.2 决策 1）；
  4. 绑定后总数 ≤ `attunement_capacity`；第 4 件拒绝；
  5. 重复绑定同一 ref → 幂等拒绝，不重复计数。
- 事件：`{"type": "attune_item", "item_ref": ...}` / `{"type": "unattune_item", "item_ref": ..., "reason": "manual"|"swap"}`。

### 3.5 `src/rulesets/dnd2024/runtime.py`

- rest 入口把 `attunement` payload 透传并做类型校验；非 dict 直接拒绝。
- 主动结束（非短休）走独立小入口：校验持有与已同调后清除 ref 并记事件，**不需要短休**（RAW 主动结束无需动作）。

### 3.6 验证载体（仅测试 fixture，不写生产内容）

- 在 `tests/rulesets/test_dnd2024_attunement.py` 内构造 `chain_shirt_attunement_fixture`：`item_type: armor`、`category: medium`、`ac_base: 13`、`use_dex: true`、`dex_cap: 2`、`ac_bonus: 1`、`requires_attunement: true`。
- 这是为覆盖“AC 加值受同调门控”而设的合成 fixture，**不得写入 SRD bundle、不得伪造 SRD source_ref，也不得把它当作真实 `+1 链甲衫` 规则条目**。
- 三件容量测试物品同样使用测试内构造；本切片不导入任何 DMG 魔法物品内容。

### 3.7 `src/rulesets/dnd2024/combat/reducer.py`

- 两处死亡写入点（A10）在写 `conditions["dead"]` 的同时清空 canonical `attunement.refs`，并追加 `{"type":"unattune_item","item_ref":...,"reason":"death"}` 事件（每件一条）。
- `character.revived` 处理器不改：复活后同调为空，必须重新短休绑定。

### 3.8 tests

- 新增 `tests/rulesets/test_dnd2024_attunement.py`。
- 既有 rest / reconciliation / reducer 测试只允许追加用例。

## 4. 测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| T1 | 未同调 +1 链甲衫 | AC = 普通链甲衫（13 + DEX 封顶 2），无 +1；`attunement_pending` 标记存在 |
| T2 | 短休绑定后 | AC +1；`derived.attuned_refs` 含该 ref；rest events 含 `attune_item` |
| T3 | 同调 3 件后再绑第 4 件 | 拒绝、零状态变更、容量不变、有可读错误 |
| T4 | 容量派生 | 基础 3；声明 `attunement_slot_bonus` 的特性按 resolver 加值；非法参数 fail closed |
| T5 | 满 3 件时同短休"解绑 A + 绑定 D" | 原子完成：A 失效、D 生效，总数仍 3 |
| T6 | 重复绑定同一 ref | 幂等拒绝，不重复计数、不刷 revision |
| T7 | 解绑未同调物品 / 未持有物品 / 非魔法物品 | 全部拒绝且不产生事件 |
| T8 | 同款数量 > 1 | 同调与解绑均拒绝（不静默让多件同时生效） |
| T9 | 主动结束（非短休） | 立即失效、无需短休、事件 reason=manual |
| T10 | 战斗中尝试短休同调 | 被既有 `validate_rest_context` 拒绝 |
| T11 | 昏迷角色 | `_require_conscious` 拒绝，同调不发生 |
| T12 | 死亡（两条路径：死亡豁免失败、0 HP 伤害） | `attunement.refs` 清空、每件一条 reason=death 事件 |
| T13 | 复活 | `dead` 清除但同调保持空；AC 回到未同调值 |
| T14 | 旧档（无 `attunement` 段）reconcile | 视为空集 + 一条 warning + 不删装备 + 不自动同调 |
| T15 | 幂等 | 同调状态不变时 reconcile 短路、revision 不刷；换绑后必须重算 |
| T16 | 零 RNG | 哨兵骰未被弹出（同调全流程不消费随机数） |
| T17 | 长休 | 同调不随长休结束；长休资源恢复语义不变 |
| T18 | 回归 | 1C 全部目标测试与既有 rest/reconciliation/reducer 测试零断言改动通过 |

## 5. 验收命令

```bash
python -m pytest -q tests/rulesets/test_dnd2024_attunement.py
python -m pytest -q tests/rulesets -k "rest or reconciliation or armor or combat"
python -m pytest -q --cov=src
```

## 6. 禁止事项

- 禁止把同调上限写成常量 `3` 而不走容量派生。
- 禁止在 AC 派生之外的文件里各写一份"是否已同调"判断；一切读取走 `attuned_refs`。
- 禁止为同款多件提供"同调一件全部生效"的捷径。
- 禁止自动把旧档角色视为已同调。
- 禁止把同调挂到长休结束、战斗结束或回合刷新上。
- 禁止新增同调相关的战斗 intent、前端交互面板或 RNG。
- 禁止在本切片实现 charges、激活动作、+N 攻击加值、`attunement_restriction`。
- 禁止用删除装备、删除背包行的方式"修正"非法同调状态。

## 7. 停止条件

- 同款多件的拒绝语义被评审推翻、要求 slot map → 停止，先单独做 canonical 装备模型改造，不在本切片夹带。
- 死亡清理需要改到 reducer 死亡写入点之外的第三处 → 停止并登记，说明死亡语义还有未覆盖路径。
- 容量修正需要新的特性参数类型或跨职业聚合规则 → 停止，回到特性目录契约。
- 短休无法在同一次调用内同时完成资源恢复与同调变更 → 停止，不得拆成两次短休来模拟。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

- 基线 commit 与行号漂移核对
- `.rulebooks` 原文行号回填（Attunement 节）
- T1–T18 逐条结果
- 白名单符合性与偏差裁决
- 旧档迁移的实际验证样本

### 迁移债登记（本切片显式产生，禁止后续切片"顺手"处理）

| # | 债务 | 归属建议 |
|---|---|---|
| D2-1 | canonical 装备去重、同款多件不可表达；同调按 ref 记录并以数量守卫兜底 | canonical 装备 set → slot map 独立切片 |
| D2-2 | `attunement_restriction`（施法者/职业/生物类型限定）未落 | 阶段二对应内容需要时连同消费者落地 |
| D2-3 | 离开 100 尺 24 小时自动结束未落 | 战术层/世界时钟具备后 |
| D2-4 | 诅咒物品的"无法主动结束同调"未落 | 诅咒物品独立切片 |
| D2-5 | 激活与充能的同调门控未接线 | D3 必须消费 `attuned_refs` |
