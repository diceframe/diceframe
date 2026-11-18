# S0-F15 执行报告：攻击前声明与连续状态

日期：2026-11-14
分支：`feat/dnd2024-s0f15-attack-state`（叠在 `feat/dnd2024-s0f14-hit-riders@f23704fc` 之上）
基线：`f23704fc`（F14 三个提交）
范围：Reckless Attack（野蛮人 2）、Frenzy（狂战士 3）、Brutal Strike（野蛮人 9）、Improved Brutal Strike（野蛮人 13，17 级同一条声明的等级升级）、Studied Attacks（战士 13）

---

## 1. 结论

五项特性落在**一种形状、四种后果**上，没有一份特判：

| 后果 | 特性 | 落点 | 事件证据 |
| --- | --- | --- | --- |
| 掷骰前的声明，按回合记账 | Reckless Attack | `_attack_declaration_state` / `_attack_declaration_events`；账本 `combat["attack_rolls"]` | `condition.applied{reckless, attacker_next_turn_start}` → `dnd2024.attack_roll.recorded` → `check.resolved` |
| 放弃优势，但来源留痕 | Brutal Strike | `_attack_check_mode` 之后把 mode 清空、sources 保留 | `check.resolved{brutal_strike: true, advantage_mode: "", advantage_sources: [...]}` 只掷一颗 d20 |
| 目标绑定的持续状态 | 四个 Brutal 效果 | 条件（`hamstrung` / `next_save_disadvantage` / `reaction_blocked` / `sundered`）+ 位置事件 | `condition.applied` / `position.changed{forced}` / `movement.granted{brutal_forceful}` |
| 攻击者→目标的标记 | Studied Attacks | `combat["attack_marks"]["studied"][attacker][target]` | 掷骰前 `attack_mark.cleared`，未中后 `attack_mark.set` |

四种后果对应四种不同的失败模式，混在一起就会立刻出错：

- **声明**若事后追认，客户端可以在看到骰面之后再拿优势。窗口由账本判定，不由条件判定：一个没声明 Reckless 的普通第一次攻击也必须关掉窗口，否则「第二次攻击还能补声明」。
- **放弃**若只删 Reckless 而保留其他 Advantage，Brutal 就成了「优势照拿、额外伤害照吃」。所以 `sources` 全留、`mode` 清空，单骰是唯一形态。
- **持续状态**若写成攻击者身上的 flag，「另一个生物对目标的下一攻击 +5」就没有可读的地方；反过来，若写成受益者特性，受益者是「另一个生物」，按定义没有 Improved Brutal Strike。
- **标记**若在掷骰后建，「一次攻击同时消费并受益于自己刚产生的效果」就会成立。

验证：新增 **27 条用例（28 个测试项，面板契约用例参数化两条）**；聚焦回归 **215 passed**；全量 `tests/rulesets` **见 §6**；`ruff` 干净；`mypy` 102 文件 0 错。

## 2. 五个先决条件（本切片真正的成本）

任务书假设「五项声明 + 现成机制」。实现时有五处缺口必须先补，它们都不在特性目录里。

### 2.1 Reckless 的"本回合第一次掷骰"没有地方存

条件注册表能表达"我有一个条件"，表达不了"这是我本回合的第一次攻击掷骰"。`condition.applied(reckless)` 落下去之后，第二次攻击看到的只有"我已经鲁莽了"，看不到"窗口已经关了"，而两者的拒绝理由并不相同（前者是重复声明，后者是事后声明）。

补法：新增 `dnd2024.attack_roll.recorded` 事件与 `combat["attack_rolls"][actor_id] = {round, turn_index, count}`。跨回合不显式清零——回合标识变了就等于归零，与 `feature_usage` 的记账方式一致。记账范围是**本回合的攻击掷骰**：反应里打出的攻击（借机攻击、Retaliation）既开不了也关不了这个窗口，记下来只会让每个既有反应攻击多出一个与规则无关的事件（§6 的 Retaliation 回归就是这么发现的）。

### 2.2 Brutal 的资格需要"存在 Disadvantage"，而 `attack_mode` 只给相消后的结果

