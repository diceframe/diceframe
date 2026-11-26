# S0-F31 执行报告：Use Magic Device、法术书与 Evocation Savant

| 项 | 值 |
|---|---|
| 任务书 | `S0F31_SPELLBOOK_ITEMS_TASK_SPEC_2026-11-07.md`（本片修订版，见其 §0） |
| 基线 | `b0496921`（2026-11-26，GAP-43 栈 #856–#866 与 #844/#847 已合入） |
| 分支 | `feat/dnd2024-s0f31-spellbook-items` |
| 交付范围 | UMD 同调 + 充能免收、Ritual Adept、Memorize Spell、Evocation Savant；卷轴 `not_automated` |
| 状态 | 四项自动化交付完成；卷轴三项按 §1 停止条件保持非完成 |

## 1. 基线核对：任务书 §3 的三条"缺失"有两条已过时

开工前逐条探测，结论写进任务书 §0：

| 原判断 | 实测（`f2d86b1a`） |
|---|---|
| 无同调容量函数 | `character/attunement.py::attunement_capacity` 已读 `attunement_slot_bonus`，BASE 3 / MAX 10，只是全仓无声明使用 |
| 无充能管线 | `items/charges.py` 有 `spend_charges` / `should_check_destroy` / `destroy_check_events`；`item.charge_spent` 两处发出点 |
| 无 Spell Scroll 物品 | 目录文件有两条描述条目，**运行时 item 实体 115 条里没有卷轴**；条目无 `spell_ref` / 等级 / `activation` |

另两条本次新发现、直接影响排期的事实：

- **`ritual` 零实现**：全仓只有 `spells/selection.py` 一句注释，探索与战斗都没有仪式入口。
- **四条特性没有定义对象**：`classes.*.features` 里没有它们，`feature_value()` 读不到参数。所以第一步是补声明。

## 2. 交付内容与落点

原则：**这四项里没有一项需要新主干**。已有的"特性授予玩家 N 个法术选择"（`spell_choice_*` 槽位族，代表 Magical Discoveries）、"休息时替换准备法术"（`PreparationPolicy` + `parse_prepared_spells`）都能直接承载，本片的活是把它们参数化到够用，而不是各起一套。

| 项 | 落点 | 关键决定 |
|---|---|---|
| 四条特性声明 | `srd_class_features.json` | Evocation Savant 走授权槽 `spell_choice_*`；Memorize Spell 只要 `short_rest_replace: 1` |
| UMD 同调 4 件 | 纯数据 | `attunement_capacity` 早就读参数，代码零改动 |
| UMD 充能 1d6 | `combat/resolution.py`、`exploration/engine.py`、两处事件字段表 | 掷了就发 `dnd2024.item.charge_roll`（含 `waived`），**不发 `amount: 0` 的 charge_spent**（`spend_charges` 拒绝 0 价） |
| Ritual Adept | `exploration/engine.py`、`combat/validation.py` | 同一意图加 `ritual: true`，不新增意图类型；战斗显式拒绝 |
| Memorize Spell | `spells/preparation.py`、`spells/selection.py`、`resting/engine.py`、`runtime.py` | 并入 `prepared_spells` 主干；短休预算 = 职业 policy + 特性声明 |
| Evocation Savant | `spells/selection.py`、`features/resolver.py`、`progression/advancement.py` | 授权槽 `target="spellbook"`；正常增量恰好 N，多出来的必须正好是授予项 |
| 卷轴 | 无代码 | `not_automated`，两条用例锁边界 |

三处"并回主干"的具体形状：

1. **Evocation Savant 是授权槽，不是新账本。** 声明 `spell_choice_count=2` /
   `spell_choice_per_new_slot_level=1` / `spell_choice_school=evocation` /
   `spell_choice_min_level=1` / `spell_choice_target=spellbook` / `spell_choice_replacement=0`，
   提交走既有的 `spell_grants`，持久化走既有的 `spell_grant_choices`。代码里不再有特性 id、
   不再有 `free_spellbook_allowance`、"学派"从硬编码变成被读取的声明。槽位条数是
   **累计欠账**（3 级 2 个，每开一环 +1），靠 `_new_slot_levels()` 比较相邻等级槽表得出，
   不写成算术常数；未填不作废，后面补上仍然合法。
