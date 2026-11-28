# FD10 任务书：Savage Attacker（武器伤害骰双掷取高）

- 日期：2026-11-28
- 状态：待批准（1 个实现期必须确认的开放点，见 §7）
- 上游依据：`FEAT_DOMAIN_SURVEY_2026-11-26.md` §L2-F2、GAP-54
- 前置：**#902**（GWF 的 `roll_damage_node_with_trace`、`settlement.die_floor`）；与 #903 的
  `feat_provenance` 无交集
- 边界：不含"每回合不限次"的房规；不含把 rider 也纳入重掷（GAP-58 的同一类作用域问题）

## 1. 规则原文与范围

SRD 5.2.1 p87（CC-BY），原文：

> Savage Attacker. Once per turn when you hit a target with a weapon, you can roll the weapon's
> damage dice twice and use either roll against the target.

中文（同一句）：

> 每回合一次，当你使用武器命中目标时，你可以掷两次武器的伤害骰，并自选其中一次应用在目标上。

由此确定四件事：

1. **每回合一次**，不是每攻击一次；
2. **命中**之后才发生（不需要提前声明），且是“can”——玩家可以选择不用；
3. **武器的伤害骰**掷两次，不是整次伤害；偷袭、印记、至圣斩、命中 rider 都是独立伤害源，
   不参与重掷（与 GWF 同一边界，GAP-58 的房规读法对它同样适用）；
4. **任意武器**：不限近战、不限双手、不限属性。远程武器同样适用——这一点与 GWF 的
   `two_handed_weapon_refs` 判定**不能共用**。

## 2. 与 GWF 的叠加顺序

GWF 是“每次掷出的骰面 1/2 视为 3”的 clamp，Savage 是“同一棵武器伤害 AST 掷两遍”。
两者同时存在时顺序只有一个正确答案：

1. 第一组：按 GWF 规则 clamp 后得到一组骰面；
2. 第二组：同样先 clamp，再得到一组骰面；
3. 比较两组的**总和**，取较高者。

不允许“先比较未 clamp 的总和、再对选中那组 clamp”——那会让 GWF 只在部分情况下生效。
实现上体现为：两次都走 `roll_damage_node_with_trace(..., die_floor=...)`，再比较。

## 3. 决策窗口

### 3.1 形状

新增一个 `pending_decisions` 条目（沿用 `hit_resolution._hit_spell_offer` 的模式——
伤害事件已构造、尚未结算时挂起）：

```
{
  "decision_id": "savage-attacker:<intent_id>",
  "kind": "savage_attacker",
  "actor_id": "<攻击者>",
  "assigned_to": "<玩家 uid>",
  "options": ["spend", "decline"],
  "target_id": "<目标>",
  "critical": <bool>,
  "conceal_damage": true,
  "damage_event": { ... deepcopy ... },
  "reroll_plan": { "weapon_node", "weapon_dice", "weapon_rolls",
                   "extra_total", "modifier", "rage_damage",
                   "damage_type", "die_floor", "first_amount" },
  "next_decision": { ... 链上的下一个窗口 ... }
}
```

`conceal_damage` 让公开投影（`visibility._public_damage_event` 的调用点）只保留
`type/target_id/damage_type/critical`：骰面与数值在 resolve 之后才出现。没有它，
玩家能看到第一组结果再决定，比印刷版强。`reroll_plan` 与 `next_decision` 不在
`_PUBLIC_DECISION_FIELDS` 里，非 GM 看不到（GM 分支仍是完整权威视图）。

`decision_id` 由 `intent_id` 派生，保证 replay 到达同一攻击时得到同一个窗口，而不是第二个。
提交走既有 `decision.resolve`。

### 3.2 每次命中都询问；decline 只放弃这一次

**明确的决定（2026-11-28）**：不做“本回合不再询问”的便捷选项。

理由：多打角色第一次攻击不用，通常是在算期望——这一击已经够高不该改，或者把机会留给
暴击。把它理解成“本回合不用了”是错的。所以 `decline` 的语义是“这一次不用”，本回合下一次
命中仍然询问。

代价是每回合可能问两三次；这是规则给的策略空间，不是噪声。

### 3.3 必须在伤害揭示之前

