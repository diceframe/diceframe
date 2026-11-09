# S0-F2 任务书：激励骰实例与 Bardic 掷后修正

| 项 | 值 |
|---|---|
| 切片编号 | S0-F2（独立新任务，不覆盖旧 SP） |
| 验收载体 | `bardic_inspiration`、`font_of_inspiration`、`superior_inspiration`、`peerless_skill`；补全 `cutting_words` |
| 通用能力 | 激励骰实例、逻辑时间到期、失败 D20 修正、反应减骰、资源互换与退款 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Bard p31–33；College of Lore p35；伤害修饰/抗性/易伤顺序 p17 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-B、S0-D2、S0-D3、S0-F1 已合入；D3 攻击面窗口保持可用 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得覆盖既有工作或重写 D3 为另一套窗口。
2. 区分“Bard 的可授予次数”“目标持有的激励骰实例”“Peerless/Cutting Words 直接消耗的次数”，三者不得共用错误扣账。
3. 所有距离、动作、反应、骰值、资源与伤害由服务端裁决；客户端只能选择合法窗口，不提交结果。
4. 一小时必须使用持久化逻辑时钟，不使用系统时间、后台线程或不可重放计时器。
5. D3 前置提供 Cutting Words 攻击面窗口；本任务在同一窗口主干上扩展属性检定与伤害面，不另造平行管线，也不假定 Dark Luck 已经实现。

## 2. 官方英文关键原文与语义边界

SRD p31–32：

> As a Bonus Action, you can inspire another creature within 60 feet of yourself who can see or hear you. That creature gains one of your Bardic Inspiration dice. A creature can have only one Bardic Inspiration die at a time.

> Once within the next hour when the creature fails a D20 Test, the creature can roll the Bardic Inspiration die and add the number rolled to the d20, potentially turning the failure into a success. A Bardic Inspiration die is expended when it’s rolled.

> You can confer a Bardic Inspiration die a number of times equal to your Charisma modifier (minimum of once) ... The die becomes a d8 at level 5, a d10 at level 10, and a d12 at level 15.

SRD p32–33：

> **Font of Inspiration.** You now regain all your expended uses of Bardic Inspiration when you finish a Short or Long Rest.

> In addition, you can expend a spell slot (no action required) to regain one expended use of Bardic Inspiration.

> **Superior Inspiration.** When you roll Initiative, you regain expended uses of Bardic Inspiration until you have two if you have fewer than that.

SRD p35：

> **Cutting Words.** When a creature that you can see within 60 feet of yourself makes a damage roll or succeeds on an ability check or attack roll, you can take a Reaction to expend one use of your Bardic Inspiration; roll your Bardic Inspiration die, and subtract the number rolled from the creature’s roll, reducing the damage or potentially turning the success into a failure.

> **Peerless Skill.** When you make an ability check or attack roll and fail, you can expend one use of Bardic Inspiration; roll the Bardic Inspiration die, and add the number rolled to the d20 ... On a failure, the Bardic Inspiration isn’t expended.

SRD p17：

> Modifiers to damage are applied in the following order: adjustments such as bonuses, penalties, or multipliers are applied first; Resistance is applied second; and Vulnerability is applied third.

语义边界：Peerless 的退款针对“次数”而非另造激励骰；Cutting Words 的 2024 文本没有 2014 版听觉或 Charmed 免疫前提，不得擅自添加。伤害减骰发生在抗性/易伤之前，后者仍是最后的伤害修饰步骤。

## 3. 已定点核实的代码事实

| 基线与前置事实 | 文件与函数 |
|---|---|
| D3 前置新建统一攻击掷后窗口；本任务复用其真实注册、验证、消费与结束接口，不预设未合入的函数名 | D3 的 `combat/check_windows.py`；祖先基线没有该文件 |
| D3 只验收 `cutting_words` 攻击面；属性检定与伤害面由本切片扩展，不能宣称它们已在基线注册 | D3 任务书 §4/§6 与实际合入代码 |
| 伤害提交与抗性有真实公共入口，Cutting Words 必须在抗性/易伤前修改伤害并保持事件分项 | `combat/resolution.py::_attack_events`、`_resist_damage`、`_submit_hit_damage` |
| 先攻已有实际掷骰入口，Superior 必须挂接该事件而非角色静态值 | `combat/resolution.py::_start_combat_event` |
| 法术位/职业资源免费施法已有资源函数，但 Font 的“槽换次数”不是既有免费施法 | `combat/triggered_spells.py::free_cast_resource` |
| 短休/长休统一恢复职业资源和法术位 | `resting/engine.py::complete_short_rest`、`complete_long_rest`、`_recover_class_resources` |
| 权威世界状态使用 `clock: {day, minute}`，可转换绝对逻辑分钟 | `src/engine/world_state.py::clock_to_minutes`、`world_clock` |
| 时间只由权威流程推进，无后台 tick；到期事件具有确定性顺序 | `src/engine/world_events.py::advance_world_time` |

