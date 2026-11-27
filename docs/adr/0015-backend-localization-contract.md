# ADR-0015: 后端本地化契约：语言轴、身份与渲染边界

- Status: Accepted (local implementation; unreleased)
- Date: 2026-11-27

> 决策已接受，但本文描述的落地范围仍是本地实现，未随发布版推出，也不表示上游已采纳；`Accepted` 不等于已发布。核验基线：本地检出 `47fc66ef`（`src/version.py` = 3.3.3，writer instance schema 48）。
>
> 2026-11-27 修订：verdict 码化改为两阶段，阶段一只新增可选 `verdict_code`、不升 persisted schema；补偿 `dice_resolver` / `round_actions` 的中英混排约束问题；叙事一致性校验改为结构化输入。

## Context

前端已有多语言：集中目录 `frontend-v2/src/i18n/messages/`、社区译文来源指纹 `source-review.json`、`locales:check` / `locales:review` 门禁。后端并非完全没有本地化，而是**同时存在四套机制，且没有共同的边界纪律**：

| 机制 | 入口 | 语言来源 |
|---|---|---|
| `t("ns.key")` | `src/localization/messages.py` + 230 个 yml | `Accept-Language`（请求轴） |
| `localized_text(lang, {...})` | `src/engine/language.py`，280 处内联 dict | 显式传 `instance.language` |
| 内容 overlay 目录 | `templates/{worlds,rules,rulesets,adventures}/locales/<lang>/` | `content_locale_candidates()` |
| `intent/locales/<lang>/` | `src/engine/intent/lexicon.py` | 局内语言 + en + zh-CN 的 union |

`src/` 共 394 个 Python 文件含 5675 条中日文（CJK）字符串字面量，其中 2866 条（50%）所在文件**没有接入任何本地化 helper**。存量中相当一部分是日志与注释，不要求翻译；问题在于当前没有可执行的标准区分“该翻”与“不该翻”。

以下是已经发生或必然发生的失败类别，不是假设。

### 1. 译文被当作机械身份

- `src/engine/dice_rules_coc.py:182` `_SUCCESS_RANK = {"大失败": -1, "失败": 0, "普通成功": 1, ...}`；`apply_required_level`、`opposed_outcome`、`SANITY_SUCCESS_VERDICTS`、`REQUIRED_LEVEL_VERDICT` 都以这些中文串为键。
- `src/engine/checks.py:835` `if verdict == "失败"` 决定幸运抵扣；`:855` `is_critical = verdict == "大成功"`。
- `src/engine/check_decisions.py:62` `pushable_verdicts = {"失败", "大失败"}`；`:204` `failed=verdict in {"失败", "大失败"}`。
- `src/engine/combat.py:23`、`src/commands/state_update_applier.py:59` 同样以中文串或中英混写集合判断。
- 前端被迫二次嗅探：`frontend-v2/src/components/play/CheckRevealCard.vue:84` `verdict.includes('成功') || verdict.includes('success')`。

`verdict` 还会写进 `modules.checks.last_check` / `last_checks` 存档并跨存档边界传播。任何给 `coc_success_level` 增加语言的改动都会**直接改变**推骰资格、幸运消耗、SAN 成功集合与对抗判定。

### 2. locale 会改变行为（不只是显示）

- `src/commands/round_helpers.py:85` `is_critical = "大成功" in dice_block`。`dice_block` 由 `dice_resolver._verdict_text` 渲染；英文局输出 `Critical Success`，因此大成功/大失败的叙事一致性校验**对非中文局完全失效**，且不报错。
- 同一函数还用 `("你", "你们")` 与中文成功/失败词表切分叙事，英文叙事必然不匹配。
- `src/commands/dice_resolver.py:219`：`subject` 在所有语言分支里都渲染成中文（`技能「侦查」45%`），`ja` 分支的 `結果: {verdict}` 直接输出中文 verdict。英文、德语、日语局每一轮都在向 GM 送中英混排的硬约束。
- `src/commands/round_actions.py:141` 的 swipe 重生成路径把 `check['verdict']` 原样拼进 en/de/ja 句式，德语局出现 `Ergebnis: 困难成功`。
- `src/engine/constants.py` `COMBAT_ATTACK_KEYWORDS` / `PUZZLE_KEYWORDS` 只有中文；隔壁 `src/engine/intent/locales/` 已是 5 语言资源驱动（正确范式）。

