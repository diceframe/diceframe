# S0-MM2 任务书：stat block 事实层补全（除特质与动作之外全落实）

| 项 | 值 |
|---|---|
| 切片编号 | S0-MM2（接 S0-MM1 的事实层） |
| 验收载体 | 六维参与豁免/技能派生、六个补全字段、目录化与查询、抗性取整口径 |
| 规则基准 | SRD 5.2.1 Monsters 章（stat block 字段）、p344–364 Animals（CR0–1 Beast）、p4 能力值调整表 |
| 代码基线 | `main@d843f7a4`（含 #809 MM1、#822、#823） |
| 前置任务 | 无（S0-MM1 已合入） |
| 状态 | 待执行 |

## 1. 范围与完成定义

MM1 把 stat block 的**事实**塞进了敌人契约，但留下三类问题：六维**存而不用**、四个字段**不在白名单**、数据**只内联不持久**。本片收口到"除特质与动作之外全部落实"：

1. **六维参与检定派生**：非玩家单位（敌人 / companion）需要豁免或技能检定时，与玩家**同一套算法**——`属性调整 + 熟练加值（熟练时）`，不允许"给值就完事、从不计算"。
2. **补六个字段**：`skills`、`languages`、`proficiency_bonus`、`hit_dice`、`subtype`、`passive_perception`。
3. **目录化**：stat block 从内联 dict 提升为持久内容（`content/monster_catalogs/srd_monsters.json`），加载器与 `validate_enemy_profiles` **共用同一份 schema**，并提供查询接口。
4. **抗性取整口径固化**：减半向下取整（31 → 15），写成契约并钉进用例。

**本片不做的**：特质（traits）、动作（actions 里 Multiattack / 豁免型动作 / rider / recharge）、召唤、CR 反算、alignment、`languages` 的"能否说话"消费。见 §7。

## 2. 规则依据

- **能力调整值**（p4）：`floor((score - 10) / 2)`。15→+2、8→−1、1→−5、20→+5、30→+10。
- **豁免**：stat block 只印"有熟练的那几项"的总加值；未印的按 `属性调整` 计算。
- **技能**：`属性调整 + PB`（专精为 `2×PB`）。因此 MM 给的技能加值**与标准算式自洽**——这正是"覆盖即等价"的前提。
- **熟练加值**：按 CR 决定（CR 0–4 → +2，5–8 → +3，9–12 → +4，13–16 → +5，17–20 → +6，21–24 → +7，25–28 → +8，29–30 → +9）。
- **抗性**：受到该类型伤害时减半，**向下取整**（31 → 15、7 → 3、1 → 0）。易伤 ×2、免疫 0；同一伤害实例只结算一次。

## 3. 定点代码事实（基线已核实）

| 事实 | 位置 |
|---|---|
| 敌人契约白名单 **无** `skills` / `languages` / `proficiency_bonus` / `hit_dice` / `subtype` / `passive_perception` | `combat/validation.py::_ENEMY_KEYS`（86–92） |
| 敌人 `abilities` 存进战斗状态，但**没有任何计算读它** | `resolution.py:300`；攻击用 `attack_bonus`、豁免用 `saving_throws` 原值 |
| 玩家豁免/技能的派生口径（本片要对齐的目标） | `character/derivation.py:710`：`ability_modifier(score) + (PB if proficient)` |
| 抗性减半已是 `amount // 2`，即向下取整 | `resolution.py::_halve_on_resist`：`max(0, amount // 2)` |
| 三组防御互斥、按"免疫→易伤→抗性"只取一条 | 同上（MM1 已做） |
| MM1 的教训：校验放行 ≠ 落账 | `_combat_start_events` 里敌人是显式白名单装配，新字段必须同时改校验、装配、视图 |

## 4. 精确契约

### 4.1 六维派生（本片核心）

1. 新增一个**共用**函数（不复制玩家逻辑）：

   ```python
   def derived_modifier(score: int) -> int
   def derived_save(abilities, ability, *, proficiency_bonus, proficient) -> int
   def derived_skill(abilities, skill_ability, *, proficiency_bonus, proficient) -> int
   ```

   玩家的 `derivation.py` 改为调用同一实现（消除两套口径），玩家的产出必须逐字节不变（现有测试即回归）。

2. **覆盖优先**：`saving_throws` / `skills` 里出现的条目视为**最终加值覆盖**，未出现则派生。
   - 覆盖生效时事件带 `source: "statblock"`；派生时带 `source: "derived"`。
   - 两者并存且数值不一致 → **不报错也不静默**：取覆盖值，事件同时记 `declared` 与 `derived` 两个值，供 GM 事后核对。

3. `abilities` 缺失或不足六项时：派生一律 fail closed（拒绝该次检定），不得默认 10。

### 4.2 六个补全字段

| 字段 | 形状 | 语义 |
|---|---|---|
| `skills` | `{skill_id: int}`，闭集（SRD 18 技能） | 最终加值覆盖；未列出的技能按派生 |
| `languages` | `[str]`，去重、非空、≤20 项 | 只存不消费（登记） |
| `proficiency_bonus` | `1..9` 整数，可缺省 | 缺省按 CR 表推导；显式给值优先 |
| `hit_dice` | `{count: 1..30, sides: 4..20, bonus: int}` 或字符串 `"5d8+10"` | 只校验形状，**不校验与 hp 相等**（SRD 允许取平均/取整） |
| `subtype` | 字符串（≤40，可空） | 不参与 `creature_type` 判定；`Beast` 只认 `creature_type` |
| `passive_perception` | `0..40` 整数，可缺省 | 缺省时由 `skills.perception`（覆盖优先）派生；两处都没有则 fail closed |

