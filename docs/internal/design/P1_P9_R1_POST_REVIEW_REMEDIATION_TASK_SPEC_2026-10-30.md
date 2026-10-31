# P1–P9 / R1 提交后审查修复任务书

- 版本：v1.0
- 编制日期：2026-10-30
- 执行模型：dsv4.1f
- 类型：post-review remediation；不是新法术切片
- 编制时基线：`4e3751098b28`（短哈希）
- 执行方式：三个独立修复包，顺序 A → B → C；不得自行 commit

## 1. 任务目标

在不扩大 P1–P9 / R1 既定规则边界的前提下，修复提交后审查确认的以下问题：

1. Counterspell 在真实服务链路中被自动 `decline`，且人类 GM 没有完整决策入口。
2. Counterspell 与借机攻击遗漏失能/禁反应资格检查。
3. False Life 的 Self 目标被错误实现为盟友目标。
4. 新 spell mode 的 projection、Web 前端和 P2P intent 字段契约不完整，导致部分已实现法术无法由玩家合法提交。
5. 探索层允许死亡、0 HP 或失能角色施法，R1 因此允许死亡角色自我复活。
6. 临时 HP 全部吸收伤害时，0 HP 角色错误地不增加死亡豁免失败。

完成标准不是“补齐完整 2024 RAW”，而是：

- 本批已声明支持的能力可从真实玩家入口使用；
- 跨原语组合不产生已确认的错误结算；
- 未支持语义继续显式拒绝或保持已登记的 GM 裁定边界；
- Web、P2P、ApplicationAction 继续共用服务端权威 intent，不建立平行结算链。

## 2. 事实来源与阅读顺序

开工前必须阅读：

1. `docs/ARCHITECTURE_CN.md`
2. `docs/ENGINEERING_RULES.md`，尤其 §11–§17
3. `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md`
4. `docs/internal/design/P4_FALSE_LIFE_TASK_SPEC_2026-10-29.md`
5. `docs/internal/design/P7_COUNTERSPELL_TASK_SPEC_2026-10-29.md`
6. `docs/internal/design/P9_REVIVIFY_TASK_SPEC_2026-10-29.md`
7. `docs/internal/design/R1_REVIVE_EXPLORATION_TASK_SPEC_2026-10-30.md`
8. 本任务书全文

旧任务书中的“审核通过”是历史记录。发现旧口径与本次确认的规则修正冲突时，以本任务书 §6、§8 为准，并在旧任务书或本任务书执行报告中追加勘误，不得静默改写历史执行结果。

## 3. 基线与工作树闸门

### 3.1 开工检查

执行：

```powershell
git rev-parse --short HEAD
git status --short
git diff --check
```

要求：

- 编制时基线为 `4e3751098b28`。若执行时 HEAD 不同，先重读本任务书涉及的函数、测试和白名单；存在语义偏移时停止上报，不得机械套用。
- 工作树必须干净，或审核端已明确书面确认哪些既有改动可以保留。
- 若任何白名单内文件已有未提交改动，停止上报；不得自行丢弃、覆盖、reset、clean 或暂存这些改动。
- 当前编制时工作树存在空白/行尾类改动，因此执行模型必须先完成上述闸门，不得直接动代码。

### 3.2 白名单前置缺口检查

在写代码前逐项确认：

- 所有计划修改的生产代码和测试均在 §4 白名单内；
- 服务层 Counterspell 测试可复用现有 `ruleset_gameplay` / HTTP harness，不需要修改白名单外服务；
- 战斗法术 projection 的真实落点是 `combat/view.py`，不是 `runtime.py`；
- 前端类型落点是 `frontend-v2/src/api/types.ts`；
- P2P 还有独立的 `ruleset.intent` 字段白名单，必须同步 `resist_type` 与 `teleport_distance`；
- 不需要新增 reducer event、resource/economy 类型、route、locale canonical ID 或存档迁移。

若任一必须为通过验收而修改的文件不在白名单内，立即停止，报告“文件、最小改动、原因”，不得自行扩权。

## 4. 文件白名单

### 4.1 生产代码

允许按对应修复包修改：

- `src/rulesets/dnd2024/combat/engine.py`
- `src/rulesets/dnd2024/combat/reaction_spells.py`
- `src/rulesets/dnd2024/combat/resolution.py`
- `src/rulesets/dnd2024/combat/validation.py`
- `src/rulesets/dnd2024/combat/reducer.py`
- `src/rulesets/dnd2024/combat/view.py`
- `src/rulesets/dnd2024/combat/catalog.py`
- `src/rulesets/dnd2024/exploration/engine.py`
- `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`
- `frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue`
- `frontend-v2/src/api/types.ts`
- `frontend-v2/src/peer/game/bridge.ts`

