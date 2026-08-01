# DiceFrame v1.8.3

## 中文

这个版本在完整角色与跨端游玩体验的基础上，重构检定触发为数据驱动的多语言词表：修正「观察」类动作的检定归属（DND 5e 下改为感知检定，不再误判为智力），英文触发词改为整词匹配以消除误报，并让词表随规则走——不同规则自动对应各自的骰制。同时沿用 v1.8.2 的全部功能与修复记录。

### 新功能

- 检定触发改为数据驱动词表：内置 6 类意图（潜行/调查/感知/社交/运动/战斗）+ 通用检定，中英文双份、覆盖 400+ 常见触发词；规则模板可继承或覆盖词表，自定义规则也能定义自己的判定方式。
- 修正「观察」类动作的判定：在 D&D 5e 等 d20 规则下，观察周围/看看/张望/瞅瞅/环视等动作改为感知检定（Wisdom），不再错误地走智力检定；在 CoC 规则下自动走「侦查」技能检定（d100）。
- 检定按规则对应骰子：同一动作在 d20 规则（dnd5e / 自由奇幻 / 武侠 / 赛博朋克）走属性检定 vs DC，在 CoC（freeform_coc）走 d100 技能阈值，在无骰规则（tavern_free）不触发检定。
- 英文触发词支持：英文玩家用自然语言（look around / sneak / persuade / attack 等）即可触发检定，且使用整词边界匹配，避免「roll」误命中「scroll」这类误报。
- 为后续多语言扩展预留结构：词表按语言键组织，新增语言只需补对应语言列与后缀登记，不改判定逻辑。

### 优化与修复

- 消除检定判定两条路径不一致：属性推断（`_guess_attribute_key`）与意图识别共用同一份词表，同一句「观察」不再出现一边走感知、一边走智力。
- 修复英文局基本无法触发检定的问题：此前触发词几乎全为中文，英文局只能靠 UI 手动选择；现在英文触发词与整词边界匹配一起补齐。
- 通用检定词改走词表：避免「roll/check」在英文句中误命中单词内部的子串。
- 世界模板、世界书和角色卡按内容语言筛选，减少中英文内容混杂。
- 强化模型输出协议隔离：异常的状态标签、检定标签和修复文本不会混入公开叙事。
- 改进回合回滚、待处理检定恢复、运行时配置热更新和 SSE 身份票据。

### 自 v1.8.2 以来的完整功能（合并发布）

- 新增独立角色库：无需先创建游戏即可建卡、编辑和保存角色，并记录所属规则；跨规则使用时会明确提示转换，旧角色卡仍可继续读取。
- 新增角色头像系统：内置六套规则主题头像，每套 8 张，支持稳定自动分配和 PNG/JPEG/WebP 自定义上传；头像会显示在角色状态、对话时间线和分享建卡页中。
- 新增幸运改判流程：支持的 d100 规则在失败后会暂停结算，玩家可直接选择消耗对应点数的幸运改为成功，或保留失败；Web、QQ/NapCat 与外部 Bot Bridge 共用同一套持久化判定。
- 扩展插件管理：插件主题、内容资源、工具和市场状态使用统一描述与独立界面逻辑。
- 修复 801–980px 宽度下 GM 控制栏布局问题；完善 NPC 头像管理；内置头像改为高质量 WebP 并精简便携包体积。
- Token 自动升档成功后显示低干扰提示；优化 QQ/NapCat 与 Bot Bridge 的幸运选择、英文消息、扩展协议和 Web 同步去重。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v1.8.3-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v1.8.3-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This release rebuilds check triggering on a data-driven multilingual keyword table: "observe"-style actions now resolve to the correct check (Perception instead of Intelligence under D&D 5e), English trigger words use word-boundary matching to avoid false positives, and the table follows the active ruleset so each rule keeps its own dice system. It retains the full feature and fix history from v1.8.2 below.

### New Features

- Data-driven check intents: six intents (stealth / investigate / perception / social / athletics / combat) plus generic checks, in both Chinese and English with 400+ common trigger phrases. Rule templates can inherit or override the table, so custom rules define their own check behavior.
- Fixed "observe"-style actions: under d20 rules (D&D 5e etc.), actions like observe / look around / glance / scan now resolve to a Perception (Wisdom) check instead of an Intelligence check; under CoC they resolve to a Spot Hidden skill check (d100).
- Checks follow the ruleset's dice system: the same action uses attribute-vs-DC under d20 rules (dnd5e / freeform fantasy / wuxia / cyberpunk), a d100 skill threshold under CoC, and no check at all under dice-less rules (tavern_free).
- English trigger words: English players can trigger checks with natural language (look around / sneak / persuade / attack), and matching uses word boundaries so "roll" no longer falsely matches "scroll".
- Structure ready for more languages: alias lists are keyed by language, so adding a language only requires new language columns plus a suffix registration, not logic changes.

### Improvements and Fixes

- Unified the two check-resolution paths: attribute guessing and intent recognition now share one keyword table, so "observe" can no longer resolve to Wisdom on one path and Intelligence on another.
- Fixed English games being unable to trigger checks from free text: trigger words were almost all Chinese, so English games relied on manual UI selection.
- Generic check words now use the keyword table, avoiding substring false positives like "roll" inside other words.
- Filtered world templates, lorebooks, and character cards by content language to reduce mixed Chinese and English content.
- Strengthened model-output protocol isolation so malformed state tags, check tags, and repair text no longer leak into public narration.

### Full History From v1.8.2 (Included in This Release)

- Standalone character library: create, edit, and save characters without starting a game, keep ruleset identity, receive explicit cross-ruleset conversion warnings, and keep legacy cards readable.
- Character portraits: six ruleset-themed packs of eight portraits each, stable automatic assignment, and custom PNG/JPEG/WebP uploads shown in character status, timelines, and shared character creation.
- Luck-spend decision flow for supported d100 rules: a failed check pauses before narration so the player can spend the exact Luck difference to succeed or keep the failure; shared, idempotent persistence across Web, QQ/NapCat, and external Bot Bridge.
- Expanded plugin management with unified descriptors and separated views for themes, content, tools, marketplace, installation, and updates.
- Fixed GM control rail layout at 801–980px; completed NPC portrait management; converted built-in portraits to high-quality UI-sized WebP and slimmed the portable package.
- Low-disturbance notice when automatic token-budget escalation succeeds; improved QQ/NapCat and Bot Bridge luck, English responses, extension handling, and Web-sync deduplication.

### Download Guide

- **Most Windows users**: Download `DiceFrame-v1.8.3-windows-portable.zip`.
- **Source package users**: Download `DiceFrame-v1.8.3-windows.zip`.
- `.sha256` files are used for update verification and do not need to be downloaded manually.
