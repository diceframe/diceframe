# DiceFrame v2.5.1

> 稳定版。v2.5.1 延续 v2.5.0 的功能，并统一购买提案、AI 行动识别、多人结算展示与图像生成配置。重要战役升级前，请先备份完整的 `data/` 文件夹。

## 中文

### 冒险与 D&D 2024

- **D&D 2024 专业玩法**：完整的角色创建、角色资料、战役记录、休息/升级、权威战斗与统一游戏时间线现已提供。战斗仍在同一条故事流中进行，不会把叙事和结算拆成两套游戏。
- **统一对局与 AI GM**：剧情、公共消息、行动输入、队伍状态与 GM 控制台使用同一条对局流程；AI GM 可在玩家行动后推进剧情、收集多人行动、处理检定并唤醒符合当前节点的遭遇，GM 仍可随时裁定或接管。
- **冒险包正式可用**：冒险包负责剧情流程、场景、NPC、地图位置与专属遭遇；世界书仍负责世界设定和长期 lore。两者可以组合，但不会互相覆盖。
- **更好用的冒险编辑器**：编辑器按“概览 / 流程 / 遭遇 / 预览 / JSON”分区，提供可视化流程、明确的起点、分支与可达性检查，以及步骤与遭遇的绑定。
- **结构化遭遇和怪物**：可直接编辑遭遇难度、怪物、HP、AC 与攻击动作。服务端会校验数据范围，阻止不完整或无效的战斗配置进入对局。
- **AI 生成可控草稿**：AI 可以从自然语言起草冒险；在编辑器中只生成当前所在分区的概览、流程或遭遇内容。结果先进入草稿，只有经过人工检查和冒险包校验后才会保存。
- **更清楚的战斗反馈**：公共战斗历史会标出最新记录与结算结果；死亡豁免、回合反馈和敌方行动的展示更容易追踪。重开/重置不会携带上一局的会话派生记忆或旧战斗状态。
- **叙事视角与语音输入**：创建对局时可选择第一或第三人称叙事，GM 也可以在对局中调整；在 HTTPS 或 localhost 下，可配置 OpenAI 兼容的语音识别模型，将麦克风输入转成行动文本。

### 世界书、内容与多人游玩

- **世界书可见性管理**：可按 GM、全队或指定角色设置 lore 可见性，并用视角检查器确认每名玩家真正能看到什么。AI 生成世界书时也受到同一套可见性边界约束。
- **世界图库**：世界书支持封面、自定义排序和分页浏览，管理大量世界时更容易定位内容。
- **桌边协作**：新增全队桌边交流与玩家向 GM 提问的入口；玩家提问保持只读边界，不会绕过 GM 权限或直接改写权威状态。
- **内容工作区**：世界书、规则、角色与管理入口的组织更清晰，窄屏下的导航和常用编辑操作也进行了适配。

### 模型、连接与运维

- **模型目录与主模型**：可以在模型目录中直接指定主模型；写入失败时会回滚，避免界面显示与实际配置不一致。
- **更清晰的连接安全说明**：设置页补充本地 HTTPS、局域网/外网访问、证书与 Android 连接的说明，并改善相关移动端布局。
- **运行日志与诊断**：运行日志可持久化与导出；需要排查时，内置助手可以在明确授权后阅读受限、脱敏的 DiceFrame 运行诊断上下文。
- **Docker 托管更新首次发布**：容器可下载并校验应用更新包，经过健康检查与观察期后切换版本；失败时可回到上一应用版本，业务数据仍独立保留。

### 本版重点更新

- **AI 行动层与购买提案**：GM planner 可从中文、英文、日文等自然语言行动识别购买意图，并生成付款人待确认的购买提案；AI 只负责理解，不能直接扣款或发货。
- **付款确认安全边界**：购买统一为单一提案状态机，只有报价中的付款人确认后才会扣款和发放物品；未明确说出的价格不会被模型自行推断。
- **系统结算信息分层**：扣款、奖励和物品发放显示为全队可见的系统结算卡，不再混入 GM 叙事；私聊和私人提问仍保持私密。
- **经济状态可靠性**：补强回滚、并发写入和重复提交保护，并将内存投递 outbox 从经济模块中拆出，降低重复结算风险。
- **普通奖励自动结算**：上限内（默认 50 金币，可配置）的单人金币奖励在回合完成后自动到账，不再阻塞整桌等待 GM 确认；超额奖励或关闭开关时保持原有的 GM 确认流程。
- **重置后快速重建与身份恢复**：重置后 GM/单人游玩页提供直接创建角色入口；玩家从连接注册后返回旧游玩地址时会自动恢复本地玩家身份，避免被误判为未加入本局。
- **意图多语言**：意图识别模式迁移到语言资源文件，日文局的「剣を買います」等购买句式现在可以正确识别。
- **MiniMax 图像生成**：新增显式 `minimax` provider，支持 `image-01` 模型及对应的尺寸、响应和 Base64 校验。

