# ADR-0012: 玩法包边界

- Status: Accepted
- Date: 2026-10-23

## Context

[ADR-0010](0010-gal-first-non-trpg-gameplay.md) 决定 gal 是"组合，不是引擎分支"，P0 / P1 已合入上游。继续施工前盘点（2026-10-23，upstream/main @ v3.0.2）发现，按[轨道 P 计划](../GAL_TRACK_PLAN.md)原排法，gal 与跑团的差异会以分支形式落进跑团自己的模块：

- 玩法模式注册表（`PLAY_MODES` / `MODE_BOT_SURFACES`）放在 Adventure 进度模块 `adventure_runtime_state` 里；新增玩法要改冒险模块。
- `play_mode` 一个枚举混了两条轴：`free` / `adventure` 是"跑团是否绑定冒险包"，`gal` 是"玩哪种游戏"。直接后果是 gal 创建拒绝绑定冒险包，而 v1 路线恰恰要用 Adventure 图。
- 通用创建服务 `game_lifecycle.py` 内联了 `if play_mode == "gal"` 校验块，每加一种玩法多一段分支。
- 回合管线 `RoundProcessor.process_round_impl` 是跑团形状（检定规划、经济指纹、战斗叙事、死亡豁免）；原 P7 计划把 `scene` 模式作为 progression 内分支，按模式分叉回合状态机、经济纪元键与行动条数上限。
- 前端 `PlayView.vue`（约 2000 行、73 个 import）是整块跑团界面；原 P9 计划在其数据层上叠 gal 组件。

另有 `src/game_profiles/`（劫案 / 生存两个有界确定性 demo，`/games/{key}/profile` + `ProfileBoard`），自带推进与界面，不经过 `play_mode`。它证明了"非跑团玩法可以走独立推进与视图"，本 ADR 不改动它；日后可作为一种玩法包的驱动接入注册表，届时单独决策。

后续还会有经营等玩法。若每种玩法都以"在跑团模块里加一个分支"的方式接入，跑团模块会成为所有玩法的公共垃圾场，ADR-0005 的三层边界名存实亡。

## Decision

### 1. 一种玩法 = 一个玩法包

`src/gameplays/` 下每种玩法一个包（`trpg`、`gal/`，以后 `sim/` 等），以 `GameplayMode` 契约（`src/gameplays/contracts.py`）声明自身：模式 id、`bot_surface`、创建校验；后续步骤按需扩展推进驱动、prompt 组装、前端视图键等字段，扩展以实际消费方为准，不预先设计。

- 只有组合根 `src/gameplays/builtin.py` 可以 import 具体玩法包；generic path 只经注册表接触玩法，不出现具体玩法 id 分支。
- 玩法包之间互不 import。
- 非跑团玩法包不得依赖跑团专属机制（回合管线 `src.commands`、检定、战斗、经济、具体 ruleset）。需要共用的能力（例如叙事响应应用链）先抽到共享底座，再由各玩法调用。
- 共享底座保持一份：GameInstance 核心、存档与迁移、房间与权限、LLM / 记忆 / 世界书 / 内容契约、TTS / 生图。这与 ADR-0005、ADR-0010 一致，不拆仓库，不建第二套运行时。

`tests/architecture/test_gameplay_packages.py` 守卫以上规则。

### 2. 推进由玩法提供驱动，不在 progression 里分支

替代 ADR-0010 §4 的实施方式：引入推进驱动接缝，现有 `RoundProcessor` 原样登记为跑团的 `narrative_round` 驱动，gal 在自己的包里实现场景驱动，经营以后实现日历驱动。`narrative_round` 的跑团行为与代码路径不因其它玩法改变。`KNOWN_MODES` 可以增加新的推进模式值作为存储标识，但模式专属的状态机逻辑归各自驱动。

### 3. 玩法与"是否绑定冒险包"分轴（存储拆分推迟）

玩法与 Adventure 绑定是两个维度：Adventure 绑定由 `GameInstance.adventure_binding` 单一权威，每个玩法在自己的创建校验里决定能否 / 必须绑定。`free` / `adventure` 是跑团玩法下的两种绑定形态，不是两种游戏。

存储层暂不拆：`play_mode` 仍存于 `adventure_runtime` 槽并保留现值（`free` / `adventure` / `gal`），该槽只存 id，模式定义归注册表。解码时已知值原样保留，所以 `gal` 局绑定冒险图不需要迁移——gal 路线（P4 / P6）落地时只改 `src/gameplays/gal` 的创建校验。把玩法 id 迁入独立 `gameplay` 模块槽需要实例 schema 升级与旧存档映射，在出现确实需要它的消费方（例如第二个需要冒险绑定的非跑团玩法）时单独 PR 完成，映射规则写清楚，无法证明安全的转换 fail closed。

### 4. 前端按玩法分发视图

`/play` 入口按对局玩法从注册表选择视图组件。gal 呈现层在 `frontend-v2/src/features/gal/` 独立实现，`PlayView.vue` 不新增任何 gal 分支；共用 API client、时间线数据层、TTS、头像 / 立绘组件。创建页只负责选择玩法，玩法专属的配置步骤由各玩法提供。

## Alternatives Considered

| 方案 | 取舍 |
|---|---|
| 按原轨道 P 计划，在 progression / 回合管线 / PlayView 内按模式分支 | 单个玩法改动最少；每加一种玩法，跑团模块多一组分支，跑团回归面持续扩大。拒绝 |
| gal 独立仓库或独立 GameInstance | 隔离最干净；重复建设存档、权限、LLM 管线，ADR-0010 已拒绝 |
| 先设计完整玩法 DSL / 组合层 | 违背 ADR-0005"从真实玩法归纳"原则；契约字段随 gal 实际需要逐步增加 |

## Consequences

### Benefits

- 新玩法主要新增一个包和一次注册，跑团代码不因此改动。
- 跑团回归面固定，gal 的实验性改动不影响现有用户。
- 玩法边界由守卫测试执行，不依赖评审记忆。

### Costs / Risks

- 推进驱动接缝与前端分发是两次额外重构，需在 gal v1 功能 PR 之前完成。
- 玩法 / 冒险存储拆分推迟后，`play_mode` 值域里跑团的绑定形态与其它玩法并列，这是已知的命名债务，由注册表隔离其影响。
- 共享件的抽取（如叙事应用链）要求行为不变，验收是跑团全量测试全绿。

### Migration / Compatibility

- 注册表外移（本 ADR 首个 PR）：不改存档、API 与错误响应；`adventure_runtime_state` 删除 `PLAY_MODES` / `MODE_BOT_SURFACES` / `BOT_SURFACE_*` / `bot_surface()`，调用方改用 `src.gameplays`。
- 推进驱动（P0.5c）、前端分发（P0.5d）各自单独 PR，各自声明影响的契约；存储拆分（P0.5b）推迟，见 §3。

## Validation

- generic path 中具体玩法字面量 = 0，具体玩法包仅被组合根 import（守卫）。
- gal 包不依赖跑团专属机制（守卫）。
- 跑团 backend full pytest / 前端测试与基线失败集一致。

## Supersedes / Superseded By

修订 [ADR-0010](0010-gal-first-non-trpg-gameplay.md) §4 的实施方式（`scene` 模式归驱动而非 progression 分支）与 §2 中 gal 概念落点（新增"玩法包"）；ADR-0010 其余决策不变。
