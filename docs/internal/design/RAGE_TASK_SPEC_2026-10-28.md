# 切片 2 任务书：Rage（持续状态 + 条件伤害加成 + 豁免优势）

版本：v1（2026-10-28）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。

---

## 1. 目标

为 D&D 2024 规则集实现 **Rage**（野蛮人 1 级特性）的完整闭环：catalog 声明 → 进入/持续/结束的生命周期 → 战斗结算 → 测试。本切片引入三个新原语，是后续所有"持续状态类"特性（祝福、诗人激励、各种 stance）的模板：

1. **持续状态实例**：condition 挂载 → 每回合续期检查 → 到期/失能移除；
2. **条件性伤害加成**：力量攻击命中时附加 track 驱动的 flat bonus；
3. **豁免优势来源**：raged 时力量豁免双骰取高。

**规则语义（SRD 5.2.1，2024 版）**：

- 以附赠动作进入狂暴，消耗 1 次使用次数；次数曲线与恢复**已由现有 catalog 完整声明，本切片勿改**（见 §2）；
- **狂暴伤害**：使用力量发动武器攻击或徒手打击并对目标造成伤害时，伤害掷骰获得额外加值（+2 / 9 级起 +3 / 16 级起 +4）；
- **力量优势（2024 版语义）**：力量检定与力量豁免具有优势。**注意：2024 版不给攻击掷骰优势**（那是鲁莽攻击的事），不要接错管线；
- **持续时间（2024 版语义）**：狂暴持续至你的下个回合结束。若下一回合狂暴仍激活，可通过以下方式延长一轮：对一名敌人进行一次攻击检定 / 以附赠动作延长。每次延长持续至下回合结束，至多维持 10 分钟（100 轮）；
- **提前结束**：陷入失能（昏迷）状态时狂暴立即结束；战斗结束时狂暴随之结束（之后没有回合可以续期）；
- 规则原文出处：`.rulebooks/dnd5r/玩家手册2024/角色职业/野蛮人/野蛮人.htm` L58-77（狂暴完整文本）及 L36-56 特性表（狂暴次数列、狂暴伤害列）。

**明确不做（本切片裁剪，勿实现）**：

| 项 | 不做的原因 |
|---|---|
| 钝击/穿刺/挥砍伤害抗性（减半） | 需要伤害减半管线（reducer 按 damage_type 分流），独立切片 |
| 狂暴期间无法施法/保持专注 | 需要 cast_spell validation 与 concentration 联动，独立切片 |
| "迫使敌人豁免"延长路径 | raged 野蛮人无法施法，1-8 级无迫使豁免途径，无宿主 |
| 力量**检定**优势（豁免优势要做） | dnd2024 战斗 `INTENT_TYPES`（primitives.py L13-20）无 ability_check 类型，无宿主；待 GM check 系统接入 dnd2024 后补 |
| 重甲提前结束/进入限制 | 战斗内无换装投影，边缘场景 |
| 前端任何改动 | condition 与 capability 按钮均数据驱动 |

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| **次数 track 已存在勿改**：barbarian `tracks.rages` = `[2,2,3,3,3,4,4,4,4,4,4,5,5,5,5,5,6,6,6,6]`（1-2 级 2、3-5 级 3、6-11 级 4、12-16 级 5、17 级起 6） | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` L58 |
| **加值 track 已存在勿改**：barbarian `tracks.rage_damage` = `[2×8, 3×7, 4×5]`（1-8 级 +2、9-15 级 +3、16 级起 +4） | 同文件 L59 |
| **资源声明已存在勿改**：`class_resources.barbarian` 已含 `rages`（`maximum_track: "rages"`，短休恢复 1、长休全恢复） | `templates/rulesets/dnd2024_srd/content/rest_catalogs/srd_recovery.json` L30-32 |
| fighter L1 特性表已含 `rage` id（幽灵 id，勿改 features_by_level） | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` L48 |
| `ACTION_KINDS` 白名单（切片 1 后为 5 种，含 `heal`） | `src/rulesets/dnd2024/features/models.py` |
| capability 解析与 `FeatureCapability` 字段 | `src/rulesets/dnd2024/features/models.py` `_parse_capability` |
| capability → 事件结算路径（切片 1 的 heal 分支是最新模板） | `src/rulesets/dnd2024/combat/resolution.py` `_class_capability_events` |
| track 查表机制：parameters 里 `<key>_track` 自动解析（如 `rage_damage_track` → `feature_value(character, "rage", "rage_damage")`） | `src/rulesets/dnd2024/features/resolver.py` L184-233 |
| **condition 事件形状（本切片核心依赖）**：`condition.applied` 的自由字段（duration/turns_remaining 等）全量 deepcopy 进 `conditions[condition]` dict；`condition.removed` 直接 pop | `src/rulesets/dnd2024/combat/reducer.py` L182-198 |
| condition 生命周期先例：`_expire_conditions` 在回合切换时按 `duration`（`actor_turn_start`/`caster_turn_end`/`caster_next_turn_end`+`turns_remaining` 递减）直改 snapshot | `src/rulesets/dnd2024/combat/engine.py` L1097-1132 |
| reducer 直改联动先例：`condition.applied("stable")` 时直接写 `death_saves` | `src/rulesets/dnd2024/combat/reducer.py` L188-193 |
| **攻击结算（加值注入点）**：属性选择（`ability`，str/dex/finesse/ability_choice）、命中后 `attack_damage_node(damage_formula, modifier, critical, extra_dice)` → `roll_damage_node`；hexed 的 `extra_dice` 是条件性附加先例 | `src/rulesets/dnd2024/combat/resolution.py` L98-216（伤害组装在 L182-202） |
| flat bonus 包装先例：`{"op": "add", "args": [node, {"op": "constant", "value": bonus}]}`（切片 1 heal 分支同款） | `src/rulesets/dnd2024/combat/resolution.py` heal 分支 |
| **豁免结算（优势接入点）**：saving_throw 分支当前单骰 + bless，无优势机制；掷骰者是 `target`，`target["conditions"]` 可用（blessed 同款） | `src/rulesets/dnd2024/combat/resolution.py` L292-304 |
| capability 可用性投影（`requires_condition`/`forbidden_condition` 过滤发生在这里，能拿到 actor） | `src/rulesets/dnd2024/combat/engine.py` `_available_class_capabilities`（经 view.py L363-375） |
| intent 校验（`_validate_class_capability` 需新增 condition 检查） | `src/rulesets/dnd2024/combat/validation.py` L308-353 |
| 战斗内 actor view 含 `conditions` dict（key=condition id，value=元数据 dict） | `src/rulesets/dnd2024/combat/view.py` / resolution.py `cannot_regain_hp`、`blessed` 等用法 |
| 测试 helper（`capability_intent`/`capability_actions`/`advance`/`monk_instance` 等，新测试复用） | `tests/rulesets/dnd2024_monk_common.py` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。`git status` 出现白名单外的任何变更即为失败。

