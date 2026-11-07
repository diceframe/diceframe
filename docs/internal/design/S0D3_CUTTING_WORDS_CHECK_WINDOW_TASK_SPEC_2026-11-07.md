# S0-D3 任务书：D20 掷后窗口与 Cutting Words 攻击检定面

| 项 | 值 |
|---|---|
| 切片编号 | S0-D3（本文件 v2，取代旧 S-P3 草案） |
| 唯一验收载体 | College of Lore 3 级 `cutting_words`，只接攻击检定成功面 |
| 通用能力 | 攻击 d20 已掷且预判命中后、伤害骰与不可逆伤害落地前的服务器权威反应窗口 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Cutting Words 在 p35 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`；必须先执行并合入 S0-A |
| 前置任务 | `S0A_SUBCLASS_GRANT_IMPROVED_CRITICAL_TASK_SPEC_2026-11-07.md`（S0-A） |
| 建议分支 | `feat/dnd2024-s0d3-cutting-words` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 本任务书是本切片的唯一需求来源。不要用旧 S-P3、其他施工计划或外部内容扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. 开工时记录 S0-A 合入后的实际集成提交，并从该提交新建干净工作树。不要直接在已有 `df-int` 工作树施工，不要清理无关未跟踪文件。
3. 若 S0-A 尚未合入，停止。本任务依赖真实的 `features.subclass_ref` 授予、owner-scoped 查询和战斗 actor 的子职投影。
4. 本切片只实现 **Cutting Words 对攻击检定的反应**。不实现它的伤害掷骰面、属性检定面，不实现 Bardic Inspiration 的授予/队友加骰面。
5. 旧版同时包含的 Lucky、Dark One’s Own Luck、专长资源、死亡豁免、专注豁免、伤害窗口均移出本任务，后续各自独立成书。
6. 不使用 `.rulebooks`、open5e、2014 SRD 或第三方译文作为规则依据；第 2 节官方英文原文是本任务的规则基准。

## 2. 官方规则与本切片语义

SRD 5.2.1 p35，College of Lore：

> **Level 3: Cutting Words**
>
> You learn to use your wit to supernaturally distract, confuse, and otherwise sap the confidence and competence of others. When a creature that you can see within 60 feet of yourself makes a damage roll or succeeds on an ability check or attack roll, you can take a Reaction to expend one use of your Bardic Inspiration; roll your Bardic Inspiration die, and subtract the number rolled from the creature’s roll, reducing the damage or potentially turning the success into a failure.

本切片只实现其中攻击检定分支，并锁定以下语义：

1. 触发是在攻击检定**已经成功**之后；天然 1、普通 miss 不开窗。
2. 反应者是实际拥有 `(subclass:college_of_lore, cutting_words)` 的 Bard；不能仅凭 `class_id == bard` 或等级授予。
3. 反应者必须能看见掷骰的攻击者，且双方距离不超过 60 尺。现有 theater-of-mind 下以 `_distance(combat, reactor_id, attacker_id)` 为距离依据；Blinded 反应者或 Invisible 攻击者不满足该看见条件。
4. 使用者支付一个 Reaction 和一次 `bardic_inspiration`，然后掷当前 Bardic Inspiration die，从攻击总数中减去该值。
5. 扣减不修改 d20 原值、优劣势骰迹、攻击加值或 Bless 骰。`natural` 与既有暴击事实保持不变；重击仍然必中，不能被总数扣减改成 miss。
6. 若最终仍命中，才掷伤害并进入既有 Shield / Uncanny Dodge / 命中触发法术链；若改为 miss，不掷伤害、不扣 HP、不打开这些命中后窗口。
7. 官方还允许伤害掷骰和属性检定触发；本任务没有实现它们，文档和测试不得把 Cutting Words 整体标为“全部完成”。

Bardic Inspiration die 使用既有 `bardic_die` 成长轨道：1–4 级 d6、5–9 级 d8、10–14 级 d10、15–20 级 d12。资源池使用既有 `resources.class.bardic_inspiration`，其长短休恢复规则不在本任务修改。

## 3. 已核实的代码事实

| 事实 | 位置 |
|---|---|
| 攻击当前依次消费 d20、可选第二枚 d20、可选 Bless d4，随后计算 total / critical / hit | `combat/resolution.py::_attack_events` |
| 当前命中后立即掷伤害，再构造 Divine Smite 与 Shield / Uncanny Dodge 决策 | 同上 |
| 原始攻击已经产出 `check.resolved(kind="attack")`；reducer 对该事件不落状态 | 同上、`combat/reducer.py` |
| `_reaction_eligible()` 统一检查 HP、反应预算、失能与 reaction_blocked | `combat/reaction_spells.py` |
| 既有反应窗口使用 `decision.pending` / `decision.resolve` / `decision.cleared`，客户端不能提交结算数值 | `reaction_spells.py`、`validation.py` |
| `dnd2024.reaction.spent` 与 `dnd2024.class_resource.spent` 已有 reducer 支持 | `combat/reducer.py` |
| `resource_state()` 能读取 actor 投影中的 canonical class resource | `features/resources.py`、`combat/view.py` |
| Bardic Inspiration 池与 Bard 骰面轨道已经存在 | `content/rest_catalogs/srd_recovery.json`、`content/progression_catalogs/srd_classes.json` |
| 子职表已在 College of Lore 3 级列出 `cutting_words`，但特性定义尚不存在 | `content/subclass_catalogs/srd_subclasses.json`、特性 catalog |
| S0-A 后 `has_feature()` / `feature_value()` 支持可选 `owner`，战斗 actor 能看到实际 `subclass_ref` | S0-A 契约及其执行结果 |

## 4. 实现契约

### 4.1 特性声明与目录

在 canonical 特性 catalog 增加：

```json
{
  "id": "cutting_words",
  "owner": "subclass:college_of_lore",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p35:cutting-words",
  "summary": "When a creature you can see within 60 feet succeeds on an attack roll, take a Reaction and expend one Bardic Inspiration use to roll your Bardic Inspiration die and subtract it from the attack total.",
  "parameters": {
    "trigger": "attack_roll_success",
    "die_sides_track": "bardic_die",
    "resource_id": "bardic_inspiration",
    "range_ft": 60
  }
}
```

1. 该声明挂在 Bard 的职业内容段中，但身份必须显式属于 `subclass:college_of_lore`；首次授予等级必须与子职表的 3 级一致。
2. `summary` 是本切片实际实现的攻击面摘要，不声称伤害面和属性检定面已自动化。
3. 复用 S0-A 的 `die_sides_track` 校验与读取，不复制等级到骰面的 Python 表。
4. 只接受非 bool 整数 `range_ft > 0`、固定资源 id `bardic_inspiration` 和本切片支持的 trigger；目录非法时 fail closed。
5. 英文/中文 locale 同步增加标签：英文 `Cutting Words`；中文 `语出惊人`。canonical ref 不随语言变化。
6. 不修改 subclass catalog、progression catalog、rest catalog 或资源 schema。

### 4.2 新窗口模块

新建 `combat/check_windows.py`，只承载本切片的掷后攻击窗口，不发明通用插件框架。至少提供以下内部能力：

1. 常量 `CHECK_WINDOW_DECISION = "d20_check_window"`。
2. 枚举当前有资格使用 Cutting Words 的 player/companion actor；enemy 不新增职业特性框架。
3. 构造服务器权威的 pending decision；验证 `spend / decline`；完成扣减与后续结算；为 AI/companions 生成确定性选择。
4. 窗口创建和最终验证都调用 `_reaction_eligible()`，并复查 owner-scoped 特性、资源 `current >= 1`、距离与看见条件。
5. 不在 `resolution.py` 写职业名或子职名判断；具体特性资格和参数读取集中在窗口模块。

### 4.3 pending decision 形状

每个反应者一个决策，至少包含：

```json
{
  "decision_id": "check-window:<source-intent-id>:<reactor-actor-id>",
  "kind": "d20_check_window",
  "actor_id": "player:<reactor>",
  "attacker_id": "enemy:<attacker>",
  "target_id": "player:<target>",
  "assigned_to": "<human uid or GM uid>",
  "options": ["spend", "decline"],
  "feature_id": "cutting_words",
  "owner": "subclass:college_of_lore",
  "resource_id": "bardic_inspiration",
  "die_sides": 6,
  "check": {"...": "服务器生成的原始或当前攻击 check 深拷贝"},
  "attack_context": {"...": "继续本次命中结算所需的服务器权威上下文"},
  "next_decision": null
}
```

1. `actor_id` 是反应者，不是攻击者；权限和反应扣减均以此为准。
2. `check` 至少含原始 `rolls / natural / modifier / bless_bonus / total / target / success / critical / advantage_mode`。
3. `attack_context` 由服务器构造并深拷贝，含原意图、`spend_attack_action` 及继续伤害结算所需数据。客户端只能提交 `decision_id` 与 `option`，不得提交或覆盖这些字段。
4. 真人玩家由本人决定；companion 和 AI 托管玩家指派 GM/server automation authority。
5. 多个有资格的诗人按战斗 actor 的稳定顺序排列，通过 `next_decision` 串行提供。不得一次把同一诗人的多个重复窗口入队。
6. 前任 decline 后进入下一位；前任 spend 且仍命中时才进入下一位；一旦改为 miss，丢弃剩余候选并结束本次攻击。

### 4.4 攻击结算重排

在 `_attack_events` 中提取一个共享的命中后结算辅助函数，供无窗口直通和窗口 finisher 共同调用：

1. 攻击动作/攻击次数成本、`attack_roll` 提前终止、原始 `check.resolved`、Rage 延长与攻击条件消费仍只发生一次，不在 finisher 重放。
2. 有 Cutting Words 候选时，在原始攻击 check 之后发 `decision.pending` 并返回；此时不能消费伤害骰或产生 HP/专注/胜利事件。
3. 无候选时走既有命中后辅助函数；事件字段、顺序和 RNG 消耗必须与基线一致。
4. finisher 不得重新调用完整 `_attack_events`，否则可能重复支付动作、重掷 d20 或重复触发攻击条件。
5. 最终命中后沿用既有伤害 AST、Sneak Attack、Rage、抗性、Divine Smite、Shield、Uncanny Dodge、专注和胜利链，不复制第二套伤害规则。下游 Shield 等改判必须读取当前已扣减的 attack total，不得回看原始 total。
6. 最终 miss 时不构造伤害事件，不记 Sneak Attack 使用，不打开命中后反应/法术窗口，不触发受击专注或胜利。

### 4.5 spend / decline 与事件

decline：

1. 只发既有 `dnd2024.decision.cleared`，不扣 Reaction、不扣资源、不掷诗人骰。
2. 没有下一位候选时，以当前 check 继续原命中后结算；原始攻击事件保持不变。

spend：

1. 先发 `decision.cleared`，再发 `dnd2024.reaction.spent(actor_id=reactor, reason="reaction_feature")` 与 `dnd2024.class_resource.spent(actor_id=reactor, resource_id="bardic_inspiration", amount=1)`。
2. 只掷一次 `1d<die_sides>`，并发出一条审计事件：

```json
{
  "type": "check.resolved",
  "kind": "cutting_words",
  "check_kind": "attack",
  "actor_id": "<attacker>",
  "target_id": "<target>",
  "source_actor_id": "<reactor>",
  "feature_id": "cutting_words",
  "owner": "subclass:college_of_lore",
  "rolls": ["<inspiration die>"],
  "natural": "<unchanged natural>",
  "modifier": "<negative inspiration die>",
  "base_total": "<total before this use>",
  "total": "<total after subtraction>",
  "target": "<unchanged AC>",
  "success": "<final hit>",
  "critical": "<final critical>"
}
```

3. reducer 对 `check.resolved` 继续只作审计事件；不得为总数修改新增 canonical 状态。
4. 每次使用基于当前 total 再扣一次，多个不同诗人的效果可依次累积；同一诗人的 Reaction 已扣，不能再次使用。
5. 若窗口打开后资格已失效，validation 拒绝 spend，不扣资源、不掷骰；不得把非法 spend 静默当 decline。
6. reducer 已有 Reaction 与资源非负校验，不在本任务放宽。

### 4.6 命中、暴击与 RNG

1. `adjusted_total = current_total - inspiration_roll`；`natural`、原 d20 rolls、attack modifier、Bless bonus 均不变。
2. 天然 20 或 S0-A 特性造成的既有 critical 仍为 critical 且必中；Cutting Words 不能改变 d20 暴击身份。
3. 对依赖“命中后 5 尺内升级重击”的条件，只有最终仍命中才保留/重算该重击；若改为 miss，不产生重击伤害。
4. 普通攻击最终 `hit = critical or adjusted_total >= target_ac`；天然 1 不会开窗。
5. RNG 顺序固定：
   - 无候选：与原实现完全一致；
   - 开窗但 decline：不新增随机数；
   - spend：先掷诗人骰，最终仍命中才掷伤害及后续专注等随机数；
   - spend 后 miss：诗人骰之后立即停止本次攻击的 RNG 消费。
6. 不得为了保持测试序列而预掷伤害再丢弃；本窗口的意义就是伤害尚未发生。

### 4.7 validation、available intents 与自动化

1. `d20_check_window` pending 时，只允许 `decision.resolve` 与 `combat.message`；其他战斗意图拒绝。
2. 只有 assigned actor/对应 GM automation authority 能解决；其他玩家不能代替诗人选择。
3. resolve 重新验证当前资格、资源和 Reaction，option 之外的数值字段一律不参与结算。
4. `available_intents()` 必须把该窗口显示给非当前回合的反应者，并避免 GM 分支重复返回同一 pending。
5. `next_automatic_intent()` 增加该 kind 的分发。AI 只有在攻击者对反应者一方敌对、当前非重击且 `total - die_sides < target`（最大骰可能翻盘）时 spend；否则 decline。
6. AI 不替真人玩家做决定，不为了降低普通命中总数而浪费资源。

## 5. 改动白名单

只允许修改以下文件；需要任何其他文件时停止并报告：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/models.py`：仅允许为本节新增参数做最小目录校验，不改变既有特性语义
5. 新建 `src/rulesets/dnd2024/combat/check_windows.py`
6. `src/rulesets/dnd2024/combat/resolution.py`
7. `src/rulesets/dnd2024/combat/validation.py`
8. `src/rulesets/dnd2024/combat/engine.py`
9. 新建 `tests/rulesets/test_dnd2024_cutting_words.py`
10. 本任务书：只允许追加第 10 节执行报告

