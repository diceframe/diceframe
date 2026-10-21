# ADR-0010: GAL 作为第一个非跑团玩法

- Status: Accepted
- Date: 2026-10-13

## Context

[ADR-0005](0005-composable-game-platform.md) 与 [vNext 母方案](../ARCHITECTURE_VNEXT_MASTER_PLAN.md)把组合层的启动条件定为"第一个非跑团玩法立项"。本 ADR 记录该立项（GAL / 结构化视觉小说玩法）及其长期边界决策。施工顺序见 [轨道 P 计划](../GAL_TRACK_PLAN.md)，本文只记录决策。

### 产品与市场

AI GM 赛道竞品增多，托管型产品以"精心调过的 prompt + 托管服务"为主。长上下文模型的自由叙事能力在贬值，结构化运行时（状态、规则、结算、内容契约）在升值。AI 恋爱陪伴类需求量大但已是红海，且全部为无结构聊天；有路线、有结局的结构化 galgame 在 AI 产品中是空位，且与本项目内容架构（角色卡、世界书、冒险图）协同最强：女主 = 角色卡，事件 = 世界书条目，路线 = Adventure 图。

### 监管

2026 年起中国对 AI 情感陪伴服务实施专项约束：禁止未成年人使用恋爱 / 情感类 AI 关系，拟人 AI 须向用户明示，平台须安全评估并建未成年人模式。自托管软件的工具分发与情感陪伴服务运营是两种责任位置；项目自身运营面是 Hub、随仓库分发的内置内容、官方社区与插件默认行为，这四处的内容与口径是项目责任，AGPL 许可证不改变这一点。

### 底座盘点（2026-10 调查结论）

已具备：solo 单人是 Web 创建默认路径且提交屏障已短路；ST / Tavern 角色卡导入已落地（`src/webui/services/character_cards.py`，PNG chara chunk、V2/V3 卡形共用 data 信封、内嵌世界书走 canonical 路径）；tavern_free 无骰叙事皮已存在（`templates/rules/tavern_free.json`）；Adventure graph_v2 + gates 可表达互斥分支与状态触发激活，gates 直接读 world_state，"好感度 ≥ X 开放路线"当天可写。

缺失：ending / terminal 节点语义（`src/adventures/graph_v2.py` 的 NODE_TYPES 无此值）；LLM 叙事者的节点完成权（现为 GM-only intent，`src/webui/services/ruleset_gameplay.py`）；节点激活触发叙事的钩子；好感度状态（全仓为零）；场景制推进模式（`KNOWN_MODES` 仅 `narrative_round`）；立绘资产类型（现有 avatar 强制 256×256 方形，scene_image 为逐条 16:9 场景图）；VN 呈现层（前端整体缺失）。

## Decision

### 1. 定位：结构化、可通关的 galgame

gal 形态 = 女主（角色卡）+ 路线（Adventure 图 + gates）+ 好感度（affection 模块）+ 章节与结局（ending 节点）+ AI 叙事（GM 模型管线）。必须存在可达成 / 可失败的结局与路线锁；"结构感"是产品差异点，也是"游戏而非情感陪伴服务"的定性依据。

明确拒绝：无结构情感陪伴聊天（带好感度数字的连续对话）。该形态既无差异化（大厂托管产品的主场），也落入 AI 情感陪伴监管射程。

### 2. gal 是组合，不是引擎分支

gal = freeform 系规则皮（新 gal 皮 JSON，模式同 `tavern_free`，纯数据）+ affection 模块槽 + Adventure 图（补 ending / 完成权 / 激活钩）+ 推进模式 `scene` + 立绘资产类型 + VN 呈现层。`play_mode` 增第三值 `gal`（现为 `free` / `adventure`）。

generic path（engine 核心模块、generic webui services、bot bridge、平台插件）不得出现任何 gal 专属词汇或分支，由 `tests/architecture/` 守卫保证，先于功能合入。gal 概念只存在于：模块槽实现、玩法模式契约、gal 皮数据、前端 gal 呈现组件。

### 3. 好感度：模块槽 + 提议结算

affection 是自持五件套的模块槽（状态 / codec / 迁移 / 投影 / 测试），照 R7 模式（`src/engine/modules/`），不进 `GameInstance` 顶层。数值投影为 relation 类事实，供 Adventure gates（`world.relation_status`）消费；TRPG 侧 NPC 态度复用同一模块，不建第二套。

结算遵循 [工程规则](../ENGINEERING_RULES.md) §13：LLM 叙事输出中的好感变化是提议，经引擎权威校验与钳制后生效，模式仿 economy 的 proposal 路径（`src/engine/economy.py` + `commands/economy_effects.py`）。

### 4. 推进模式 `scene`

`KNOWN_MODES`（`src/engine/modules/progression_state.py`）新增 `scene`：无回合屏障、按场景 / 事件推进。回合形状假设（回合状态机、经济纪元键、单人行动条数上限）按模式分支，收口在 progression 模块；`narrative_round` 的跑团行为全程不变。

### 5. 节点权威与叙事应用链

现有 GM-only 的 `adventure.node.complete` 扩展为 proposal 权限：LLM 叙事者可提议节点完成，引擎按 gates 与进度权威确认，未知 / 证据不足一律不放行（fail-closed，路线卡死优于路线乱跳）。节点激活触发场景叙事，依赖把 `apply_parsed_data_to_response`（`commands/round_llm.py`）的状态应用链从回合管线泛化为通用件——该泛化是行为不变的抽取，跑团全绿是验收条件。

