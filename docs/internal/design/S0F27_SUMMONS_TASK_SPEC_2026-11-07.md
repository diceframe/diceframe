# S0-F27 任务书：Familiar、Otherworldly Steed 与 Draconic Spirit

| 项 | 值 |
|---|---|
| 切片编号 | S0-F27（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `faithful_steed`、`wild_companion`、`dragon_companion` 及其真实召唤生命周期 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Druid p43、Paladin p55、Draconic p70、Find Familiar/Find Steed p130-131、Summon Dragon p166；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | F5 独立免费施法槽与常驻准备；F25 CR0 Beast/Wild Shape 资源；F20 turn slot；Steed/Dragon 数据与召唤生命周期由本切片新建 |
| 建议分支 | `feat/dnd2024-s0f27-summons` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 从 F5/F20/F25 实际集成提交开工；缺 Otherworldly Steed、Draconic Spirit 或合法 CR0 Beast 的真实 stat block 时停止，不能只扣槽/资源生成同名空壳。
2. 本切片实现召唤、独立/共享先攻、指令、动作限制、消失/替换/暂时收起、免费施法和长休生命周期；不是只添加三个 feature summary。
3. 所有召唤数值来自服务端官方内容；客户端只能提交法术、合法形态/类型、位置、槽和 Dragon 的 Concentration 选择。
4. **Dragon Companion 按仓库 SRD 子职表和官方 p70 的 18 级授予，不采用第三方 19 级文本，不修改 progression 到 19。**
5. 不实现其他召唤法术、通用坐骑系统重构或 Beast 以外的怪物图鉴；F5 只提供免费施法模型，本切片自行建立 Steed/Dragon 官方数据，Cone/坐骑等系统缺口必须停止。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p43，Wild Companion：

> As a Magic action, you can expend a spell slot or a use of Wild Shape to cast the Find Familiar spell without Material components. When you cast the spell in this way, the familiar is Fey and disappears when you finish a Long Rest.

SRD 5.2.1 p55，Faithful Steed：

> You always have the Find Steed spell prepared. You can also cast the spell once without expending a spell slot, and you regain the ability to do so when you finish a Long Rest.

SRD 5.2.1 p70，Dragon Companion：

> **Level 18: Dragon Companion.** You can cast Summon Dragon without a Material component. You can also cast it once without a spell slot, and you regain the ability to cast it in this way when you finish a Long Rest. Whenever you start casting the spell, you can modify it so that it doesn’t require Concentration. If you do so, the spell’s duration becomes 1 minute for that casting.

SRD 5.2.1 p130，Find Familiar：

> The familiar has the statistics of the chosen form ... though it is a Celestial, Fey, or Fiend ... Your familiar acts independently of you, but it obeys your commands.
>
> **Combat.** It rolls its own Initiative and acts on its own turn. A familiar can’t attack, but it can take other actions as normal.
>
> **Disappearance.** When the familiar drops to 0 Hit Points, it disappears ... As a Magic action, you can temporarily dismiss ... or dismiss it forever ... reappear in an unoccupied space within 30 feet ... it leaves behind ... anything it was wearing or carrying. **One Familiar Only.** If you cast this spell while you have a familiar, you instead cause it to adopt a new eligible form.

同页 Telepathic Connection：100 尺内心灵感应；Bonus Action 共享感官至下一回合开始；100 尺内 familiar 可用 Reaction 传递施法者的 Touch 法术。

SRD 5.2.1 p130-131，Find Steed/Otherworldly Steed：

> The steed is replaced by the new one ... it shares your Initiative count, and it functions as a controlled mount while you ride it. If you have the Incapacitated condition, the steed takes its turn immediately after yours and acts independently, focusing on protecting you. The steed disappears if it drops to 0 Hit Points or if you die.
>
> AC 10 + 1 per spell level; HP 5 + 10 per spell level; Fly 60 ft. (requires level 4+ spell). **Life Bond.** When you regain Hit Points from a level 1+ spell, the steed regains the same number ... within 5 feet. Otherworldly Slam uses your spell attack modifier and deals 1d8 plus the spell’s level of Radiant/Psychic/Necrotic damage.

SRD 5.2.1 p166，Summon Dragon/Draconic Spirit：

