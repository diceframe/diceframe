# 切片任务书 D3：充能与激活动作（Charges & Activation）

- 阶段：1D 物品原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1D 切片顺序表 D3 ——"新 `activate_item` intent；charges 状态与消耗；per-item `recharge`；药水；弹药/消耗品计数"
- 前置依赖：D2 合入（同调门控 `attuned_refs` 已存在）；D1 合入（物品身份闭环）。
- 锚点基线：D2 合入后的 main；行号以 `9c2ca59d`（PR #646 head）为参照，执行侧必须重读核对漂移。
- 状态：未开工

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **激活动作**：
   - 出处：DMG2024 第 7 章魔法物品"激活"节与各类物品条目（执行侧在 `.rulebooks` 定位原文并回填行号；本机无 `.rulebooks`，禁止编造行号）。
   - 语义：使用物品需要一个动作经济成本（动作 / 附赠动作），物品自己声明；激活不是施法，不消耗法术位、不触发反制法术窗口、不需要准备法术。
2. **充能（charges）**：
   - 出处：同上——部分魔法物品有充能数，每次激活消耗声明的数量，充能耗尽后物品仍保留但无法再激活。
   - 语义：`current` 不得为负、不得超过 `max`；激活所需充能不足时**整个激活拒绝**，不部分扣减。
3. **回充（recharge）**：
   - 出处：物品条目自行声明（如"每日黎明恢复 1d6+4 充能"）。
   - **关键映射**：本仓库**没有"黎明"或世界时钟日概念**，唯一的"新一天"锚点是 `resting/engine.py:complete_long_rest`。因此物品声明 `recharge.on: "dawn"` 时，**显式等价映射为长休完成**；`"short_rest"` 映射短休；`"none"` 不回充。该等价关系必须写进数据注释与执行报告。
   - 回充的骰式（如 `1d6+4`）在长休结算时掷骰并记录进事件，回放不得重掷。
4. **消耗品计数**：药水等消耗品激活后背包数量 -1；弹药在攻击时按件消耗；数量永不为负，数量为 0 时不可使用。
5. **同调门控**：声明了 `requires_attunement` 的物品，未同调时**不可激活**——读取入口只能是 D2 的 `attuned_refs`。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **裁剪：卷轴**。卷轴 = 施法路径，需要"不消耗法术位的施法"、卷轴环阶、职业法术列表匹配与失败判定，远大于一个激活分支。本切片不做，**不预留字段**。迁移债 D3-1。
2. **裁剪：耗尽销毁**。归 D3b，本切片不落 `destroy_on_empty` 字段、不做销毁判定。
3. **裁剪：物品激活的目标类型扩展**。本切片激活目标只允许 `self` 与单一盟友的**治疗**效果；敌方目标、伤害、豁免、区域、条件施加类激活不在本切片。迁移债 D3-2。
4. **裁剪：激活的反应（reaction）成本**。`ACTION_COST_KINDS` 现有 `("bonus_action","action")`；反应激活需要反应窗口与反应资源，不在本切片。
5. **裁剪：多充能变体与阶梯效果**（同一物品按消耗充能数产生不同效果）。`activation` 一个物品只声明一种激活。
6. **裁剪：前端专门物品面板**。激活入口复用既有动作区按钮与意图提交通道；不新增库存 UI 重构。
7. **RNG 边界**：本切片只允许**既有骰式管线**（治疗骰、回充骰、攻击既有的弹药判定不掷骰）；禁止新增"随机决定是否生效/是否损毁"的分支（那是 D3b/D4）。
8. **保留现状：`consumable` 空壳**。`src/engine/combat_contracts.py:27` 的 engine `ACTION_KINDS={"attack","ability","consumable"}` 服务管理员叙事扩展，**本切片不得复用或修改它**；机械激活只进 dnd2024 `INTENT_TYPES`。
9. **保留现状：数量权威**。数量仍由 character/economy 侧权威路径提交；本切片只在权威状态上扣减，不让 LLM 直接写数量。
10. **不改 D2 的同调语义**。只读取，不修改其状态结构。
11. **裁剪：医疗包与 Healer 专长联动**。医疗包不是“自我治疗激活”的等价物，涉及专长动作与生命骰治疗，归独立专长切片；本切片不添加医疗包数据或字段。迁移债 D3-6。
12. **同款多件的充能守卫**：charges 是按 ref 保存的逐物品状态，同款多件无法区分。持有同一 charged ref 数量 > 1 时拒绝激活，不得让多件共享一份充能；实例 ID/slot map 不在本切片。迁移债 D3-7。

