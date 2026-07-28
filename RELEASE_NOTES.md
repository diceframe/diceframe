# DiceFrame v1.6.3

## 中文

这个版本补齐了应用更新流程，也带来了更流畅的叙事显示、购买确认和模型配置引导。

### 新功能

- **应用更新**：可以直接在设置页检查、下载并应用新版本。
- **叙事逐字显示**：GM 叙述会逐步显示，不用再等整段内容生成完。
- **模型配置引导**：创建冒险时如果还没有配置模型 API，页面会给出提示，并能直接前往设置页。
- **购买确认**：购买物品前会先交给付款玩家确认；余额足够并确认后，才会扣款和发放物品，多人游戏同样适用。

### 优化与修复

- 更新提示现在可直接前往设置页的版本更新区域。
- 设置页只会提供适合当前安装方式的更新包，避免便携版和源码版混用。
- 修复网络较慢时更新包反复从头下载的问题。
- 便携版更新完成后会自动刷新页面。
- 更新成功后会清理下载包，并限制保留的旧版本与源码备份数量。
- 调整设置页版本更新区域的位置。
- 优化长篇叙事的生成和显示体验。
- 修复部分情况下玩家看不到 GM 叙述的问题。
- 修复模型内部状态标签偶尔出现在叙事正文中的问题。
- 调整中英文用户手册的开头说明。
- 调整 README 首页链接布局。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.6.3-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.6.3-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release completes the application update flow and adds smoother narration, purchase confirmation, and model setup guidance.

### New Features

- **Application updates**: Check, download, and apply new versions directly from Settings.
- **Streaming narration**: GM narration appears progressively instead of waiting for the complete response.
- **Model setup guidance**: When no model API is configured, the adventure creation page shows a prompt with a direct link to Settings.
- **Purchase confirmation**: The paying player confirms a purchase before funds are deducted and items are granted, including in multiplayer games.

### Improvements and Fixes

- Update notifications can now take users directly to the version update area in Settings.
- Settings now offers only the update package that matches the current installation, preventing portable and source packages from being mixed.
- Fixed update packages repeatedly restarting from the beginning on slower networks.
- Portable installations now refresh the page automatically after an update completes.
- Successful updates now remove downloaded packages and limit retained old versions and source backups.
- Moved the version update area within Settings.
- Improved the generation and display of long-form narration.
- Fixed cases where players could not see GM narration.
- Fixed internal model state tags occasionally appearing in player-facing narration.
- Refined the introduction in both user guides.
- Adjusted the link layout at the top of the README.

### Download Guide

- **Most Windows users**: Download `DiceFrame-v1.6.3-windows-portable.zip`.
- **Source package users**: Download `DiceFrame-v1.6.3-windows.zip`.
- `.sha256` files are used for update verification and do not need to be downloaded manually.