### 修复、兼容与更新可靠性

- **世界书批量导入**：超过 50 条的条目导入改为一次批量请求完成，不再触发写操作频控而中途失败（#213）；部分失败会按条目报告。
- **图像生成分步配置**：可以先启用图像生成、稍后再选择服务商和模型；配置完整前服务保持不可用而不是报错，原有 URL 安全校验不变（#218）。
- **重置后状态**：修复重置会清空玩家后缺少角色创建入口的问题；旧游玩地址的玩家身份恢复不再误判。
- **多数量购买结算**：明确区分单价和总价，购买多瓶/多个相同物品时按数量正确扣款并发货。
- **购买提案迁移**：旧购买请求和订单字段迁移时安全丢弃，不影响已经结算的余额、物品和交易流水。
- **规则边界**：D&D 专属自动化继续限制在 D&D 运行时边界内，不会自动改变传统规则、CoC、赛博朋克或 generic d20 的玩法。

### 升级提示

- 从 **v2.3.2** 升级时，请先备份完整 `data/`。便携版或源码包更新应用文件时，不要覆盖、删除或手动清空你的 `data/`。
- 首次启动会在可安全判断的前提下升级内置模板与兼容数据；已经被用户修改的世界书或内容不会被系统模板无条件覆盖，无法安全判断时会保留原样。
- 已绑定到存档的冒险包继续保持只读、不可删除，确保重开时始终使用同一份已验证的内容。
- “重开”和“重置”会清理该局的会话派生记忆，避免叙事串到新局；它们不会删除其它存档。
- 奖励自动结算默认开启（上限 50 金币）；如需回到全部 GM 确认模式，可在配置中关闭 `economy_auto_reward_enabled`。
- 存档迁移到 schema 7 时会移除旧的 `purchase_requests` 和 `purchase_orders` 字段；未确认的旧购买请求不保证保留。
- Docker 托管更新包当前支持 `linux-amd64`。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.1-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.1-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.1-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 统一校验；重要战役建议保留旧版程序与数据备份，便于回退。

## English

### Adventures and D&D 2024

- **D&D 2024 advanced play**: Full character creation, character records, campaign tracking, resting and advancement, authoritative combat, and the unified game timeline are now available. Combat remains in the same story flow rather than becoming a separate game.
- **Unified play and AI GM**: Narrative, public messages, action input, party state, and the GM console use one game flow. After player actions, the AI GM can advance the story, collect multiplayer actions, resolve checks, and awaken encounters that fit the current node, while the GM can always adjudicate or take over.
- **Adventure packages are ready for play**: Packages provide story flow, scenes, NPCs, map locations, and package-specific encounters. Worldbooks continue to provide setting and long-term lore; the two can be combined without overwriting each other.
- **A clearer adventure editor**: Overview, Flow, Encounters, Preview, and JSON sections provide a visual flow, an explicit start node, branch and reachability diagnostics, and step-to-encounter binding.
- **Structured encounters and monsters**: Edit encounter difficulty, monsters, HP, AC, and attacks directly. Server-side validation blocks incomplete or invalid combat data from reaching a game.
- **Controlled AI drafts**: AI can draft an adventure from natural language. Inside the editor it generates only the active Overview, Flow, or Encounters section. Results remain drafts until reviewed and validated before saving.
- **Clearer combat feedback**: The public combat history marks the latest record and resolution results; death saves, turn feedback, and enemy actions are easier to follow. Restart and reset no longer carry session-derived memory or stale combat state into a new game.
- **Narrative perspective and voice input**: Choose first- or third-person narration when creating a game, and let the GM adjust it during play. Under HTTPS or localhost, an OpenAI-compatible speech-recognition model can turn microphone input into action text.

### Worldbooks, content, and table play