## 2. 代码事实锚点（行号以 `9c2ca59d` 为参照）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/combat/primitives.py` L15-24 | `INTENT_TYPES` 机械意图集合；`hide`（S-P8）是最新的新增先例——action kind + intent type + 经济授权一次到位 |
| A2 | `src/rulesets/dnd2024/combat/validation.py:_validate` L124-140 | 意图统一入口：对象检查 → intent_id 长度 → `INTENT_TYPES` 成员 → `expected_version` 冲突；`activate_item` 在此注册并追加分支 |
| A3 | `src/rulesets/dnd2024/combat/engine.py` L606/680-701 | 结算分发：按 intent 类型调用 `_*_events`；新分支插入同一分发表 |
| A4 | `src/rulesets/dnd2024/combat/resolution.py:_class_capability_events` L 起 | **原子批次范式**：动作经济 `dnd2024.action.spent` + 资源消耗 + 底层动作在同一 EventBatch；激活必须照此实现，避免"扣了充能但效果被拒"的半状态 |
| A5 | resolution.py L827-846 | 治疗管线：`spell_formula_node` →（特性最大化钩子）→ `roll_damage_node` → `max(1, healing)` → 治疗事件带 `rolls`；药水治疗**复用此管线**，不另写 HP 加法 |
| A6 | `src/rulesets/dnd2024/combat/reducer.py` L211-223 | `dnd2024.class_resource.spent` 的 canonical 写回范式：只写既有 canonical 结构、只允许 player/companion、**不允许扣成负数**；充能写回照此 |
| A7 | `src/rulesets/dnd2024/exploration/engine.py` L35-40 | 探索层意图集合 `EXPLORATION_INTENT_TYPES={"exploration.cast_spell"}`；`_SUPPORTED_EFFECT_MODES={"healing","buff"}`——战斗外药水走这里，与 R1 复活家族"战斗外镜像"同一先例 |
| A8 | exploration/engine.py `_reduce_event` L443 / `_apply_hp_change` L528 | 探索层事件归约与 HP 应用点；探索激活的治疗事件必须与战斗内同构 |
| A9 | `src/rulesets/dnd2024/resting/engine.py` L172-216 / L218-271 | 短休/长休结算入口；回充挂接点。长休已有 `restore_hp`/`reduce_exhaustion` 等事件先例 |
| A10 | `src/rulesets/dnd2024/character/attunement.py`（D2 新增） | `attuned_refs(canonical)` 唯一门控读取入口 |
| A11 | `src/commands/player_state_applier.py:_use_inventory_item` L306-330 | 旧叙事路径：按 `name.casefold()` 找背包行、`qty-1`、从 `effect` 文本里抠 HP 数字回血。**这是叙事层兼容路径，dnd2024 专业角色不得再依赖它表达治疗药水**；本切片不删除它，只保证专业路径不走这里 |
| A12 | `src/rulesets/dnd2024/combat/catalog.py:from_bundle` L49-62 | 武器档案校验入口；弹药字段若加入武器档案，必须在此 fail closed 校验 |
| A13 | `src/rulesets/dnd2024/character/reconciliation.py` L33 | `_PROJECTED_ITEM_TYPES={"weapon","armor","shield","focus"}`：药水不进入 canonical 装备，激活校验必须读"持有"而非"已装备" |