## 4. 精确数据与行为契约

### 4.1 激励骰实例与资源

1. Bard 1 级获得 `bardic_inspiration`；最大授予次数为 `max(1, Charisma modifier)`。每次授予消耗一次，Bonus Action、另一生物、60 尺及“能看见或听见 Bard”在结算时重验。
2. 新增持久化实例字段至少含 `die_instance_id`、持有者、来源 Bard、`die_sides`、`granted_at_minute`、`expires_at_minute=granted+60`、状态；骰面在授予时固定为 1/5/10/15 级的 6/8/10/12。
3. 同一目标至多一个未消费且未到期的 Bardic Inspiration die；已有实例时再次授予应拒绝且不扣次数，不得静默覆盖旧骰。
4. 仅在 `current_logical_minutes < expires_at_minute` 时可使用；恰好到期或跨过到期均不可。过期实例在下一次权威写入清理，读取/视图按同一谓词隐藏，不需要后台任务。
5. 持有者在一小时内的 D20 Test 失败后才可选择掷该骰；将骰值加到现有 d20 总值，重判成功/失败。无论最终是否仍失败，该实例都因“rolled”而消费。
6. 休息不会额外延长或刷新已授予实例；升级不会 retroactively 改变旧实例骰面。资源、实例、事件必须在同一批次原子提交。

### 4.2 Font、Superior 与 Peerless

1. Font of Inspiration 5 级后，Short Rest 与 Long Rest 都恢复全部 Bardic Inspiration 授予次数；低等级仍只由 Long Rest 恢复。
2. Font 另允许无动作消耗一个真实法术位恢复一次已消耗次数；法术位合法、存在已消耗次数且不会超过上限时才可提交，两步原子生效，不能恢复为超过 Cha 上限。
3. Superior Inspiration 18 级：每次实际 roll Initiative 时，若当前次数少于 2，恢复到恰好 2；已有 2 次或更多不增加、不溢出。仅战斗开始的真实先攻触发一次。
4. Peerless Skill 14 级仅用于持有者自己的 ability check 或 attack roll 失败；不要求目标持有授予实例，直接消耗 Bard 的一次资源并掷当前 Bardic die 加值。
5. Peerless 若修正后成功则消费次数；若仍失败则次数不消费。骰已真实掷出并记录，但资源恢复与后续效果必须在同一事务中正确处理；不得先扣后忘退或失败也扣。

### 4.3 Cutting Words 三个触发面

1. 复用 D3 的 `cutting_words_check` 攻击面，并扩展 ability check 成功面；触发者必须是 Lore Bard 能看见且 60 尺内的生物，消耗 Reaction 与一次 Bardic Inspiration，掷当前 Bardic die 并从成功总值扣除后重判。
2. 属性检定或攻击即使减骰后仍成功，也已消耗 Reaction 与次数；无次数、Reaction 已用、超距或不可见均不提供窗口。
3. `cutting_words_damage` 只在实际 damage roll 后开放；没有伤害骰的固定伤害不伪造触发。减骰后总伤害最低 0，再进入既有抗性、易伤及 HP 结算。
4. 伤害事件保留原始各骰与修正、Cutting Words 骰、减后小计、抗性/易伤和最终 HP 变化；不得出现抗性后扣减、负数治疗或只改展示不改 HP。
5. 同一触发只能消费一个窗口决策；窗口取消/校验失败不扣资源，成功后清除 pending，存档/重放不得再次消费。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`rest_catalogs/srd_recovery.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/features/models.py`、`features/combat.py`、`features/resolver.py`。
3. `src/rulesets/dnd2024/combat/check_windows.py`、`resolution.py`、`validation.py`、`reducer.py`、`view.py`、`engine.py`。
4. `src/rulesets/dnd2024/resting/engine.py`、`progression/advancement.py`。
5. 新建 `tests/rulesets/test_dnd2024_bardic_inspiration.py`；可追加 `tests/rulesets/test_dnd2024_cutting_words.py` 的窗口回归。
6. 不修改通用 `src/engine/world_state.py` 或 `world_events.py`；若无法从现有权威上下文读取逻辑时钟则停止。

