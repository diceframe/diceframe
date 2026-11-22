# S0-F10B 任务书：战斗内 Hide（默认不可藏 + 可用性前置 + 检定收口）

| 项 | 值 |
|---|---|
| 切片编号 | S0-F10B（S0-F10 的第一刀；F10 已拆为 a/b/c/d/e） |
| 验收载体 | `hide` 的三条合法路径、不可用原因、检定函数收口、终止与发现 |
| 规则基准 | SRD 5.2.1 p183 Hide；现行 DC 为**占位符**（见 §0.3） |
| 代码基线 | `origin/main@e376f292`（C2A 掩护、C2B/C2E 遮蔽已具备；#822/#823 若未合并则以其分支为基） |
| 前置任务 | S0-C2A 掩护（已合入）、S0-C2B 遮蔽（#823） |
| 状态 | 已执行，在 #828 待合并（「被敌人发现」未实现，见 GAP-16） |

## 0. 相对 S0-F10 原任务书的裁剪

### 0.1 F10 拆成五片

原任务书要求"一次实现七项特性"，其中 `cunning_action` **已经实现**（三个 capability `cunning_dash` / `cunning_disengage` / `cunning_hide` 声明在目录里、路由到既有基本动作，且有测试）。其余按依赖拆开：

| 片 | 内容 | 状态 |
|---|---|---|
| F10A | Instinctive Pounce / Tactical Shift / Steady Aim / Fleet Step / Heightened Focus | 可立即做，与本片无依赖 |
| **F10B（本片）** | 战斗内 Hide | 本文 |
| F10C | 战前潜行（遭遇前） | 暂缓：产物是"突袭"，而 Surprised 机制不存在 |
| F10D | Fast Hands ①（工具存在性 + 队伍借用 + Sleight of Hand）与 ③（标准动作→附赠动作） | 依赖本片的检定收口 |
| F10E | Fast Hands ②（Utilize 对象交互） | 依赖对象模型 |

### 0.2 原任务书 §3 已过时

原文"未核实 Heavily Obscured、视线和三类 Cover 的权威模型"——掩护（C2A）与遮蔽（C2B/C2E）都已落地，`obscurement_level()` / `cover_level()` 是现成查询。基线与意图闭集也已变化（新增 `combat.set_cover` / `combat.set_obscurement`）。

### 0.3 技能检定系统是占位符，本片不改它

原文要求 Hide 用 **DC 15** 固定检定。现状是 `DC = 10 + 存活敌人最高感知豁免`——用敌人的感知**豁免**冒充被动察觉，是代理值。

**本片不动 DC**：用户已裁定"现有技能检定系统是占位符版，先接这条，之后再整体重做"。因此本片只做两件事：把 hide 内嵌的掷骰抽成可复用函数、把前置条件接上。DC 口径、被动察觉、工具检定、GM 逐次指定 DC 全部登记为**独立缺口**（§7 表），不在本片夹带。

## 1. 范围与完成定义

1. **默认不可藏**：没有合法前提时 `hide` 被拒绝，且给出可显示的原因。今天是"无条件可藏"，本片把这个默认**翻过来**。
2. **三条合法路径**（满足其一即可）：`obscurement_level == "heavily"`；`cover_level ∈ {three_quarters, total}`；或 GM 已把该角色声明为上述任一状态（同一事实的声明入口）。
3. **检定收口**：`_hide_events` 里的掷骰段抽成 `_skill_check_events(actor, skill, dc, *, sources)`，hide 改为调用它。该函数是"占位符版技能检定"的唯一入口，将来整体重做时只换它一个。
4. **终止与发现**：沿用 `invisible` 来源隔离；发现 = 敌人 Perception 对抗 `check_total`；四类终止事件保持现状并补测试。
5. **可用性投影**：`available_intents` 仍列出 `hide`，但前提不满足时带 `unavailable_reason`，让玩家在**行动前**就能看到"此处无法隐蔽"。

## 2. 规则依据（p183）

> With the Hide action, you try to hide yourself. To do so, you must succeed on a DC 15 Dexterity (Stealth) check while you're Heavily Obscured or behind Three-Quarters Cover or Total Cover, and you must be out of any enemy's line of sight.

> On a successful check, you have the Invisible condition while hidden. Make note of your check's total, which is the DC for a creature to find you with a Wisdom (Perception) check.

> You stop being hidden immediately after any of the following occurs: you make a sound louder than a whisper, an enemy finds you, you make an attack roll, or you cast a spell with a Verbal component.

**关于第三条前提（"不在敌人视线内"）**：本片**不造视线模型**。理由与做法见 §4.2.3——它被折叠进前两条的 GM 表达里，这是对 RAW 字面的**偏差**，必须在执行报告里明写。

## 3. 定点代码事实（基线已核实）

