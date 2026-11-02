# 1C Review 收尾修复任务书

| 字段 | 值 |
|---|---|
| 任务编号 | 1C-R（review remediation；不是新的 S-P13 特性切片） |
| 主计划 | `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1C，S-P1–S-P12 收尾 |
| 编写日期 | 2026-11-02 |
| 状态 | 待执行 |
| Review 候选 | `ba3f6f5` |
| 共同基线 | `0ccaee0` |
| Review 时上游 | `a2e7e2f`（#639） |
| Review 结论 | Request changes；八项阻塞问题及持久化生命周期、locale、对外说明和 CI 证据须全部闭环 |
| 执行方式 | 后续执行模型实施；本任务书本身不代表任何修复已经完成 |

---

## §0 目标、完成定义与开工纪律

### 0.1 目标

在不扩大 1C 规则范围、不破坏既有存档/事件/多人权限/RNG 契约的前提下，修复综合 review 发现的问题，使 S-P1–S-P12 从“单切片测试通过”达到“跨切片规则正确、旧数据有明确兼容策略、人类 UI 可达、完整上游 CI 可验收”。

### 0.2 完成定义

以下全部为必要条件，不得用“规则集 976 测试已通过”替代：

1. §2 的 R1–R8 全部修复；任何一项仍保留原错误行为，任务均不得标记完成。
2. §2.9 的 pending decision 持久化生命周期有真实覆盖；未证明存在损坏时可以不做数据迁移，但不能没有行为测试。
3. 中英文核心内容/UI 文案、对外迁移说明和必要的公开契约文档同步完成；公开资料不得链接本文件或其他 `docs/internal/` 文件。
4. §5 的后端、全仓、前端和真实 UI 验证全部执行并记录。失败必须分类和解释，不能只汇报成功命令。
5. 最终 changed-file list 只含本任务及明确的契约配套，不串入 1D、其他切片、运行时日志、生成缓存或无关格式化。
6. 完成后仍需一次独立 review；执行模型不得自行把“实现完成”等同于“上游 approve”。

### 0.3 开工纪律

1. 开工前记录实际 HEAD、上游 `main`、merge-base 和工作区状态。上述 commit 是 review 锚点，不要求为了匹配任务书而回退或重写历史。
2. 工作区必须干净，且没有其他会话的在途改动。若 HEAD、文件结构或上游 contract 已变化，以语义重新核对全部锚点，不得照抄行号。
3. 先为已确认错误补能够失败的回归测试，再修实现；保留修复前复现结果和修复后结果。不得通过删除断言、放宽既有契约或修改随机种子来“修复”测试。
4. 每个工作包保持独立可验证；建议提交边界见 §6，但不要求重写既有 1C 历史。

---

## §1 不可破坏的产品契约

1. **权威边界**：客户端只提交既有 intent / decision option；资源消耗、骰子、命中、伤害、专注、反应预算和胜负仍由服务端决定。不得为了 UI 可达而把判定移到浏览器。
2. **事件顺序**：同一决策的 `dnd2024.decision.pending` / `dnd2024.decision.cleared` 顺序及资源事件支付语义不变；窗口 dice 仍在 resolve 时产生，不得折回预滚序列。
3. **直通 RNG**：无 Lucky、Cutting Words、Dark One's Own Luck 等资格或不声明可选能力时，既有意图形状、事件序列和 RNG 消耗保持不变；修复不得顺手重排无关骰序。
4. **canonical identity**：`feat:lucky`、`retaliation`、`dark_ones_blessing`、condition id 等机械身份不得改为显示名或 locale 文案。
5. **旧数据**：不得在普通 actor view 中用 fail-open 默认值伪装迁移；不得猜测装备、重写历史 ledger 或让 locale 改变 mechanics。兼容逻辑留在明确的加载/投影边界。
6. **多人与信息可见性**：pending decision 仍按 `assigned_to` 校验 owner；UI 只能消费当前玩家可看的投影，不能为显示窗口而泄露隐藏角色资源或未公开状态。
7. **既有特化窗口**：Shield、Uncanny Dodge、Counterspell 等现有决策交互和 AI 自动路径不得被新的通用窗口 UI 回归。
8. **范围稳定**：本任务不修 1D，不补 Lucky 的战斗外属性检定面，不扩 Cutting Words 到法术伤害，不新增穿脱甲时长或其他未交付特性。

---

## §2 工作包与逐项修复规格

### 2.1 R1 — Lucky 优势抵消后仍取低（已复现，阻塞）

#### 证据

- `src/rulesets/dnd2024/combat/check_windows.py`：追加 Lucky disadvantage 后调用 `mode_from_sources`，但随后无条件 `natural = min(old_natural, lucky_die)`。
- Review 复现：原优势攻击骰 `[15, 3]`，Lucky 新骰 `18`，结果 `advantage_mode == ""`、`natural == 15`。
- `tests/rulesets/test_dnd2024_check_windows.py::test_lucky_cancels_advantage_on_an_enemy_attack` 使用新骰 10；它低于旧 natural 15，因此不能区分“抵消后单骰”和“继续取低”。
- `docs/internal/design/SP3_D20_CHECK_WINDOW_TASK_SPEC_2026-10-30.md` L3 已要求“优势攻击 + Lucky 劣势面 → 优劣势抵消 → 平攻重判；natural 按新模式取值”。

#### 必须实现的语义

| 原攻击模式 | 加入 Lucky 后 | natural / 骰序要求 |
|---|---|---|
| 中立 | disadvantage | 原 natural 与 Lucky 新骰取低；只多掷一枚 d20 |
| advantage | 中立 | 不再取低；按抵消后的单 d20 语义，以 Lucky 新骰作为本次重判 natural |
| disadvantage | 不开 Lucky disadvantage 窗口 | 保持现有资格门槛 |

- `rolls` 仍保留原始骰迹并追加 Lucky 新骰；不得改写原始骰值。
- `advantage_sources` 同时保留原优势来源与 Lucky 劣势来源。
- total、success、critical 必须由修正后的 natural / mode 重新计算；Lucky 点和 pending/cleared 仍各结算一次。
- 不改变中立 + Lucky、nat 1 不开窗、已有劣势不开窗和 AI 的既有行为，除非测试证明其依赖了本错误。

#### 最低测试

1. 优势原骰 `[15, 3]` + Lucky `18`：mode 中立、natural 18、total 按 18 重算。
2. 优势原骰 `[15, 3]` + Lucky `10`：natural 10；锁定现有用例的真实语义。
3. 中立攻击 + Lucky 较低骰：仍取低，证明没有破坏原路径。
4. 修正后的 natural 影响命中/重击时各一例；资源只消耗一次，事件顺序和额外 RNG 数量正确。

---

### 2.2 R2 — 同一多射线目标重复触发 Dark One's Blessing（已复现，阻塞）

#### 证据

- `src/rulesets/dnd2024/combat/resolution.py` 多射线循环对每个 ray 累加 `dealt[target_id]`，随后每次调用 `_enemy_defeated_feature_events`。
- 击败谓词只比较累计伤害与原始 HP/temp HP，没有判断本次 ray 是否首次跨过击败边界。
- Review 复现：两束 Scorching Ray 命中同一个 1 HP 目标，事件批产生两次 `dark_ones_blessing` 临时 HP 事件。
- 既有测试只覆盖两个不同敌人分别被击败，未覆盖同一目标重复命中。

#### 必须实现的语义

1. 一个 enemy 在一次事件批中只能从“尚未被累计伤害击败”到“首次被累计伤害击败”时触发一次击败特性；后续 ray 即使继续造成伤害也不能重复发放。
2. 两个不同敌人各自首次被击败时仍各触发一次；temp HP reducer 的 keep-max 语义不变。
3. 0 伤害、temp HP 吸收、overkill、胜利触发和事件顺序保持正确；不得通过吞掉后续伤害事件来规避重复触发。
4. 多个持有者及既有共享触发链不得由“全局只触发一次”的错误去重方式误伤；去重键至少包含被击败目标，语义是首次跨边界而非 feature id 全局去重。

#### 最低测试

- 同一 1 HP 目标两束及三束命中：伤害事件按规则保留，Blessing 仅一次。
- 两个不同敌人各被击败：仍各一次。
- temp HP 未被首轮完全消耗的边界，以及后续 ray 首次真正跨边界时才触发。
- 既有 Rage damage reduction、战斗胜利和 temp HP keep-max 回归。

---

### 2.3 R3 — Retaliation 读取伤害前专注状态（代码路径确认，阻塞）

#### 证据

- `src/rulesets/dnd2024/combat/reaction_spells.py::_counterattack_events` 先产生伤害和 `_concentration_events`，随后仍用原始 `instance/combat` 构造 actor view 并调用 `_attack_events`。
- `dnd2024.concentration.ended` 要到 reducer 阶段才清除专注与 Bless 条件；因此反击结算可能从伤害前条件读取 `blessed` 并额外掷 d4。

#### 必须实现的语义

1. 反击必须使用“本次伤害及专注结算之后”的有效机械状态。若伤害导致自身 Bless 专注结束，反击不再获得 Bless attack bonus，也不再消耗对应 d4 RNG。
2. 若专注豁免成功，反击仍按规则获得 Bless；不能一律剥离条件。
3. 不改变伤害事件、专注事件、反击 check 事件的因果顺序；不得重复提交伤害/专注事件，也不得为了修状态而提前部分 reduce 正式 snapshot。
4. 实现应使用明确的派生上下文或等价的权威状态视图；不得另建第二套条件真相，也不得在 generic engine 增加 Retaliation/Bless 特判。
5. Retaliation 的反应消耗、距离、HP>0 资格、Lucky/CW 等后续窗口链保持现有契约；若发现现有嵌套 pending 无法承载修复，触发 §7 停止条件而不是绕过窗口。

#### 最低测试

- 伤害 + 专注豁免失败 + 自身 Bless：无 `concentration`、无 Bless d4、无对应 RNG。
- 伤害 + 专注豁免成功 + 自身 Bless：仍有且只有一枚 Bless d4。
- 无 Bless 的既有 Retaliation 用例事件序与 RNG 不变。

---

### 2.4 R4 — Hide 的 combat-scoped Invisible 跨战斗残留（代码路径确认，阻塞）

#### 证据

- Hide 在 `src/rulesets/dnd2024/combat/resolution.py` 写入 `condition=invisible`、`duration=combat`。
- `src/rulesets/dnd2024/combat/reducer.py` 的 `combat.ended` 只清理 Rage 和 `_TURN_SCOPED_DURATIONS`，没有清理 `duration == "combat"`。

#### 必须实现的语义

1. 战斗结束时移除 player/companion 的 combat-scoped conditions，包括 Hide 施加的 Invisible；下一场战斗不得继承。
2. 优先按 `duration == "combat"` 的既有生命周期语义处理，不在 reducer 为 `invisible` 写特性特判。
3. 不清除应当跨战斗保留的条件；既有 Rage、turn-scoped duration 和 pending/combat 状态清理不回归。
4. 正常胜利结束与显式结束共用同一权威清理语义；不得只修 UI 显示。

#### 最低测试

- Hide 成功后结束战斗：canonical party/companion conditions 不含 combat-scoped Invisible。
- 紧接着开始下一场战斗：仍无残留，actor 投影也没有该条件。
- 一个非 combat 持续时间条件和一个既有 turn-scoped 条件作对照，确认清理范围既不遗漏也不扩大。

---

### 2.5 R5 — 旧角色绕过 Armor Training 并保留旧盾牌 AC（兼容阻塞）

#### 证据

- `src/rulesets/dnd2024/combat/view.py` 对缺失 `derived.armor_training` 的旧角色投影 `{}`，速度罚值也按 0；同时沿用旧 `derived.armor_class`。
- 新 AC 与 training 只在 `src/rulesets/dnd2024/character/reconciliation.py` 的显式 reconciliation 中重算。
- 普通 load 与 `combat.start` 没有对应重算；旧角色因此可能既没有未受训门控，又保留未受训盾牌 +2。
- `templates/rulesets/dnd2024_srd/manifest.json` 仍为 `srd-5.2.1+r5`；progression 对角色/bundle 内容版本有一致性要求，不能只机械 bump。

#### 执行前必须先查明并记录

1. 旧角色从持久化加载到 `combat.start` 的真实 owning path、现有 migration/version hook，以及 companion 的对应路径。
2. 旧 canonical 中 abilities、proficiencies、equipment item refs 是否足以确定性地重算 AC/training；哪些形状无法安全证明。
3. 保持、bump 或分阶段处理 `content_version` 对旧角色 progression/rest/equipment reconciliation 的影响。

#### 必须实现的语义

1. 旧角色在正常加载后第一次直接开战前，就要得到与新角色一致的 Armor Training、盾牌 AC、速度和施法门控；不能依赖玩家先休息、升级或换装。
2. 优先复用 `derive_armor_training_state` / `derive_armor_class` 和 reconciliation 的唯一公式，在明确的加载或迁移边界执行；不得把 actor view 的空对象默认值当作修复。
3. 对能够确定映射的数据做幂等转换：重复加载/开战不重复产生 revision、操作日志或资源变化，不覆盖无关用户数据。
4. 对无法证明安全的数据 fail closed 或给出明确的用户可诊断拒绝；不得猜装备、猜受训或静默赋予全受训。
5. 新角色、主动 reconciliation、rest、advancement 和装备变更保持现有行为；盾牌只在受训时提供 AC，未受训护甲本身的 AC 公式不回归。
6. `content_version` 必须与上述策略一致：若 bump，同步处理旧角色升级路径；若保持，记录为何无需版本迁移及如何保证旧派生状态更新。禁止只改 manifest 数字。

#### 最低测试

- 从真实旧形状构造“未受训护甲 + 未受训盾牌”的角色，不走显式 reconcile，加载后直接开战：AC、training、speed、attack/save/spell 门控全部与新派生一致。
- 受训角色旧存档：AC 和门控不被错误改写。
- 重复加载/开战幂等；companion 覆盖或按 §7 停止上报。
- 旧角色后续 progression/rest/equipment reconciliation 不冲突，内容版本策略有对应测试。

---

### 2.6 R6 — 历史 `bless_bonus` 战斗事件投影/显示不兼容（兼容阻塞）

#### 证据

- 共同基线的战斗事件使用 `bless_bonus`；1C 改为 `check_bonus` / `check_bonus_rolls`。
- `src/rulesets/dnd2024/runtime.py::_recent_combat_events` 的白名单不再保留旧字段。
- `frontend-v2/src/components/play/CombatLiveBar.vue` 只读取 `check_bonus`，历史事件的 total 虽不变，显示算式却丢失 Bless 加值。

#### 必须实现的语义

1. 先以共同基线代码/历史测试夹具确认旧事件的真实完整形状，再写兼容代码；不得凭字段名猜测旧骰迹结构。
2. 不重写持久化 ledger。优先在 runtime 的公开投影兼容边界把历史 Bless 证据归一化为当前消费契约，或在有明确 API 兼容要求时保留旧 alias。
3. 同一笔加值只能显示一次；新旧事件都保持 total、natural、modifier、bonus 和骰迹一致，不得前后端同时转换造成双重加值。
4. 新 `check_bonus` 契约不变；如果公开 API 必须继续向旧客户端提供 `bless_bonus`，需在执行报告中说明 alias 范围和弃用边界，而不是悄悄删除。

#### 最低测试

- 直接构造共同基线形状的历史事件，经过真实公开投影与 `combatRollEvidence`：算式含且仅含一次 Bless 加值。
- 新 `check_bonus` 事件保持现结果；无 bonus 的旧事件不新增字段或误差。
- 历史事件包含骰迹时，投影和 UI 不丢失证据；不包含时不得虚构骰迹。

---

### 2.7 R7 — check/damage window 没有人类 `spend` 路径（功能阻塞）

#### 证据

- 后端窗口选项为 `spend / decline`，pending 时只允许 `decision.resolve` / `combat.message`。
- `Dnd2024CombatPanel.vue` 只渲染 `counter`、`resolve`、`decline`，没有通用 `spend` 渲染或提交。
- 人类只能 decline，无法使用 Lucky disadvantage、Cutting Words check/damage、Dark One's Own Luck。

#### 必须实现的语义

1. 为 `check_window` / `damage_window` 增加类型完整、可访问的 pending UI：窗口种类、已掷结果/伤害、可用选项和投影中合法公开的资源/骰面信息应可理解。
2. 按服务端 `options` 渲染 `spend` 和 `decline`，统一通过 `resolveRulesetDecision(decision, option, intentId)` 提交；不得伪造其他 intent、在客户端掷骰或绕过 `assigned_to`。
3. 保留 `hit_reaction` / `hit_spell` 的特化 UI；可提取共享展示组件，但不得为了泛化删除 Shield、Uncanny Dodge、Counterspell 的既有语义。
4. UI 所需数据必须来自已有公开投影；若投影不足，在后端以明确、类型化、最小字段扩展，禁止读取隐藏 canonical sheet 或增加私有 `api._*` 穿透。
5. pending、spend、decline、owner 切换、pending 阻止其他动作、重新加载后恢复窗口均有真实交互覆盖；AI 自动 decision 路径不回归。

#### 最低测试

- 组件级：四种窗口至少覆盖 check_window 与 damage_window 的渲染、按钮和 intentId/option 提交；三种特性在服务端测试链中均有 spend + decline。
- 浏览器级：至少一条 Lucky spend 和一条 Cutting Words 或 Dark One's Own Luck spend/decline 的真实 UI 链；断言最终事件/资源/战斗状态，而非只断言按钮存在。
- 非 owner 看不到可操作窗口，pending 时普通动作仍不可提交。

---

### 2.8 R8 — Lucky 优势面、Overchannel、普通 Hide 缺少主动 UI（功能阻塞）

#### 必须实现的语义

1. **Lucky 优势面**：attack、spell attack、death save 三类既有 `spend_luck` intent 参数均有用户声明入口；只在服务端投影表明当前角色和当前动作可用时出现。缺省/false 继续发送旧意图形状。
2. **Overchannel**：cast_spell 的 `maximize_damage` 有用户声明入口，只在当前法术/角色/资源符合服务端资格时可用；不得在客户端硬编码 Sorcerer 职业名或自行判断伤害最大化是否合法。
3. **Hide**：普通 action 的 Hide 有按钮并提交既有 `stageSimple('hide', ...)` 路径；不得与 Cunning Action 的 bonus-action capability 混成同一意图。
4. 若 `available_intents` 当前缺少声明所需信息，在 ruleset owning projection 中扩展明确契约并更新 API 类型；前端不通过试提交、猜测 feature id 或读取私有接口来决定可用性。
5. 资源耗尽、非当前回合、pending decision、无资格和服务器拒绝均有可理解状态；禁用 UI 不是权限边界，服务端校验仍必须保留。

#### 最低测试

- Lucky 三类声明的参数投影/提交至少各有组件或服务端集成覆盖，并有一条真实 UI 成功消耗链；不声明时 RNG/事件与旧路径一致。
- Overchannel 勾选后只增加合法 `maximize_damage`，资源消耗和最大化伤害由服务端验证；不勾选路径不变。
- 普通 Hide 从按钮到 stealth check/condition 全链；Cunning Hide 仍走 bonus action。
- 无资格角色不显示可用声明，伪造 intent 仍被服务端拒绝。

---

### 2.9 R9 — 新 pending decision 的持久化生命周期证据（合并证据要求）

`check_window` / `damage_window` 已扩展持久化战斗决策形状。按 `docs/adr/0004-generic-combat-extension.md` 和 `docs/ENGINEERING_RULES.md` §15，至少覆盖：

1. **save/load**：窗口 pending 时保存并重新加载，owner、options、payload、next_decision 和资源状态保持；随后 spend/decline 能且只能结算一次。
2. **reset/restart**：旧窗口、旧骰迹和旧资源锁定不泄漏到新会话/新战斗。
3. **swipe / rollback / replay**：按现有 API 语义恢复或重放到窗口前后时，不重复扣资源、不重复掷窗口骰、不产生 orphan pending。
4. **投影/codec**：旧存档没有新 kind 时正常加载；新 kind 的序列化、公开投影不丢字段也不泄露隐藏信息。

若现有通用序列化天然满足，测试可以证明“零迁移”而不新增迁移代码；若需要修改通用 codec/storage/rollback，先触发 §7，不得把 D&D 特判塞进 generic engine。

---

### 2.10 R10 — Locale、对外说明与版本交付

1. 核对 1C 新增/实际点亮的全部 class feature、feat、UI 决策和声明文案，不只修 review 举例。至少补齐 `aura_of_devotion`、`retaliation`、`dark_ones_own_luck`、`cutting_words` 及 Lucky 的英文/中文所属 overlay。
2. canonical catalog 的英文 label 可以继续作为英文 fallback，但中文不得无意回退英文；locale key 只映射已有 canonical id，不改变 mechanics。
3. 为窗口 `spend/decline`、Lucky、Overchannel、Hide 的服务端错误和前端按钮补齐核心 locale；运行 `locales:check`。
4. 在现有 owning 对外文档中说明新战斗能力的基本使用方式，以及旧角色 Armor Training、历史事件投影和内容版本的兼容策略。若玩家手册属于 `diceframe-content`，当前仓库至少提交准确的 PR 迁移说明和待同步内容，不得从公开文档链接本任务书。
5. PR 描述必须按模板声明 Ruleset Mechanics、Persisted Data、Compatibility、Migration、Multiplayer/Turn Authority 和 Architecture 风险及验证，不再以“纯规则集新增”概括。

---

## §3 预期改动边界

以下是 review 已定位的预期 owning files，不是允许通过猜测扩张的空白支票；行号以执行时文件为准。

| 范围 | 预期文件/位置 |
|---|---|
| Lucky 窗口 | `src/rulesets/dnd2024/combat/check_windows.py`；`tests/rulesets/test_dnd2024_check_windows.py` |
| 多射线击败 | `src/rulesets/dnd2024/combat/resolution.py`；`tests/rulesets/test_dnd2024_dark_ones_blessing.py` 及必要的多射线既有测试 |
| Retaliation | `src/rulesets/dnd2024/combat/reaction_spells.py`；`tests/rulesets/test_dnd2024_retaliation.py` |
| Hide 生命周期 | `src/rulesets/dnd2024/combat/reducer.py`；`tests/rulesets/test_dnd2024_cunning_action.py` 或既有 combat lifecycle 测试 |
| Armor 旧角色 | `src/rulesets/dnd2024/character/reconciliation.py`、实际旧存档加载/migration owning module、必要的 combat 投影/启动消费；`test_dnd2024_armor_training.py` 与真实迁移/加载测试 |
| 历史事件 | `src/rulesets/dnd2024/runtime.py`；`frontend-v2/src/components/play/CombatLiveBar.vue`；`frontend-v2/tests/combatRollEvidence.test.ts` 与后端公开投影测试 |
| 决策及主动 UI | `Dnd2024CombatPanel.vue`、其 ruleset API/type/projection owning files、必要的 component test 与 `frontend-v2/e2e/dnd2024-workspace.spec.ts` |
| 声明可用性 | D&D combat `available_intents` / projection / validation 的 owning files；禁止为 UI 在 generic path 加具体特性分支 |
| 内容交付 | 所属 en / zh-CN locale overlay、`manifest.json`（仅当版本策略要求）、现有公开文档/PR 迁移说明 |

允许新增聚焦的测试文件，但优先使用现有测试 home。若实现必须改动 generic engine、通用 storage/codec、公共 API schema 或上述之外的模块，先按 §7 停止并说明 owning contract；不得先改后补理由。

---

## §4 汇总验收矩阵

执行报告必须逐项引用实际测试名，不允许只写“已覆盖”。

| ID | 必须证明的结果 |
|---|---|
| M1 | Lucky advantage + Lucky disadvantage 抵消后不再取低；中立路径、骰迹、total、critical、资源与 RNG 回归 |
| M2 | 同一目标多 ray 只首次击败触发一次；不同目标仍分别触发 |
| M3 | Retaliation 在专注成功/失败两种情况下读取正确的伤害后 Bless 状态 |
| M4 | combat-scoped Hide condition 不进入下一战，其他生命周期不扩大清理 |
| M5 | 旧玩家/companion 直接开战即获得正确 Armor Training 与 AC；迁移幂等且版本策略闭环 |
| M6 | 历史 Bless 事件公开投影和 UI 算式完整且不重复；新事件不变 |
| M7 | 人类可 spend/decline 新窗口，owner/pending/重载/资源结算正确，旧特化窗口不回归 |
| M8 | Lucky 三意图、Overchannel、普通 Hide 都有服务端驱动的可用 UI 和真实提交链 |
| M9 | 新 pending shapes 的 save/load、reset/restart、swipe/rollback/replay 生命周期有覆盖 |
| M10 | 核心 en/zh-CN、对外使用/迁移说明、PR 风险声明和完整 CI 证据齐全 |

---

## §5 验收命令与环境要求

### 5.1 聚焦后端与全仓

使用项目要求的 Python 环境执行，并记录实际命令和结果：

```bash
python -m pytest -q tests/rulesets/test_dnd2024_check_windows.py tests/rulesets/test_dnd2024_dark_ones_blessing.py tests/rulesets/test_dnd2024_retaliation.py tests/rulesets/test_dnd2024_cunning_action.py tests/rulesets/test_dnd2024_armor_training.py
python -m pytest -q tests/rulesets
python -m ruff check src/
python -m mypy
python -m pytest -q
```

- 另需执行本任务新增的旧存档、公开投影和生命周期测试所在套件。
- Review 时全仓结果为 `7965 passed / 7 skipped / 1 failed`，失败位于未触及的 diagnostics 日志归档顺序测试。执行时必须在干净环境/CI 复核并与基线分类；不得默认它永远无关，也不得为让本任务变绿而顺手修改无关 diagnostics。

### 5.2 前端与真实 UI

使用仓库要求的 Node 22 / npm 10 环境和 lockfile 依赖，不得以当前不匹配的 Node 24 环境结果替代：

```bash
npm run locales:check
npm run typecheck
npm run lint
npm test -- --maxWorkers=1
npm run build
npm run test:e2e -- e2e/dnd2024-workspace.spec.ts
npm run test:e2e:smoke
```

工作目录均为 `frontend-v2/`。若新增窗口组件测试不在默认 `npm test` 范围内，需补充对应命令并说明原因。不得通过跳过 smoke 来声称人类 UI 已验收。

### 5.3 最终差异检查

```bash
git diff --check
git status --short
git diff --stat
```

同时核对最终 changed-file list、公开文档无 `docs/internal/` 链接、无 lockfile/生成物/日志的意外变化。

---

## §6 建议提交边界

为避免把所有问题混成不可审查的大 hunk，建议至少分为以下独立提交；可根据实际依赖调整，但每个提交必须能独立构建和测试：

1. `fix(dnd2024): correct 1C cross-feature combat resolution` — R1–R4 及聚焦回归。
2. `fix(dnd2024): preserve legacy character and combat event behavior` — R5、R6、R9 及迁移/兼容测试。
3. `feat(frontend): expose 1C combat decisions and declarations` — R7、R8、必要投影/API 类型、UI 测试和核心 locale。
4. `docs(dnd2024): close 1C compatibility and rollout notes` — R10、执行报告和最终验证摘要。

不要 amend 既有 1C 历史、force push、推送或执行 GitHub review 操作，除非用户另行明确授权。

---

## §7 禁止事项与停止条件

### 7.1 禁止事项

1. 禁止只补测试而保留 §2 的错误行为，也禁止修改回归测试去适配已知错误。
2. 禁止把旧角色 Armor Training 修复为 actor view 的“空对象即无惩罚”，禁止猜测迁移、默认全受训或无条件覆盖用户数据。
3. 禁止为 Lucky/Retaliation/Hide 在 generic combat engine 增加具体 feature/spell id 分支；D&D 机械留在 ruleset owning module。
4. 禁止在客户端掷骰、计算最终命中/伤害、扣资源或绕开 decision owner 校验。
5. 禁止为 UI 使用私有 `api._*` / `registry._*` 穿透，禁止把完整 `WebAPI` 当 service locator 传入新 service。
6. 禁止为隐藏重复触发而删除后续合法伤害，或为修 Retaliation 而提前修改正式 snapshot/重复发事件。
7. 禁止顺手实施 1D、扩 Lucky/Cutting Words 的已裁剪规则面、重排无关 RNG、重构无关肥大文件或格式化全仓。
8. 禁止从公开文档引用本任务书或其他 `docs/internal/` 文件。

### 7.2 停止并上报

出现以下任一情况，不得带疑继续：

1. 实际 HEAD 与 review 锚点的相关实现已发生语义变化，导致某项问题无法复现或修复范围需要重新界定。
2. Armor 旧角色缺少足以安全派生的 canonical 信息，或 companion 使用不同权威结构；无法证明迁移正确时 fail closed 并上报，不猜。
3. 修复需要改变通用事件 schema、storage/codec、rollback/replay、公开 API 或多人权限契约，而现有任务边界没有该 owning 设计。
4. Retaliation 的伤害后状态与嵌套 pending decision 无法在不重复事件/骰子的前提下组合。
5. 为暴露 Lucky/Overchannel 可用性必须引入新的持久化字段或让客户端接触隐藏信息。
6. 上游新增改动与 1C 产生语义冲突，即使 `git merge-tree` 仍无文本冲突，也必须先重审集成。
7. 全仓或前端出现失败且无法区分本任务回归与既有问题；先取得干净基线/CI 证据，不得隐瞒或无关修改。
8. 任何 R1–R8 需要被裁剪。它们都是 review 阻塞项；裁剪即表示本收尾任务未完成。

---

## §8 执行报告模板（执行侧填写）

```text
## 1C-R 执行报告