## 6. 编号测试矩阵

1. Bard 1/5/10/15 的激励骰分别为 d6/d8/d10/d12，次数为 `max(1, Cha)`，错误职业/等级不可用。
2. 授予只以 Bonus Action 对 60 尺内能看见或听见 Bard 的另一生物生效，并准确扣一次。
3. 自己、超距、无视觉且听不见、动作/次数不足均拒绝且不产生实例。
4. 同一目标已有有效骰时第二次授予拒绝；不同目标可各持一个，来源与骰迹独立。
5. 实例保存授予/到期逻辑分钟和固定骰面；存档/加载及升级不重置期限或骰面。
6. 59 分钟可使用、60 分钟及跨时推进后不可使用；无现实时间或后台 tick 依赖。
7. 持有者只在失败 D20 Test 后可用激励骰，成功时不能主动消费。
8. 加骰转成功与仍失败两种结果都消费实例，骰值进入总值且只能使用一次。
9. 激励骰不用于伤害骰或非法自定义 roll；取消窗口不消费。
10. 5 级前短休不恢复授予次数，长休恢复；5 级后短休和长休均恢复全部。
11. Font 无动作消耗一个合法法术位恢复恰一次；无槽、满次数或重复提交不产生部分扣账。
12. Superior 在实际先攻时把 0/1 次恢复至 2；2 次及以上不变，未掷先攻不触发。
13. Peerless 仅用于 Lore 14 自己的失败 ability check/attack roll，不用于他人、豁免或伤害。
14. Peerless 转成功扣一次，仍失败不扣；两条路径均有真实骰迹且无重复退款。
15. Peerless 不要求已有授予实例，也不得误删持有者或其他人的激励骰。
16. Cutting Words 攻击成功面保持 D3 行为，减骰可转失败，Reaction/次数/60 尺/可见性正确。
17. Cutting Words 属性检定成功面可转失败；仍成功也扣资源，失败检定本来不开放窗口。
18. Cutting Words 只在实际 damage roll 后开放，固定无骰伤害、攻击命中但无伤害掷骰不触发。
19. 伤害减骰先于 Resistance/Vulnerability，最低 0；最终 HP 与事件分项一致。
20. Cutting Words 不沿用 2014 版听觉或 Charmed 免疫限制；2024 的可见与距离条件仍严格执行。
21. 无次数、无 Reaction、伪造窗口或重复决策均 fail closed，状态不变。
22. 多个合法掷后特性按窗口顺序结算；同一 d20 不重复消费同一窗口。
23. 长休同时恢复既有资源，不错误刷新未授予实例或临时改变 Bardic die 上限。
24. 存档/加载、重放与事件回放后实例到期、资源、Reaction 和 HP 完全一致。
25. 既有 D3、休息、伤害与先攻测试无回归；Dark Luck 的新增交互由后续 F3 验收。

## 7. 非目标与停止条件

### 非目标

- 其他 Bard 子职、Words of Creation、通用音效或表演 UI。
- 2014 版 Cutting Words 免疫规则、任意伤害减免窗口或客户端自选骰值。
- 用永久资源标记代替一小时实例，或只实现攻击面而遗漏属性检定/伤害面。

### 停止条件

1. S0-B/D2/D3/F1 未合入，或 D3 窗口不能被扩展而不复制管线。
2. 权威流程无法提供可重放逻辑分钟，或需要后台计时器才能判定一小时。
3. 伤害管线无法在抗性/易伤前减骰并保持事件与 HP 一致。
4. 需要白名单外文件、前端私有资源逻辑或通用引擎规则泄漏。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_bardic_inspiration.py tests/rulesets/test_dnd2024_cutting_words.py -q
python -m pytest tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_spells.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/combat src/rulesets/dnd2024/resting src/rulesets/dnd2024/progression tests/rulesets/test_dnd2024_bardic_inspiration.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 激励实例、单骰限制、逻辑时钟到期：
- Font/Superior 资源恢复与槽转换：
- Peerless 成败扣账证据：
- Cutting Words 攻击/属性/伤害三面：
- 抗性顺序、骰迹、重放与原子性：
- 白名单文件对照：
- 测试 1–25 / 聚焦 / 回归 / 全量 / ruff / diff：
- 偏差、停止原因与未决事项：

## 10. 执行报告（2026-11-09）