### 3. 显示名被当作查表 key

`src/engine/constants.py` 的 `WEAPON_DAMAGE` / `WEAPON_DAMAGE_DICE` 以“中文 + 英文 + 日文”三份显示名做键（源码注释自己说明这是手工补充）。德语、俄语或任意新语言的武器查表必然 miss，伤害回退默认值。这是机制缺口，不是文案缺口。

### 4. 同一组文案复制在多处，覆盖各不相同

七个成功等级的映射表至少存在四份：`commands/dice_resolver.py`（en+de）、`bots/bridge_core/presenters.py`（en+de）、`commands/state_recap.py`（en+ja）、`commands/combat_resolver.py`（分散）。增加一条文案要改四处；漏改静默回退中文。

### 5. 错误文案存在三条 authority，且互相破坏

- `src/webui/errors.py:27` `ERROR_CODE_MAP` 是**中文文案 → error_code** 的 65 条映射，出口靠它补码。
- 前端社区语言（ja/de/ru）的错误译文**唯一入口**是 `apiErrors.<code>`（`frontend-v2/src/i18n/policy.ts:318`），必须有 code 才能命中。
- 已迁移到 `t()` 的 service 在**抛出时**就按请求语言渲染（如 `src/webui/services/characters.py:271`）。日语请求拿到英文文案后，中文键查表失败 → `error_code` 丢失 → 前端 ja 译文无法覆盖。

结论：后端 `t()` 迁移每推进一个模块，就在静默削弱该模块的社区语言错误显示。这张表本身也是“文案即身份”。

### 6. 语言轴边界不清

`t()` 不传语言时取请求轴，`localized_text()` 必须显式传局内轴，两者在同一个函数里混用没有门禁。请求之外的路径没有语言概念：P2P 客人操作由房主浏览器代为执行（`frontend-v2/src/peer/store/peerSession.ts:151`），语言取的是**房主的** `Accept-Language`；SSE、推送、后台任务同理。

### 7. 同一个概念已经有两套词表

`modules.checks` 的 `verdict` 是中文文本；`src/webui/services/manual_rolls.py:142` 的 `verdict` 是 `success` / `failure` / `winner` / `loss`。前端 `GameTimeline.vue:118` 同时处理两种。

### 必须保留的既定契约

- `docs/ENGINEERING_RULES.md` §10：canonical identity 与 display text 分离，locale 不得改变 mechanics。
- §15.2：语言触发模式必须放 locale 资源，业务代码不得复制每种语言的 map。
- `docs/zh/translations.md`：社区语言可暂停；暂停只影响显示，不重命名内容、不改机制、不修改用户选择的叙事语言。
- `docs/ARCHITECTURE_CN.md` §35：rule/world locale 只能改展示。

## Decision

### 1. 四条语言轴，每个文本只属于一条

| 轴 | 来源 | 覆盖的文本 |
|---|---|---|
| `ui_locale` | `Accept-Language`（请求轴） | 接口错误/校验提示 |
| `game_locale` | `instance.language` | 叙事、发给模型的文本、面向玩家的服务端投影（检定标签、战斗日志、结算说明） |
| `content_locale` | `content_locale_candidates()` overlay 链 | 世界/规则/资料册/冒险的授权内容 |
| identity | 无 | canonical ID、机械枚举、查表 key |

约束：

- 渲染函数必须显式声明所属轴。`localized_text()` 的语言参数保持必填；新增同类 helper 不得提供默认语言。
- `t(key)` 不传语言表示请求轴，仅允许用于接口提示；写入存档、时间线或模型输入的文本必须显式传 `game_locale`。
- 禁止用请求轴决定对局内容语言，也禁止用 `instance.language` 渲染接口错误。

### 2. 身份层不承载语言

- 机械枚举与查表 key 必须是 ASCII stable code，登记在单一模块，不在调用点重复字面量。
- 显示名 → code 的映射只允许存在于 `src/compat/` 或迁移代码；业务逻辑与规则运行时不得以显示文本做分支或集合成员判断。
- 持久化只存 code。已渲染文本可以作为展示缓存随存档冻结，但不得参与判断。

