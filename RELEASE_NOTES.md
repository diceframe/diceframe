# DiceFrame Release Notes

## Unreleased

### 中文

- Windows 便携版新增旁路安装、健康检查、观察期和失败自动回滚；源码发布包使用可恢复的事务式替换。
- Docker、NAS 和 Git 开发目录只显示适合各自环境的更新提示，不在运行环境中直接覆盖程序。
- 创建冒险时新增模型 API 配置引导，后端会在写入世界、角色和存档前检查配置。
- 购买物品改为由付款玩家确认；余额足够并确认后才扣款和发放物品。
- 修复部分模型输出把内部状态标签显示在叙事正文中的问题。

### English

- Added side-by-side portable updates with health checking, probation, and automatic rollback. Extracted source releases use recoverable transactional replacement.
- Docker, NAS, and Git development installations now receive environment-specific update guidance without in-place replacement.
- Added model-API guidance on Create and a backend preflight before writing world, character, or save data.
- Item purchases now require confirmation from the paying player; funds and items move only after confirmation and a sufficient-balance check.
- Fixed internal model state tags appearing in player-facing narration.

## 中文

### v1.6.0

本版本新增应用内更新检查与叙事逐字显示，升级与回合反馈都更顺手。

#### 新功能

- **应用内更新**：在设置页"版本更新"中检查并下载新版本安装包。
- **叙事逐字显示**：GM 叙述现在逐字呈现，无需等待整段生成完毕。
- **叙事生成更流畅**：优化了长叙事的生成体验。

#### 修复

- 修正部分情况下玩家看不到 GM 叙述内容的问题。

## English

### v1.6.0

This release adds in-app update checks and token-by-token narration display.

#### New Features

- **In-app update**: Check and download new release packages from the "Version Update" panel in Settings.
- **Streaming narration**: GM narration now appears token by token instead of waiting for the full paragraph.
- **Smoother narration generation**: Improved the long-form narration experience.

#### Fixes

- Fixed an issue where players could not see GM narration in some cases.