- **实际基线**：`5efb0194`（`feat/dnd2024-s0f1-d20-skills` 链头；S0-B 的选择槽、D3 的掷后窗口主干、F1 的 `derive_skill_check()` 均在链内）
- **分支 / 提交**：`feat/dnd2024-s0f2-bardic-inspiration`
  1. `36c2ac0a` 声明：`bardic_inspiration`(1)、`font_of_inspiration`(5)、`peerless_skill`(14)、`superior_inspiration`(18)；`CHECK_WINDOW_TRIGGERS` 扩为 `{attack_roll_success, grant, own_check_failure}`，`ACTION_KINDS` 增 `bardic_grant`／`inspiration_slot_exchange`
  2. `6f53dcb7` 授予、实例与失手窗口
  3. `fc779f06` 属性检定面（Hide）
  4. `5530d64c` Font 的槽换次数与 Superior 的先攻补足
  5. `ebd6166c` 授予的校验前置、失败面排队、25 条矩阵测试
- **改动文件**：`combat/check_windows.py`、`combat/resolution.py`、`combat/validation.py`、`combat/reducer.py`、`combat/engine.py`、`features/models.py`、`content/class_feature_catalogs/srd_class_features.json`、新建 `tests/rulesets/test_dnd2024_bardic_inspiration.py`

### 实例是 combat 状态，不是第二张资源表

`combat["bardic_instances"]` 只有一份，字段 `die_instance_id`／`holder_id`／`source_actor_id`／`die_sides`／`granted_at_minute`／`expires_at_minute`／`status`。三类账目因此天然分开：Bard 的**可授予次数**仍是 `resources.class.bardic_inspiration`（`class_resource.spent`／`refunded`），目标的**持有**是实例（`bardic.consumed` 把状态改成 `rolled`），Peerless／Cutting Words 直接吃 Bard 自己的次数。没有任何一处用"实例是否存在"去推断次数，也没有用次数去掩盖实例。

`dnd2024.bardic.granted` 由 reducer 写库时做两件事：顺手清掉已过期实例，以及重复持有 fail closed（同一持有者出现第二条 `active` 记录即拒绝整批）。`gameplay_view` 用与窗口同一个谓词（`now < expires_at_minute` 且 `status == "active"`）投影，所以过期的骰在视图里和可用性上同一刻消失，不需要后台任务。

### 一小时只认权威世界时钟

`_logical_minutes()` = `clock_to_minutes(world_clock(instance.world_state))`，`granted_at_minute` 由 resolution 算好写进事件，reducer 不读时钟——重放同一批次得到同一分钟。矩阵 6 用 `advance_world_time(instance, 59)` 与再 +1 分钟钉住边界：59 分钟仍有窗口，够 60 分钟当次即无窗口。测试全程没有 `sleep`、没有系统时间。

### 失败面：一次掷骰只重判一次，但可以排多个面

失手后 `_failed_attack_windows()` 按固定顺序返回 `bardic_inspiration` → `peerless_skill`。**这轮修掉了它只取 `codes[0]` 的缺陷**：一个 Lore 14 的诗人如果手里还攥着别人给的骰，他同时拥有两个合法失败面，原来取消第一个就把第二个静默丢了。现在 settlement 记录 `failed_offered`，收尾时若重判仍是失败就打开下一个面（矩阵 22）。

代价与取舍：同一枚 d20 上两个面依次结算意味着可能掷两颗骰加同一个总值。这是"每次失败都可以用一次特性"的直接结果（两处措辞都是 "when the creature fails a D20 Test"），且每个面各扣各的资源，没有重复消费同一份成本。事件层面每个面各自一批，各自的 `check.resolved` 保留当时的总值与该面骰迹。

`settlement["miss_face"] = True` 让未翻盘的所有路径（取消、掷了还失败）复刻直通失手的 Graze 专精伤害；这条是补齐"窗口路径与直通路径必须落在同一处"的一致性缺口。

### Font / Superior

Font 的槽换次数是 `action.kind = "inspiration_slot_exchange"`，`cost: {}`（无动作），走 `class_resource.refunded` + `dnd2024.spell.slot_spent{exchange: true}`。`exchange: true` 让 reducer 不置 `slot_spell_cast`——换次数不是施法，否则同一回合会被"已施法"卡住。前置校验要求"确有已消耗次数"且"该环法术位仍可用"，两步在同一批次里原子生效；重复提交的第二条被"no expended"挡下，不产生部分扣账（矩阵 11）。

