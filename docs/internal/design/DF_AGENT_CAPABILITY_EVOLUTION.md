# DF Agent 能力目录与持续扩展

状态：部分实现，尚未发布。统一动作目录、动态发现和有限服务端授权已经落地；连续任务、外部副作用和领域覆盖仍受明确边界约束。本文不把提案目标当作现有功能。

## 已实现的基础

`ApplicationAction`/`ActionRegistry` 是能力扩展的唯一目录入口。owning feature/domain module 注册动作，并通过显式 `PreparedAction`/`ActionAssessment` contract 负责准备语义 payload、解析真实目标和评估实际可见性。动作公开稳定 name、version、domain、effect、schema、instructions、guard/preview 元数据和 handler。registry 计算整体 `revision`，每个 descriptor 计算 `contract_digest`；计划确认时重新检查动作契约，版本变化、删除或 source 刷新失败都会使旧计划失效或不可用。tool name 不能绕过 registry。

当前目录由应用、内容/世界、资料册、游戏/冒险、规则/恢复、本局资料册、设置和插件 source 组成。已注册的新动作会被 `GET /api/assistant/agent/capabilities` 发现，并可由 Agent 的 `search_capabilities`/`describe_capability` 按需加载完整 schema。运行时 compact `capability_catalog` 最多携带 160 条摘要，并带 `has_more` 与 `domains`；搜索分页的下一次 `offset` 从当前页起点加返回条数计算。完整 schema 只在 describe 或搜索命中后按需加入 callable 集合；动作 `instructions` 会自动并入工具描述。`library` 页面自动覆盖 `lorebook`、`worlds` 和 `adventures` domain。动态插件 descriptor 在每次 source refresh 时重新读取；失效 source fail closed 并从目录移除其动作。

“可发现”只表示动作已注册且当前 descriptor 可读，不表示模型拥有权限，也不表示系统能执行任意未注册的 route、Python 函数、HTTP、文件、SQL 或插件 API。实际 handler 和领域服务仍是最终 authority。

## 授权模型

服务端授权模式已经实现：

- `suggest`：无连续写授权，每个修改动作逐项计划/确认；
- `routine`：仅允许 `automatic=True`、带 guard 的普通 `write` 动作自动执行，授权 4 小时；
- `task`：允许 scope/domain 内的普通 `write` 与 `disclosure` 连续执行，授权 1 小时；`external`、`destructive` 和未自动许可动作不能借此绕过人工确认。

资料册可见性由 owning module 准备和评估。单条 setter 与普通 `write_lorebook_entries` 使用同一实际 audience policy，拆分写入不能绕过审批；只有 visibility-only prepared payload 不含正文并只改变 `visible_to`，普通 `write_lorebook_entries` payload 含待写正文并执行正常内容写入。canonical book targets 的低风险 GM 收窄可由覆盖目标的 task 自动执行；扩展到 public 或新增角色 audience 时，一个封存的多册计划取得一次人工批准，task 不因此获得 blanket disclosure。前端缺少 ID 时仍保持当前 scope 的明确“全部”选择要求；不能自动全选。

两种连续授权的 `max_steps` 为 1..64，执行消耗 `remaining_steps`。scope 的每个键必须与 owning module 解析的每个 canonical target 精确匹配，没有 prepared assessment 时才匹配动作参数；domain 也必须匹配。授权过期、owner/session 不符、动作 effect 不兼容、资源 guard 变化或 contract digest 变化时 fail closed。

设置写入和插件宿主调用都涉及外部副作用。设置 patch/插件启停标为 `external`；插件声明 `read_only` 或 `mutation` 时也按外部动作人工确认，声明 `destructive` 时按 destructive 人工确认。未知、缺失或 malformed effect 不会降级放行。插件声明不是沙箱，也不替代宿主权限。

## 按需发现和真实观察

Agent 初始只接收必要的基础动作；其它能力由搜索命中或 describe 结果按需加入可调用集合。查询结果带 registry revision 和 unavailable source；descriptor 不暴露 handler。模型必须使用 live catalog、目标资源和领域服务结果选择 canonical ID，不能从翻译名称、旧手册或聊天记忆猜测身份。

文档检索是可选的只读说明能力，只接受当前安装版本的本地/版本匹配来源；它不能创造能力、授权或 API。新功能要进入 Agent，必须先由 owning module 建立真实 schema、handler、权限、guard/preview、effect 和 recovery/receipt 语义，再注册到目录。

## 可见性与领域边界

批次报告中的 `unconfirmed` 表示可能已提交、待核对，不计入 `not-executed`；`unconfirmed_count` 单独统计它。

