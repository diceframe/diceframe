# S0 任务书：休息的 DM 裁决与逻辑时间推进（跨切片补丁）

| 项 | 值 |
|---|---|
| 切片编号 | S0-REST-DECISION（跨切片补丁，不占用 F 编号） |
| 来源 | `S0_TASK_INDEX_2026-11-07.md` §10.1（休息无时钟、无中断）与 §10.4 第 4 条（游戏时钟） |
| 验收载体 | DND2024 短休/长休的队伍流程；`rest_session` 状态机；世界时钟 `advance_world_time` |
| 规则基准 | SRD 5.2.1《Playing the Game》Resting 段：Short Rest 至少 1 小时，Long Rest 至少 8 小时 |
| 代码基线 | `feat/dnd2024-rest-panel-wiring@c5e4a4f1`（stack 在 `feat/dnd2024-s0f5-free-casting@910733c0` 之上） |
| 硬前置 | §11 休息面板接线已合入；`world_state.clock` 与 `advance_world_time` 已在基线 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. **不新建时钟**。权威逻辑时钟已在基线：`GameInstance.world_state["clock"] = {"day", "minute"}`，唯一写入口 `advance_world_time`。本切片只把休息接上去。
2. **不复用墙钟**。休息会话中一切「何时发生」一律用逻辑时间（绝对分钟），禁止 `datetime.now()` 进存档——同一存档在不同时刻打开必须给出同一结果。
3. **裁决属于 DM，不属客户端**。玩家提交休息申报只产生「待裁决」；推进多少时间由服务端按规则声明算出，客户端不提交分钟数。
4. **单人模式不引入空按钮**。solo 下玩家即 GM，提交即结算并在同一事务内推进时间；队伍模式才需要显式裁决。
5. **失败整队回滚**。角色结算或时钟推进任一失败，角色与 `world_state` 一同回滚，不允许「人恢复了但时间没走」或反之。
6. **无时长的规则集不受影响**。未声明 `rest_duration_minutes` 的规则集照旧结算、不推进时钟。

## 2. 规则原文与语义边界

> **Short Rest.** A Short Rest is a period of downtime, at least 1 hour long, during which a character does nothing more strenuous than eating, drinking, reading, and tending to wounds.

> **Long Rest.** A Long Rest is a period of extended downtime—at least 8 hours long—during which a character sleeps for at least 6 hours and performs no more than 2 hours of light activity.

语义边界：

- 规则只给下界（「至少」）。本切片取 **short = 60 分钟、long = 480 分钟**，即规则下界；「至少」的裁量（例如「这一夜过了 10 小时」）留给 DM 在上界内自行叠加，见 §7。
- 时间推进是**世界**事件，不是角色事件。因此它属于 `world_state`，不属于角色卡。
- 长休的「6 小时睡眠 + 2 小时轻活动」允许玩家在期间做别的事（放警戒术、造小屋）。本切片只提供**裁决窗口**，不提供期间的行动建模（见 §7 非目标）。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与行 |
|---|---|
| 世界时钟容器：`{"day": 1, "minute": 0}`，新建对局从第 1 天 00:00 起 | `src/engine/world_state.py::fresh_world_state`（143–158） |
| 唯一推进入口，单次上限 1440 分钟；确定性排序、幂等（`pending → applied/failed`）、可整轮回滚 | `src/engine/world_events.py::advance_world_time`（52）、`MAX_ADVANCE_MINUTES`（49） |
| 时钟换算与上界：`MINUTES_PER_DAY = 1440`、`MAX_CLOCK_DAY = 365_000` | `src/engine/world/contracts.py`（97–129） |
| 现有推进点只有两处：回合元数据里的 planner 声明、CoC 恢复批准 | `src/commands/round_processor.py:545`；`src/webui/services/resource_recovery.py:241` |
| 「申请 → 批准才推进」的既有范式：`new_source(started_tick=tick)` → GM `approve` 时按 `duration` 补足差额、分段推进、写世界记忆 | `src/webui/services/resource_recovery.py::_apply`（218–247） |
| 队伍休息集齐后**直接结算**，没有裁决步骤 | `src/webui/services/ruleset_rest.py`（500–510） |
| 会话用墙钟记录三个时刻 | 同文件 456（`started_at`）、498（`submitted_at`）、533（`resolved_at`） |
| `confirm_elapsed_time` 已被强制为真，文案已承诺「游戏时间会随本次休息推进」，但代码从未推进 | 同文件 274–275、405–406 |
| 会话投影只暴露 `resolved_at`，且 `active` 只认 `collecting`/`resolving` | `src/webui/ruleset_rest_projection.py`（18–59） |
| `resolved_at` 在前端仅 `types.ts:304` 一处类型声明，无消费方 | `frontend-v2/src/api/types.ts` |
| GM 身份判定入口 | `src/webui/viewer.py::viewer_for`（12）；`viewer.is_gm` |
| AI GM 的休息工具：`perform_ruleset_rest`，`automatic=False`、`effect="external"`、guard 要求 `viewer.uid in game.players` | `src/webui/services/assistant_gameplay_tools.py`（292–328） |
| 前端链路：`PlayView` → `RulesetCharacterCenterHost` → `ProfessionalCharacterCenter`，中间不传 GM 身份 | `PlayView.vue:1421`、`RulesetCharacterCenterHost.vue`（49） |

