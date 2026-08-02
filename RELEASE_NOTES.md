# DiceFrame v1.8.4

## 中文

这个版本修复了单人模式下反复切换行动导致掷骰卡死的问题，并优化了插件页的内容包界面：插件列表默认收起、内容包/主题插件可直接在列表上启用、内容包资源按插件分组折叠展示。同时沿用 v1.8.3 的全部功能与修复记录。

### 修复

- 修复单人模式切换行动：反复修改行动现在会替换上一版，不再堆积多条行动触发 3 条上限，也不会让未掷骰的旧检定残留、卡住后续掷骰。

### 优化

- 插件列表默认收起：插件较多时页面不再被全部展开撑得过长；用户展开的插件在刷新后保持展开。
- 内容包与主题插件可在插件列表上直接启用：新增"启用"开关，勾选即保存生效，无需再进入配置页。
- 内容包资源按插件分组折叠展示：每个资源包一个折叠卡片，内部按角色/NPC/道具/法术/职业分组并以网格排列，修复多个资源包同时启用时页面过长、条目重叠的问题。

### 自 v1.8.3 以来的完整功能（合并发布）

- 检定触发改为数据驱动的多语言词表：内置 6 类意图 + 通用检定，中英文双份、覆盖 400+ 触发词；修正「观察」类动作在 d20 规则下改走感知检定。
- 检定按规则对应骰子：d20 规则走属性检定 vs DC，CoC 走 d100 技能阈值，无骰规则不触发。
- 独立角色库、角色头像系统、幸运改判流程、扩展插件管理等完整功能。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.8.4-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.8.4-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release fixes solo-mode action switching stalling dice checks, and improves the content-pack interface: plugin list collapses by default, content packs and themes get an enable toggle in the list, and content resources are grouped per plugin. It retains the full v1.8.3 feature and fix history.

### Fixes

- Fixed solo-mode action switching: revising an action now replaces the previous one instead of stacking duplicates, so it no longer hits the 3-action cap or leaves stale pending dice checks that block later rolls.

### Improvements

- Plugin list collapses by default, so many plugins no longer stretch the page; plugins you expand stay expanded after refresh.
- Content packs and themes can be enabled directly from the plugin list with a toggle that saves immediately.
- Content resources are grouped by plugin in collapsible sections, with items in a responsive grid per type, fixing the overly long overlapping layout when many packs are enabled.

### Full History From v1.8.3 (Included in This Release)

- Data-driven multilingual check intents, "observe"-style actions resolving to Perception under d20 rules, and checks following each rule's dice system.
- Standalone character library, character portraits, Luck-spend flow, and expanded plugin management.

### Download Guide

- **Most Windows users**: Download `DiceFrame-v1.8.4-windows-portable.zip`.
- **Source package users**: Download `DiceFrame-v1.8.4-windows.zip`.
- `.sha256` files are used for update verification and do not need to be downloaded manually.
