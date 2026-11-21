# S0-F10A 任务书：五项 Bonus Action 机制（Pounce / Tactical Shift / Steady Aim / Fleet Step / Heightened Focus）

| 项 | 值 |
|---|---|
| 切片编号 | S0-F10A（S0-F10 拆分后的第一片，见 `S0F10_ACTION_ROUTING_STOP_NOTE_2026-11-13.md` §5） |
| 验收载体 | 附带移动额度、Steady Aim 的"未移动"前提、Fleet Step 的非递归、Heightened Focus 的三项升级 |
| 规则基准 | SRD 5.2.1 p29（Pounce）、p48（Tactical Shift）、p62（Steady Aim）、p52（Heightened Focus）、p51（Fleet Step） |
| 代码基线 | `origin/main@e376f292` + F10B 的 `_skill_check_events`（若已合入；本片不依赖它） |
| 前置任务 | 无（`cunning_action` 已完成，不在本片范围） |
| 状态 | 已执行（16 条矩阵 15 条；Step 携带见 §7 的 GAP-19） |

## 1. 范围与完成定义

1. **五项特性**，全部经 `class_capability` 路由，不改 `INTENT_TYPES`：
   - Instinctive Pounce：进入 Rage 的附赠动作附带 ≤ 半速移动；
   - Tactical Shift：Second Wind 的附赠动作附带 ≤ 半速移动，且不引发借机攻击；
   - Steady Aim：附赠动作，**本回合未移动过**才可用；给本回合下一次攻击优势；用后速度 0 至回合末；
   - Fleet Step：完成一个非 Step of the Wind 的附赠动作后可立即用一次 Step of the Wind（不扣附赠动作，付费版仍扣 1 Focus），**不得递归**；
   - Heightened Focus：付费 Flurry 三次徒手、付费 Patient Defense 两次 Martial Arts 骰给临时 HP、付费 Step 可携带 5 尺内自愿且 Large 或更小生物。
2. **新增一个只读方法**，不新增状态：

   ```python
   def has_moved_this_turn(self, combat, actor_id) -> bool
   ```

   读 `combat.economy.moved`（reducer 在 `position.changed` 里已写，且只对当前行动者置位）。这是 GAP-14 的关闭方式。
3. 附带移动是**独立额度**：`min(当前 Speed // 2, ...)`，不消耗 `economy.movement`，事件带 `source` 与 `allowance_ft`。
4. 不做的事见 §7（视线、Hide、Fast Hands、Utilize、GM 裁定原语）。

## 2. 规则依据（原文要点）

- **Instinctive Pounce**：As part of the Bonus Action you take to enter your Rage, you can move up to half your Speed.
- **Tactical Shift**：Whenever you activate your Second Wind with a Bonus Action, you can move up to half your Speed without provoking Opportunity Attacks.
- **Steady Aim**：As a Bonus Action, you give yourself Advantage on your next attack roll on the current turn. You can use this feature only if you haven't moved during this turn, and after you use it, your Speed is 0 until the end of the current turn.
- **Fleet Step**：When you take a Bonus Action other than Step of the Wind, you can also use Step of the Wind immediately after that Bonus Action.
- **Heightened Focus**：Flurry 三次；Patient Defense 额外两次 Martial Arts 骰的临时 HP；Step 携带 5 尺内自愿 Large 或更小生物同行至回合末、被携带者移动不引发 Opportunity Attacks。

## 3. 定点代码事实（基线已核实）

| 事实 | 位置 |
|---|---|
| 附赠动作成本与能力声明在目录里由 `capabilities[].cost` 表达 | `class_feature_catalogs/srd_class_features.json` |
| 五项特性的目录条目**已存在但无 capabilities**（`caps=[]`） | 同上（`instinctive_pounce` / `tactical_shift` / `steady_aim` / `fleet_step` / `heightened_focus`） |
| 移动事件与额度扣减 | `combat/reducer.py::position.changed`（`movement_cost` / `economy.movement` / `economy.moved`） |
| 速度聚合唯一入口 | `combat/check_modes.py::effective_speed` |
| 临时 HP 的替换规则 | `combat/reducer.py::_apply_temp_hp_change`（keep-max） |
| Focus 资源与 Flurry/Patient/Step 既有声明 | 目录同名条目 + `class_capability` 结算 |

