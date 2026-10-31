# 切片 4 任务书：Action Surge（动作经济扩展）

版本：v2（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。

---

## 1. 目标

为 D&D 2024 规则集实现 **Action Surge**（战士 2 级特性）的完整闭环：catalog 声明 → 额外动作经济 → 支付选择 → 每回合限制 → 测试。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

> 你可以在短期内让自己突破身为凡骨的极限。**在你的回合中，你可以执行一个额外的动作，但不能是魔法动作。**你使用此特性后，你必须在完成一次短休或长休后才能再次使用。**第17级起，你可以在每次休息之间使用此特性两次，但每回合只能使用一次。**

出处：`.rulebooks/dnd5r/玩家手册2024/角色职业/战士/战士.htm` L83-84。

三条硬性语义，任何一条丢失都是错误实现：

1. **额外动作不能是魔法动作**（2024 版特有，2014 无此限制）——不能简单 `action += 1`；
2. 短休/长休恢复（rest_catalog 已有声明，勿改）；
3. **每回合只能使用一次**（17 级两次资源时此限制才有实际意义，但必须实现）。

**明确不做（本切片裁剪，勿实现）**：

| 项 | 不做的原因 |
|---|---|
| Tactical Mind（战术思维） | 依赖 second_wind 资源转换 + 属性检定管线，独立切片 |
| 前端 economy 面板显示 surge_action | 可用性由服务端投影驱动，显示是可选美化，非本切片目标 |

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| **economy 是当前行动 actor 的单份经济**（非 per-actor 表）：`{actor_id, action, bonus_action, reaction, movement, speed, attacks_remaining, disengaged}`，每回合由 `_fresh_economy` 重置，经事件 `economy` 字段 deepcopy 落地 | `src/rulesets/dnd2024/combat/engine.py` L998-1007；`src/rulesets/dnd2024/combat/reducer.py` L86 |
| **action.spent 事件是通用 resource key 递减**，`current < amount` 时 fail-closed 抛 `EventBatchError` | `src/rulesets/dnd2024/combat/reducer.py` L122-128 |
| **攻击支付走独立的 `attack.spent` 事件**（v2 修正，v1 误并入上一行）：`attacks_remaining > 0` 时优先递减（Extra Attack），否则按 `resource` 字段递减；`resource` 由 engine.py `_attack_cost_event` 生成 | `src/rulesets/dnd2024/combat/reducer.py` L175-187 |
| **攻击/基础动作门控**：`available_intents` 中 `action > 0 or attacks_remaining > 0` 才暴露 attack/dash/dodge/disengage/stabilize | `src/rulesets/dnd2024/combat/engine.py` L216-240 |
| **施法成本只看 `economy[cost]`**（cost ∈ action/bonus_action）——surge_action 独立 key 则天然不能被施法消耗 | `src/rulesets/dnd2024/combat/view.py` L276-277 |
| **`_basic_action_events` 硬编码 `resource: "action"`**——surge 后 dash 若不改会被 reducer 拒绝 | `src/rulesets/dnd2024/combat/resolution.py` L451-460 |
| **class_capability 按钮管线**：`_available_class_capabilities` → available_intents 投影 → 前端通用按钮，**零前端改动**（切片 3 教训：人类玩家入口 = 既有 capability 按钮，点击后 attack 等按钮因 surge_action 重新出现） | `src/rulesets/dnd2024/combat/engine.py` L251-260 |
| **engine 已有 `_available_class_capabilities` 覆写**（切片 2：requires/forbidden_condition 过滤） | `src/rulesets/dnd2024/combat/engine.py` L156-177 |
| **action_surge 资源已声明**：rest_catalog fighter `action_surge`（短休/长休全恢复），progression track `[0,1,...,2 at 17]`——两文件均勿改 | `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json` L43；`progression_catalogs/srd_classes.json` fighter tracks |
| **resolver 校验**：`resource_id` 必须在 rest_catalog 有声明（切片 3 偏离 1 的教训；action_surge 有声明，可正常使用） | `src/rulesets/dnd2024/features/resolver.py` L566-574 |
| **capability 成本必须用 `cost.resources`**，裸自定义 key 会被静默忽略成零成本（切片 3 偏离 2 的教训） | `src/rulesets/dnd2024/features/models.py` `_parse_capability` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。`git status` 出现白名单外的任何变更即为失败。

