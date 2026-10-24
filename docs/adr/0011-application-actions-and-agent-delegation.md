# ADR-0011：应用动作与 Agent 委托

- 状态：`Accepted`
- 范围：本地实现，尚未发布
- 日期：2026-10-19

## Context

DF Agent 需要调用多个 owning module 的真实业务能力，同时保持服务端 owner/session、游戏 actor、GM/private visibility、规则 authority 和插件宿主边界。把动作名单、参数 schema、权限判断和执行逻辑分别维护在 Agent、页面和 route 中，会让能力目录漂移；把任意函数或 HTTP route 暴露给模型则无法建立稳定 identity、版本 guard 或恢复语义。

当前实现还需要支持新增游戏、世界、资料册、设置和插件动作被发现，而不把“自动发现”误解成“自动支持任意 API”。连续任务也必须区分普通写入、信息披露、外部副作用和破坏性操作。

## Decision

### 1. 统一应用动作契约

以 `src/application_actions.py` 的 `ApplicationAction` 和 `ActionRegistry` 作为应用动作的公共 contract。owning module 注册稳定 name、version、schema、domain、effect、guard/preview 和 handler，并显式提供 `PreparedAction` 与 `ActionAssessment` contract：owning module 负责准备语义 payload、解析真实目标和评估实际可见性策略，registry 只负责目录、版本和 contract 校验。确认计划必须重新核对 descriptor/digest、assessment、资源 guard 和服务器权限；版本变化、删除或动态 source 失败会使旧计划失效或不可用。

动作目录是发现与校验边界，不是新的领域 authority。handler 仍调用既有 service、engine、worldops、recovery 或 plugin host。未注册的函数、route、文件、SQL、任意 HTTP 和 RPC 不具备 Agent 执行资格。

### 2. 动态发现按需加载

Agent 初始只加载必要动作；运行时 compact catalog 最多提供 160 条摘要，并带 `has_more` 和按 domain 统计的 `domains`。`search_capabilities` 支持按 query/domain 分页，下一页从当前页起点加返回条数继续；`describe_capability` 从 live registry 按需加载精确 schema。`library` 页面自动加载 lorebook、worlds 和 adventures domains，动作 instructions 自动进入工具描述。动态 source（当前为插件 descriptor）每次刷新并重新验证；失效 source 的动作移除并报告 unavailable。文档、聊天内容和模型输出不能改变 registry、canonical identity 或授权。

### 3. 服务端有限授权

授权保存在 owner session，由服务端强制执行：

- `suggest` 不建立连续写授权，修改逐项确认；
- `routine` 仅允许带 guard 的 `automatic` 普通 `write`，有效期 4 小时；
- `task` 允许授权 scope/domain 内的普通 `write` 和 `disclosure`，有效期 1 小时。

连续授权的步数为 1..64；每个给定 scope 键必须与 owning module 解析出的每个 canonical target 精确匹配，没有 prepared assessment 的动作则匹配参数；domain 也必须匹配。`external`、`destructive`、未声明 effect 和不满足 guard 的动作 fail closed 或回到人工确认。设置 patch/插件启停属于 external；插件声明 read_only/mutation 仍走 external 人工确认，destructive 走 destructive 人工确认。插件 effect 声明不是沙箱。

资料册可见性动作不以 tool name 绕过 registry 或授权。单条 setter 与普通 `write_lorebook_entries` 都使用 owning module 评估出的实际 audience policy，因此把同一变更拆成多条写入不能绕过审批；只有 visibility-only prepared payload 不含正文并只改变 `visible_to`，普通 `write_lorebook_entries` payload 含待写正文并执行正常内容写入。这些 prepared visibility writes 也不产生新的 blanket disclosure。对 canonical book targets 的低风险 GM 收窄可由覆盖该目标的 `task` 自动执行；扩展到 `public` 或新增角色 audience 时，由一个封存的多册计划取得一次具体审批，不按册重复审批。已有当前 scope UI 在缺少 ID 时仍要求用户明确选择“全部”。

本局纯展示偏好与影响规则/GM 决策权的策略分开注册。后者、语义包含策略变更的通用规则指令和确认游戏时间的休息操作均保留逐项审批，task 授权不能代替这些确认。专业休息与 HTTP 共用单人/多人分派命令，不允许 Agent 绕过多人收集流程。

### 4. 领域 authority 不被 Agent 取代

资料册 audience 变更只更新 `visible_to`，指定角色使用 live canonical UID 并由 GM/owner 边界校验；世界事实 visibility 走正式 worldops；规则、骰点、角色资源、回合和恢复复用既有 authority。Agent 不引入 generic engine 的具体规则分支，也不把完整 WebAPI 作为 service locator。

### 5. 取消与不确定结果

服务器保存计划和 receipt。取消撤销尚未派发步骤；已进入权威写入的确认受 shield 保护，客户端断开不等于写入取消。重复确认返回原 receipt；结果不确定时标记 `recovery_required`，禁止盲目重放。该模型不提供持久化工作流自动恢复、跨模块万能事务或通用 undo。