Superior 挂在 `_start_combat_event` 的真实先攻之后：`refilled{target: 2}`，reducer 取 `max(current, min(2, maximum))`。18 级 Bard 的 Cha 上限是 2，所以"满额时不溢出"与"0/1 补到 2"用同一行代码表达（矩阵 12）。

### Peerless 的退款在同批次

修正后仍失败 → 同一批次追加 `class_resource.refunded{reason: "peerless_failure"}`，顺序是 `check.resolved` → `refunded`，不存在的中间态是"扣了没退"。骰已真实掷出并留在 `check.resolved` 的 `window_roll`／`window_bonus_rolls` 里，所以"掷了但没花"是可审计的，不是"没掷"。

### Cutting Words 三个面各归其位

攻击面沿用 D3 的 `cutting_words_check` 未改；伤害面 `cutting_words_damage` 是 D3 既有实现，抗性前减骰与最低 0 由 `test_dnd2024_check_windows.py::test_cutting_words_damage_face_reduces_committed_damage` 继续固定。本切片新增的是**属性检定成功面** `cutting_words_ability`：`surface = ability_check`，`uses_reaction = True`，只在实际 `check.resolved` 之后开口，减骰后重判。hide 的 `invisible` 只在重判成功时落，失败则不落——与直通路径一致（矩阵 17）。

### 校验期拒绝 vs 结算期重验

原来的 `_bardic_grant_events` 对"自授／超距／看不见听不到／已持有"一律 `raise CombatIntentError`。`resolve_intent` 只在 `validate_intent` 外面有 `try`，所以这些错误会**穿透成异常**而不是 `{"ok": False}`，与 `_validate_attack` 既有的"声明式前置校验 + 结算重验"模式不一致。本轮把四项事实的判定移到 `_validate_class_capability`，resolution 侧保留同样的判断作为纵深防御。合并后 `test_grant_rejects_self_out_of_range_and_unseen_targets` 用两张桌分别验证"射程"与"看不见听不到"（同一张桌上 70 尺必然先撞射程）。

### 矩阵 1–25 对照

| 编号 | 证据 | 备注 |
|---|---|---|
| 1 | `test_die_face_follows_the_bard_progression[4 档]`、`test_wrong_class_cannot_grant` | d6/d8/d10/d12，次数 = max(1, Cha) = 2 |
| 2 | `test_grant_spends_bonus_action_and_one_use` | 附赠动作 1→0、次数 2→1、实例 1 条 |
| 3 | `test_grant_rejects_self_out_of_range_and_unseen_targets` | 自授／70 尺／隐身+目盲，均不产生实例 |
| 4 | `test_second_grant_to_one_holder_is_rejected_and_others_keep_their_own` | 换回合再授 → "already holds"，次数不变 |
| 5 | `test_instance_survives_a_save_round_trip_and_level_up_keeps_the_face` | JSON 往返逐字段相等，骰面是快照 |
| 6 | `test_expiry_is_exactly_one_hour_of_logical_minutes` | 59 有窗口、60 无窗口 |
| 7–8 | `test_held_die_turns_a_miss_into_a_hit_and_is_consumed`、`test_held_die_is_consumed_even_when_the_roll_still_fails` | 掷后 `status = rolled`，两条路径都消费 |
| 9 | `test_a_hit_never_offers_the_holder_die`、`test_declining_keeps_the_die_and_matches_the_straight_through_miss` | 命中不开口；取消不消费 |
| 10 | `test_short_rest_only_restores_from_level_five` | 走 `srd_recovery.json` 的 `short_all_from_level: 5` |
| 11 | `test_font_exchanges_one_slot_for_one_use` | 槽 -1、次数 +1、未置 `slot_spell_cast` |
| 12 | `test_superior_refills_to_two_on_the_real_initiative_roll` | 0→2；满额不变 |
| 13 | `test_peerless_only_answers_the_bards_own_failed_roll` | 他人失手不开 Peerless 窗口 |
| 14 | `test_peerless_consumes_on_success_and_refunds_on_failure` | 仍失败整批退款、成功只扣一次 |
| 15 | `test_peerless_needs_no_held_die_and_never_touches_instances` | 别人的实例保持 `active` |
| 16 | `test_dnd2024_cutting_words.py`（D3 全套 30+ 条） | 攻击面未改，回归通过 |
| 17 | `test_cutting_words_can_cut_a_successful_hide`、`test_a_cut_that_still_succeeds_still_spends` | 转失败不落 invisible；仍成功也扣 Reaction 与次数 |
| 18–19 | `test_dnd2024_check_windows.py` 的 damage face 用例 | 固定伤害不开口、抗性前减骰、最低 0 |
| 20 | 同 16/17 | 2024 只保留可见与距离；无听觉/Charmed 限制 |
| 21 | `test_unknown_decisions_fail_closed_and_a_replay_never_spends_twice` | 伪造 decision 被拒，状态不变 |
| 22 | `test_a_held_die_is_offered_before_the_holder_own_pool` | 手持骰先、Peerless 后，取消不串味 |
| 23 | `test_a_long_rest_refills_the_pool_without_touching_the_live_die` | 长休补次数，实例记录不动；8 小时逻辑时间才让它消失 |
| 24 | 同 21 + 5 | 同 `intent_id` 同 `expected_version` 回放返回原批次，`apply_batch` 判重 |
| 25 | 全量 `tests/rulesets` | 净引入 0（见验收段） |