明确不允许修改：

- `src/webui/services/ruleset_gameplay.py`：现有服务只负责调用自动阶梯；应通过 `next_automatic_intent` 的契约修复，不把规则分支堆进服务。
- `src/llm/combat_choice.py`、`combat/hosted_choices.py`：本轮不实现 AI 反制或 AI 法术选择策略。
- `src/rulesets/dnd2024/runtime.py`：本轮不修 recent feed 白名单。
- `exploration/revive_effects.py`：本轮不扩展复活家族语义。
- 任何 generic engine、generic reducer、AI 配置、经济模块、存档迁移或 1C S-P 文件。

### 4.2 测试

允许修改：

- `tests/rulesets/test_dnd2024_counterspell.py`
- `tests/rulesets/test_dnd2024_hold_person.py`
- `tests/rulesets/test_dnd2024_false_life.py`
- `tests/rulesets/test_dnd2024_exploration_revive.py`
- `frontend-v2/tests/Dnd2024CombatPanel.test.ts`
- `frontend-v2/tests/peerGameBridge.test.ts`

允许新增：

- `tests/rulesets/test_dnd2024_spell_remediation.py`

新测试文件用于 projection、真实 service 链路和跨修复包组合，避免把服务测试继续塞进单法术 engine 测试。若确实需要修改另一个既有测试文件，先按 §3.2 停止上报。

### 4.3 文档

允许修改：

- 本任务书：追写执行报告。
- `docs/internal/design/P4_FALSE_LIFE_TASK_SPEC_2026-10-29.md`：仅追加死亡豁免规则勘误，不改写旧执行报告。
- `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md`：仅追加本次 remediation 结果和后续登记，不重排 1C，不修改其它切片记录。

不得读取、修改、删除或暂存与本任务无关的 SP 后续任务书、竞品资料或其它工作区产物。

## 5. 修复包 A：反应资格与 Counterspell 决策链

### 5.1 人类 GM 的 pending 不得被自动消费

修改 `combat/engine.py` 的自动决策契约：

- `next_automatic_intent` 不得再为 `counter_spell` pending 自动生成通用 `decline`。
- 人类 GM 选择前，初始 `cast_spell` 批次应用后必须停止自动推进，pending 保持可处理。
- 不为 hosted/AI GM 新增 Counterspell 策略；既有 hosted 行为只能作为明确记录的自动选择，不得反向影响人类 GM。
- 不得通过跳过全部 pending、关闭敌方自动回合或修改 service 的方式规避问题。

同时修正 `available_intents` 的发现顺序：

- assigned GM 即使不是当前回合 player actor，也能获得自己应处理的 `decision.resolve`；
- 非 assigned actor 不能借该路径处理 decision；
- 不改变既有 hit reaction / melee hit decision 的优先级和权限。

### 5.2 统一反应资格

在 reaction owning module 中复用同一资格判定，至少覆盖：

- Counterspell 开窗；
- Counterspell `counter` 最终结算；
- 借机攻击开窗；
- 借机攻击 `resolve` 最终结算。

资格必须同时满足：

- reactor HP > 0；
- reaction 资源可用；
- `condition_flags(...)[incapacitated]` 为 false；
- 不存在 `reaction_blocked`。

最终结算必须复查，不得只依赖开窗时的旧快照。既有 Shield / Uncanny Dodge 行为不得回归；可复用其判定，但不要求为本任务重构所有反应类型。

### 5.3 前端 counter 入口

修改 `Dnd2024CombatPanel.vue`：

- 当 pending options 包含 `counter` 时，渲染真实 counter 操作；
- 提交 `decision.resolve`，option 为 `counter`；
- 保留 `decline`；
- 不伪造额外 spell slot、目标、伤害或反制结果；
- 不破坏 Shield、Uncanny Dodge、melee-hit 等既有 decision 卡片。

### 5.4 A 组必测矩阵

至少覆盖：

