# S0-C2B v2 任务书：遮蔽的事实层（区域源 → 定级 → 感官合并）

| 项 | 值 |
|---|---|
| 切片编号 | S0-C2B v2（替换 2026-11-20 的 v1；v1 的声明层已实现，本片在其上补齐来源与感官） |
| 验收载体 | 区域遮蔽事实、`obscurement_row()` 定级、`effective_obscurement()` 感官合并、单一查询 |
| 规则基准 | SRD 5.2.1 Obscured Areas / Light；法术原文取自 `srd_spell_descriptions.json`（见 `S0C2_OBSCUREMENT_RESEARCH_2026-11-20.md`） |
| 代码基线 | `origin/main@e376f292` + 本片已实现的声明层（`combat/obscurement.py`、`combat.set_obscurement`） |
| 前置任务 | S0-C2A 掩护等级（已合入） |
| 状态 | 待执行 |

## 0. 与 v1 的差别（为什么重写）

v1 只做了"GM 声明等级"这一个来源，理由是"先做接口"。调研之后这个理由不成立：

1. **遮蔽的成因几乎全部是区域**——10 条 SRD 法术以及全部自然环境，都是"某片区域是轻度/重度遮蔽"，不是"某个生物身上带着一个状态"。只做 per-actor 声明，等于让每张桌子口述每一条 `fog_cloud`，正是 S0-F10 当初停止的那条理由。
2. **后果不取决于等级，取决于能否看见**。同一个 `heavily`，darkvision 下是"看得见但有劣势"，blindsight 下完全无影响。等级单独存在时回答不了任何规则问题。
3. 因此本片把"事实层"一次做完整：**区域 → 生物所处等级 → 观察者能否看见**。消费者（攻击劣势、感知劣势、Hide 前提）仍留到下一片。

## 1. 范围与完成定义

1. **区域源**：`combat.zones` 记录来自法术的遮蔽区域；每次查询按当前位置**现算**区域内生物，不往生物身上挂状态。
2. **定级**：生物的遮蔽等级 = `best(声明, 覆盖它的所有区域)`，取最高不叠加。
3. **感官合并**：`effective_obscurement(observer_id, target_id)` 返回观察者看该目标时的**有效**遮蔽等级，按 Darkvision / Blindsight / Truesight 的差异降级或忽略。
4. **撤销**：专注结束、被驱散、持续时间到、施法者死亡，四条路径都要能撤掉对应区域，且只能撤自己创建的那一条。
5. 本片**不改任何掷骰**：`resolution.py` 零改动。攻击劣势/感知劣势/Hide 前提属 C2E。
6. 先只接两条法术：`fog_cloud`（Heavily，非魔法）与 `darkness`（Heavily，**魔法**黑暗，带 darkvision 无效标记）。其余 8 条同形，留给后续按需补数据。

## 2. 规则依据（要点，详见调研文档）

- Lightly Obscured：依赖视觉的 Perception 有 Disadvantage。
- Heavily Obscured：试图看清时视为 Blinded（看不见）。
- `darkness`：魔法黑暗，**Darkvision 穿不过**；非魔法光无法照亮；与 ≤2 环的 Bright/Dim Light 重叠时驱散对方；用不透明物盖住载体可阻断。
- `fog_cloud`：区域 Heavily Obscured，非魔法。
- Darkvision：微光当明亮、黑暗当微光（→ 自然黑暗降为 lightly；**魔法黑暗无效**）。
- Blindsight / Truesight：不依赖视觉 / 看穿魔法黑暗 → 对遮蔽**完全忽略**。

## 3. 定点代码事实（基线已核实）

| 事实 | 位置 |
|---|---|
| 位置是一维标量，距离 = 位置差 | `combat/engine.py::_position` / `_distance` |
| 战斗状态一次性建立 | `combat/reducer.py`（`combat.started` 初始化 `positions` / `cover` / `obscurement`） |
| 专注终止是既有事件，且已有级联 | `combat/reducer.py`（`dnd2024.concentration.ended`） |
| 遮蔽声明（本片保留） | `combat/obscurement.py`、`combat.set_obscurement`、`combat/engine.py::obscurement_level` |
| 怪物感官已落地、无消费者 | MM1：敌人视图 `senses: {darkvision, blindsight, ...}` |
| 玩家感官仅一条声明 | 特性 `blindsight_range_ft: 30` |
| 10 条遮蔽法术均无 `spell_effects` 条目 | `combat_catalogs/srd_combat_core.json`（46 条效果，0 命中） |