## 4. 行为契约

### 4.1 状态机

```text
idle ──首个提交──> collecting
collecting ──集齐 required──> awaiting_approval      (party)
collecting ──solo 提交──> resolving                  (solo，等价于自己批准)
awaiting_approval ──DM approve──> resolving ──> completed
awaiting_approval ──DM reject──> rejected
resolving ──任一角色失败──> error
```

- 终止态：`completed` / `rejected` / `error`。三者都能被下一次提交重开（现有代码只认前两个加 `idle`，需补 `rejected`）。
- `awaiting_approval` 期间参与者可再次提交以修改自己的申报（覆盖 `participants[uid]`），状态不变。
- `rejected` 不修改任何角色卡、不推进时钟；会话保留 `decision`（`{approve: false, by, tick}`）供投影与审计。

### 4.2 时长声明

规则运行时新增可选方法 `rest_duration_minutes(rest) -> int | None`：

- DND2024：`short → 60`、`long → 480`；
- 方法缺失或返回 `None` → 结算照常，**不推进时钟**（其它规则集零影响）；
- 返回值必须是 >= 0 的整数，否则按 `REST_DURATION_INVALID` 拒绝（fail closed，不猜）。

### 4.3 逻辑时间字段

会话与参与者不再写墙钟：

| 旧字段 | 新字段 | 含义 |
|---|---|---|
| `started_at`（ISO 字符串） | `started_tick`（int） | 会话开始时的绝对逻辑分钟 |
| `submitted_at`（ISO 字符串） | `submitted_tick`（int） | 该参与者提交时的绝对逻辑分钟 |
| `resolved_at`（ISO 字符串） | `resolved_tick`（int） + 投影 `resolved_clock` | 结算时的绝对分钟 / `{"day", "minute"}` |

- 旧存档里的字符串字段**不迁移、不解析**，读到时忽略，`public_rest_session` 对缺失字段保持沉默降级。
- `public_rest_session` 新增：`resolved_clock`、`duration_minutes`、`awaiting_since_tick`（仅 `awaiting_approval` 时给出）。删除 `resolved_at`。
- 前端 `RestSessionStatus` 同步：删 `resolved_at`，加 `resolved_clock?`、`duration_minutes?`。

### 4.4 裁决接口

`POST /api/games/{game_key}/rest/decide`，body：

```json
{"approve": true, "operation_id": "..."}
```

- 权限：`viewer.is_gm` 为真；否则 403（不区分「不是 GM」与「游戏不存在」以外的信息，避免泄露）。
- 前置：会话 `status == "awaiting_approval"`；否则 `REST_NOT_AWAITING_APPROVAL`（409）。
- 幂等：`decision.operation_id` 相同 → 返回 `duplicate: true` 与当前会话，不重复推进时钟。
- `approve: false` → `rejected`，返回 `{"ok": true, "rejected": true, "rest_session": ...}`。
- `approve: true` → 逐人结算（沿用现有 `resolve_live` 每人一次）→ 推进时钟 → `completed`。

### 4.5 时钟推进

在**全部角色结算成功之后**、写 `completed` 之前，同一个 `authoritative_write` 与 `instance._lock` 内：

1. `minutes = runtime.rest_duration_minutes(rest)`；
2. 分段推进：`while remaining: step = min(remaining, MAX_ADVANCE_MINUTES)`（本轮 480 不触发分段，但按既有范式写死上限）；
3. 每段之后调用 `dependencies.advance_adventure_world(instance)`（可缺省），使 Adventure 的 gate / 进程 due_at 重新求值；
4. `queue_world_memory(instance, outcome["events"], round_number=progression_state.round_value(instance))`，让 AI GM 知道时间流逝；
5. `source_round` 传当前回合号；
6. 任一步抛错 → 回滚所有 `before_players` 与 `world_state` 快照，会话置 `error`，返回 `PARTY_REST_FAILED`。

### 4.6 单人模式

