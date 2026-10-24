# DiceFrame 轨道 P：GAL 玩法施工计划

> 基线：`origin/main` @ 2026-10-13（实例 schema 43；轨道 R 完成 R0–R8 并经 #525–#532 演进为模块 lifecycle hooks；轨道 C 的 A–I 已全部合并；lorebook canonical spec 为 `lorebook_v3`）。本仓库本地 main 若落后 origin/main，先同步再基于本计划开工。
>
> 决策来源：[ADR-0010](adr/0010-gal-first-non-trpg-gameplay.md)（GAL 立项与八项边界决策）。本文只定义施工顺序、同步点与完成标准；与源码冲突时以源码和 [ENGINEERING_RULES](ENGINEERING_RULES.md) 为准。
>
> 轨道代号取 **P**（Playmode）：R / C / M 已被占用，且轨道 C 的步骤代号（PR A–I，上游提交信息使用 G2a–G2d 等）不应与玩法轨道的步骤号混淆。

---

# 0. 一句话结论

gal = 组合：freeform 系规则皮 + affection 模块槽 + Adventure 图（补 ending / 完成权 / 激活钩）+ 推进模式 `scene` + 立绘资产类型 + VN 呈现层。分两段交付：**v0 预览**（P0–P1，近乎零引擎改动，验证需求）与 **v1 完整玩法**（P2–P10，可通关）。generic path 全程零 gal 词表，跑团行为零回归。

---

# 1. 定位与边界

1. **做结构化、可通关的 galgame**：路线锁、章节、可达成 / 可失败的结局。结构感是产品差异点，也是"游戏而非情感陪伴服务"的定性依据。
2. **不做无结构情感陪伴聊天**（ADR-0010 §1）。
3. 官方内容与渠道边界（规范性，见 ADR-0010 §8 与 ENGINEERING_RULES §24）：内置与 Hub 内容全年龄；Hub 不收 NSFW 与版权角色二创卡，AI 生成立绘 / CG 须标注；官方口径用游戏语言；gal 局 `bot_surface = read_only`，不提供 QQ 群聊游玩。
4. 移动端原生不投入；web 响应式撑 v1，验证出需求后再排 `diceframe-mobile`。
5. 完整"命令面能力投影"不做；先 `bot_surface` 三档声明 + 绑定单点检查，等第二个命令面异构的玩法立项后归纳。

---

# 2. 底座现状（2026-10-13，origin/main 盘点）

| 能力 | 现状 | 位置 |
|---|---|---|
| 单人 + AI 叙事 | 已是 Web 默认路径，提交屏障已短路 | `routes/game_lifecycle_routes.py`、`turn_state.py` |
| ST / Tavern 卡导入 | 已落地，且已并入统一 content-sync 导入路径（confirmable import plans，G2b/G2c） | `services/character_cards.py` |
| 无骰叙事皮 | tavern_free 已存在，gal 皮照此模式新增 JSON | `templates/rules/tavern_free.json` |
| 内容契约 | 轨道 C 已交付：ContentKind / ContentRef 契约、ContentProjectionService、统一导入导出 | `src/content_modules/` |
| 路线分支 / 互斥 / 状态门 | graph_v2 + gates 可表达，fail-closed | `src/adventures/gates.py`、`graph_v2.py` |
| ending 节点 | 缺（NODE_TYPES 无 terminal） | `src/adventures/graph_v2.py` |
| LLM 节点完成权 | 缺（GM-only intent） | `services/ruleset_gameplay.py` |
| 节点激活 → 叙事钩子 | 缺 | `src/webui/services/adventure_runtime.py` ↔ 回合管线 |
| 好感度 | 缺（全仓为零） | 新模块槽 |
| 推进模式 | 仅 `narrative_round` | `engine/modules/progression_state.py` |
| 立绘资产 | 缺（avatar 256×256，scene_image 16:9） | 新 ContentKind |
| VN 呈现层 | 缺（可复用 `GameTimeline.vue` 聊天流 / `AdventurePanel.vue` 进度 / `SceneGalleryModal.vue`） | `frontend-v2` |
| 音色 | GPT-SoVITS + 参考 WAV 已有，缺角色 ↔ 音色绑定 | `services/speech.py`、`tts/providers.py`、`tts/profile_store.py` |