- 实际基线：HEAD=<commit>；upstream/main=<commit>；merge-base=<commit>
- 开工工作区：clean=<yes/no>；语义锚点变化=<无 / 列表>
- 修复前复现：R1=<命令+结果>；R2=<命令+结果>；R3/R4/R5/R6=<代码/测试证据>

### 工作包
- [ ] R1 Lucky：实现摘要；测试名；骰迹/RNG/资源结果
- [ ] R2 Dark One's Blessing：首次击败去重语义；同目标/多目标测试
- [ ] R3 Retaliation：伤害后状态来源；专注成功/失败测试
- [ ] R4 Hide：combat 生命周期；下一战与对照条件测试
- [ ] R5 Armor：实际加载 owning path；迁移/版本策略；幂等与旧角色测试
- [ ] R6 历史事件：旧 schema 证据；兼容边界；新/旧投影与 UI 测试
- [ ] R7 窗口 UI：组件与真实浏览器 spend/decline/owner/reload 结果
- [ ] R8 主动 UI：Lucky×3、Overchannel、Hide 的投影和真实提交结果
- [ ] R9 生命周期：save/load、reset/restart、swipe/rollback/replay 测试
- [ ] R10 交付：locale、公开文档、PR 风险说明

### 验证
- 聚焦 pytest：<命令/结果>
- tests/rulesets：<命令/结果>
- ruff / mypy：<结果>
- 全仓 pytest：<结果；既有失败分类证据>
- locales/typecheck/lint/vitest/build：<结果>
- focused e2e / browser smoke：<环境/命令/结果>

### 最终审查
- changed files：<完整列表>
- 公开契约/迁移变化：<无 / 列表及文档>
- 停止条件：<无 / 条目、原因和处理>
- 残余风险/迁移债：<无 / 列表；任何 R1–R8 残余均阻止完成>
- 最终 changed-file 与运行时产物检查：<结果>
- 等待独立 review：<reviewer / 日期 / 结论>
```
