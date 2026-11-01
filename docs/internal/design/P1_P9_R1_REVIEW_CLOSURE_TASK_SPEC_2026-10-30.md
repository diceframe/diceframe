# P1–P9 / R1 审查收尾修复任务书（2026-10-30）

## 1. 任务目标

关闭独立 post-implementation review 留下的三个问题：

1. 借机攻击窗口打开后，威胁者 reaction 变为 0 时，`resolve` 被 validation 提前拒绝，导致 resolution 已有的最终资格复查无法执行。
2. Revivify 前端把死亡 companion 显示为候选，但服务端只允许复活 player。
3. combat catalog 接受显式 `target_scope: null`，没有按“仅缺省或 `\"self\"`”的契约 fail closed。

本任务只做上述收尾，不扩展复活、反应、Self 目标或自动决策语义。当前源码是事实来源；原任务书用于确认契约：

- `docs/internal/design/P1_P9_R1_POST_REVIEW_REMEDIATION_TASK_SPEC_2026-10-30.md` §5.2、§6.1、§6.3。

## 2. 已确认的问题与预期行为

### 2.1 借机攻击 reaction=0

当前路径：

- `combat/validation.py` 的 `decision.resolve` 在 `option == "resolve"` 时检查威胁者 reaction，并在为 0 时抛出 `the threatening actor has no reaction available`。
- `combat/resolution.py` 的 `_decision_events` 已有最终 `_reaction_eligible` 复查，设计上应在资格失效时清除 decision、不攻击、不支付反应，并继续结算移动。

已用真实 engine 反例确认当前结果为：`ok: False`、opportunity pending 保留、移动位置不变。

预期行为：

- `resolve` 仍由被指派且有权限的提交者处理；decision、option、`expected_version` 等校验不放宽。
- 威胁者 reaction=0 时不发生借机攻击，不生成 `dnd2024.reaction.spent`。
- 原 opportunity decision 被清除，移动正常结算，reaction 保持 0。
- 开窗时的资格检查以及失能、`reaction_blocked`、HP 等最终复查保持现有行为。

### 2.2 Revivify 死亡 companion 候选

当前路径：

- `combat/view.py::_all_targets` 投影 player、companion、enemy，这是供多种法术共用的目标全集，行为正确。
- `Dnd2024CombatPanel.vue::targetsFor` 对 revive 只筛选 `allied(target) && target.is_dead === true`。
- `combat/validation.py` 已明确拒绝非 player 的 revive 目标。

预期行为：

- Revivify 只显示 `side` 为盟友、`kind === "player"` 且 `is_dead === true` 的目标。
- 死亡 companion 不显示；0 HP 但未 dead 的 player 不显示；死亡 player 正常显示并可提交。
- 不改变服务端 player-only 校验，不扩展 companion/enemy 复活，不从全局 target projection 删除 companion。

### 2.3 显式 `target_scope: null`

当前 `combat/catalog.py` 使用 `effect.get("target_scope")`，把字段缺省与显式 null 都当成未声明。

预期行为：

- 字段缺省：合法，保持原有目标语义。
- 字段为字符串 `"self"`：合法。
- 字段存在但为任何其他值，包括 `null`、空字符串、布尔值、数字和未来 scope：装载时抛出 `CombatCatalogError`。
- 不新增其他 scope，不改变 projection 或 validation 的 Self 机制。

## 3. 工作树与修改边界

本任务基于尚未提交的 P1–P9 / R1 remediation 工作树，不要求先清空或提交这些改动。开始前运行：

```powershell
git status --short
git diff --stat
```

保留并理解既有改动，不回退、不覆盖、不暂存其他任务内容。若白名单内已有无法归属的新改动，停止并报告。

### 3.1 允许修改的生产文件

- `src/rulesets/dnd2024/combat/validation.py`
- `src/rulesets/dnd2024/combat/catalog.py`
- `frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue`

### 3.2 允许修改的测试文件

- `tests/rulesets/test_dnd2024_spell_remediation.py`
- `frontend-v2/tests/Dnd2024CombatPanel.test.ts`

### 3.3 允许修改的文档

- 本任务书：仅追加执行报告、实际测试结果和 changed-file list。

除上述文件外不得修改、创建、删除或暂存任何文件。尤其不得为了收尾修改 `resolution.py`、`view.py`、reducer、P2P bridge、内容 JSON、route、service 或 generic engine。

## 4. 实施要求

按“先反例、后最小修复、再回归”的顺序执行。三个反例应先加入测试并在当前未修复代码上分别观察到预期失败，记录失败原因；不得用删除反例、放宽断言或伪造结果使测试通过。

### 4.1 修复借机攻击最终复查