## 4. 精确实现契约

### 4.1 区域事实

1. 状态：`combat.zones = {zone_id: {kind, level, origin, radius_ft, magical, spell_ref, caster_id, concentration, rounds_left}}`。
2. `kind` 闭集：`sphere` / `emanation` / `cylinder` / `cube`。**本片一律按半径判定**：`distance(origin, actor) <= radius_ft`。柱/立方用其外接半径，不建几何——任务书里写明这是近似，等 2D 地图再换。
3. `origin` 是坐标标量（与 `positions` 同一维），不是 actor id：法术点选在"20 尺外的地面"时落一个坐标。施法时若目标为空地，origin = 施法者位置 + 提交的偏移（不引入新的空间模型，只允许标量）。
4. `magical`：`darkness` 为 true，`fog_cloud` 为 false。这一位只被感官合并读。
5. `zone_id` 由 `(spell_ref, caster_id, 施法批次版本)` 决定，可重放。

### 4.2 区域的建立与撤销

1. 建立：`cast_spell` 结算到这两条法术时，额外发 `dnd2024.zone.created`；reducer 写进 `combat.zones`。不改 `cast_spell` 的既有事件序，只在末尾追加。
2. 撤销（四条，全部必须有用例）：
   - `dnd2024.concentration.ended`：撤掉 `caster_id` 匹配且 `concentration: true` 的全部区域；
   - 驱散路径：按 `spell_ref + caster_id` 撤；
   - 回合推进时 `rounds_left` 递减到 0：撤；
   - 施法者死亡：走既有专注终止级联（`dead` 会断专注），不另写一条。
3. **只能撤自己那条**：两个法师各放一个 `fog_cloud`，其中一人断专注，另一个的区域必须还在；两区域重叠时等级取最高，等级相同的多个来源 `sources` 全列。

### 4.3 定级

1. `obscurement_row(combat, actor_id)`：`best(声明等级, 覆盖该 actor 的所有区域等级)`，`sources` 列 `gm` 与 `zone:<zone_id>`。
2. 声明**不再优先**，改为参与取最高（v1 的"声明优先"作废）：两者都是独立事实——"GM 说他站在浓烟里"与"这里确实有一团浓雾"同时为真时，答案只能是更高的那个。**这是相对 v1 的口径变更，必须在报告里显式记录。**
3. 无任何来源时 `none`。

### 4.4 感官合并

`effective_obscurement(observer_id, target_id)` 按下列顺序处理，返回 `{level, sight_disadvantage, blocks_sight, sources, bypass}`：

| 观测者能力 | 处理 |
|---|---|
| 无特殊感官 | 原样返回目标的等级 |
| Darkvision（范围内） | `heavily` 且来源非魔法 → 降为 `lightly`；`lightly` → `none`；`heavily` 且来源含魔法 → **不降级**，记 `bypass: "darkvision_fails_magical_darkness"` |
| Blindsight（范围内） | 完全忽略，返回 `none`，`bypass: "blindsight"` |
| Truesight（范围内） | 完全忽略，返回 `none`，`bypass: "truesight"` |

1. 感官范围读观测者视图的 `senses`（怪物）与特性声明（玩家 `blindsight_range_ft`）；范围不足按"无特殊感官"处理，用 `_distance` 判断。
2. **Darkvision 降级只对非魔法来源**：只要有任一同级来源是 `magical` 就不降级——感官层要看 `magical`，不能只看等级。
3. 本函数是**唯一**的"能否看见"入口，C2E 的攻击/感知/Hide 都从这里读。
4. 名字用 `effective_obscurement` 而不是 `sight_obscurement`：后者暗示只处理视觉链，而 blindsight 恰恰不走视觉。

### 4.5 投影

1. actor view 的 `obscurement` 保持 row 形状（v1 已实现），`sources` 增加 `zone:` 项。
2. **不在视图里投影成对结果**：`effective_obscurement` 只作为函数提供，避免在没有面板需求前先造字段。

## 5. 改动白名单

