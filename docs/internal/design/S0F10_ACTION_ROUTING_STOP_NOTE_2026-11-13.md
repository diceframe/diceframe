# S0-F10 停止说明：Bonus Action 路由与移动附带效果

| 项 | 值 |
|---|---|
| 切片编号 | S0-F10 |
| 任务书 | `S0F10_ACTION_ROUTING_TASK_SPEC_2026-11-07.md` |
| 判定日期 | 2026-11-13 |
| 代码基线 | `feat/dnd2024-s0f12-save-survival` 的起点（S0-F6c 之后的实际提交） |
| 结论 | 按任务书第 7 节停止条件 2、3 停止；本切片**未实现任何特性** |

本文只记录"为什么这一片现在不能按契约执行"以及核实过的代码事实。它不是执行报告：
没有任何一项特性被声明为已完成，也没有留下简化版实现。

## 1. 停止条件命中

任务书 §1.5 规定：「Hide、Fast Hands 的环境/物品前提若缺少权威数据，按第 7 节停止，
不做'简化版'。」核实结果如下。

### 1.1 停止条件 2：没有可判定的遮蔽 / 掩蔽 / 视线模型（Hide）

| 事实 | 位置 |
|---|---|
| 战斗状态只有一维 `positions` 与 `position_mode: "theater"`，没有光照、遮蔽、掩蔽或视线字段 | `combat/engine.py::initialize_state` |
| 全仓 `obscured` / `line_of_sight` / `three_quarters` / `total_cover` 检索结果为空 | `src/rulesets/dnd2024/**`、`content/combat_catalogs/**` |
| 现行 Hide 的 DC 是「10 + 存活敌人最高感知豁免」，不是任务书 §4.3.2 要求的固定 DC 15 | `combat/resolution.py::_hide_events` |
| 现行 Hide 不检查任何环境前提，成功后直接挂 `invisible` | 同上 |

任务书 §4.3.1 要求「结算时检查持有者处于 Heavily Obscured，或 Three-Quarters/Total Cover
后，且不在任何敌人的 line of sight；缺少任一权威事实则不提供能力」。测试矩阵 9 也要求
「无遮蔽/掩蔽、仍处敌人视线或环境事实缺失时不可用」。

白名单（§5）只有 `combat/*.py` 与 `combat_catalogs/srd_combat_core.json` 两个数据面，没有
遭遇/场景模型（`encounter_preset` 的敌人档案、`positions` 是仅有的空间事实）。在白名单内
无法建立"这面墙是不是全遮蔽、这条视线是否被挡"的权威闭环；用 GM 意图里的自由字段顶上，
就是把场景事实变成客户端声明，正是 §4.3.5 禁止的"把条件名叫 invisible 当作完整 Hide"。

### 1.2 停止条件 3：Fast Hands 没有工具 / 对象 / 物品动作元数据

| 事实 | 位置 |
|---|---|
| 战斗意图闭集里没有 Utilize，也没有"以 Magic action 使用物品"的意图 | `combat/primitives.py::INTENT_TYPES` |
| 能力动作 kind 闭集 `ACTION_KINDS` 同样没有 utilize / magic_item | `features/models.py::ACTION_KINDS` |
| 物品激活只接受 `action` 与 `bonus_action` 两种成本，没有 Magic action 区分 | `items/activation.py::ACTIVATION_COSTS` |
| 盗贼工具条目只有 id、source_ref、automation_level，没有开锁/拆陷阱的难度或目标信息 | `content/tools/thieves_tools.json`、`content/items/thieves_tools.json` |
| 不存在锁、陷阱、口袋等场景对象模型（全仓检索为空） | `src/rulesets/dnd2024/**` |

任务书 §4.2.5 要求 Fast Hands 三选一：①对锁/陷阱/口袋的 Sleight of Hand 检定；②Utilize；
③仅以 Magic action 使用明确要求该动作的魔法物品。三类中：

- ① 没有对象模型，服务端无法核验"你在对什么开锁"；
- ② 没有 Utilize 这个动作；
- ③ 没有 Magic action 成本，也没有"该物品要求 Magic action"的声明。

§7 停止条件 3 的原文正是「Fast Hands 缺少工具/对象/物品 Magic action 元数据，需要虚构目标
或结果」，三条全部命中。

## 2. 同一任务书里**不**构成阻塞的部分

记录这一点是为了让后续重启时不必重新核实：

- 附带移动：`dnd2024.movement.granted` + `economy.movement` 已是权威额度机制
  （`combat/resolver` 的 `_movement_events`、`_critical_hit_move_events` 已在用），
  「至多一半当前 Speed」可由 `effective_speed` 直接算出。
- Steady Aim 的「Speed 0 至回合末」：`check_modes.effective_speed` 已由 `speed_zero`
  条件压倒一切修正，已有条件可复用。
- OA 抑制作用域：`condition.applied` 支持 `duration` / `ends_on`，本切片只需声明作用域，
  不需要完整借机攻击系统。