明确不改 `combat/reducer.py`、`combat/reaction_spells.py`、`combat/view.py`、`features/resources.py`、rest/runtime/前端、subclass/progression/rest catalog。若 S0-A 的实际交付未提供所需投影，停止，不在本任务补做 S0-A。

## 6. 测试矩阵

新测试必须通过真实 `resolve_intent -> apply_batch` 链，不只调用窗口辅助函数。角色升级走真实 `apply_next_level` 并选择 College of Lore；允许在战斗 fixture 中设置位置、条件和 AI 控制，但不伪造窗口结算结果。

### 6.1 声明与所有权

1. College of Lore 3 级角色拥有 owner 为 `subclass:college_of_lore` 的 Cutting Words；2 级没有。
2. 3 级 Bard 未选择/缺失 `subclass_ref` 时没有该特性，不开窗。
3. 3 级 Bard 选择其他职业子职、其他 Bard 子职占位或伪造 owner 时不能获得该特性。
4. 非 Bard 即使写入 `subclass:college_of_lore` 也不能获得该特性。
5. 英文/中文投影名称正确，内部 `owner`、resource id 和 trigger 不本地化。
6. `die_sides_track` 分别读出 d6/d8/d10/d12；非法 track、range、resource 或 trigger 在目录加载时 fail closed。
7. 既有职业特性与资源 catalog 校验保持通过。