1. 普通威胁仍产生 Counterspell pending。
2. `next_automatic_intent` 不自动消费人类 GM 的 Counterspell pending。
3. 真实 `ruleset_gameplay.submit_intent` 施法后 pending 保持，未生成自动 decline 批次。
4. assigned GM 在非自身 player 回合仍能发现 decision。
5. 非 GM/非 assigned actor 不能处理。
6. 重复 decision、过期 `expected_version` 继续拒绝。
7. GM 选择 counter 与 decline 均保持 P7 原有经济和重放语义。
8. `paralyzed`、`stunned`、`unconscious` 敌人不能 Counterspell。
9. `reaction_blocked` 敌人不能 Counterspell。
10. 麻痹敌人不能获得或执行借机攻击。
11. `reaction_blocked` 敌人不能获得或执行借机攻击。
12. 正常敌人的 Counterspell 和借机攻击仍可用。
13. 前端 counter/decline 都提交正确 option。
14. 既有 Shield/Uncanny Dodge 测试继续通过。

## 6. 修复包 B：Self 目标与施法 projection/UI/P2P 契约

### 6.1 声明式 Self 目标

不要只把 `temp_hp` 永久写成 Self，也不要只在前端隐藏盟友。

采用最小声明式约束：

- combat spell effect 可增加可选 `target_scope: "self"`；本批只需要支持缺省与 `"self"`，不得预建其它未来值。
- `false_life` 与 `misty_step` 声明 `target_scope: "self"`。
- catalog 对非法 `target_scope` fail closed。
- validation 对任意带该声明的法术要求 `target_id == actor_id`；用统一约束替代 teleport 的重复特判。
- False Life 对盟友但非自己必须拒绝；自身继续正常结算。

### 6.2 服务端 projection 声明提交需求

扩展 `combat/view.py` 的 spell/target projection，只投影服务器已有 canonical 信息：

spell 行至少提供本批 UI 所需字段：

- `target_scope`（如声明）；
- `target_side`：`ally` / `enemy` / `any`，由服务端现有阵营语义生成；
- `target_count`、`upcast_target_count`、`repeat_targets`；
- `resist_choice`（如有）。

target 行至少提供服务端判定的 `is_dead`，供 Revivify 目标筛选：

- 不得让前端用 `hp === 0` 推断死亡；
- 0 HP 但仍在做死亡豁免的角色不等于 dead；
- 不新增客户端可写的权威状态。

同步 `frontend-v2/src/api/types.ts`，字段保持只读投影用途。

### 6.3 前端合法目标和参数

修改 `Dnd2024CombatPanel.vue`，以 projection 替代当前的 allied mode 硬编码：

- `target_scope: "self"`：目标固定为施法者；
- `target_side: "ally"`：只显示合法盟友；
- `target_side: "enemy"`：只显示合法敌方；
- `target_side: "any"`：允许双方，供 Dispel Magic 使用；
- Revivify 只显示 `is_dead` 的合法盟友；
- triggered damage 的固定命中目标逻辑保持不变。

提交契约：

- 单目标可继续 `target_id`；多目标使用 `target_ids`；不得同时发送互相矛盾的字段。
- P1/P2 按当前 slot 计算可选择目标上限；不得重复目标。
- P3 允许 1 到当前射线数的 target entries，并保留 `repeat_targets` 语义；不得借本任务改变“少选目标则少发射线”的已登记行为。
- `resist_choice` 存在时必须选择并提交 `resist_type`。
- teleport 必须输入并提交非零整数 `teleport_distance`；前端可做基本输入提示，最终范围与合法性仍由服务端判定。
- 不得在前端计算临时 HP、伤害、复活结果或条件效果。

### 6.4 P2P 字段透传

在 `frontend-v2/src/peer/game/bridge.ts` 的 `ruleset.intent` 白名单中加入：

- `resist_type`
- `teleport_distance`

不得加入 HP、伤害、condition、资源结果或其他客户端权威字段。`target_ids` 已存在，不得重复或放宽其它操作。

### 6.5 B 组必测矩阵

至少覆盖：

1. False Life 自身合法，盟友非自己和敌人均拒绝。
2. 非法 catalog `target_scope` 装载失败。
3. Misty Step Self 校验保持，teleport distance 规则不回归。
4. projection 正确声明 self/ally/enemy/any、目标数量、repeat 和 resist choices。
5. Revivify 目标投影区分 dead 与 0 HP 未 dead。
6. Web UI 可提交 False Life、Misty Step、Revivify 的合法 payload。
7. Web UI 可提交 Dispel Magic 盟友和敌方目标。
8. Web UI 可提交 `resist_type` 与 `teleport_distance`。
9. Web UI 可提交升环 Invisibility/Hold Person 多目标。
10. Web UI 可提交 Scorching Ray 多 target entries 和合法重复目标。
11. P2P 保留上述两个新字段，同时继续剥离伪造结果字段。
12. 既有 triggered damage、单目标治疗和探索施法 UI 测试不回归。