## 3. 白名单改动规格（逐文件）

### 3.1 新增 `src/rulesets/dnd2024/items/`（`__init__.py`、`activation.py`、`charges.py`）

物品激活与充能的 owning module：

- `activation.py`：`validate_activation_declaration(item)` 校验 `activation` 声明：`cost` ∈ `{"action","bonus_action"}`；`target` ∈ `{"self","ally"}`；`effect.mode` 仅允许 `"healing"`；骰式必须匹配既有 `DICE_RE`；可选 `charge_cost` 必须为正整数，声明 `charges` 的物品必须显式给出。未知键 fail closed。
- `charges.py`：物品侧只允许 `charges: {"max": 正整数}`；`max` 是 bundle 静态定义，运行时唯一可变值是 canonical `equipment.item_state[item_ref].charges.current`。
- `recharge` 只允许 `{"on": "dawn"|"short_rest"|"none", "amount": 正整数 | 合法骰式}`；`on: none` 不允许再携带其它回充触发键。
- 导出 `charges_of(canonical, item_ref)`、`spend_charges(...)`（纯计算，不写状态）、`recharge_for_rest(...)`；`current` 不得为负或超过 max。
- 禁止把激活规则写进 `features/` 或 `spells/`：物品激活不是职业能力也不是施法。

### 3.2 `src/rulesets/dnd2024/combat/primitives.py`

- `INTENT_TYPES` 新增 `"activate_item"`。

### 3.3 `src/rulesets/dnd2024/combat/validation.py`

