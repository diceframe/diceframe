# S0-F10 任务书：Bonus Action 路由与移动附带效果

| 项 | 值 |
|---|---|
| 切片编号 | S0-F10（独立新任务，不覆盖旧 SP） |
| 验收载体 | `instinctive_pounce`、`tactical_shift`、`cunning_action`、`steady_aim`、`fast_hands`、`fleet_step`、`heightened_focus`；并回归 `martial_arts`、`monks_focus` 三项基础能力、`second_wind` 与 `action_surge` |
| 通用能力 | Bonus Action 底层动作路由、附带移动、真实 Hide/技能检定、连续状态 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-F1、S0-F9 已合入；子职特性必须真实授予 |
| 建议分支 | `feat/dnd2024-s0f10-action-routing` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 执行纪律与范围

1. 从前置任务合入后的实际提交新建干净工作树，记录提交；不得清理无关工作区内容。
2. 本切片完整实现七项特性，不得只增加名称、按钮或 Invisible 条件。
3. 所有动作、检定、移动、资源和条件均由 `validate_intent → resolve_intent → apply_batch` 决定；客户端不得提交检定结果、速度、发现 DC 或位置结果。
4. 只使用本任务引用的官方 PDF，不使用 2014 规则、第三方译文或其他计划扩张范围。
5. Hide、Fast Hands 的环境/物品前提若缺少权威数据，按第 7 节停止，不做“简化版”。

## 2. 官方英文关键原文与页码

SRD p29、p48、p62：

> **Instinctive Pounce.** As part of the Bonus Action you take to enter your Rage, you can move up to half your Speed.

> **Tactical Shift.** Whenever you activate your Second Wind with a Bonus Action, you can move up to half your Speed without provoking Opportunity Attacks.

> **Cunning Action.** On your turn, you can take one of the following actions as a Bonus Action: Dash, Disengage, or Hide.

> **Steady Aim.** As a Bonus Action, you give yourself Advantage on your next attack roll on the current turn. You can use this feature only if you haven’t moved during this turn, and after you use it, your Speed is 0 until the end of the current turn.

SRD p183 的 Hide 是本切片不可裁减的规则：

> With the Hide action, you try to hide yourself. To do so, you must succeed on a DC 15 Dexterity (Stealth) check while you’re Heavily Obscured or behind Three-Quarters Cover or Total Cover, and you must be out of any enemy’s line of sight; if you can see a creature, you can discern whether it can see you.

> On a successful check, you have the Invisible condition while hidden. Make note of your check’s total, which is the DC for a creature to find you with a Wisdom (Perception) check.

> You stop being hidden immediately after any of the following occurs: you make a sound louder than a whisper, an enemy finds you, you make an attack roll, or you cast a spell with a Verbal component.

SRD p64、p52、p51–52：

> **Fast Hands.** As a Bonus Action, you can do one of the following. **Sleight of Hand.** Make a Dexterity (Sleight of Hand) check to pick a lock or disarm a trap with Thieves’ Tools or to pick a pocket. **Use an Object.** Take the Utilize action, or take the Magic action to use a magic item that requires that action.

> **Fleet Step.** When you take a Bonus Action other than Step of the Wind, you can also use Step of the Wind immediately after that Bonus Action.

> **Heightened Focus.** Your Flurry of Blows, Patient Defense, and Step of the Wind gain the following benefits.

> **Flurry of Blows.** You can expend 1 Focus Point to use Flurry of Blows and make three Unarmed Strikes with it instead of two.

> **Patient Defense.** When you expend a Focus Point to use Patient Defense, you gain a number of Temporary Hit Points equal to two rolls of your Martial Arts die.

> **Step of the Wind.** When you expend a Focus Point to use Step of the Wind, you can choose a willing creature within 5 feet of yourself that is Large or smaller. You move the creature with you until the end of your turn. The creature’s movement doesn’t provoke Opportunity Attacks.

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| 战斗意图仅含基本动作与 `class_capability`，没有现成 Hide/Utilize 意图 | `combat/primitives.py::INTENT_TYPES` |
| 可用能力由 actor view、economy 与 feature boundary 生成 | `combat/engine.py::available_intents`、`_available_class_capabilities` |
| 能力动作 kind 仅支持既有基本动作，未知 kind 应 fail closed | `features/models.py::ACTION_KINDS`、`CapabilityAction` |
| 能力实际拥有与资源成本由 resolver/catalog 决定 | `features/combat.py::combat_capabilities`、`features/resolver.py::features_for` |
| Second Wind、Rage、Flurry、Patient Defense、Step 已有声明；本批特性不能仅凭 progression 条目视为已实现 | `content/class_feature_catalogs/srd_class_features.json` |
| 基本动作与职业能力分开结算，移动已有事件入口 | `combat/resolution.py::_basic_action_events`、`_class_capability_events`、`_movement_events` |
| capability 在服务端重新校验，不信客户端能力参数 | `combat/validation.py::_validate_class_capability` |
| 速度为 0 与攻击加减益已有共享聚合 | `combat/check_modes.py::effective_speed`、`attack_sources`、`attack_mode` |
| 每回合使用记录与条件/资源落账由 reducer 负责 | `combat/reducer.py::_reduce_event`、`_apply_hp_change` |
| 当前 positions 是一维距离，未核实 Heavily Obscured、视线和三类 Cover 的权威模型 | `combat/engine.py::initialize_state`、`_distance` |