- Fleet Step / Heightened Focus：Monk 的免费与付费动作已拆成独立 capability
  （`flurry_of_blows`、`patient_defense` / `patient_defense_focus`、
  `step_of_the_wind` / `step_of_the_wind_focus`），升级只需改写既有声明。

也就是说，七项特性里只有 Hide 与 Fast Hands 被数据缺口挡住，而任务书不允许拆分交付。

## 3. 重启需要先补的前置

按依赖顺序：

1. **场景事实**：一个权威的遮蔽/掩蔽/视线模型（遭遇档案或战斗状态字段），能让 Hide
   的三种前提与 Fast Hands 的"目标对象"都由服务端判定。
2. **Utilize / Magic action**：`INTENT_TYPES` 与 `ACTIVATION_COSTS` 扩到这两个动作，
   并让物品目录声明"该物品要求 Magic action"。
3. 以上两项落地后，本任务书的 §4.3 与 §4.2.5 才能在白名单内闭合。

在 1、2 存在之前，F10 的 22 条测试矩阵里至少 9、10、11、12、13、14、15 无法写出
"走真实 `validate_intent → resolve_intent → apply_batch`"的版本；写出来的只能是
客户端声明即事实的假测试。

## 4. 对主线的影响

- F10 的前置（S0-F1、S0-F9）已在本链上：`579b398a` 就是 F9 的移动事实。
- F11 的停止条件里同样有「远程重定向需要 Total Cover」，与本节 1.1 是同一个缺口；
  重启 F10/F11 时应一起补模型，而不是各补一半。
- F12（豁免减伤、动态抗性、归零存活）不依赖本切片的任何一项，已单独推进。

## 5. 后续：本切片已拆分（2026-11-20）

两个阻塞项一个已解一半、一个完全未动，整体重启已不可能，故 S0-F10 拆为五片，
原任务书 `S0F10_ACTION_ROUTING_TASK_SPEC_2026-11-07.md` 作废（不再按其执行）。

| 片 | 范围 | 状态 | 任务书 |
|---|---|---|---|
| F10A | Instinctive Pounce / Tactical Shift / Steady Aim / Fleet Step / Heightened Focus | **已完成**（GAP-14 关闭；Step 携带见 GAP-19） | `S0F10A_ACTION_MECHANICS_TASK_SPEC_2026-11-20.md` |
| **F10B** | 战斗内 Hide（默认不可藏 + 三前提 + 检定收口） | **已写，待执行** | `S0F10B_HIDE_TASK_SPEC_2026-11-20.md` |
| F10C | 战前潜行（遭遇前） | **不做**：产物是突袭，GAP-5（无 Surprised）未解 | — |
| F10D | Fast Hands ①③（工具存在性 + 借用 + 标准动作→附赠动作） | **已写，等 B** | `S0F10D_FAST_HANDS_TASK_SPEC_2026-11-20.md` |
| F10E | Fast Hands ②（Utilize） | **不做**：数据已在（GAP-6），但 GM 无法施加机械后果（GAP-16/GAP-18） | — |

### 5.1 阻塞项的现状（逐条对账）

| 原停止条件 | 2026-11-20 现状 | 落在哪片 |
|---|---|---|
| 2. 无 Heavily Obscured / ¾·Total Cover 模型 | **已解**：掩蔽 C2A（已合入）、遮蔽 C2B/C2E（#823） | F10B |
| 2. 无视线（line of sight） | **未解**，F10B 选择折叠进 total cover / heavily 的 GM 声明，作为显式偏差 | F10B（记偏差）、阶段三（真解） |
| 3. 无工具/对象/物品动作元数据 | **半解，且有一处更正**：工具表 `equipment_catalogs/srd_tools.json` **已带** `utilize: [{task, dc}]`（36 个工具中 35 个有，盗贼工具是开锁/拆陷阱 DC 15）——数据在、引擎没读；物品原语（充能/消耗/激活/同调）已完成但零内容使用；激活效果只有 healing | F10D / F10E |

### 5.2 `cunning_action` 已完成，不重复实现

原任务书 §1.2 要求"完整实现七项"，其中 `cunning_action` 已有三个 capability
（`cunning_dash` / `cunning_disengage` / `cunning_hide`）并通过 `class_capability`
路由到既有基本动作，配套测试在 `test_dnd2024_cunning_action.py`。拆分后不再计入范围。

### 5.3 拆分时的两条纪律

1. **一个环境门槛动作先落一个具名字段**（F10B 的 `unavailable_reason`），第二个出现再抽象，
   不许提前造通用"可用性原因"体系。
2. **F10B 不动 DC**：现行 `DC = 10 + 存活敌人最高感知豁免` 是占位符（用感知豁免冒充被动察觉），
   DC 15、被动察觉、工具检定、GM 逐次指定 DC 一并归入独立切片「检定工具」。

### 5.4 缺口去哪了

本轮发现的全部缺口已集中登记在 `docs/internal/design/GAP_REGISTER.md`（GAP-1–GAP-14），
每条带证据位置与归属片。修复后改状态，不删行。
