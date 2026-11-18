# S0-F18 执行报告：Hunter's Mark 实例与 Ranger 高等级链

日期：2026-11-14
分支：`feat/dnd2024-s0f18-hunters-mark`（叠在 `feat/dnd2024-s0f17-cunning-strike@c0f44bcc` 之上）
基线：`c0f44bcc`（F17 停止报告）
范围：`favored_enemy`（游侠 1）、`relentless_hunter`（13）、`precise_hunter`（17）、`feral_senses`（18）、`foe_slayer`（20）与 `spell:hunter_s_mark`

---

## 1. 结论

五项特性不是五段逻辑，而是**一种形状、五个读者**：施法者自己的专注实例带 `mark_target_id`，其余五项各自只问它一个问题。

| 读者 | 落点 | 事件证据 |
| --- | --- | --- |
| Favored Enemy | 既有授权机制 `granted_spell_id` + `resource_id`；次数取既有 `favored_enemy` 轨道 | `dnd2024.action.spent{bonus_action}` → `dnd2024.class_resource.spent{favored_enemy, amount: 1}` → `dnd2024.concentration.started{mark_target_id, mark_expires_hours}` → `dnd2024.spell.cast` |
| Hunter's Mark 本体 | `concentration.mark_target_id` / `mark_expires_hours`；转移走 `dnd2024.concentration.retargeted` | 同上 + `retargeted{target_id, previous_target_id}` |
| Relentless Hunter | `_concentration_events` 开头按 `spell_ref` 短路 | **没有** `check.resolved{kind: concentration}`，也**没有** `concentration.ended` |
| Precise Hunter | `check_modes.mark_attack_sources()`：读攻击者自己的实例 | `check.resolved{advantage_sources: [{code: precise_hunter}]}` |
| Feral Senses | `observer_senses(engine, observer, distance=...)`：声明与距离分离 | `check.resolved.rolls` 的骰数；`available_intents.cast_spell.senses` |
| Foe Slayer | `_mark_damage_die()`：只换骰面，不换实例 | `resource.changed{feature_id: hunter_s_mark, rolls: [9]}` |

四条不变量由数据形状保证，而不是靠约定：

1. **一个施法者同时只有一个标记** —— `mark_target_id` 写在 `concentration` 这一个键里，第二个标记必须先覆盖它；
2. **另一个 Ranger 不共享** —— 读的是攻击者自己的 `concentration`，不是目标身上的条件；
3. **盟友的攻击读不到** —— 同上，盟友没有这个实例；
4. **转移不重新施法** —— 复用同一实例，`spell_ref` 与剩余时长都不动。

验证：新增 **24 条用例**（任务书 §6 的 24 项一项一条）；聚焦回归 **141 passed**；全量 `tests/rulesets` **2019 passed / 11 failed（全部是 Windows 本地既有基线，已在 §6.0 用 stash 对照证明）**；`ruff` 干净；`mypy` 102 文件 0 错；`git diff --check` 干净。

## 2. 六个先决条件（本切片真正的成本）

任务书假设「五项声明 + 现成机制」。实际有六处缺口，都不在特性目录里。

### 2.1 战斗目录的法术 mode 是闭集，而 `combat/catalog.py` 不在白名单

初版把 `hunter_s_mark` 声明成 `"mode": "mark"`，`Dnd2024CombatCatalog.from_bundle` 立刻抛 `CombatCatalogError: combat spell mode is invalid: hunter_s_mark`。这不是可以顺手放宽的校验：`SPELL_MODES` 是结算分支的契约。

**补法**：复用既有 `"mode": "debuff"`（标记确实不造成伤害、不施加条件），另加一条声明式 `"marks_target": true`。于是「这个法术的实例带唯一标记目标」成为**内容声明**，代码里所有判定都读 `effect.get("marks_target")`，没有新增 mode，也没有为它开分支。

### 2.2 「当前标记」原本没有容器

条件注册表能表达「我身上有 `marked`」，表达不了「**谁**标记了**谁**、这个标记属于**哪个施法者**」。写成目标身上的条件，第一个失败就是两名 Ranger 标同一只生物共享一骰——任务书 §1.3 明确排除的写法。

**补法**：写进施法者**自己的专注实例**，与 `spell_ref`、`target_ids` 同层，三个新字段：`mark_target_id`、`mark_expires_hours`（+ `target_ids` 同步）。写入点只有一个：`dnd2024.concentration.started` 里由 `effect.get("marks_target")` 门控。读入点也只有一个：`check_modes.owned_mark(actor)`。

### 2.3 感官是范围，不是布尔

