# DiceFrame v3.0.3

## 中文

### 结团准备

- 修复房间设有密码、访问令牌或席位凭据时，结团流程误报 `the frozen run changed; cancel preparation and start again` 的问题。
- 独立的房间访问凭据与设置变更不再使结团准备任务失效。
- 角色、世界和完整日志的变更仍会被来源指纹检测，过期的准备任务仍会被拒绝。

### 升级说明

- 现有结团准备任务使用旧版本的来源摘要。升级前请先复制尚未保存的尾声与回顾；升级后取消旧任务并重新准备。
- 已结束的存档保持只读。

## English

### Finale preparation

- Fix false `the frozen run changed; cancel preparation and start again` errors during finales in rooms with passwords, access tokens, or seat credentials.
- Independent room-access credentials and settings no longer invalidate a prepared finale job.
- Character, world, and complete-log changes remain fingerprinted; stale prepared jobs are still rejected.

### Upgrade notes

- Existing finale preparation jobs use the previous source digest. Copy any unsaved epilogue and recap before upgrading, then cancel and prepare again after the upgrade.
- Ended archives remain read-only.

## Downloads

- **普通 Windows 用户**：`DiceFrame-v3.0.3-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v3.0.3-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v3.0.3-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
