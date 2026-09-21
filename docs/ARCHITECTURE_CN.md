# DiceFrame 架构事实来源

本文描述当前实现，不是路线图。代码依赖方向为 `routes -> WebAPI -> services -> 核心`；核心层不得导入 `src.webui`，WebAPI 是委托层，跨 service 调用经由 API 委托。

## 场景生图与参考头像

场景图沿 `routes/generated_images -> WebAPI -> services/generated_images -> src/imagegen` 生成；手动生图与分镜分析仍为 GM/server-only 操作，不新增玩家或 P2P 写入口。手动请求的 `combine_avatar_references` 是可选布尔值，省略时为 `true`，仅在用户启用头像参考时生效。`src/imagegen/reference_sheet.py` 将实际选中的多个头像按输入顺序拼成一张临时编号参考图，提示词绑定 `Ref N` 与稳定玩家 ID / 公开名称；单头像保持原文件，显式关闭拼接则保留多文件上传。参考表不是输出分镜布局，不持久化图像输入；生成记录仅保留角色 ID、来源数量、实际上传数量及拼接状态。

手动分镜的 `panel_count` 省略表示自动，指定时只接受整数 1–6。固定模式使用独立的模型数量要求，最多纠正一次，原始数量、有效内容和证据均通过才返回候选，不允许通过截断、空格补齐或自动回退满足数量。候选与缓存绑定剧情版本及目标格数；前端修改目标不覆盖已应用稿，不匹配的候选不能应用。生图入口再次验证数量，所有格均进入提示词，预算不足以保留每格地点、人物与动作时明确失败。图像模型自行选择构图，程序不加分割线，也不将请求格数视为视觉验证结果。自动分镜开关及 `SCENE_IMAGE` 触发语义不变。

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

## 访问凭据与扫码配对

Owner 访问有两类平级凭据，都以 `Authorization: Bearer` 提交，由 `src/webui/access_control.py` 统一判定：

- 访问密码：`STATE["access_token"]` 只保存 PBKDF2 哈希，服务端不掌握明文，任何接口都不得把它兑换出去；
- 设备令牌：`src/webui/device_tokens.py` 的高熵随机串，落盘只存 sha256 摘要（随机 token 无需 KDF，且它在每个请求上验证），逐台可吊销，吊销不牵连主密码与其它设备。

扫码登录由 `src/webui/pairing.py` 与 `src/webui/routes/pairing.py` 负责：owner 会话调 `POST /api/pairing` 签发一次性短 TTL 配对码（服务端同样只存摘要），移动端匿名调 `POST /api/pairing/claim` 兑换成设备令牌。兑换端点必须匿名可达（此刻手机还没有任何凭据），因此它与 `/api/login` 共用 abuse-guard 限流桶并写入同一份登录审计；配对码一次性、过期即作废、不续期。设备清单 `GET /api/devices` 与吊销 `DELETE /api/devices/{id}` / `POST /api/devices/revoke-all` 只对 owner 开放，清单不返回任何可用于鉴权的字段。

免密服务器上 `POST /api/pairing` 对任何能连上的客户端开放，那时签发的设备令牌等价于「谁连得上谁就是 owner」。因此首次设置访问密码（`access_token` 从未配置变为已配置）会连带吊销全部设备令牌与待兑换配对码；已有密码时再改密码不吊销——设备令牌是与访问密码平级的独立凭据，设置页有单独的逐台 / 全部吊销入口。

二维码要编的地址只有服务端知道——GM 本机浏览器的 origin 往往是 localhost，对手机无意义。`GET /api/system/network`（owner 限定）基于 `src/web_transport/local_addresses.py` 返回本机可达候选地址；该模块同时是自签证书 SAN 的地址来源。

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

