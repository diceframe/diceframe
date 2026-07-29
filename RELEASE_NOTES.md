# DiceFrame v1.7.4

## 中文

这个版本让 DiceFrame 更适合通过端口映射邀请朋友游玩，并补齐了群聊 Bot 的中英文体验和插件扩展能力。

### 新功能

- **公网访问保护**：加入轻量请求限流和更清楚的操作频繁提示，降低大量连续请求对游戏的影响。
- **最近登录记录**：设置页可以查看最近的登录时间、来源 IP 和结果，记录会自动限制数量。
- **中英文群聊 Bot**：Bot 会跟随对局语言显示帮助、状态、前情、地图、支付和错误提示，英文对局可以直接使用英文命令。
- **群聊展示扩展**：插件可以增加 Bot 命令，或把状态、地图等回复替换为自定义文字、图片和卡片；扩展失败时会自动使用内置展示。

### 优化与修复

- 减少私下部署的 DiceFrame 页面被搜索引擎收录。
- 补充常用安全响应头，并改进登录与接口访问保护。
- 修复未设置固定密码时，部分情况下页面却显示已经设置密码的问题。
- “忘记密码”说明现在会明确指出重置文件应放在 `data` 目录。
- 改善日间主题的文字、状态标签和高亮内容对比度。
- 调整游玩页顶部栏高度和排版，避免内容上下溢出，并移除与场景卡片重复的信息。
- 更新 Bot Bridge、QQ / NapCat 和插件开发文档。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.7.4-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.7.4-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release makes DiceFrame safer to share through port forwarding and completes the bilingual chat Bot experience and extension support.

### New Features

- **Public access protection**: Lightweight request throttling and clearer rate-limit messages reduce the impact of repeated requests on active games.
- **Recent login history**: Settings now shows recent login times, source IPs, and results while automatically limiting retained entries.
- **Bilingual chat Bot**: Help, status, recap, map, payment, and error messages follow the game language, with native English commands for English games.
- **Chat presentation extensions**: Plugins can add Bot commands or replace status, map, and other replies with custom text, images, or cards. DiceFrame falls back to its built-in presentation if an extension fails.

### Improvements and Fixes

- Reduced search-engine indexing of privately hosted DiceFrame pages.
- Added common security response headers and improved login and API access protection.
- Fixed cases where DiceFrame reported that a fixed password was configured when none had been set.
- Password recovery guidance now clearly identifies the `data` directory.
- Improved text, status tag, and highlighted-content contrast in the light theme.
- Refined the play-page header to prevent vertical clipping and removed information duplicated by the scene card.
- Updated Bot Bridge, QQ / NapCat, and plugin development documentation.

### Download Guide

- **Most Windows users**: Download `DiceFrame-v1.7.4-windows-portable.zip`.
- **Source package users**: Download `DiceFrame-v1.7.4-windows.zip`.
- `.sha256` files are used for update verification and do not need to be downloaded manually.
