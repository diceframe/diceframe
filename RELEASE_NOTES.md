# DiceFrame v3.3.0

3.2.1 之后的扩展平台大版本：统一扩展中心与重做的模块商店、创作工坊（Creator Studio）、实验性的外部规则运行时平台、本地向量模型管理、DF Agent 全面重构（预算、记忆与文件附件）、官方 OpenAI GPT-6.2 原生支持、D&D 2024 大批内容与机制，以及一批可靠性与更新链路修复。稳定频道用户可直接从 3.2.1 升级。

## 中文

### 扩展平台（新增）

- **统一扩展中心**：插件、模块与内容包的安装、启停、依赖规划集中到一处；多包组安装原子化，崩溃后自动恢复到一致状态，安装留有可审计收据。
- **模块商店重做**：搜索、分类、精选货架、恢复判定、就地更新；市场端「暂停 / 下架」状态实时生效，不受离线缓存影响。
- **外部规则运行时（实验性）**：规则系统可以打包为 `.dfplugin`（stdio 子进程）接入 DiceFrame——建局绑定、权威意图、敌方 / AI 席自动行动、自由叙事检定与声明式面板接缝齐备。内置规则（D&D 2024 等）行为不变。
- **顺序 / 组合动作（SK-01）**：服务端权威的顺序多检定与组合技能计划；规则运行时声明能力后才会出现对应入口。
- **角色创建规则选择（RC-01）**：创建角色时独立选择规则，加入对局时做兼容校验，不兼容的角色卡被明确拒绝。

### 创作工坊（新增）

- **Creator 工程**：项目 revision、校验、预览、构建 `.dfplugin` 与投稿回执；AI 助手流式确认（SSE），支持自动批准。
- **AI 助手不再失忆**：会话记忆搬到服务端按会话持久化，超窗历史折叠为状态卡；最新指令始终钉在每次模型请求末尾——跨轮记得自己做过什么、你要求过什么。
- **工程状态卡**：每轮自动注入当前工程 revision / 文件清单 / 内容计数，AI 定向不再需要反复读取工程目录；重复读取会被守卫拦停并友好提示。
- **文件附件**：输入栏语音按钮左侧新增上传按钮（全局助手与创作工坊都有）——规则书、剧本、设定资料等文本文件（txt/md/json/csv/log，2MB）直接挂进会话，AI 分页读取全文。
- **安装到本机**：发布按钮旁新增一键把当前工程构建并安装进本机（与市场安装同一通道，含深度包校验），装完按惯例询问是否启用。
- **可构建脚手架**：官方冒险模板自带实体样例与 locale 覆盖，新建工程开箱即可通过预检并构建 `.dfplugin`；agent 改动后内容总览与文件面板实时刷新。

### DF Agent（重构）

- **用量账本**：每次真实 HTTP 请求按传输层入账，区分已上报 / 分项 / 未知用量——预算显示真实消耗，供应商漏报不再被当成 0 成本。
- **不再频繁打断**：单段预算触顶自动整理上下文接续执行，「继续任务」退为兜底；任务总预算不因续段放大。
- **上下文体积有界化**：历史、工具观察与能力目录压缩到有界视图，结构字段（ID / 草稿 / 审批 / 分页元数据）不截断且保持合法 JSON。
- **供应商输出上限自学习钳制**：max_tokens 超出模型上限时自动钳制重试，不再静默失败成空回复。

### 模型接入（新增）

- **官方 OpenAI GPT-6.2**：`gpt-6.2-astra` / `gpt-6.2-sol` 的连接测试、叙事与原生工具调用自动走 Responses API（仅官方端点白名单，`store: false` 无远端状态）；流式输出完整性校验，可见输出后中断不重试不重复。
- **本地向量模型管理**：内置目录在线安装（主源 + 镜像回退）、按 profile 隔离向量、损坏或换模型后台自动重建；模型缺失不再阻止启动。
- **npm 11+**：前端工程要求放宽至 Node 新版本自带的 npm，新环境不再报 Unsupported engine。

### D&D 2024（内容与机制）