`mode_from_sources` 会把一份优势与一份劣势抵消成 `""`。如果 Brutal 读 `mode`，「劣势被另一份优势抵消」就变成了通行证——这正是任务书停止条件 §7.2 的字面描述。所以 `_validate_attack_state_options` 读的是 `check_mode["sources"]` 里**有没有任何一条 disadvantage**，相消不算。

### 2.3 17 级的 2d10 与"两个效果"不能是第二条声明

成长表在 13 级与 17 级各授一次 `improved_brutal_strike`（`progression_catalogs/srd_classes.json` 第 53、54 行）。若照抄成两条声明，同一 `(owner, id)` 会撞 `duplicate class feature identity`，第二条永远授不上。因此 17 级是 `brutal_strike` 上的三个字段：`damage_dice_upgrade`、`upgrade_level`、`effects_per_use_upgrade`，运行时按职业等级读（`_brutal_strike_dice`）。

### 2.4 Forceful 的"半速跟随"在一维位置下是"英尺预算 + 方向"

"推 15 尺"是 `position.changed{forced}`；"随后最多半速直线朝它移动且不引发借机攻击"不是一次瞬移，而是一份额度：`movement.granted{reason: brutal_forceful, amount: 半速, target_id}` → `economy["brutal_move"]`，玩家仍要提交 `move`。免借机攻击的条件是**方向朝向被推开的目标且不超额度**，与暴击后移动（`crit_move`）复用同一形状。方向不按「有没有那段代码」判定，按位置符号判定：`(distance > 0) == (toward > 0)`。

### 2.5 Studied 的"至你下回合结束"比回合开始到期多活一个回合

标记的 `turns_remaining` 计数的是"还剩几次回合开始"：建时为 1，第一次回合开始递减到 0 仍然可用，第二次回合开始才删除。若按普通回合开始到期处理，标记会在自己的下个回合刚准备用的时候就消失。

## 3. 逐项机械

### 3.1 Reckless Attack 与 Frenzy

- **Reckless**（2 级）：`self_condition: reckless` + `condition_duration: attacker_next_turn_start`。声明本身不限攻击属性（规则只把 Advantage *给予*力量掷骰），优势由代码在 `_attack_check_mode` 里按声明的 `ability` 加一条来源；因此敏捷野蛮人在敏捷攻击上声明 Reckless 不会白拿优势，但**针对他的攻击仍然有优势**——那一侧走条件注册表 `reckless: {attack_modes: [{side: target, mode: advantage}]}`。
- **Frenzy**（狂战士 3）：判据读的是**本次检查的来源列表**是否含 `reckless`，不是读 actor 身上的条件——同一批事件里的 `condition.applied(reckless)` 在结算时还没落进 actor view。Rage 判据则读 actor view 的 conditions（`_feature_character` 是特性投影，`conditions` 为空，读它会永远不触发）。骰数取 `dice_count`（由 `dice_count_track: rage_damage` 在投影里算好：3 级 2d6、9 级 3d6、17 级 4d6），类型随本次攻击。

### 3.2 Brutal Strike（9 级，17 级升级）

- 声明前校验：必须本人回合、本回合已声明 Reckless、力量攻击、本回合未用、且**没有任何 Disadvantage 来源**。四条各自有独立文案，都走 `CombatIntentError`，不会静默降级成普通攻击。
- 掷骰：`check_mode` 的 `mode` 清空、`sources` 保留；`check.resolved` 带 `brutal_strike: true`。记账（`dnd2024.feature.used`）发生在**掷骰前**，所以未命中同样花掉本回合的 Brutal，不能改投。
- 伤害：`1d10`（9–16 级）/ `2d10`（17 级起），与 Frenzy 各自成一个 `resource.changed` 实例，暴击时各自翻倍（`_hit_rider_events` 用同一个 `critical` 标志）。骰序固定为 武器 → Frenzy → Brutal。

### 3.3 四个效果

