# DiceFrame v1.6.1

## 中文

这个版本补齐了应用更新流程，也带来了更流畅的叙事显示、购买确认和模型配置引导。

### 新功能

- **应用更新**：可以直接在设置页检查、下载并应用新版本。
- **叙事逐字显示**：GM 叙述会逐步显示，不用再等整段内容生成完。
- **模型配置引导**：创建冒险时如果还没有配置模型 API，页面会给出提示，并能直接前往设置页。
- **购买确认**：购买物品前会先交给付款玩家确认；余额足够并确认后，才会扣款和发放物品，多人游戏同样适用。

### 优化与修复

- 设置页只会提供适合当前安装方式的更新包，避免便携版和源码版混用。
- 优化长篇叙事的生成和显示体验。
- 修复部分情况下玩家看不到 GM 叙述的问题。
- 修复模型内部状态标签偶尔出现在叙事正文中的问题。
- 调整中英文用户手册的开头说明。

## English

This release completes the application update flow and adds smoother narration, purchase confirmation, and model setup guidance.

### New Features

- **Application updates**: Check, download, and apply new versions directly from Settings.
- **Streaming narration**: GM narration appears progressively instead of waiting for the complete response.
- **Model setup guidance**: When no model API is configured, the adventure creation page shows a prompt with a direct link to Settings.
- **Purchase confirmation**: The paying player confirms a purchase before funds are deducted and items are granted, including in multiplayer games.

### Improvements and Fixes

- Settings now offers only the update package that matches the current installation, preventing portable and source packages from being mixed.
- Improved the generation and display of long-form narration.
- Fixed cases where players could not see GM narration.
- Fixed internal model state tags occasionally appearing in player-facing narration.
- Refined the introduction in both user guides.
