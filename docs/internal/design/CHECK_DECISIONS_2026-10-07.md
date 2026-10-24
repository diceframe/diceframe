# 检定申请与 GM 授予（本地整合分支）

R4 的骰制入口已实现；本文描述本地 `codex/vnext-integrated-baseline`，不把未合并实现写成 main 现状。

## 请求与 authority

玩家的行动文字和 action 字段不再授予优势／劣势／奖励骰／惩罚骰。检定规划器作为 GM 裁定、规则声明的协助及 D&D 状态效果继续有效。玩家可通过游玩页申请，并填写理由；GM 审核、授予、拒绝或清除授权。叙事回合范围内作用于该 PC 的行动检定，不把控制该 PC 的授权扩展到委派 Companion 检定。D&D 当前支持武器与法术攻击，反应攻击不继承主动攻击的授权。

`CheckModePolicyRuntime` 负责支持范围、scope 与公开掷骰前投影。Legacy adapter 使用 `round:<n>`；D&D 使用当前 state version 和 actor 的 scope，第一次规则操作结算后过期。`modules.check_modes`（schema 1）只保存申请和裁定。存档 36→37 只初始化新模块，不将旧行动文字追认成 GM 授权，也不改写已结算结果；未知模块 schema 原样保留、拒绝消费。

读取时玩家只能看到自己的申请和授权，GM 可审阅所有座位。GM 理由会随该角色的公开攻击检定展示。每次写入验证 viewer、座位、run、scope、revision、phase 和已准备检定门禁；持 authority/state gate 保存，失败或取消恢复 before image。回合已准备或正在判定时不可追加授权；暂停的已有回合可预先设置。移除座位与重开清除已支持 schema 的运行记录。

优势和劣势以来源合并，有任一相反来源即抵消，多个同方向来源不叠加更多骰。协助、GM 授予与自动效果组合时保留抵消事实。D&D 预览和最终结算共用来源逻辑，事件保存候选骰、采用结果和来源；公开记录在五语言 UI 显示。

## 产品与 transport

游玩页的独立 CheckModePanel 提供申请、GM 队列、审核理由、状态及掷骰前来源说明，支持五语言。编辑时捕获 run/scope/revision；切局丢弃旧响应，保存失败保留草稿。房主玩家预览只读；开启代操作后只能以该玩家权限申请，不能得到 GM 授权权限。

HTTP 为 `GET /games/{key}/check-modes` 与 `POST /games/{key}/check-modes/seats/{uid}/{request|grant|reject|clear}`。P2P 显式支持读和申请，host 使用已绑定 actor 并剥离身份与操作注入；GM 授予为房主本机操作，不能由 peer 申请升级。

## 受控 Push 与 Luck

先复现文字／字段自授予问题，再增加权限、并发过期、保存失败、重开、未来 schema、真实 D&D 预览对照结算和 P2P 注入覆盖。桌面、手机已通过真实 API 的申请→GM 审核→保存→刷新链路。

CoC 普通技能／属性检定失败后，可以接受原结果、消耗可用幸运或申请孤注一掷。玩家必须说明新的尝试方法，GM 告知具体失败后果，原检定玩家确认后才由服务端按已保存的阈值和骰制重掷。攻击、对抗、SAN、Luck、伤害与 Companion 检定不开放此流程；AI 托管席位不等待不存在的真人确认。已申请 Push 就关闭 Luck，重掷不能再 Push 或消耗幸运。原结果、采用结果、理由、后果与 revision 一起保存在该 CheckResult 的 `push` 字段；38 存档边界不追认／重放历史检定。未来 schema 拒绝消费。

独立 CheckDecisionService 在 authority/state gate 下隔离规划、保存失败／取消恢复完整 before image；重掷提交复核 run/round/check/revision 与玩家身份。Luck 扣除及 GM 强制推进也使用隔离保存，失败不重复收费。后台 Luck 超时和 GM 强制推进会记录关闭原因；待 Push 的存档重启后仍保持判定阶段。叙事只在全部 Luck／Push 选择完成后进入已有 round authority 链。Push 失败将用户已接受的后果作为约束传给 GM，不把“更严重”擅自变成伤害翻倍。旧 LLM PUSH 标签不再掷骰或写旧技能标记，模板也改为指引受控入口。

五语言骰子卡片支持申请、GM 风险／拒绝、玩家确认／接受失败及双结果记录；玩家预览只读，允许代操作时仍是玩家权限。P2P 只提供已绑定玩家的 request/confirm/decline；offer/reject 留在房主端。分享入口保持该玩家 identity，不因房主 cookie 恢复 GM 权限。

验证：后端全量 5624 项通过、1 项跳过；后续补测未知同轮决定与分享 identity 等边界。前端全量 720 项通过，`vue-tsc -b`、ESLint、Ruff 与真实 build 通过。桌面、手机真实请求→GM 风险→玩家确认→刷新保存链路均通过。

规则依据：[Chaosium 官方规则](https://cthulhuwiki.chaosium.com/rules/skill-rolls-and-difficulty-levels.html)。