---

# 3. PR 序列

**v0 = P0 + P1；v1 = P2–P10。** 一个 PR 只做一件事；schema 各归各。

| 步骤 | 内容 | 依赖 | 主要落点 |
|---|---|---|---|
| P0 | 契约与守卫：`play_mode` 增 `gal`（创建流接受 / 校验，fail-closed）；玩法模式 transport 声明 `bot_surface`；绑定准入单点检查（现有跑团 = full，行为不变）；`tests/architecture/` 守卫：generic path 无具体玩法分支、bot bridge 与插件无玩法词表。纯契约，gal 尚不可玩 | 无 | `services/game_lifecycle.py`、`routes/pairing.py` / bind 命令、`tests/architecture/` |
| P1 | **v0 gal 预览**：gal 规则皮 JSON（照 tavern_free）；创建流入口（实验性标注）；现有 GameTimeline / 头像 / TTS / swipe 呈现。无好感度、无路线锁、无结局，仅需求验证，不作为正式功能宣传 | P0 | `templates/rules/`、创建流前端 |
| P2 | affection 模块槽（已实施）：`src/engine/modules/affection.py`，每个对象一个 [-100, 100] 整数分（对象为本局 NPC key——叙事 NPC 以显示名为 key——或世界实体 id，≤80 字符，至多 64 个），表示对队伍 / 主角的态度；按 lifecycle hooks 参与回合提交 / 中止 / 回退 / 重置（日志条目带 `pre_affection`）；实例 schema 47 → 48 为读者护栏，旧存档不补数据。分档、单轮上限、衰减等平衡参数归玩法配置，不进引擎。写入 API `adjust` 暂无调用方，P3 结算成为唯一写入者（架构守卫）。gates 接入改为新增 `affection.*` gate 类型，不投影到 `world.relation_status`（世界关系状态只有 active / inactive / severed，表达不了好感分档，投影会形成双权威），随 P3 或 P4 落地 | P0 | `engine/modules/affection.py`、`migrations/instance.py`、`tests/` |
| P3 | affection 结算权威（已实施）：规则 JSON 可选 `affection` 块（`max_delta_per_round` 1–20，未知字段 / 非法值构造时报错）声明启用，gal_narrative 设为 5；模型写 `AFFECTION:NPC名:变化量:原因` 只是提议，`src/commands/affection_effects.py` 结算：规则未启用一律丢弃，对象须是本局已登记 NPC（同轮先登记的也算），变化量须为整数，每对象每轮合计钳制到上限、每轮至多 8 个对象，模块再钳制到 [-100, 100]；结算按对局 `rule_id` 取规则（与 GM prompt 一致，不用世界默认规则）。启用时 GM prompt 规则附录说明标签并列出当前好感。经济提案屏障会连同好感一起延后。gates 接入仍待 P4 / P6 | P2 | `src/rules/affection_config.py`、`commands/affection_effects.py`、`state_update_applier.py`、`prompt_composer.py`、标签协议 |
| P4 | Adventure ending 语义（已实施）：NODE_TYPES 增 `ending`；结局节点不得有 transitions / 选择，`on_complete` 不得再完成其它节点；完成结局节点记录 `progress.ending_node_id`，关闭其余开放节点与选择请求，此后任何节点不可再完成。不升图 schema：旧运行时不认识该类型会整图拒绝（fail closed）。投影带 `ended`（所有人）与 `ending_node_id`（仅结局对该视角可见时）；场景上下文提示叙事者只写结局与尾声，隐藏结局不泄露文本。"达成即终局"止于冒险进度；对局本身的结束（run finale）由 P6 / P7 驱动 | 无（可与 P2/P3 并行） | `src/adventures/graph_v2.py`、`progress.py`、`scene_context.py`、`webui/services/adventures.py` |
| P5 | 叙事响应应用链泛化（已实施）：`apply_parsed_data_to_response` 移入 `commands/narrative_application.py`（`round_llm` 留 re-export 维持既有 import 路径），新增可选 `context_label` 供链外调用以自定义日志上下文；抽取通用尾部链 `apply_narrative_effects`（成长奖励 → 快捷行动 → 记忆 → embedding 冲刷 → 剧情 → 私信），`process_round_impl` 改为调用，链序与参数不变。`commit_deferred_economy_effects` 的手工链刻意不迁移：其步骤顺序与"记忆失败必须抛出以中止结算事务"的语义是刻意设计。行为不变，backend full pytest 全绿 | 无（可并行） | `commands/narrative_application.py`、`round_llm.py`、`round_processor.py` |
| P6 | 节点完成 proposal + 激活钩（已实施）：规则皮可声明顶层 `adventure_progression` block（`auto_confirm_completion` / `activation_narration`，缺省即关闭，跑团皮零变化；gal 皮开启）。启用后，回合提交后由 `adventure_effects.settle_adventure_completion` 结算叙事者提议：仍复用 GM 命令的同一完成事务（gates / world ops / ending 冻结 / 奖励出口，全有或全无回滚），确认失败即丢弃该提议并保持路线锁定（fail closed，路线卡死优于路线乱跳）；每次结算至多一次权威确认（链式上限）。激活过渡叙事为纯文本（`scene_transition.SceneTransitionNarrator`，复用场景上下文与结局收束指令），作为系统消息追加进刚完成的回合，叙事者不获得任何状态写权。编排收口在 `turns` 的回合后结算（与奖励自动结算同模式），wiring 在 api。架构守卫：结算 writer 单链 + `adventure_progression` 声明只属于玩法皮 | P4、P5 | `commands/adventure_effects.py`、`commands/scene_transition.py`、`rules/adventure_progression_config.py`、`webui/services/turns.py`、`webui/api.py` |
| P7 | 推进模式 `scene`（已实施）：`KNOWN_MODES` 增 `scene`（round 字段继续作为推进计数，经济纪元沿用 `progression.current_era` 同一单点，economy 零改动；不 bump schema，旧运行时按未知 mode fail closed）。`set_mode` 为安装 API；`should_advance` 对 scene 固定单人语义（不依赖 solo_mode 配置，防误装挂死）；`progression.ensure_advancement_allowed`：scene 模式下冒险抵达结局（P4 `ending_node_id`）后不再开启推进，narrative_round 跑团不受影响。gal 创建流安装 mode=scene 并**解除 v0 的不能绑定冒险包限制**（`GAL_CANNOT_BIND_ADVENTURE` 移除，结构化形态就位）。run finale 全自动流程不在本步（gal 收尾走既有手动 finale；ending 冻结 + 推进拒绝已构成对局终局）；单人行动条数维持替换式一条 | P0（建议在 P6 后合入以缩小状态机分支面） | `engine/modules/progression_state.py`、`engine/progression.py`、`engine/turn_state.py`、`webui/services/game_lifecycle.py` |
| P8 | 立绘资产类型 + 音色绑定：standing portrait 新增为 ContentKind（registry 已随轨道 C PR A 合并，直接扩展；portable schema 须客户端可独立读写，母方案 §3.7），含可选表情差分集，v1 UI 可只消费首图；角色 ↔ voice profile 绑定 | 无（可并行，建议 P4 后） | `src/content_modules/` ContentKind 注册、资产服务、`tts/profile_store.py` |
| P9 | VN 呈现层：对话框模式、立绘层、好感条、路线 / 结局 UI、自动播放 / 快进；全部界面语言 | P2–P8 | `frontend-v2`（新增 gal 呈现组件，复用 GameTimeline 数据层） |
| P10 | 样板 gal 内容包（已实施，v1 骨架）：原创全年龄样板《灯塔之夏》——样板世界 `templates/worlds/horizon_strait.json`（汐澜镇 + 女主林晚晴 lorebook，zh/en）+ 第一个内置 Adventure v2 包 `templates/adventures/lighthouse_summer/`（`core:adventure` 叙事 runtime、三章 7 节点、exclusive 抉择三选项对应三结局、告白选项挂好感门）。顺带落地 P2 预告的 `affection.score` gate 类型（阈值语义，分数未知不放行；不投影 `world.relation_status` 避免双权威）。端到端验收 `tests/test_gal_sample_content.py` 把 P2/P3/P4/P6/P7 串联实测：好感结算（钳制）→ choice gate 路线锁（好感不足告白被拒、fail closed）→ 抉择分支 → ending 冻结 → scene 推进拒绝。默认音色 / 立绘为用户侧 P8 绑定资产，内容包不携带（文档说明用法）；Hub 分发待审核规则，先内置 | P4 契约定型 | `templates/worlds/`、`templates/adventures/lighthouse_summer/`、`adventures/gates.py`、`tests/test_gal_sample_content.py` |

