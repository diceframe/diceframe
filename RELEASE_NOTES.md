# DiceFrame v3.0.1

## 中文

3.0.1 汇总了 3.0.0 发布后的稳定性、部署、D&D 2024 和 TTS 修复。

### 稳定性与部署

- 独立检定模型配置失效时返回明确的 409 业务错误，不再出现裸 HTTP 500 或伪成功。
- 增加检定模型失败后的恢复路径，并修复 Bot 席位认领边界。
- 修复托管 Docker 更新后运行日志不可见的问题，日志继续保存在共享数据目录。
- 改进自签 HTTPS 插件的设置提示，说明证书校验限制、插件连接方式和可行的处理建议。

### D&D 2024

- 接入起源专长“魔法学徒（Magic Initiate）”法术：背景戏法、一级法术、免费施法资源和角色中心展示。
- 增加并接入更多起源专长法术与戏法，包括 Shield、Chill Touch 等。
- Shield 反应现在会在命中触发法术（例如 Divine Smite）之前结算；暴击仍保持暴击，AI 也不会在无法改变结果时浪费 Shield。
- 修正 Chill Touch 的“无法恢复生命”持续到施法者下一回合结束。

### TTS

- 将 `edge-tts` 最低版本提高到 7.3.1，并更新 Docker 与 Windows 便携版的 hash 锁定依赖。
- 修复微软接口要求 `Sec-MS-GEC` v2 后 Edge TTS 持续返回 HTTP 403 的问题。

### 文档

- 整理 README 中 Language maintenance 文档入口的位置。

### 升级说明

- 托管 Docker 用户会在更新后使用新的 Edge TTS 依赖。
- Windows 便携版会随本版本携带 `edge-tts` 7.3.1。
- 升级重要对局前仍建议备份完整的 `data/` 文件夹。

## English

DiceFrame 3.0.1 consolidates post-3.0.0 stability, deployment, D&D 2024, and TTS fixes.

### Stability and deployment

- Invalid independent check-model configuration now returns an actionable 409 business error instead of a raw HTTP 500 or false success.
- Add recovery paths for failed check-model operations and correct Bot-seat claiming boundaries.
- Restore runtime log visibility after managed Docker updates by keeping logs in the shared data directory.
- Improve the self-signed HTTPS plugin guidance with a clearer settings dialog explaining certificate verification limits and recovery options.

### D&D 2024

- Wire Origin Feat Magic Initiate spells into character and combat flows, including background cantrips, a level-one spell, free-cast resources, and character-center presentation.
- Add and connect more Origin Feat spells and cantrips, including Shield and Chill Touch.
- Resolve Shield reactions before hit-triggered spells such as Divine Smite; critical hits remain critical, and AI defenders do not waste Shield when it cannot change the result.
- Keep Chill Touch’s healing prevention until the end of the caster’s next turn.

### TTS

- Raise the `edge-tts` minimum to 7.3.1 and update the hash-pinned Docker and Windows portable dependencies.
- Fix Edge TTS HTTP 403 failures after Microsoft’s endpoint began requiring `Sec-MS-GEC` v2 tokens.

### Documentation

- Reorganize the README entry for Language maintenance.

### Upgrade notes

- Managed Docker users receive the updated Edge TTS dependency with this update.
- The Windows portable build includes `edge-tts` 7.3.1.
- Back up the complete `data/` directory before upgrading important campaigns.

## Downloads

- **普通 Windows 用户**：`DiceFrame-v3.0.1-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v3.0.1-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v3.0.1-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