- **Worldbook visibility management**: Set lore visibility for the GM, the party, or named characters, then verify what each player can actually see with the perspective inspector. AI-generated lore follows the same visibility boundaries.
- **World gallery**: Worldbooks support cover images, custom sorting, and pagination for easier management of larger libraries.
- **Table collaboration**: Party table talk and player-to-GM questions are available from the game experience. Player questions remain read-only and cannot bypass GM authority or alter authoritative state.
- **Content workspaces**: Worlds, rules, characters, and management entry points are organized more clearly, with improved narrow-screen navigation and editing actions.

### Models, connectivity, and operations

- **Provider catalog and main model**: Choose the main model directly from the catalog. Configuration writes roll back on failure so the UI and stored configuration cannot silently diverge.
- **Clearer connection guidance**: Settings now explain local HTTPS, LAN/public access, certificates, and Android connectivity, with improved mobile layouts for related controls.
- **Runtime logs and diagnostics**: Runtime logs can be retained and exported. With explicit permission, the built-in assistant can inspect a bounded, redacted DiceFrame diagnostic context for troubleshooting.
- **First managed-Docker update release**: Containers can download and verify an application update package, switch versions after health checks and a probation period, and return to the prior application version on failure while business data remains separate.

### Included in this release

- **AI action layer & purchase proposals**: The GM planner recognizes purchase intent in Chinese, English, Japanese, and other natural-language actions, then creates a payer-confirmed proposal. The AI understands actions but cannot charge or deliver items directly.
- **Payer-confirmation boundary**: Purchases use one proposal state machine. Only the quoted payer can confirm a charge; prices that were not explicitly stated are never invented by the model.
- **System settlement cards**: Charges, rewards, and item delivery appear as party-visible system settlement cards instead of GM narration. Private messages and questions remain private.
- **Economy reliability**: Rollback, concurrent writes, and duplicate submissions are guarded, and the memory delivery outbox is separated from the economy module.
- **Automatic reward settlement**: Plain single-recipient gold rewards within the configured cap (default 50, configurable) settle automatically after each round instead of blocking the table for a GM click. Over-cap rewards and the off switch keep the original confirmation flow.
- **Post-reset rebuild & identity restore**: After a reset, the GM/solo play page offers a direct character-creation entry, and players returning to an old play URL after joining restore their local identity instead of being treated as not joined.
- **Intent multilingual support**: Intent trigger patterns moved into language resource files; Japanese phrases such as「剣を買います」are now recognized.
- **MiniMax image generation**: Added an explicit `minimax` provider with the `image-01` model and provider-specific size, response, and Base64 validation.

### Fixes, compatibility, and update reliability

- **Lorebook batch import**: Importing more than 50 entries now completes in a single batched request instead of tripping the write flood guard mid-import (#213); per-entry failures are reported individually.
- **Staged image generation**: Enable image generation first and pick the provider/model later. Before the configuration is complete the service reports unavailable cleanly instead of erroring, with URL security checks unchanged (#218).
- **Post-reset state**: Fixed the missing character-creation entry after a reset cleared the roster; player identity restoration for old play URLs no longer misfires.
- **Multi-quantity purchase settlement**: Unit and total prices are distinguished, so buying multiple copies charges and delivers the requested quantity correctly.
- **Purchase proposal migration**: Legacy purchase request and order fields are safely removed during migration without changing already-settled balances, items, or transaction history.
- **Ruleset boundaries**: D&D-specific automation remains inside the D&D runtime boundary and does not automatically alter traditional rules, Call of Cthulhu, cyberpunk, or generic d20 play.

### Upgrade notes

- When upgrading from **v2.3.2**, back up the complete `data/` directory first. When replacing application files with a portable or source package, do not overwrite, delete, or manually clear `data/`.
- On first startup, bundled templates and compatibility data are upgraded only when this is safe to determine. User-edited worldbooks and content are never unconditionally overwritten; uncertain cases are preserved unchanged.
- Adventure packages bound to saves remain read-only and undeletable so restarts always use the same validated content.
- Restart and reset clear session-derived memory for that game to prevent narrative carry-over; they do not delete other saves.
- Automatic reward settlement is enabled by default (cap 50 gold); disable `economy_auto_reward_enabled` in the configuration to return to full GM confirmation.
- Save migration to schema 7 removes legacy `purchase_requests` and `purchase_orders`; unconfirmed legacy purchase requests are not guaranteed to survive.
- Managed Docker updates currently support `linux-amd64`.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.1-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.1-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.1-docker-update-linux-amd64.zip`
- Verify downloads with the Release `SHA256SUMS`. For important campaigns, keep the prior program version and a data backup so rollback remains possible.