- **Forceful**：`position.changed{distance: ±15, forced: true}`，方向按目标与攻击者的位置符号。随后 `movement.granted` 授予半速额度（40 尺速度 → 20）。
- **Hamstring**：新条件 `hamstrung: {speed_modifier: -15}`，`attacker_next_turn_start` 到期。同一目标只挂一份 → 「只保留最新、不叠成 -30」由数据形状保证，不需要代码去重。
- **Staggering**：拆两个条件。`next_save_disadvantage` **不带 duration**（"它进行的下一次豁免"，只有真掷过才消费），`reaction_blocked` 按 `attacker_next_turn_start` 到期。豁免消费点在 `_consume_save_markers`，被三处豁免路径调用（法术豁免、豁免重复、Relentless Rage 收尾），所以"豁免已消费"不会提前恢复 OA。
- **Sundering**：条件 `sundered` 带 `attack_bonus: 5`。消费点在掷骰侧：`_sundering_bonus(target, actor_id)` 读**目标身上的条件**，跳过 `source_actor_id == actor_id` 的那一份，命中与否都 `condition.removed`。受益者不需要拥有任何特性。

### 3.4 Studied Attacks（战士 13）

未命中后建 `attack_marks.studied[attacker][target] = {turns_remaining: 1, expiry, round, turn_index}`；下一次对该目标的攻击在**掷骰前** `attack_mark.cleared` 并加一条 `studied_attacks` 优势来源；若这次又未命中，在掷骰之后再建新标记——顺序由事件列表本身可验证（`cleared` < `check.resolved` < `set`）。标记按目标分开存放，不转移、不全局覆盖。

## 4. 白名单对照

| 白名单项 | 是否改动 | 内容 |
| --- | --- | --- |
| canonical class feature catalog | 是 | 5 条声明（含 17 级升级字段）+ 4 个 blow 名称标签 |
| en / zh-CN locale | 是 | 只新增 `forceful_blow` / `hamstring_blow` / `staggering_blow` / `sundering_blow`；五项特性标签基线已有 |
| `combat/check_modes.py` | **否** | `attack_sources` 已是数据驱动，`reckless` 作为条件目录数据即可生效 |
| `combat/resolution.py` | 是 | 声明状态/事件、检查模式合并、四种效果、Studied 标记、Sundering 消费；`attack_ability` / `attack_range_disadvantage` 提为共用函数 |
| `combat/validation.py` | 是 | `_validate_attack_state_options`、`_validate_brutal_strike_effects`、`_attack_roll_count` |
| `combat/reducer.py` | 是 | 三种新事件 + `brutal_move` 额度 + `attack_rolls`/`attack_marks` 初始化 |
| `combat/engine.py` | 是 | `_attack_state_options`（面板）、`_expire_attack_marks`（到期） |
| `combat/runtime.py` | **否** | 没有新增入口 |
| `features/resolver.py` | 是 | `_ATTACK_STATE_PARAMETER_KEYS` 与 `_validate_attack_state_parameters`（双向严格：未知键、缺键都报错） |
| `combat_catalogs/srd_combat_core.json` | 是 | 两个持久状态 `reckless`、`hamstrung` |
| `tests/rulesets/test_dnd2024_attack_state.py` | 新建 | 27 条 / 28 项 |
| 执行报告 | 追加 | 本文件 |

白名单外无改动。

## 5. 测试矩阵

新增 `tests/rulesets/test_dnd2024_attack_state.py`，27 条用例 / 28 个测试项，逐项对应任务书 §6：