---

# 4. 同步点与交点

| 交点 | 规则 |
|---|---|
| 轨道 C PR A（ContentKind / ContentRef 契约） | 已合并；P8 直接在其 registry 上新增 standing portrait kind |
| 轨道 C PR D（运行时投影，d1/d2 已合并，R6-b 已消费） | P4–P6 在 `src/adventures/` 与 `content_modules` 投影之上扩展，不复制可见性谓词；与后续 C 轨道收尾 PR 共享文件时谁后合并谁 rebase（母方案 §1.3），跨轨道 owner 互评（母方案 §4.3） |
| 轨道 C PR G（统一导入导出，content-sync G2a–G2d 已合并） | P10 内容包与女主卡走统一 Draft → CommitPlan 路径；`device` 为一等 source_kind |
| M 轨道（模组 / 内容包） | 样板 gal 内容包（P10）若走 package 分发依赖 M1/M2；否则先内置 `templates/`，M3 详情页落地后作为首批样例迁移 |
| Schema | P2 升实例 schema（在合并时当前版本上 +1，不预留号码）；P4 若改冒险图 schema 只升 adventure 侧；同一 PR 不得推进两行（母方案 §3.5） |
| R 轨道遗留 | 无新交点；`narrative_round` 回合写入点不被轨道 P 改动语义 |

