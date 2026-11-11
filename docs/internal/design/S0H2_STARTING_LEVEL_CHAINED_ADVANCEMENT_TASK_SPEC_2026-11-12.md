# S0-H2 任务书：起始等级与开卡后连续升级

| 项 | 值 |
|---|---|
| 切片编号 | S0-H2 |
| 验收载体 | 玩家在建卡时可选目标等级（默认 1，现状不变）；提交后升级面板连续逐级升到目标等级；产出的 N 级卡带完整 `progression.history`，被持久化层 replay 接受 |
| 通用能力 | `Dnd2024AdvancementPanel` 链式模式；三条升级通道（本地 rules 端点 / 卡端点 / 不含 live）的逐级循环 |
| 规则基准 | 无新规则声明。SRD 5.2.1 的逐级成长（每级一次等级获取）就是本切片的形状；不引入「一次跳多级」规则 |
| 代码基线 | S0-H1 完成后的载体分支。硬前置：S0-A/#734（同文件 `progression/advancement.py`、`runtime.py`）、S0-H1（升级法术判定的正确性是链式升级每一级的前提） |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行 |

## 1. 方案裁定：逐次升级，不是塞进开卡窗口

两个候选方案：

- **A. 开 1 级卡，立刻接上升级窗口，逐级升到目标**（采用）；
- B. 把 1 到目标等级的所有升级选项塞进开卡窗口（否决）。

采用 A 的理由，每条都有代码事实支撑：

1. **`apply_next_level` 只能 +1 级**（`progression/advancement.py`，`from_level → target_level` 单步），方案 B 需要新建一个「一次吞 N 级」的引擎路径，与 replay 重放逻辑重复；
2. **replay 契约已经是「1 级卡 + N-1 条 history」**：`normalize_character_submission`（`runtime.py:742-813`）把提交卡降回 1 级重建、再逐级 `apply_next_level` 重放 `history`。方案 A 的产物天然就是这个形状，方案 B 还要额外合成同样的 history；
3. **前端不需要重做**：升级面板（`Dnd2024AdvancementPanel.vue`）已有完整的 preview → choices → apply 循环，方案 A 只是把它连续打开；方案 B 要把 N 级的选项混进建卡器的单页表单；
4. **用户裁定**：大多数电子游戏都选逐次升级。

## 2. 为什么这不是「建卡引擎」任务

开卡校验**继续硬限 1 级**（`character/validation.py:31-32` 的 `level != 1` 报错保持原样）。起始等级是「1 级卡 + 连续升级」的**流程**属性，不是建卡数据属性。这带来三个免费收益：

1. 建卡器、校验、derive/finalize 全部不动；
2. 产出卡的合法性由持久化层 replay 兜底——本地链式升级只是编辑期便利，**篡改 history 会在提交时 fail closed**（`runtime.py:778-779`：条数不符即拒；`:797-799`：to_level 不连续即拒）；
3. 外部导入卡缺 `history` 被拒的现状不变（`professional character advancement history is incomplete`），本切片不放松这条。

## 3. 已定点核实的代码事实

### 3.1 三条升级通道都已存在，H2 只做编排

| 通道 | 端点 | 契约 | 现有消费者 |
|---|---|---|---|
| 本地暂存（无持久化） | `POST /rules/{rule_id}/advancement/preview` / `apply`（`routes/rules.py:200-201`） | 收完整 character 提交，返回升级后的 character；无 revision、无 operation_id | `frontend-v2/src/api/rulesets.ts:88-110` |
| 卡 | `POST /character-cards/{card_id}/advancement/preview` / `apply`（`routes/character_cards.py:183-187`） | `expected_revision` + `operation_id` 幂等（`character_cards.py:72-95` 的 409/422 映射） | `rulesets.ts:112-139`、`CharactersView.vue:1034` |
| live（对局内角色） | `POST /games/{key}/character/{uid}/advancement/…`（`routes/games.py:251-255`） | 同卡契约 | `CharactersView.vue:1046` |

**live 通道不在本切片**：对局内角色升级是 GM 逐次操作，一次一级本来就是现状。

### 3.2 升级面板是单级的，applied 即关

`Dnd2024AdvancementPanel.vue`：`refresh()` 拉预览 → 玩家填 choices → `apply()`（:279）→ `emit('applied', response.character)`（:302）。宿主 `RulesetAdvancementHost.vue` 把 `applied` 透传，所有调用方（`CharactersView.vue:723 onCardAdvanced`、`:734 onLiveCharacterAdvanced`）收到后关闭并 reload。**没有任何循环结构**——升 5 级要点开 4 次对话框，每次重新找入口。

