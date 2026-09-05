# DiceFrame 架构事实来源

本文描述当前实现，不是路线图。代码依赖方向为 `routes -> WebAPI -> services -> 核心`；核心层不得导入 `src.webui`，WebAPI 是委托层，跨 service 调用经由 API 委托。

## WebUI 启动与配置

`web_server.py` 是源码版、Windows 便携版和 Docker 共用的稳定启动入口，主要负责加载项目环境、组合明确的 WebUI owner，并启动 aiohttp listener。具体职责位于：

- `src/webui/runtime_config.py`：`RuntimeConfig` / `ConfigStore`，唯一应用 `env > secrets.json > config.json` 优先级并负责敏感配置分离、脱敏与原子写入；
- `src/webui/composition.py`：从显式路径、配置状态和 factory 构造核心 subsystem 与 `WebAPI`；
- `src/webui/application.py`：`create_app`、middleware 与 route composition，不启动监听器；
- `src/webui/bootstrap.py`：模板同步、插件/Hub 启动、后台任务、存档恢复和清理；
- `src/webui/access_control.py`：owner、Bot、SSE ticket、玩家分享与房间密码访问边界；
- `src/webui/config_controller.py`：配置热重载事务和服务商连接测试。

AI 应用配置仅使用 `ai_providers` 与各能力的 `*_provider_ref`；凭据以 `ai_provider_key_<id>` 单独保存。旧能力级直填地址/密钥/API 格式及 AI 能力环境入口不再参与解析，更新请求包含旧字段时明确拒绝，不自动迁移或创建服务商。缺失/未知引用不会激活残留配置；browser、edge-tts 与 disabled ASR 无需引用，本地服务商允许空 key。composition 解析后传给服务的内部 `*_base_url` / `*_api_key` 仍是有效运行时契约。

模板同步和配置默认值迁移写盘只在真实 application startup 发生，不在导入独立 owner 模块时发生。配置热重载必须先完整构造候选 runtime，写盘成功后才替换活动状态；构造或写盘失败均保留旧 runtime。

WebUI service 不直接导入另一个 service。跨域业务调用使用 composition root 注入的 callable/protocol；多域共同使用但不执行业务编排的纯契约和投影位于 `src/webui/` 根边界，例如生命周期事务上下文、规则草稿 shape 校验、休息只读投影及角色卡 identity/deduplication。类型检查专用导入不构成运行时依赖。

## Content V2

所有输入先经过兼容边界，再进入当前 canonical model：

```text
Legacy / V1 Rule / Plugin / Save / World / Character
                    ↓
              Compatibility
                    ↓
          Canonical Current Model
                    ↓
            Runtime Mechanics
                    ↓
             Typed Locale
                    ↓
                   UI
```

Canonical identity 是稳定引用键，例如 `fighter`、`longsword`、`chain_mail`、`athletics`、`str`、`npc_innkeeper`。`战士 / Fighter`、`长剑 / Longsword / ロングソード`、`老汤姆 / Old Tom` 只是 display text。切换语言不得改变 ID。

正常 V2 runtime 的 mechanics authority 是 canonical rule/content。`ARMOR_LITE`、`WEAPON_DAMAGE`、`WEAPON_DAMAGE_DICE` 等旧表只用于旧存档、V1 或 legacy fallback。

## Rule Locale

Rule core 保留 `dice_system`、`damage_dice`、`ac_base`、`dex_cap`、`attribute_points`、`proficiency`、`combat_model`、`skill_pools`、`item_categories`、伤害/死亡机制以及 permissions、capabilities、scripts。职业技能池使用 class/skill canonical ID；typed locale 只能翻译这些 ID 的显示名，不能替换技能池或物品分类。嵌套 unknown/mechanics 字段必须拒绝。

## World Locale

World core 拥有 `world_id`、`default_rule`、`recommended_rules`、`suggested_difficulty` 以及 starter lorebook 的 entry set/order、ID、type、tier、`unreliable`、`sync_on_enter`、`triggers_recursive`、`visible_to`、`match_mode`、`sticky`、`cooldown`、`delay`、`order`、`probability`、`group`、`group_weight`、`connected_to` 等确定性字段。