### 6.2 开窗、权限与排除

8. 敌方攻击普通命中且 3 级 College of Lore Bard 有资格时，产生一个 `d20_check_window`，`actor_id` 是 Bard，options 恰为 spend/decline。
9. 开窗前已有动作成本与原始攻击 check，但没有伤害、HP、专注或胜利事件；pending 的 check 与原始骰迹一致。
10. 攻击原本 miss 或天然 1 时不开窗。
11. Bard 2 级、无实际子职、资源为 0、Reaction 为 0、失能、reaction_blocked 或 HP 为 0 时均不开窗。
12. Blinded Bard 或 Invisible 攻击者不开窗； Bard 与攻击者距离 61 尺不开窗，60 尺可开窗。
13. 目标不是 Bard 本人也可开窗；触发距离按 Bard 到攻击者计算，而不是 Bard 到受击者。
14. 非 assigned 玩家 resolve 被拒；pending 期间 attack/cast_spell/move/end_turn 等被拒，combat.message 可用。
15. 客户端额外提交 total、damage、rolls、die_sides 或伪造 attack_context 不得改变结算。
16. 开窗后把资源或 Reaction 改为不足再 spend，validation 拒绝且状态不变。

### 6.3 使用、放弃与改判

17. decline 不扣 Reaction/资源、不掷诗人骰，伤害与基线同一 RNG 序列一致。
18. spend 扣 1 Reaction 和 1 Bardic Inspiration，只掷一次当前诗人骰，并产生字段完整的 `check.resolved(kind="cutting_words")`。
19. 原 total 18、AC 15、诗人骰 4 时 total 14、success=false；无伤害骰、无 HP 变化、无 Sneak Attack、无 Shield/Uncanny Dodge/Divine Smite pending。
20. 原 total 18、AC 15、诗人骰 2 时 total 16 仍命中；之后正常掷伤害并可进入既有命中后窗口，Shield 看到的是 16 而非 18。
21. 扣减不修改 natural、原 d20 rolls、攻击 modifier 或 Bless bonus；Bless 已消费的骰不重掷。
22. 天然 20 与 S0-A 扩展暴击均开窗但扣减后仍 critical+命中；AI 对这类窗口 decline。
23. 仅由 5 尺内条件升级的暴击在改为 miss 后不造成任何伤害；仍命中时继续按既有条件结算。
24. 同一 Bard 使用一次后 Reaction 为 0，不能对下一攻击再次开窗；长休恢复行为仍由既有资源测试守护。

