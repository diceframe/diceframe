# DiceFrame Architecture vNext 合并母方案

## 运行时拆能力 × 内容架构重构

> 基线：`diceframe/diceframe` `main` HEAD `5638c482`（Lorebook v2 #398 已合并）。
>
> 本文合并两份计划：
>
> 1. **运行时拆能力方案**：源自 [ADR-0005](adr/0005-composable-game-platform.md) 的方向讨论，目标是把 `GameInstance` 上的运行能力逐个拆成自持状态、迁移、投影与测试的模块，缩小每次改动的爆炸半径。
> 2. **[内容架构 vNext 母方案](CONTENT_ARCHITECTURE_VNEXT_MASTER_PLAN.md)**（World / Lorebook / Adventure / Module / Content Pack 全盘重构）：目标是把内容对象的 authority、ownership、reference 与生命周期一次定死，兼容只留在适配边界。
> 3. **[Native Module / Content Package 方向整理](NATIVE_MODULE_CONTENT_PACKAGE_DIRECTION.md)**：内容架构下的模组子方案，固定 Module 是分发容器而不是第四种 authority，给出 MODULE-1 到 MODULE-6 的施工顺序，并把 #400 置于 HOLD。
>
> 三份计划的原文分别保留；本文只定义它们的关系、共同原则、交点决议、合并后的实施顺序和完成标准。发生冲突时以本文为准，本文与源码冲突时以源码和 [ENGINEERING_RULES](ENGINEERING_RULES.md) 为准。

---

# 0. 一句话结论

两份计划改的是同一套系统的两个不同层，**大体正交，可以双轨并行**。

```text
内容定义层   World Definition / Lorebook + Binding / Adventure / Content Package
             —— 内容架构 vNext 负责：内容是什么、谁拥有谁、谁引用谁、怎么导入导出

运行时底座   GameInstance 拆出的模块：控制权 / 经济 / 战斗 / 行动闸门 / 推进 / 信息视图 / WorldState
             —— 运行时拆能力负责：一局游戏怎样可靠运行、运行状态归谁

具体玩法     跑团（现有）；狼人杀、经营等（未来）
             —— 由 ADR-0005 的组合层承接，本文不启动
```

两层唯一的硬接口是**内容如何进入一局游戏**：Binding 解析出的内容投影，被运行时的信息视图消费。这也是两份计划真正需要对齐顺序的地方。

---

# 1. 两份计划的关系

## 1.1 各自回答的问题

| | 运行时拆能力 | 内容架构 vNext |
|---|---|---|
| 核心对象 | `GameInstance` 及其 104 个字段 | World / Lorebook / Binding / Adventure / Package |
| 权威类型 | 一局游戏的运行状态 | 可复用的静态内容定义 |
| 主要痛点 | 单一 god aggregate；三条行动路径各查各的；整数回合是唯一节奏 | `World = Lorebook` 旧语义渗透；开局靠复制 World；多套导入落地逻辑 |
| 交付形式 | 每个模块一个 PR，跑团行为不变 | PR A–I，按 authority 而不是按页面拆 |
| 存储 | `data/saves/*/state.json`，实例 schema 16 | `data/lorebook.db`，lorebook schema 6；world template schema |

## 1.2 已经一致的地方

两份计划独立得出了同样的判断，这些直接作为合并后的公理：

- **World Definition 与 WorldState 彻底分名。** 内容方案 §6 与 ADR-0005 "状态与事实" 一节说的是同一件事：静态内容不覆盖当前游戏事实；Adventure 的 `world_seed` 经 materialization 变成 World Ops 写入 WorldState，而不是修改 World 定义。
- **兼容只存在于适配与迁移边界。** 内容方案 §13 与 ENGINEERING_RULES §6 一致；运行时方案对存档迁移的要求相同。
- **架构守卫比文档重要。** 两边都要求在 `tests/architecture/` 加 AST 级守卫。
- **不做一次性重写。** 内容方案"按 authority 拆 PR"，运行时方案"一次只拆一个能力"，都是 strangler 路线。
- **api.py 不能再长。** 运行时方案把它作为完成标准；内容方案的新 API 全部走 canonical service。

## 1.3 交点

