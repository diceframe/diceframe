# 切片 3 任务书：Uncanny Dodge（触发式机制泛化）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。

---

## 1. 目标

为 D&D 2024 规则集实现 **Uncanny Dodge**（游荡者 5 级特性）的完整闭环：catalog 声明 → 触发式机制泛化 → 伤害减半结算 → 测试。本切片将触发式机制从法术专属泛化到职业特性，是后续所有"反应类"特性（Parry、Protection、Counterspell 等）的模板。

**规则语义（SRD 5.2.1，2024 版）**：

- **触发条件**：当一个你能看见的攻击者用一次攻击检定命中你时；
- **效果**：你可以用你的反应将这次攻击对你造成的伤害减半（向下取整）；
- **消耗**：reaction（反应）；
- **限制**：游荡者 5 级特性；
- 规则原文出处：`.rulebooks/dnd5r/玩家手册2024/角色职业/游荡者/游荡者.htm` L138-139。

**明确不做（本切片裁剪，勿实现）**：

| 项 | 不做的原因 |
|---|---|
| "能看见的攻击者"视线检查 | 当前 `position_mode: "theater"` 无视线/遮蔽概念，默认能看见 |
| Evasion（反射闪避，7 级） | 需要豁免→伤害减半管线，独立切片 |
| 其他反应类特性（Parry、Protection 等） | 依赖本切片的泛化机制，后续切片逐个填充 |

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| **触发式机制现状**：`_hit_reaction_offer` 在攻击命中时调用，硬编码只检查法术（`_reaction_spell_choices` 遍历 `defender["spell_refs"]`） | `src/rulesets/dnd2024/combat/reaction_spells.py` L67-95 |
| **pending decision 机制**：`dnd2024.decision.pending` 事件创建，`dnd2024.decision.cleared` 事件移除；`decision.resolve` intent 路由到 `_decision_events` | `src/rulesets/dnd2024/combat/reducer.py` L276-285；`src/rulesets/dnd2024/combat/engine.py` L648-649 |
| **reaction 资源机制**：`combat["reactions"]` dict，`dnd2024.reaction.spent` 事件消耗；`_reaction_spell_choices` 检查 `combat.get("reactions", {}).get(defender["actor_id"], 0)` 和 `"reaction_blocked" in defender["conditions"]` | `src/rulesets/dnd2024/combat/reaction_spells.py` L41-45；`src/rulesets/dnd2024/combat/reducer.py` L96-102 |
| **决策结算路径**：`_decision_events` 中 `HIT_REACTION_DECISION` 分支调用 `_finish_hit_reaction` | `src/rulesets/dnd2024/combat/resolution.py` L682-683 |
| **validation 拦截**：如果有 pending reaction decision，只能提交 `decision.resolve` 或 `combat.message` | `src/rulesets/dnd2024/combat/validation.py` L211-212 |
| **feature_value 查表机制**：`feature_value(character, feature_id, key)` 从 parameters 读取，支持 `<key>_track` 自动查 progression 表 | `src/rulesets/dnd2024/features/resolver.py` L184-207 |
| **rogue progression 现状**：`features_by_level` 已含 `uncanny_dodge`（5 级），`tracks` 无 uncanny_dodge 相关（本特性无数值曲线） | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` L186-199 |
| **catalog 现状**：`class_feature_catalogs` 只有 monk/paladin/fighter/barbarian，无 rogue | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` L29 |
| **ACTION_KINDS 现状**：`{unarmed_strike, dash, dodge, disengage, heal, rage, rage_sustain}` | `src/rulesets/dnd2024/features/models.py` L26 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。`git status` 出现白名单外的任何变更即为失败。

### 3.1 `src/rulesets/dnd2024/features/models.py`

`ACTION_KINDS` 增加 `"uncanny_dodge"` 一个 kind。

### 3.2 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

新增 `"rogue"` 类条目（与 `"monk"`/`"paladin"`/`"fighter"`/`"barbarian"` 平级）：