### 6.4 多反应者、自动化与重放

25. 两位有资格 Bard 时按稳定顺序串行开窗；第一位 decline 后第二位可选择，不能重复第一位窗口。
26. 第一位 spend 但仍命中时第二位可继续扣减；第一位直接改为 miss 时第二位窗口不再出现。
27. AI/companion 在非重击且最大骰可翻盘时 spend，否则 decline；真人不自动生成 resolve。
28. 每批事件 reducer 重放后 canonical 状态与 live 一致；重复应用同一批次不产生第二次扣费或伤害。
29. 无资格角色的普通攻击、既有 Shield/Uncanny Dodge、Counterspell 与 S0-A 暴击回归保持原行为。
30. 无候选直通、decline、spend 后命中、spend 后 miss 四条路径的 RNG 消耗分别符合 §4.6，不多掷也不少掷。

## 7. 明确非目标

1. Cutting Words 的伤害掷骰面与属性检定面。
2. Bardic Inspiration 的附赠动作授予、队友持骰与失败后加骰。
3. Lucky、Dark One’s Own Luck、Guidance、重掷、优势/劣势改判。
4. 死亡豁免、专注豁免、法术豁免或探索属性检定窗口。
5. enemy 的职业/子职特性框架，多窗口并行投票，通用反应插件市场。
6. 新前端组件、公共 feed 版式、本地化排版或 GM 引导文案。
7. 既有 Shield/Uncanny Dodge 注册表重构和 rest/resource/reducer 改造。