### 3.1 `src/rulesets/dnd2024/features/models.py`

1. `ACTION_KINDS` 增加 `"rage"` 与 `"rage_sustain"` 两个 kind；
2. `FeatureCapability` 增加两个可选字段（默认 `None`，二者语义互斥，解析时同设报错）：
   - `requires_condition: str | None` —— 仅当 actor 持有该 condition 时 capability 可用；
   - `forbidden_condition: str | None` —— actor 持有该 condition 时 capability 不可用。

除上述外不动任何解析逻辑与既有字段。

### 3.2 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

新增 `"barbarian"` 类条目（与 `"monk"`/`"paladin"`/`"fighter"` 平级）：

```json
"barbarian": {
  "source_ref": "srd-5.2.1:p28:barbarian-features",
  "features": [
    {
      "id": "rage",
      "minimum_level": 1,
      "source_ref": "srd-5.2.1:p28:rage",
      "summary": "Bonus Action: enter a Rage. While raging, Strength attacks and Unarmed Strikes deal extra damage (see the Barbarian table), and your Strength saving throws have advantage. The Rage lasts until the end of your next turn; making an attack against an enemy, or using a Bonus Action, extends it for another turn, up to 10 turns. It ends early if you are Incapacitated. You regain one use on a Short Rest and all uses on a Long Rest.",
      "parameters": {
        "rage_damage_track": "rage_damage"
      },
      "resource_id": "rages",
      "capabilities": [
        {
          "id": "rage",
          "cost": {"bonus_action": 1, "resources": [{"id": "rages", "amount": 1}]},
          "requires_hostile_target": false,
          "forbidden_condition": "rage",
          "actions": [{"kind": "rage", "count": 1}]
        },
        {
          "id": "rage_sustain",
          "cost": {"bonus_action": 1},
          "requires_hostile_target": false,
          "requires_condition": "rage",
          "actions": [{"kind": "rage_sustain", "count": 1}]
        }
      ]
    }
  ]
}
```

同步在顶层 `labels` 增加：`"barbarian": "Barbarian"`、`"rage": "Rage"`、`"rage_sustain": "Sustain Rage"`。

### 3.3 `src/rulesets/dnd2024/combat/resolution.py`