## 4. 精确实现契约

### 4.1 附带移动（Pounce / Tactical Shift / Fleet Step 的 Step 部分）

1. 新增事件 `dnd2024.movement.granted {actor_id, amount, source, allowance_ft}`；`amount` 是本次使用的距离，`allowance_ft` 是上限（`floor(Speed/2)`），二者都可审计。
2. 结算顺序：先付能力成本（Rage / Second Wind 的附赠动作与资源）→ 再给移动额度 → 再落 `position.changed`（**不带 `movement_cost`**，因此不扣 `economy.movement`、不触发"用掉移动选择"）。
3. 越界（`amount > allowance_ft`）、Speed 为 0、非当前行动者三种情况一律拒绝；额度取 `effective_speed`，不是裸 `speed`。
4. **不实现借机攻击系统**：Tactical Shift 的"不引发 OA"只在事件上记 `provokes_opportunity: false`，不接任何 OA 判定（OA 本来就没有实现）。

### 4.2 Steady Aim

1. 可用性：`not has_moved_this_turn(combat, actor_id)`，且本回合未用过。
2. 效果：给"本回合下一次攻击"优势——复用既有的攻击侧来源机制（`attack_sources` 的 `extra_sources` 或等价物），标记 `steady_aim`，**命中或未中即消费**。
3. 用后：`speed_zero` 作用于本回合剩余时间，由 `effective_speed` 返回 0；不得用附带移动绕过。
4. 拒绝理由要可显示：`"you have already moved this turn"` / `"steady aim is already used this turn"`。

### 4.3 Fleet Step

1. 触发点：一个**非 Step of the Wind** 的附赠动作结算完成之后，同一回合内提供一次"免费 Step of the Wind"额度。
2. 免费 Step 不扣附赠动作，但**付费内容照扣**（1 Focus 换 Dodge 之外的升级由 Heightened Focus 管）。
3. **不得递归**：由 Fleet Step 提供的 Step 本身不再触发 Fleet Step；实现上用一个 per-turn 标记，而不是"链式回调"。
4. 额度随回合结束失效。

### 4.4 Heightened Focus

1. **Flurry**：付费版三次 Unarmed Strike；`capabilities` 里 flurry 的 `actions` 数量由 `heightened_focus` 提升为 3，不存在"免费三次"。
2. **Patient Defense**：付费版额外掷两次 `martial_arts` die，合计给临时 HP（走 `_apply_temp_hp_change`，旧临时 HP 不相加）。
3. **Step**：付费版可指定 5 尺内、自愿、Large 或更小生物同行至回合末；携带者随行不扣被携带者移动，也不触发 OA（同样只记 `provokes_opportunity: false`）。
4. 三项升级只在**付费**分支生效；免费分支（`cunning_action` 之外的免费 Step）不受影响。

### 4.5 视图与投影

1. `available_intents` 的能力条目在不可用时带 `unavailable_reason`（与 F10B 的 hide 同形，但**不抽象**——第二个消费者出现时再统一，见 GAP-13）。
2. 不新增前端字段。

## 5. 改动白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` + `locales/{en,zh-CN}`（五项补 `capabilities`）
2. `src/rulesets/dnd2024/combat/engine.py`（`has_moved_this_turn`、能力分支）
3. `src/rulesets/dnd2024/combat/resolution.py`（附带移动、Steady Aim、Fleet Step、Heightened Focus 结算）
4. `src/rulesets/dnd2024/combat/check_modes.py`（`effective_speed` 读 `speed_zero`）
5. `src/rulesets/dnd2024/combat/reducer.py`（`movement.granted`、`speed_zero`、per-turn 标记）
6. `tests/rulesets/test_dnd2024_action_routing_a.py`（新建）

不得改：`INTENT_TYPES`、视线模型、Hide、Fast Hands、Utilize、物品目录、前端、存档迁移。