所有 Ruleset 共用同一套通用 Lore 检索（`src/lorebook/retrieval.py`）：正常回合、swipe 与桌外问答都走同一个 `LoreRetriever`，检索输入是本轮行动加当前 scene / canonical location / 在场 NPC 锚点。既有 `KeywordMatcher` 仍是基础检索，语义检索只是可选的增强——沿用现有 embedding 配置，并复用 `MemoryStore.embedding_client` 这一个 `EmbeddingClient` 实例，不新建第二套 embedding 客户端；未配置或调用失败时自动退回锚点加关键词，绝不阻断回合。条目向量存在 `lorebook.db` 的 `lorebook_embeddings` 派生缓存里（按 entry / language / embedding_profile 隔离，`content_hash` 变化即重建），它不是 authority，删掉可自动重建。语义命中只代表"可能相关"，仍要过可见性与预算，既不写 WorldState 也不改 Ruleset 裁定，也不触发任何事件。

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

## 货币模型（Currency Model）

规则货币结构的唯一权威是 `src.engine.currency` 的 `CurrencySpec`：`currency_system`（`schema_version: 2`）声明 `base_unit`（canonical 整数的计数单位，rate 恒为 1）、`display_unit` 与各单位正整数 rate；只有 `currency` 名称的旧规则按 legacy rate=1 spec 兼容，不按名称猜单位。`parse_currency_amount` / `format_currency_amount` 是显示金额 ↔ canonical 整数的唯一转换入口（Decimal、不精确即拒绝），economy 引擎只处理 canonical base-unit 整数，不感知货币名称；禁止业务代码散落 ×100 / ÷100 或引入 float。V2 声明在 `RuleSystem` 构造与 `RuleBundleLoader` 加载边界 fail-fast，规则 CRUD、AI 生成规则与插件规则安装都复用同一校验；显示换算在前端 `frontend-v2/src/utils/currency.ts` 收口。`MAX_ECONOMY_AMOUNT` 是 base-unit 下的技术上限。只有 base_unit 语义变更的规则才迁移存量数据（当前仅内置 `freeform_coc` 美元→美分，×100 一次，经 schema v12 版本化迁移）；旧自定义规则与 legacy 规则数据不动，已有游戏实例的规则禁止通过编辑器修改 base_unit 语义。

## GameInstance 聚合边界

`GameInstance` 是单局对局的 Aggregate Root，继续拥有权威运行时状态、不变量、状态转换以及 `_authority_lock` / `_process_lock` / `_lock` 协调权。锁顺序固定为 authority → process → state；runtime lock 不进入存档，也不随 persisted-state replacement 复制。历史重写独占 authority gate，普通 live writer 通过同一原子 gate 在修改前拒绝，不能用分离的布尔检查制造 TOCTOU。玩家、战斗、回合和支付不会仅为缩短文件而拆成彼此独立的 aggregate。

每个存档同时具有稳定 `game_key` 和可轮换 `run_id`。程序恢复保留 `run_id`；重置与重开在旧聚合写锁内构造候选聚合并完成原子替换，等待中的旧 run 写入在替换后按 stale run 拒绝，开场已经应用到候选角色的状态不会被旧角色整表覆盖。历史 swipe 重写与正常回合共用 `_process_lock`，并一直持锁到“恢复旧快照 → LLM → 应用新分支 → 权威存档”完成；玩家行动在重写期间于写入前拒绝。经济回滚是整轮语义（ADR 0003）：回滚到第 N 轮会撤销第 N 轮及之后的一切结算，早于该轮创建、在其后才结算的提案恢复为待确认；物品恢复使用绝对快照投影，不做选择性库存差分；swipe 同时截断目标轮之后的日志分支并从该轮重放。长期记忆通过持久化 `memory_namespace` 隔离，隔离不依赖先删除旧记录。重开保留角色、资产和成长但清除死亡、战斗、剧情与待处理提案；重置同时清除角色。存档 shape 的升级只经 `src/migrations/instance.py` 的顺序迁移入口。