- **SRD 5.2.1 参考文本**：全部 339 条法术描述、86 条冒险装备与 36 条工具（含毒师工具包）以 reference 目录收录（CC-BY-4.0，出处完整标注），不改任何战斗结算。
- **机制批次**：掷骰前后果与标记读者（F15/F18）、先攻选择 / 四组主动特性 / 引导神力 / 三项 capstone（F20–F24）、武器词条（`two_handed`/`loading`）、在手状态与 Light 追加攻击（F38）。
- **独立检定方案 fail-closed**：独立检定模型返回无效方案时回合并状态回滚，不再按「本轮无需检定」推进；畸形原生工具条目整批拒绝重试；被拒方案如实入账。

### 多语言（新增）

- **后端提示接入文案表（第一批）**：`src/webui` 返回给移动端、机器人与第三方调用方的提示按 `Accept-Language` 返回对应语言；不带该头仍为简体中文，机器人脚本行为不变。

### 更新链路与可靠性（修复）

- **Docker 更新包依赖**：更新包锁文件必须覆盖全部依赖否则拒绝出包；重复应用当前版本不再杀掉健康进程；beta.1 的 `python-i18n` 缺失问题已修复。
- **购买体验闭环**：玩家想买但无人报价时不再「不扣钱也不给东西」——游玩页出现「待报价」卡片，GM 一键开单、玩家可自助出价；确认前不扣款不发货的语义不变。
- **内置 Fabula Ultima 规则已外部化**：按 `.dfplugin` 路线执行，主仓不再内置第三方规则数值表。
- 插件安装日志恢复与发布互斥；Creator worker 超时回执不再卡死，孤儿回执启动时回收；外部运行时收据按游戏 / Run 隔离并防篡改，stdio 强制 UTF-8。
- 首次启动「同意用户协议」不再被版本校验闸门拦截；创作工坊最大化按钮修复；空 `manifest` 的旧工程构建不再误判。

### 升级说明

- 本版本包含记忆向量库迁移（v3→v5，纯加列），可直接从 3.2.1 升级；旧向量自动隔离并在后台重建，期间语义召回降级为关键词召回。
- **dnd2024 内容包破坏性变更**：武器条目现要求显式 `two_handed` 与 `loading` 键（自带模板已全量补齐）。缺键的自制 / 魔改 dnd2024 combat catalog 升级后会装载失败并点名武器 id——fail-closed 有意行为，请补齐两个布尔键后重载。
- **安装版**：运行新的 `setup.exe` 覆盖安装；**便携版**：解压覆盖或用更新器；**Docker**：`3.3.0` 与 `latest` 标签同步更新。
- 预览频道已装 beta.1–beta.3 的实例会正常收到 3.3.0（正式版版本号更高）。
- de / ja / ru 界面下本版新增文案暂时回退英文，由社区翻译补齐。

## English

The extension-platform release after 3.2.1: a unified extension center with a rebuilt module store, the Creator studio, an experimental external ruleset runtime platform, local embedding model management, a ground-up DF Agent rework (budget, memory and file attachments), native official OpenAI GPT-6.2 support, a large D&D 2024 content and mechanics batch, plus reliability and update-path fixes. Stable-channel users can upgrade directly from 3.2.1.

### Extension platform (new)

- **Unified extension center**: plugins, modules and content packs in one place; atomic group installs with crash recovery and auditable receipts.
- **Rebuilt module store**: search, categories, featured shelf, in-place updates; marketplace `paused`/`withdrawn` apply live.
- **External ruleset runtimes (experimental)**: rule systems ship as `.dfplugin` stdio packages — save binding, authoritative intents, enemy/AI-seat turns, free-text checks and declarative panels are all wired. Built-in rules unchanged.
- **Sequential / composite actions (SK-01)** and **character-creation rule choice (RC-01)** seams.

### Creator studio (new)

