# DiceFrame v3.3.0-beta.1

> 这是 3.3.0 的**预览版（beta）**。默认的稳定频道不会收到它；请在「设置 → 预览版更新」里开启后才会检测到本版本。重要存档建议先备份。扩展中心、创作工坊与外部规则运行时均为本批实验性能力，欢迎在预览频道反馈。

扩展平台批次：统一扩展中心与重做的模块商店、创作工坊、实验性的外部规则运行时平台、本地向量模型管理，以及后端提示多语言的第一批。同时包含插件安装原子性与一批可靠性修复。

## 中文

### 扩展平台（新增）

- **统一扩展中心**：插件、模块与内容包的安装、启停、依赖规划集中到一处；多包组安装原子化，崩溃后自动恢复到一致状态，安装留有可审计收据。
- **模块商店重做**：搜索、分类、精选货架、恢复判定、就地更新；市场端「暂停 / 下架」状态实时生效，不受离线缓存影响。
- **外部规则运行时（实验性）**：规则系统可以打包为 `.dfplugin`（stdio 子进程）接入 DiceFrame——平台侧已提供建局绑定、权威意图、敌方 / AI 席自动行动、自由叙事检定与声明式面板的完整接缝；首批第三方规则插件包将陆续上架。内置规则（D&D 2024 等）行为不变。
- **顺序 / 组合动作**：服务端权威的顺序多检定与组合技能计划接缝就位（SK-01），规则运行时声明能力后才会出现对应入口。
- **角色创建规则选择（RC-01）**：创建角色时独立选择规则，加入对局时做规则兼容校验，不兼容的角色卡会被明确拒绝。
- **内置 Fabula Ultima 兼容规则**：第一方收录社区维护的 FU 兼容规则（命刻、物语点、非 d20 骰制与成长），与外部运行时路线共用同一套引擎接缝；存档往返与命刻键冲突有回归保护。

### 创作工坊（新增）

- **Creator 工程**：项目 revision、校验、预览、构建与提交回执；AI 助手改为流式确认（SSE），支持自动批准开关。

### 模型接入（新增）

- **本地向量模型管理**：内置模型目录支持在线安装（主源 + 镜像回退，走系统代理）、按模型 profile 隔离向量、换模型或数据损坏时后台自动重建索引、手动重建入口。
- 本地模型文件损坏或缺失不再阻止启动：向量能力本次停用并提示，修好后重开即恢复。
- 模型安装接口补齐管理员校验。

### 多语言（新增）

- **后端提示接入文案表（第一批）**：`src/webui` 返回给移动端、机器人与第三方调用方的提示按请求 `Accept-Language` 返回对应语言；不带该头时仍为简体中文，机器人脚本行为不变。

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

> This is the **beta preview** of 3.3.0. The stable channel does not receive it; enable *Preview updates* in Settings to see it. Back up important saves first. The extension center, Creator studio and external ruleset runtimes are experimental — feedback on the preview channel is welcome.

The extension-platform batch: a unified extension center with a rebuilt module store, the Creator studio, an experimental external ruleset runtime platform, local embedding model management, and the first batch of localized backend messages — plus atomic plugin installs and a round of reliability fixes.

### Extension platform (new)

- **Unified extension center**: installing, enabling and dependency planning for plugins, modules and content packs in one place; group installs are atomic with crash recovery and auditable receipts.
- **Rebuilt module store**: search, categories, featured shelf, recovery handling and in-place updates; marketplace `paused` / `withdrawn` statuses apply live and are not bypassed by the offline cache.
- **External ruleset runtimes (experimental)**: rule systems can ship as `.dfplugin` packages (stdio child processes). The platform now provides the full seams — save binding, authoritative intents, enemy / AI-seat automatic turns, free-text check resolution and declarative panels; the first third-party ruleset packages will follow. Built-in rules (D&D 2024 and friends) are unchanged.
- **Sequential / composite actions**: server-authoritative seams for sequential multi-check and composite-skill plans (SK-01); entries only appear when a runtime declares the capability.
- **Character-creation rule choice (RC-01)**: pick the rule independently at character creation; joining a game validates compatibility and incompatible cards are rejected explicitly.
- **Built-in Fabula Ultima-compatible ruleset**: the community-maintained FU-compatible ruleset (clocks, fabula points, non-d20 dice and growth) ships first-party, sharing the same engine seams as the external runtime route; save round-trips and clock-key collisions have regression coverage.

### Creator studio (new)

- **Creator projects**: revisions, validation, preview, build and submission receipts; the AI assistant moved to streaming confirmations (SSE) with an auto-approve switch.

### Models (new)

- **Local embedding model management**: install from the built-in catalog (primary + mirror fallback, via system proxy), per-profile vector isolation, background rebuild on model switch or corruption, plus a manual rebuild entry point.
- A broken or missing local model no longer blocks startup: the embedding capability is disabled for the run with a clear warning.
- The model install route now requires owner authentication.

### Localization (new)

- **Backend messages, first batch**: messages returned to mobile clients, bots and third-party callers from `src/webui` now follow the request's `Accept-Language`; without the header they stay in Simplified Chinese, so bot scripts see no change.

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

- Windows 安装包 / Windows installer：`DiceFrame-v3.3.0-beta.1-windows-setup.exe`
- 便携版 / Portable：`DiceFrame-v3.3.0-beta.1-windows-portable.zip`
- Docker：`diceframe`（`3.3.0-beta.1` 标签；beta 不打 `latest`）