窗口挂起时**不得**把这次伤害写进公开事件流。否则玩家看到“这组已经很好”再决定不重掷，
等于白拿一次免费信息，比规则给的能力更强。Relentless Rage 已经用过同一手法（hold the
damage and offer the save），照此实现。

### 3.4 与至圣斩 / 减伤窗口同时可用时

命中路径上现有三个 offer：`_hit_spell_offer`（至圣斩）、`_damage_window_offer`（Cutting Words
等减伤）、`_hit_reaction_offer`（Shield / Uncanny Dodge）。它们用 `next_decision` 串成一条链，
一次只挂一个。

链的续接已经核实：每个 offer 把前一个存进自己决策的 `next_decision`（`check_windows.py:1117`
做 `deepcopy`），`decision.resolve` 之后再挂起（`:1747-1760`）。所以链不会丢条目，只是顺序
由“谁持有谁”决定。

Savage 的接法：`savage = _savage_attacker_offer(..., next_decision=offer)`，再把
`_damage_window_offer` 的 `next_decision` 从 `offer` 换成 `savage or offer`。得到的实际顺序是：

```
reaction (Shield / Uncanny Dodge) → damage_window (Cutting Words) → savage → smite
```

即防守反应最先（它可以把命中改成失手，后面的都不该发生），然后是减伤，再是 Savage（改武器
主骰），最后是至圣斩（加额外骰、消耗法术位）。Savage 排在至圣斩之前，是因为它改的是主骰，
而至圣斩追加的是独立伤害源；先定主骰，追加骰才有意义。

## 4. 声明与投影

```json
// content/feats/savage_attacker.json
"parameters": {"weapon_damage_rerolls_per_turn": 1}
```

- 键登记在 `parameter_registry.py`，阶段 `combat`，reader 指向本片的投影函数；
- 投影放 `features/feat_parameters.py`（与 GWF 的 die floor 并列），读作“每回合可重掷次数”；
- `automation_level`：从 `guided` 升为 `deterministic`（引擎完成掷骰与取高，玩家只做
  “用/不用”的选择）；
- 目前只有 1 条专长使用该键，值固定为 1；写成数字而不是布尔，是为将来“每回合两次”的
  房规留位置，且让 reader 的语义（次数）与参数名一致。

## 5. 状态、事件与重放

- **额度**：复用 `_feature_used_this_turn(combat, actor_id, "savage_attacker")`（与
  `sneak_attack` / `brutal_strike` 同一机制）。`use` 后置位；`decline` 不置位。
- **RNG**：第二组在 `decision.resolve` 时掷，事件必须记下两组的原始骰面：
  ```
  damage_event["damage_rerolls"] = [[3, 5], [6, 1]]
  damage_event["damage_reroll_picked"] = 1
  damage_event["damage_reroll_source"] = "savage_attacker"
  ```
  只记最终点数不记两组，会让审计无法回答“它是不是真的重掷了”；只记两组不记 picked，
  replay 就得重新比较（能算，但多一份实现）。
- **重放**：replay 从 `damage_rerolls` 取两组、按 `damage_reroll_picked` 取用，不再消耗 RNG。
  这一点与 `_reroll_check` 的做法一致（把 `rerolled_*` 留在事件上）。

## 6. 验收矩阵

1. 有 Savage、未用过的战士命中：出现 `savage_attacker` 决策，伤害**尚未**进事件流。
2. `use`：`damage_rerolls` 有两组、`damage_reroll_picked` 指向总和较高的一组；伤害取该组；
   本回合标记已用。
3. 同回合第二次攻击命中：不再询问（已用）。
4. `decline`：伤害等于原本那一组，不标记已用；同回合第二次命中仍然询问。
5. 暴击：两组都包含翻倍后的武器主骰（例如 2d6 暴击 → 每组 4 颗）。
6. 同时有 GWF：两组各自 clamp 后再比较；用例要构造“未 clamp 时第二组更大、clamp 后第一组
   更大”的骰序，证明顺序正确。