任务书 §4.2.4 要求「actor view 增加来源化 `blindsight: 30`」，而 `combat/view.py` 不在白名单。更要紧的是：**感官一旦写进任一方身上，60 尺也就「看得见」**——`attack_sources` 按 `negated_by` 判定的正是这种布尔抵消。

**补法**（`check_modes.py`）：

- `sense_declarations()` 只回答「这个生物有哪几种感官、范围多少」，与距离无关；
- `observer_senses(..., distance=)` 回答「此刻这个距离上它在不在范围内」；
- `vision_codes()` 把「条件」与「派生感官」合成一个闭集，再交给注册表的 `negated_by` 匹配。

注册表因此仍是「什么能抵消这个条件」的唯一出处，代码只回答「观察者此刻有没有」。`attack_mode` 每次按 `combat["positions"]` 求距离，所以 30 与 31 尺得到不同答案，而同一份声明同时服务于攻击模式与法术可见性（`_spell_target_visible`）。

`blindsight` 只加进 `blurred` 与 `invisible` 的 `negated_by`。`blurred` 里其实**早就写着** `blindsight`——一直没有东西能产生这个代码；`invisible` 那两条是本切片补的。`truesight` 语义一个字没动：`observer_senses` 永远不会产出 `truesight`。

### 2.4 免费次数池不用新通道

`granted_spell_id` + `resource_id` 是既有的「常驻准备 + 免费施放」授权（`paladins_smite` 是现成先例）：`resolver.actor_projection` 把声明投影成带 `resource_id` 的 `spell_grants` 行，`triggered_spells.free_cast_resource` 再按 class resource 余量匹配。

因此 `srd_recovery.json` 与 `resting/engine.py` **都没有改动**：`class_resources.ranger.favored_enemy` 早就声明了 `short: none` / `long: all`，`tracks.favored_enemy = [2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,6,6,6,6]`。白名单 §5.2、§5.5 里这两处是「预留」，不是「必须改」。

### 2.5 标记伤害必须与 rider 同形，但不能走同一入口

武器命中路径在「所有能把它变成未命中的窗口都关闭之后」才掷骰；法术攻击路径（`spell_attack`）没有这样的窗口，命中即掷。两条路径若各写一份伤害构造，抗性边界迟早分叉。

**补法**：抽出 `_rider_damage_event(row, target, critical, rng)`，两条路径共用同一形状（`feature_id` / `owner_ref` / `resisted`）与同一次 `_resist_damage`。标记行由 `_mark_rider_row()` 产生，插在 `_hit_rider_declarations` 的**最前面**：它是法术效果，不是职业特性，所以它不经过 `feature_definition`。

### 2.6 Relentless 的「不豁免」不能发事件

`_concentration_with_dool` 把「首事件不带 `success`」当作失败并开窗口。若受影响时改发一条 message 或一条「自动成功」的 `check.resolved`，就会为一次**不需要**的豁免开窗口，客户端还能在里面掷骰。

**补法**：`_relentless_hunter_holds(target)` 为真时 `_concentration_events` 直接返回 `[]`——不掷骰、不留记录、不开窗口。判据两半都要成立：实例存在**且** `spell_ref == "spell:" + 声明里的法术`。只读特性会让所有专注免疫，只读法术会让没这特性的游侠白拿。

## 3. 逐项机械

### 3.1 Favored Enemy（1 级）

声明只有 `parameters: {granted_spell_id: hunter_s_mark}` + `resource_id: favored_enemy`。`_validate_mark_parameters` 的闭集就是这一个键：多写一个没人读的键，就是一条永远不会发生的承诺。

「总是准备」的验收点不在准备位数量上，而在**两份投影的差**：`actor["spell_refs"]` 含 `spell:hunter_s_mark`，`actor["class_spell_refs"]` 不含。1 级游侠仍有 2/2 个准备位，这个法术不占其中任何一位。

`automation_status` 从「这个法术真的有可结算效果」派生，所以它是 `automated`；目录里存在条目本身不构成实现。

### 3.2 Hunter's Mark 本体

- 施法走既有 `_validate_spell`：成本、射程、目标数、免费次数、可见性全部复用。新增的只有一条：`marks_target` 的法术要求目标可见（`_spell_target_visible`），而「可见」不是「有没有 `invisible`」，是「它的 `invisible` 还挡不挡我」——所以它问的是注册表的 `negated_by` 与本距离下的 `vision_codes`。
- 时长：`_mark_duration_hours(effect, slot_level)` 读 `mark_duration_hours` 与 `mark_duration_hours_by_slot`（3–4 环 8 小时、5 环以上 24 小时），结果随实例落库。
- 转移：`intent.retarget_mark` 在 `_spell_events` 与 `_validate_spell` 里都在**其他一切之前**分流，因此不走施法成本、目标数、免费次数；只花一个 Bonus Action，发 `dnd2024.concentration.retargeted`。reducer 同时更新 `mark_target_id` 与 `target_ids`，否则会出现「标已转移、目标列表还停在上一只生物」的实例（探针里就是这个现象）。
- 伤害：每次命中一枚骰，`sum` 进本次伤害用于专注 DC（一次命中一个 DC）。

