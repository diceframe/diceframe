# DiceFrame v1.9.1

## 中文

这个版本带来了存档导入/导出与插件商店改版：对局可一键导出，也能导入为新对局继续跑，方便迁移与备份；插件商店卡片重新布局，作者、类型、评分一目了然。同时修复了换一条回复（Swipe）或回滚后重启存档丢失、手动掷骰触发词不足等问题。

### 新功能

- 存档导入/导出：对局可一键导出为 zip（对话历史分离保存），也可导入为全新对局继续跑，方便迁移与备份。
- 角色头像全库选择：头像选择器新增"从所有头像中选择"，可在全部 6 套规则共 48 个内置头像中挑选，不再局限于当前规则。
- 角色卡批量导出更安全：同名卡片自动加后缀区分，不再互相覆盖；导出保留来源与酒馆原始数据，导出→导入可无损往返。

### 修复

- 修复 Swipe 与回滚后存档不落盘：换回复、回滚现在会立即写入存档，重启不再丢失最近修改。
- 修复手动掷骰难以触发：支持更多触发词（掷骰/投骰/骰子/roll/dice 等），同时避免"骰子真有趣"之类普通句子误触发。
- 修复导入存档为新对局后对局列表不显示的问题。

### 优化

- 插件系统重构：插件类型改由后端描述文件单一驱动，商店筛选、插件列表与类型表统一；插件页新增 README 文档展示与内容包一键导入，内容包启用时自动灌注全部资源，卸载时自动清理注册表；商店评分改读索引仓快照，进商店不再卡顿。
- 角色管理页改版：卡片式网格布局，头像/名字/元信息对齐；规则徽章单独一行，长规则名自动省略并悬停显示全文。
- UI 调整：世界书、首页、规则页主操作按钮统一为绿色，规则描述悬停可查看完整内容。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.9.1-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.9.1-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release brings save import/export and a plugin-store overhaul: games can be exported in one click and imported as a brand-new game for migration or backup, and plugin store cards are redesigned with author, type and rating at a glance. It also fixes save loss after swipe/rollback, and makes manual dice prompts easier to trigger.

### New Features

- Save import/export: export a game as a zip (chat history stored separately) and import it as a new game for migration or backup.
- All-library portrait picking: the portrait picker adds "Choose from all portraits", covering all 48 built-in avatars across 6 rulesets.
- Safer batch card export: same-name cards get suffixes instead of overwriting each other; exports keep source and tavern data for lossless round trips.

### Fixes

- Fixed save loss after swipe/rollback: switching replies and rolling back now persist immediately, so recent changes survive restarts.
- Fixed manual dice prompts being hard to trigger: more trigger words (掷骰/投骰/骰子/roll/dice…) are supported, while ordinary sentences no longer false-trigger.
- Fixed imported saves not appearing in the game list.

### Improvements

- Plugin system rework: plugin types are driven by a single backend descriptor, unifying store filters, the plugin list and the type table; plugin pages now show README docs with one-click content-pack import, enabling a content pack auto-infuses all of its resources, and uninstalling cleans up the registry; store ratings read an index-repo snapshot so the store no longer stalls.
- Character management page: card grid layout with aligned avatar/name/metadata; rule badges sit on their own line, truncating with a hover tooltip when long.
- UI polish: primary action buttons on the lorebook, home and rules pages are now green, and rule descriptions show full text on hover.

### Download Guide

- **Regular Windows users**: download `DiceFrame-v1.9.1-windows-portable.zip`.
- **Source-run users**: download `DiceFrame-v1.9.1-windows.zip`.
- `.sha256` files are update checksums; regular users do not need to download them manually.