### 3. 渲染边界

用户可见文本只在三个出口渲染：HTTP 响应出口（`src/webui/errors.py` 一类中间件）、bot presenter（`src/bots/bridge_core/`）、prompt composer（`src/commands/prompt_composer.py` / `src/llm/`）。其余模块产出 code + 参数。

显式例外（内容而非界面）：叙事、检定标签、战斗日志在**生成时**按 `game_locale` 渲染并随存档冻结。切换语言不重译历史；需要重译时必须是一次显式的重放或重新生成动作，不能是渲染层的隐式行为。

### 4. 错误文案单一 authority

- service 抛错时用 `src/webui/errors.py` 的 `error_payload("webui.services.<module>.<key>", **params)` 返回；code 由目录键派生（`worlds_name_empty`），也可用 `code=` 指定共享语义 code。**不再从文案反推 code。**
- 出口 `error_code_middleware` 的职责收窄：已声明的 code 原样保留；能在 `webui/errors.<lang>.yml` 找到同码译文时用译文替换 `error`，否则保留抛出点渲染的文案。
- `ERROR_CODE_MAP`（中文文案 → code）冻结：只允许删除，不允许新增。它是迁移遗留，不是新路径。
- 新 code 必须 snake_case（`^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$`）；现存的 80 个 UPPER 写法登记为 `LEGACY_DECLARED_CODES` 并冻结。**不批量改写旧 code**：`apiErrors` 的社区译文按 key 名登记来源指纹，改名会让已审核译文失效，收益不匹配代价。
- 同一条错误只在一处登记译文：`webui/errors.<lang>.yml` 与 service 级 yml 不得重复覆盖同一 code。
- 前端 `apiErrors.<code>` 保持社区语言的唯一出口；未登记的 code 回退后端原文。
- 迁移用 ratchet：`scripts/audit_error_codes.py` 按文件记录未声明 code 的 `"error": t(...)` 数量（`scripts/error_code_baseline.json`），只允许减少。首个模块 `webui/services/worlds.py` 已完成（52 处）。

### 5. verdict 词表统一（第一项落地，取代 display-as-identity）

分两阶段执行：先解除语言对行为的控制，再按需决定是否持久化 code。

**共同部分（阶段一即完成）**

- 新增 `src/engine/check_verdict.py`，作为成功等级的唯一定义：`critical_failure(-1)` / `failure(0)` / `regular_success(1)` / `hard_success(2)` / `extreme_success(3)` / `critical_success(4)`，另有 d20 的 `success`。rank、成功集合、所需档位映射、孤注一掷资格都由该模块派生；调用点不得再写显示文本字面量集合。
- `verdict` 字段的语义明确为“渲染文本”，不参与判断；所有比较改走 code。`is_critical` / `is_fumble` 保留为派生兼容字段。
- 统一入口 `check_verdict.code_of(check)`，优先级固定：已声明的 `verdict_code` → runtime 显式 `success` 布尔 → 核心自己已落盘的骰值事实 → `src/compat/verdict_labels.py` 的**精确**旧标签桥（仅限历史七档的确切写法）。任意散文、带修饰的句子一律不解析，返回空。
- 渲染统一在边界完成（`engine.check_verdict.*` 资源），删除 `dice_resolver` / `presenters` / `state_recap` / `combat_resolver` 中的私有映射表。同类字段 `achieved_verdict`、`opposed.opponent_verdict` 也存 code；只有 `verdict` 存渲染文本。
- 修复混排：`dice_resolver._format_constraint` 的 `subject` / `roll_label` 与 `round_actions.format_check_results_constraint` 的 `Result` 一律按 `game_locale` 渲染，不得把存盘文本直接拼进另一种语言的句式。

**阶段一：写 code + 读档重算，不升 schema**

