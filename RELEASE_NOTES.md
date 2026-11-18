# Unreleased（下一预览版草拟，发布时并入正式段落）

## 中文

- **dnd2024 内容包破坏性变更（#785）**：武器条目现要求显式 `two_handed` 与 `loading` 键，自带的 dnd2024 模板已全量补齐。缺这两个键的自制或魔改 dnd2024 combat catalog 在升级后会**装载失败**并点名具体武器 id——这是有意的 fail-closed 行为，不会静默跳过。请为自定义武器补齐这两个布尔键后重新装载。

## English

- **Breaking change for dnd2024 content packs (#785)**: weapon entries now require explicit `two_handed` and `loading` keys; the bundled dnd2024 templates are fully updated. Custom or modified dnd2024 combat catalogs missing these keys will **fail to load** after the upgrade, naming the offending weapon ids — intentional fail-closed behavior, nothing is silently skipped. Add both boolean keys to custom weapons and reload.

---

# DiceFrame v3.3.0-beta.3

> 这是 3.3.0 的**预览版（beta）**。默认稳定频道不会收到它；请在「设置 → 预览版更新」开启后检测。重要存档建议先备份。扩展中心、创作工坊与外部规则运行时均为本批实验性能力，欢迎在预览频道反馈。本版重点修复 DF Agent / 创作工坊的预算与上下文问题。

3.2.1 之后的扩展平台版本：统一扩展中心与模块商店、创作工坊、外部规则运行时平台、本地向量模型管理、后端提示多语言第一批，以及一批可靠性与更新链路修复。

扩展平台批次：统一扩展中心与重做的模块商店、创作工坊、实验性的外部规则运行时平台、本地向量模型管理，以及后端提示多语言的第一批。同时包含插件安装原子性与一批可靠性修复。

## 中文

### 扩展平台（新增）

- **统一扩展中心**：插件、模块与内容包的安装、启停、依赖规划集中到一处；多包组安装原子化，崩溃后自动恢复到一致状态，安装留有可审计收据。
- **模块商店重做**：搜索、分类、精选货架、恢复判定、就地更新；市场端「暂停 / 下架」状态实时生效，不受离线缓存影响。
- **外部规则运行时（实验性）**：规则系统可以打包为 `.dfplugin`（stdio 子进程）接入 DiceFrame——平台侧已提供建局绑定、权威意图、敌方 / AI 席自动行动、自由叙事检定与声明式面板的完整接缝；首批第三方规则插件包将陆续上架。内置规则（D&D 2024 等）行为不变。
- **顺序 / 组合动作**：服务端权威的顺序多检定与组合技能计划接缝就位（SK-01），规则运行时声明能力后才会出现对应入口。
- **角色创建规则选择（RC-01）**：创建角色时独立选择规则，加入对局时做规则兼容校验，不兼容的角色卡会被明确拒绝。

### 创作工坊（新增）

- **Creator 工程**：项目 revision、校验、预览、构建与提交回执；AI 助手改为流式确认（SSE），支持自动批准开关。

### 模型接入（新增）

- **本地向量模型管理**：内置模型目录支持在线安装（主源 + 镜像回退，走系统代理）、按模型 profile 隔离向量、换模型或数据损坏时后台自动重建索引、手动重建入口。
- 本地模型文件损坏或缺失不再阻止启动：向量能力本次停用并提示，修好后重开即恢复。
- 模型安装接口补齐管理员校验。

### 多语言（新增）

- **后端提示接入文案表（第一批）**：`src/webui` 返回给移动端、机器人与第三方调用方的提示按请求 `Accept-Language` 返回对应语言；不带该头时仍为简体中文，机器人脚本行为不变。

### 更新链路与可靠性修复（重要）

### DF Agent / 创作工坊修复（本 beta 重点）

- **不再频繁打断**：单段 token 预算触顶时自动整理上下文并接续执行（同一对话、不重做已完成步骤），「继续任务」退为兜底；任务总预算不因续段放大，界面显示真实累计消耗。
- **上下文体积有界化**：对话历史、工具观察与初始目录压缩到有界视图，长任务不再因重发全量上下文几轮烧光预算；ID、草稿、审批、错误码等结构字段不截断。
- **供应商输出上限自学习钳制**：max_tokens 超出模型上限（如 DeepSeek 的 393216）时自动钳制重试，不再静默失败成空回复。
- 工具观察压缩保持合法 JSON 与分页元数据（name 集合、total/offset/has_more/next_offset），能力发现契约不破坏。

- **Docker 更新包依赖缺失**：beta.1 的更新包缺 `python-i18n`，候选进程启动即崩、更新必回滚并报「候选进程已退出」。已补齐锁文件并新增「锁必须覆盖全部依赖，否则拒绝出包」的构建校验；重复应用当前版本不再杀掉健康进程重启。
- **内置 Fabula Ultima 规则已撤回**：按外部化路线（`.dfplugin` 插件包）执行，主仓不再内置第三方规则数值表。
- 首次启动时「同意用户协议」不再被版本校验闸门短暂拦截。
- **购买体验闭环**：玩家想买但没人报价时，不再「不扣钱也不给东西」地静默落空——游玩页出现「待报价」卡片，GM 一键预填开单、玩家可自助出价（`我要出价`）；确认前永远不扣款不发货的既有安全语义不变。模型侧同时加固：任何人在叙事里报过价即视为已定价，不再轻易降级。

### 可靠性（修复）

- **插件安装**：安装日志恢复与发布互斥，并发重扫 / 卸载不再可能回滚进行中的安装。
- **Creator worker**：超时任务的回执不再卡在 `running`；崩溃后的孤儿回执在启动时自动回收。
- **外部运行时**：收据按游戏 / Run 隔离，重放返回显式 `replayed` 标记；stdio 强制 UTF-8（修复 Windows 中文规则数据必炸的乱码问题）；账本防篡改校验。

### 升级说明

- 本版本包含记忆向量库迁移（v3→v5，纯加列）：旧数据全部保留，旧模型 / 异 profile 的向量自动隔离并在后台重建，可直接从 3.2.1 升级。
- 升级后若启用本地向量模型，首次启动会在后台重建一次索引，期间语义召回会降级为关键词召回。
- **安装版**：运行新的 `setup.exe` 完成覆盖安装；**便携版**：解压覆盖或用更新器升级；**Docker**：镜像同步更新标签。
- de / ja / ru 界面下本版新增文案暂时回退英文，由社区翻译补齐。

## English



> This is the **beta preview** of 3.3.0. The stable channel does not receive it; enable *Preview updates* in Settings to see it. Back up important saves first. This beta focuses on DF Agent / Creator budget and context fixes. The extension center, Creator studio and external ruleset runtimes are experimental — feedback on the preview channel is welcome.

The extension-platform release after 3.2.1: a unified extension center with a rebuilt module store, the Creator studio, an experimental external ruleset runtime platform, local embedding model management, and the first batch of localized backend messages — plus atomic plugin installs and a round of reliability fixes.

### Extension platform (new)

- **Unified extension center**: installing, enabling and dependency planning for plugins, modules and content packs in one place; group installs are atomic with crash recovery and auditable receipts.
- **Rebuilt module store**: search, categories, featured shelf, recovery handling and in-place updates; marketplace `paused` / `withdrawn` statuses apply live and are not bypassed by the offline cache.
- **External ruleset runtimes (experimental)**: rule systems can ship as `.dfplugin` packages (stdio child processes). The platform now provides the full seams — save binding, authoritative intents, enemy / AI-seat automatic turns, free-text check resolution and declarative panels; the first third-party ruleset packages will follow. Built-in rules (D&D 2024 and friends) are unchanged.
- **Sequential / composite actions**: server-authoritative seams for sequential multi-check and composite-skill plans (SK-01); entries only appear when a runtime declares the capability.
- **Character-creation rule choice (RC-01)**: pick the rule independently at character creation; joining a game validates compatibility and incompatible cards are rejected explicitly.

### Creator studio (new)

- **Creator projects**: revisions, validation, preview, build and submission receipts; the AI assistant moved to streaming confirmations (SSE) with an auto-approve switch.

### Models (new)

- **Local embedding model management**: install from the built-in catalog (primary + mirror fallback, via system proxy), per-profile vector isolation, background rebuild on model switch or corruption, plus a manual rebuild entry point.
- A broken or missing local model no longer blocks startup: the embedding capability is disabled for the run with a clear warning.
- The model install route now requires owner authentication.

### Localization (new)

- **Backend messages, first batch**: messages returned to mobile clients, bots and third-party callers from `src/webui` now follow the request's `Accept-Language`; without the header they stay in Simplified Chinese, so bot scripts see no change.

### Update-path and reliability fixes (important)

### DF Agent / Creator fixes (the focus of this beta)

- **No more constant interruptions**: when a segment's token budget trips, the agent compacts context and continues automatically within the same conversation (finished steps are not redone); the task total budget never grows via continuations, and the UI shows real cumulative usage.
- **Bounded context growth**: conversation history, tool observations and the initial catalog are compacted to a bounded view; structural fields (IDs, drafts, approvals, error codes) are never truncated.
- **Provider output ceilings are self-learned**: a max_tokens beyond the model's range (e.g. DeepSeek's 393216) is clamped and retried instead of failing silently into empty replies.
- Tool observations compact into valid JSON that preserves pagination metadata (name sets, total/offset/has_more/next_offset).