| 交点 | 运行时侧 | 内容侧 | 风险 |
|---|---|---|---|
| 参与者信息视图 | 步骤 R6：席位级可见性 | PR D：`ContentProjectionService.for_character` + Binding resolver | 各自实现一套"这个席位能看到什么" |
| 开局流程 | `GameInstance` 上的内容绑定字段 | PR E：删除 `source_world_id` / `blank_lorebook` / `copy_lorebook_entries`，改为 World ref + Book bindings | PR E 往 `GameInstance` 加新的顶层字段，与拆模块方向相反 |
| `round_processor` / `round_actions` | 步骤 R5 改推进模型 | PR D 把 `round_actions.py` 里 `initialize_puzzles_from_lorebook` 的 `list_entries(world_id)` 换成投影服务 | 仅一处调用重叠，后合并者 rebase 即可，不构成依赖 |
| 世界书运行时状态 | `lorebook_timed_state` 挂在 `GameInstance` 上，#398 改了形状但没升实例 schema | 内容方案未涉及 | 先例问题：模块状态不走版本迁移 |
| 架构守卫 | 模块间不得读写彼此私有状态 | 禁止新业务调用 `list_entries(world_id)` 等七条 | 两套守卫文件风格不一 |
| Schema 版本 | 每个模块自己升实例 schema | lorebook DB v7+、world template v3 | 一个 PR 同时升两边版本 |
| Game 直接引用模组内容 | `content_binding` 模块槽里的 ref 形状 | 模组方案 §9、§24：Game 可直接引用 module Adventure / World / Book，卸载前检查 active references | 槽里只存裸 id，卸载模组后存档静默损坏 |
| 规则内容归属 | Ruleset Runtime 的 catalog 契约 | 模组方案 §14：法术、职业、怪物定义进 Ruleset Catalog 而不是 Lorebook | 当前只有 D&D 2024 注册了 catalog loader，CoC / Freeform 没有落点 |

---

# 2. 合并后的共同原则

以下原则对两条轨道同时生效。

1. **同一份事实只有一个权威负责模块。** 内容侧：Entry 的 owner 只有 `book_id`；Primary 关系以 Binding 为 authority，World API 只投影 `primary_lorebook_id`。运行时侧：交易和建造都通过经济模块扣款，不各自维护余额。
2. **每个模块只有一个写入口。** WorldState 的 `apply_world_ops` 是现成模板；新拆出的运行时模块和 canonical content service 都照这个模式。
3. **模块必须自持五件东西**：状态、编码/解码、迁移、投影、测试。拆出去之后 `GameInstance` 只保留一个按模块名索引的状态槽。
4. **兼容只在边界。** 允许残留的位置只有 legacy adapter、DB migration、legacy route facade、legacy fixture；每处残留带 `LEGACY_BOUNDARY` 标记和删除条件。
5. **Schema 升级各归各。** 一个 PR 只升一种 schema（实例 / lorebook DB / world template / 模块内 schema），并在 PR 描述里写明影响的存档、identity、API 与拒绝策略。
6. **守卫先于功能。** 每条轨道的第一个 PR 都是契约加守卫，不改产品行为。
7. **api.py 只减不增。** 新增 API 表面进入 `src/webui/services/*.py`；#398 加进 api.py 的 160 行世界书转发在内容轨道 PR A 或 B 中迁到 `services/lorebooks.py`。
8. **投影不是第二 authority。** `ContentProjectionService` 和运行时各模块的投影都只读，不复制数据。
9. **组合层不启动。** 机制模块契约、组合定义、DSL、可视化编辑器，等第一个非跑团玩法立项后再从已拆出的模块归纳，ADR-0005 的这部分保持 Proposed。
10. **一个 PR 只做一件事。** 投影替换 PR 不改流程；拆模块 PR 不改玩法；页面 PR 不改 ownership。内容方案 §40 列出的六种"假重构"同样禁止。

---

# 3. 交点决议

## 3.1 信息视图：内容投影是运行时视图的一半