1. `_class_capability_events` 新增两个分支：
   - `action.kind == "rage"`：产事件 `{"type": "condition.applied", "target_id": actor_id, "condition": "rage", "duration": "rage", "turns_remaining": 100}`；
   - `action.kind == "rage_sustain"`：产事件 `{"type": "dnd2024.condition.sustained", "target_id": actor_id, "condition": "rage"}`（只刷新续期标记，**不得**重置 `turns_remaining`）。
2. 攻击结算（`_attack_events` L182-202 命中分支）：当 `"rage" in actor["conditions"]` 且本次攻击 `ability == "str"` 时，将 `attack_damage_node(...)` 结果包装 `{"op": "add", "args": [node, {"op": "constant", "value": rage_damage}]}`（heal 分支同款），其中 `rage_damage = feature_value(self._feature_character(actor), "rage", "rage_damage")`；加值只影响伤害，不影响攻击加值。
3. 攻击结算针对 `target["kind"] == "enemy"` 且 `"rage" in actor["conditions"]` 时，追加事件 `{"type": "dnd2024.condition.sustained", "target_id": actor_id, "condition": "rage"}`（自动续期）。**注意：规则原文是"对一名敌人进行一次攻击检定"即延长——不要求命中**，落空同样续期；只要求攻击对象是敌人。
4. saving_throw 分支（L292-304）：当 `target` 持有 `"rage"` condition 且 `save_ability == "str"` 时双骰取高（rolls 记两颗），`check.resolved` 事件补充 `"advantage_mode": "advantage"` 与 `"advantage_sources": [{"code": "rage", "mode": "advantage"}]`。

### 3.4 `src/rulesets/dnd2024/combat/engine.py`

1. `_available_class_capabilities`：按 capability 的 `requires_condition`/`forbidden_condition` 过滤（actor conditions 可从该层访问；参考 view.py `_feature_character` 的取法）。
2. `_expire_conditions`：新增 rage 生命周期——当 `previous_actor_id` 是 rage 持有者且其回合刚结束时（**v2 修正：加 `established` 保证轮**）：
   - 若 `unconscious`：无条件移除；
   - 若 `established` 为假（进入回合 N 刚结束）：置 `established = True`、清除 `sustained_this_turn`，**不移除**（规则保证狂暴持续至下个回合结束，第一轮存续与本回合是否攻击/延长无关）；
   - 若 `sustained_this_turn` 为真：清除该标记、`turns_remaining -= 1`，减到 0 移除；
   - 否则移除 `rage`。
   v1 的规格缺少保证轮（进入回合不攻击即死，比规则提前一轮），执行端按 v1 实现后在审核中发现并修正，`condition.applied` 载荷相应补 `established: False`。

### 3.5 `src/rulesets/dnd2024/combat/validation.py`

`_validate_class_capability` 增加：`requires_condition` 未满足或 `forbidden_condition` 满足时拒绝 intent，错误消息含 capability 标签（对齐既有资源不足的报错风格）。

### 3.6 `src/rulesets/dnd2024/combat/reducer.py`

1. 新事件 `dnd2024.condition.sustained`：`conditions[target_id][condition]["sustained_this_turn"] = True`；
2. `_apply_hp_change` 应用昏迷（unconscious）时：若该 actor 持有 `rage` 则直接 pop（直改联动先例：reducer L188-193 的 stable→death_saves）。

### 3.7 本地化（两个文件）

- `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`：加 `barbarian` / `rage` / `rage_sustain` 标签；
- `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`：加 `barbarian: "野蛮人"`、`rage: "狂暴"`、`rage_sustain: "延长狂暴"`。

### 3.8 测试（新文件 + 既有文件）

新增 `tests/rulesets/test_dnd2024_rage.py`（复用 `dnd2024_monk_common` helper），在 `tests/rulesets/test_dnd2024_class_features.py` 补 catalog 装载用例：