### 6. transport 能力声明

玩法模式契约携带 `bot_surface: full | read_only | none`，绑定准入（bind 命令 / pairing 路由）单点检查并返回明确理由。bridge_core 与平台插件不为具体玩法新增分支（现状命令面是跑团形状，gal 的限制不在其中实现）。gal 初始 `bot_surface = read_only`（群聊可查章节进度，不可游玩）；现有跑团 = `full`，行为不变。完整"命令面能力投影"不在本次范围，等第二个命令面异构的玩法（预计狼人杀）立项后归纳。

### 7. 立绘资产类型

新增 ContentKind：standing portrait（全身透明立绘，含可选表情差分集，v1 UI 可只消费首图）。这是 gal 唯一的新 ContentKind（ContentKind registry 已随轨道 C PR A 合并，直接扩展；portable schema 须客户端可独立读写）；女主、事件、路线全部复用现有 kind。角色与 GPT-SoVITS voice profile 增加绑定关系（"每个女主固定音色"）。

### 8. 官方内容与渠道边界

以下为规范性约束，落实进 [工程规则](../ENGINEERING_RULES.md) 与 Hub 审核规则：

- 随仓库分发与 Hub 分发的 gal 内容一律全年龄；官方样板 gal 必须全年龄。
- Hub 不收 NSFW 内容与版权角色二创卡；AI 生成立绘 / CG 须标注。
- 官方口径（README、官网、社区）使用游戏语言（章节、路线、结局），不使用陪伴语言，不做"她不是 AI"式表达。
- gal 局默认不提供 QQ 群聊游玩（`bot_surface = read_only`）。

## Alternatives Considered

| 方案 | 取舍 |
|---|---|
| 狼人杀作为第一玩法 | 最大化复用 R6 席位可见性 / 私频 / 阶段推进，内容需求最小；但不带动内容生态，与角色卡 / 世界书 / 冒险图协同弱。保持第二顺位，底座不冲突 |
| 无结构 AI 恋爱陪伴聊天 | 需求量最大；红海（大厂托管产品主场），合规定性落入情感陪伴服务，且无结构不可积累内容资产。拒绝 |
| 独立仓库 / 独立产品做 gal | 隔离干净；重复建设运行时，违背平台方向与 ADR-0005。拒绝 |
| 等 C 轨道 PR A–I 完成再立项 | 核实上游后 A–I 已于 2026-10 全部合并，不构成等待；gal 直接在其成果（ContentKind、投影、统一导入导出）之上施工 |

## Consequences

### Benefits

- ADR-0005 组合层触发条件满足，组合契约可以从 gal 实现中归纳，不必预先设计 DSL。
- 内容生态获得启动器：ST 卡即女主，冒险图即路线，Hub / 本地包有第一批异质内容。
- 直接消费轨道 C 已落地的内容契约（ContentKind、统一导入导出、投影），成为其首个异质内容消费方；M 轨道（模组详情页 / 内容包）获得首批 gal 样例。
- 移动端 web 响应式 + 长会话场景成立，为后续 mobile 投入提供依据。

### Costs / Risks

- VN 呈现层是最大单块工程（对话框模式、立绘层、好感条、路线 / 结局 UI、自动播放，四语言）。
- 立绘跨 CG 一致性是行业级难题；卖点押在文本 + 语音 + 结构，不押 CG 质量。
- gal 的长会话模式（每日短会话）与跑团（一晚一局）不同：存档恢复手感、好感度跨会话连续、记忆召回节奏需按 gal 调整；TTS 逐句与 CG 生成烧玩家 API key，按需播放与生成缓存必须是 v1 默认值。
- 生态需求错配：该品类需求大头与 NSFW 重叠，官方渠道只承载全年龄，Hub gal 内容量预期有限；本地文件分发是一等公民。
- 内容空窗风险：样板 gal 是人力内容工作，须与 v1 同步排期。

### Migration / Compatibility

- 实例 schema：affection 模块槽自升一步（在合并时当前版本上 +1；撰写本 ADR 时上游为 43，按母方案 §3.5 不预留号码），各归各。
- `play_mode` 新值 `gal` 对旧存档无影响（旧值校验不变，未知值 fail-closed 拒绝）。
- `bot_surface` 对现有跑团局 = `full`，绑定行为不变。
- Adventure 图 schema 新增 ending 节点类型为可选枚举，旧图无 ending 照常运行。
- 无法证明安全的转换一律 fail-closed。

## Validation

- gal 局从创建到达成结局全程无需人类 GM 席位干预。
- 路线锁由 gates + affection 权威结算决定，LLM 无直接写权；gate 证据不足时路线不放行。
- `narrative_round` 跑团行为不变，backend full pytest 零回归。
- architecture 守卫：generic path 中 gal 词表 = 0；bot bridge 与平台插件无具体玩法分支。
- gal 局 `bot_surface = read_only`：群聊仅可查询进度。
- 内置样板全年龄；Hub 审核规则已更新；官方文档口径为游戏语言。

## Supersedes / Superseded By

None。本 ADR 触发 [ADR-0005](0005-composable-game-platform.md) 组合层部分的立项条件，不取代它。