## 4. 精确实现契约

### 4.1 声明与通用路由

1. 为七项特性增加 canonical declaration、owner/minimum level、对应页码 source_ref 与中英文 locale；不得修改等级轨道代替真实授予。
2. 新能力只声明服务器支持的 action kind；扩展 `ACTION_KINDS` 时必须同步声明校验、可用性、验证和结算，未知 kind 继续拒绝。
3. Bonus Action 只支付一次。底层 Dash/Disengage/Hide/Utilize/Magic item 不得再次扣 action 或 bonus_action；伪造 capability id、动作类别、目标或资源由服务端拒绝。
4. 附带移动以“至多一半当前 Speed（向下取整）”为独立额度，不凭空消耗普通 movement；事件记录来源、额度和实际距离。超过额度、Speed 为 0 或非法目的地均失败。
5. Opportunity Attack 抑制必须有作用域和到期点，不能写成全局永久免疫；本切片不实现完整借机攻击系统。

### 4.2 各特性边界

1. **Instinctive Pounce**：只在实际执行进入 Rage 的 Bonus Action 时附带一次移动；Sustain Rage、已狂暴或其他来源不触发。
2. **Tactical Shift**：只在实际激活 Second Wind 且支付其 Bonus Action/次数后附带移动；Tactical Mind 等非 Bonus Action 用法不触发；本次移动不引发 Opportunity Attacks。
3. **Cunning Action**：将同一 Dash、Disengage、Hide 语义路由为 Bonus Action，不复制出数值不同的“盗贼版”动作。
4. **Steady Aim**：本回合尚未发生任何移动才可用；只授予当前回合下一次攻击的 Advantage，命中或未中都消费；使用后至回合末有效 Speed 为 0，不能用附带移动绕过。
5. **Fast Hands**：三选一且互斥：①带 Thieves’ Tools 的开锁/拆陷阱或扒窃的 Dexterity (Sleight of Hand) 检定；②Utilize；③仅以 Magic action 使用明确要求 Magic action 的魔法物品。不得把任意施法、普通 Attack 或无需 Magic action 的物品纳入。
6. **Fleet Step**：完成一个非 Step of the Wind 的 Bonus Action 后立即可用一次 Step of the Wind；第二项不再扣 Bonus Action，但付费版本仍扣 1 Focus；Step→Fleet Step 不得递归触发。
7. **Heightened Focus**：Flurry of Blows 始终支付 1 Focus，未升级为两次、升级为三次 Unarmed Strike，不存在“免费 Flurry 两次”；仅付费 Patient Defense 额外掷两次 `martial_arts` die 并给予合计临时 HP；仅付费 Step 可携带 5 尺内、自愿、Large 或更小生物，随移动至回合末且被携带者移动不引发 Opportunity Attacks。Patient Defense 与 Step 的免费版本不获得这些付费升级。
8. 临时 HP 复用既有替换规则，不与旧临时 HP 相加；被携带者身份、尺寸、自愿和距离在结算时重验，不接收客户端声明为事实。

### 4.3 Hide 完整契约

1. 结算时检查持有者处于 Heavily Obscured，或 Three-Quarters/Total Cover 后，且不在任何敌人的 line of sight；缺少任一权威事实则不提供能力。
2. 服务端掷一次 d20 并加真实 Dexterity (Stealth) 加值，DC 固定 15；失败不获得 hidden/Invisible，Bonus Action 仍按既有动作结算。
3. 成功后保存 `hidden` 实例及 `check_total`、来源 `hide`，并授予同源 Invisible；发现者以 Wisdom (Perception) 对该 total 检定，被动感知若纳入也必须以同一 total 为 DC。
4. 攻击掷骰、施放含 V 成分的法术、高于耳语的发声或敌人发现后，立即同时结束 hidden 与其 Hide 来源 Invisible；其他来源 Invisible 不被误删。
5. 隐藏状态必须可重放、可存档并在事件中原样记录检定分项；不得把“条件名叫 invisible”当作完整 Hide 验收。

## 5. 改动白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/features/models.py`、`features/combat.py`、`features/resolver.py`。
3. `src/rulesets/dnd2024/combat/primitives.py`、`validation.py`、`resolution.py`、`engine.py`、`check_modes.py`、`reducer.py`、`runtime.py`。
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅本切片条件/动作声明。
5. 新建 `tests/rulesets/test_dnd2024_action_routing.py`；本任务书仅可追加执行报告。
6. 需要修改 progression/subclass catalog、物品/遭遇模型或任何其他文件时停止，不擅自扩张。

## 6. 编号测试矩阵