## 8. 验收命令

在干净工作树中使用项目既有 Python 环境；Windows 下为 pytest 指定独立 basetemp，避免共享临时目录干扰：

```powershell
python -m pytest tests/rulesets/test_dnd2024_cutting_words.py tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_resting.py --basetemp=.ptmp-s0d3 -q
python -m pytest tests/rulesets --basetemp=.ptmp-s0d3-all -q
ruff check src/rulesets/dnd2024/combat/check_windows.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/validation.py src/rulesets/dnd2024/combat/engine.py tests/rulesets/test_dnd2024_cutting_words.py
ruff format --check src/rulesets/dnd2024/combat/check_windows.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/validation.py src/rulesets/dnd2024/combat/engine.py tests/rulesets/test_dnd2024_cutting_words.py
git diff --check
git status --short
```

基线已知的 11 条 `character_builder` 环境失败不得被“顺手修复”；若出现，应与同一基线复跑对照并如实记录。不得删除或放宽失败测试来制造通过。

## 9. 停止条件

出现任一情况立即停止并报告，不自行扩大范围：

1. S0-A 未合入，或实际实现没有提供选择感知的子职所有权及战斗投影。
2. 为复用命中后结算必须复制伤害规则，或需要修改白名单外的法术/反应/reducer 代码。
3. 无法在不重掷 d20、不重复支付成本的前提下完成窗口 finisher。
4. 无候选直通路径的事件或 RNG 与基线出现差异。
5. 需要实现 Cutting Words 的伤害面、属性检定面或其他 D20 特性才能完成本切片。
6. 官方原文、子职授予等级、Bardic Inspiration 池或骰面轨道与第 2/3 节不符。
7. 出现权限绕过、客户端可伪造结算数值、资源扣负、重放重复扣费等正确性问题。
8. 既有失败集发生变化且不能用同基线对照证明为环境既有问题。

## 10. 执行报告模板

完成后把以下内容追加到本文件；不得只口头声称完成：

```text
## S0-D3 执行报告（<日期>）
- 基线：<S0-A 合入后的完整 commit>
- 分支 / HEAD：<branch> / <commit>
- 改动文件：<逐项列出；与白名单比较>
- 官方依据：SRD 5.2.1 p35；source_ref=<实际写入值>
- 特性身份与所有权：<目录、Resolver、actor 投影结果>
- 窗口与 continuation：<pending/validate/finish/多反应者摘要>
- 命中后复用：<共享辅助函数及未复制的规则清单>
- 事件与资源：<spend/decline/审计事件/reducer 结果>
- RNG：无候选 / decline / 仍命中 / 改 miss 四条实测序列
- §6 测试矩阵：1–30 逐项填写 通过/失败/未实现及证据，不得汇总跳项
- 聚焦测试：<命令与 passed/failed 数字>
- rulesets 全量：<命令与 passed/failed 数字；失败逐项归因>
- ruff / format / diff 检查：<结果>
- 非目标确认：§7 七项均未越界
- 遗留问题：<没有则写“无”；伤害面与属性检定面仍属明确后续范围>
```
