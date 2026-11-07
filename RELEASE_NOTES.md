# DiceFrame v3.1.0

## 中文

### 克苏鲁的呼唤（CoC 7e）

- 新增 CoC 7e 规则轨道：裁定档位与对抗检定、理智（SAN）结算、幸运与灵感、7e 建卡流程与叙事审计。
- 服务端安全加固：SAN 结算执行权威边界（不在场或已死亡角色不再被越权结算）；声明建卡边界的规则在入场时校验技能点、技能数与单项上限，绕过前端直接调 API 的超预算角色会被拒绝；d100 规则不再套用 D&D 的属性伤害加值，避免夸张的自动战斗伤害。
- 中性冒险与旧式部分属性角色卡继续走既有 clamp/normalize 兜底，不受建卡校验影响；对手属性换算只在显式声明时进行百分制放大。
- 已知限制：CoC 自动战斗仍为受限能力（HP 归零语义、贯穿伤害、全角标点标签等缺口见 issue #698），正式支持前请勿宣称完整自动战斗。

### D&D 2024

- 新增多重攻击（Extra Attack）与 3D 物品激活动画；消耗最后一次充能时自动投损坏判定（destroy-on-empty）。
- 修复：借机攻击按实际伤害构成选择近战武器；攻击次数与卡面读取使用同一所有权过滤；旧式已同调盾牌不再绕过同调上限；叙事充能状态损坏时本地化重算并容错；非法 destroy_on_empty 在加载时拒绝并清理对应行；背包拾取与数量引用保持一致。

### 内容包与创作者工具

- 创作者体验：所有内容包统一导出对话框、封面上传与市场展示、内容语言声明（supported_locales）、仓库格式导出附带 LICENSE、发布向导（指引作者发布到自己的仓库并提交上架表单）、`.dfplugin` 解压与回填说明。
- 模块与市场：在线内容架与 Hub 详情卡片、按内容语言与 content profile 标签筛选、索引致谢展示、仅在较新版本时提供更新且更新后刷新内容、本地导入行对齐与包命名统一。
- 可靠性：市场索引坏数据按条目隔离——单个畸形包不再让整个目录返回失败；`compatible_rulesets` 容错读取，非法字段降级为空并告警。
- 规则标签体系：拆分「规则硬依赖」（`requires.rulesets`）与「规则归属标签」（`compatible_rulesets`），为特定规则编写的包（如 CoC）用归属标签即可参与筛选，不再要求注册运行时；规则筛选不再把未声明规则的包筛成空；移除未经证明的 dnd5e → dnd2024 自动别名。

### 经济系统

- 加固：规划兜底不再猜测价格；回滚后分期阶段从账本推导；叙事奖励结算仅限 GM 支付；延迟结算效果以回执为准；无骰规则只保留经济结果。

### GAL 与冒险玩法

- GAL 规则选择与玩法模式/单人状态的联动统一：切换世界来源（模板/AI/自定义）、AI 基础规则与 AI 生成规则等路径不再保留非法的 GAL 组合，非法状态在创建前即修正。
- 草稿恢复按生效规则显式收敛，AI 草稿恢复后不再出现无法提交的非法组合；GAL 规则下直接禁用「多人」选项；移除冗余的 GAL 预览开关。

### 界面与体验

- 战斗 HUD 显示玩家与 NPC 的真实头像；战斗界面移动按钮并入输入行、详情块与固定按钮块布局、工具窗关闭按钮并入标题行。
- 骰盘主题化金属质感与真实投掷动画；内容库标题字号统一；资料册措辞统一；登录访问帮助内联；作品导出失败步骤可见。

### 升级说明

- 本版本不新增存档 schema 迁移。
- 升级前请备份 `data/saves`；已结束的存档继续保持只读。
- 内容包作者请注意：如果包面向特定规则，请在 manifest 中声明 `compatible_rulesets`（归属标签）；确需硬依赖某个已注册运行时才写 `requires.rulesets`。旧的 `dnd5e` 别名不再自动映射为 `dnd2024`，请显式书写。

## English

