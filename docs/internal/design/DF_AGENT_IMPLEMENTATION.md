# DF Agent 实现与能力边界

状态：本地实现，尚未发布。本文记录当前源码已经提供的能力和边界；后续设计目标不视为已经实现的产品承诺。

DF Agent 是 owner 应用壳中的应用代理。模型输出只是注册动作的候选，不能授予自己权限，也不能绕过领域服务、服务端 actor、规则引擎或插件宿主。

## 当前实现范围

`src/application_actions.py` 定义唯一的应用动作契约 `ApplicationAction` 和 `ActionRegistry`。动作由 owning module 在应用组合根注册；路由、页面和 Agent 适配器复用同一领域 handler。每个动作都有稳定 `name`、`version`、JSON 参数 schema、`domain`、effect、是否自动处理、guard、preview 和 handler，并通过显式 `PreparedAction`、`ActionAssessment` 暴露准备后的语义 payload 与评估结果。owning module 负责解析 live 目标和实际可见性策略，registry 不从 tool name、路由或 Python 成员推导权限，也不提供旁路。动作描述不包含 handler 或其它私有回调。

`contract_digest` 是动作公开 descriptor 的 SHA-256；registry `revision` 是按 canonical action name 排序后的 descriptor 集合摘要。计划保存动作的 `contract_digest`，确认和执行时重新读取 registry 并拒绝已删除或契约已变化的动作。版本替换必须使用更高版本；重复注册和不合法 effect/schema 会失败。

当前组合根注册的能力包包括：应用目录、内容/世界/角色叙事、资料册、游戏与冒险、规则/恢复、游戏本局资料册、权威世界事实、设置和插件动作。内容库适配器支持世界克隆、资料册元数据和绑定读取、原生 JSON 导出/导入计划、冒险复制与详情；原生导入重用 owning service 的预览和提交摘要，不支持任意路径文件读取。新增的已注册游戏、世界、资料册、设置或插件动作会出现在同一个 live registry 中，并可通过目录发现；这不等于 Agent 自动支持任意未注册的 Python 函数、HTTP route、插件 RPC、文件或数据库 API。

变化中的动作集合可以通过 registry source 刷新。当前插件 source 每次刷新都会重新校验 descriptor；插件消失时动作移除，source 出错时该 source 的动作全部移除并在 `unavailable_sources` 中报告，不能保留旧 descriptor 冒充可用。未知或缺少必要字段的插件 effect 不会降级为低风险动作。

## 发现与按需加载

Agent 启动时只加载基础 application 动作、当前页面 domain、当前服务器授权 domain，以及两个发现动作。模型需要其它能力时调用：

- `search_capabilities`：按 query/domain 查询 live registry，返回 `revision`、能力摘要、总数、`offset`、`has_more`、按 domain 统计的 `domains` 和不可用 source；分页时下一次 `offset` 使用当前页的起点加返回条数。Agent 初始 prompt 只接收最多 160 条 compact `capability_catalog`，其中不包含参数 schema；`library` 页面会自动加载 `lorebook`、`worlds` 和 `adventures` 三个 domain。
- `describe_capability`：按稳定动作名读取当前完整 descriptor 和精确参数 schema。

搜索和描述不会授予执行权限。compact catalog 和搜索页只返回摘要；搜索命中后动作才加入 callable 集合，完整 schema 仍可通过 `describe_capability` 精确读取。注册动作的 `instructions` 会自动进入对应工具描述，供 owner Agent 遵循，但不能改变权限。描述或文档中的文字不能注册动作、扩大 scope 或改变 effect。registry 的结果和领域服务的实时观察才是 canonical ID、可用性与执行契约的来源。`search_documentation` 只在需要使用/部署说明时按需查询本地版本匹配的说明，不能扩展动作目录。

## 服务端授权与确认

授权由 `assistant_authorization.py` 在服务端保存，并按 owner/session 绑定。三种模式的当前语义是：

| 模式 | 已实现行为 |
| --- | --- |
| `suggest` | 不建立连续写授权；修改动作逐项生成服务器计划并等待确认。 |
| `routine` | 仅允许动作明确标记 `automatic=True` 且 effect 为普通 `write`，由已授权范围内自动执行。 |
| `task` | 在已授权范围内允许普通 `write` 和 `disclosure` 连续执行；仍拒绝 `external`、`destructive` 和未满足动作 guard 的操作。 |