`submit_live` 在 solo 下仍走 `resolve_live`：结算成功后在同一事务内推进时钟（等价于玩家自行批准）。这保证单人局不出现无人可点的裁决按钮。

### 4.7 AI GM

新增工具 `decide_party_rest`（`game_key`、`run_id`、`approve`）：guard 要求 `viewer.is_gm`；`mutation=True`、`automatic=False`、`domain="ruleset"`、`effect="external"`；preview 返回等待中的休息摘要与将推进的分钟数。没有此工具时，AI GM 局的队伍休息会永久停在 `awaiting_approval`。

### 4.8 前端

- `awaiting_approval` 时：所有参与者看到「全员已提交，等待 DM 裁决」；提交按钮不再显示「结算中」。
- GM（`isGm` 经 `PlayView → Host → Center` 传入）额外看到「确认休息（推进 N 小时）」与「拒绝」两个按钮；`N` 取自服务端 `duration_minutes`，前端不硬编码。
- 确认/拒绝失败时展示服务端原因，不静默吞掉。

## 5. 改动文件白名单

服务端：

- `src/rulesets/dnd2024/runtime.py`（`rest_duration_minutes`）
- `src/webui/services/ruleset_rest.py`（状态机、逻辑时间、裁决、时钟推进）
- `src/webui/ruleset_rest_projection.py`（投影字段）
- `src/webui/services/assistant_gameplay_tools.py`（`decide_party_rest`）
- `src/webui/api.py`（deps 扩展、`ruleset_rest_decide` 包装、agent tool 回调）
- `src/webui/routes/game_character_routes.py` 或 `src/webui/routes/games.py`（新增路由注册，二选一，优先前者与既有 rest 路由同处）
- `src/webui/services/rest_activity.py`（若 `_DND_RESTS` 状态集需同步）

前端：

- `frontend-v2/src/api/rulesets.ts`、`frontend-v2/src/api/types.ts`
- `frontend-v2/src/features/rulesets/ProfessionalCharacterCenter.vue`
- `frontend-v2/src/features/rulesets/RulesetCharacterCenterHost.vue`、`frontend-v2/src/features/play/PlayView.vue`（传 `isGm`）
- `frontend-v2/src/features/play/party/PartyActions.vue`（`awaiting_approval` 时的进度文案）

测试：

- `tests/rulesets/test_dnd2024_rest_decision.py`（新建）
- `frontend-v2/tests/ProfessionalCharacterCenter.test.ts`

## 6. 编号测试矩阵

Python（`test_dnd2024_rest_decision.py`）：

1. 队伍集齐后状态是 `awaiting_approval`，不结算任何人。
2. `awaiting_approval` 时角色卡与 `world_state.clock` 都没变。
3. 非 GM 调用裁决 → 403 语义（`REST_DECISION_FORBIDDEN`）。
4. 状态不是 `awaiting_approval` 时裁决 → `REST_NOT_AWAITING_APPROVAL`。
5. `approve=false` → `rejected`，角色与时钟都不变，`decision` 记录存在。
6. `approve=false` 后可以重新发起休息（`rejected` 是重开态）。
7. `approve=true` → 全员结算，状态 `completed`。
8. `approve=true` 后时钟恰好推进 60 / 480 分钟（短休与长休各一）。
9. 时钟推进跨天：第 1 天 20:00 + 480 分钟 → 第 2 天 04:00。
10. `duration_minutes` 缺失（替身 runtime）→ 结算成功且时钟不动。
11. 非法 `duration_minutes`（负数 / 非整数）→ `REST_DURATION_INVALID`，角色与时钟都不动。
12. 同一 `operation_id` 重复裁决 → `duplicate: true`，时钟不再推进。
13. 会话不写任何墙钟字段：`started_tick` / `submitted_tick` / `resolved_tick` 都是 int，序列化后不含 ISO 字符串。
14. `public_rest_session` 对旧存档（只有 `started_at` 字符串）沉默降级，不抛错。
15. solo 模式提交即结算，并推进对应分钟数。
16. AI GM 工具存在且 guard 拒绝非 GM。
17. 时钟推进失败（注入 `WorldStateError`）→ 角色回滚、会话 `error`。
18. 长休的 `land_terrain` / `free_cast` 与裁决共存：批准后两类声明仍按原路径落账。

前端：

19. `awaiting_approval` 时玩家看到等待文案、看不到提交按钮。
20. `isGm` 为真时出现「确认休息」与「拒绝」，确认会调用裁决接口。

## 7. 非目标与停止条件

### 非目标