### Call of Cthulhu (CoC 7e)

- New CoC 7e rules track: adjudication tiers and opposed checks, Sanity (SAN) settlement, Luck and Inspiration, 7e character creation, and narrative auditing.
- Server-side hardening: SAN settlement now enforces an authority boundary (off-scene or dead characters are no longer settled by proxy); rules that declare character-creation bounds validate skill budgets, skill counts, and per-skill caps on entry, rejecting over-budget sheets that bypass the frontend; d100 rules no longer apply the D&D ability damage modifier, preventing inflated auto-combat damage.
- Neutral adventures and legacy partial-attribute sheets keep the existing clamp/normalize path; opponent attribute scaling only widens percentiles when explicitly declared.
- Known limitation: CoC auto-combat remains restricted (HP-zero semantics, piercing damage, full-width punctuation tags — see issue #698); do not present it as fully supported yet.

### D&D 2024

- Added Extra Attack and 3D item activation; rolling the destroy-on-empty check when an activation spends the last charge.
- Fixed: opportunity attacks choose melee weapons by actual damage components; attack counts read through the same ownership filter as the sheet; a legacy attuned shield no longer bypasses the attunement gate; narrative death recompute stays localized and tolerates corrupt charge state; a bad destroy_on_empty is rejected at load and its anonymous row cleared; backpack take agrees with the refs the counts derive.

### Content packs and creator tools

- Creator experience: one export dialog for every content pack option, cover upload and marketplace display, declared content languages (supported_locales), a LICENSE shipped with repository-format exports, a publishing guide (authors publish to their own repository and submit via the listing form), and clearer `.dfplugin` unpack/re-import instructions.
- Modules and marketplace: online shelf with Hub detail cards, filtering by content language and content profile labels, index credits, updates offered only when newer with content refreshed after update, aligned local import row, and unified pack naming.
- Reliability: marketplace catalog isolates bad entries per item — a single malformed pack no longer fails the whole directory; `compatible_rulesets` is read defensively, degrading invalid fields to an empty list with a warning.
- Ruleset labeling: split hard runtime dependencies (`requires.rulesets`) from attribution labels (`compatible_rulesets`); packs written for a specific system (e.g. CoC) can join filtering via labels without registering a runtime; filtering no longer hides packs that declare no rulesets; the unproven dnd5e → dnd2024 auto-alias was removed.

### Economy

- Hardening: the planner backstop no longer guesses prices; installment stages derive from the ledger after rollback; the narrative reward extractor stays to GM payments; deferred settlement effects gate on a receipt; no-dice rules keep only the economy result.

### GAL and adventure gameplay

- Unified GAL linkage between the active rule, play mode, and solo state: world-source switching (template/AI/custom), the AI base-rule input, and AI-generated rules no longer keep an illegal GAL combination — illegal states are fixed before creation instead of failing at submit.
- Draft restore converges on the effective rule explicitly, so AI drafts no longer restore into an unsavable state; the multiplayer option is disabled while the GAL rule is active; the redundant GAL preview toggle was removed.

### Interface and experience

- Combat HUD shows real player and NPC portraits; combat layout folds move buttons into the input row with a detail block and fixed action block, and the toolbox close button joins the title row.
- Theme-aware metal dice tray with a real toss; consistent library title sizing; unified lorebook wording; inline login-access help; visible failed steps in the export dialog.

### Upgrade notes

- This release adds no save-schema migration.
- Back up `data/saves` before upgrading; ended archives remain read-only.
- Content-pack authors: if your pack targets a specific ruleset, declare `compatible_rulesets` (attribution labels) in the manifest; write `requires.rulesets` only for a hard dependency on a registered runtime. The old `dnd5e` alias no longer maps to `dnd2024` automatically — spell it out.

## Downloads

- **普通 Windows 用户 / Windows portable users**: `DiceFrame-v3.1.0-windows-portable.zip`
- **源码运行用户 / Source-run users**: `DiceFrame-v3.1.0-windows.zip`
- **托管 Docker 更新 / Managed Docker update**: `DiceFrame-v3.1.0-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