### 3.3 高等级四项

- **Relentless**：见 §2.6。伤害照常扣 HP，其余终止方式（新专注、施放另一专注法术、长休）一个没少。
- **Precise**：`mark_attack_sources()` 判据是「我的实例指向你」**且**「我有这条声明」。放在 `attack_mode` 里，所以武器攻击与法术攻击都成立；转移后旧目标立刻失效，新目标立刻生效。
- **Feral**：见 §2.3。`_spell_target_visible` 与攻击模式共用 `vision_codes`，「能不能标记它」与「它还能不能靠 Blur 吃到劣势」不会给出两个答案。
- **Foe Slayer**：`_mark_damage_die` 读 `foe_slayer.mark_damage_die` 覆盖基础骰面，仅限**这个施法者自己的**实例；`srd_combat_core.json` 里 `mark_damage` 仍是 `1d6`（测试直接断言目录未变）。

## 4. 白名单对照

| 任务书 §5 | 文件 | 结果 |
| --- | --- | --- |
| 1 | `content/class_feature_catalogs/srd_class_features.json`、`locales/{en,zh-CN}/…` | 改：5 条声明 + 5 组双语标签 |
| 2 | `content/combat_catalogs/srd_combat_core.json` | 改：`hunter_s_mark` 效果；`invisible` 的两条 `negated_by` 追加 `blindsight` |
| 2 | `content/rest_catalogs/srd_recovery.json` | **未改**：`favored_enemy` 早已声明（§2.4） |
| 3 | `combat/resolution.py` | 改：rider 行、伤害事件抽取、施法事件、转移、法术攻击的标记骰 |
| 3 | `combat/validation.py` | 改：`retarget_mark` 分流、`marks_target` 可见性、`_validate_mark_retarget` |
| 3 | `combat/check_modes.py` | 改：`owned_mark` / `marked_target_id` / 感官三函数 / `mark_attack_sources` / `mark_find_sources`；`attack_mode` 按距离求值 |
| 3 | `combat/reducer.py` | 改：`concentration.started` 写 `mark_target_id`；新增 `concentration.retargeted` |
| 3 | `combat/engine.py` | 改：Relentless 短路、`hunters_mark_find_advantage`、`_hunters_mark_state`、`available_intents` 的 `hunters_mark`/`senses` |
| 3 | `combat/runtime.py` | **未改** |
| 4 | `features/resolver.py` | 改：`_validate_mark_parameters` 闭集校验 |
| 4 | `features/combat.py` | **未改** |
| 5 | `resting/engine.py` | **未改**：`preserve_spent` 与 `long: all` 已是既有行为 |
| 6 | 新建 `tests/rulesets/test_dnd2024_hunters_mark.py` | 新建 |
| 6 | 本文 | 追加 |

`spell_catalog`、`progression_catalogs`、前端、其他法术均未触碰。

## 5. 测试 1–24 映射

