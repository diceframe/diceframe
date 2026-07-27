# DiceFrame v1.6.0

## 中文

### v1.6.0

本版本带来应用内自动更新与叙事流式输出，让版本升级与回合反馈都更顺畅。

#### 新功能

- **应用内版本更新**：设置页"版本更新"面板可检测新版本，下载 Windows 安装包并自动校验完整性，便于本地升级。Docker 部署会提示不支持自更新，请重建镜像升级。
- **叙事流式输出**：GM 叙述现在逐字流式显示，无需等待整段生成完毕，回合反馈更即时（WebUI）。
- **叙事生成提速**：优化长叙事的延迟摘要与默认额度配置，生成更流畅。

#### 修复

- LLM 叙事输出被截断时，自动提高输出额度并重试，减少空回复。
- 修复分享链接玩家无法接收流式叙事的问题。

### v1.5.0（上一版，紧邻发布）

本版本引入统一的检定（check）系统与 GM 指令流：掷骰与判定由系统权威生成，GM 专注叙事，玩家侧不再看到内部机制块。

#### 新功能

- **统一检定请求**：玩家用自然语言描述行动（如“悄悄上楼”）时，系统自动识别意图并生成规则无关的检定请求，按规则骰制（CoC 的 d100、DnD5e 的 d20）一次性掷骰。GM 不再自行编造骰值或成功等级。
- **GM 指令流**：GM 可直接调整玩家资源（如幸运、理智），即时生效且不进入公开行动队列；叙事性 GM 指令私有存储，不会触发检定检测或出现在玩家日志中。
- **优势/劣势**：DnD5e 规则下支持优势/劣势双骰取高/取低，并能从行动描述中识别相关情形。
- **叙事净化**：内部系统检定块不再泄漏到玩家可见的叙事文本，玩家只看到故事本身。
- **公开/内部日志分离**：公开日志自动过滤 GM 内部指令，内部日志保留全部信息便于复盘。

#### 调整

- GM 叙事行为更新：仅依据上下文中的系统检定块叙事，不得伪造骰值或检定块。
- 新增 `SAN`、`LUCK` 资源标签，供 GM 在状态块中调整理智与幸运。
- 前端行动编辑器、时间线与检定结果展示适配新检定流。

## English

### v1.6.0

This release adds in-app updates and streaming narration, making upgrades and round feedback smoother.

#### New Features

- **In-app version update**: The "Version Update" panel in Settings can detect new releases, download the Windows package, and verify its integrity automatically for easier local upgrades. Docker deployments report self-update as unsupported; rebuild the image to upgrade.
- **Streaming narration**: GM narration now streams token by token instead of appearing all at once, making round feedback more immediate (WebUI).
- **Faster narration generation**: Optimized deferred summary and default token budgets for smoother long-form narration.

#### Fixes

- When LLM narration output is truncated, the token budget is automatically raised and retried, reducing empty replies.
- Fixed share-link players not receiving streamed narration.

### v1.5.0 (previous release, published shortly before)

This release introduces a unified check system and GM command flow: dice and outcomes are now produced authoritatively by the system, letting the GM focus on narration while players no longer see internal mechanic blocks.

#### New Features

- **Unified check requests**: When a player describes an action in natural language (e.g. “sneak upstairs”), the system identifies the intent and generates a rule-neutral check request, rolling once according to the rule set’s dice system (d100 for CoC, d20 for DnD5e). The GM can no longer invent dice values or success levels.
- **GM command flow**: The GM can directly adjust player resources (e.g. Luck, Sanity) with immediate effect, without entering the public action queue; narrative GM directives are stored privately and never trigger check detection or appear in player logs.
- **Advantage/Disadvantage**: DnD5e now supports advantage/disadvantage with two dice taking the higher/lower, recognized from action descriptions.
- **Narration sanitization**: Internal system check blocks no longer leak into player-visible narration; players only see the story.
- **Public/internal log separation**: Public logs automatically filter GM internal directives, while internal logs retain everything for review.

#### Changes

- GM narration behavior updated: narrate only from the system check block in context; never fabricate dice values or check blocks.
- Added `SAN` and `LUCK` resource tags for the GM to adjust sanity and luck in state blocks.
- The frontend action composer, timeline, and check result display adapt to the new check flow.