## 7. 修复包 C：探索施法资格与死亡豁免

### 7.1 探索层公共施法资格

在 `exploration/engine.py` 建立同一施法资格判定，并同时用于 `available_intents` 与 `_validate`：

施法者必须：

- HP > 0；
- 不存在 `dead`；
- `condition_flags(...)[incapacitated]` 为 false。

要求：

- dead、0 HP 未 dead、unconscious、paralyzed 等状态不能施法；
- 不得仅给 Revivify 加目标特判；公共探索施法都必须遵守；
- `reaction_blocked` 不应被错误解释为禁止普通施法；
- 非法施法不得扣法术位、不得产生部分事件；
- `available_intents` 不再向无资格角色提供施法入口。

本任务不新增探索层动作经济、回合制或视觉判定。

### 7.2 临时 HP 不吞掉 0 HP 死亡豁免失败

修改 `combat/reducer.py`：

- 在临时 HP 吸收前保存“0 HP 角色是否受到原始伤害”的事实；
- 临时 HP 全部吸收伤害时仍增加一次死亡豁免失败；
- 重击仍增加两次；
- 临时 HP 继续优先吸收，HP 保持 0；
- 非 0 HP 角色不因此新增死亡豁免逻辑；
- 正治疗和其它 resource.changed 行为不得改变。

P4 旧测试中“全部吸收不增加失败”的错误预期必须修正，并在 P4 任务书追加勘误。

### 7.3 C 组必测矩阵

至少覆盖：

1. dead 施法者不能对自己施放 Revivify。
2. dead 施法者不能对他人施放 Revivify。
3. 0 HP 但未标 dead 的施法者不能施法。
4. paralyzed/unconscious 施法者不能施法。
5. 上述拒绝均无 `spell.slot_spent`，canonical slots 不变。
6. 这些角色没有探索施法 available intent。
7. 正常角色及仅有 `reaction_blocked` 的正常角色仍可探索施法。
8. 0 HP + temp HP 全部吸收普通伤害，failures +1。
9. 同场景重击，failures +2。
10. 部分吸收和穿透伤害保持正确 HP/temp HP/failures。
11. 既有 Revivify、探索层高环法术拒绝、临时 HP keep-max/吸收测试继续通过。

## 8. 明确不在本任务内

不得为了“顺便完整”而实现或重构：

- Counterspell 视觉、遮蔽、视线系统；
- Revivify 一分钟窗口、死亡时刻、300 GP 钻石、老死、遗体部位、companion/enemy 复活；
- Raise Dead / Resurrection 惩罚、Reincarnate 角色重建、True Resurrection 唤名恢复；
- 高环复活法术的任何新实现子集；
- Scorching Ray 累计击杀的胜利事件时序重做；
- recent combat feed 的 `spell.countered` 白名单；
- hosted AI 自动选择反制、抗性、传送或多目标策略；
- 探索/战斗复活 reducer 抽象合并；
- `_spell_events` handler/registry 拆分；
- source-aware catalog、战棋层、独立战役日历、材料经济或长休衰减；
- 全仓库行尾 renormalize。

上述项目如执行中再次遇到，只记录，不扩大本任务。

## 9. 实施顺序与回归要求

### 9.1 每个修复包的工作顺序

1. 在干净基线上添加最小反例，确认新测试在修复前因预期原因失败。
2. 做最小生产代码修改，不先做大重构。
3. 跑本包定向测试。
4. 跑 P1–P9 / R1 相关回归。
5. 检查 diff 仅含本包归属改动，再进入下一包。

不得通过删除反例、放宽断言、伪造 catalog 状态或跳过真实 service/UI 路径使测试变绿。

### 9.2 后端定向验收