- **Creator projects**: revisions, validation, preview, `.dfplugin` build and submission receipts; streaming AI confirmations (SSE) with auto-approve.
- **The assistant no longer forgets**: session memory is server-side and persistent, older turns fold into a state card, and the latest user request is pinned to every model request — it remembers what it did and what you asked.
- **Project state card**: live revision / file list / content counts injected each turn; repetitive reads are guarded and explained in plain language.
- **File attachments**: an upload button left of the voice button (global assistant and Creator studio) attaches rulebooks, scripts and notes (txt/md/json/csv/log, 2MB); the agent paginates through the full text server-side.
- **Install locally**: one click next to Publish builds and installs the current project through the same host channel as marketplace installs, then offers to enable it.
- **Buildable scaffold**: the adventure template ships an entity stub and a locale overlay, so a fresh project passes pre-install checks out of the box; the overview and file panel refresh after agent edits.

### DF Agent (rework)

- **Usage ledger**: every real HTTP attempt is accounted at the transport layer with reported/components/unknown semantics — budgets show true cost, missing usage is never treated as free.
- **No more constant interruptions**: segment budgets continue automatically; the task total never grows via continuations.
- **Bounded context**: history, observations and the capability catalog compact to a bounded view while keeping structural fields and valid JSON.
- **Self-learned output ceilings**: out-of-range max_tokens is clamped and retried instead of failing silently.

### Models (new)

- **Official OpenAI GPT-6.2**: `gpt-6.2-astra`/`gpt-6.2-sol` connection tests, narration and native tool calls automatically use the Responses API (official-endpoint whitelist, `store: false`); streamed output is completeness-checked and never retried after visible output.
- **Local embedding model management**: catalog installs with mirror fallback, per-profile isolation, background rebuilds; a missing model no longer blocks startup.
- **npm 11+**: the frontend engine range accepts modern bundled npm.

### D&D 2024 (content & mechanics)

- **SRD 5.2.1 reference text**: all 339 spell descriptions, 86 adventure gear entries and 36 tools (incl. the Poisoner's Kit) as reference catalogs (CC-BY-4.0, attributed) — no combat resolution changes.
- **Mechanics batches**: pre-roll consequences and marker readers (F15/F18), initiative choice / four active-feature groups / Channel Divinity / three capstones (F20–F24), weapon traits (`two_handed`/`loading`), hands state and Light extra attacks (F38).
- **Dedicated check plans fail closed**: an invalid dedicated-model plan rolls the round back instead of advancing as "no checks"; malformed native tool batches are rejected and retried; rejected plans still record their paid usage.

### Localization (new)

- **Backend messages, first batch**: `src/webui` messages follow `Accept-Language`; without the header they stay Simplified Chinese.

### Update path & reliability (fixes)

- Docker update packages refuse to ship unless the lock covers every requirement; re-applying the current version no longer restarts a healthy process; the beta.1 `python-i18n` omission is fixed.
- **Purchase loop closed**: unpriced purchase intents surface as "awaiting quote" cards (GM one-click order, player self-quote) instead of silently doing nothing; nothing is charged or granted before confirmation.
- Built-in Fabula Ultima ruleset externalized to `.dfplugin`.
- Plugin install journal recovery serialized with publication; Creator worker receipts unstick; external-runtime receipts scoped and tamper-checked; stdio pinned to UTF-8.
- First-boot agreement no longer blocked by the update gate; the studio maximize button works; old projects with an empty manifest build correctly.

### Upgrade notes

- Includes the memory vector-store migration (v3→v5, additive columns); direct upgrades from 3.2.1 are supported and old vectors rebuild in the background.
- **Breaking change for dnd2024 content packs**: weapon entries now require explicit `two_handed` and `loading` keys (bundled templates updated). Custom catalogs missing them fail to load, naming the weapon ids — intentional fail-closed behavior; add both boolean keys and reload.
- **Installer**: run the new `setup.exe`; **portable**: unpack over the old directory or use the updater; **Docker**: `3.3.0` and `latest` tags update together.
- Preview-channel instances on beta.1–beta.3 receive 3.3.0 normally (stable version sorts higher).
- New strings fall back to English on de/ja/ru until community translations land.

## Downloads

- Windows 安装包 / installer：`DiceFrame-v3.3.0-windows-setup.exe`
- 便携版 / portable：`DiceFrame-v3.3.0-windows-portable.zip`
- Docker：`diceframe`（`3.3.0` 与 `latest` 标签）