| 项 | 测试 | 关键断言 |
| --- | --- | --- |
| 1 | `test_the_five_declarations_are_granted_where_the_srd_puts_them` | 1/2/3/8/9/12/13 级边界、`source_ref`、13 级四选项与 17 级升级字段、双语标签 |
| 2 | `test_reckless_is_only_declared_before_the_first_attack_roll_of_the_turn` | 首次可用；重复声明、普通首攻之后、他人回合三个拒绝文案 |
| 3 | `test_reckless_grants_advantage_to_strength_rolls_and_against_itself` | 自身力量优势、敏捷攻击无自身优势、敌方攻击有优势、下回合开始消失 |
| 4 | `test_reckless_and_a_second_disadvantage_cancel_into_one_d20` | 两份来源相消、只掷一颗、到期后只剩劣势 |
| 5 | `test_frenzy_waits_for_the_first_strength_hit_after_reckless` / `test_frenzy_pays_only_once_per_turn` | 未命中不消费；同回合第二次命中不再触发 |
| 6 | `test_frenzy_needs_rage_a_strength_attack_and_the_declaration` | 无 Rage / 敏捷攻击不触发；9 级 3d6、17 级 4d6，类型随武器 |
| 7 | `test_brutal_strike_needs_reckless_a_strength_attack_and_a_free_budget` | 无 Reckless、非力量、同回合第二次三条拒绝 + 面板可用性 |
| 8 | `test_brutal_strike_refuses_any_disadvantage_and_rolls_one_die` | 相消仍拒绝；合法时 `brutal_strike: true`、`mode: ""`、单骰、来源留痕 |
| 9 | `test_brutal_strike_pays_one_d10_until_seventeen_and_two_after` | 13 级 1d10、17 级 2d10、类型；未命中无伤害/效果且本回合额度已花 |
| 10 | `test_frenzy_and_brutal_strike_settle_as_separate_instances` | 声明事件顺序、骰序（武器→Frenzy→Brutal）、三个独立伤害实例、暴击各自翻倍 |
| 11 | `test_brutal_options_unlock_at_thirteen_and_double_at_seventeen` | 9/13/17 级选项集与上限；不选效果仍有额外伤害；17 级两个不同效果各自落地 |
| 12 | `test_illegal_brutal_option_selections_leave_no_partial_state` | 低等级选新效果、13 级选两项、17 级三项、重复同一项 → 四条拒绝且零残留 |
| 13 | `test_forceful_blow_pushes_fifteen_feet_and_licenses_the_follow_move` / `test_forceful_follow_move_refuses_the_wrong_direction_and_extra_distance` | 推 15 尺、半速额度、朝目标走免借机攻击并消费额度；反向 15 尺与同向 25 尺都开窗 |
| 14 | `test_hamstring_reduces_speed_by_fifteen_and_keeps_only_the_latest` | 30→15、重复施加仍只有一份、攻击者下回合开始消失 |
| 15 | `test_staggering_consumes_the_next_save_and_blocks_reactions_separately` / `test_staggering_also_denies_opportunity_attacks` | 下一次豁免劣势来源、消费后 `reaction_blocked` 仍在、到期消失；对照：未震慑时同一移动开借机攻击窗 |
| 16 | `test_sundering_helps_only_another_creatures_next_attack` | 攻击者本人不吃 +5 且条件保留；盟友 +5 后消费；`total = natural + modifier + 5` |
| 17 | `test_one_sundering_bonus_per_attack_and_the_mark_expires` / `test_the_declared_state_survives_a_save_and_a_replay` | 条件按 id 唯一（不可能 +10）；存档往返后条件、账本、额度一致；重放是 duplicate |
| 18 | `test_studied_attacks_only_helps_against_the_creature_it_studied` | 对另一目标无效、对标记目标有优势 |
| 19 | `test_a_studied_mark_is_spent_by_the_roll_and_never_by_its_own_creation` | `cleared` < `check.resolved` < `set`；再次未中才建新标记 |
| 20 | `test_studied_marks_age_out_after_the_following_turn` | 非攻击动作不消费；第一次回合开始降为 0 仍可用；第二次删除 |
| 21 | `test_stale_versions_forged_fields_and_replays_fail_closed` | 过期 version、伪造 `advantage_mode`/`damage`、效果不带声明均拒绝；重放不二次记账 |
| 22 | `test_a_plain_attack_records_nothing_this_slice_owns` + §6 的聚焦回归 | 无声明时零账本、零标记、零新字段 |

另有一条面板契约用例 `test_the_panel_reports_the_options_the_server_enforces`（9/13 级选项集 + 用完后关窗）。

## 6. 回归

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增用例 | `pytest tests/rulesets/test_dnd2024_attack_state.py -q` | 28 passed（27 条用例） |
| 聚焦回归 | `rage / hit_riders / sneak_attack / combat / class_features` | 215 passed |
| 全量 | `pytest tests/rulesets -q -n 6 --dist loadfile` | 11 failed, 1995 passed（8:09）|
| 静态检查 | `ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features/resolver.py tests/rulesets/test_dnd2024_attack_state.py` | All checks passed |
| 类型检查 | `mypy`（102 文件） | 0 errors |
| 空白 | `git diff --check` | 干净 |

