# DiceFrame v3.0.5

## 中文

### D&D 2024

- 修正借机攻击的武器选择：按实际伤害构成选择近战武器，覆盖武器骰式、固定伤害加值和角色属性修正。
- 延续并强化 Action Surge、Rage、Second Wind、Sneak Attack、Uncanny Dodge 等职业能力，以及反应、状态、目标和战斗状态校验。

### GAL 与冒险玩法

- 延续 Adventure v2 场景推进、结局后续、gameplay package 边界和好感度结算能力。

### WebUI、内容与可靠性

- 修复首次启动法律文件在在线内容与本地快照切换时的误报，并让公共内容读取复用配置的镜像源。
- 限制镜像文本响应的最大读取大小，避免异常内容占用不受限内存。
- 保留首次启动引导、登录访问帮助、好友邀请和移动端战斗界面改进。

### 升级说明

- 本版本不新增存档 schema 迁移。
- 升级前请备份 `data/saves`；已结束的存档继续保持只读。

## English

### D&D 2024

- Fixed opportunity attacks choosing a melee weapon by actual damage components, including weapon dice, flat damage bonuses, and character ability modifiers.
- Continued the strengthened Action Surge, Rage, Second Wind, Sneak Attack, Uncanny Dodge, reaction, condition, target, and combat-state coverage.

### GAL and adventure gameplay

- Continued Adventure v2 scene progression, ending follow-ups, gameplay package boundaries, and affection settlement.

### WebUI, content, and reliability

- Fixed false first-start legal update prompts when online and bundled content snapshots switch, and routed public-content reads through configured mirrors.
- Enforced maximum response sizes for mirrored text content.
- Retained the first-run tour, login access help, friend invitations, and mobile combat UI improvements.

### Upgrade notes

- This release adds no save-schema migration.
- Back up `data/saves` before upgrading; ended archives remain read-only.

## Downloads

- **普通 Windows 用户 / Windows portable users**: `DiceFrame-v3.0.5-windows-portable.zip`
- **源码运行用户 / Source-run users**: `DiceFrame-v3.0.5-windows.zip`
- **托管 Docker 更新 / Managed Docker update**: `DiceFrame-v3.0.5-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