World locale 只能修改 `world_name`、`description`、`world_setting`、`starter_scene`，以及按 canonical lore entry ID 修改 `name`、`keywords`、`content`。World Locale cannot replace `starter_lorebook` entries. Language changes cannot add, remove, or rename canonical lore identities。

例如 core ID 为 `npc_innkeeper`，中文可以是 `npc_innkeeper.name = 老汤姆`，英文可以是 `npc_innkeeper.name = Old Tom`；identity 永远是 `npc_innkeeper`。

世界书数据库保存 canonical/core 条目；关键词匹配、prompt 和谜题初始化按每局 `GameInstance.language` 构造只读本地化视图，不把译文写回共享数据库。

## Plugin Content V2

Manifest 当前支持：`schema_version = 1`、`content_schema_version = 1 or 2`、`locale_schema_version = 1`，以及 package locale fallback 的 `default_locale`。Locale fallback 为 exact requested locale -> base locale -> package/default locale -> base(default locale) -> canonical/core display fallback。

`ResourceRef` 示例：`core:item:longsword`、`plugin:my-pack:item:moon_blade`。普通 V2 item/class/spell/npc/character_template 可以通过 namespace 共存。Rule/World 仍主要使用 plain `rule_id` / `world_id`，因此不同 V2 plugin 的重复 Rule/World ID 必须明确拒绝，不能 first-wins 或 last-wins。

V2 资源 ID 必须已经是 canonical 形式；注册器不会替插件把大小写、空格或非 ASCII ID 悄悄归一化。V2 locale 或内容校验失败时，目录 API 返回 `CONTENT_VALIDATION_FAILED`，不得省略损坏资源或回退到未本地化内容。应用内内容包导出器始终生成 Content V2 core + typed locale 布局；V1 全文副本只在导入适配器中支持。

## Plugin 运行时扩展边界

`src/plugin_host/support.py` 是插件类型、process mode、推导权限和 contribution mapping 的单一元数据来源。`src/plugin_host/descriptors.py` 负责将不可信 initialize payload 校验为 typed descriptor；`src/plugin_host/capabilities.py` 负责 RPC capability 初始化、查询和投影；`PluginHost` 保留 package、process、lifecycle、security 与兼容 facade 职责。

新增合法 provider capability kind 只需插件实现、SDK 契约和测试，不修改 `PluginHost`。只有真正新增 plugin type 时，才评估 support descriptor、runtime initializer、permissions、cleanup 和对外 metadata。贡献路径见 `docs/plugins/EXTENDING_CN.md`。

## Migration 与 Compatibility

`src/migrations/` 负责 persisted schema upgrade；`src/compat/` 负责 old external/runtime shape 到当前 canonical model 的兼容。V1 包通过适配器读取，不能把兼容分支散回正常业务逻辑。

持久化 `GameInstance` 加载后的迁移统一经过 `src.migrations.migrate_instance` 编排入口。各数据域的具体迁移可以由 `src/compat/` 提供纯适配实现，但 service、route 和 runtime 不得直接分散调用域适配器。迁移必须幂等、可测试、按明确的版本/identity/digest 边界执行；无法证明安全迁移时 fail closed。新增功能应新增版本化迁移步骤，不修改已发布迁移的语义。

## GameInstance 聚合边界

`GameInstance` 是单局对局的 Aggregate Root，继续拥有权威运行时状态、不变量、状态转换以及 `_authority_lock` / `_process_lock` / `_lock` 协调权。锁顺序固定为 authority → process → state；runtime lock 不进入存档，也不随 persisted-state replacement 复制。历史重写独占 authority gate，普通 live writer 通过同一原子 gate 在修改前拒绝，不能用分离的布尔检查制造 TOCTOU。玩家、战斗、回合和支付不会仅为缩短文件而拆成彼此独立的 aggregate。