routine 授权有效 4 小时，task 授权有效 1 小时；两者都有 `remaining_steps`，步数必须为 1..64。可选 domain 必须是 registry 中已知 domain；每个动作的 domain 必须匹配。可选 scope 只接受 `game_key`、`world_id`、`book_id`、`card_id`，每一个给出的键都必须与 owning module 解析出的每个 canonical target 逐字相等；没有 prepared assessment 时匹配动作参数，缺失或跨目标都 fail closed。授权过期、步数耗尽、owner 不符或 session 不符均不放行。

资料册可见性使用 owning module 的 `PreparedAction`/`ActionAssessment`。单条 setter 与 `write_lorebook_entries` 均按实际 audience policy 判断，拆分写入不能绕过审批；visibility-only prepared payload 不携带正文并最终只更新实际行的 `visible_to`，而普通 `write_lorebook_entries` 的 prepared payload 包含要写入的正文并执行正常内容写入。对 canonical book targets 的 GM 收窄可由覆盖目标的 task 自动执行；扩展为 `public` 或新增角色 audience 时，一个封存的多册计划取得一次人工批准，task 不因此获得 blanket disclosure。`suggest` 与撤销授权仍可用。

前端 scope 只接受有效且非空白的 `game_key`、`world_id`、`book_id`、`card_id`。缺少目标 ID 时，对应选项保持禁用；routine/task 授权在用户明确选择“全部”之前保持阻止，前端不会自动替用户选择全部目标。scope/grant 的 HTTP 和持久化格式保持不变。

确认计划绑定 owner、session、动作名、参数、guard、contract digest 和有效期。审批前会再次执行 schema、authorize、guard 和 preview；确认时还会重新检查动作 descriptor、授权、资源版本和计划摘要。客户端只能提交 session/approval ID、摘要和确认意图，不能改写参数。

设置动作的 `patch_settings` 和插件启停被标为 `external`，不能由 routine/task 自动放行，必须人工确认。插件 descriptor 声明 `read_only` 或 `mutation` 时，宿主调用仍按 `external` 处理并人工确认；声明 `destructive` 时按 `destructive` 处理并人工确认。缺失、未知或不完整的声明 fail closed，回到手动插件流程；声明本身不是原生插件进程沙箱，也不是行为证明。

本局偏好分为 `set_room_preferences` 的四个展示字段（快捷行动、骰子揭示、叙事视角、呈现模式）和 `set_room_policy` 的游戏策略字段（成长方式/决策权、GM 风格、Push 与幸运等待时限）。前者可在 routine 范围内自动处理；后者与通用 `submit_ruleset_intent` 均要求具体确认，防止规则自动化指令绕过策略分类。`perform_ruleset_rest` 同样逐次确认，必须显式提交 `confirm_elapsed_time=True`，适配器不得代填；预览展示角色、休息类型和生命骰，时间语义仍由当前规则契约决定。

取消会撤销服务器授权并阻止尚未派发的步骤。已经进入权威写入的确认使用 shield，浏览器断开或取消不会把已提交动作伪装成未执行。重复确认返回原 receipt；派发后结果不确定时标记 `recovery_required`，保留待核实计划并禁止盲目重放。取消不提供对已提交写入的万能回滚。

取消会清理已经持久化但尚未派发的计划，并在工具开始通知之后、异步权限检查之后再次核对取消状态和计划 identity；只有跨过写入派发边界的动作才受 shield 保护。

## 领域 authority 与隐私

批次报告中的 `unconfirmed` 表示可能已提交、待核对，不计入 `not-executed`；`unconfirmed_count` 单独统计它。