1. `combat/obscurement.py`（区域类型、成员判定、定级合并、`effective_obscurement`）
2. `combat/primitives.py`（如需补事件名常量）
3. `combat/reducer.py`（`zones` 状态、`zone.created` / `zone.expired`、专注与回合递减的撤销）
4. `combat/engine.py`（`obscurement_row` 改读区域、`effective_obscurement`、施法分支追加 `zone.created`）
5. `combat/resolution.py`——**只允许**在 `fog_cloud` / `darkness` 的结算点追加一条区域事件，不得改任何掷骰或伤害路径
6. `templates/.../combat_catalogs/srd_combat_core.json`（仅给 `fog_cloud` / `darkness` 补最小 `spell_effects` 条目）
7. `tests/rulesets/test_dnd2024_obscurement_levels.py`（扩充，v1 的 22 条保留）

不得改：光照系统、2D 地图、几何求交、攻击/感知/Hide 的消费者、前端、存档迁移。

## 6. 逐项编号测试

1. v1 的 22 条全部保留并通过（声明层不回归）。
2. 区域建立：施放 `fog_cloud` → `dnd2024.zone.created` → `combat.zones` 有一条，`level=heavily`、`magical=false`。
3. 区域定级：半径内的生物 `obscurement_row` 返回 `heavily`，`sources` 含 `zone:`；半径外返回 `none`。
4. 走进走出：移动进区域 → 再查为 `heavily`；移动出去 → 回到 `none`（状态里没挂过东西，故无需清理）。
5. 重叠不叠加：两个 `fog_cloud` 重叠，等级仍是 `heavily`，`sources` 两条。
6. 声明与区域并存：GM 声明 `lightly` + 区域 `heavily` → 结果 `heavily`（取最高，v1 的"声明优先"作废）。
7. 专注撤销：断掉施法者专注 → 他的区域消失，另一个施法者的区域仍在。
8. 回合到期：`rounds_left` 递减到 0 → 区域消失，且不误伤其它区域。
9. 驱散撤销：按 `spell_ref + caster_id` 精确移除。
10. 魔法标记：`darkness` 的 `magical=true`，`fog_cloud` 的 false。
11. Darkvision 对自然重度：观测者 darkvision 60、距离 30 → `heavily` 降为 `lightly`。
12. Darkvision 对魔法重度：同一观测者看 `darkness` 内目标 → 仍 `heavily`，`bypass == "darkvision_fails_magical_darkness"`。
13. Darkvision 范围不足：距离 70、darkvision 60 → 不降级。
14. Blindsight 忽略：blindsight 30、距离 20 → `none`，`bypass == "blindsight"`；距离 40 → 原样返回。
15. Truesight 忽略：同上，`bypass == "truesight"`。
16. 区域坐标：施法者位置 + 偏移落成 `origin`；同批次重放得到同一 `zone_id` 与同一结果。
17. 既有回归：`fog_cloud` / `darkness` 之外的法术行为不变；未建立区域的战斗，`obscurement_row` 与 v1 逐字段一致。

## 7. 非目标与停止条件

**非目标**：光照（Bright/Dim/Darkness 作为区域事实）、`darkness` 与 ≤2 环光的互相驱散、盖住载体阻断黑暗、其余 8 条遮蔽法术、2D 几何与真实形状、攻击/感知/Hide 消费者、前端、区域可视化。

**停止条件**：

1. 若 `cast_spell` 的结算不允许多发一条事件而不改既有事件序，停止并报告——不得把区域塞进 `condition.applied` 冒充。
2. 若"点位坐标"必须引入新的空间模型（例如需要二维坐标才能表达 20 尺外的球心），停止：本片只接受**沿既有标量轴**的偏移；需要二维是下一片的前提。
3. 若玩家侧感官读不到（`blindsight_range_ft` 之外没有统一投影），先只接怪物 `senses`，并在报告里登记"玩家 darkvision 未接"，不得用角色名或文本猜测。
4. 需要存档迁移或改 `GameInstance` 序列化形状时停止。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_obscurement_levels.py -q
python -m pytest tests/rulesets/test_dnd2024_cover_levels.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_obscurement_levels.py
mypy
```

## 9. 执行报告模板

- 基线 / 分支 / 提交：
- 区域形状与成员判定的近似（柱/立方 → 半径）落在哪里：
- 「声明优先 → 取最高」的口径变更记录：
- 四条撤销路径各自的触发事件与用例编号：
- 感官合并表（darkvision / blindsight / truesight）的实现位置与 `bypass` 取值：
- 测试 1–17 逐项结果：
- 偏差与未决（尤其玩家侧感官缺口、区域坐标的一维限制）：