| # | 用例 | 断言要点 |
| --- | --- | --- |
| 1 | `test_matrix_01_…land_at_their_own_levels` | 12/13/17/18/20 级各自多出哪一条；低一级没有；五条 `source_ref`；五个参数值；en / zh-CN 标签 |
| 2 | `test_matrix_02_…never_a_prepared_slot` | 1 级 2 个准备位且不含它；`spell_refs` 含、`class_spell_refs` 不含；授予带 `resource_id` 与 `automated`；战士既无特性也无授予 |
| 3 | `test_matrix_03_…follows_the_class_table` | 1/4/5/8/9/12/13/16/17/20 → 2/2/3/3/4/4/5/5/6/6；花掉两次后升到 5 级是 `1/3`（只补新增额度） |
| 4 | `test_matrix_04_…never_a_slot` | 首次免费：扣 `favored_enemy` 1、槽不变、无 `slot_spent`、无 `feat_spell` 事件；用尽后 `available_slot_levels == [1]`、免费施法被拒；改用 1 环成功；短休不恢复、长休回满并清专注 |
| 5 | `test_matrix_05_…the_existing_spell_boundary` | 目录真值（Bonus Action / 90 ft / V / concentration / p141）；超距、双目标、Bonus Action 已用、不可见目标四种拒绝；成功时 `concentration.started` 的四个字段 |
| 6 | `test_matrix_06_…declared_duration` | 1 / 8 / 24 小时；实例 `spell_ref` + 唯一 `mark_target_id`；重施先 `ended{new_concentration_spell}` 再 `started` |
| 7 | `test_matrix_07_…rolls_the_die_again` | 同一回合两刀各掷一枚（4 与 6）；打别的生物无 force 事件 |
| 8 | `test_matrix_08_…not_to_the_target` | 盟友无实例 → 无骰；两名 Ranger 各掷自己那枚（6 / 2）；Force 抗性只减半标记那一份（4→2），武器 7 不变 |
| 9 | `test_matrix_09_…skill_check` | Perception / Survival 有来源；Stealth / 别的目标 / 无实例均为空 |
| 10 | `test_matrix_10_…takes_the_mark_with_it` | 真施放第二个专注法术（`protection_from_energy`）→ `ended{new_concentration_spell}`；之后既无骰也无优势 |
| 11 | `test_matrix_11_…pays_nothing` | 只发 `action.spent{bonus_action}` + `retargeted`；无 `spell.cast`、无槽、无次数；`mark_expires_hours` 保持 8；旧目标寻找优势立刻归零 |
| 12 | `test_matrix_12_…five_ways_to_be_illegal` | 目标未归零 / Bonus Action 已用 / 95 尺 / 两个目标 / 同一只 / 看不见的新目标，六条独立文案 |
| 13 | `test_matrix_13_…this_one_concentration` | 12 级：`check.resolved{concentration, success: false}` + `ended{failed_concentration_save}`；13 级：无该检查、无 ended，两边 HP 都 -7 |
| 14 | `test_matrix_14_…one_spell_and_only_damage` | 13 级持另一个专注法术受伤仍然照掷并失败；13 级重施 Hunter's Mark 仍然结束旧实例 |
| 15 | `test_matrix_15_…owner_of_the_mark` | 自己的标记有优势、别的生物没有、盟友（无实例）打被标记生物也没有；16 级自己没有；完整攻击链 `advantage_sources` 只多一条 |
| 16 | `test_matrix_16_…never_disagrees` | 无实例 / 施法后 / 归零后 / 转移后 / 存档重载后，`_precise` 与 `mark_target_id` 始终一致 |
| 17 | `test_matrix_17_…whole_difference` | 30 尺 Blur 被抵消、31 尺保留、17 级 30 尺仍保留；同一距离下「能不能标记隐形生物」得到相反答案 |
| 18 | `test_matrix_18_…not_truesight` | `observer_senses` 只给 30 尺内一条 `blindsight`；`vision_codes` 永不含 `truesight`、无 combat 时为空；Blur + Faerie Fire 在 30 尺合成优势、31 尺相消为空 |
| 19 | `test_matrix_19_…twentieth_level` | 19 级 `1d6` / 20 级 `1d10`；20 级用 9 点证明骰面（`SequenceRng` 会拒绝超出骰面的值） |
| 20 | `test_matrix_20_…out_of_the_instance` | 武器骰仍 `1d8`；目录 `mark_damage` 仍是 `1d6`；19 级盟友自己那枚仍是 `d6`；转移后新目标吃 `d10` |
| 21 | `test_matrix_21_…cannot_choose_any_of_it` | 伪造 `mark_target_id` / `mark_damage_die` / `blindsight_range_ft` / 专注例外 / `damage` / `advantage_mode` 全被忽略；免费次数用尽后伪造 `free_cast` 被拒 |
| 22 | `test_matrix_22_…twice` | 过期 version 被拒；同一意图重放 `replayed` + `duplicate`，次数与标记都不变；同回合第二次施法 / 第二次转移被拒 |
| 23 | `test_matrix_23_…same_fight` | 存档重载后标记、次数、槽位、双方 HP 全等；重放为重复；长休回满次数并清专注 |
| 24 | `test_matrix_24_…chains_still_work` | 内容只加宽未收窄（`invisible` 仍认 `see_invisibility`/`truesight`，全表只有一个 `marks_target`）；无标记时治疗与攻击链照旧 |

全量回归由任务书 §8 第二条命令（五个聚焦文件）承担，本文件只做同一批文件的冒烟。

## 6. 门禁