- `src/knowledge/visibility.py` 继续是唯一的可见性谓词，两条轨道都不得复制。
- 内容轨道 PR D 交付 `ContentProjectionService.for_world_authoring / for_game / for_character`，内部只调用 Binding resolver、`list_book_entries` 和可见性谓词。
- 运行时步骤 R6 的"参与者信息视图"定义为：**权威状态投影 + 内容投影 + 私有频道**三部分的组合。内容部分直接消费 PR D 的服务，不重做。
- 现有 `public | gm` 两档可见性在 R6 扩为席位级时，Lorebook 的 `visible_to` 与 Binding 的 `character` scope 是现成输入，R6 不新增第二套席位标识。
- 顺序：R6 拆为两步。**R6-a**（权威状态投影 + 私有频道的席位级隔离）不依赖 PR D，跟随 R5；**R6-b**（内容投影接入）在 PR D 之后。

## 3.2 开局流程：内容绑定进模块槽

- PR E 不得往 `GameInstance` 新增顶层字段。Game 的 `world_ref`、game-scoped Book bindings、`adventure_binding` 进入一个名为 `content_binding` 的模块状态槽，自带 codec 与迁移。
- 因此运行时步骤 **R0（模块状态槽与迁移模板）先于 PR E。** R0 的验收样例就用世界书运行时状态：把 `lorebook_timed_state` 迁入模块槽，并补上 #398 欠下的实例 schema 升级（16 → 17）。
- 现有 `adventure_binding` 字段在 PR E 中一并迁入模块槽；ADR-0001 关于 run identity 的规则不变。

## 3.3 `round_processor` 与 `round_actions`：先投影后流程

- PR D 对这两个文件的改动限定为：把 `list_entries(instance.world_id)` 换成投影服务调用，不改行动收集、检定、叙述流程。
- 运行时 R5（推进模型）与 PR D 在 `round_processor.py` 只有谜题初始化那一行调用重叠，**不互相等待**；谁后合并谁 rebase。
- R1、R2、R3、R4 不碰这些文件，可以与 PR A–D 完全并行。R4 改的是 `turns.py`、`ai_player.py`、`ruleset_gameplay.py`，与 PR D 无共享文件。

## 3.4 架构守卫：一处维护

- 两条轨道的守卫都放在 `tests/architecture/`，各自一个文件：`test_content_authority.py`（内容方案 §38 的七条）和 `test_runtime_modules.py`（模块间不读写私有状态、`GameInstance` 不新增顶层字段、模块状态必须带 schema）。
- 现有 `test_dependencies.py` 的依赖方向规则继续有效，新增规则不得放松已有规则。

## 3.5 Schema 与迁移

| 轨道 | 版本 | 规则 |
|---|---|---|
| 实例存档 | `CURRENT_INSTANCE_SCHEMA_VERSION` 16 → 每拆一个模块 +1 | 顺序迁移，deepcopy，未来版本拒绝 |
| 模块内 schema | 每个模块自带 `schema_version` | 模块自己迁，实例 schema 只记录模块槽存在 |
| lorebook DB | 6 → PR B 起逐步升 | 迁移前做 invariant 检查，不满足生成报告，不静默修 |
| world template | 2 → 3（PR C） | 旧 template 只经 LegacyWorldAdapter 进入 |

同一个 PR 不得同时推进两行。

版本号不预留。谁真正改变 `GameInstance` 的持久化形状，谁就在实际父分支的当前版本上 +1；并行 PR 后合并的一方必须 rebase，并重新编号迁移函数、`CURRENT_INSTANCE_SCHEMA_VERSION`、文档和测试。PR E 只有真的往存档里加入 `content_binding` 才 +1。

## 3.6 ADR 的处理

- ADR-0005 拆成两部分：拆能力部分改为 Accepted，范围即本文轨道 R；组合层部分保持 Proposed，触发条件是第一个非跑团玩法立项。
- 内容架构 vNext 的 authority 模型（内容方案 §3–§8、§29、§41）另写 ADR-0006，与 PR A 同步 Accepted。
- ADR-0003 的整数回合作为经济回滚纪元键，在 R2 中换成抽象纪元键；R2 合并时给 ADR-0003 加 superseded 注记。

## 3.7 客户端离线内容：移动端不是纯服务器附属，但也不带 core

移动端（`diceframe-mobile`，Expo / React Native，约 43k 行）当前是纯瘦客户端：所有功能直接调用服务端 HTTP，本地只持久化设置，角色卡编辑也是服务端 CRUD。本节定义它在 vNext 之后的位置。