所有行为测试走真实 `validate_intent → resolve_intent → apply_batch`；角色经真实职业/子职链构造，禁止手写特性或预期事件冒充结算。

1. 七项特性在正确职业/子职和等级授予，低一级及错误子职不授予，source_ref 与中英文标签正确。
2. 未知 action kind、伪造 capability、重复 Bonus Action、资源不足均 fail closed 且状态不变。
3. 7 级野蛮人进入 Rage 可移动至多一半当前 Speed；Sustain Rage 与重复进入不触发。
4. Pounce 超半速、Speed 0、非当前回合均拒绝，普通 movement 不被错误扣除。
5. 5 级战士 Second Wind 后附带半速移动且不引发 OA；未实际激活或 Tactical Mind 不触发。
6. Cunning Action 的 Dash/Disengage 与基本动作数值一致，只扣 Bonus Action。
7. Steady Aim 在本回合已移动后不可用；未移动时可用且 Speed 立即变 0 至回合末。
8. Steady Aim 仅强化下一次当前回合攻击，未中即消费，不跨回合、不强化第二次攻击或能力检定。
9. Hide 在无遮蔽/掩蔽、仍处敌人视线或环境事实缺失时不可用，不授予 Invisible。
10. Hide 使用真实 Dexterity (Stealth) 对 DC15；14 失败、15 成功，检定分项与 RNG 顺序可重放。
11. Hide 成功保存 check_total；Perception total 小于该 DC 不能发现，达到或超过可发现并立即结束 hidden。
12. 攻击掷骰、含 V 法术、高于耳语发声分别立即终止 Hide 来源 Invisible；无 V 法术不因此终止。
13. 同时存在其他 Invisible 来源时，Hide 终止只移除自己的来源实例。
14. Fast Hands 三类选项互斥且只扣一次 Bonus Action；工具、锁/陷阱/口袋目标由服务端核验。
15. Fast Hands 不允许普通施法取代 Magic item，不允许激活不要求 Magic action 的物品或伪造物品结果。
16. 11 级 Open Hand 在非 Step Bonus Action 后获得一次免费动作经济的 Step；Step 本身不触发，链式递归不可能。
17. Fleet Step 选择付费 Step 时仍扣 1 Focus；Focus 不足只拒绝付费版本，不扣第二次 Bonus Action。
18. 10 级 Monk 付费 Flurry 恰三次徒手攻击；未升级付费 Flurry 为两次，不能零 Focus 使用 Flurry，Martial Arts 的免费 Bonus Unarmed Strike 仍只有一次且不与 Flurry 混同；未命中不补攻击。
19. 付费 Patient Defense 只掷两次 Martial Arts die 并给予临时 HP；免费版本只有 Disengage、无 Dodge 或临时 HP，新旧临时 HP 不相加。
20. 付费 Step 仅可携带 5 尺内自愿且 Large 或更小目标；Huge、非自愿或超距拒绝。
21. 被携带者随行至回合末且其移动不引发 OA；回合结束、存档/加载与重放后状态一致。
22. 既有 Rage、`second_wind`、`action_surge`、`martial_arts`、`monks_focus` 三项基础能力、普通移动、Invisible 与 Uncanny Dodge 测试无回归；Flurry 付费、Patient/Step 免费与付费分支各有行为证据。

## 7. 非目标与停止条件

### 非目标

- 完整 2D 地图、通用探索/盗窃模拟、所有魔法物品、完整 OA 或被动感知 UI。
- Thief’s Reflexes、Second-Story Work、其他 Monk/Rogue/Fighter/Barbarian 特性。
- 用文案、Invisible 单独授予或客户端选择结果替代 Hide/技能/物品结算。

### 停止条件

1. S0-F1/F9 未合入，或真实子职授予与 capability 接口不一致。
2. 没有可判定的 Heavily Obscured、视线、Three-Quarters/Total Cover 或敌人发现模型，且白名单无法建立最小权威闭环。
3. Fast Hands 缺少工具/对象/物品 Magic action 元数据，需要虚构目标或结果。
4. 附带移动无法与普通 movement、Steady Aim 和 OA 作用域保持同一权威状态。
5. 需要白名单外文件、content_version 迁移或前端私有规则才能完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_action_routing.py -q
python -m pytest tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_monk_resources.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_invisibility.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_action_routing.py
git diff --check
```

## 9. 交付要求

1. 报告实际基线、全部变更文件与测试 1–22 的逐项映射。
2. 单独展示 Hide 的前提、DC15、发现 DC、四类终止和来源隔离证据。
3. 展示三项 Heightened Focus 升级及 Fleet Step 非递归证据；保存重放与 RNG 记录。
4. 任何停止或偏差必须明示，不得把未实现子效果写成已完成。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 特性授予与动作路由：
- 附带移动、Steady Aim 与 OA 作用域：
- Hide 环境、检定、发现及终止：
- Fast Hands 三类边界：
- Fleet Step 与 Heightened Focus：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 重放、存档与 RNG 证据：
- 偏差、停止原因与未决事项：
