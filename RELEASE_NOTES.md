# DiceFrame v2.5.5-beta.2

> 回合推进失败恢复与 GM 中止生成测试版。

## 中文

### 修复内容

- 修复判定/叙事生成失败后对局永久停在「正在生成剧情」的问题：失败会把本回合退回行动阶段，玩家不再遇到提交行动 409、界面无限等待、只能重启容器的卡死场景。
- 失败回滚改为一致回滚：玩家血量、幸运决定、本轮检定与战斗结算缓存一起回到本轮开始前的状态；旧版 hp_based 路径对怪物造成的伤害也会回滚，重试不会再出现「结算记录显示受伤、实际血量却是满血」，也不会重复扣血。
- GM 的「强制推进」现在会中止正在进行的生成：等待中的玩家会收到「本轮已被 GM 中止，已退回行动阶段」的明确提示，随后本回合按当前行动重新处理，不必再等模型超时。
- 所有推进入口（玩家提交行动、幸运超时、命令行调试、`/stream-action`）共用同一恢复边界，任一入口失败都会回滚并落盘，不再有绕过服务层的卡死入口。
- 剧情概览在落卡前校验来源日志：生成期间被回滚重写的回合不会再被挂上过期的旧概览。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.5-beta.2-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.5-beta.2-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.5-beta.2-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 校验文件。

## English

### Fixes

- Fixes rounds getting permanently stuck on "Generating story" after a failed judgment/narration: the round now rolls back to the action phase instead of leaving players with 409s, an endless spinner, and a container restart as the only way out.
- Rollback is now consistent: player HP, luck decisions, the round's checks and the combat resolution cache all return to the state from the start of the round. Legacy hp_based damage to NPCs is rolled back as well, so a retry no longer reports damage the HP never took — and never applies it twice.
- GM "Force advance" now aborts a running generation: waiting players are told the GM stopped the round and that it is back in the action phase, and the round is reprocessed from the current actions instead of waiting out the model timeout.
- Every advance entry point (player submit, luck timeout, CLI debug loop, `/stream-action`) shares one recovery boundary, so a failure on any of them rolls back and persists instead of leaving the game stuck.
- Story recaps validate their source log before being attached, so a stale recap is never pinned onto a round that was rolled back and rewritten while it was generating.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.5-beta.2-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.5-beta.2-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.5-beta.2-docker-update-linux-amd64.zip`
- Verify downloads with the `SHA256SUMS` file attached to the Release.