7. 远程武器命中：同样询问（与 GWF 的适用范围不同）。
8. 徒手 / 非法术武器：不询问。
9. 有偷袭或至圣斩的同一击：重掷只作用于武器主骰，rider 的骰面不变。
10. 无该专长的角色：事件流与改动前逐字节一致。
11. replay：同一份含 `damage_rerolls` 的事件重放，伤害点数与当时相同，且 RNG 不再被消耗。
12. `decision.resolve` 的 `use` 与 `decline` 都不能绕过“每回合一次”（构造第三次攻击）。

## 7. 实现期必须确认的开放点

1. **AI / 无人响应的 actor**：敌方或 companion 不产生该决策（`actor.kind` 限定
   `player`/`companion`，companion 由 GM 代答）。玩家掉线时的默认行为沿用现有决策超时策略，
   不新增。
2. **前端**：决策卡只渲染 `spend` / `decline` 两个按钮，所以选项用 `spend` 而不是
   `use`——与 Cutting Words 的伤害窗口同一个词，前端零改动。`damage_rerolls` 的展示
   仍留给前端片。
3. **与至圣斩的顺序需要一条端到端用例**：链的顺序已按代码核实（见 §3.4），但“两者同时可用”
   的真实命中还没有测试。实现时补一条，若实际顺序与 §3.4 不符，以代码为准并回写本文。

## 8. 非目标与停止条件

**非目标**：把重掷扩到 rider（GAP-58）；每回合多次；自动取高之外的“选哪组”第二窗口；
前端展示；其它起源专长。

**停止条件**：

- 需要改存档 schema 时停止；
- 需要在 `WINDOW_REGISTRY` 里新增一个“改伤害骰组”的 surface 时停止——先确认
  `pending_decisions` 真的不够用（目前判断是够的：`options` + `damage_event` 已能表达）；
- 发现必须改 `progression/` 任何文件时停止（本片只碰战斗结算与专长声明）。

## 9. 验收命令

```
ruff check src/ tests/
/c/Python314/python.exe -m mypy
TEMP=TMP=TMPDIR=E:/pytest-tmp pytest tests/rulesets/test_dnd2024_savage_attacker.py -q --basetemp=<新目录>
TEMP=TMP=TMPDIR=E:/pytest-tmp pytest tests/rulesets tests/architecture -n 6 --dist loadfile --basetemp=<新目录>
```

全量回归在收尾时跑，basetemp 放 E 盘，命令与 `docs/ENGINEERING_RULES.md` §15.3 一致。

## 9. 执行记录

### 9.1 落点

| 层 | 文件 | 改动 |
|---|---|---|
| 声明 | `content/feats/savage_attacker.json` | `parameters.weapon_damage_rerolls_per_turn = 1`，`automation_level: deterministic` |
| 注册表 | `features/parameter_registry.py` | 键登记，阶段 `combat`，reader 指向投影 |
| 投影 | `features/feat_parameters.py` / `resolver.py` / `combat/view.py` | 每回合次数投影到 actor view |
| 掷骰 | `combat/hit_resolution.py` | 主骰与附加骰分开掷；新增 `_savage_attacker_offer`；`conceal_damage` |
| 结算 | `combat/check_windows.py` | `_finish_savage_attacker`：resolve 时掷第二组、取高、记两组 |
| 分派 | `combat/resolution.py` | `savage_attacker` 决策分支 |
| 门禁 | `combat/validation.py` | 决策未解决时挡住其它意图 |
| 公开投影 | `combat/visibility.py` | `conceal_damage` 抹掉 amount/rolls |
| 测试 | `tests/rulesets/test_dnd2024_savage_attacker.py` | 10 例 |

### 9.2 偏差

| 计划 | 实际 |
|---|---|
| "同一棵伤害 AST 再掷一组" | **做不到**：那棵树里混着偷袭与印记，重掷会连 rider 一起换。改为在 `_hit_settlement_events` 里把主骰与附加骰分开掷（RNG 顺序不变），resolve 时只重掷主骰节点，再把 `extra_total`、`modifier`、`rage_damage` 加回去 |
| 选项用 `use` | 改用 `spend`（既有按钮已渲染），前端零改动；`Dnd2024CombatPanel.vue` 因 1872 行已贴 1500+100 棘轮上限，加一行就会失败 |
| 决策标识取 `intent_id` | 改取攻击检定的 `roll_id`：不新增 `intent_switch` 耦合债务，且 replay 到同一击得到同一窗口 |
| 额度用 `economy` 标记 | 改用 `damage_event["feature_uses"]` 延迟记账（与偷袭同一机制）：命中被改判落空时不消耗额度 |
| 只有 1 个开放点（AI/前端） | 多一个：公开投影必须 `conceal_damage`，否则"看到第一组再决定"是比规则更强的能力 |