### 3.1 `src/rulesets/dnd2024/features/models.py`

- `ACTION_KINDS` 增加 `"action_surge"`；
- `FeatureCapability` 增加可选字段 `once_per_turn: bool = False`（dataclass 默认值在末尾），`_parse_capability` 解析该 key（必须是 bool，否则 fail-closed 抛错）。

### 3.2 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

fighter 的 `features` 数组追加：

```json
{
  "id": "action_surge",
  "minimum_level": 2,
  "source_ref": "srd-5.2.1:p48:action-surge",
  "summary": "On your turn, you can take one additional action, except the Magic action. Starting at level 17, you can use it twice before a rest, but only once on a turn.",
  "parameters": {},
  "resource_id": "action_surge",
  "capabilities": [
    {
      "id": "action_surge",
      "cost": {"resources": [{"id": "action_surge", "amount": 1}]},
      "requires_hostile_target": false,
      "once_per_turn": true,
      "actions": [{"kind": "action_surge", "count": 1}]
    }
  ]
}
```

顶层 `labels` 增加 `"action_surge": "Action Surge"`（zh-CN locale：`"动作如潮"`）。

### 3.3 `src/rulesets/dnd2024/combat/resolution.py`

1. 新增辅助方法 `_spend_action_resource(combat) -> str`：`economy.action > 0` 返回 `"action"`，否则返回 `"surge_action"`。**支付优先级：先扣 action，action 为 0 才扣 surge_action**（保留 surge 给非施法动作无差别，行为确定）。
2. `_class_capability_events` 新增 `elif action.kind == "action_surge"` 分支，事件序列：
   - `dnd2024.class_resource.spent`（resource_id `action_surge`，amount 1）
   - `dnd2024.economy.granted`（新事件，见 §3.4）：`{"actor_id": actor_id, "grants": {"surge_action": 1}, "flags": {"action_surge_used": True}}`
   - **无 action.spent**（Action Surge 本身不消耗动作）。
3. `_basic_action_events` 与 `_stabilize_events`（v2 补：stabilize 走独立事件构建路径，v1 遗漏，不改则 surge 后 stabilize 被拒）：`resource` 由硬编码 `"action"` 改为 `_spend_action_resource(combat)`（spend_action=True 时）。
4. 攻击主流程的支付选择（v2 修正落点）：实际事件类型是 `dnd2024.attack.spent`，`resource` 字段在 engine.py `_attack_cost_event` 中生成并透传，不在 resolution.py `_attack_events`。**不得改动 `attacks_remaining`（Extra Attack）路径**。

### 3.4 `src/rulesets/dnd2024/combat/reducer.py`

新增事件类型 `dnd2024.economy.granted`：

- `grants`：`{key: amount}`，`combat["economy"][key] = int(economy.get(key, 0)) + int(amount)`；
- `flags`：`{key: value}`，直接写入 `combat["economy"][key]`；
- 未知/负值 amount fail-closed 抛 `EventBatchError`。

### 3.5 `src/rulesets/dnd2024/combat/engine.py`

1. `available_intents` 攻击/基础动作门控（L216-218）：增加 `or int(economy.get("surge_action", 0) or 0) > 0`。**施法门控（L241 `_available_spells`）不得包含 surge_action**。
2. `_available_class_capabilities` 覆写：追加 once_per_turn 过滤——`capability.once_per_turn and economy.get(f"{capability.id}_used")` 时视为不可用（与被 requires/forbidden_condition 过滤同等处理：不出现在可用列表）。
3. 其余 `can_act` 类门控（L349-351、L443-446，AI 托管选择用）：同步包含 surge_action，否则 AI 战士 surge 后不会继续攻击。

### 3.6 `src/rulesets/dnd2024/combat/validation.py`

attack/dash/dodge/disengage/stabilize 的动作可用性校验同步接受 surge_action；cast_spell 校验**不得**接受 surge_action（规则：额外动作不能是魔法动作）。

### 3.7 本地化（两个文件）

- `locales/en/class_feature_catalogs/srd_class_features.json`：`action_surge: "Action Surge"`；
- `locales/zh-CN/class_feature_catalogs/srd_class_features.json`：`action_surge: "动作如潮"`。

