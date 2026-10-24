# DF Agent 内容纠错、撤销与删除策略

状态：本地实现，尚未发布。本文描述当前已接入的资料册/条目恢复边界；它不提供通用删除、永久清空或跨领域万能撤销。

## 当前代码事实

- Agent 修改动作先由 owning service 做 schema、权限、目标和版本检查，再进入服务器保存的计划。计划绑定 owner/session、参数、guard、preview、contract digest 和有效期；确认时重新检查这些条件。
- 资料册操作记录和 canonical 快照由 `lorebook/operations.py` 及内容恢复服务维护。Agent 可读取自己的操作元数据，并对已有、可靠记录的操作执行 undo/restore；历史未记录的编辑不会被猜测补造快照。
- 资料册条目写入沿用 owning SQLite 操作边界；跨文件的世界/角色叙事由 prepared/applied/recovery_required 记录表达，不能宣称跨文件原子事务。
- 派发后断开、取消或进程重启不能证明写入没有发生。结果未知标为 `recovery_required`，保留 receipt/计划供核实，禁止按新 ID 自动重放；重复确认返回原结果。
- 插件和设置遵守 DF Agent 主实现中的 `external`/`destructive` 人工确认规则，未声明或不完整的插件 effect fail closed。

## 已实现的内容操作

Agent 可以在具体服务契约允许时提出并执行：

- 资料册或明确条目的启用/停用；
- 明确自建条目的有限归档和恢复；
- 已记录内容操作的 undo/restore；
- 通过游戏资料册服务调整单局的可编辑资料来源；这只更新目标 game 的绑定，不全局停用资料册。

这些动作均使用 canonical book/entry/operation ID，客户端只提交 ID、参数和确认意图。模型名称、`agent_` 前缀、旧聊天内容或模糊名称不能证明对象归属。

## 删除和归档限制

- 内置来源、插件来源、系统规则、凭据、主资料册，以及仍被游戏/角色引用的内容受保护；Agent 不提供它们的删除或归档动作。
- 单次条目操作最多接受 10 个明确 ID；整本归档只适用于不超过 10 条目的自建、非主、无引用资料册。没有通配符、模糊匹配后全量删除、删除文件、任意 SQL 或永久 purge。
- 影响范围必须由服务器预览显示。仅想移除某局引用时操作该局 binding，不得为了一个游戏全局停用资料册。
- 草稿编辑/丢弃不产生持久化清理；已保存内容优先更正、停用或走有记录的恢复操作。

## 可见性纠错

资料册既有条目的 visibility action 只写 `visible_to`，不重写名称、正文或扩展字段。指定角色时，服务端读取目标 game 当前 live roster 的 canonical UID，并由 composition callback 校验 owner/GM 权限；display name 不能充当 UID。公开、GM-only 和指定角色 audience 的变化都要经过同一审批/guard/恢复边界，含私密标记的内容不能被错误公开。

世界事实的可见性由正式 world operations（`apply_world_ops`、revision 和 authoritative write）负责，不由资料册恢复逻辑代替；规则状态、资源、骰点、回合和已确认剧情不在本策略的可见恢复范围。

## 恢复契约

可恢复操作至少需要：

1. 目标的 canonical identity、受影响字段的 before/after、原始版本/guard 和引用摘要由 owning service 记录。
2. 恢复前重新读取当前 canonical 内容，并确认它仍匹配本次操作的 after 版本。
3. 检测到人工或其它 Agent 后续编辑、引用变化、缺失快照或结果不确定时拒绝自动覆盖，要求重新审查或手动处理。
4. 只恢复本次操作触及的字段，不能用整库快照覆盖无关修改。

恢复记录属于 owner 私有数据；GM 内容不能进入普通公开投影、无关日志或不必要的模型上下文。恢复列表传元数据，不把快照正文自动交给模型。

## 不提供的保证

本策略没有持久化工作流自动恢复、跨模块通用事务、通用补偿算法或万能 undo。已提交写入的取消不是回滚；可恢复性必须由具体 owning service 明确提供。不能证明安全时 fail closed，并把状态报告为待核实，而不是声称操作已经撤销。

## 验证重点

测试和验收应继续覆盖 owner/session 隔离、参数伪造、scope/domain、审批 digest/guard、10 条目上限、内置/插件/主册保护、活动引用、live UID 与 GM 校验、正文保持不变、快照缺失、后续编辑冲突、并发/重复确认、取消 shield、重启后的 recovery_required 和私密内容隔离。模型只提供候选动作，测试不得把模型提示词当作权限边界。