2. **Memorize Spell 是短休预算，不是旁路校验器。** `PreparationPolicy` 增加
   `short_rest_replace`，`parse_prepared_spells(payload, request, context="short_rest")`
   走同一套选择校验（环阶上限、书内、准备数不变、戏法不可换都由它统一保证）。
   `memorize_spell` 这个参数名被删掉，改由已在 `rest_option_names` 白名单里的
   `prepared_spells` 承载——原来的写法是**白名单外的参数，通用休息服务会静默丢弃**。
3. **仪式是同一意图上的一个开关。** 已准备的仪式法术在投影里只出现一条并标
   `ritual: true`（原来会漏掉"它能省槽"这个事实），槽位用尽时仪式法术不再从列表里消失。
   `available_intents` 的过滤条件从"没槽就跳过"改成"没槽且不是仪式才跳过"。

## 3. 验收矩阵逐项（任务书 §6，14 条）

| # | 内容 | 结果 |
|---|---|---|
| 1 | Thief 12/13 容量 3/4、非 Thief、跨职业不泄漏 | ✅ 4 条用例 |
| 1b | 短休同调交换、第五件拒绝、死亡解绑、读档一致 | ⬜ 既有 `test_dnd2024_attunement.py` 已覆盖，本片未重复 |
| 2 | 真实耗充能每次先掷 d6；6 免收；1–5 扣原价 | ✅ 探索 2 条 + 战斗 2 条（各含免收与扣费） |
| 2b | 不耗充能路径零 RNG | ✅ 无 UMD 的盗贼一条；`charge_waiver_roll` 只在 `maximum`/`charge_cost` 都在时调用 |
| 3 | 多充能只掷一次；免收不触发销毁；apply/replay 不重复扣费 | 🟡 免收不触发销毁由 `remaining == current` 保证但无用例；多充能合并仍在 `spend_charges`，本片未动 |
| 4–5 | 卷轴戏法/1 环可靠施放、2+ 环 Arcana 检定 | ⛔ `not_automated`（缺物品身份） |
| 6 | 客户端伪造/缺系统 fail closed | 🟡 卷轴两条负例；`charge_roll` 的 `waived` 由服务端计算，客户端无法提交（字段表冻结）；`ritual` 必须是真布尔、`slot_level` 必须是整数，两条类型负例锁住"字符串 truthy 就免掉槽"和"`int()` 抛裸 ValueError" |
| 7 | 书中未准备 Ritual 可施放、不耗槽 | ✅ 正面 + 4 条负例（非 Ritual / 书外 / 非 Wizard / 战斗） |
| 8 | 探索投影区分准备与仪式；战斗不压缩 | ✅ 投影 3 条（未准备单列、已准备只列一条且标 ritual、无槽不消失）+ 战斗拒绝 1 条 |
| 9 | 5 级短休换一个准备法术 | ✅ 正面 2 条（自带参数 / 走 `rest_option_names` 声明入口） |
| 10 | 一次换多个/戏法/书外/非 Wizard 拒绝 | ✅ "多个"有断言（`at most 1`）、书外、5 级以下；戏法由环阶预算间接覆盖 |
| 11 | Evoker 3 级两个免费、跨学派拒绝 | ✅ 正面 + 跨学派 + 跨列表 + 戏法 |
| 11b | 0 环 / 3 环 / 重复项拒绝 | 🟡 0 环（戏法）已测；3 环与重复项靠 `maximum_spell_level` 与书目集合去重，未单独断言 |
| 12 | 新开环阶 +1、未开不加 | ✅ 4 级 =2、5 级 =3（本条为新增） |
| 13 | 免费名额不占正常两个、不自动 prepared、不扣 GP/时间 | ✅ `spellbook_additions` 仍 2、`spell_grant_rows == []`、四个普通法术凑数被拒；GP/时间本层无机制 |
| 14 | 四特性在 advance/history/save-load/replay 一致 | 🟡 升级路径真实走通；history 与 replay 未单独断言（Memorize 只改 canonical，见 §5） |