### 白名单对照（§5）

| 白名单项 | 是否改动 | 说明 |
|---|---|---|
| 1 类特性目录 + en/zh-CN locale | 是 | 4 条新声明；标签键 `bardic_inspiration`／`font_of_inspiration`／`peerless_skill`／`superior_inspiration` 早已在 locale 中存在，未新增 |
| 2 `features/models.py` | 是 | 只扩两个闭集（`CHECK_WINDOW_TRIGGERS`、`ACTION_KINDS`） |
| 3 `combat/*` | 是 | `check_windows.py` 主力、`resolution.py` 事件、`validation.py` 前置、`reducer.py` 四类事件、`engine.py` 视图一行 |
| 4 `resting/engine.py`、`progression/advancement.py` | **否** | 短休语义已由 `srd_recovery.json` 的 `short_all_from_level` 表达，无需改代码 |
| 5 新建测试 | 是 | `test_dnd2024_bardic_inspiration.py`（25 条）；未改 `test_dnd2024_cutting_words.py` |
| 6 不碰通用引擎 | 遵守 | `world_state.py`／`world_events.py` 零改动，只调用 `clock_to_minutes`／`world_clock`／`advance_world_time` |

### 验收结果

| 命令 | 结果 |
|---|---|
| `pytest test_dnd2024_bardic_inspiration.py test_dnd2024_cutting_words.py test_dnd2024_check_windows.py -q` | **112 passed** |
| `pytest test_dnd2024_resting.py test_dnd2024_spells.py -q` | **13 passed** |
| `pytest tests/rulesets -q` | **11 failed / 1654 passed** |
| `ruff check …`（白名单五项） | All checks passed |
| `git diff --check` | 无输出 |

全量失败清单与 F1 收尾记录的基线**逐条相同**（全部在 `test_dnd2024_character_builder.py`，`unresolved reference 'ability:con' in background:soldier` 一族），F2 净引入 **0**；通过数从 1629 增至 1654，正好是本切片的 25 条。

### 偏差、停止原因与未决事项

1. **豁免面未实现（已裁决的偏差）**。持有者在**法术豁免**失败后加骰重判属于同一特性的另一个面，但 `_spell_effect_events` 把"逐目标豁免 → 伤害 → 附加条件"内联在同一个循环里，要暂停等窗口就得把循环尾部（约 300 行）抽成可续算函数。本轮按裁决只交付主体，豁免面作为独立跟进切片，先做单目标。
2. **`deafened` 未建模**。工作可见/听觉判定用"隐身施予者 + 目盲承受者"这一对表达"既看不见也听不到"，因为状态词表里没有 `deafened` 的任何听觉语义。这只让判定偏保守（少拒绝），不会误放行已建模的条件。
3. **叠面时 `window_bonus_rolls` 只留最后一面**。第二个面重判时 `check["total"]` 累加正确，但 `window_bonus`／`window_bonus_rolls` 被覆盖为当前面的骰。每个面的 `check.resolved` 各在自己的批次里留痕，所以审计不受影响；如果以后要做单事件回放展示，需要改成累加并区分"本面骰"与"累计加值"。
4. **"升级不回写旧实例骰面"没有独立断言**。构造上不可变：`die_sides` 是授予时写进事件的快照，`_active_bardic_instance()` 读记录而不读 track。矩阵 5 用存档往返与快照字段固定了这一点，但没走一次真实 `apply_advancement`。
5. **`_failed_attack_window_offer` 的 `exclude` 是写入 settlement 的字符串列表**，不是集合；顺序信息用来保证"按注册表顺序给面"，重复项由调用方保证不出现（每次 offer 前先取差集）。