每个存档同时具有稳定 `game_key` 和可轮换 `run_id`。程序恢复保留 `run_id`；重置与重开在旧聚合写锁内构造候选聚合并完成原子替换，等待中的旧 run 写入在替换后按 stale run 拒绝，开场已经应用到候选角色的状态不会被旧角色整表覆盖。历史 swipe 重写与正常回合共用 `_process_lock`，并一直持锁到“恢复旧快照 → LLM → 应用新分支 → 权威存档”完成；玩家行动在重写期间于写入前拒绝。长期记忆通过持久化 `memory_namespace` 隔离，隔离不依赖先删除旧记录。重开保留角色、资产和成长但清除死亡、战斗、剧情与待处理提案；重置同时清除角色。存档 shape 的升级只经 `src/migrations/instance.py` 的顺序迁移入口。

通用经济状态属于 `GameInstance`。叙事 `GOLD` / `PAY`、世界书文本与 AI 输出只能创建提案；余额变化必须经过服务端权限、余额、run identity 与幂等校验并写入事务流水。`currency.amount` 是余额 authority，`gold` 仅为兼容投影。个人支付由付款人确认，自由叙事奖励由 GM 确认，Web、Bot 与其它 transport 进入同一经济路径。

经济提案同时是叙事提交屏障：当前 run 仍有待决定提案、未提交效果组或待投递/待撤销外部效果时，行动、强制推进、幸运续接、SSE 与直接回合处理均不得开始下一段叙事。同一模型回复中的场景、角色状态、物品、任务、记忆、私密信息与快捷行动先持久化为挂起效果，不得在付款决定前成为权威状态。单项确认后提交一次；同轮多项提案必须全部提交后才应用整组效果，任一拒绝、取消或余额不足都会丢弃整组效果。SQLite 记忆属于跨存储外部效果：先随游戏存档写入持久 outbox，再以 delivery identity 幂等投递并记录可验证的前后镜像；swipe/rollback 会持久化撤销请求并恢复仍属于该 delivery 的记忆，且投递或撤销回执未落盘均可在启动或下一次推进前重试。交易关联的场景图 prompt 在 staged 效果中被隔离，只有第一次权威存档成功后才允许启动异步生图。最终结果写入有界经济 outcome，并作为可信服务端上下文覆盖此前模型叙事；经济修订号用于阻止决定期间仍在飞行的旧 AI 回复落地。重开与重置均清空提案、流水、outcome、挂起效果、outbox 和修订号；重开只保留已经结算进角色卡的余额，重置同时清除角色。

附属投影有独立 owner：`src/engine/game_state_codec.py` 负责稳定存档投影与重建，`src/engine/game_context_projector.py` 负责通用 LLM/展示视图；旧存档 payload 的 shape 归一化位于 `src/migrations/instance.py`，在构造聚合前对副本执行，不修改调用方输入。`GameInstance.to_dict()`、`from_dict()` 与 `to_llm_view()` 是兼容委托，不再实现这些投影。旧版属性修正、护甲求和和字符串技能默认值由独立的 `src/engine/legacy_game_projection.py` 提供，并由 `LegacyRulesetAdapter` 显式采用；Ruleset runtime 可以在通用投影之上追加自己的权威视图，但不能把具体 mechanics 写回通用 projector。

这是第一轮 codec / projection / migration 边界抽取，不表示 generic state shape 已经终局化或完全去规则化。通用投影为兼容现有世界、存档与 prompt，仍保留 `hp`、`max_hp`、`class`、`race`、`level`、`attributes`、`equipment`、`skills`、`inventory` 等传统角色字段；这些 compatibility shape 后续仍可在不破坏存档和规则运行时契约的前提下继续收口。

`src/engine/game_state_contracts.py` 声明存档顶层、通用上下文和玩家回滚快照的 typed contract。`ruleset_runtime`、`ruleset_state`、`adventure_binding` 扩展 payload、event payload 和 character extension fields 在 generic engine 内有意保持 opaque。新增持久化字段时，必须依次检查：`GameInstance` 权威字段 → `GamePersistedState` → codec encode/decode → migration/default 兼容 → 只在 LLM/UI 需要时才增加 projection → behavior regression。

## 应用更新边界