### 9.3 关键实现事实

- 决策链顺序：`reaction → damage_window → savage → smite`（`next_decision` 深拷贝 + resolve 后续挂）。
- 两组都先各自过 GWF 的 clamp 再比较；暴击时两组各自含翻倍骰。
- 远程武器同样询问（与 GWF 的范围不同），徒手不询问。
- 只重掷武器主骰：`damage_rerolls` 的两组只含主骰点数，rider 骰面在 resolve 时原样保留。
- `decline` 不消耗额度，同回合下一次命中仍询问。

## 10. 测试隔离（2026-11-28）

### 10.1 影响面

全量 `tests/rulesets` 首轮：**99 failed / 3287 passed**，集中在 12 个文件，全部同一原因：
这些用例的卡是 `background:soldier`（直接建卡或经 `stalwart_guardian` / `relentless_vanguard`
两个 preset），Soldier 白送 Savage Attacker，于是每次武器命中都挂起窗口，伤害不再直接进事件流。
错误形态分三类：`StopIteration`（找不到伤害事件）、`INVALID_INTENT: resolve the savage attacker
decision first`（后续意图被挡）、状态断言失败（状态没变到期望值）。

| 文件 | 条数 | 卡的来源 |
|---|---|---|
| `weapon_mastery` | 24 | `_preset_sheet` → `stalwart_guardian` |
| `attack_state` | 23 | `barbarian()` → `relentless_vanguard` |
| `weapon_hands` | 15 | `_sheet` → `stalwart_guardian` |
| `rage` | 7 | `_barbarian_sheet` → `relentless_vanguard` |
| `item_activation` | 7 | `_runtime_sheet` → `stalwart_guardian` |
| `improved_critical` | 7 | `_fighter_sheet` → `stalwart_guardian` |
| `action_surge` | 5 | `_fighter_sheet` → `stalwart_guardian` |
| `retaliation` | 4 | `_barbarian_sheet` → `relentless_vanguard` |
| `equipment_data` | 4 | `_sheet` → `stalwart_guardian` |
| `unarmed_strike` | 1 | `_preset_instance(relentless_vanguard)` |
| `extra_attack` | 1 | `_fighter_sheet` → `stalwart_guardian` |
| `combat` | 1 | `_character` → `stalwart_guardian` |

### 10.2 决定

**这些文件不验证 Savage Attacker 的行为，它们只管各自的东西**（武器掌握、狂暴、动作经济、
双手持握……）。因此在这 12 个建卡 helper 里显式移除 `feat:savage_attacker`，与 `FD7` 的
GWF 测试同一做法；不改生产代码，不改这些用例的断言。

覆盖没有丢：`test_dnd2024_savage_attacker.py` 自己就用 `fighter_sheet`（Soldier 背景）建卡，
覆盖"命中挂窗 → use 取高 / decline 保留 / 每回合一次"的完整路径。

生产行为不变：选 Soldier 背景的角色仍然在每次武器命中时被询问。这与 GWF 测试移除风格专长
的理由相同——测试隔离的是"不相关的能力"，不是"不存在的规则"。

### 10.3 AI 托管不使用该窗口

全量首轮还有 8 条 AI 战斗用例失败（`test_ai_combat_choices` 6、`test_ai_player_combat` 2），
形态都是"攻击后目标 HP 未变"：AI 托管的玩家角色命中也挂了窗口，而 AI 决策链只答检定窗口
（`_automatic_check_window` 读的是 `check` 字段），不答伤害窗口，于是它的回合停在窗口上。

处理：`_savage_attacker_offer` 对 `kind == "player"` 且 `is_ai_controlled` 的 actor 直接不挂窗。
AI 因此**不使用**该专长，而不是替它选一个答案——它连"这一击值不值得重掷"的期望都不该由引擎
代算。等 AI 决策支持伤害窗口后，再把这里改成自动 `spend`（取高）。