> The creature disappears when it drops to 0 Hit Points or when the spell ends ... shares your Initiative count, but it takes its turn immediately after yours ... If you don’t issue any [verbal commands], it takes the Dodge action and uses its movement to avoid danger. Use the spell slot’s level for the spell’s level in the stat block.
>
> AC 14 + the spell’s level; HP 50 + 10 for each spell level above 5. **Multiattack.** A number of Rend attacks equal to half the spell’s level (round down), and it uses Breath Weapon. **Rend.** 1d6 + 4 + the spell’s level Piercing. **Breath Weapon.** 30-foot Cone, Dexterity save, 2d6 damage of a chosen Resistance type, half on success. **Shared Resistances.** You have Resistance to the chosen damage type until the spell ends.

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 三个 id 已在 progression/subclass 表按 Paladin 5、Druid 2、Draconic **18** 列出，canonical 定义尚不存在 | `content/progression_catalogs/srd_classes.json`；`content/subclass_catalogs/srd_subclasses.json` |
| Find Familiar、Find Steed、Summon Dragon 已有基础 spell 条目，但无召唤 stat block/效果 | `content/spell_catalogs/srd_spells.json`；`content/combat_catalogs/srd_combat_core.json` |
| 基线没有 Beast/steed/dragon summon stat block 文件；CR0 Beast 由 F25 提供，Steed/Dragon 由本切片新建，F5 只提供免费施法槽 | 基线 `templates/rulesets/dnd2024_srd/content/` 文件清单；F25 Beast catalog；§5 明确标记的新建文件 |
| `ruleset_state.party.companions` 是现有 AI 队友权威容器，active companion 可进入战斗 | `combat/engine.py::initialize_state`；`combat/view.py::_active_companions` |
| combat.start 为 active companion 独立掷先攻；F20 turn slot 是共享/紧随 schedule 的扩展点 | `combat/resolution.py::_start_combat_event`；`combat/engine.py::_current_actor` |
| companion 自动行动、HP、条件、法术和死亡跳过已有统一路径 | `combat/engine.py::_allied_automatic_intent`；`combat/reducer.py::_apply_hp_change` |
| 法术 cast、槽支付、专注开始/结束和条件清理已有事件 | `combat/resolution.py::_spell_events`；`combat/reducer.py::_reduce_event` |
| F5 免费槽由长休统一恢复；本切片只新增 Wild Companion familiar 的长休消失，不另写免费资源恢复 | `resting/engine.py::complete_long_rest`、`_recover_class_resources`；F5 recharge 接口 |
| 探索与战斗各有事件 reducer，召唤生命周期必须一致 | `exploration/engine.py::resolve_intent`、`_reduce_event` |
| 基线 companion 没有真实创建/移除事件，不能由客户端直接写 party | `combat/reducer.py::_companion_character`；`exploration/engine.py::_companion_canonical` |

## 4. 精确行为与数据契约

### 4.1 统一召唤记录、事件与 turn slot

1. 三特性按真实职业/子职和等级授予；`faithful_steed`、`dragon_companion` 各注册一个 F5 `(owner,feature_id,free_slot_id)` 免费槽，固定环阶 2/5、一次、Long Rest recharge。不得另建平行普通资源或共享免费 bool；Wild Companion 不建免费池。
2. 每次召唤生成 `dnd2024.companion.summoned`，内容包含唯一 companion id、`summon_kind`、`source_spell_ref`、`source_feature_id`、caster/owner、`slot_level`、真实 stat block ref、creature type、位置和持续/专注元数据。
3. reducer 在 `party.companions` 创建带 `ruleset_character` 的 active 记录；移除统一用 `dnd2024.companion.dismissed(reason)`，处理 0 HP、法术结束、长休、替换、暂时/永久收起和物品掉落，不能仅把 active 标志交给客户端。
4. 战斗中创建须原子插入 turn slot 和位置，战斗外创建须在下一次 combat.start 采用同一规则；存档/加载、重放和战斗结束不复制或意外复活。
5. Familiar 使用自己独立 d20 initiative；Steed/Dragon 不另掷先攻，使用共享 count 并在每个对应 caster turn slot 后紧随其 linked turn。Action Surge 不是新回合，不增加召唤回合；Thief 的两个实际 turn slot 各有其后置 linked slot。
6. 多个召唤的 tie-break、owner 死亡、施法者失能、跳过 0 HP companion 和新增/移除 slot 必须确定且可重放；不能把 shared summon 放到整场队尾。

### 4.2 Wild Companion 与完整 Familiar 生命周期