资料册 audience 变更只更新既有条目的 `visible_to`，指定角色使用 live roster canonical UID 并通过 GM 校验。`set_lorebooks_visibility(book_ids, audience, ...)` 先从 live catalog 解析显式 1..128 个 canonical book IDs；scope 绑定 owner 解析出的每册 book target，`game_key` 只校验 live 角色，不构成 book 授权；每册最多 1000 条目。计划签名绑定不可变 payload、assessment、版本和 `contract_digest`，4 MiB 上限只作用于 `prepared.payload`。按册报告 success/failure/unconfirmed/not-executed，并提供 `unconfirmed_count`；册间可取消。每册在 owning-store 事务中写入：隐藏 `visibility_plan_book` 负责幂等协调 parent receipt，真正可撤销子记录是 `kind=write_entries` 加 `visibility_only` marker，不形成跨册原子性。private-marker 只是 validation warning。`POST /api/assistant/agent/resume` 是 owner 对原封存剩余目标的显式确认，复核 plan/contract/TTL 和每个未完成目标；原 automatic 执行还逐册重新检查 live task grant，不依赖已撤销的 grant，并跳过 committed receipts。重复 confirm 返回已存响应且不重试部分写入。只有注册 resumable executor 可协调 owner receipts，未知突变仍为 `recovery_required`。世界事实可见性使用正式 worldops；规则结算、骰点、资源、回合和恢复动作复用既有 authority。specific ruleset 不得反向污染 generic engine，也不能为 Agent 增加任意资源写入旁路。

整册可见性恢复由隐藏的 `kind=visibility_plan_book` 协调每册幂等 parent receipt；真正可撤销记录是 `kind=write_entries` 加 `visibility_only` marker。`prepared.payload` 上限 4 MiB、最多 128 册；每册 child receipt 可稳定恢复，重启不自动写入。恢复会拒绝目标内容、绑定或资料册语义元数据冲突；只有 visibility-only marker 的记录忽略 `revision` 与 `updated_at` 派生字段。它保持逐册独立回执，不能被描述为跨资料册全局回滚。

首次未批准计划在 15 分钟后失效；已派发计划暂停后，owner 可以核对原封存的剩余目标并显式确认续跑，每次取得新的 15 分钟执行窗口，不改写原计划 `expires_at` 或续期 Task grant。最近持久化活动起 24 小时的会话保留限制仍有效，已提交目标依可信回执跳过。检查点须对照完整目标清单，尚有未执行目标时不能报告整体 `completed`。

新动作必须明确 owner、actor、目标资源、domain、effect、并发/版本 guard、结果 receipt、取消和不确定结果语义。没有这些契约的能力应保持未注册或人工流程，而不是用提示词模拟权限。

## 运行预算与任务边界

provider 返回的 `total_tokens` 在每次请求完成后检查，runtime 对各次响应求和。`assistant_agent_task_max_tokens`（默认 256000）与单次回复 `assistant_agent_max_tokens`（默认 4096）独立；默认最多 24 次请求/工具迭代，显式授权可放宽至 64。任务预算不再由 `max_tokens * steps` 推导，也不是费用、余额或 provider 账单的硬上限；请求、工具调用和每领域子调用仍由 framework 或 owning service 的预算控制。

当前 session 持久化草稿、审批计划、任务和 receipt，可处理重复确认、客户端断开和结果待核实；它不是持久化工作流自动恢复系统。取消只阻止尚未派发的步骤；已提交写入保持 receipt，未知结果进入 `recovery_required`，不得生成新 ID 盲目重试。没有跨模块通用事务或万能 undo。

## 扩展规则

新增能力按以下顺序进入：

1. 在 owning module 定义稳定 identity、schema、domain/effect、权限和资源 guard。
2. 复用正式 service/engine/plugin host，不把完整 WebAPI 当作 service locator，也不新增 generic path 的具体实现分支。
3. 注册 `ApplicationAction`，让 registry revision/descriptor/digest 成为发现和计划校验来源。
4. 为 routine/task/suggest 明确授权语义；外部/破坏性动作默认人工确认，未知 effect fail closed。
5. 增加针对权限、私密数据、并发、版本冲突、取消、重复确认和不确定结果的行为测试，并让 UI 和 Agent 共用同一 handler contract。

现有目录可以持续扩展游戏、世界、资料册、设置和插件动作；但每项能力必须逐项提供真实实现。目录自动发现不能替代业务实现、领域恢复或发布审批。

## 尚未实现的长期目标

以下仍是后续工作边界，不应在产品文案中当作当前能力：持久化多步骤工作流的暂停/继续和跨重启自动恢复、跨模块事务协调、通用补偿/合并、任意设置管理、插件写操作的额外宿主契约、完整导入导出和所有游戏 mechanic 的覆盖。实现这些目标前，应先更新对应 owning contract、测试和架构事实，并新增或 supersede ADR。