```json
"rogue": {
  "source_ref": "srd-5.2.1:p61:rogue-features",
  "features": [
    {
      "id": "uncanny_dodge",
      "minimum_level": 5,
      "source_ref": "srd-5.2.1:p63:uncanny-dodge",
      "summary": "When an attacker that you can see hits you with an attack, you can use your Reaction to halve the attack's damage against you (rounded down).",
      "parameters": {
        "damage_multiplier": 0.5
      },
      "resource_id": "reaction",
      "capabilities": [
        {
          "id": "uncanny_dodge",
          "cost": {"reaction": 1},
          "requires_hostile_target": false,
          "actions": [{"kind": "uncanny_dodge", "count": 1}]
        }
      ]
    }
  ]
}
```

同步在顶层 `labels` 增加：`"rogue": "Rogue"`、`"uncanny_dodge": "Uncanny Dodge"`。

### 3.3 `src/rulesets/dnd2024/combat/reaction_spells.py`

**泛化触发式机制**，将 `_reaction_spell_choices` 扩展为同时返回法术和特性选项：

1. 新增 `_reaction_feature_choices(combat, defender)`：
   - 检查 `defender` 是否有 `uncanny_dodge` 特性（通过 `self.features.features_for(character)` 或 `feature_value`）；
   - 检查 reaction 资源是否可用（与 `_reaction_spell_choices` 相同的检查）；
   - 返回特性选项列表：`[{feature_id, name, mode: "reaction_defense", damage_multiplier: 0.5}]`。

2. 修改 `_hit_reaction_offer`：
   - 将 `_reaction_spell_choices` 的结果与 `_reaction_feature_choices` 的结果合并；
   - 在返回的 decision 中增加 `features` 字段（与 `spells` 字段并列）。

3. 修改 `_finish_hit_reaction`：
   - 处理 `option == "feature"`（或复用 `cast` 选项但检查选项类型）；
   - 如果是特性选项：消耗 reaction，将伤害减半（`damage // 2`），不重新检查命中（Uncanny Dodge 不改变命中）。

4. 修改 `_automatic_hit_reaction`：
   - 为 AI 托管的角色自动选择特性选项（如果有且伤害 > 0）。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

在 `_decision_events` 中：
- 如果 decision 包含 `features` 字段且 intent 选择了特性选项，路由到 `_finish_hit_reaction` 的特性处理分支。

### 3.5 `src/rulesets/dnd2024/combat/validation.py`

在 `_validate_hit_reaction` 中：
- 增加对特性选项的校验（`option == "feature"` 或检查 `feature_id`）。

### 3.6 `src/rulesets/dnd2024/combat/engine.py`

在 `_automatic_hit_reaction` 中：
- 为 AI 托管的角色自动选择特性选项（如果有且伤害 > 0）。

### 3.7 本地化（两个文件）

- `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`：加 `rogue` / `uncanny_dodge` 标签；
- `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`：加 `rogue: "游荡者"`、`uncanny_dodge: "直觉闪避"`。

### 3.8 测试（新文件 + 既有文件）

新增 `tests/rulesets/test_dnd2024_uncanny_dodge.py`（复用 `dnd2024_monk_common` helper），在 `tests/rulesets/test_dnd2024_class_features.py` 补 catalog 装载用例：

1. **装载**：rogue 的 `uncanny_dodge` capability 可解析；`damage_multiplier` 查表（固定 0.5）；zh-CN 标签（"直觉闪避"）；
2. **触发**：游荡者被攻击命中 → 出现 `HIT_REACTION_DECISION`，包含 `features` 字段；
3. **减半**：选择 `uncanny_dodge` → 消耗 reaction，伤害减半（向下取整），不重新检查命中；
4. **拒绝**：选择 `decline` → 不消耗 reaction，伤害正常；
5. **资源**：reaction 已消耗时 → 不出现 `uncanny_dodge` 选项；
6. **AI 托管**：AI 控制的游荡者被命中 → 自动选择 `uncanny_dodge`（如果有且伤害 > 0）；
7. **混合**：同时有 Shield 法术和 Uncanny Dodge → 两个选项都出现；
8. **等级**：4 级游荡者无此特性 → 不出现选项。