1. Druid 2 级以 Magic action 选择消耗一个真实法术槽或 1 Wild Shape use，施放 Find Familiar 且无需 Material；此路径覆盖普通 1 小时/Ritual casting time，不另耗一小时。
2. 形态只能是 F25 Beast catalog 中的合法 CR0 stat block ref，并覆盖 Find Familiar p130 所列 Bat/Cat/Frog/Hawk/Lizard/Octopus/Owl/Rat/Raven/Spider/Weasel；本路径 creature type 固定 Fey，不能选择 Celestial/Fiend，长休完成时消失。
3. Familiar 服从指令但独立行动，自己的 initiative/turn 独立；所有 attack、Opportunity Attack 和含攻击的 Multiattack 意图均禁止，其他合法动作可正常执行。
4. 100 尺内支持心灵感应；Bonus Action 共享视觉/听觉及特殊感官至下一回合开始；100 尺内可用其 Reaction 在施法者施放 Touch 法术时完成传递。
5. 0 HP 消失并留下穿戴/携带物；Magic action 可暂时收起到 pocket dimension 或永久解散，暂时收起后可用 Magic action 在 30 尺内 unoccupied space 重现，物品掉落规则一致。
6. 同一施法者最多一个 familiar；再次施法不生成第二个，而将现有 familiar 改为新合法形态。新施法重新确定来源/类型/长休规则，不能遗留旧形态统计。

### 4.3 Faithful Steed 与 Otherworldly Steed

1. Paladin 5 级始终准备 Find Steed；可用正常 2+级槽或 F5 的 `faithful_steed` 免费槽一次，免费施法 slot_level=2，由 F5 长休恢复，短休不恢复。
2. Find Steed 为 Action、30 尺 unoccupied space、Instantaneous 且无 Concentration；类型选择 Celestial/Fey/Fiend。已有 steed 时新一个替换旧一个，不并存。
3. Steed AC=10+slot level、HP=5+10×slot level，HD 数量等于 slot level，Speed 60；仅 4+级槽获得 Fly 60。PB、攻击加值和 save DC 使用真实 Paladin 派生值。
4. Otherworldly Slam 为 1d8+slot level，类型按 Celestial/Fey/Fiend 为 Radiant/Psychic/Necrotic；本切片按 p131 实装三类专属 Bonus Action 及各自 Long Rest recharge，不能全类型混用，也不得归派给 F5。
5. Life Bond：Paladin 从 level 1+法术实际恢复 HP 且 steed 在 5 尺内时，steed 恢复相同实际 HP 数；非 spell 治疗、超距或原治疗为 0 不触发。
6. 骑乘时 shared initiative 且为 controlled mount；Paladin 失能时 steed 紧随其回合独立行动并以保护 Paladin 为目标。F5 不提供骑乘模型；无权威骑乘/受控坐骑接口时停止。
7. Steed 在 0 HP 或 Paladin 死亡时消失并掉落携带/穿戴物；再次施法可选择召回曾消失的同一个或不同 steed，记录选择而非暗中复制装备。

### 4.4 Dragon Companion 与 Draconic Spirit