**两类数据，两种答案。**

| 数据 | 权威 | 客户端能否离线拥有 |
|---|---|---|
| 一局游戏的运行状态：HP、余额、回合、WorldState、经济、存档 | 服务端 `GameInstance` 及轨道 R 拆出的模块 | 否 |
| 内容对象：角色卡、Lorebook、World 定义、Adventure 草稿 | 内容层，vNext 定义为可移植对象 | 是 |

**不做的：客户端携带运行时底座。** 底座是 Python，Expo 没有运行时；用 TS 重写规则与结算就是第二套权威，ENGINEERING_RULES §11、§14 和架构文档的 Authority Model 都禁止。手机单机跑团只有把底座重写为 Rust / WASM 一条路，那是独立项目，不在本文范围。

**做的：内容层离线化，复用轨道 C 的协议。** ContentDraft、CommitPlan、ContentRef、provenance 与导入导出对称，本来是为插件和外部格式设计的，同时也是客户端离线内容的同步协议：

- 客户端本地存储的就是可移植格式文件：角色卡 v3、Lorebook v3、World schema v3。不另设移动端专有格式。
- 客户端把内容推到任一服务器时走 import preview → commit；`source_kind = device` 是一等来源，provenance 区分同一张卡在不同服务器上的副本。
- 服务端返回的 canonical id 与设备本地 id 分离，规则同内容方案 §32。

**建卡的分界：离线草稿，在线定稿。**

- 自由规则与 CoC 的角色卡是 JSON 人设加数值，可完全离线编辑。
- D&D 建卡依赖 Ruleset Runtime 的 `builder_choices / validate_character / derive_character / finalize_character` 与职业、法术目录。客户端缓存目录快照做选择流程与表单级校验，提交草稿后由服务端 finalize 派生数值；客户端可显示预览，但不作权威。现有 `fetchCharacterSchema` 已是这条路的一半。

**对两条轨道的约束。**

- PR A：ContentKind registry 中每种 kind 的 portable schema 必须可由客户端独立读写，不依赖服务端才能解析。
- PR G：导入导出把 `device` 作为一等 `source_kind`；导入预览对"同一 external_id 已存在"给出更新 / 另存 / 跳过三选一，而不是一律新建。
- 轨道 R 不受影响。

**顺序。** 移动端的本地内容库（expo-sqlite 或文件）与同步流程在 PR A、PR G 合并后开工，估计两到四周。移动端 `docs/mobile-feature-gaps.md` 中"角色卡库导入导出"与"规则建卡"两项归入此处，不再作为单独功能补齐。

---

# 4. 合并后的实施顺序

## 4.1 两条轨道

**轨道 R：运行时拆能力**

| 步骤 | 内容 | 依赖 | 影响文件（主要） |
|---|---|---|---|
| R0 | 模块状态槽、codec、迁移模板、守卫；样例：`lorebook_timed_state` 入槽，实例 schema 17 | 无 | `engine/game_instance.py`、`engine/game_state_codec.py`、`migrations/instance.py`、`tests/architecture/` |
| R1 | 控制权模块：`players[uid].control` 与 `away_control_policy` 迁入槽，单写入口 | R0 | `engine/player_control.py`、`webui/services/game_controls.py` |
| R2 | 经济模块：迁入槽；回滚纪元键从 `round_number` 抽象为 `era_key` | R0 | `engine/economy.py`、`engine/currency/`、`commands/economy_effects.py` |
| R3 | 通用战斗扩展模块：补齐状态槽与投影 | R0 | `engine/combat_*`、`webui/services/combat_extension.py` |
| R4 | 统一行动闸门：真人自由文本、AI 席位、结构化意图三条路径共用一组检查 | R0–R3 | `webui/services/turns.py`、`commands/ai_player.py`、`webui/services/ruleset_gameplay.py` |
| R5 | 推进模型：回合号单一写入者与 `progression` 模块槽（带推进模式，当前仅 narrative_round）；新模式留到第一个非跑团玩法 | R2、R4 | `engine/progression.py`（新）、`engine/modules/progression_state.py`（新）、六个写入点所在文件、`migrations/instance.py` |
| R6 | 参与者信息视图：R6-a 权威状态投影与私有频道的席位级隔离；R6-b 接入内容投影 | R6-a：R5；R6-b：R6-a 与 **PR D** | `engine/world/read.py`、`llm/context_builder.py`、`webui/services/game_queries.py` |
| R7 | 低风险字段组迁入模块槽：`private_channels`、`media`、`health`、`narrative_notes`、`round_presentation`、`world_reports`、`table_settings`、`room_access`（一组一个 PR） | R0 | `engine/game_instance.py`、`engine/modules/`、`migrations/instance.py` |
| R8 | 剩余字段按真实 owner 分组迁入模块槽：`session_stats`；`checks`（`last_check(s)`、`round_checks_prepared`、`manual_roll_requests`）；`round_safety`（两个回合快照、`death_save_outcomes`）；`legacy_combat`（旧版战斗五字段，只是兼容投影的存储，D&D 战斗权威仍在 `ruleset_state["combat"]`）；`ruleset_runtime`（binding + state + `event_ledger`）。做完这些先停 | R7 | 同 R7，另含 `engine/instance_lifecycle.py`；reset 逐字段保持现状 |

