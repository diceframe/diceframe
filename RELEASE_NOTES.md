# DiceFrame v2.5.1-beta.3

> 预览版本。v2.5.1-beta.3 延续 v2.5.0 的功能，并补充经济结算、跨回合购买确认、报价生命周期与发布包顺序修复。重要战役升级前，请先备份完整的 `data/` 文件夹。

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

### 本预览版补充

- **经济结算回滚**：多笔晚结算按逆提交顺序恢复余额、金币、物品和堆叠数量；origin-round Swipe 不再把后续轮次的状态写回旧快照。
- **奖励资格**：开场、普通回合和 Swipe 都会拦截尚未有完成证据的条件性奖励，避免任务未完成就生成奖励提案。
- **跨回合购买确认**：报价会持久化到原始对局回合，只能由原付款人确认；确认时以已保存的价格和物品为准，并防止重复发货或误把普通拾取物识别成商品报价。

### 修复、兼容与更新可靠性

- **重开、重置和战斗状态**：修复了新开/重开对局可能带入旧会话记忆、死亡或战斗状态的问题；战斗历史、死亡豁免与敌方回合的公共反馈也经过补强。
- **冒险包确定性**：对局会固定冒险包的 identity、版本与内容摘要。内容缺失、被修改或与世界策略不匹配时会明确拒绝，而不是静默切换到另一套场景；流程编辑也会检查起点、分支、可达性和遭遇引用。
- **世界书可见性兼容**：兼容历史 `PUBLIC` / `公开` 等别名和逗号分隔的角色名单，避免公开 lore 在保存后被误判为 GM 私密；内置世界书仅在仍能证明是旧官方默认时才会升级。
- **模型配置写入**：修复主模型切换时写入失败仍显示新选择的问题；现在会回滚到已保存的配置。
- **内容包与界面修复**：导入内容包时会规范资源 ID；修复设置页在窄屏与恢复桌面宽度时的导航表现、语音识别错误横幅无法关闭等问题，并调整了资料库和移动端操作布局。
- **连接与更新链路**：为首次发布的 Docker 托管更新补齐了强制 HTTPS 健康检查、加密依赖和更新包校验；同时修复 Windows 便携版依赖锁校验。慢设备、较多存档或短暂网络波动下的启动等待与失败判断也更可靠。
- **规则边界**：D&D 专属自动化继续限制在 D&D 运行时边界内，不会自动改变传统规则、CoC、赛博朋克或 generic d20 的玩法。

### 升级提示

- 从 **v2.3.2** 升级时，请先备份完整 `data/`。便携版或源码包更新应用文件时，不要覆盖、删除或手动清空你的 `data/`。
- 首次启动会在可安全判断的前提下升级内置模板与兼容数据；已经被用户修改的世界书或内容不会被系统模板无条件覆盖，无法安全判断时会保留原样。
- 已绑定到存档的冒险包继续保持只读、不可删除，确保重开时始终使用同一份已验证的内容。
- “重开”和“重置”会清理该局的会话派生记忆，避免叙事串到新局；它们不会删除其它存档。
- Docker 托管更新包当前支持 `linux-amd64`。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.1-beta.3-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.1-beta.3-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.1-beta.3-docker-update-linux-amd64.zip`
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

### Included in this preview

- **Economy rollback**: multiple late settlements now restore balances, gold, items, and stacked quantities in reverse commit order; origin-round Swipe no longer projects later-round state into an earlier snapshot.
- **Reward eligibility**: opening, normal-round, and Swipe pipelines reject conditional rewards without completion evidence, so unfinished tasks do not create reward proposals.
- **Cross-turn purchase confirmation**: quotes persist with their originating run and payer; confirmation uses the stored amount and items, prevents duplicate delivery, and does not misclassify ordinary loot as a shop quote.

### Fixes, compatibility, and update reliability

- **Restart, reset, and combat state**: Fixed cases where a new or restarted game could inherit prior session memory, death state, or combat state. Public feedback for combat history, death saves, and enemy turns was also strengthened.
- **Deterministic adventure packages**: Games pin an adventure package identity, version, and content digest. Missing, modified, or world-policy-incompatible content is rejected explicitly rather than silently switching scenes. Flow editing also checks starts, branches, reachability, and encounter references.
- **Worldbook visibility compatibility**: Historical `PUBLIC` / localized public aliases and comma-separated character lists are accepted so public lore is not misclassified as GM-private when saved. Bundled worldbooks upgrade only when they can still be proven to be the old official default.
- **Model configuration writes**: Fixed main-model selection appearing to change when the configuration write failed; the UI now rolls back to the persisted selection.
- **Content package and UI fixes**: Content-package installs now normalize asset IDs. Settings navigation handles narrow screens and a return to desktop width correctly, dictation error banners can be dismissed, and library and mobile action layouts were refined.
- **Connectivity and update path**: The first managed-Docker update release includes forced-HTTPS health checks, required cryptography dependencies, and update-package validation; Windows-portable dependency-lock validation was also fixed. Startup waits and failure detection are more reliable on slower devices, installations with many saves, or brief network interruptions.
- **Ruleset boundaries**: D&D-specific automation remains inside the D&D runtime boundary and does not automatically alter traditional rules, Call of Cthulhu, cyberpunk, or generic d20 play.

### Upgrade notes

- When upgrading from **v2.3.2**, back up the complete `data/` directory first. When replacing application files with a portable or source package, do not overwrite, delete, or manually clear `data/`.
- On first startup, bundled templates and compatibility data are upgraded only when this is safe to determine. User-edited worldbooks and content are never unconditionally overwritten; uncertain cases are preserved unchanged.
- Adventure packages bound to saves remain read-only and undeletable so restarts always use the same validated content.
- Restart and reset clear session-derived memory for that game to prevent narrative carry-over; they do not delete other saves.
- Managed Docker updates currently support `linux-amd64`.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.1-beta.3-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.1-beta.3-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.1-beta.3-docker-update-linux-amd64.zip`
- Verify downloads with the Release `SHA256SUMS`. For important campaigns, keep the prior program version and a data backup so rollback remains possible.