在 `tests/rulesets/test_dnd2024_spell_remediation.py` 增加 reaction 耗尽的反例，可复用现有 opportunity 测试和 helpers：

1. 用 `new_combat()`、`_attack_first(instance)` 建立正常场景。
2. 提交 `distance: -15` 的移动并 `apply_batch`，确认产生 `opportunity_attack` pending。
3. 在 apply 之后，通过 `combat_of(instance)` 重新取得当前 canonical combat，将 `enemy:goblin-1` 的 reaction 设为 0。
4. 用 `resolve_decision(..., "resolve", rolls=[15, 4])` 提交决策。
5. 断言没有 `dnd2024.reaction.spent`，pending 已清空，`player:gm` 位置为 -15，威胁者 reaction 仍为 0。

注意：不得保存 apply 前的 combat dict 再修改它；`apply_batch` 会替换 canonical combat，修改旧快照会形成假反例。

生产修复位于 `validation.py`：删除或收窄 opportunity `resolve` 的 reaction 资源前置拒绝，使该场景进入 `resolution.py` 已有的 `_reaction_eligible` 最终复查。不要复制一套资格算法，不要把 reaction=0 自动改写为 decline，也不要修改 resolution 的事件语义。

### 4.2 修复 Revivify 前端候选

扩展 `frontend-v2/tests/Dnd2024CombatPanel.test.ts` 现有 `offers only dead allies for a revive spell` 场景，加入：

- 一个 `kind: "companion"`、`side: "party"`、`is_dead: true` 的死亡 companion。
- 保留现有死亡 player 和 0 HP 未死亡 player。

修复前测试应证明候选不只有 `player:fallen`；修复后仍断言只有一个 option，且值为 `player:fallen`。不要只断言提交时服务端报错，本任务是消除非法 UI 候选。

生产修复仅位于 `targetsFor` 的 revive 分支：在现有盟友和 `is_dead` 条件上增加 player kind 限制。其他 `target_side`、Self、多目标和 triggered target 分支保持不变。

### 4.3 修复 catalog null fail closed

把 `None` 加入 `test_catalog_rejects_an_unsupported_target_scope` 的参数列表。当前代码应因未抛出 `CombatCatalogError` 而失败。

生产修复位于 `catalog.py`：根据 `"target_scope" in effect` 区分字段缺省与显式值；字段存在时只接受严格的字符串 `"self"`。保持缺省与 Self 的现有合法路径，不修改内容文件和 projection。

## 5. 非目标与停止条件

不得在本任务中：

- 实现 companion 或 enemy 复活；
- 修改 Revivify 服务端目标范围、死亡判定或 reducer；
- 重构所有 decision validation，或改变 Counterspell、Shield、Uncanny Dodge、melee-hit decision 权限；
- 修改自动阶梯、hosted/AI 选择策略或新增自动反制；
- 新增事件、资源、字段、canonical ID、存档迁移或 API route；
- 做全仓库行尾 renormalize；
- commit、push 或暂存文件。

出现以下情况立即停止并报告，不自行扩权：

1. 必须修改白名单外文件才能实现上述预期行为。
2. 新反例无法在当前代码上按预期失败，或失败原因与审查结论不同。
3. 修复需要改变现有事件契约、权限、复活范围或 Self 语义。
4. 回归失败原因不明，或只能通过放宽既有断言使其通过。

## 6. 验收

### 6.1 后端定向测试

在仓库根目录运行：

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/rulesets/test_dnd2024_spell_remediation.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_revivify.py tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_misty_step.py -q
```

随后运行 ruleset 回归：

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py
```

### 6.2 前端验收

在 `frontend-v2` 下运行：

```powershell
npm test -- Dnd2024CombatPanel.test.ts peerGameBridge.test.ts
npm run typecheck
npm run lint
npm run build
```

若 lint/build 失败明确来自未修改的基线文件，记录准确错误并停止，不修改白名单外文件顺手修绿。

### 6.3 最终检查

```powershell
git diff --check
git status --short
git diff --stat
```

验收必须同时满足：

- 三个新反例均有修复前失败、修复后通过的记录。
- reaction=0 的 opportunity `resolve` 不攻击、清 pending 并结算移动。
- Revivify 只显示死亡 player 盟友，不显示死亡 companion 或 0 HP 未 dead 目标。
- 显式 `target_scope: null` 在 catalog 装载时被拒绝；缺省和 `"self"` 仍合法。
- changed-file list 严格符合 §3，无其他任务残留。
- 未执行 commit。

## 7. 执行报告格式

完成后在本节追加：

1. 每个反例的修复前失败原因与修复后结果。
2. 实际生产改动摘要，说明为什么不需要修改 `resolution.py` 和 `view.py`。
3. 每条验收命令的真实 passed/failed 或退出结果，不沿用历史数字。
4. 最终 changed-file list 与 `git diff --check` 结果。
5. 未执行项和残余风险；不得把未运行的检查写成通过。

