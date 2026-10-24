# Game Profile、阶段与日历验证（本地整合分支）

这是 `codex/vnext-integrated-baseline` 的已实现设计与验收记录。它不是 main 已合并架构，也没有把讨论稿中的全部游戏类型变成当前版本的交付承诺。音乐继续挂起。

## 玩家入口

创建页面点击“玩法模板”，输入 1–4 个角色名称，即可启动“潜入行动：封印宝库”或“生存：避难所五日”。无需 AI 服务商。创建完成后直接进入专用游玩页；从存档列表重新打开也会进入同一页。

页面显示当前阶段／日历、队伍资源、角色、合法行动和已结算记录。潜入模板额外显示行动时钟，生存模板显示天气与健康。第一个角色是 GM 席位，使用房主登录；其余角色可打开玩家分享页，只控制自己的席位。GM 可选择角色、推进阶段／日期以及切换真人／规则托管。托管按钮通过同一服务端合法行动入口执行一个有限动作，不调用 LLM。共同资源变化需要刷新后读取；当前不是实时推送房间。

这两种模板均为小型合作玩法验证。角色创建后固定；角色状态、历史与资源在本模板中均为全队公开。它们没有实现隐秘身份、任意角色编辑器、完整 Blades 规则、万能 DSL、用户自定义 Profile 或 P2P Profile 传输。既有 D&D、CoC、自由叙事与 P2P 游戏仍由原游玩页承接。

## 已实现玩法

| 模板 | 推进方式 | 可用机制 | 终局 |
|---|---|---|---|
| `core:demo-heist` v1 | 计划 → 行动 → 休整，每人每阶段两次行动 | 四项角色属性、d6 检定、准备／警报／战利品时钟、压力、伤势、工具、物资、阵营声望 | 行动阶段战利品达到 4 后，由 GM 推进以撤离；警报 4 时失败 |
| `core:demo-survival` v1 | 每人每日一次行动，由 GM 结算当天并推进 | 食物、水、物资、健康、天气、社区关系、基地建造、制作／使用医疗包 | 完成第五天结算且至少一人存活即成功；全员健康 0 则失败 |

潜入检定采用服务端 d6 + 对应属性 + 行动阶段的准备加成，达到 6 为成功。准备每两格加 1；潜行／强行突破会自动消耗工具包加 1。失败仍推进一格战利品，同时增加警报与压力；强行突破失败另增加伤势。领取工具消耗一份物资，休整中的恢复减少压力和伤势。压力 9 在行动阶段阻止行动；伤势 3 的角色不能行动。

生存搜寻与收集在 d6 ≥ 4 时获得更多资源；探索失败降低健康。制作医疗包消耗 2 物资，建造一级基地消耗 3 物资；进食休息消耗 1 食物并恢复 2 健康，医疗包恢复 3 健康并消耗道具。日结算每位存活角色消耗一份食物与水，不足扣 1 健康；天气掷 1 为风暴、2–3 为雨，其余晴朗。风暴在基地低于 2 时额外扣 1 健康。终局冻结在最后结算阶段／日期，不再允许重复结算。

以上为原创、有边界的演示规则，不将 UI 文案、外部资料数字或模型输出作为 mechanics。

## Owner 与契约

- `src/game_profiles/contracts.py` / `registry.py`：代码注册的 Profile ID、版本、推进方式、每阶段行动额度、有限 UI 区块，以及 runtime public contract。
- `heist.py` / `survival.py`：各自负责状态校验、合法行动、资源与胜负的领域结算。
- `service.py`：组合身份、门禁、合法动作、revision、控制权、存档与投影；通过显式依赖使用 registry/save/die，不传完整 WebAPI。
- `src/engine/modules/profile_run.py`：schema 1，固定 Profile 身份与领域状态。
- `src/engine/modules/profile_progression.py`：schema 1，phase/calendar、cycle/day 和各角色本阶段已行动次数；两种玩法共用推进机制。
- `projection.py`：只提供公开路由描述符。未知绑定显示 Profile 错误，不回退到叙事输入框；不泄露异常存档原始字段。
- `frontend-v2/src/features/game-profiles/`：统一的有限区块页面，根据 `ui_sections` 组合角色、时钟、资源和历史；创建入口与路由仅负责组合。五种界面语言只改显示文案。

入口先读取游戏的公开 routing descriptor，再为已绑定的 Profile 请求专用投影。旧局不探测受房间密码保护的 Profile 子路由，也不要求 P2P 多支持一个新协议。入口加入原 KeepAlive 边界；离开游玩页后回到同一身份／游戏保留已有页面，切换游戏或身份才重新判定，避免重建旧页面而丢掉草稿。

GameInstance 顶层仍使用当前 schema 38。新模块在旧存档中默认为未配置，旧 narrative_round 的 `round_number` 和结算时代含义保持原样。新模板的 round_number 保持 0，日历／阶段绝不写入叙事回合计数；旧 narrative 自动推进、自然语言行动和控制切换后的叙事补位在 Profile 局不执行。未知模块 schema、未知 Profile 版本、未匹配原角色表、非法历史／行动计数均拒绝写入，不猜测迁移。

公开 HTTP 契约：owner 的 `GET /api/game-profiles` 和 `POST /api/game-profiles/create`，成员的 `GET /api/games/{key}/profile`、`POST /api/games/{key}/profile/actions`。路由通过 public facade 委托，不穿透 `api._*`。玩家分享白名单只允许上述游戏读取与行动的精确方法／路径，仍遵守房间密码、入口关闭与 GM 席位限制。

行动必须携带 `expected_run_id`、`expected_revision`；角色行动还必须携带 `actor_uid` 与 `expected_control_revision`。服务端持 authoritative admission 和 instance lock，验证游戏仍在 registry、当前角色所有权、真人／AI 控制权和额度，然后在两个模块的隔离副本上结算，验证后保存。保存失败恢复两个模块；重复请求只成功一次。创建先保存再注册，保存失败不产生半个活跃游戏。

## 验证

- 后端完整阶段胜利、五日日历胜利、重新载入不重扣资源、角色／GM 越权、控制权过期、重复并发请求、旧 run ID、未知 schema、建造成本、风暴保护、医疗包使用、创建及行动保存失败均已覆盖。
- 真实 aiohttp + auth middleware + registry 验证分享读取、本人行动、跨角色／GM 操作拒绝、GM 身份冒用和精确白名单。
- 前端验证服务端合法动作与 run/revision/control fences、角色切换期间禁用操作、失败保持原状态、未知绑定不回退，以及旧／P2P 游戏不探测受保护 Profile 子路由。