通用经济状态属于 `GameInstance`。叙事 `GOLD` / `PAY`、世界书文本与 AI 输出只能创建提案；余额变化必须经过服务端权限、余额、run identity 与幂等校验并写入事务流水。`currency.amount` 是余额 authority，`gold` 仅为兼容投影。个人支付由付款人确认，自由叙事奖励由 GM 确认——小额单人纯货币奖励可在本局奖励策略允许时经同一结算路径免 GM 点击自动到账；策略按 本局覆盖 → 规则模板 `economy_defaults` → 服务器全局兜底 解析，物品奖励、多人分摊与超上限奖励一律不自动结算。叙事奖励不做“任务完成证据”启发式拦截（该层会无声吞掉应得奖励），一律进入提案交由策略与 GM 判定。Web、Bot 与其它 transport 进入同一经济路径。

经济提案同时是叙事提交屏障：当前 run 仍有待决定提案、未提交效果组或待投递/待撤销外部效果时，行动、强制推进、幸运续接、SSE 与直接回合处理均不得开始下一段叙事。同一模型回复中的场景、角色状态、物品、任务、记忆、私密信息与快捷行动先持久化为挂起效果，不得在付款决定前成为权威状态。单项确认后提交一次；同轮多项提案必须全部提交后才应用整组效果，任一拒绝、取消或余额不足都会丢弃整组效果。SQLite 记忆属于跨存储外部效果：先随游戏存档写入持久 outbox，再以 delivery identity 幂等投递并记录可验证的前后镜像；swipe/rollback 会持久化撤销请求并恢复仍属于该 delivery 的记忆，且投递或撤销回执未落盘均可在启动或下一次推进前重试。交易关联的场景图 prompt 在 staged 效果中被隔离，只有第一次权威存档成功后才允许启动异步生图。最终结果写入有界经济 outcome，并作为可信服务端上下文覆盖此前模型叙事；叙事生成前后比较经济指纹（提案 id→状态快照），决定期间的合法结算不判过期，而新增提案或回滚会使仍在飞行的旧 AI 回复失效。重开与重置均清空提案、流水、outcome、挂起效果、outbox 和修订号；重开只保留已经结算进角色卡的余额，重置同时清除角色。

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

## 通用战斗扩展

通用战斗扩展（Issue 212 / ADR 0004）提供规则无关的公式 DSL、资源池、效果引擎与调度器原语。依赖方向固定为 contracts → primitives → ruleset adapter → ruleset catalog → transport，generic engine 不含任何 per-ruleset 分支。动作、效果与消耗全部是数据（通用 kind 词表），法术/遁术/丹药等身份由规则动作目录的 canonical `action_id` 表达；伤害与消耗金额经受限 JSON-AST 公式求值——白名单节点、深度/节点/骰子/结果上限、未知引用 fail closed、可注入确定性骰源，绝不 eval。资源池与调度器由规则 runtime 显式声明 capability（`combat_action_effects` / `combat_resource_pools` / `combat_scheduler`）后启用，客户端只提交 intent 并渲染服务端投影，伤害、速度与资源结算值不可信。D&D 2024 的伤害/治疗骰式已经由 D&D 侧适配器改经通用公式 AST 求值，法术位、专注、豁免与胜利判定仍归 D&D reducer；调度器与资源池的持久化随首个消费规则集落地。

## 世界状态（World State）

`GameInstance.world_state` 是“当前世界真相”的唯一权威容器，仍属于单局聚合根，不引入第二个 aggregate、独立数据库或后台运行器。第一版结构固定为 `schema_version / revision / clock / facts / scheduled_events`：fact 是 canonical key（`actor:<uid>.location`、`bridge:old.passable` 这类坐标，不接受翻译后的 display name）加标量值与 `public | gm` 可见性，并记录 `source_round` 与 `updated_revision`；`clock` 是逻辑世界时间（day + minute）；`scheduled_events` 是待结算事件的持久化数据，按稳定 `event_id` 索引。