### 3.9 前端（v2 新增白名单：审核期决定）

v1 曾把前端列为禁止项。审核时确认：既有决策卡只渲染"施放法术"与"放弃"，人类游荡者没有任何按钮可使用直觉闪避（只有 AI 托管/GM 代选可用），特性对人类玩家等于不存在。故扩入以下三个文件：

- `frontend-v2/src/api/types.ts`：新增 `RulesetCombatReactionFeature` 接口（`feature_id` / `name` / `mode` / `damage_multiplier`）；
- `frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue`：新增 `reactionFeatures` computed；在 `hit_reaction_spell` 决策卡中按 `pendingDecision.features` 渲染按钮，点击提交 `{ option: "feature", feature_id }`；
- `frontend-v2/src/features/rulesets/dnd2024/combat/spellCopy.ts`：五个语种补 `useReactionFeature` 按钮文案，并把 `hitReactionHint` 由"施放法术"改为"施放法术或使用职业能力"。

验收补充：`cd frontend-v2; npm run build` 必须成功（构建产物 `static-v2/` 已被 git 忽略，不进入提交）。

## 4. 前置步骤（动代码之前）

```powershell
# 基线必须全绿，否则停下报告。已知豁免：test_dnd2024_character_builder.py 的 11 个上游既有失败
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_rage.py -q
```

## 5. 验收标准

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py -q   # 全绿
.venv\Scripts\python.exe -m pytest tests -q -k "uncanny"                              # 全绿
.venv\Scripts\python.exe -m pytest tests/rulesets -q                                  # 除已知豁免外全绿
git status   # 变更仅限白名单
```

## 6. 禁止事项

- 不改 `progression_catalog`（`uncanny_dodge` id 已在 5 级，无需改动）；
- 不改 `rest_catalog`（reaction 资源由战斗引擎管理，不在 rest_catalog 中）；
- 不改 `ACTION_COST_KINDS` / `EQUIPMENT_REQUIREMENT_KEYS` / `WEAPON_KINDS`；
- 不实现"明确不做"表中的任何项（视线检查、Evasion、其他反应特性）；
- 不改法术反应（Shield）的既有语义（只在泛化时扩展，不修改其逻辑）；
- 不加兼容层、回退、try/except 兜底；不为未来特性预留字段；
- 不新增第三方依赖；
- 不修改本任务书之外的任何文档。

## 7. 停止条件（触发即停，写入执行报告，不要猜）

1. §2 所列"已存在勿改"的 catalog 事实与实际不符（如 `uncanny_dodge` id 不在 5 级）；
2. `_hit_reaction_offer` 的返回结构无法扩展 `features` 字段而不破坏既有法术逻辑；
3. `_finish_hit_reaction` 的 cast/decline 分支无法泛化到特性选项而不引入重复代码；
4. `reaction` 资源的检查逻辑（`combat["reactions"]`）与特性选项的消耗逻辑冲突；
5. 基线测试非绿（已知豁免除外）；
6. 任何需要改动 `resolver.py` 公有契约、`view.py` 或 `frontend-v2/` 才能完成的情况。

## 8. 执行报告（完成后必须填写，仓库根 `EXECUTION_REPORT_SLICE3.md`）

```markdown
# 切片 3 执行报告
- 任务书版本：v1
- 分支：feat/uncanny-dodge
## changed-file list（与白名单逐项对照）
...
## 测试输出（完整粘贴）
...
## 停止条件触发情况（无则写"无"）
...
## 未完成项
...
```

注意：报告文件 `EXECUTION_REPORT_SLICE3.md` 是白名单唯一豁免项（临时产物，审核后删除）。

## 9. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-10-29 | 初版（依据规则书原文与源码勘察；触发式机制泛化设计） |
| v2 | 2026-10-29 | 审核期扩白名单：§3.9 前端三文件（决策卡无特性按钮，人类玩家无法使用）；§3.2 `resource_id: "reaction"` 与 resolver 校验冲突按执行端偏离 1 处理；§3.3.4/§3.6 `_automatic_hit_reaction` 归属更正为 reaction_spells.py |