### 4.3 目录化与查询

1. 新建 `content/monster_catalogs/srd_monsters.json`，先收 **Animals p344–364 的 CR0–1 Beast**（含 Fly 形态），每条必须有 `source_ref`（页码）。
2. 加载器必须复用 `validate_enemy_profiles`，**不得写第二份 schema**；字段缺失或来源不符 → 整文件拒绝加载。
3. 查询接口（供 F25 与未来的 Polymorph 使用）：

   ```python
   catalog.get(ref) -> statblock | None
   catalog.find(creature_type="beast", cr_max="1/4", allow_fly=False) -> list[statblock]
   catalog.lookup(skill=..., ability=...)  # 不需要
   ```

4. `combat.start` 的敌人契约继续接受内联 statblock（旧路径不变）；目录条目与内联条目走**同一个校验函数**。

### 4.4 抗性取整

1. 保持 `amount // 2`，并在契约注释里写明"向下取整，31→15"。
2. 新增用例钉住 31→15、7→3、1→0、0→0；以及"易伤 ×2 后再抗性不再折半"（互斥）。

## 5. 改动白名单

1. `templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json`（新建）
2. `src/rulesets/dnd2024/combat/validation.py`（`_ENEMY_KEYS` + 六个字段校验）
3. `src/rulesets/dnd2024/combat/resolution.py`（构造敌人时装配新字段；抗性注释）
4. `src/rulesets/dnd2024/combat/engine.py`（敌人视图投影新字段；如豁免/技能读取点在此）
5. `src/rulesets/dnd2024/character/primitives.py`（共用派生函数）
6. `src/rulesets/dnd2024/character/derivation.py`（改为调用共用函数，产出不变）
7. 新建 `src/rulesets/dnd2024/monsters/catalog.py`（加载 + 查询）
8. `tests/rulesets/test_dnd2024_monster_facts.py`（扩展现有 13 条）
9. `tests/rulesets/test_dnd2024_monster_catalog.py`（新建）

不得改：特质/动作相关路径、`counterspell_dc`（见 §7）、召唤、CR 反算。

## 6. 逐项编号测试

1. `derived_modifier`：1→−5、8→−1、10→0、15→+2、20→+5、30→+10。
2. 非玩家豁免未给 `saving_throws` 时按派生（含/不含熟练各一条）。
3. 覆盖优先：给了 `saving_throws` 用给值，事件 `source="statblock"`。
4. 覆盖与派生不一致：取覆盖值，事件同时记两个值。
5. `abilities` 不足六项 → 检定 fail closed。
6. 技能覆盖：给 `skills.perception=5` 时用它；未给时按属性+PB 派生。
7. 技能自洽：`skills` 给值等于 `属性调整+PB` 时，两条路径结果相同（覆盖即等价）。
8. `proficiency_bonus` 缺省按 CR 表（CR 1/4→+2、CR 5→+3、CR 17→+6）；显式给值优先。
9. `hit_dice` 形状校验（`"5d8+10"` 合法；`"5d8+"`、`"0d8"`、`"5d7"` 非法）。
10. `subtype` 不影响 `creature_type` 判定：`creature_type="beast"` + `subtype="swarm"` 仍算 Beast。
11. `languages` 去重、空项拒绝、超 20 项拒绝。
12. `passive_perception` 缺省时由 `skills.perception` 派生；两处都缺 → fail closed。
13. 抗性向下取整：31→15、7→3、1→0、0→0。
14. 三组防御互斥：同一类型同时出现在抗性与易伤 → 校验拒绝。
15. 目录加载：CR0–1 Beast 全量可读；缺 `source_ref` 或字段不全 → 整文件拒绝。
16. 查询：`find(cr_max="1/4", allow_fly=False)` 不含任何 fly 形态；`cr_max="1"` 含 CR1。
17. 未知字段仍被拒绝，且错误信息列出全部未知键名（含新字段拼错）。
18. 旧敌人（不含新字段）照常开战，派生路径不炸、抗性/免疫行为不变。
19. 玩家派生回归：`derivation.py` 改用共用函数后，玩家豁免/技能数值逐字节不变（既有测试即证）。
20. 视图投影：新字段能通过 `available_intents` / actor view 读到（不出现"校验过、视图读不到"）。

## 7. 非目标与登记

**非目标**：特质（traits）、动作（Multiattack / 豁免型动作 / rider / recharge）、传说动作、召唤、CR 反算、XP 公式、alignment、`languages` 的"能否说话"消费。

**已知历史包袱（本片只登记，不动）**：`counterspell_dc` 是"把一个特质压成一个标量字段"的孤例，将来应由通用特质 + 反应表达吸收 → 缺口 `GAP-25`。

**停止条件**：

1. 派生要改玩家路径的**数值**（不是改成共用函数）→ 停止。
2. 覆盖与派生冲突时静默取一个 → 停止（必须留痕）。
3. 目录与内联出现第二份 schema → 停止。
4. 需要改 `_ENEMY_KEYS` 之外的既有敌人字段语义 → 停止并报告。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_monster_catalog.py tests/rulesets/test_dnd2024_monster_facts.py -q
python -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q -n 6 --dist loadfile
ruff check src/ tests/rulesets
mypy
```

## 9. 执行报告模板

- 基线 / 分支 / 提交：
- 共用派生函数的落点与玩家数值不变证据：
- 覆盖 vs 派生的裁决与留痕字段：
- 六个新字段的校验规则与拒绝样例：
- 目录规模（条目数、CR 分布、Fly 形态数）：
- 抗性取整用例结果：
- 测试 1–20 逐项结果：
- 偏差与未决：