唯一写入口是 `src/engine/world_state.py` 的 `apply_world_ops(instance, ops)`：整批 op 先校验再原子提交，越界、未知 op/字段、非法 key/value、损坏或未来 schema 都 fail closed 且不写入；事实可见性只由 world ops 决定，缺省更新不会把 GM 私有事实降级为公开。世界真相不使用 `ruleset_state`、`key_facts`、`lorebook_timed_state` 或 memory 作为容器，也不允许 LLM 直接写入。

持久化与生命周期遵循既有 `GameInstance` / codec / migration 模式：schema 12 → 13 为旧存档补一个空世界容器（不猜测任何事实，且可重复执行）；save/load、import/rebind 保留世界真相并隔离 run 身份；重置与重开从空世界重新开始；世界 ops 属于写入它的那一轮，整轮回滚、判定中止与 swipe 分支切换都按 ADR 0003 的整轮语义把它一起撤销。

世界真相不等于玩家可见真相：`project_visible_state(instance, viewer_is_gm=...)` 是唯一的读取入口，`gm` 私有事实只进入 GM 上下文块（并明确标注玩家不可见），玩家视角只拿 `public` 投影。行动合法性由 server 侧 `world_legality` 判定，模型只能通过结构化 `world_requirements`（`act` / `move` + canonical 地点 id）提议；判定只使用已登记地点与明确 `passable=false` 这类可证明证据——`passable=false` 阻止的是进入 / 经过 / 抵达，因此 `move` 只检查声明的 `via` 与目的地，行动者当前所在地点不参与该检查，已经身处不可通行地点的角色仍然可以离开；空世界、未知地点、行动者位置未知一律不阻断，已证明矛盾则向 GM 注入「需要先移动 / 未能完成」的可信裁定块，合法移动由 server 写入世界真相。该通道与 overreach 相互独立：overreach 管玩家替世界或他人声明事实，合法性管玩家自己的动作与权威世界事实矛盾。逻辑世界时间只经 `world_events.advance_world_time(+N)` 推进：它把时钟推到新的时刻，按 `(day, minute, event_id)` 稳定顺序结算到期事件，并把每个事件持久化为 `applied` 或 `failed`（到期时 ops 已不可应用）——没有后台 tick、没有独立 scheduler；持久化事件的 `ops` 在读取时按与 `schedule_event` 写入路径同一套结构契约逐条校验，损坏数据 fail closed，不会被静默过滤成「没有执行任何 op 却标记 applied」的伪成功状态；同一事件不会因重试、重复保存或刷新页面执行两次，结算结果与时钟同属 `world_state`，因此完整继承整轮回滚 / swipe / 重置 / 重开语义。

## 玩家控制（Player Control）

`players[uid].control` 是席位控制者的权威记录：`human`（真人负责）/ `ai`（服务器负责产生行动）/ `unclaimed`（席位已存在但暂无人玩），并带 `revision`、`temporary` 与 `resume_mode`。它回答的是“谁在玩这个角色”，不是“这个角色是什么”：角色本体、HP、装备、法术槽、状态、世界位置与战斗 actor（`player:<uid>`）在任何模式下都只有一份，控制器切换不复制、不搬运、不改键。第一版词汇表刻意封闭，不含 gm / remote_bot / script / hybrid 等模式。

唯一写入口是 `src/engine/player_control.py` 的 `set_control`；所有读取也经过同一模块——未知席位读出保守默认值，损坏记录降级为 `human`（即契约出现前的行为），而写入对未知席位、未知模式、缺少恢复目标的临时托管一律 fail closed。控制器属于桌面会话状态而非剧情世界结果：`revision` 只在记录真正变化时递增，整轮回滚、判定中止与 swipe 只回滚角色卡与世界事实，不重新指派席位；`temporary=true` 的暂离托管必须能回到 `resume_mode`，不得在重启后变成永久 AI。