R8 之后暂缓的字段，等 PR D、PR E 合并并 rebase 后再设计：`scene`；`adventure_progress`（将来归 `adventure_runtime` 模块，不放进 `content_binding`）；`play_mode`；`puzzle_manager` 与 `plot_tracker`（运行时对象，codec 有专门的序列化逻辑，PR D 会改谜题初始化）；`world_name` 与 `group_name`（仍有多处直接使用，属于 Game 基础元数据）。`gm_uid`、`language`、`log`、`world_state` 留在顶层。

**轨道 C：内容架构 vNext**（内容方案 §39 原序，加入与 R 的约束）

| PR | 内容 | 与轨道 R 的约束 |
|---|---|---|
| A | ContentRef / ContentDraft / CommitPlan / ContentKind registry / canonical service 契约 / 投影契约 / 守卫；api.py 世界书转发迁入 `services/lorebooks.py` | 与 R0 并行；portable schema 须客户端可独立读写（§3.7） |
| B | Store 与 DB ownership cutover：Entry 只认 `book_id`，Primary 以 Binding 为 authority，Book-scoped API，删除语义，provenance 基础 | 无 |
| C | 内置 World / Lorebook 拆分：world schema v3、`templates/lorebooks/`、legacy adapter、locale overlay 拆分、AI 生成输出 Draft | 无 |
| D | 运行时投影统一：Narrative、NPC、Puzzle、Map、Knowledge preview、player-safe 全部走 `ContentProjectionService` | **只替换调用，不改流程；R6-b 在其后** |
| E | 开局 cutover：删除复制 World 语义，改为 World ref + Book bindings + Adventure refs | **R0 之后**，绑定进 `content_binding` 模块槽 |
| F | World 编辑器与 Lorebook 页面整理、四语言 | 无 |
| G | 统一导入导出：Draft preview、CommitPlan、reference closure、Character/Tavern adapter | `device` 为一等 source_kind，重复 external_id 三选一（§3.7）；移动端本地内容库在其后开工 |
| H | Content Pack / Plugin / Module cutover：catalog-only、legacy_autoimport 进 adapter、receipt-based uninstall、`target_book_id` | 无 |
| I | Legacy 删除：`starter_lorebook`、`Entry.world_id`、旧 route、旧开局语义 | 前提：canonical callers 100%、legacy internal callers 0 |

**轨道 M：Native Module / Content Package**（模组方案 §28 原序，是轨道 C 的子轨道，不独立于 PR A–H 之外）

| 步骤 | 内容 | 依赖 |
|---|---|---|
| M1 | Native Package Contract：profile、resources、refs、dependencies、provenance | PR A（ContentRef / ContentKind） |
| M2 | Catalog Lifecycle：install / enable / browse / update / disable / uninstall、reference protection，catalog 不写用户数据 | PR B、PR H 前半 |
| M3 | Module Detail 产品面：Overview / Adventures / Worlds / Lorebooks / Ruleset Content / Assets，展示真正的 package resource | M2 |
| M4 | Import / Fork：接统一 ContentDraft → Preview → CommitPlan | PR G |
| M5 | Adventure Launch：module Adventure → Create Game → resolve World 与 required Books → Bindings → materialize WorldState | PR E、**R0**、M2 |
| M6 | External Adapters：Foundry / Fantasy Grounds 只产出 ContentDraft，格式识别 fail-closed，Human Review 是真实流程 | M1–M5 全部完成 |