## 4. 验证输出

```
pytest tests/rulesets/test_dnd2024_spellbook_items.py                  40 passed
pytest tests/rulesets/test_dnd2024_spellbook_items.py \
       test_dnd2024_item_destroy.py                                   61 passed
pytest tests/rulesets tests/architecture -n 6 --dist loadfile \
       --basetemp=E:/pytest-tmp/f31v6       3210 passed, 224 warnings in 1318.92s
ruff check src/ tests/                                    All checks passed
mypy (python -m mypy)                                     118 files, no issues
```

全量跑了两轮，两轮都有意义，不存在"拿旧结果充数"：

1. `f31v5` → 3136 passed，对应 rebase 之前（基座 `f2d86b1a`）。
2. `f31v6` → 3210 passed，对应 rebase 到 `b0496921` 之后的代码。差额 74 条是上游
   GAP-43 栈带进来的新用例，不是本片新增——本片自己的两个文件前后都是 61 passed。

再早的两轮作废原因（记下来免得重犯）：核对过程中改过 `exploration/engine.py`
（`ritual` 类型收紧），worker 早已 import 完成，结果不代表最终代码；另有一轮
`--basetemp` 指向已存在目录，pytest 先删它 → 删除走沙箱回收站 shim → `Errno 53`。

既有契约风险点不是"新用例是否绿"，而是"共享校验器被改后，别的类的升级/休息是否还绿"
——这正是 3200+ 条全量要回答的问题：`spellbook_additions` 仍为 2、`prepared_spells`
仍是唯一长休入口，paladin/ranger/cleric 的休息与升级路径全部未受影响。

## 5. 偏差与未决

**必须记住的三条**

1. **卷轴三项没有交付**，且不是"差一点代码"：运行时没有卷轴物品实体，条目也没有
   `spell_ref`。要做必须先有物品身份（谁装的哪个法术、几环、用后是否崩解），那是
   物品系统的事，不在本片白名单内。
2. **`charge_roll` 与 `spell.cast.ritual` 是新事件字段/类型**。两份 schema
   （`combat/reducer.py`、`exploration/engine.py`）已同步登记，但**前端与审计日志
   还没有消费它们**——目前只保证不破坏 apply。`charge_roll` 每次掷骰都发（含
   `waived: false`），这样"掷了没免"和"根本没掷"在事件流里可区分。
3. **仪式的时间与组件没有强制**。探索层没有时钟可推，"10 分钟"只体现在"不花槽、
   必须读书里的那一条"；施法时间与材料消耗留给叙事层。这条写进任务书 §4.3 的
   偏差里，不能在验收时宣称"完整实现"。

**未覆盖但已知可行的**

- 多充能只掷一次、免收不触发销毁检查：代码上 `spent = 0` 会让 `remaining = current`，
  `should_check_destroy` 只在 `remaining == 0` 时为真，因此天然不触发；但缺用例。
- 3 环授予项与重复项：`maximum_spell_level` 与书目集合都拦，但没有点名用例。
- Memorize Spell 只改 canonical 准备表，不写 `progression.history`；长休的准备变更
  同样不写，所以两者一致，但"改过准备"这件事目前只能从 `prepared_spells_changed`
  事件流读出。

**一次几乎交付出去的破坏性契约变更（必读，含一次自我纠正）**

Evocation Savant 先后有过三版，全部在同一处出错——**把"授予"当成了"增量"**：

1. 第一版把免费名额**并进** `spellbook_additions`（3 级从 2 变 4），于是所有既有
   升级夹具（只交正常那几个）立刻不合法：全量 **214 failed / 37 error**，集中在
   paladin_auras、overchannel、hold_person、stoneskin、free_casting 等 12 个走
   Wizard 升级路径的文件。