持久化使用 schema **13 → 14**：旧存档的每个席位一律获得 `human`，迁移不按在线状态、角色名或历史行为猜测谁是 AI，且可重复执行。`control` 与 `character_sheet` 同级，属于玩家记录本身，因此随 save/load 往返，并在席位被清理（例如加载时的幽灵玩家清理）时一并消失，不会留下 orphan control。

控制模式现在是权威的准入判定：`submission_block(instance, uid)` 决定真人能否提交普通行动——`ai` 席位返回 `PLAYER_AI_CONTROLLED`、`unclaimed` 返回 `PLAYER_UNCLAIMED`，Web 与 SSE 共用的 `turns.submit_action` 对两者返回 409；它只决定"真人不得代打"，不改变服务器 AI 自身何时出手（见下文的补行动与即时接管）。

多人 ready barrier 只看真人：`GameInstance.active_human_players` = 存活、未暂离且 `control.mode == human`，`all_alive_ready()` 与 `multiplayer_status()` 的 ready / waiting 集合都由它计算，因此 AI 托管与未认领的席位不会阻塞推进；它们分别在 `ai_players` / `unclaimed_players` 及其计数中列出，说明"还差谁"以外那部分席位由谁负责。暂离真人依旧不阻塞（`active_alive_players` 语义未变，仍供幸运超时等只看人数的调用点使用）。

认领转换统一走同一权威：`claim_seat` 是 Web 加入已有席位的规范入口，把 `ai` / `unclaimed` 无损转为 `human`（角色本体、HP、装备、法术、世界位置与战斗 actor 都不搬运），并在 `expected_revision` 过期时以 `CONTROL_STALE`、对已是真人的席位以 `CONTROL_NOT_CLAIMABLE` fail closed；新建席位仍由 `put_player` 直接生成为 `human`。控制权变更的安全边界由 `control_change_block` 判定：只有处于 `ACTIVE_ACTION` 且没有在飞处理锁时为 `""`，否则 `CONTROL_CHANGE_BUSY`。

`ai` 席位在普通探索轮由 `src/commands/ai_player.py` 补行动：闸门是 `GameInstance.human_actions_ready()`（真人一侧交齐，且与 `should_advance()` 是两个不同问题），在唯一的推进入口——`turns.submit_action` 里真人闸门满足之后、`try_advance()` 之前——调用一次。每个席位一次 plain-text 调用、按 uid 串行，因此它只知道自己的角色卡、player-safe 公开上下文与本轮已宣告的行动，读不到 GM 私有世界事实、`gm_directives`、他人 `private_log` 或未来剧情，也不额外传 lorebook。产出只是一段普通行动文本（不含 DC / 加值 / 成败），经 `add_action` 走真人同一条 canonical 入口，由既有 Check Planner 与 WorldState 合法性裁定；行动带 `source` / `control_revision` / `generated_for_round` 元数据，仅用于去重与调试。调用前捕获 run / round / 席位 / `control.revision`，返回后四者与阶段全部复核，任一变化即丢弃；供应商错误或不可用输出记录 `AI_ACTION_SKIPPED` 后继续，不阻塞本轮。席位在本轮已经声明过行动时（例如真人先出手、GM 之后才把它交给 AI）不会被补行动覆盖——补行动只补还没行动的席位。

System prompt 明确要求**按角色卡扮演**而不是替玩家做战术最优决策：身份与背景、性格、价值观、目标 / 动机、已建立的人际关系、个人经历、自己已经知道的线索、私密感知、当前身体与资源状态优先，冲突时只要行为仍合理合法就保持角色一致性，且不得编造角色卡里不存在的人设、经历或关系。角色卡仍是唯一的人物设定来源：不新增 `ai_persona` 之类的第二份存储，可见性通道也不变（自己的角色卡 / 公开剧情 / 本人私密感知 / 明确对该角色可见的知识）。