| 事实 | 位置 |
|---|---|
| Hide 结算：一次 d20 + stealth，DC 硬编码为 `10 + 最高 wis`，**无任何前置检查** | `combat/resolution.py::_hide_events` |
| `hide` 只要还有动作就会被列出，不看环境 | `combat/engine.py::available_intents` |
| 遮蔽查询（含区域与感官） | `combat/engine.py::obscurement_level` / `effective_obscurement` |
| 掩护查询 | `combat/engine.py::cover_level` |
| `ability_check` 面的窗口族已存在（bardic_inspiration_check 等） | `combat/check_windows.py`（`surface="ability_check"`） |
| **没有 `ability_check` 意图**，技能检定只有三处内联（hide、dispel、少数特性） | `combat/primitives.py::INTENT_TYPES` |
| 探索层的通用检定在 `src/engine/checks.py`，DC 来自全局难度、不支持逐次指定 | `build_check_request` / `check_request_for_action` |

## 4. 精确实现契约

### 4.1 前置判定

1. 新增 `hide_preconditions(instance, combat, actor_id) -> dict`，返回 `{allowed, reason, source}`：
   - `source` ∈ `{"obscurement", "cover", ""}`，表示靠哪条路径成立；
   - `reason` 是可直接展示给玩家的短句（英文 canonical，locale 另行）。
2. 判定顺序：`obscurement_level == "heavily"` → `cover_level ∈ {"three_quarters","total"}` → 都不满足则 `allowed=False`，`reason="no heavy obscurement or three-quarters cover"`。
3. 该函数是**唯一**判定位：`validation` 拒绝、`available_intents` 投影、`_hide_events` 复核三处共用，不允许第二份判断。

### 4.2 三条路径与"视线"的折叠

1. **路径一（遮蔽）**：读 `obscurement_level(combat, actor_id)`。注意这是**原始等级**，不是 `effective_obscurement`——前提问的是"他所处的环境"，不是"某个观察者看不看得见他"。
2. **路径二（掩护）**：读 `cover_level`，`three_quarters` 与 `total` 均可。`half` 不够。
3. **路径三（视线，折叠）**：不新增字段、不建模型。GM 若要表达"敌人看不见他"，用已有的 `combat.set_cover(total)` 或 `combat.set_obscurement(heavily)`——这两条声明的规则含义本就覆盖"看不见"。
   - **偏差声明**：这在字面上合并了 SRD 的第三个前提与第一、二个前提。理由是三者在本引擎里可用的权威事实只有两条，而 total cover 的定义正是"不能被直接选为目标"。执行报告必须记这一条。

### 4.3 检定收口

```python
def _skill_check_events(
    self, instance, combat, actor, skill: str, dc: int, *,
    sources: list[dict] | None = None, context: str = "",
) -> tuple[list[dict], dict]:
```

1. 行为与今天的 `_hide_events` 掷骰段**逐字节等价**：护甲隐匿劣势 → `mode_from_sources` → 掷 1~2 枚 d20 → `_skill_check_floor` 下限 → `total = natural + skill 加值`。
2. 返回值第二项是 check_row（`{natural, total, dc, success, rolls, mode, sources}`），hide 用它决定是否授予 `invisible` 与保存 `check_total`。
3. `hide` 的 DC 仍取 `10 + 存活敌人最高 wis`，`context="hide"`，**不改**。
4. 探索层不接：`src/engine/checks.py` 是另一套，本片不碰。

### 4.4 失败与终止

1. 检定失败：不授予 `invisible`，动作/附赠动作**照常花掉**（RAW：尝试即消耗）。
2. 前提不满足：**不花动作**，直接拒绝（`INVALID_INTENT`，`reason` 来自 §4.1）。
3. 四类终止保持现状：攻击掷骰、含 V 成分施法、高于耳语发声、被敌人发现；来源隔离沿用 `source: "hide"`。
4. 发现：**本片不做**。原文写"补 GM 主动发现与对抗测试"，核对后这条自相矛盾——引擎里既没有 GM 移除条件的手段（G16），也没有任何"发现"路径（`invisible` 的 `ends_on` 只有 `attack_roll` 与 `spell_cast`）。**实现它需要新增一条裁决通道，属 G16**，不在本片伪造。

### 4.5 可用性投影

1. `available_intents` 的 `hide` 条目增加 `available: bool` 与 `unavailable_reason: str`。
2. 其他意图不带这两个字段（避免契约扩散），只 hide 带——理由是 hide 是第一个有"环境门槛"的基本动作，等第二个出现再抽象。
3. 战斗未开始时 hide 仍不出现在列表（现状）。

## 5. 改动白名单