2. 第二版读反了 SRD 的"you **can** add"，把两个名额做成**可选额度**
   （`free_spellbook_picks` + 区间校验），并提交 `923c47dc`。这是**错的方向**：
   SRD 第一句是祈使句 "Choose two … and add them"，"can" 只修饰后半段的
   "whenever you gain access to a new level of spell slots"。这一版没有破坏任何人，
   但也把"玩家该有两个免费页"这件事降级成了提示。
3. 现行版本把名额放回**授权槽位族**：`count=2` 是累计欠账，不是每级上限；未填不作废、
   可后补；正常增量仍恰好 2，多出来的必须正好是授予项。

教训有两个，第二个才是这次真正踩到的：

- 动 `requirements` 里的数字前，先问"这个字段是下限还是上限"。改成上限兼容，
  改成下限会让所有既有调用方同时失效。
- **判断"这里该不该复用已有主干"要在写代码之前做，不是审阅时做**。这三版各自都能
  自圆其说、自己那几十条用例也全绿，唯一的破绽是：同样的语义在仓库里已经有一份实现。
  新增一个 `free_spellbook_allowance`、一个 `_plan_memorize_spell`，代价不是多写的
  那些行，而是后面每个语义（学派、环阶上限、替换预算）都要在两处各改一遍——第二处
  一定先漏。

同类的第二个旁路是 `memorize_spell`：它既不在 `rest_option_names` 白名单里（通用休息
服务会静默丢掉，Web 端根本换不了法术），又自带一套只校验"书内 / 已准备 / 1+ 环"的
私有校验，漏掉了 `_replacement_errors` 里"环阶不超过最高槽位"这条统一约束。并回
`prepared_spells` 后，这两条都是免费得到的。

**与上游 GAP-43 栈的关系**

`_activate_item_events` 在栈顶分支里仍在 `resolution.py`（未搬走），所以本片的
充能改动不会因搬家失效。

2026-11-26 已实际 rebase 到 GAP-43 栈合入后的 main（`b0496921`，含 #856–#866 与
#844/#847）：**12 个提交里只有 1 处冲突**，在
`tests/architecture/combat_coupling_baseline.py` —— 上游把 `sacred_weapon_damage` /
`same_plane` / `sear_undead` 三条 `intent_switch` 随抽取删掉了，本分支只是新增
`ritual` 一条。解决方式是保留本分支那一条、接受上游的删除，没有手改检测逻辑。
这个"行号冲突"的预判其实偏悲观：抽取把代码搬了家，但本片的 hunk 都落在没被搬走的
函数里，只有集合式白名单真的撞上了。

## 6. 收尾清单（提交前逐项打勾）

- [x] `ruff check src/ tests/` All checks passed
- [x] `mypy` 118 files, no issues（本机命令是 `C:/Python314/python.exe -m mypy`，该解释器 `Scripts/` 下没有 `mypy.exe`）
- [x] `pytest tests/rulesets tests/architecture` 3210 passed / 0 failed（rebase 到 `b0496921` 之后重跑）
- [x] 任务书 §4.1.4 / §4.3 / §4.4 已改成新口径（`charge_roll` 一次掷骰一条、短休走 `prepared_spells`、授权槽 `target=spellbook`），§5 白名单补上 `spells/preparation.py` 与 `runtime.py`
- [x] `combat_coupling_baseline.py` 的 `intent_switch: ritual` 条目保留，并在条目上方写明它是**拒绝**而非特性分支（删掉这条读取 = 把 fail-closed 的拒绝变成静默改规则）；架构守卫要求集合精确匹配，因此"条目 + 分支"必须同进同出

**仍未做、且已知原因**

- 免收不触发销毁的用例：SRD 5.2.1 里没有物品声明 `destroy_on_empty`，造一个只为测试的假物品违反 §5"不新建虚假物品"。
- `progression.history` 不记录准备变更：长休与短休一致，属于既有行为，不在本片改。
- 卷轴三项：缺物品身份，`not_automated`。