控制权变更本身就是一次唤醒：`human → ai` 写入成功后，`GameControlService.set_player_control`（以及 `ai_takeover` 的暂离托管）调用 `turns.resume_after_control_change`——它只判断阶段是否可推进、真人闸门是否满足，然后调用上面**同一个**推进入口，因此不需要真人再发一句话，也绝不伪造空行动；仍有真人未行动时照样不抢跑，同一 round 重复 `set ai` 仍幂等，AI 席位的补行动若已写入则一并落盘。`ai → human` 不受影响：飞行中的旧 AI 结果继续由 control revision 竞争守卫丢弃。

探索之外的权威战斗走另一条路：AI 托管 PC 的战斗回合由 `next_automatic_intent` 以 server/GM automation authority 提交**结构化意图**，而不是叙事行动，并且复用 companion 已有的同一条确定性阶梯（`_allied_automatic_intent`：治疗濒危 → 攻击最近敌对 → 移动 → Dodge → End Turn），不新增第二套战斗引擎，本阶段也不接 LLM。控制权变更时，如果当前 actor 正是刚交给 AI 的席位，唯一入口是 `ruleset_gameplay.resume_authoritative_combat`：它复用同一个自动阶梯循环（`src/rulesets/automation.py` 的 `advance_automatic_intents`，`submit_intent` 也走它），一直推进到轮到真人、战斗结束或没有自动意图，并把推进与控制权一起落盘；当前 actor 不是该席位时一个状态字节都不改，绝不顺手替别的 actor 出手。失败时整段事务回滚并返回结构化错误（`AUTOMATIC_TURN_FAILED`），不半提交。它与 companion 的唯一真实差异是 0 HP：companion 不做死亡豁免，玩家角色必须做，否则战斗会卡在该席位。意图仍走 validate / resolve / apply 同一权威链，受同一行动经济约束（action / attacks_remaining / movement），只看该席位自己的角色卡；候选意图在当前状态下不合法时退回合法 `end_turn`，保证托管席位的回合一定结束。校验侧同步收紧：`player:` actor 只有在席位确实处于 `ai` 托管时才允许 `submitted_by == gm_uid` 代提交，否则维持「玩家只能提交自己角色」；真人既不能代打 AI 席位，也不能手动操控它。`human` 与 `unclaimed` 席位永远不产生自动意图。

房间与桌面可以**表达**谁来玩：开房时逐张角色卡可选「我来控制 / 等待玩家认领 / AI 托管」，另有「未认领角色默认」的全局快捷项，逐卡选择优先于全局默认；两者都缺省时保持旧行为（每个席位 `human`），未知模式在开房阶段直接 fail closed（`INVALID_PLAYER_CONTROL`），不会悄悄建成默认席位。席位列表按控制记录显示四种徽章：真人 / AI 托管 / 等待认领 / AI 临时托管。

「暂离」的含义由房间设置 `away_control_policy` 决定，默认 `pause`：暂离只改在场状态，**绝不**把角色交给 AI。设为 `ai_takeover` 时，玩家暂离会把席位交给服务器 AI 的**临时**形态（`{mode: ai, temporary: true, resume_mode: human}`），点「回来」即归还并清空 `temporary` / `resume_mode`；临时托管在重启后仍可归还，不会变成永久 AI。GM 另有托管控件「设为 AI / 停止 AI 托管」，只改控制记录——不复制角色、不动 Web 身份与 Bot 绑定、不重置 ready、不重置 HP、不重建战斗 actor。所有控制权变更只发生在安全边界（`ACTIVE_ACTION` 且没有在飞处理锁），否则返回可重试的 `CONTROL_CHANGE_BUSY`。断线**不会**触发 AI 接管：接管只能来自 GM 的明确操作、玩家的明确暂离，或房间的明确配置。该房间设置随存档持久化，schema 为 **14 → 15**，旧存档一律补 `pause`（即旧版本的真实行为），损坏值同样降级为 `pause`。

