# 切片 1 任务书：Second Wind（heal 原语）

版本：v3（2026-10-28；v2 修正条目已存在问题；v3 依据规则书原文修正次数曲线表述，并接入规则书引用）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。

---

## 1. 目标

为 D&D 2024 规则集实现 **Second Wind**（Fighter 1 级特性）的完整闭环：catalog 声明 → 资源生命周期 → 战斗结算 → 测试。同时为特性能力系统引入第一个 `heal` 动作原语。

**规则语义（SRD 5.2.1）**：

- Bonus Action，目标为自身；
- 恢复 `1d10 + Fighter 职业等级` 点 HP；
- 使用次数为固定升级表：**1-3 级 2 次、4-9 级 3 次、10 级起 4 次**；短休恢复 1 次已消耗的使用次数，长休恢复全部（**以上资源与恢复语义已由现有 rest_catalog 完整声明，本切片勿改**）；
- 规则原文出处：`.rulebooks/dnd5r/玩家手册2024/角色职业/战士/战士.htm` L73-74（"1级：回气 Second Wind"）及 L45-56 特性表"回气"列；
- 角色处于 `cannot_regain_hp` 条件时恢复量归零（与现有法术治疗一致）。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| 特性目录 JSON 形状（monk 是模板，注意 parameters / resource_id / capabilities 结构） | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` |
| `ACTION_KINDS` 白名单（fail closed，未知 kind 装载即报错） | `src/rulesets/dnd2024/features/models.py` L26 |
| capability 解析（cost = 一个 action + resources[]；actions = kind+count） | `src/rulesets/dnd2024/features/models.py` `_parse_capability` |
| fighter L1 特性表**已含** `second_wind` id（幽灵 id，勿改） | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` L117 |
| **资源声明已存在勿改**：`class_resources.fighter` 已含 `second_wind`（`maximum_track: "second_wind"`，短休恢复 1、长休全恢复） | `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json` L42-46 |
| **次数 track 已存在勿改**：fighter `tracks.second_wind` = `[2,2,2,3,3,3,3,3,3,4,...]`（1-2 级 2 次、3 级起 3 次、10 级起 4 次） | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` L126-131 |
| capability → 事件的唯一结算路径 | `src/rulesets/dnd2024/combat/resolution.py` `_class_capability_events` L447-504 |
| 治疗事件的权威形状（照抄此形状，含 `cannot_regain_hp` blocked 逻辑与 `max(1, healing)`） | `src/rulesets/dnd2024/combat/resolution.py` L339-355（法术 healing 分支） |
| 掷骰与公式节点工具 | `src/rulesets/dnd2024/combat/action_adapter.py`（`roll_damage_node` 等） |
| track 查表机制（`_track_value(character, class_id, track_id, default)`） | `src/rulesets/dnd2024/features/resolver.py` L489/L508 |
| capability 可用性校验（`requires_hostile_target: false` 且非 unarmed_strike 时现路径不做目标校验） | `src/rulesets/dnd2024/combat/validation.py` `_validate_class_capability` L308-353 |
| 现有特性测试文件 | `tests/rulesets/test_dnd2024_class_features.py` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。`git status` 出现白名单外的任何变更即为失败。

### 3.1 `src/rulesets/dnd2024/features/models.py`

`ACTION_KINDS` 增加 `"heal"`（一行改动）。**不要**动 `ACTION_COST_KINDS`、`EQUIPMENT_REQUIREMENT_KEYS`、`WEAPON_KINDS` 或任何解析逻辑。

### 3.2 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

新增 `"fighter"` 类条目（与 `"monk"`、`"paladin"` 平级）：

```json
"fighter": {
  "source_ref": "srd-5.2.1:p47:fighter-features",
  "features": [
    {
      "id": "second_wind",
      "minimum_level": 1,
      "source_ref": "srd-5.2.1:p48:second-wind",
      "summary": "Bonus Action: regain HP equal to 1d10 plus your Fighter level. Uses equal to your Proficiency Bonus, all restored on a Long Rest.",
      "parameters": {
        "heal_dice": "1d10",
        "heal_bonus_track": "second_wind_heal_bonus"
      },
      "resource_id": "second_wind",
      "capabilities": [
        {
          "id": "second_wind",
          "cost": {"bonus_action": 1, "resources": [{"id": "second_wind", "amount": 1}]},
          "requires_hostile_target": false,
          "actions": [{"kind": "heal", "count": 1}]
        }
      ]
    }
  ]
}
```

同步在顶层 `labels` 增加：`"fighter": "Fighter"`、`"second_wind": "Second Wind"`。

### 3.3 ~~rest_catalog 新增条目~~（v2 撤销：`class_resources.fighter.second_wind` 已存在且正确，本文件**不做任何改动**）

### 3.4 `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json`

fighter 的 `tracks` 对象（L126-131）**只新增一个** track——治疗加值曲线（Fighter 等级）：

```json
"second_wind_heal_bonus": [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20]
```

不要动该文件里已有的 `second_wind` 次数 track 与 `features_by_level`。

### 3.5 `src/rulesets/dnd2024/combat/resolution.py`

`_class_capability_events` 的 action 分支（L498 `elif action.kind in {"dash", ...}` 之后）新增：

```python
elif action.kind == "heal":
    # 从 capability 所属 feature 的 parameters 取 heal_dice 与 heal_bonus_track
    # bonus = track 值（角色 fighter 等级），dice = "1d10"
    # healing, rolls = roll_damage_node(<dice+bonus 节点>, rng); healing = max(1, healing)
    # blocked = "cannot_regain_hp" in actor conditions（照抄法术 healing 分支语义）
    # 事件：{"type": "resource.changed", "resource": "hp", "target_id": actor_id,
    #        "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
    #        "healing": True, "rolls": rolls, **({"blocked_by": "cannot_regain_hp"} if blocked else {})}
