# DiceFrame v1.7.5

## 中文

这个版本主要修复删除游戏后遗留临时世界模板和群聊 Bot 绑定的问题。

### 优化与修复

- 删除游戏时，会一并清理该游戏生成且未被其他存档使用的临时世界模板。
- 启动时会清理旧版本遗留的孤立临时模板，同时保留用户主动创建和保存的模板。
- QQ / NapCat Bot 连续确认绑定的游戏已经不存在后，会自动解除群绑定，不再每隔数秒重复报错。
- 调整启动顺序，避免程序恢复存档前错误地把有效 Bot 绑定判定为失效。
- MaiBot Bridge 同步支持识别已删除的游戏并解除失效绑定。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.7.5-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.7.5-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release fixes temporary world templates and chat Bot bindings being left behind after a game is deleted.

### Improvements and Fixes

- Deleting a game now also removes its generated temporary world template when no other save uses it.
- Startup cleanup removes orphaned temporary templates from older versions while preserving templates created or saved by users.
- QQ / NapCat automatically unbinds a group after repeatedly confirming that its bound game no longer exists, stopping recurring sync errors.
- Save recovery now finishes before Bot plugins start, preventing valid bindings from being treated as stale.
- MaiBot Bridge now also recognizes deleted games and clears stale bindings.

### Download Guide

- **Most Windows users**: Download `DiceFrame-v1.7.5-windows-portable.zip`.
- **Source package users**: Download `DiceFrame-v1.7.5-windows.zip`.
- `.sha256` files are used for update verification and do not need to be downloaded manually.
