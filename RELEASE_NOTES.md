# DiceFrame v1.8.1

## 中文

这个版本扩展了角色与跨端游玩体验，并修复 v1.8.0 首次引入头像资源后导致的安装包异常膨胀。角色可以在开团前独立创建和管理，Web 与 Bot 可共同处理幸运改判，并加入完整的角色头像系统。

### 新功能

- 新增独立角色库：无需先创建游戏即可建卡、编辑和保存角色，并记录所属规则；跨规则使用时会明确提示转换，旧角色卡仍可继续读取。
- 新增角色头像系统：内置六套规则主题头像，每套 8 张，支持稳定自动分配和 PNG/JPEG/WebP 自定义上传；头像会显示在角色状态、对话时间线和分享建卡页中，玩家与 GM 可在游玩页直接更换。
- 新增幸运改判流程：支持的 d100 规则在失败后会暂停结算，玩家可直接选择消耗对应点数的幸运改为成功，或保留失败；Web、QQ/NapCat 与外部 Bot Bridge 共用同一套持久化判定，避免重复扣除。
- 扩展插件管理：插件主题、内容资源、工具和市场状态使用统一描述与独立界面逻辑，安装、更新和能力展示更清晰。

### 优化与修复

- 将内置头像图集改为适配界面分辨率的高质量 WebP，并让便携包只保留运行所需的编译产物；发布流程会阻止 PNG 图集回流、头像重复打包和体积异常增长。
- 加强模型输出协议隔离：异常的状态标签、检定标签和修复文本不会再混入公开叙事；必要时自动进行一次格式修复重试。
- Token 自动升档成功后会在 GM 页面显示低干扰提示，便于发现默认预算不足；截断重试会记录实际使用档位。
- 世界模板、世界书和角色卡按内容语言筛选，减少中英文内容混杂。
- 优化 QQ/NapCat 与 Bot Bridge：补充幸运选择、英文消息、扩展协议和 Web 同步去重；已删除游戏的失效绑定会在连续确认后自动清理。
- 修复分享建卡长页面背景断层、公开分享页错误请求后台更新/插件接口，以及 GM 游玩页头像无法点击的问题。
- 改进回合回滚、待处理检定恢复、运行时配置热更新和 SSE 身份票据，降低重启或配置更新对正在进行对局的影响。
- 便携版更新现在明确只保留当前与上一套程序文件，并在后续更新成功后清理根目录旧版 `app/`、`python/`，同时保留 `data/`、`logs/` 和启动器。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.8.1-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.8.1-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release expands character and cross-client play and fixes the package-size regression introduced when v1.8.0 first added portrait assets. Characters can now be created and managed before a game starts, Web and bot clients share the same Luck-spend workflow, and DiceFrame gains a complete portrait system.

### New Features

- Added a standalone character library. Create, edit, and save characters without starting a game, retain their ruleset identity, receive explicit cross-ruleset conversion warnings, and keep legacy cards readable.
- Added character portraits with six ruleset-themed packs of eight portraits each, stable automatic assignment, and custom PNG/JPEG/WebP uploads. Portraits appear in character status, conversation timelines, and shared character creation; players and GMs can change them from the play view.
- Added a Luck-spend decision flow for supported d100 rules. A failed check pauses before narration so the player can spend the exact Luck difference to succeed or keep the failure. Web, QQ/NapCat, and external Bot Bridge clients share the same persisted, idempotent decision.
- Expanded plugin management with unified descriptors and separated views for themes, content resources, tools, marketplace state, installation, and updates.

### Improvements and Fixes

- Converted built-in portrait atlases to high-quality, UI-sized WebP assets and removed frontend source copies from portable packages. Release validation now blocks legacy PNG atlases, duplicate portable assets, and future portrait-size regressions.
- Strengthened model-output protocol isolation so malformed state tags, check tags, and repair text no longer leak into public narration; one strict format-repair retry is used when needed.
- Added a subtle GM notice when automatic token-budget escalation succeeds, and retained the actual successful retry budget for diagnostics.
- Filtered world templates, lorebooks, and character cards by content language to reduce mixed Chinese and English content.
- Improved QQ/NapCat and Bot Bridge behavior with Luck decisions, English responses, extension handling, Web-sync deduplication, and confirmed cleanup of bindings for deleted games.
- Fixed the shared character-creation background ending before long forms, unauthorized owner-only requests on public share pages, and non-clickable GM portraits in the play view.
- Improved round rollback, pending-check recovery, transactional runtime configuration reloads, and SSE identity tickets to reduce disruption to active games.
- Clarified portable update retention: only the current and previous application payloads are kept, obsolete root `app/` and `python/` payloads are removed after later successful updates, and `data/`, `logs/`, and the launcher remain untouched.

### Download Guide

- **Most Windows users**: Download `DiceFrame-v1.8.1-windows-portable.zip`.
- **Source package users**: Download `DiceFrame-v1.8.1-windows.zip`.
- `.sha256` files are used for update verification and do not need to be downloaded manually.