```

实现要求：

- `heal` 事件的 `target_id` 一律是 `actor_id`（自身），忽略 intent 的 `target_id`；
- feature parameters 的获取路径：`FeatureCapability.feature_id` → resolver 侧 catalog。若 resolver 缺少只读访问方法，允许在 `src/rulesets/dnd2024/features/resolver.py` 增加一个小的只读方法（如 `class_track_value(character, class_id, track_id)` 包装现有 `_track_value`），除此之外不动 resolver 任何现有方法；
- 公式节点形状参考 `action_adapter.py` 中现有节点构造；不要新写掷骰逻辑；
- 每回合多次 `heal`（count > 1）按语义应为单次事件合并（当前 catalog 只有 count=1，按 1 实现即可，不要为 count>1 发明行为）。

### 3.6 本地化（两个文件）

- `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`：加 `fighter` / `second_wind` 标签；
- `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`：加 `fighter: "战士"`、`second_wind: "回气"`（若该文件结构与 en 不一致，以 en 结构为准，先读再改）。

### 3.7 测试（新增用例，不改现有用例）

在 `tests/rulesets/test_dnd2024_class_features.py`（如战斗结算类用例更适合放在既有 combat 测试文件，先 `Grep pattern="class_capability" path="tests"` 找到现有 capability 结算测试所在文件，放同处）：

1. **装载**：bundle 加载后 fighter 的 `second_wind` feature 与 capability 可解析，未知字段缺失时报错（fail-closed 已由现有机制保证，加一条正向断言即可）；
2. **资源**：1 级 fighter 的 `second_wind` 资源存在、上限 2；4 级上限 3；10 级上限 4；短休恢复 1、长休恢复满；
3. **结算**：提交 `class_capability`（capability_id=second_wind）→ 同一 EventBatch 内依次出现 `dnd2024.action.spent`(bonus_action)、`dnd2024.class_resource.spent`(second_wind,1)、`resource.changed`(hp, healing=true, rolls 长度=1)；
4. **治疗量**：1 级 fighter 治疗 = 1d10 + 1（断言 rolls 与 delta 的关系）；5 级 = 1d10 + 5；
5. **拒绝**：资源为 0 时 intent 被 `CombatIntentError` 拒绝，且不产生任何事件（原子性）；
6. **阻断**：`cannot_regain_hp` 条件下 delta 为 0 且带 `blocked_by`。

## 4. 前置步骤（动代码之前）

```powershell
# 基线必须全绿，否则停下报告
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py -q
```

## 5. 验收标准

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py -q   # 全绿
.venv\Scripts\python.exe -m pytest tests -q -k "second_wind"                          # 全绿
git status   # 变更仅限白名单
```

全量回归（建议，慢）：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## 6. 禁止事项

- 不改 `ACTION_COST_KINDS` / `EQUIPMENT_REQUIREMENT_KEYS` / `WEAPON_KINDS` 及任何 schema 语义；
- 不改 `progression_catalog` 的 `features_by_level`（`second_wind` id 已存在）；
- 不改法术 healing 结算路径（`resolution.py` L339-355 只读参考）；
- 不改 validation 逻辑（除非核实发现空 `target_id` 会被拒，见 §7）；
- 不动 `frontend-v2/`（capability 按钮由 view 层数据驱动出现，本切片无前端工作）；
- 不加兼容层、回退、try/except 兜底；不为未来特性预留字段；
- 不新增第三方依赖；
- 不修改本任务书之外的任何文档。

## 7. 停止条件（触发即停，写入执行报告，不要猜）

1. `srd_classes.json` 中 fighter 的 `tracks` 段与 v2 描述不符（如 `second_wind_heal_bonus` 已存在）；
2. resolver 无法以只读方式暴露 track 值 / feature parameters，需要改动公有契约；
3. 核实发现 `class_capability` intent 对空 `target_id` 会被 validation 拒绝（heal 以自身为目标，需要确认现有路径放行）；
4. `resource.changed` 事件被 reducer 以与法术 healing 不同的方式处理；
5. 基线测试非绿；
6. 任何需要新增事件类型或修改 reducer 的情况（本切片不允许）。

## 8. 执行报告（完成后必须填写，附在仓库根 `EXECUTION_REPORT_SLICE1.md`）

```markdown
# 切片 1 执行报告
- 任务书版本：v1
- 分支：feat/second-wind
## changed-file list（与白名单逐项对照）
...
## 测试输出（完整粘贴）
...
## 停止条件触发情况（无则写"无"）
...
## 未完成项
...
```

注意：报告文件 `EXECUTION_REPORT_SLICE1.md` 是白名单唯一豁免项（临时产物，审核后删除）。

## 9. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-10-28 | 初版 |
| v2 | 2026-10-28 | 执行侧触发停止条件（fighter 条目已存在）后修正：§3.3 撤销（rest_catalog 已声明 `second_wind` 资源）、§3.4 缩减为仅新增 `second_wind_heal_bonus` track、规则语义修正（次数为 2/3/4 固定表而非 PB 曲线；短休恢复 1 次而非不恢复）、测试断言同步更新 |
| v3 | 2026-10-28 | 依据规则书原文（`.rulebooks` 玩家手册2024/战士）修正次数曲线表述（1-3 级 2 次、4 级起 3 次，v2 误写为 3 级起 3 次）；任务书新增"规则原文出处"引用字段 |
