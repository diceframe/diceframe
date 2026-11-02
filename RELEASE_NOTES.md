# DiceFrame v3.0.4

## 中文

### D&D 2024

- 补齐并强化 Action Surge、Rage、Second Wind、Sneak Attack、Uncanny Dodge 等职业能力。
- 扩展 P1–P9 法术与 R1 Revivify 探索复活流程，完善反应、状态、目标和战斗状态校验。
- 新增战场 HUD、先攻条、行动面板、目标卡和战斗反馈，改善移动端快捷操作。

### GAL 与冒险玩法

- 接入 Adventure v2 场景推进、结局后续和 gameplay package 边界。
- 新增加好感度结算与 Lighthouse Summer 示例内容。

### WebUI、内容与可靠性

- 新增首次启动引导、登录访问帮助、好友邀请和更完整的消息展示。
- 修复手动骰存档失败后的重试路径：确认再次落盘后才返回幂等成功。
- 加强角色立绘与声音引用保护、世界书范围/递归处理、Tavern PNG 导入和 Markdown 只读渲染。
- 启动器会检测前端依赖完整性并自动修复缺失的构建依赖。
- 更新 Pillow 至 13.0.0；首次启动引导使用 `driver.js`。

### 升级说明

- GameInstance 存档 schema 从 47 升至 48，增加好感度回滚能力的 reader fence。旧存档不会被推断出历史好感度分数。
- 升级前请备份 `data/saves`。升级后，若存在进行中的结团准备任务，请取消并重新准备，以生成新的来源摘要。
- 已结束的存档继续保持只读。

## English

### D&D 2024

- Expanded and hardened Action Surge, Rage, Second Wind, Sneak Attack, and Uncanny Dodge class abilities.
- Added the P1–P9 spell coverage and the R1 Revivify exploration flow, with stronger reaction, condition, target, and combat-state validation.
- Added the battlefield HUD, initiative bar, action dock, target cards, and combat feedback, including improved mobile quick actions.

### GAL and adventure gameplay

- Added Adventure v2 scene progression, ending follow-ups, and gameplay package boundaries.
- Added affection settlement and the Lighthouse Summer sample adventure.

### WebUI, content, and reliability

- Added the first-run tour, login access help, friend invitations, and improved message presentation.
- Fixed manual-roll retries after a failed save: an idempotent retry now confirms persistence before acknowledging success.
- Hardened standing portrait and voice references, lorebook scope/recursion handling, Tavern PNG import, and read-only Markdown rendering.
- The launcher now detects incomplete frontend dependencies and repairs missing build packages automatically.
- Updated Pillow to 13.0.0; the first-run tour uses `driver.js`.

### Upgrade notes

- The GameInstance save schema advances from 47 to 48 with a reader fence for affection rollback support. Older saves never infer historical affection scores.
- Back up `data/saves` before upgrading. After upgrading, cancel and re-prepare any in-progress finale preparation so it uses a new source digest.
- Ended archives remain read-only.

## Downloads

- **普通 Windows 用户 / Windows portable users**: `DiceFrame-v3.0.4-windows-portable.zip`
- **源码运行用户 / Source-run users**: `DiceFrame-v3.0.4-windows.zip`
- **托管 Docker 更新 / Managed Docker update**: `DiceFrame-v3.0.4-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