- **Docker update package dependency**: the beta.1 package shipped without `python-i18n`; every candidate crashed on import, so in-app Docker updates always rolled back with "candidate process exited". The lock is fixed and the build now refuses to ship unless it covers every requirement; re-applying the running version no longer restarts a healthy process.
- **Built-in Fabula Ultima ruleset reverted**: FU stays external (`.dfplugin` package) per the externalization decision; third-party rule data no longer ships in the main repo.
- Accepting the user agreement on first boot is no longer briefly blocked by the update-validation gate.
- **Purchase loop closed**: when a player wants to buy something but nobody has quoted a price, the purchase no longer silently does nothing (no charge, no item) — the play screen shows an "awaiting quote" card where the GM can pre-fill an order in one click and the player can name their own price. The existing safety contract (nothing charged or granted before confirmation) is unchanged. The model prompt is also hardened: any price a human stated in narration now counts as established.

### Reliability (fixes)

- **Plugin installs**: journal recovery is serialized with publication; a concurrent rescan / uninstall can no longer roll back an in-flight install.
- **Creator workers**: timed-out jobs no longer leave receipts stuck in `running`; orphaned receipts are recovered at startup.
- **External runtimes**: receipts are scoped per game / run with an explicit `replayed` marker; stdio is pinned to UTF-8 (fixing garbled Chinese rule payloads on Windows); the event ledger is content-checked against tampering.

### Upgrade notes

- Includes the memory vector-store migration (v3→v5, additive columns only): existing rows are kept, vectors from other profiles are isolated and rebuilt in the background; upgrading from 3.2.1 directly is supported.
- If a local embedding model is enabled, the first start after upgrade rebuilds the index in the background; semantic recall falls back to keyword recall meanwhile.
- **Installer**: run the new `setup.exe`; **portable**: unpack over the old directory or use the updater; **Docker**: tags update together.
- New strings fall back to English on de / ja / ru until community translations land.

## Downloads

- Windows 安装包 / Windows installer：`DiceFrame-v3.3.0-beta.3-windows-setup.exe`
- 便携版 / Portable：`DiceFrame-v3.3.0-beta.3-windows-portable.zip`
- Docker：`diceframe`（`3.3.0-beta.3` 标签；beta 不打 `latest`）