Windows source/portable 与托管 Docker 共用 `src/webui/services/updater.py` 的下载状态机，但安装提交权分离：source 使用备份事务，portable 由 Windows launcher 提交，Docker 候选只能由镜像内稳定的 `src/docker_launcher/` 在健康检查和观察期通过后提交。Docker 应用进程只能写相对候选路径的 restart signal，不得控制 Docker daemon、挂载 Docker socket或覆盖当前版本目录。

Docker Update schema 1 绑定版本、`linux-amd64`、CPython ABI、launcher schema、基础 runtime API 与 `data_rollback_safe`。更新包构建器、应用 updater 和 launcher 必须复用同一 contracts 校验；checksum、平台、ABI、runtime、数据回滚声明或路径安全失败全部 fail closed。版本化应用副本位于 `data/_updater/docker-versions/`，业务数据迁移仍归 `src/migrations/`，程序目录回滚不得冒充数据 schema 回滚。

运行日志统一由 `src/runtime_logging.py` 管理，launcher 和业务服务不得各自实现轮转或保留策略。便携版日志位于安装根目录 `logs/`，托管 Docker 位于持久化 `data/logs/`，默认保留 30 天；清理接口只允许删除 DiceFrame 运行日志，不得触碰对局记录、存档或第三方日志。

DF 助手仅在 owner 主动提出检查运行日志时，经 `src/runtime_diagnostics.py` 读取 DiceFrame 自身最近两个日志文件；本地只负责凭据脱敏、成功轮询过滤、重复事件压缩和上下文限额，故障判断仍由当前配置的模型完成。发送上下文最多 24,000 字符，不得读取任意文件，也不得把日志内容当成指令。

## Frontend 与规则边界

Backend materializes V2 locale，frontend 只渲染返回字段，不重新实现 Content V2 locale architecture。D&D 如何使用 d20 不等于修改 generic d20 本身；D&D 专属行为必须留在 D&D 边界内。

## Ruleset Runtime

`src/rulesets/` 是版本化规则运行时边界。规则模板缺少 `runtime` 时显式回退到 `core:legacy`，继续使用现有 RuleSystem、RoundProcessor、CombatResolver 和 ProgressionResolver。新运行时必须由 canonical `runtime.id` 绑定，不能根据 `rule_id`、翻译名或 mechanics 字符串模糊推断。未知或版本不兼容的 runtime 必须拒绝。

Ruleset runtime 可导入通用 engine 原语；generic engine、generic d20、memory、lorebook 不得反向导入任何具体规则运行时。WebAPI 和前端只通过 `ruleset_runtime` capabilities 了解体验能力。

当前完成的是第一轮 ruleset capability normalization：主要 D&D 专属语义已移出 generic 层，并建立了可继续收缩的 optional runtime capability 边界。`RulesetRuntime` 主协议仍承载角色构建、验证、intent、事件、投影和迁移等较宽的基础契约；这不是“所有规则能力都已独立 capability 化”或 runtime 协议已经最小化的声明。

## Ruleset Bundle v1

`templates/rulesets/<directory_id>/` 是第一方高级规则的离线内容快照，不是 Plugin Content V2 的替代。Bundle manifest 绑定 `bundle_id`、`runtime_id`、规则/内容版本、locale 与归属文件。Canonical entity 必须具有稳定 `kind:id`、`source_ref` 和 `automation_level`。

Bundle locale 只能物化白名单展示字段。效果使用白名单 DSL；任意代码执行键、未知效果原语、重复 ID、无效内部引用、越界归属路径或 locale mechanics override 都会使整个 bundle 加载失败。详细格式见 `docs/rulesets/dnd2024/CONTENT_BUNDLE_CN.md`。

## Adventure Bundle v1

高级玩法由四个相互独立的输入组成：Ruleset Runtime 提供机制，Worldbook 提供世界设定与 lore，可选 Adventure Bundle 提供剧情图、场景、NPC、地图位置与冒险专属遭遇，Coach 只在前端提供本地帮助。未绑定 Adventure Bundle 就是标准自由对局，不得暗中加载固定教学剧情。