### 3.8 测试

新增 `tests/rulesets/test_dnd2024_action_surge.py`（复用 `dnd2024_monk_common` helper），`tests/rulesets/test_dnd2024_class_features.py` 补装载用例：

1. **装载**：fighter 2 级可解析 action_surge capability；`once_per_turn` 为 True；zh-CN 标签"动作如潮"；1 级战士无此 capability；
2. **使用**：提交 action_surge → `class_resource.spent` + `economy.granted`，economy.surge_action == 1、action_surge_used == True，action/bonus_action 不变；
3. **额外攻击**：action=0 后 surge → attack 重新可用；攻击扣 surge_action 不扣 action；
4. **支付优先级**：action=1 且 surge_action=1 时攻击扣 action；
5. **基础动作**：action=0、surge_action=1 时 dash 成功且扣 surge_action；
6. **不能施法**：action=0、surge_action=1 时施法 intent 被拒绝（action_spent），cast_spell 不出现在 available_intents；
7. **每回合一次**：17 级战士（资源 2 次）同回合第二次 action_surge 不出现/被拒绝；end_turn 后新回合可用；
8. **资源耗尽**：action_surge=0 时 capability 不出现；
9. **AI 托管**：AI 战士 surge 后 hosted 选择能继续攻击。

## 4. 前置步骤（动代码之前）

```powershell
# 基线必须全绿。已知豁免：test_dnd2024_character_builder.py 的 11 个上游既有失败
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_uncanny_dodge.py -q
```

## 5. 验收标准

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py -q    # 全绿
.venv\Scripts\python.exe -m pytest tests -q -k "action_surge"                           # 全绿
.venv\Scripts\python.exe -m pytest tests/rulesets -q                                    # 除已知豁免外全绿
git status   # 变更仅限白名单
```

## 6. 禁止事项

- 不改 `progression_catalog` / `rest_catalog`（action_surge 的 id、track、资源声明均已存在）；
- 不改 `ACTION_COST_KINDS` / `EQUIPMENT_REQUIREMENT_KEYS` / `WEAPON_KINDS`；
- **不得用 `action += 1` 实现额外动作**（会违反"不能是魔法动作"）；
- 不得让 surge_action 参与施法支付；
- 不改 `view.py`、不改 `frontend-v2/`（人类玩家入口走既有 class_capability 按钮管线，已核实零前端改动）；
- 不实现 Tactical Mind 或其他裁剪项；
- 不加兼容层、回退、try/except 兜底；不新增第三方依赖；
- 不修改本任务书之外的任何文档。

## 7. 停止条件（触发即停，写入执行报告，不要猜）

1. §2 所列"已存在勿改"的 catalog 事实与实际不符；
2. `economy` 的实际结构与 §2 描述不符（例如变成 per-actor 表）；
3. 攻击主流程的 action.spent 不在 `_attack_events` 而在别处，导致支付选择无法单点实现；
4. once_per_turn 过滤与切片 2 的 requires/forbidden_condition 覆写产生语义冲突；
5. 基线测试非绿（已知豁免除外）；
6. 任何需要改动 `view.py`、`frontend-v2/` 或 resolver 公有契约才能完成的情况。

## 8. 执行报告（完成后必须填写，仓库根 `EXECUTION_REPORT_SLICE4.md`）

```markdown
# 切片 4 执行报告
- 任务书版本：v1
- 分支：feat/action-surge
## changed-file list（与白名单逐项对照）
...
## 测试输出（完整粘贴）
...
## 停止条件触发情况（无则写"无"）
...
## 未完成项
...
```

注意：报告文件 `EXECUTION_REPORT_SLICE4.md` 是白名单唯一豁免项（临时产物，审核后删除）。

## 9. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-10-29 | 初版（依据规则书原文与源码勘察；surge_action 独立经济 key 方案；前端零改动已核实） |
| v2 | 2026-10-29 | 审核后修订：修正攻击支付落点（`attack.spent` 事件 + engine.py `_attack_cost_event`，v1 误指 `_attack_events` 的通用 action.spent）；§3.3.3 补 `_stabilize_events` 路径；两处执行偏离入册 |