- `activate_item` 分支：`actor_id` 为玩家/companion；`item_ref` 必须 `item:<id>`；角色持有该物品；动作经济可用；若 `requires_attunement` 则必须在 `attuned_refs` 中；充能/数量足够；目标合法。
- 同一 charged ref 的持有数量 > 1 时拒绝激活（§1.2 决策 12）；不得让多件物品共享 `item_state[item_ref]`。
- 全部失败路径返回可读错误，**不允许部分扣减**。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py` + `combat/engine.py`

- 新增 `_activate_item_events`，按 A4 原子批次产出：
  1. `dnd2024.action.spent`（动作/附赠动作）
  2. `dnd2024.item.charge_spent`（若物品有充能）或背包数量扣减事件（若消耗品）
  3. 治疗事件（复用 A5 管线，带 `rolls`）
  4. `dnd2024.item.activated` 审计事件（item_ref、target、cost）
- 弹药：`_attack_events` 在武器声明 `ammunition_item_ref` 时校验并扣减对应弹药背包数量；弹药不足则攻击在 validation 阶段拒绝，**不允许先攻击后扣弹药**。

### 3.5 `src/rulesets/dnd2024/combat/reducer.py`

- 登记新事件类型与字段白名单；`dnd2024.item.charge_spent` 写回 canonical `equipment.item_state[item_ref].charges.current`，扣减不得为负（A6 范式）。
- 新增 `dnd2024.item.consumed`：`{"item_ref":...,"amount":1}` 只通过既有 character/economy 权威接口扣 backpack qty；药水与弹药共用，数量为 0 时删除空行但不得删除其它同名 ref。
- `dnd2024.item.activated` 为纯审计事件，不改状态。

### 3.6 `src/rulesets/dnd2024/character/reconciliation.py`

- 首次 reconcile 到持有物品声明 `charges.max` 且 `item_state` 缺失时，幂等初始化 `current=max`；既有 current 不被休息、换装或重复 reconcile 重置。
- 初始化只写 canonical item_state，不把 max 复制成第二份运行时权威；`item_type: potion/ammunition` 不进入装备 refs 的规则不变。

### 3.7 `src/rulesets/dnd2024/exploration/engine.py`

- `EXPLORATION_INTENT_TYPES` 新增 `"exploration.activate_item"`；战斗外只允许 `target=self` 的消耗品治疗（药水）。
- `_SUPPORTED_EFFECT_MODES` 不变；治疗事件与战斗内同构，走 `_apply_hp_change`。
- 战斗外**不消耗动作经济**（无回合），但仍消耗物品数量/充能；同款多件充能守卫与战斗内一致。

### 3.8 `src/rulesets/dnd2024/resting/engine.py`

- 短休/长休结算末尾追加物品回充：遍历持有物品中声明 `recharge` 且 `on` 匹配本次休息的条目，按声明恢复固定值或掷骰恢复，事件 `{"type":"recharge_item","item_ref":...,"before":...,"after":...,"rolls":[...]}`。
- `current` 封顶 `max`；`recharge.on` 不在 `{"dawn","short_rest","none"}` 内 → 装载期 fail closed。
- 同调不随休息改变（D2 语义不回归）。

### 3.9 数据（仅验证载体，不做阶段二批量填充）

- 新增 `templates/rulesets/dnd2024_srd/content/items/potion_of_healing.json`：`item_type: "potion"`、`activation.cost: "action"`、`activation.target: "self"`、`activation.effect: {"mode":"healing","healing":"2d4+2"}`、`consumable: true`、经核对可分发来源的 source_ref；配套 en/zh-CN locale。若 SRD 5.2.1 未覆盖该条目，改为测试 fixture，禁止伪造来源。
- `item_type` 新增取值 `"potion"` 必须在读取处显式登记；**不得**让 potion 进入 `_PROJECTED_ITEM_TYPES`。
- 充能与回充的验证使用**测试内构造的 wand fixture**，不向仓库添加未经核对的魔法物品内容。
- `srd_combat_core.json` 新增可选 `ammunition_item_ref`：shortbow/longbow → `item:arrows`，D1 的三种弩 → `item:bolts`；`combat/catalog.py` 校验目标存在且 `item_type: ammunition`。
- 新增 `content/items/bolts.json` 与 en/zh-CN locale（字段对照既有 arrows）。blowgun/sling/火器的弹药内容不在本切片，登记 D3-8，不得用 arrows 代替。

### 3.10 tests

- 新增 `tests/rulesets/test_dnd2024_item_activation.py`、`tests/rulesets/test_dnd2024_item_charges.py`。
- 既有 combat / exploration / rest / reconciliation 测试只允许追加用例。

## 4. 测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| T1 | 战斗内激活治疗药水 | 动作消耗 + 数量 -1 + 治疗事件带 rolls；HP 不超上限 |
| T2 | 探索层激活同一药水 | 不消耗动作经济、数量 -1、HP 上升、事件同构 |
| T3 | 数量为 0 / 未持有 | 拒绝，零状态变更 |
| T4 | 充能激活（wand fixture） | 首次持有初始化 current=max；激活按 `charge_cost` 扣减；`charge_spent` 写回 canonical |
| T5 | 充能不足 | 整个激活拒绝，动作经济与充能都不扣 |
| T6 | 充能不会为负、不超 max | reducer 层兜底拒绝 |
| T7 | `requires_attunement` 且未同调 | 拒绝激活；同调后可激活（消费 D2 访问器） |
| T8 | 动作经济不足（动作已用/附赠已用） | 拒绝，物品不扣 |
| T9 | 长休回充（`on: dawn`） | 映射长休；固定值与 `1d6+4` 骰式都正确；封顶 max；事件带 rolls |
| T10 | 短休回充（`on: short_rest`） | 只在短休触发；长休不触发 |
| T11 | `on: none` / 未知回充键 | 不回充；未知键装载 fail closed |
| T12 | 回放/重放同一事件批次 | 不重掷骰、不重复扣充能、不重复扣数量 |
| T13 | 弹药 | shortbow/longbow 扣 arrows、三种弩扣 bolts；弹药为 0 时攻击在 validation 拒绝 |
| T14 | 非法激活声明 | 未知 cost/target/mode、非法骰式、非法 charge_cost 均 fail closed |
| T15 | 激活不是施法 | 不消耗法术位、不开反制法术窗口、不要求准备法术 |
| T16 | 激活不触发借机攻击、不消耗移动力 | 事件无 `movement_cost`，不进移动事件 |
| T17 | 旧叙事路径不污染 | dnd2024 专业角色使用治疗药水走新管线，不经 `_use_inventory_item` 的文本 HP 解析 |
| T18 | 回归 | 1C/D1/D2 全部目标测试与 combat/exploration/rest 套件零断言改动通过 |
| T19 | 同一 charged ref 持有多件 | 战斗与探索激活均拒绝；不多件共享充能、不扣动作 |
| T20 | 充能状态幂等 | 重复 reconcile/换装不重置 current；`item.consumed` 只扣目标 ref 的数量 |

## 5. 验收命令

```bash
python -m pytest -q tests/rulesets/test_dnd2024_item_activation.py tests/rulesets/test_dnd2024_item_charges.py
python -m pytest -q tests/rulesets -k "combat or exploration or rest or attunement"
python -m pytest -q --cov=src
```

## 6. 禁止事项

- 禁止复用或修改 engine `ACTION_KINDS` 的 `consumable` 空壳来表达机械激活。
- 禁止把激活实现为 `cast_spell` 的特例，或让激活消耗法术位。
- 禁止新增 `destroy_on_empty` 字段（D3b）、卷轴字段（D3-1）、反应激活。
- 禁止在激活路径里新增"随机生效/随机损毁"判定。
- 禁止让物品激活的伤害/豁免/区域效果混入本切片。
- 禁止让前端或 LLM 直接提交充能值、数量值；一切扣减由服务端结算。
- 禁止为药水新增第二套 HP 计算；必须复用既有治疗管线。

## 7. 停止条件

- 激活需要目标为敌方或需要伤害/豁免模式 → 停止，归 D3-2 另立切片。
- 回充需要真实"黎明"或跨日计时 → 停止；本切片只接受长休等价映射，若评审不接受则回到主计划补世界时钟。
- 充能状态需要第二处权威（例如既写 canonical 又写战斗态） → 停止，保持单一 canonical 权威。
- 弹药消耗需要改敌方攻击或多次攻击语义 → 停止，登记后单独切片。
- 发现探索层与战斗层治疗事件无法同构 → 停止，不得各写一套 HP 语义。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

- 基线 commit 与行号漂移核对
- `.rulebooks` 原文行号回填（激活/充能/回充节）
- T1–T20 逐条结果
- `dawn`→长休等价映射的明确确认
- 白名单符合性与偏差裁决

### 迁移债登记（本切片显式产生，禁止后续切片"顺手"处理）

| # | 债务 | 归属建议 |
|---|---|---|
| D3-1 | 卷轴（施法路径、不耗法术位施法、环阶与职业列表匹配）未落 | 独立卷轴切片，复用施法管线 |
| D3-2 | 激活效果仅支持治疗；伤害/豁免/区域/条件激活未落 | 激活效果扩展切片 |
| D3-3 | 反应成本激活未落 | 反应窗口切片 |
| D3-4 | 耗尽销毁未落，字段未预留 | D3b |
| D3-5 | 多充能变体与阶梯效果未落 | 阶段二内容需要时 |
| D3-6 | 医疗包与 Healer 专长联动未落 | Healer 专长独立切片 |
| D3-7 | 同 ref 多件充能状态不可区分，暂以数量守卫拒绝激活 | 实例 ID / slot map 独立切片 |
| D3-8 | blowgun/sling/火器弹药内容未落，仍抽象弹药 | 对应装备内容需要时连同消费者补齐 |