1. `src/rulesets/dnd2024/combat/engine.py`（`hide_preconditions`、`available_intents`）
2. `src/rulesets/dnd2024/combat/validation.py`（hide 前置拒绝）
3. `src/rulesets/dnd2024/combat/resolution.py`（`_skill_check_events` 抽取、`_hide_events` 改调用）
4. `tests/rulesets/test_dnd2024_hide_preconditions.py`（新建）
5. **既有用例改写（前置变更的必然结果）**：
   - `tests/rulesets/test_dnd2024_cunning_action.py`（6 处 hide 意图）
   - `tests/rulesets/test_dnd2024_d20_skills.py`
   - `tests/rulesets/test_dnd2024_failed_d20_windows.py`
   - `tests/rulesets/test_dnd2024_bardic_inspiration.py`
   改写方式统一为：在提交 hide 前给该角色声明一条 `heavily` 遮蔽（或 total 掩护），并在注释里写"前提由本用例的夹具提供，不是引擎默认允许"。

不得改：DC 口径、探索层 `src/engine/checks.py`、感知系统、视线模型、物品/工具、Fast Hands、前端、存档迁移。

## 6. 逐项编号测试

1. **默认拒绝**：无遮蔽、无掩护时 hide 被拒，错误串含 `no heavy obscurement or three-quarters cover`，且**不消耗**动作。
2. **路径一**：声明 `heavily` 后 hide 可用并掷骰。
3. **路径二**：`three_quarters` 与 `total` 均可用；**`half` 不可用**（边界）。
4. **路径三（折叠）**：GM 声明 `total` 掩护时，即使没有遮蔽也可藏——证明"看不见"确实由掩护声明承担。
5. **可用性投影**：`available_intents` 中 hide 的 `available=False` 且 `unavailable_reason` 非空；声明后变 `True`、reason 为空。
6. **检定等价**：同一 RNG 序列下，改写前后 `check.resolved` 的 `natural/total/dc/rolls/mode` 逐字段一致（用固定种子对照）。
7. **失败成本**：失败后动作已花、`invisible` 未授予、`check_total` 未保存。
8. **成功后保存 check_total**，且与 `check.resolved.total` 相同。
9. ~~发现~~ → 见 §4.4.4：本片不做，转 G16。
10. **四类终止**：攻击掷骰 / 含 V 施法 / 高于耳语发声 / 被发现，各有一条用例，且只移除 `source="hide"` 的来源。
11. **来源隔离**：角色同时有另一来源的 `invisible` 时，hide 终止不移除它。
12. **既有回归**：`cunning_action`（`cunning_hide` 走附赠动作）仍可用，且同样受前置约束——它不得因为"是能力而不是基本动作"就绕过 §4.1。
13. **战斗未开始**：hide 不在可用列表里（现状不变）。
14. **重放**：同批事件重放得到相同 `check_total` 与相同 `invisible` 结果。

## 7. 非目标与登记缺口

**非目标**：DC 15 与被动察觉、GM 逐次指定 DC、发现用的通用感知检定、视线模型、战前潜行、Surprised、Fast Hands、Utilize、前端显示、技能检定系统整体重做。

**登记缺口**（不在本片修，写入执行报告并转交后续切片）：

| 缺口 | 影响 | 归属 |
|---|---|---|
| 技能检定系统是占位符（DC = 10 + 最高感知豁免；无被动察觉；无工具检定） | hide、开锁、拆陷阱、扒窃全部依赖它 | 独立切片「检定工具」 |
| 无 GM 发起检定的入口（不能"这里有一把锁，DC 15"） | 战斗外裁定只能靠全局难度 | 同上 |
| 无视线模型（第三条前提被折叠进掩护/遮蔽声明） | GM 需手动声明 total cover 或 heavily | 阶段三 |
| `available_intents` 只有 hide 带环境门槛字段 | 第二个环境门槛动作出现时需抽象 | 后置 |

**停止条件**：

1. 若 `cover_level` / `obscurement_level` 之一在 `hide` 的调用路径上不可用（例如需要 `instance` 而签名没有），停止并报告，不得复制判定。
2. 若 `_skill_check_events` 抽取后无法与今天的事件逐字段一致，停止：说明书要求的是"等价重构"，不是"顺便改进"。
3. 若既有用例的改写需要改**行为**（而不是夹具），停止并报告——那说明前置判定有问题。
4. 需要改 `src/engine/checks.py` 或新增存档迁移时停止。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_hide_preconditions.py -q
python -m pytest tests/rulesets/test_dnd2024_cunning_action.py tests/rulesets/test_dnd2024_d20_skills.py tests/rulesets/test_dnd2024_failed_d20_windows.py tests/rulesets/test_dnd2024_bardic_inspiration.py -q
python -m pytest tests/rulesets -q
ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_hide_preconditions.py
mypy
```

## 9. 执行报告模板

- 基线 / 分支 / 提交：
- `hide_preconditions` 的三个消费者（validation / available_intents / \_hide_events）与调用点：
- `_skill_check_events` 与旧实现的事件逐字段对照证据（测试 6）：
- 三条路径与"视线折叠"的实际落点：
- 17 条既有用例的改写方式与清单：
- 测试 1–14 逐项结果：
- 偏差与未决（必须含：DC 占位符、视线折叠两处）：