- 内容、资料册、恢复和游戏本局资料册动作复用各自 owning service 的校验、guard、快照/操作记录和权限边界；Agent 没有通用文件、SQL、shell 或任意 HTTP 写入能力。
- `set_lorebooks_visibility(book_ids, audience, ...)` 要求先从 live catalog 解析出显式 1..128 个 canonical book IDs；scope 绑定 owner 解析出的每册 book target，`game_key` 只用于校验 live 角色，不构成 book 授权。每册最多 1000 条目。owning module 为每册准备并评估真实 audience，生成签名绑定不可变 payload、assessment、版本和 `contract_digest` 的多册计划；visibility-only `prepared.payload` 不含正文，普通 `write_lorebook_entries` payload 仍包含待写正文并执行正常内容写入。4 MiB 上限只约束 `prepared.payload`，private-marker 仅是 validation warning。每册在 owning-store 事务中写入：隐藏的 `visibility_plan_book` 记录负责幂等协调 parent receipt，真正可撤销的子记录仍是 `kind=write_entries` 加 `visibility_only` marker。计划逐册报告 success/failure/unconfirmed/not-executed，并提供 `unconfirmed_count`；允许册间取消。`POST /api/assistant/agent/resume` 是 owner 对原封存计划剩余目标的显式确认，复核 plan/contract/TTL 和每个未完成目标；原 automatic 执行还要逐册重新检查 live task grant，不依赖已撤销的 grant，随后跳过 committed receipts。重复确认返回已存响应且不重试部分写入；只有注册的 resumable executor 可协调 owner receipts，未知突变仍为 `recovery_required`。指定角色使用 live roster canonical UID，显示名不能代替 UID。
- 世界事实可见性走正式 `worldops`（`apply_world_ops`、revision guard、权威写锁和保存），不在通用资料册或世界元数据里另造一个同名布尔字段。读取使用 viewer-safe world projection。
- 规则结算、骰点、角色资源、回合和世界事实仍由现有规则/游戏 authority 执行；角色资源恢复复用 `ResourceRecoveryService`，内容撤销复用内容恢复服务，不形成第二套 mechanics 或权限模型。
- HTTP 与 Agent 的专业休息共用 live-rest 应用命令，单人沿用单角色结算，多人收集符合资格角色的提交后协同结算；Agent 不直接绑定单角色路径，也不新增独立的时钟推进逻辑。
- 设置读取只暴露脱敏配置和 provider catalogue；凭据正文不进入模型、会话、receipt 或错误提示。AI 配置使用共享 `ai_providers`、独立 `ai_provider_key_<id>` 凭据和能力级 `*_provider_ref` 新格式；Agent 设置 patch 只处理允许的目录/reference 字段，旧直填地址、密钥或 API 字段被明确拒绝。
- owner/session 隔离在路由和服务层同时执行。GM/private 数据必须在投影或服务端边界过滤，不能先发送给无权用户再靠 UI 隐藏。

## Token、步骤与运行状态

模型每次请求使用独立 `assistant_agent_max_tokens`（默认 4096）；整次任务使用 `assistant_agent_task_max_tokens`（默认 256000，可设 16000..2000000），两者独立保存与热更新。通过 `AgentContentTools` 生成内容草稿时，使用独立的 `text_gen_max_tokens`（默认 16384），不受 Agent 单次回复上限或整项任务预算替代或合并。任务累计 provider 报告的输入与输出总量，工具往返会重复计算上下文；不再用回复上限乘步骤推导总预算。普通任务默认最多 24 次请求/工具调用，显式授权的更大步骤额度可放宽至 64；写入授权余量独立校验。provider 返回后检查使用量，超出 token 预算时不派发该响应的工具，因此这不是费用、余额或 provider 账单硬上限。领域工具的子调用预算仍由各自 owning service 管理。

配置 `generation_defaults_version` 升至 7 时，对 `text_gen_max_tokens` 执行一次性迁移：所有低于 16384 的旧值（包括用户自定义的低值）提升到 16384，已达到或超过 16384 的值保留。迁移完成后，用户仍可手动降低该值；后续加载不会再次重置。其它生成预算字段不受这次迁移影响。显式 `TRPG_TEXT_GEN_MAX_TOKENS` 环境覆盖继续优先，沿用现有配置保存语义，不属于这次自动提额范围。

预算触顶时任务以 `budget_reached` 停止，`budget_reason` 区分 `tokens` 与 `steps`，已用 token、草稿和回执保留。用户可以点击“继续任务”启动下一轮，不能自动重试或跳过审批。当前会话存在待批准计划时，悬浮入口、权限折叠条和任务区显示数字角标；点击入口展开并定位审批卡，确认、取消或切换会话后按当前状态清除。角标只统计待批准计划，不统计历史事件或草稿。

任务结果通过 SSE 发送 `session`、`status`、`task`、`tool_start`、`tool_result`、`draft`、`approval`、`authorization`、`navigate`、`sources`、`delta`/`done` 或 `error` 事件。framework 的内部 `usage` 通知汇入 `task.used_tokens/token_budget`，不是独立客户端事件。`GET /session/{session_id}` 提供 owner 可恢复的草稿、待审批计划、receipt、任务和当前授权摘要。会话和执行记录写入数据目录的 `assistant_agent`，不写入凭据。

当前持久化记录支持会话 TTL、重复确认、重启后的结果核对和不确定状态标记；它不是持久化工作流引擎，也没有跨模块的自动恢复、全局事务或万能 undo。跨文件/数据库操作必须依照具体 owning service 的 prepared、guard、receipt 和 recovery contract，无法证明结果时 fail closed。