- 内置 d100 / d20 检定的 `roll`、`rolls`、`threshold`、`dc`、`difficulty`、`natural`、`is_critical`、`is_fumble` 已经落盘，旧存档的 code 由这些字段确定性重算；`coc_success_level` / `d20_verdict` 返回 code，文本在边界渲染。
- 新写入的 check 增加**可选**字段 `verdict_code`（additive：旧 reader 忽略未知键，不读也不报错）。不新增必需字段，`instance_schema_version` 保持 48，`modules.checks` 保持 v1。
- 自定义 ruleset runtime 走 `src/rulesets/narrative_checks.py` 的 `verdict`（任意字符串），无法重算。契约新增可选 `verdict_code` 与可选 `success` 布尔；两者缺失时该条检定只能靠精确旧标签识别，仍无法识别时依赖 rank 的**可选**分支（孤注一掷资格、幸运抵扣、SAN 成功集合、所需档位升级）不启用。核心结算结果不变。
- 基于叙事文本的校验改为结构化输入：`validate_dice_constraint(check_results, narration, language=...)` 只读 code，句子归属用角色名（语言无关），仅在单人回合才用代词；代词与成败词表放 `src/engine/intent/locales/<lang>/narration.json`，缺资源则跳过并记录。

**阶段二（触发条件满足后）：持久化 code**

触发条件：至少一个非内置 runtime 声明并通过 `verdict_code` 测试，或出现重算无法覆盖的内置骰制。届时 `modules.checks` v1 → v2 增加 `verdict_code`，`instance_schema_version` 48 → 49，迁移把已知标签映射成 code、未知保留原文。

### 6. 语言触发模式与基于文本的校验

- 语言触发模式一律进 `src/engine/intent/locales/<lang>/`（沿用 §15.2 与 `lexicon.py` 的 union 语义）。`COMBAT_ATTACK_KEYWORDS` / `PUZZLE_KEYWORDS` 迁入资源，业务代码只消费稳定 intent ID。
- 依赖叙事文本内容的校验（如 `validate_dice_constraint`）不得以单一语言词表实现。优先级：消费结构化输入（`is_critical` / `is_fumble` / 主语）→ 若必须保留启发式，则模式进 locale 资源。语言无资源时**跳过**该校验（它本来就是软性质量检查，fail open 不得阻断回合），并记录一次可观测警告。

### 7. 新增语言的验收标准

新增一门语言只允许改资源文件，不允许改业务代码。完成条件：

1. `src/localization/locales/` 对应文件的 key 与 en 齐平；
2. `src/engine/intent/locales/<lang>/{intents,messages}.json` 覆盖当前声明的稳定键；
3. 内容 overlay 目录按需提供，缺失时回退链可验证；
4. `src/localization/registry.json` 登记层级与状态；
5. 通过下面 Validation 的多语言行为等价测试。

### 8. 门禁（ratchet，不清算存量）

- `scripts/audit_error_codes.py`：未声明错误码的接口错误按文件计数，只允许减少（ADR §4）。
- 待建：`scripts/audit_localization.py`，用于机械目录禁止新增 CJK 字面量、全仓 CJK 计数 ratchet、`localization/locales/**/*.yml` 的 en / zh-CN key 齐平（后者目前由 `tests/test_localization_messages.py` 与前端 `locales:check` 分别覆盖）。

## Alternatives Considered

**一次性把所有后端文本改成 code + 参数（通用 message descriptor）。** 上下文爆炸，LLM prompt 组装与冻结的对局文本无法用 descriptor 表达，且迁移期间两套并存更难验证。否决，改为“按轴分类 + 只在机械/分支处强制 code”。

**后端只保留一套 `t()` 服务端渲染。** 无法解决身份污染；`t()` 不知道 `instance.language`；社区语言缺 code 的问题反而加剧。

**后端只回中文，翻译全部交给前端。** 把同一个决定复制到第二个 authority；CLI、机器人、第三方客户端没有前端目录；且现有 `Accept-Language` 契约已经存在。

**替换四套机制中的库（如统一换成 Babel / Fluent / python-i18n 单一入口）。** 换渲染库不改变数据流；问题在于身份与边界，不在库。

**对 LLM 输出做后置机器翻译。** 破坏叙事一致性，无法验证，且与“不得用 locale 改变对局语言”冲突。

## Consequences

### Benefits