### 3.3 三个建卡入口的持久化时机不同，决定了链式升级的通道选择

| 入口 | 提交时机 | 链式升级通道 |
|---|---|---|
| `CreateView.vue:1205`（开局建角） | builder submit 后角色存本地数组，随开局一次性提交 | **rules 端点**（本地暂存，无 card_id 可用） |
| `JoinView.vue:509`（玩家加入） | builder submit → `POST /games/{key}/players`（:329-340） | **rules 端点**（同上，先升完再提交入局） |
| `CharactersView.vue:1082`（GM 卡库向导） | `onWizardSubmit`（:787）保存卡 | **卡端点**（先存 1 级卡拿到 card_id，再逐级 apply） |

`CreateView.vue:657-660` 现有文案「高级建卡器只负责创建 1 级角色；职业能力请在角色管理或对局内通过职业升级调整」——这正是本切片要替换掉的 UX 缺口，同时该文案揭示编辑既有卡的路径**不**做（见 §7 非目标）。

### 3.4 升级响应里已有前端续链需要的全部字段

apply 响应 `response.character` 是升级后的完整角色（`runtime.apply_advancement` → `_live_projection`，`runtime.py:834-843`）；卡端点返回体含新 `ruleset_revision`。链式循环只需：新 character 进面板 → preview 下一级 → apply，零新后端字段。

## 4. 精确行为契约

### 4.1 面板链式模式（提交 A）

`Dnd2024AdvancementPanel.vue` 新增 props：

```ts
targetLevel?: number   // 默认 0 = 单级模式，行为与现状逐字节一致
```

链式模式（`targetLevel >= 2` 且当前 `level < targetLevel`）：

1. 顶部显示进度「第 X 级 → 第 X+1 级（目标 N）」；
2. `apply()` 成功后：若新 `level < targetLevel`，**不 emit `applied`**，以返回的 character 为新输入立即 `refresh()` 下一级预览；若达到目标，emit 新事件 `chainCompleted: [character]`；
3. 逐级 revision 更新：卡模式下用 apply 响应的新 revision 替换 `revision` prop 的本地值（响应体已有）；
4. 单级模式（不传 `targetLevel`）保持现状：apply 成功即 emit `applied`。**不改既有事件语义**，三个现有调用方零改动。

`RulesetAdvancementHost.vue` 透传 `targetLevel` 与 `chainCompleted`。

### 4.2 本地暂存模式（提交 A 同步落）

面板在**既无 `cardId` 也无 `gameKey`/`userId`** 时走 rules 端点（`previewRulesetAdvancement` / `applyRulesetAdvancement`，`rulesets.ts:88-110`，两个函数已存在，当前无消费者）：

- 每级提交完整 character（上一级 apply 的返回值）；
- 无 revision/operation_id——rules 端点是无状态计算；
- **信任边界声明**：本地逐级升级不是权威。持久化时（开局提交 / 入局 POST / 卡保存）后端 `normalize_character_submission` 用 history 重放重算，任何前端篡改在该处 fail closed。因此本地模式不加任何额外校验层，重放就是校验。

### 4.3 建卡目标等级（提交 B）

`Dnd2024CharacterBuilder.vue` 的基础信息区新增「起始等级」选择：

- 取值 1–20，默认 1；**默认值时 UI 与现状完全一致**（不显示额外说明也不改变提交流程）；
- 选 >1 时显示一句提示：保存后将连续完成 2 级到目标级的升级选择；
- builder 的 `draft.level` **仍恒为 1**（§2），目标等级只作为流程参数随 submit 事件传出：`emit('submit', character, { targetLevel })`——事件签名扩展为可选第二参数，单参消费者不受影响；
- `RulesetExperienceHost.vue` 接收后：`targetLevel > 1` 时 builder 关闭后不关闭宿主，切换为内嵌 `Dnd2024AdvancementPanel`（本地暂存模式，`targetLevel` 传入）；`chainCompleted` 后才 emit 宿主的 `submit`（携带链终态 character）。`cancel` 语义：链式阶段取消 = 保留当前已升到的层级（对本地对象而言已是 N 级中间态），宿主以当前 character emit `submit`？——**否**：取消 = 放弃整个流程，emit `cancel`，与现状一致。已 apply 的层级只存在于本地对象，随放弃丢弃，无持久化残留。

### 4.4 卡库入口（提交 B）