1. Draconic Sorcerer 18 级所有 Summon Dragon 施法均免 Material；可用正常 5+级槽或 F5 的 `dragon_companion` 免费槽一次，免费 slot_level=5，由 F5 长休恢复，短休不恢复。
2. 每次开始施法可选择普通 Concentration up to 1 hour，或修改为无 Concentration、duration=1 minute；该选择进入 spell.cast/ summon 事件。无 Concentration 版本不占专注、受伤不做该法术专注豁免，也不因另施专注法术结束。
3. Dragon 在 60 尺内可见 unoccupied space 出现，0 HP 或法术结束时消失；普通版本 Concentration 结束立即消失，修改版本在一分钟到期消失。
4. stat block 使用真实 slot level：AC=14+level，HP=50+10×(level-5)，速度/感官/免疫和 PB 完整；5 级免费版不能伪造升环数值。
5. Rend 使用施法者 spell attack modifier，伤害 1d6+4+level Piercing；Multiattack 为 floor(level/2) 次 Rend 加一次 Breath Weapon。Breath 为 30 尺 Cone、施法者 spell save DC、所选抗性类型 2d6，成功半伤。
6. 施法时选择一种 Dragon 已有 Acid/Cold/Fire/Lightning/Poison resistance，施法者获得同类型 Shared Resistance 直到该法术结束；召唤消失即移除，不残留或叠加。
7. Dragon 服从无动作口头指令；未下指令时自动 Dodge 并移动避险。共享先攻紧随施法者，不是独立 d20；其攻击/区域结算走真实战斗管线，缺 Cone 或 stat block 时停止。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. 新建 `templates/rulesets/dnd2024_srd/content/creature_catalogs/srd_summons.json`、`templates/rulesets/dnd2024_srd/locales/en/creature_catalogs/srd_summons.json`、`templates/rulesets/dnd2024_srd/locales/zh-CN/creature_catalogs/srd_summons.json`（基线均不存在；仅 p131/p166 官方 Steed/Dragon 数据）
5. `src/rulesets/dnd2024/features/resolver.py`
6. `src/rulesets/dnd2024/spells/catalog.py`：仅召唤施法修饰校验
7. `src/rulesets/dnd2024/combat/resolution.py`
8. `src/rulesets/dnd2024/combat/engine.py`
9. `src/rulesets/dnd2024/combat/reducer.py`
10. `src/rulesets/dnd2024/combat/validation.py`
11. `src/rulesets/dnd2024/combat/view.py`
12. `src/rulesets/dnd2024/combat/primitives.py`：仅共享 slot/事件常量
13. `src/rulesets/dnd2024/exploration/engine.py`
14. `src/rulesets/dnd2024/resting/engine.py`：仅 Wild Companion 长休消失；免费槽恢复复用 F5
15. `src/rulesets/dnd2024/runtime.py`
16. 新建 `tests/rulesets/test_dnd2024_summons.py`
17. 本任务书：只允许追加执行报告

Steed/Dragon 数据只能在第 4 项新建，CR0 Beast 复用 F25 catalog 与加载器；免费槽复用 F5，不改 rest catalog。不得改 progression/subclass/spell catalog、通用 rest service 或前端；缺数据时停止。

## 6. 编号测试矩阵

1. 三特性按 Paladin 5、Druid 2、Draconic 18 授予；Draconic 17 没有，19 级不是首次授予，其他子职没有。
2. Wild Companion 以槽或 Wild Shape 二选一支付 Magic action，免 Material；资源不足、两种都不足或客户端伪造成本时零事件。
3. Wild Companion 只生成真实合法 CR0 Beast stat block、固定 Fey；缺 Beast 数据时 fail closed 且不扣资源。
4. Familiar 独立掷先攻并拥有自己的回合；所有攻击/借机攻击被拒绝，Dodge/Help 等非攻击动作可用。
5. 100 尺内心灵感应、Bonus Action 感官共享和 100 尺 Reaction 传递 Touch 法术均正确，超距不可用。
6. Familiar 0 HP 消失并掉落物品；暂时收起、30 尺内重现、永久解散及无效位置行为正确。
7. 同一施法者再次施法只改变现有 familiar 形态，不生成第二个；旧类型/统计不残留。
8. Wild Companion familiar 在完成长休时消失并产生原因事件；其他来源 familiar 不被误删，重放不重复删除。
9. Faithful Steed 始终准备 Find Steed，免费施法为 2 级且长休一次；正常槽可升环，短休不恢复免费次数。
10. Steed AC/HP/HD 随 slot level 正确，2 级无 Fly、4 级起 Fly60；类型选择和 30 尺空位校验有效。
11. 已有 steed 时重施替换旧记录，不并存、不复制装备；0 HP 或 Paladin 死亡消失并掉落物品。
12. Steed 共享先攻，骑乘时为 controlled mount；Paladin 失能时紧随其后独立保护，不能独立重掷先攻。
13. Otherworldly Slam 使用 Paladin spell attack、1d8+slot level 和正确类型；三类专属 Bonus Action 不混用且长休恢复。
14. Life Bond 在 5 尺内把 level 1+法术实际治疗量同样恢复给 steed；非 spell、超距和 0 治疗不触发。
15. Dragon Companion 18 级所有施法免 Material；免费 5 级一次、长休恢复，正常槽升环使用真实 level。
16. 每次施法可选普通 Concentration/1 小时或无 Concentration/1 分钟，事件和到期理由准确。
17. 无 Concentration 版本不占专注、不因伤害做该法术专注豁免、不被新专注法术结束；一分钟仍到期。
18. 普通版本 Concentration 被伤害/失能/新专注结束时 Dragon 消失；0 HP 和法术结束均正确清理。
19. Draconic Spirit AC/HP/速度/感官/免疫/PB 使用真实 stat block 和 slot level，免费版不伪造升环。
20. Dragon 共享先攻且紧随 caster slot，不独立掷骰；Action Surge 不增加其回合，Thief 两个 caster slot 的后置顺序确定。
21. 无口头指令时 Dragon Dodge 并移动避险；合法指令不消耗施法者动作，AI/GM 自动路径一致。
22. Multiattack 的 Rend 次数为 floor(level/2) 并使用 Breath；Rend 命中/伤害为官方公式，攻击加值来自施法者。
23. Breath Weapon 为真实 30 尺 Cone、施法者 DC、所选类型 2d6、成功半伤；缺区域系统时停止而非全场伤害。
24. Shared Resistance 只选五种合法类型之一，持续期间只减半一次；Dragon 消失后立即移除且不叠加。
25. 战斗中召唤原子插入位置/slot，战斗外召唤在下场战斗遵循同一独立或共享规则，多个召唤排序稳定。
26. 召唤物的 HP、条件、动作、死亡跳过、胜利判定和 feed 与普通 companion 一致，不变成玩家或敌人。
27. apply/replay、save/load 后 companion 记录、法术持续、专注、slot schedule、免费资源和长休状态一致。
28. 其他玩家不能操纵、解散、伪造或覆盖非本人召唤；GM 自动意图权限不扩大客户端权限。
29. Find Familiar/Find Steed/Summon Dragon 的既有 spell 校验、Wild Shape、F20 turn slot、治疗和装备回归通过。
30. 全量 rulesets、ruff、diff 通过；任何只扣资源而无真实 stat block/生命周期的实现均判失败。