- 加语言不再有改动机制的路径；`dice_rules_coc` / `checks` / `check_decisions` 的分支只依赖 code。
- 后端 `t()` 迁移不再破坏社区语言错误显示。
- 新增语言成为纯资源工作，可外包给社区维护者，与 `docs/zh/translations.md` 的暂停模型一致。
- 消灭四份 verdict 映射表与三份错误译文权威。
- “locale 不改变行为”变成可执行测试，而不是文档承诺。

### Costs / Risks

- 存量 5675 条 CJK 字面量中相当一部分不会迁移（日志、诊断）。ratchet 只能防新增，最终一致性依赖逐模块 owner 推进。
- 阶段一的自定义 runtime 旧存档维持现状：可选分支按 legacy 规则不启用，与当前文本匹配结果一致，因此不构成回归，但也没有修好。
- `validate_dice_constraint` 在缺资源语言下跳过，等于对部分语言维持现状；这是显式降级，不是修复。
- 前端需要同时接受 code 与既有文本一段时间。
- 分两阶段意味着 `check_verdict.code_of()` 会长期承担兼容职责；阶段二落地前不得在其它模块直接读 `verdict` 文本。

### Migration / Compatibility

- **Persisted data（阶段一）**：不改任何 schema。新写入的 check 多一个可选 `verdict_code`（additive、可忽略），`verdict` 文本继续落盘作为展示缓存；无 code 的旧数据由 `code_of()` 读时重算或查精确旧标签桥。
- **Persisted data（阶段二）**：`instance_schema_version` 48 → 49；`modules.checks` v1 → v2 把 `verdict_code` 变为必需字段。迁移只读改写展示缓存与已知标签，不重算骰值、不改已结算结果。未知值保持原文、code 置空。
- **降级读取（仅阶段二）**：旧 reader 遇到新 schema 按既有 fail-closed 规则拒绝，不做猜测性降级。
- **Public / extension API**：`CheckResult` 增加可选 `verdict_code`（阶段一为响应字段，不落盘）；`narrative_checks` 契约增加可选 `verdict_code`；HTTP 响应同时返回 `verdict` 与 `verdict_code`，旧客户端不受影响。
- **Deprecation**：`ERROR_CODE_MAP`、`WEAPON_DAMAGE*` 显示名键、四份 verdict 表标记为 legacy，只减不增；删除前必须核实无生产调用路径。
- **Rollback**：阶段一只替换调用点与新增模块，不存在数据形态变化，回退即撤回代码；渲染文本始终保留。阶段二回退到旧 writer 只丢失 code（展示不变），schema 版本不回退。

## Validation

1. **行为等价**：固定 RNG 序列 + 固定剧本回合，在 zh-CN / en / ja / de / ru 五种 `game_locale` 下运行，比对归一化后的存档（资源、经济、战斗状态、`code_of` 结果）必须逐字段相同；只有 display 文本允许不同。
2. **重算等价**：读档后 `check_verdict.code_of()` 对内置 d100 / d20 检定返回与原始结算相同的 code；含未知 verdict 的自定义 runtime 检定返回空、可选分支不启用、读档不失败。
3. **混排**：同一 `check` 在五种 `game_locale` 下生成的 GM 约束文本，其 `Result` / `检定` / `判定` / `Ergebnis` 句式与 verdict 文本语言一致；断言模板不再把存盘 `verdict` 原样嵌入其它语言的句子。
4. **错误契约**：同一错误在五种 `Accept-Language` 下返回同一 `error_code`，`error` 文本按语言变化；已迁移 service 不得丢失 code。
5. **intent 资源**：新增语言的 intent/message 资源通过结构校验与经济 fail-closed 回归（§15.2 既有范围）。
6. **audit 门禁**：`scripts/audit_localization.py` 在基线上通过；故意新增一条机械目录 CJK 字面量必须失败。
7. **前端**：删除 `CheckRevealCard.vue` 的字符串嗅探后，未知 code 仍能回退显示后端文本。

## Scope

本文覆盖后端用户可见文本的语言轴、身份分离、渲染边界、错误契约与门禁。不覆盖：前端目录结构与审核流程（沿用现有 `i18n/policy.ts`）、用户创作内容的翻译、LLM 叙事的后置翻译、文档翻译、内容作者工作流。P2P 协议携带调用方语言是独立契约变更，另开记录。

## Supersedes / Superseded By

None