持久化计划不代表写入已经派发：取消清理未派发计划，并在开始通知及异步权限校验后重新核对 stop/plan identity；取消之后不得启动新的领域写入。

`set_lorebooks_visibility(book_ids, audience, ...)` 只接受显式 1..128 个 canonical `book_id`；“全部”必须先从 live catalog 解析为 ID 列表，scope 绑定 owner 解析的每册 book target，`game_key` 只验证 live 角色，不构成 book 授权。owning module 先按每册最多 1000 条目准备并评估实际 audience，再生成带不可变 payload、assessment、版本和 `contract_digest` 的多册计划；只有 visibility-only `prepared.payload` 不含正文，普通 `write_lorebook_entries` payload 仍含待写正文。4 MiB 上限只约束 `prepared.payload`。private-marker 只产生校验警告，不构成同意。计划报告每册 success、failure、unconfirmed 或 not-executed，并提供 `unconfirmed_count`；支持册间取消。每册仍在 owning-store 事务中写入：`kind=visibility_plan_book` 隐藏记录负责幂等协调 parent book receipt，真正可撤销的子记录仍是 `kind=write_entries` 加 `visibility_only` marker，不形成跨册事务。重复 confirm 返回已存响应且绝不重试部分写入；重启不自动写入，`POST /api/assistant/agent/resume` 是 owner 对原封存剩余目标的显式确认，复核 plan/contract/TTL 和每个未完成目标；原 automatic 执行还逐册重新检查 live task grant，不依赖已撤销 grant，并跳过已有 committed receipts。只有注册的 resumable executor 可协调 owner receipts；未知突变仍进入 `recovery_required`。恢复检查目标内容、绑定和资料册语义元数据冲突，仅对 visibility-only 记录忽略派生 revision/time 字段。

首次未批准计划的有效期为 15 分钟。已派发后暂停的计划不因首次审批过期而丢失续跑入口：owner 核对原封存计划并显式确认剩余目标后，取得新的 15 分钟执行窗口，已完成目标依可信回执跳过。续跑不续期或扩大 Task grant，仍检查 owner、计划完整性、动作版本和每个未完成目标；沿用最近持久化活动起 24 小时的会话保留限制。检查点只有在原计划全部目标成功时才能报告 `completed`，未初始化或尚未执行的目标也计入完成判定。

### 6. 使用量语义

provider 报告的 `total_tokens` 在单次请求完成后检查并按响应累加用于任务观测。每次回复上限 `assistant_agent_max_tokens`（默认 4096）与单次任务预算 `assistant_agent_task_max_tokens`（默认 256000，范围 16000..2000000）独立，不再以回复上限乘步骤推导任务预算。默认请求/工具调用上限为 24，较大显式授权可提高至 64；写入授权余量仍独立约束。预算触顶保留结果、标明 token 或步骤原因，由用户手动继续，不自动重试。这不是费用、余额或 provider 账单硬上限；领域工具保留各自子调用预算。

## Consequences

正面结果：

- 页面和 Agent 可以共享同一动作与领域校验；
- 新注册动作自动进入 live discovery，不需要另维护中心 Agent 名单；
- contract digest、revision、guard 和 receipt 让旧计划在动作变化、资源变化或权限变化时安全失效；
- external/destructive 与未知 plugin effect 默认保守，减少把外部副作用伪装成普通写入的风险。

代价与限制：

- 每个新能力必须先建立 owning contract、effect、权限和恢复语义；不能靠提示词或目录摘要补齐；
- 动态发现不提供任意 API 能力，也不保证模型会正确选择动作；
- 跨文件或跨模块操作仍由具体服务决定是否可恢复，不能承诺统一事务；
- routine/task 的步数和 token 观测不等于费用封顶；外部副作用必须保留人工确认；
- 新的持久化工作流、插件写操作和更广业务覆盖需要后续 ADR/架构更新与行为测试。

## Implementation boundary

实现入口和当前事实记录在：

- `src/application_actions.py`
- `src/webui/services/assistant_agent.py`
- `src/webui/services/assistant_authorization.py`
- `src/webui/routes/assistant_agent.py`
- `docs/internal/design/DF_AGENT_IMPLEMENTATION.md`
- `docs/internal/design/DF_AGENT_CAPABILITY_EVOLUTION.md`
- `docs/internal/design/DF_AGENT_DELETE_POLICY.md`

本 ADR 不授权发布版本、tag、Release 或对外说明；当前 Accepted 只表示本地代码和文档采用该长期边界。

## Validation

行为验证应覆盖 registry revision/digest、动态 source fail-closed、按需 search/describe、scope/domain/期限/步数、suggest/routine/task effect、设置和插件人工确认、live UID/GM visibility、worldops、取消 shield、重复 receipt、重启后的 recovery_required，以及 owner/private 数据隔离。测试保护这些行为 contract，不绑定某个内部文件形状。