## 8. 执行报告（2026-10-30）

### 8.1 反例修复前后对照

| 反例 | 修复前 | 修复后 |
| --- | --- | --- |
| `test_opportunity_attack_is_skipped_when_the_threat_has_no_reaction_left` | 失败：`the threatening actor has no reaction available` | 通过：无 `reaction.spent`、pending 清空、位置 -15、reaction 仍为 0 |
| `test_catalog_rejects_an_unsupported_target_scope[None]` | 失败：`DID NOT RAISE CombatCatalogError` | 通过 |
| `offers only dead allies for a revive spell`（新增死亡 companion） | 失败：候选 option 数为 2，期望 1 | 通过：仅有 `player:fallen` |

### 8.2 生产改动摘要

- `combat/validation.py`：删除 opportunity `resolve` 的 reaction 预算前置拒绝，改由 `resolution.py` 既有 `_reaction_eligible` 最终复查接管。`resolution.py` 无需改动，其清除决策、跳过攻击、继续移动的逻辑本来就正确。
- `combat/catalog.py`：改为 `"target_scope" in effect` 判断字段是否存在；存在且不为 `"self"` 即拒绝，显式 `null` 已 fail closed。
- `Dnd2024CombatPanel.vue`：Revivify 候选增加 `target.kind === 'player'`。`view.py` 的全局目标投影不改，其他法术仍需要 companion。

### 8.3 验收结果

1. 后端定向：
   `& ".venv\Scripts\python.exe" -m pytest tests/rulesets/test_dnd2024_spell_remediation.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_revivify.py tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_misty_step.py -q`
   → **80 passed**。
2. 前端定向：`npm test -- Dnd2024CombatPanel.test.ts peerGameBridge.test.ts` → **86 passed（2 files）**。
3. `npm run typecheck` → 退出码 0。
4. `npm run lint` → 退出码 0。
5. `npm run build` → 退出码 0；仅有基线既存的 lightningcss `@undefined` 与 chunk size 警告。
6. 全量 ruleset 回归（`tests/rulesets`，排除 `test_dnd2024_character_builder.py`）→ **718 passed, 39 deselected, 35 warnings in 2618.87s**，0 failed。较既有 716 增加 2 个，对应本条新增的反例。
7. `git diff --check`：仅有 LF/CRLF 提示，无空白错误。
8. `git status --short`：本任务改动仅涉及 §3 白名单文件；未执行 commit。

### 8.4 PR-1B 独立分支基线复验（2026-10-30）

§8.3 的数字是在含 1C（S-P1 / S-P7）的工作树上验收的。1B / 1C 拆分为两次 PR 时，本修复落在独立分支 `feat/dnd2024-p1-p9-r1-closure`（基线 `62b486a2`，不含 1C 的 improved critical 与 survivor 切片），在该基线上复验：

- 分支内容验证：`git diff 62b486a2..HEAD` 共 21 个文件（20 个修复文件 + 本任务书），零 1C 文件（SP1/SP7 任务书、`features/resolver.py`、`srd_class_features.json`、improved_critical / survivor 测试均不在分支上）。
- 提交拆分：`fix(dnd2024): harden reaction eligibility, self targeting and exploration casting`（remediation，20 文件）+ `fix(dnd2024): close post-review findings on reaction budget, revive candidates and target scope`（收尾，6 文件）。
- 纯 remediation 中间态定向复验：78 passed（= 收尾后 80 − 新增 2 反例），证明修复代码对 1C 无隐性依赖。
- 后端定向（§6.1 五文件）：80 passed。
- 全量 ruleset 回归：**660 passed, 39 deselected, 0 failed in 2808.16s（0:46:48）**——较含 1C 树的 718 恰差 58（improved_critical 23 + survivor 35），与剥离范围精确一致。
- 前端定向：86 passed（2 files）；`npm run typecheck` / `npm run lint` / `npm run build` 退出码 0（build 仅既存 lightningcss `@undefined` 与 chunk size 警告）。
- 行尾幻影文件（P2/P5/P6/P7/P8 任务书、`srd_spells.json`、`test_dnd2024_stoneskin.py`）经 stash 迁移后不再产生 diff，未进入任何提交。


### 8.5 未执行项与残余风险

- §8.3–§8.4 的全量数字均为 rulesets 目录范围；本次未重跑全仓库其他测试集（非本任务验收范围）。
- 借机攻击的最终资格复查仍依赖 `resolution.py` 的 `_reaction_eligible`；后续若再在 validation 前插入反应预算校验，可能重新截断该路径，需保持该契约。