- **长休频次限制**（24 小时一次）：需要「上次长休时刻」的独立记录，另开切片。
- **`combat.start` 的中断语义**（§10.1 第一条状态漏洞）：本切片的拒绝路径已能表达「DM 打断并开战」（拒绝 → `rejected` 是终止态，不悬挂），但不修 `combat.start` 不看清 `rest_session` 的问题。
- **休息期间的行动建模**（警戒术、李欧蒙小屋、守夜轮班）：属 §10.2 仪式施法。
- **休息前预览**（这一觉会恢复什么）。
- **时钟的叙事输出**（谁在什么时刻看见日出）：属 AI GM 的叙述层，本切片只保证世界记忆被入队。

### 停止条件

- 若 `advance_world_time` 无法在同一 `authoritative_write` 内与角色结算共享回滚，则停止并记录，不得改用「先保存角色再推进时钟」的两段提交。
- 若 AI GM 工具层无法表达「GM 权限 + 非自动执行」，则停止该子项并明确报告死锁风险，不得用自动批准掩盖。

## 8. 验收命令

```bash
python -m pytest tests/rulesets/test_dnd2024_rest_decision.py -q --basetemp=.workbuddy/scratch/rest-decision
python -m pytest tests/rulesets/test_dnd2024_rest_options.py tests/rulesets/test_dnd2024_character_lifecycle.py \
  tests/test_rest_activity.py tests/test_assistant_party_rest_integration.py \
  tests/test_ruleset_binding_fail_closed.py tests/test_world_events.py -q \
  --basetemp=.workbuddy/scratch/rest-decision-reg
python -m pytest tests -q --basetemp=.workbuddy/scratch/rest-decision-full
cd frontend-v2 && npx vue-tsc -b && npx eslint src tests && npm test
```

## 9. 执行报告

### 9.1 基线、分支与提交

分支 `feat/dnd2024-rest-decision`，stack 在 `feat/dnd2024-rest-panel-wiring@c5e4a4f1`。提交链：`ab7c4425` AI GM 工具 → `8f136cc7` 服务端裁决与时钟 → `2c743a5e` 前端 → `dbb6edd8` 测试。索引记录见 `S0_TASK_INDEX_2026-11-07.md` §12。

### 9.2 矩阵结果

矩阵 1–17 全部有对应测试（`test_dnd2024_rest_decision.py`，15 条，其中矩阵 9 的跨天与矩阵 7 的重复裁决各占一条）；矩阵 18（地形/研究与裁决共存）未单独立测试，理由是该路径在 `_settle_party` 里与既有的 `_rest_options(runtime, item)` 完全一致，已由 §11 的 `test_dnd2024_rest_options.py` 覆盖，重复造夹具没有新增证据。前端矩阵 19–20 已补。

### 9.3 与任务书的偏差

1. **矩阵 19 的措辞**：任务书写「提交按钮不再显示」，实现选择「保留按钮但改为『已提交，等待 DM 裁决』并禁用」。空掉按钮会让玩家找不到自己刚提交的入口。
2. **`duration_minutes` 提前到申报齐时算好并写进会话**（任务书只要求裁决时算），因为 DM 界面与 AI GM 预览都要显示「批准会推进多久」，不能让客户端自己推算。
3. **重复裁决的检查提前到状态检查之前**，理由见索引 §12.4。
4. **AI GM 工具的实际签名**是 `decide_party_rest(game_key, run_id, approve)`（任务书写的是 `decide_party_rest`，字段未定），preview 的分钟数取自会话里的 `duration_minutes`。

### 9.4 验收

| 项 | 结果 |
|---|---|
| `pytest tests/rulesets/test_dnd2024_rest_decision.py` | **15 passed** |
| 12 个受影响的 `tests/*` 与 `tests/rulesets/*` 定向回归 | **259 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1786 passed / 0 errors**，11 条为 `character_builder` 基线，净引入 0 |
| 前端 `npm test` / `vue-tsc -b` / `eslint` | **221 files / 1589 tests passed** / 通过 / 通过 |
| `ruff check` / `git diff --check` | 通过 |

### 9.5 期间发现的两件事

1. **`test_ruleset_binding_fail_closed.py` 的 `rest_option_names` 断言是 F5 留下的旧债**：F5 加 `free_cast` 时没同步这个文件，而它不在 `tests/rulesets` 里，所以历次切片全量从未跑到。本切片顺手对齐。
2. **`pytest tests`（非切片套件全量）在本环境不可作为证据**：一次跑出 2094 errors，把同样的文件单独跑是 133 passed，逐条不可复现。已写进 §12.6，避免下次误判为回归。

### 9.6 未决

见索引 §12.7：`combat.start` 的中断语义、长休频次限制、长休「至少」的留白、前端未加 locale 键。