## 7. 非目标与停止条件

### 7.1 非目标

- 其他召唤法术、通用怪物图鉴、第三方 Dragon Companion 19 级规则或新坐骑编辑器。
- 2D 布阵、完整战术 AI、前端召唤卡片改版或自定义 stat block。
- 让 Familiar 攻击、让 Dragon 独立掷先攻，或把无 Concentration 版本仍写成一小时。

### 7.2 停止条件

1. F25 未提供完整 CR0 Beast，或本切片新建的 p131/p166 Steed/Dragon 数据不完整；F5 不提供 stat block。Cone、装备或受控坐骑权威接口缺失时同样停止，不得生成空壳。
2. F25/F20 未提供 Wild Shape 支付和可表达独立/共享 linked slot 的 schedule。
3. party.companions 不能由事件原子创建/移除，或战斗与探索 reducer 必须双写不同生命周期。
4. 免费槽、材料例外、Concentration 修改或长休消失无法与现有 spell/rest 事件一致。
5. 核心测试 3–8、12–24 只能通过伪造 stat block、手写预期事件、客户端 HP 或修改白名单外文件通过。
6. 基线或并行分支已有同责召唤实现，存在 companion id、先攻、资源或专注双写。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_summons.py -q
python -m pytest tests/rulesets/test_dnd2024_wild_shape.py tests/rulesets/test_dnd2024_combat_start_features.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/spells/catalog.py src/rulesets/dnd2024/combat src/rulesets/dnd2024/exploration/engine.py src/rulesets/dnd2024/resting/engine.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_summons.py
git diff --check
git status --short
```

记录实际 Python 路径、F25 Beast 与新建 Steed/Dragon 数据版本、F5 免费槽接口和测试 1–30 映射，单独展示 Familiar 禁攻击、Steed 缩放/替换、Dragon 紧随/免专注和 18 级授予证据。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、召唤记录、事件和 turn slot schema。
2. 展示三种生命周期的创建、行动、受伤、消失、重施、长休、存档和重放证据。
3. 报告 RNG、资源原子性、材料/槽/专注修改、区域与装备完整性。
4. 不得把第三方 19 级、假 stat block、Familiar 攻击或独立 Dragon 先攻写成已实现。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- F25 Beast、新建 Steed/Dragon 数据与统一召唤记录：
- Wild Companion / Familiar 生命周期：
- Faithful Steed 数值、骑乘与 Life Bond：
- Dragon Companion 数值、先攻、专注与 18 级证据：
- 改动文件对照白名单：
- 测试 1–30 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档、权限与数据完整性证据：
- 与契约的偏差及原因：
