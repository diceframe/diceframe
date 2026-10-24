# AI 座位配置与恢复（本地未合并实现）

本设计说明属于 `codex/vnext-integrated-baseline`，不代表 main 已合并架构。音乐继续挂起。

## 所有权与请求链

- `engine/modules/ai_seats.py` 拥有保存策略、版本和安全 outcome 元数据；角色与控制权仍由现有玩家模块拥有。
- `webui/services/ai_seats.py` 解析当前应用服务商目录、生成脱敏投影、保存配置和协调重试。
- `llm/seat_policy.py` 提供可注入生成契约。叙事 adapter 仍是 `commands/ai_player.py`；使用既有玩家信息视图和唯一行动提交门禁。
- `features/play/ai-seats/` 拥有 UI；PlayView 只传入对局、玩家、预览权限并委托刷新和控制权切换。
- route 使用公开 WebAPI 委托和 `viewer_for().is_gm`；AccessControl 传递新接口的玩家预览身份。管理员预览、delegate 和普通玩家都不因此获得设置权限。

## 保存与继承

GameInstance schema 35→36 新增 `modules.ai_seats` schema 1。旧存档得到空座位覆盖和继承应用设置的本局默认；已有模块（包括未知 schema）原样保留，不解释旧直填 AI 配置、不复制地址或密钥。提高整体存档 schema 可以阻止较旧程序忽略座位覆盖后静默使用共享模型。该变化不是应用发行版本号升级。

配置仅包含 `provider_ref`、`model`、`style`；`null` 逐字段继承本局默认，默认继续继承应用引用/模型。模型只在对应引用仍是应用默认时继承应用模型，换服务商而没有可继承模型时保持未配置。风格预设为 character/concise/cautious/bold/supportive，只补充表达偏好，不覆盖角色信息、知识边界或规则权限。

保存请求携带 `expected_revision`，过期编辑拒绝。保存失败恢复原策略。重开保留本局默认、清除座位覆盖和 outcome；移除玩家清理其配置，并以 revision 防止同 UID 重用接收旧生成。无法解释的配置原样保留且不可运行。

设置 API 将 `/ai-seats/defaults` 与 `/ai-seats/seats/{uid}` 分开，玩家 ID 为 `defaults` 时也不会误改全局默认。UI 校验接口响应结构，读取失败保留页面并提供重新加载入口。

凭据每次从当前应用配置按引用解析，运行时连接参数只进入新建的 LLMClient，用完关闭。座位引用失效、地址缺失、目录中的模型移除或显式非 chat 能力均保持不可用，绝不回落共享 client。应用 provider 删除仍由原配置边界清理凭据和应用能力引用；座位保留失效引用以解释问题，等待 GM 重新选择，不能把它清成继承后偷偷换模型。没有模型目录的本地服务可以填写模型名；远端实际可用性由调用结果反映。

## 并发与状态

生成开始捕获 run、round、control、座位/默认配置 revision 以及解析后的连接快照。现有 action commit 在权威/状态锁内调用同步 `admission_check`，当前配置或连接变化后丢弃旧结果。其它座位的编辑不会使本席位结果过期。

`generating` 为运行时状态，不写存档；保存最近 outcome 的安全状态、原因类别及回合，不保存模型文本、异常消息或连接信息。失败分类包括超时、连接失败、凭据被拒、限流、空输出和未配置。重复/过期结果不覆盖上一份有效 outcome。失败/跳过的状态即使未产生行动也会保存。

重试沿用 `turns.resume_after_control_change` 的唯一推进边界：真人未交齐、等待骰子、非行动阶段、非 AI 控制或该席位已提交时拒绝；已经提交的等待回退、真人行动和已结算结果不会删除或重写。停止托管复用现有控制权切换。UI 显示对应原因并禁用当前不可执行的操作。

## 权威战斗选择

`HostedIntentChoiceRuntime` 是可选公共契约，D&D 实现有限候选集，每项均通过真实 intent validation。模型仅返回候选 ID，不提交自由 JSON mechanics。自己的角色与公开历史复用 player-safe context；战斗只附公开 combat 投影，不发送其他角色卡、GM NPC 内部或 Director/campaign 隐藏上下文。敌人、同伴和反应策略继续由 ruleset 拥有。

`engine/staged_transaction.py` 负责隔离复制与并发复核：复用 aggregate publication contract，创建独立锁，保留完整日志和新加入席位，不把运行实例当存档载入而触发 ghost cleanup。`webui/services/hosted_combat.py` 在 stage 上执行同一 resolve/apply ladder；模型 I/O 期间不持 live authority/process/state lock。发布时再次核对完整保存投影、完整历史、对象/运行身份及所有模型连接快照；发生变化整段拒绝，不覆盖并发编辑。保存失败或取消恢复整段，派生记忆在保存后投影。

一次自动段至多 8 次模型调用，总决策窗口 30 秒、单次至多 20 秒；失败席位在该自动段不再请求模型，继续使用原合法确定性策略。未知 ID、额外 damage 等字段、Markdown 和自由文本均拒绝。服务端骰子、动作经济和 256 步终止保护不变。连续移动测试发现已消耗反应的敌人仍创建借机攻击决定，已在 D&D movement owner 修正并覆盖，不创建无法结算的额外反应。

正常 HTTP intent、控制权切换和重试复用该自动段。叙事 Director 在 AI 玩家回合前停止同步自动段，`commands/hosted_combat.py` 的单运行调度器等原回合锁释放后再继续；关服取消调度器并关闭 client。前端轮询安全 outcome，后台战斗结算后刷新对局；战斗显示自己的 combat round，不把叙事 round 当战斗回合。

叙事重试仍按原流程补全部缺失 AI 行动。战斗重试只接受轮到该席位的请求，不套用叙事行动队列/真人交齐门禁；已结算动作不被删除或重写。

## 验收

验收包含 provider 删除、能力不匹配、未来模块 schema、凭据脱敏、生成中的配置变化和锁等待、失败原因、保存失败恢复、重开/移除、多人既有门禁、预览权限；浏览器在桌面和手机实际完成保存、刷新及玩家投影验证。

战斗通过真实 D&D runtime 验证闪避选择、超时/恶意输出回退、合法武器/施法候选、控制/配置/重开/加入变化、并发段只提交一次、保存失败与取消、锁等待、叙事 Director 接管及连续移动终止。相关已有回归 366 项通过，后续功能/权限/战斗组合 97 项通过；新增回合编号与反应边界已补测，前端 14 项、TypeScript/ESLint 及桌面/手机 2 条浏览器链路通过。全量后端回归另行记录。