在仓库根目录使用项目环境：

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/rulesets/test_dnd2024_spell_remediation.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_exploration_revive.py -q
```

然后运行 P1–P9 / R1 相关测试，以及全量：

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

记录实际 passed/deselected/failed 数字，不沿用旧报告的 603、613 或其它历史数字。

### 9.3 前端验收

在 `frontend-v2` 下使用 npm 10：

```powershell
npm --version
npm test -- Dnd2024CombatPanel.test.ts peerGameBridge.test.ts
npm run typecheck
npm run lint
npm run build
```

若 lint/build 的失败明确来自未修改的基线文件，停止并报告，不得修改白名单外文件“顺手修绿”。

### 9.4 最终提交前检查

```powershell
git diff --check
git status --short
git diff --stat
```

要求：

- changed-file list 严格符合 §4；
- 无 1C、其它切片、缓存、测试临时文件或工作区残留；
- 无整文件行尾幻影差异；
- 新增反例有修复前失败、修复后通过的对照记录；
- 未执行 commit。

## 10. 停止条件

出现以下任一情况立即停止并上报：

1. 工作树闸门不通过，或白名单内已有无法归属的改动。
2. 必须修改白名单外文件才能完成某项验收。
3. 发现需要新增事件类型、resource/economy、route、迁移或 canonical ID。
4. 修复人类 GM pending 需要改变 hosted/AI 的产品语义，且无法保持现有行为。
5. 为避免回归而必须改变 P7 的 Counterspell 经济、P9/R1 的复活范围或 P3 的已登记目标数语义。
6. 发现旧存档真实损坏、权限绕过、跨 game 决策泄露或部分扣位/复活。
7. 测试失败原因不明，或只能通过放宽规则断言使其通过。
8. 发现自己正在实现 §8 的非目标。

## 11. 执行报告模板

将以下内容追写到本任务书末尾：

```markdown
## 执行报告（dsv4.1f，2026-10-30）

### 基线
- HEAD：
- 开工 git status：
- 白名单缺口检查：
- 既有工作树改动处置（仅记录，不擅自清理）：

### 修复包 A
- 修改文件与归属：
- 修复前失败反例：
- 实现摘要：
- 定向测试结果：

### 修复包 B
- 修改文件与归属：
- 修复前失败反例：
- projection / Web / P2P 契约摘要：
- 定向测试结果：

### 修复包 C
- 修改文件与归属：
- 修复前失败反例：
- 实现摘要：
- P4 规则勘误位置：
- 定向测试结果：

### 全量验收
- 后端命令与 passed/deselected/failed：
- 前端 npm 版本：
- 前端 test/typecheck/lint/build：
- git diff --check：

### 偏差与遗留
- 与白名单/任务书的偏差：
- 未处理且明确后置的项目：
- 新发现问题：

### 最终 changed-file list
- 