1. **装载**：barbarian 的 `rage`/`rage_sustain` capability 可解析；`rage_damage` 查表（1 级 = 2、9 级 = 3、16 级 = 4）；zh-CN 标签（"狂暴"）；
2. **资源**：1 级 `rages` 上限 2；短休恢复 1、长休全恢复（rest_catalog 既有声明，正向断言）；
3. **进入**：提交 `rage` capability → 事件序 `dnd2024.action.spent`(bonus_action) → `dnd2024.class_resource.spent`(rages,1) → `condition.applied`(rage, turns_remaining=10)；落地后 `conditions.rage` 存在；
4. **重复进入被拒**：raged 时 `rage` intent 校验失败（forbidden_condition），无任何事件、资源与经济不变（原子性）；
5. **加成**：raged 5 级野蛮人 str 近战武器命中 → `resource.changed` 的 damage = 骰值 + 属性修正 + 3（固定骰序列断言 rolls 与 delta 关系）；同 actor 用 dex/远程武器命中不加成；
6. **自动续期**：raged 后对敌人进行攻击（含落空）→ 出现 `dnd2024.condition.sustained`；推进该 actor 回合结束后 rage 仍存在且 `turns_remaining` 减 1；对非敌对目标攻击不产生续期；
7. **到期移除**：raged 后本回合未进行任何对敌攻击、未提交 `rage_sustain` → 回合结束后 rage 被移除；
8. **附赠动作延长**：raged、本回合未攻击、提交 `rage_sustain` → 回合结束后 rage 保留；
9. **上限**：连续续期至 `turns_remaining` 归 0 → rage 移除（可用直接改 snapshot 构造剩余轮数为 1 的前置状态，不必真打 100 轮）；
10. **豁免优势**：raged野蛮人作为法术 target 掷 str 豁免 → `rolls` 长度 2 且 `advantage_mode == "advantage"`；非 str 豁免单骰；
11. **昏迷联动**：raged 野蛮人 hp 降至 0 → rage 被移除；
12. **rage_sustain 前置**：未 raged 时 `rage_sustain` intent 校验失败（requires_condition）。

## 4. 前置步骤（动代码之前）

```powershell
# 基线必须全绿，否则停下报告。已知豁免：test_dnd2024_character_builder.py 的 11 个上游既有失败
# （10 × unresolved reference 'ability:con' in background:soldier，1 × 404），与本切片无关。
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_monk_combat.py -q
```

## 5. 验收标准

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py -q   # 全绿
.venv\Scripts\python.exe -m pytest tests -q -k "rage"                                # 全绿
.venv\Scripts\python.exe -m pytest tests/rulesets -q                                 # 除已知豁免外全绿
git status   # 变更仅限白名单
```

## 6. 禁止事项

- 不改 `progression_catalog`（`rages`/`rage_damage` track 与 `features_by_level` 均已正确）；
- 不改 `rest_catalog`（`rages` 资源声明已存在且正确）；
- 不改 `ACTION_COST_KINDS` / `EQUIPMENT_REQUIREMENT_KEYS` / `WEAPON_KINDS`；
- 不实现"明确不做"表中的任何项（抗性、施法限制、迫使豁免延长、力量检定优势、重甲、前端）；
- 不改法术豁免的既有单骰语义（只在 raged+str 时走双骰分支）；
- 不加兼容层、回退、try/except 兜底；不为未来特性预留字段；
- 不新增第三方依赖；
- 不修改本任务书之外的任何文档。

## 7. 停止条件（触发即停，写入执行报告，不要猜）

1. §2 所列"已存在勿改"的 catalog 事实与实际不符（如 `rage_damage` track 不存在或曲线不同）；
2. `condition.applied` 的自由字段被 reducer 丢弃或被事件校验拒绝（§2 契约失效）；
3. `_expire_conditions` 的 `previous_actor_id` 语义无法表达"rage 持有者回合结束"时机（执行前先读该函数的全部调用点再判断）；
4. `FeatureCapability` 新增可选字段会破坏既有 catalog 装载（`_parse_capability` 拒绝未知键且无法在白名单内解决）；
5. 基线测试非绿（已知豁免除外）；
6. 任何需要改动 `resolver.py` 公有契约、`view.py` 或 `frontend-v2/` 才能完成的情况。

## 8. 执行报告（完成后必须填写，仓库根 `EXECUTION_REPORT_SLICE2.md`）

```markdown
# 切片 2 执行报告
- 任务书版本：v1
- 分支：feat/rage
## changed-file list（与白名单逐项对照）
...
## 测试输出（完整粘贴）
...
## 停止条件触发情况（无则写"无"）
...
## 未完成项
...
```

注意：报告文件 `EXECUTION_REPORT_SLICE2.md` 是白名单唯一豁免项（临时产物，审核后删除）。

## 9. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-10-28 | 初版（依据规则书原文与源码勘察；progression/rest catalog 经核实零改动） |
| v2 | 2026-10-29 | 审核期修正：§3.4.2 生命周期加 `established` 保证轮（v1 规格把续期检查提前到进入回合，违反"持续至你的下个回合结束"）；§3.8.5 数值错误（5 级 +2 非 +3，执行端已按 track 事实自我纠正）；§3.4.1 方法归属错误（`_available_class_capabilities` 实际定义于 view.py，执行端以 engine 同名覆写解决） |