群聊（Bot）入口与 Web 等价：`托管 角色名` / `取消托管 角色名` 让 GM 在群里直接改变席位归属，走的是同一个服务端控制权 API（桥接层不保存任何控制状态，也没有第二份权威）。这两条与 `暂离` / `回来` 是不同契约——后者改在场状态、前者改控制者——且都需要 GM 或授权账号；目标角色必须唯一匹配 roster，否则回复可用角色而不是猜测。服务端返回 `CONTROL_CHANGE_BUSY` 时，群里得到的是「本轮结束后再试」的友好提示，而不是原始失败文案。

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

职业特性边界是 `src/rulesets/dnd2024/features/`：它只回答“这个角色拥有什么”——职业与职业等级、已获得的 feature、feature 的标量参数、职业资源当前值/上限，以及当前可用的 combat capability。职业表（`progression_catalog`）仍是获得等级的权威，`class_feature_catalog` 只做 parameterize 与展示标签（含 locale overlay）。Combat 只消费 capability id、动作/资源成本、目标要求与底层 canonical action，不在 generic engine 或前端判断职业；职业资源仍写在既有 `resources.class` 结构里，创建、升级与休息恢复继续由现有 rest/advancement 路径负责，没有第二套资源表、第二套 action economy 或第二套攻击结算。feature 的**装备前提**也由这个边界回答（`features/equipment.py`）：它只读 canonical `equipment.item_refs` 的 item type 与 combat catalog 的武器档案（category / ranged / 是否 Light），因此武艺不再只看职业等级，装备变化经既有 reconciliation 重新投影，前端与战斗结算只消费投影结果。职业资源如何随升级后的新上限调整是 rest catalog 上的显式 `resize_policy`（默认 `preserve_spent` 保持既有语义，`preserve_current` 保留合法 current 并夹取溢出），没有任何按 class / resource id 的分支。

Session 0 的每次修订都会清空旧成员确认，只有全部当前玩家接受后 GM 才能锁定。任务、线索、事实、重要物品和关系先保存为 pending proposal，再由 GM 以独立 Intent 确认或拒绝。章节摘要是已确认事件的确定性投影，并在存档成功后写入长期记忆；记忆投影失败不得回滚或伪装已经持久化的权威状态。

自然语言行动继续使用 DiceFrame 唯一的 `/action` 回合流程：单人即时推进，多人先收齐当前存活且在场成员的行动，再统一进行检定与 GM 回复。D&D runtime 只向同一 LLM 上下文追加权威战斗、战役和当前冒险节点的只读信息；所选 Worldbook 与匹配 lore 仍由通用叙事管线提供。LLM 不得直接创建战役事实、扣减资源或推进权威冒险步骤。

前端保留通用的单时间线、单行动输入框、角色卡、队伍状态、地图、场景图库、规则说明、世界书和 GM 控制台。左侧 `DND5E工具` 只包含冒险/战役与权威战斗两个 D&D 专属入口；工具以有界弹层覆盖主游玩区，不建立第二套消息流或第二套叙事提交接口。冒险节点进入遭遇门槛时会切换到战斗工具；自由剧情中 GM 明确裁定进入先攻，或玩家提交明确攻击并通过通用判定规划识别后，会产生只负责唤醒界面的 `encounter_request`，具体预设、先攻与所有机械结算仍须经过权威战斗 Intent。结束后回到同一条公共时间线继续游玩。

剧情遭遇访问权由 runtime composition root 根据 canonical adventure step 投影成 `EncounterAccess`；campaign 与 combat 引擎不得互相导入。战斗开始事件保存 canonical `encounter_instance_id`、preset 与来源 step，结束后写入 bounded history；剧情门槛只接受匹配的 encounter identity，因此已消费的冒险遭遇不能再次启动。敌方回合由服务端按同一验证、事件和 reducer 管线自动结算；每位玩家只能操作自己的角色，非当前玩家明确处于等待状态。场景、NPC 与地图位置使用 Adventure Bundle canonical refs，locale 只物化显示字段。直接联机桥接对玩家 Intent 使用字段白名单。