独立冒险位于 `templates/adventures/<directory_id>/`，采用 `diceframe:adventure-graph-v1`。Manifest 声明 canonical adventure ID、版本、世界策略以及最低 runtime 契约。创建游戏时先校验规则、runtime、格式和世界兼容性，再不可变地保存 `adventure_id / version / format / content_digest / world_id`；重开必须保留并重新校验同一绑定，内容丢失、被改动或 fixed-world 不匹配时直接拒绝。详细格式见 `docs/adventures/ADVENTURE_BUNDLE_CN.md`。

服务启动时，内置冒险包以完整目录为单位同步到 `data/templates/adventures/`；DND runtime、目录 API 和管理 API 共同读取该运行目录。内置包只读，自定义包使用独立 canonical identity，可复制、校验编辑、ZIP 导入/导出和删除。任何已被存档绑定的包禁止编辑或删除，避免破坏固定摘要与重开确定性；所有写入先在临时目录通过同一个 `AdventureBundleLoader` 完整校验，再替换正式目录。

冒险步骤只能替代当前剧情入口，不能替代玩家选择的世界书。叙事上下文始终包含实际 Worldbook 的设定、起始场景与匹配 lore；冒险完成后回到同一世界的标准自由对局，而不是停留在“教程已结束”死页。

## D&D 2024 权威游戏状态

`core:dnd2024` 的战斗、Session 0 与战役记录共享 `GameInstance.ruleset_state.version` 和 EventBatch ledger；可选冒险通过精确绑定向同一状态机提供剧情输入，但不是 Ruleset Bundle 的一部分。战斗事件只由战斗 reducer 应用，战役事件只由 campaign reducer 应用；runtime composition root 按显式 `intent_type` 分派，generic engine 不导入 D&D 实现。

高级规则角色的机械权威是 `ruleset_character`。创建、共享卡库导入/编辑、加入游戏、游戏内资料编辑、升级和休息均经由 `character_lifecycle` capability；legacy 顶层角色字段只是兼容投影。资料编辑不得覆盖属性、HP、AC、成长历史、runtime/content/state 版本等机械字段，机械更新必须从 canonical 选择与历史重新验证或回放。

Session 0 的每次修订都会清空旧成员确认，只有全部当前玩家接受后 GM 才能锁定。任务、线索、事实、重要物品和关系先保存为 pending proposal，再由 GM 以独立 Intent 确认或拒绝。章节摘要是已确认事件的确定性投影，并在存档成功后写入长期记忆；记忆投影失败不得回滚或伪装已经持久化的权威状态。

自然语言行动继续使用 DiceFrame 唯一的 `/action` 回合流程：单人即时推进，多人先收齐当前存活且在场成员的行动，再统一进行检定与 GM 回复。D&D runtime 只向同一 LLM 上下文追加权威战斗、战役和当前冒险节点的只读信息；所选 Worldbook 与匹配 lore 仍由通用叙事管线提供。LLM 不得直接创建战役事实、扣减资源或推进权威冒险步骤。

前端保留通用的单时间线、单行动输入框、角色卡、队伍状态、地图、场景图库、规则说明、世界书和 GM 控制台。左侧 `DND5E工具` 只包含冒险/战役与权威战斗两个 D&D 专属入口；工具以有界弹层覆盖主游玩区，不建立第二套消息流或第二套叙事提交接口。冒险节点进入遭遇门槛时会切换到战斗工具；自由剧情中 GM 明确裁定进入先攻，或玩家提交明确攻击并通过通用判定规划识别后，会产生只负责唤醒界面的 `encounter_request`，具体预设、先攻与所有机械结算仍须经过权威战斗 Intent。结束后回到同一条公共时间线继续游玩。

剧情遭遇访问权由 runtime composition root 根据 canonical adventure step 投影成 `EncounterAccess`；campaign 与 combat 引擎不得互相导入。战斗开始事件保存 canonical `encounter_instance_id`、preset 与来源 step，结束后写入 bounded history；剧情门槛只接受匹配的 encounter identity，因此已消费的冒险遭遇不能再次启动。敌方回合由服务端按同一验证、事件和 reducer 管线自动结算；每位玩家只能操作自己的角色，非当前玩家明确处于等待状态。场景、NPC 与地图位置使用 Adventure Bundle canonical refs，locale 只物化显示字段。直接联机桥接对玩家 Intent 使用字段白名单。