`CharactersView.vue` 的 GM 卡库向导（`:1082` RulesetExperienceHost）：`targetLevel > 1` 时流程改为「保存 1 级卡 → 以新卡 card_id 打开 `RulesetAdvancementHost`（卡模式链式）→ 全部完成后再关闭」。中途取消：**已 apply 的级别不可回滚**（每级都是独立持久化事务），保留已升层级并提示玩家「已升至 X 级，剩余可在卡库继续」。文案进 `CharactersView` 现有 toast 通道。

### 4.5 开局提交的端到端契约（提交 C）

`CreateView` 开局随 game 创建提交 N 级角色（本地链式产物）时：

1. `progression.history` 恰有 N-1 条，`to_level` 严格 2..N（链式每级 apply 由引擎写入，`advancement.py:515-522`）；
2. 服务端 `normalize_character_submission` 重放成功，返回与提交等价的 canonical 角色；
3. `missing_choices`（G3）在链式逐级 apply 全部完成时返回空——这是链式流程的**隐含正确性**：每级都在面板里做完了该级选择。

### 4.6 错误与中断

| 场景 | 行为 |
|---|---|
| 某级 preview 返回缺项/422 | 面板照常显示该级缺项（既有错误渲染），玩家必须完成才能继续——**不跳级、不代填** |
| 某级 apply 409（卡 revision 过期） | 停链，显示既有 STALE_CHARACTER_REVISION 处理，玩家重开面板续升（`_record_advancement` 语义保证已完成级别不丢） |
| 链式阶段刷新页面（本地模式） | 本地对象丢失，等同放弃建卡，无服务端残留 |
| 目标等级 = 当前等级 | 链式模式退化为单级预览一闪后直接 `chainCompleted`？**否**：`targetLevel <= 当前 level` 时按单级模式处理（防御性归一） |

## 5. 改动文件白名单

**提交 A（面板链式 + 本地暂存模式）**

- `frontend-v2/src/features/rulesets/dnd2024/progression/Dnd2024AdvancementPanel.vue`
- `frontend-v2/src/features/rulesets/RulesetAdvancementHost.vue`
- `frontend-v2/src/features/rulesets/dnd2024/progression/` 下的面板测试（新建 `Dnd2024AdvancementPanel.spec.ts` 或项目现行前端测试形态；若仓库无前端单测基建，改为 `tests/rulesets/test_dnd2024_chained_advancement.py` 的 HTTP 层连续 apply 覆盖并在报告 §9.8 注明）
- `tests/rulesets/test_dnd2024_chained_advancement.py`（新建：HTTP 层，rules 端点连续 preview/apply 两级）

**提交 B（建卡目标等级 + 两入口接线）**

- `frontend-v2/src/features/rulesets/dnd2024/create/Dnd2024CharacterBuilder.vue`
- `frontend-v2/src/features/rulesets/RulesetExperienceHost.vue`
- `frontend-v2/src/features/admin/CharactersView.vue`（仅向导 targetLevel 接线与取消提示）
- `frontend-v2/src/features/player/JoinView.vue`（仅 `createProfessional` 前的链式接线，与 `:509` 的 props）
- `frontend-v2/src/features/create/CreateView.vue`（仅 `:1205` 接线与 `:657` 文案替换）

**提交 C（验收收口）**

- `tests/rulesets/test_dnd2024_starting_level.py`（新建：replay 端到端——1 级建卡 + 连续两级 apply 产物过 `normalize_character_submission`；外部导入缺 history 仍被拒的回归）
- 受影响的既有测试：`test_dnd2024_character_lifecycle.py`（若断言 builder 事件签名）

白名单外文件一律不动。需要动时必须先停下说明理由。

## 6. 编号测试矩阵