| 命令 | 结果 |
| --- | --- |
| `pytest tests/rulesets/test_dnd2024_hunters_mark.py -q` | 24 passed（58s） |
| 五个聚焦文件（`spells`/`invisibility`/`resting`/`combat`/`class_features`） | **141 passed** |
| `pytest tests/rulesets -q`（`-n 6 --dist loadfile`） | **2019 passed, 11 failed**（7m05s）；11 条全在 `test_dnd2024_character_builder.py`，签名 `unresolved reference 'ability:con' in background:soldier` |
| `ruff check`（combat、features、resting/engine.py、新测试） | All checks passed |
| `mypy` | Success: no issues found in 102 source files |
| `git diff --check` | 干净 |

### 6.0 那 11 条失败是基线，不是本切片

`git stash` 掉 `src/` 与 `templates/` 的改动后，同一条命令在基线上跑 `test_dnd2024_character_builder.py` 得到**同样的 11 failed, 28 passed**（30.99s）。根因是 `background:soldier` 里引用 `ability:con`（`backgrounds` 目录里的既有数据问题，Windows 本地常驻），其中一条 HTTP 契约用例是同一根因的连带 404。本切片没有净新增失败。

### 6.1 只有门禁才会现形的三处

1. **目录闭集**：`mode: "mark"` 在 `Dnd2024CombatCatalog.from_bundle` 当场失败。读代码看不出来——`SPELL_MODES` 在另一个文件、且看起来只是常量。
2. **reducer 的 `target_ids`**：转移只改 `mark_target_id` 时，实例里两个字段互相矛盾。断言「转移生效」会通过，断言「实例自洽」才会失败。
3. **Relentless 的窗口**：把「不豁免」写成一条 message 之后，`_concentration_with_dool` 会为它开窗口。测试若只断言「专注还在」，这条会假绿；必须断言「没有 `check.resolved{kind: concentration}`」。

## 7. 偏差与未决

1. **`combat/view.py` 不在白名单**：§4.2.4 的「actor view 增加 `blindsight: 30`」实现为**服务端意图行** `available_intents.cast_spell.senses = [{code, range_ft, source}]` + 每次掷骰按真实距离求值。结果是：视图不携带「我看得见」，因为那个答案依赖距离，写进视图就是错的。
2. **`combat/catalog.py` 不在白名单**：`hunter_s_mark` 用 `debuff` + `marks_target`，未新增 mode（§2.1）。
3. **`resting/engine.py`、`srd_recovery.json` 未改**：无需改动，既有声明已覆盖（§2.4）。
4. **法术成分（V）只做内容真值**：引擎全局没有「沉默/言语成分」判据（`grep -i "verbal\|silenced"` 为空），本切片不为一个法术新造这套规则。矩阵 5 的 V 一项断言的是目录声明。
5. **掩体没有模型**：`srd_combat_core.json` 里没有 cover 概念，`grep total_cover` 为空。矩阵 18 的「不忽略 Total Cover」无法被证伪——引擎里没有掩体可忽略，这既不是本切片引入的，也不是它能修的。
6. **时长没有时钟**：`combat` 状态里没有时间，`mark_expires_hours` 是随实例落库的**声明值**，不是会被执行的到期点。与既有 concentration 模型的诚实程度一致，不另造时钟。
7. **Relentless 的另外三种终止不可达**：`Incapacitated` 需要敌方施法者（敌人只有武器攻击）、`到期` 需要时钟、`主动结束` 在 combat 里没有意图（只有 `resting/engine.py` 的长休会发 `end_concentration`）。因此矩阵 14 覆盖的是「另一专注法术仍然会断」与「新专注仍然会替换」，其余三种在报告里标注为不可达，而不是用一条手写事件假装覆盖。
8. **寻找检定优势只是一个权威入口**：`engine.hunters_mark_find_advantage(instance, actor_id, skill_id, target_id)` 回答「这一掷有哪些来源」，但 combat 引擎不掷技能检定，白名单里也没有探索侧检定解析器。本切片提供权威答案，消费方留给后续切片——这一点比「给探索侧加一条分支」更保守，也更不容易分叉。
9. **转移后的 `target_ids`**：reducer 同时更新两个字段，避免实例内部矛盾；`concentration.retargeted` 事件带 `previous_target_id` 以便审计。

### 未决

- **探索侧检定接入**：见偏差 8。需要探索/非战斗检定解析器落地后再把 `hunters_mark_find_advantage` 接上。
- **法术成分**：一旦引擎引入言语/姿势成分判据，`hunter_s_mark` 的 V 会自动生效；届时矩阵 5 的 V 一项应从内容真值升级为行为断言。
- **掩体**：需要先有 cover 模型；`feral_senses` 不承诺穿透总掩体，`blindsight` 也没有被加进任何掩体相关的 `negated_by`（因为不存在）。
