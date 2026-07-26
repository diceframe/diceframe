# DiceFrame v1.4.2

## 中文

本版本修复了推理模型输出截断时可能暴露思考过程、回滚后玩家页面未及时刷新等问题，并提高了默认叙事输出额度。

### 修复

- 当兼容 OpenAI 的推理模型没有返回最终正文时，不再把 `reasoning_content` 当作叙事结果，避免内部推理过程出现在游戏正文中。
- 修复回滚到上一回合或同回合撤销行动后，玩家端仍停留在旧的提交、掷骰或角色状态的问题。
- 修复 NapCat 使用指南中的 `@bot` 指令显示不完整的问题。

### 调整

- 默认叙事最大 Token 从 1024 提升至 2048；仅迁移仍使用旧默认值的配置，保留已有自定义值。
- 长叙事二次压缩的输出额度提高至 1024–2048 Token；设置页对应统计明确为“叙事 Token（含二次压缩）”。

## English

This release fixes cases where truncated reasoning-model responses could expose internal reasoning, improves player refresh after rollbacks, and raises the default narration budget.

### Fixes

- When an OpenAI-compatible reasoning model returns no final content, DiceFrame no longer uses `reasoning_content` as narration. This prevents internal reasoning from appearing in the game text.
- Fixed player pages retaining stale submission, dice, or character state after rolling back to an earlier round or undoing actions in the same round.
- Fixed incomplete rendering of literal `@bot` commands in the NapCat guide.

### Changes

- Raised the default narrative token limit from 1024 to 2048. Only configurations still using the old default are migrated; custom values are preserved.
- Raised the long-narration compression budget to 1024–2048 tokens and clarified the usage label as “Narrative Tokens (incl. compression)”.