| # | 断言 | 提交 |
|---|---|---|
| 1 | 不传 `targetLevel` 的面板行为与改动前一致（单级，applied 即出） | A |
| 2 | `targetLevel = 3` 的本地模式：连续 apply 2 级、3 级后 emit `chainCompleted`，character.level = 3 | A |
| 3 | 链式产物 `progression.history` 恰 2 条且 `to_level` 为 2、3 | A |
| 4 | 本地模式走 rules 端点（请求 URL 断言），不带 revision/operation_id | A |
| 5 | 卡模式链式：每级 apply 携带上一级响应的新 revision | A |
| 6 | `targetLevel <= 当前 level` 时按单级模式处理 | A |
| 7 | rules 端点连续 preview→apply 两次（1→2→3），每次返回 `level` 递增且 history 增长 | A |
| 8 | builder 默认目标等级 = 1，提交事件与现状单参兼容 | B |
| 9 | 目标等级 3：CreateView 链式完成后本地数组里的角色为 3 级 | B |
| 10 | 目标等级 3：JoinView 在 POST players 前完成链式，服务端建出的 live 角色为 3 级 | B |
| 11 | 卡库向导目标等级 3：先产生 1 级卡，链式完成后卡为 3 级且 history 完整 | B |
| 12 | 卡库链式中途取消：保留已升层级，出现「已升至 X 级」提示 | B |
| 13 | 某级缺项未完成时无法进入下一级（apply 被缺项拦截，既有行为） | B |
| 14 | 链式产物（3 级）经 `normalize_character_submission` replay 成功且 `missing_choices` 为空 | C |
| 15 | 外部导入缺 history 的 3 级卡仍被拒（回归，文案 `advancement history is incomplete`） | C |
| 16 | 链式产物在开局提交后 `missing_choices` 为空、`revision`/`operation_id` 语义不变 | C |
| 17 | CreateView `:657` 文案不再声称「只负责创建 1 级角色」（目标等级 >1 时） | B |

## 7. 非目标与停止条件

### 非目标

1. **不做「编辑既有 1 级卡为目标等级」**。卡库已有的单级升级入口不动，玩家想要 3 级卡就在建卡时选 3。
2. **不动 live 通道**（对局内角色升级保持逐次）。
3. **不做「一次跳多级」的后端引擎**。`apply_next_level` 保持 +1 语义。
4. **不放松外部导入契约**。缺 `history` 的外部卡继续被拒；「导入无 history 模组卡」是另一个切片，需要的是导入期交互式补 history，不是放松校验。
5. **不改 validation 的 1 级硬限**（`validation.py:31-32`）。
6. **不做经验值/里程碑等升级触发条件**。何时升到目标等级由玩家在建卡时声明。

### 停止条件

1. 若链式逐级 apply 在某一级产生 `missing_choices` 非空（即某级选项在面板里做完了但检测仍报缺），停止——那是 G3 检测与升级引擎的口径差，必须先修口径，不允许在链式流程里绕过。
2. 若 rules 端点对本地 character 提交存在超出 replay 的隐式假设（如要求 rule_binding 已持久化），停止并上报，不在前端伪造字段。
3. 若 `RulesetExperienceHost` 的内嵌切换需要改动三个以上入口组件的结构（而非 props/事件接线），停止——说明宿主抽象不对，先回来改设计。
4. 若发现 builder 的 `draft.level` 被 derive/finalize 之外的路径消费（grep `draft.level`），停止并核实改动影响面。

## 8. 验收命令

```bash
/c/Python314/Scripts/ruff check src/
/c/Python314/Scripts/mypy

unset CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR CODEBUDDY_TOOL_CALL_ID
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets -q \
  -p no:cacheprovider -n 4 --dist loadfile --basetemp=C:/pt-h2
```

- A 完成后：`test_dnd2024_chained_advancement.py` + `test_dnd2024_progression.py`
- B 完成后：`test_dnd2024_character_lifecycle.py`（前端行为以矩阵 9–12 的人工/脚本走查记录进报告）
- C 完成后：`test_dnd2024_starting_level.py` + 全量
- 交付前必须同时满足：`ruff` 全过、`mypy` 无 issue、全量失败集合与基线逐条相同（Windows 本地基线见 S0-H1 §8，含 11 条 builder `ability:con` 失败与 docker/长路径项）。**只跑 `tests/rulesets` 不算回归**。
- 本切片前端为主：`vue-tsc` 不在门禁内（G3 §9.10 已划掉该项），但提交 B 改动的 `.vue` 文件必须通过 `frontend-v2` 现有构建（`npm run build`）零类型错误。

## 9. 执行报告（执行侧填写）

### 9.1 基线与提交

（待填）

### 9.2 面板链式模式的实现要点

（待填：状态机、revision 续传、本地模式的端点选择）

### 9.3 三个入口的接线方式与差异

（待填：CreateView / JoinView / CharactersView 各自的实际改动）

### 9.4 端到端 replay 验证

（待填：矩阵 14 的实际返回、history 形状）

### 9.5 前端测试形态说明

（待填：仓库前端单测基建现状与本次覆盖方式）

### 9.6 白名单对照

（待填）

### 9.7 矩阵 1–17 通过情况

（待填）

### 9.8 偏差、停止原因与未决事项

（待填）