轨道 M 在 PR A、B、D 落地前不开工；#400 保持 HOLD，其 adapter 与测试代码在 M6 时按新契约参考重写，不直接恢复。

## 4.2 同步点

```text
时间 ──────────────────────────────────────────────────────────────►

轨道 R   R0 ──► R1 ──► R2 ──► R3 ──► R4 ──► R5 ──► R6-a ──────► R6-b
          │                                                        ▲
          │ S1: R0 先于 PR E                                       │ S3
          ▼                                                        │
轨道 C   A ──► B ──► C ──► D ──────────────────────────────────────┴──► E ──► F ──► G ──► H ──► I
```

| 同步点 | 规则 |
|---|---|
| S1 | PR E 在 R0 合并后开分支；绑定字段进模块槽 |
| S2 | 已取消：R5 不等 PR D，两者仅一行调用重叠 |
| S3 | R6-b 消费 PR D 的 `for_character` 投影；R6-a 不等 |
| S4 | M5 在 PR E 与 R0 之后；`content_binding` 槽里的 ref 必须是 source-aware 的 `(source_kind, source_id, id, digest)`，与 `src/adventures/registry.py` 现有的 `(kind, source_id)` 同形，卸载模组前据此检查 active references |

R1、R2、R3 与 PR A、B、C 完全并行，互不阻塞。轨道 M 整体排在 PR D 之后，与 R4–R6 并行但不共享文件。轨道 R 中只有 R6-b 需要等待轨道 C。

## 4.3 责任与评审

- 每条轨道一个 owner。跨轨道文件（`round_processor.py`、`round_actions.py`、`game_instance.py`、`game_state_codec.py`、`migrations/instance.py`、`tests/architecture/`）的改动需要另一条轨道 owner 评审。
- 每个 PR 描述必须写明：升了哪个 schema、影响哪些存档与 API、对旧数据的策略（保留 / 适配 / 迁移 / 明确拒绝）。
- 不允许长期分支。任一 PR 超过两周未合并，先拆小再继续。

---

# 5. 当前开放 PR 的处理

| PR | 内容 | 处理 |
|---|---|---|
| #399 | Module 主题与市场过滤 | rebase 最新 main 后可合。规则集过滤应从 `RulesetRuntimeRegistry` 取 id，删掉不存在的 `coc7` / `freeform` 别名；未知过滤值返回 400 |
| #400 | 外部 VTT 适配器与 Module 世界书面 | **HOLD**（模组方案 §27）。四个架构 blocker：格式识别不够 fail-closed、`ExternalModuleDraft` 未统一到 ContentDraft、Human Review 不是完整流程、Module Lorebook 只有读侧。此外 XML 解析未用 defusedxml、预览上限沿用 100 MB、新测试被拼进别的测试函数。不再为合并打补丁，M6 时按新契约重写 |
| #401 | Imagegen 分镜与头像参考图 | 与两条轨道正交，解决冲突后独立进行 |
| #402–#405 | Lorebook UI 链 | 属于 PR F 的范围，可以先合，但不得引入新的 `list_entries(world_id)` 调用或 `starter_lorebook` 读取 |

---

# 6. 不做的事

合并两份计划的禁止清单：

- 不启动组合层：不写机制模块格式、组合配置、依赖检查、DSL、可视化编辑器。
- 不拆 `GameInstance` 之外的东西来凑模块数量；模块数不是指标。
- 不做内容方案 §40 的六种假重构：包一层 `list_entries`、UI 新数据旧、Plugin 仍灌 World、保留 `Entry.world_id` 备用、各 service 自己 resolve Books、Module 自建 store。
- 不在投影替换 PR 里改流程，不在拆模块 PR 里改玩法，不在页面 PR 里改 ownership。
- 不动前端规则集注册表的通用化，等后端模块边界稳定。
- 不为了新玩法先改跑团体验；跑团行为在两条轨道全程保持不变，由现有 3000 个测试守住。
- 模组方案 §31 的 Scope Freeze 生效：内容架构未进入施工前，不再往 #400 或任何 Module 代码增加新的外部格式、Module 专属 store / Draft / Binding、新的 legacy autoimport 分支、`target_world_id → Lorebook Entry`、`starter_lorebook` 新功能、World copy workaround。