---

# 5. 完成标准

```text
gal 局从创建到达成结局全程无需人类 GM 席位干预
路线锁由 gates + affection 权威结算决定，LLM 无直接写权；gate 证据不足不放行
narrative_round 跑团行为不变，backend full pytest / frontend unit / vue-tsc / lint / build / E2E 全绿
generic path（engine 核心模块、generic webui services、bot bridge、平台插件）gal 词表 = 0（守卫）
gal 局 bot_surface = read_only：群聊仅可查询进度，绑定游玩被明确拒绝
内置样板全年龄；Hub 审核规则已更新（NSFW / 版权卡 / AI 标注）
玩家成本默认值：TTS 按需播放、CG 生成后缓存复用
好感度跨会话连续，存档恢复后状态正确
```

---

# 6. 不做的事

- 无结构情感陪伴模式与以情感依恋为目标的任何设计。
- Hub 收录 NSFW 或版权角色二创卡。
- 完整"命令面能力投影" / 组合层 DSL / 可视化编辑器（从 gal 实现归纳，不预先设计，ADR-0005 原则）。
- 移动端原生投入。
- 为 gal 改跑团体验；跑团行为由现有全套测试守住。

---

# 7. 开放问题

1. 立绘差分集的消费优先级：v1 UI 只用首图还是上表情切换（影响 P8/P9 切分）。
2. ~~affection 数值模型~~：P2 已定为有界整数（[-100, 100]，对队伍 / 主角），分级由玩法配置从数值派生；按席位区分的态度需要新的槽 schema，不从现有数据推测。
3. gal 皮与 tavern_free 的边界：纯规则皮，还是携带预设 prompt / 界面偏好（若后者，需明确归属，防止玩法配置渗进引擎）。
4. 样板 gal 的题材选择与作者排期（内容工作，现在立项）。
5. 好感度上限 / 衰减等平衡参数：留在玩法配置，不进引擎。