资料册可见性批次由隐藏的 `kind=visibility_plan_book` 记录负责每册幂等协调 parent receipt；真正可撤销的子记录仍是 `kind=write_entries` 加 `visibility_only` marker。private-marker 扫描只属于校验，不构成发布同意。恢复预览会比较目标条目的内容、绑定、资料册语义元数据和其它受影响快照；只有该 marker 的 visibility-only 记录忽略资料册 `revision` 与 `updated_at` 这两个派生时间字段。批准前重新检查资料册内容、绑定和受影响对局，变化后必须重新预览。4 MiB 上限只约束 `prepared.payload`，最多 128 册；每册沿用 512 KiB 快照上限并预留撤销记录空间。冲突时拒绝覆盖后续编辑；它不把多册计划提升为跨资料册事务，也不重写条目正文。

首次审批有效期与执行窗口分开：未批准的计划 15 分钟后不能直接确认；已派发并暂停的计划可在既有会话保留期内，由 owner 核对原封存剩余目标后显式续跑，每次确认给予新的 15 分钟执行窗口。首次 `expires_at` 和计划摘要保持不变，已提交目标通过可信回执跳过；续跑不续期或扩大 Task grant。会话沿用最近持久化活动起 24 小时的保留限制。中间检查点必须按原计划的全部目标判定，只有全部成功才能报告 `completed`。

## HTTP contract

所有 Agent route 都要求 owner authentication；页面 context 只用于发现和提示，不构成 actor、game 或权限授权。

| 方法 | 路径 | 当前用途 |
| --- | --- | --- |
| `GET` | `/api/assistant/agent/capabilities` | 查询 live registry；支持 `q`、`domain` 与当前页起点 `offset`（0..1000000）；返回 `offset`、`has_more`、`total` 与动态 `domains`。 |
| `POST` | `/api/assistant/agent/authorization` | 服务端创建/替换 suggest、routine 或 task 授权；校验 scope、domain、1..64 steps 和有效期。 |
| `POST` | `/api/assistant/agent/cancel` | 撤销 session 授权并取消尚未派发的工作。 |
| `POST` | `/api/assistant/agent/run` | 运行模型任务并以 SSE 返回规划、发现、工具、审批、使用量摘要和结果事件。 |
| `POST` | `/api/assistant/agent/confirm` | 以 `approval_id`、`plan_digest` 和 `approved` 确认/拒绝一个服务器计划。 |
| `POST` | `/api/assistant/agent/resume` | owner 以 `session_id`、`approval_id` 和原始 `plan_digest` 确认恢复原封存计划的剩余目标；校验计划完整性、版本和 TTL，逐册复核目标并跳过已提交回执。 |
| `GET` | `/api/assistant/agent/session/{session_id}` | 读取 owner 私有 session 快照、任务、授权和 receipt。 |
| `POST` | `/api/assistant/agent/review` | 重新审查用户编辑后的内容草稿，并使旧保存计划失效。 |
| `POST` | `/api/assistant/agent/plan` | 内容管理工作台为明确白名单恢复动作创建同一服务器计划。 |
| `GET` | `/api/assistant/agent/content-operations` | 当前 owner 的内容恢复记录元数据，不传快照正文。 |

既有 schema 1 会话继续可读。没有动作 contract digest 的旧待审批计划必须取消并重新规划；已完成 receipt 仍可恢复，禁止将旧计划默认为新契约授权。

`/api/assistant/chat` 保留原只读问答契约。Agent 导航只返回已知本地 route 和受限 query；模型不能提供可执行 URL。

## 前端边界

owner 业务页面共享 `AssistantShell`/`AssistantPanel`；公开、玩家和分享入口不挂载 owner Agent。窗口、浮动入口、草稿和任务区的浏览器偏好由对应 composable 管理；这些偏好不改变服务端授权。草稿编辑、重新审查和确认使用服务器返回的 draft/plan，不把客户端卡片当作权威参数。

## 验证与剩余边界

当前测试覆盖 registry revision/contract digest、动态 source 刷新和 fail-closed、发现后调用、owner/session 隔离、scope/domain/过期/步数、routine/task effect、插件 descriptor、设置脱敏、visibility-only 的 live UID 与 GM 校验、worldops revision guard、取消、shield、重复确认和 recovery_required。

仍需单独补齐的能力必须先在 owning module 建立正式 contract，再注册到 registry：持久化多步工作流的暂停/继续、跨模块补偿语义、更多设置/插件写操作、导入导出和任意新的游戏机制。本文不把这些后续目标描述成现有能力，也不承诺 Agent 自动支持尚未注册的应用 API。