---

# 7. 完成标准

## 7.1 轨道 R

```text
GameInstance 顶层字段数：104 → R7 后 59 → R8 后约 40（不是硬指标：ownership 分错比多留字段更难收拾）
api.py 行数只减不增（基线 2903）
每个模块：单写入口、自带 schema、自带迁移步骤、自带投影、自带测试目录
删除任一模块，跑团仍可运行，只缺该能力
真人与 AI 提交同一种行动经过同一组检查（R4）
不调用 GM 叙述也能完成一次程序规则行动与结算（R4）
经营式日历与狼人杀式阶段都能表达，跑团不被迫使用阶段表（R5）
席位私有信息不经查询、AI 上下文、界面泄露（R6）
```

## 7.2 轨道 C

内容方案 §43 原样生效：

```text
新业务中 starter_lorebook = 0
新业务中 Entry world_id ownership = 0
新前端 legacy lorebook world routes = 0
Create Game copy/blank World lore path = 0
Plugin manual lore import target_world_id = 0
Runtime NPC/Puzzle/Knowledge 直接 list_entries(world_id) = 0
```

## 7.3 共同

```text
tests/architecture 全绿，且守卫数量只增不减
每次 schema 升级有对应迁移测试与回滚拒绝测试
backend full pytest、frontend unit、vue-tsc、lint、production build、E2E 全绿
文档：ARCHITECTURE_CN/EN 在每个 PR 合并后同步更新"当前 main"，不写未落地能力
```

---

# 8. 待两位 owner 共同决定的问题

1. "使用 Arkham 世界但不用它的主世界书"的 override 机制（内容方案 §9），三个候选里选哪个，需要在 PR A 定契约。
2. NPC / item / spell / class 的内容路由（内容方案 §22）：哪些进 Lorebook，哪些进 Ruleset Catalog，哪些是 seed。这决定 ContentKind registry 的初始条目。
3. `content_binding` 模块槽的字段形状：只放 refs 与 bindings，还是同时承载 Adventure 进度。建议只放 source-aware refs，进度归 Adventure 运行时模块。
4. R2 的纪元键命名与语义：是 `(phase_id, sequence)` 还是不透明字符串。影响 ADR-0003 的 superseded 写法。
5. 守卫的执行方式：只在 CI 跑，还是也作为 pre-commit。
6. 两条轨道的 owner 与每周同步节奏。
7. 规则内容的落点：`src/rulesets/module_catalogs.py` 已有按 runtime 注册 catalog loader 的接缝，但只有 D&D 2024 注册了，`core:legacy`（CoC / Freeform）没有 catalog。模组方案 §14 要求法术、职业、怪物定义进 Ruleset Catalog，那么 legacy runtime 的这类内容是补一个通用 catalog、进 Lorebook、还是明确拒绝，需要在 ContentKind registry 定型前决定。这一项同时涉及 `src/rulesets`，不属于两条轨道任一方，需要单独指定 owner。
8. 客户端离线内容的冲突策略：同一张卡在设备与服务器两边都被编辑后，导入时是以设备为准、以服务器为准，还是保留两份并提示。建议先做"保留两份并提示"，等真实使用反馈再决定是否做合并。

---

# 9. 附：两份原计划的定位

- ADR-0005 记录方向，本文记录路线；路线完成后回头更新 ADR-0005 状态。
- 内容架构 vNext 母方案是轨道 C 的详细设计，本文 §4 的 PR A–I 与其 §39 一一对应；其 §3–§38 的具体定义继续有效，未在本文重复。
- Native Module / Content Package 方向整理是轨道 M 的详细设计，本文 §4 的 M1–M6 与其 §28 一一对应；其 §29 的九条不变量是轨道 C 不变量的子集，不另行维护。
- 本文本身不是 ADR，属于实施计划；当某一步改变了长期依赖方向或持久化模型时，仍按 [ADR 规则](adr/README.md) 单独记录。
