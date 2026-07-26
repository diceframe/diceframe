# DiceFrame v1.5.0

## 中文

### v1.5.0

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

### v1.4.2（上一版，紧邻发布）

本版本修复了推理模型输出截断时可能暴露思考过程、回滚后玩家页面未及时刷新等问题，并提高了默认叙事输出额度。

#### 修复

- 当兼容 OpenAI 的推理模型没有返回最终正文时，不再把 `reasoning_content` 当作叙事结果，避免内部推理过程出现在游戏正文中。
- 修复回滚到上一回合或同回合撤销行动后，玩家端仍停留在旧的提交、掷骰或角色状态的问题。
- 修复 NapCat 使用指南中的 `@bot` 指令显示不完整的问题。

#### 调整

- 默认叙事最大 Token 从 1024 提升至 2048；仅迁移仍使用旧默认值的配置，保留已有自定义值。
- 长叙事二次压缩的输出额度提高至 1024–2048 Token；设置页对应统计明确为“叙事 Token（含二次压缩）”。

## English

### v1.5.0

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

### v1.4.2 (previous release, published shortly before)

This release fixes cases where truncated reasoning-model responses could expose internal reasoning, improves player refresh after rollbacks, and raises the default narration budget.

#### Fixes

- When an OpenAI-compatible reasoning model returns no final content, DiceFrame no longer uses `reasoning_content` as narration. This prevents internal reasoning from appearing in the game text.
- Fixed player pages retaining stale submission, dice, or character state after rolling back to an earlier round or undoing actions in the same round.
- Fixed incomplete rendering of literal `@bot` commands in the NapCat guide.

#### Changes

- Raised the default narrative token limit from 1024 to 2048. Only configurations still using the old default are migrated; custom values are preserved.
- Raised the long-narration compression budget to 1024–2048 tokens and clarified the usage label as “Narrative Tokens (incl. compression)”.