### 声明
- 未修改白名单外文件：是/否
- 未混入 1C 或其它任务产物：是/否
- 未执行 commit：是/否
```

## 执行报告（dsv4.1f，2026-10-30）

### 基线
- HEAD：`a32a8b83`（`docs: record S-P7 survivor review and execution report`）。与编制基线 `4e3751098b28` 不同；已按 §3.1 重读本任务书涉及的函数、测试与白名单，未发现阻止套用的语义偏移。
- 开工 git status：工作树已有 7 个行尾幻影文件（P2/P5/P6/P7/P8 任务书、`srd_spells.json`、`test_dnd2024_stoneskin.py`）；`git diff --ignore-all-space` 核实为零内容差异，未清理、未 staging、未 renormalize。另有本任务书自身未跟踪。
- 白名单缺口检查：所需生产/测试/文档落点全部在 §4 内；无需修改 `ruleset_gameplay.py`、`runtime.py`、`exploration/revive_effects.py` 或任何白名单外文件。
- 既有工作树改动处置：仅记录与核实，未丢弃、覆盖、reset、clean 或暂存。

### 修复包 A
- 修改文件与归属：`combat/engine.py`、`combat/reaction_spells.py`、`combat/resolution.py`、`combat/validation.py`、`Dnd2024CombatPanel.vue`、`test_dnd2024_spell_remediation.py`、`Dnd2024CombatPanel.test.ts`。
- 修复前失败反例：A 组 12 项在旧代码下全部失败（自动 decline、孤立 pending 不可见、失能/禁反应仍可反制或借机）。
- 实现摘要：`next_automatic_intent` 不再自动消费人类 GM 的 `counter_spell`；assigned GM 可发现决策；统一 `_reaction_eligible` 同时用于开窗与最终结算；前端提供 counter 按钮；非 hit 类 decision 归属改为 `assigned_to or gm_uid`。
- 定向测试结果：A 组 12 passed；counterspell + hold_person 30 passed；面板 46 passed（修复后）。

### 修复包 B
- 修改文件与归属：`combat/catalog.py`、`validation.py`、`view.py`、`srd_combat_core.json`、`frontend-v2/src/api/types.ts`、`peer/game/bridge.ts`、`Dnd2024CombatPanel.vue`、`test_dnd2024_spell_remediation.py`、`Dnd2024CombatPanel.test.ts`、`peerGameBridge.test.ts`。
- 修复前失败反例：B 组 11 项在暂存生产文件后全部失败（False Life 接受盟友、非法 `target_scope` 不抛错、projection 缺 `target_scope` / `is_dead`）；恢复后 11 passed。
- projection / Web / P2P 契约摘要：`target_scope: "self"` 缺失或 `"self"` 才合法，其它值 catalog fail closed；validation 统一要求 `target_id == actor_id`；spell 行投影 `target_side` / `target_count` / `upcast_target_count` / `repeat_targets` / `resist_choice`，target 行投影 `is_dead`；前端按投影选择目标并提交 `target_ids` / `resist_type` / `teleport_distance`；P2P 白名单仅新增这两个已声明字段，伪造 HP/伤害/condition 继续剥离。
- 定向测试结果：后端 remediation 23 passed；前端 `Dnd2024CombatPanel` 53 passed、`peerGameBridge` 33 passed。

### 修复包 C
- 修改文件与归属：`exploration/engine.py`、`combat/reducer.py`、`test_dnd2024_exploration_revive.py`、`test_dnd2024_false_life.py`。
- 修复前失败反例：暂存两个生产文件后，探索资格 8 项失败（dead 可自复活/复活他人、0 HP/失能仍可施法、仍提供施法入口）；死亡豁免 2 项失败（全吸收仍 `failures == 1` / 重击仍 `0`）。恢复后全部通过。
- 实现摘要：`_casting_eligible`（HP>0、非 `dead`、`condition_flags(incapacitated)` 为假）同时用于 `available_intents` 与 `_validate`；`reaction_blocked` 不禁止普通施法；拒绝发生在事件构造前。reducer 在临时 HP 吸收前保存 `raw_damage`，0 HP 全吸收仍 +1，重击 +2。
- P4 规则勘误位置：`docs/internal/design/P4_FALSE_LIFE_TASK_SPEC_2026-10-29.md` §10.6。
- 定向测试结果：exploration_revive 19 passed、false_life 15 passed、remediation 23 passed；§9.2 首条命令合计 87 passed。

### 全量验收
- 后端命令与 passed/deselected/failed：`.venv\Scripts\python.exe -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py` → **716 passed, 39 deselected, 0 failed, 35 warnings in 1905.43s**。
- 前端 npm 版本：`11.13.0`（环境实际版本，非任务书示例的 npm 10）。
- 前端 test/typecheck/lint/build：`npm test -- --run tests/Dnd2024CombatPanel.test.ts tests/peerGameBridge.test.ts` → 86 passed；`npm run typecheck` 通过；`npm run lint` 通过（`--max-warnings 0`）；`npm run build` 通过（仅有既有 CSS `@undefined` 与 chunk 体积警告）。
- git diff --check：无空白错误；仅 LF/CRLF 提示。

### 偏差与遗留
- 与白名单/任务书的偏差：HEAD 与编制基线不同（已重读核实）；npm 主版本为 11；无反例删除、断言放宽或伪造 catalog 状态。
- 未处理且明确后置的项目：§8 全部非目标继续登记（Counterspell 视觉/视线、复活家族扩展、hosted AI 策略、recent feed 白名单、全仓库行尾 renormalize 等）。
- 新发现问题：无超出 §8 的新问题；既有行尾幻影仍存在，未处理。

### 最终 changed-file list
- 生产：`src/rulesets/dnd2024/combat/{catalog,engine,reaction_spells,reducer,resolution,validation,view}.py`、`src/rulesets/dnd2024/exploration/engine.py`、`templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`、`frontend-v2/src/api/types.ts`、`frontend-v2/src/peer/game/bridge.ts`、`frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue`
- 测试：`tests/rulesets/test_dnd2024_spell_remediation.py`（新增）、`tests/rulesets/test_dnd2024_exploration_revive.py`、`tests/rulesets/test_dnd2024_false_life.py`、`frontend-v2/tests/Dnd2024CombatPanel.test.ts`、`frontend-v2/tests/peerGameBridge.test.ts`
- 文档：本任务书、`docs/internal/design/P4_FALSE_LIFE_TASK_SPEC_2026-10-29.md`（§10.6 勘误）、`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md`（remediation 记录）
- 既有行尾幻影（零内容差异，未纳入本任务改动）：`P2/P5/P6/P7/P8` 任务书、`srd_spells.json`、`test_dnd2024_stoneskin.py`

### 声明
- 未修改白名单外文件：是
- 未混入 1C 或其它任务产物：是
- 未执行 commit：是