## 6. 逐项编号测试

1. 五项特性在正确职业/子职/等级授予（低一级、错子职不授予），label 双语正确。
2. Pounce：进入 Rage 后可移动 ≤ 半速；`Sustain Rage` 不触发；重复进入不触发。
3. Pounce 越界 / Speed 0 / 非当前行动者 → 拒绝且状态不变。
4. 附带移动不扣 `economy.movement`：与普通移动同回合并用时，剩余移动力只被普通移动消耗。
5. Tactical Shift：Second Wind 后附带移动，事件 `provokes_opportunity: false`；Tactical Mind 不触发。
6. Steady Aim：未移动可用；**移动过则拒绝**（`has_moved_this_turn` 为真，含被推开的第三方不算）。
7. Steady Aim：用后 `effective_speed == 0`；本回合下一次攻击有优势；命中后消费；第二击无优势。
8. Steady Aim 跨回合失效：下回合攻击无优势，速度恢复。
9. Fleet Step：任意非 Step 附赠动作后出现免费 Step；Step 之后不再出现（非递归）。
10. Fleet Step 付费 Step 仍扣 1 Focus；Focus 不足时只拒绝付费版，不扣附赠动作。
11. Heightened Focus：付费 Flurry 恰三次；非 10 级两次；零 Focus 不可用；Martial Arts 的免费附赠徒手仍只有一次。
12. 付费 Patient Defense 掷两枚 Martial Arts 骰给临时 HP；与既有临时 HP 取高不相加；免费版不给。
13. 付费 Step 携带：5 尺内、自愿、Large 或更小可用；Huge / 非自愿 / 超距拒绝。
14. 被携带者随行后位置同步、其本回合移动不受影响；回合结束携带关系解除。
15. 重放：同一批事件重放得到相同位置、相同临时 HP、相同 `speed_zero`。
16. 既有回归：`second_wind` / `action_surge` / `rage` / `martial_arts` / `monks_focus` / `cunning_action` 全部无回归。

## 7. 非目标与登记缺口

**非目标**：完整借机攻击系统、完整视线、Hide（F10B）、Fast Hands（F10D/E）、Utilize、GM 裁定原语（GAP-16）、光照、2D 地图、前端。

**本片关闭的缺口**：GAP-14（加 `has_moved_this_turn` 读方法）。

**本片已知未实现（用户裁定：登记后视为完成）**：Heightened Focus 的「付费 Step 携带 5 尺内自愿 Large 或更小生物」——缺口册 GAP-19。它是五项里唯一跨角色的效果（需要"移动另一个 actor"的事件形状），其余四项的全部行为与 11 条用例已落地。
**本片不关闭但会被它影响的缺口**：GAP-13（`unavailable_reason` 的第二个消费者）。

**停止条件**：

1. 若 `economy.moved` 不是"当前行动者是否移动过"的可靠来源（例如被强制位移也会置位），停止并报告——不得改用客户端声明。
2. 若附带移动无法与普通移动共享同一权威状态（`positions` + `economy`），停止。
3. 若五项里任一项的 `capabilities` 无法用既有 `cost`/`actions` 形状表达（例如 Fleet Step 需要"附赠动作之后"的钩子而引擎没有），停止并报告，**不得引入自由文本效果**。
4. 需要改 `INTENT_TYPES`、`features/models.py::ACTION_KINDS` 或在通用路径加职业分支时停止。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_action_routing_a.py -q
python -m pytest tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_cunning_action.py -q
python -m pytest tests/rulesets -q
ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_action_routing_a.py
mypy
```

## 9. 执行报告模板

- 基线 / 分支 / 提交：
- 五项 `capabilities` 的声明形状与路由：
- `has_moved_this_turn` 的数据来源与"被推开不算移动"的证据：
- 附带移动与 `economy.movement` 的隔离证据（测试 4）：
- Fleet Step 的非递归落点：
- Heightened Focus 三项升级各自的付费分支：
- 测试 1–16 逐项结果：
- 偏差与未决：
