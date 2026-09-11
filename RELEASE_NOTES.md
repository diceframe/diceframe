# DiceFrame v2.5.5

> 稳定版。本版修掉「回合处理失败后对局永久卡住」的生产事故，给 GM 补上中止在飞生成的能力，并把「强制推进」放进 GM 控台「流程」分组。升级无需迁移，重要战役建议先备份完整 `data/`。

## 中文

### 本版重点：失败可恢复

- **不再卡在「正在生成剧情」**：判定/叙事生成失败时会自动把本回合退回行动阶段，玩家不再遇到提交行动 409、界面无限等待、只能重启容器的场景。
- **GM 可中止在飞生成**：GM 控台「流程」分组新增常驻「强制推进」（判定/生成中可点）。确认后会中止当前生成、清掉半截流式文本并重新处理本回合；等待中的玩家会收到「本轮已被 GM 中止」的明确提示。
- **回滚保持一致**：玩家血量、幸运决定、本轮检定与战斗结算缓存一起回到本轮开始前；旧版 hp_based 路径对怪物造成的伤害也会回滚，重试不会出现「结算记录显示受伤、实际血量却是满血」，也不会重复扣血。
- **所有推进入口共用同一恢复边界**：玩家提交行动、幸运超时、命令行调试与 `/stream-action` 任一入口失败都会回滚并落盘，不再有绕过服务层的卡死入口。

### 修复

- 剧情概览在落卡前校验来源日志：生成期间被回滚重写的回合不会再被挂上过期的旧概览。
- 回合失败文案按语言本地化（中/英/日），异常原文只进入服务器日志。

### 界面文案

- GM 控台的中文按钮「发起支付提案」精简为「支付提案」。

### 升级提示

- **无存档迁移**：本版新增的判定实体快照是增量字段，旧存档可直接加载；缺该字段的旧存档会自动退化为按目标核对战斗缓存。
- 单人局与多人局行为一致；升级重要战役前建议备份完整 `data/` 目录。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.5-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.5-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.5-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 校验文件。

## English

### Highlights: recoverable rounds

- **No more being stuck on "Generating story"**: a failed judgment/narration rolls the round back to the action phase instead of leaving players with 409s, an endless spinner, and a container restart as the only way out.
- **The GM can abort a running generation**: a persistent "Force advance" entry now lives in the GM console's Flow group (enabled while the round is judging or generating). Confirming aborts the generation, clears the partial stream, and reprocesses the round; waiting players are told the GM stopped it.
- **Consistent rollback**: player HP, luck decisions, the round's checks and the combat resolution cache all return to the state from the start of the round. Legacy hp_based damage to NPCs rolls back too, so a retry never reports damage the HP never took, and never applies it twice.
- **One recovery boundary for every entry point**: player submit, luck timeout, CLI debug loop and `/stream-action` all roll back and persist on failure instead of leaving the game stuck.

### Fixes

- Story recaps validate their source log before attaching, so a stale recap is never pinned onto a round that was rolled back and rewritten while it was generating.
- Round failure messages are localized (zh/en/ja); raw exception text stays in the server logs.

### UI copy

- The GM console's Chinese label for the payment proposal button is shortened to 「支付提案」(English and Japanese labels are unchanged).

### Upgrade notes

- **No save migration**: the new judgment entity snapshot is an additive field, so existing saves load as-is; saves without it automatically fall back to verifying combat caches per target.
- Solo and multiplayer tables behave the same. Back up the complete `data/` directory before upgrading important campaigns.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.5-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.5-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.5-docker-update-linux-amd64.zip`
- Verify downloads with the `SHA256SUMS` file attached to the Release.
