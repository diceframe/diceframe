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
| P3 | affection 结算权威：prompt 注入好感状态；模型提议 → 引擎校验钳制结算（仿 economy proposal）；拒绝路径与测试 | P2 | `engine/affection_*`、`commands/`、prompt 组装 |
| P4 | Adventure ending 语义：NODE_TYPES 增 ending（可选枚举，旧图照常）；进度投影；达成即终局 | 无（可与 P2/P3 并行） | `src/adventures/graph_v2.py`、`progress.py`、投影 |
| P5 | 叙事响应应用链泛化：`apply_parsed_data_to_response` 出回合管线，行为不变，跑团全绿为验收 | 无（可并行） | `commands/round_llm.py`、`round_processor.py` |
| P6 | 节点完成 proposal + 激活钩：LLM 提议节点完成，引擎按 gates 权威确认（fail-closed）；节点激活触发场景叙事 | P4、P5 | `services/ruleset_gameplay.py`、`src/webui/services/adventure_runtime.py` |
| P7 | 推进模式 `scene`：`KNOWN_MODES` 新值；回合状态机、经济纪元键、单人行动条数按模式分支，收口在 progression；`narrative_round` 行为不变 | P0（建议在 P6 后合入以缩小状态机分支面） | `engine/modules/progression_state.py`、`engine/progression.py`、`economy.py` |
| P8 | 立绘资产类型 + 音色绑定：standing portrait 新增为 ContentKind（registry 已随轨道 C PR A 合并，直接扩展；portable schema 须客户端可独立读写，母方案 §3.7），含可选表情差分集，v1 UI 可只消费首图；角色 ↔ voice profile 绑定 | 无（可并行，建议 P4 后） | `src/content_modules/` ContentKind 注册、资产服务、`tts/profile_store.py` |
| P9 | VN 呈现层：对话框模式、立绘层、好感条、路线 / 结局 UI、自动播放 / 快进；全部界面语言 | P2–P8 | `frontend-v2`（新增 gal 呈现组件，复用 GameTimeline 数据层） |
| P10 | 样板 gal 内容包（全年龄）：女主卡 + 路线图 + 事件条目 + 默认音色 / 立绘配置。内容工作，与 P7+ 并行排期，不阻塞代码 PR；走统一 Draft → CommitPlan 导入导出路径 | P4 契约定型 | `templates/` 或内容包 |

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