全量在本机（Windows）有 **11 条常驻失败**，全部是 `test_dnd2024_character_builder.py`：`unresolved reference 'ability:con' in background:soldier`，与基线一致，与本切片无关。

**只看门禁才现形的两处缺陷**（都不是单测能发现的）：

1. `condition.applied(reckless)` 起初没有 `source_actor_id`。`attacker_next_turn_start` 的到期逻辑靠"这个字段等于刚开始回合的人"来匹配，缺了它 Reckless 就**永不到期**——一次声明，整场战斗都在优势。所有新用例当时都是绿的，是"到期后来源消失"这一条断言把它逼出来的。
2. `track_roll` 起初对任何带 Reckless 的角色无条件置真，于是 Retaliation（14 级野蛮人的反应反击）多出一个 `dnd2024.attack_roll.recorded`，既有用例的事件序列断言立刻变红。改为只在本回合记账后，Retaliation 恢复原样（`test_dnd2024_retaliation.py` 14 passed）。

## 7. 偏差 / 未决

1. **分支**：按任务书建议名另起 `feat/dnd2024-s0f15-attack-state`，但从 F14 分支 HEAD 起（栈式），F14 若未合入则本分支的 PR 标题需注明叠在其上。未推送、未开 PR（项目规则要求先获批准）。
2. **`check_modes.py` 与 `runtime.py` 未改**。两者都在白名单里，但本片没有需要它们的地方：前者已是数据驱动（`reckless` 走条件注册表），后者没有新增入口。白名单是许可，不是义务。
3. **Reckless 的"本人回合"守卫在普通攻击路径上不可达**。通用回合门（`it is not this actor's turn`）先拒绝，所以测试覆盖的是那条通用文案；本片自己的那条守卫只有反应授予的攻击（借机攻击）能触达，本片没有构造借机攻击用例。
4. **相消用的劣势来源是"未受训护甲"派生标记**，不是条件。目录里所有条件型劣势都需要外部施加（倒地、中毒），而本片没有能给自己挂条件的特性。护甲的派生属于护甲切片契约（`test_dnd2024_armor_training.py` 已覆盖推导），这里只写派生字段、只消费它。因此"到期后两类来源同时消失"退化为"Reckless 半边消失、mode 翻转成劣势"——测试里写明了这一点，没有假装两类都到期。
5. **Staggering 的"下一次豁免"由盟友牧师的真实施法触发**（`spell:sacred_flame`，魅力/感知豁免 DC 走真实法术链），没有合成 `dnd2024.saving_throw` 事件。多角色测试因此需要 `end_turn` 轮转与显式先攻顺序；`_combat(initiative={...})` 按 uid 指定点数，避免"排序靠前的人先动"这种隐形依赖。
6. **Forceful 的"不引发借机攻击"必须存在第二只相邻敌人才能观测**。一维位置无法表达"从我身边走开"，所以用 `goblin-2` 站在身后当威胁源；没有它，任何方向与额度限制都测不出来（没有威胁就永远没有窗，正向与反向都是绿的）。
7. **`brutal_strike_effects` 不带 `brutal_strike: true` 提交，现在直接拒绝**。原本会被静默忽略——客户端以为要来一次推击，实际是一次普通攻击。这条是 fail closed 的补强，不是任务书要求。
8. **静态标签去重**：`reckless_attack` / `frenzy` / `brutal_strike` / `improved_brutal_strike` / `studied_attacks` 五个标签在基线目录里已经有（en + zh-CN），本片只新增四个 blow 名称。初稿曾重复添加，已删除并归位到字母序位置。

## 8. 后续

- 野蛮人剩下的大件（Retaliation 的触发面、Persistent Rage、Indomitable Might）与战士的 Tactical Master 等仍按主计划排在 F16 之后。
- `attack_rolls` 这份账本现在是通用能力：任何"本回合第 N 次攻击掷骰"的判定都可以直接读它，不需要再各写一份计数。
- Studied Attacks 的标记层（`attack_marks`）已经按 kind 分桶，Sneak Attack 的"每回合一次"若将来需要"同一目标"的粒度，可以复用同一结构而不是新开字段。
