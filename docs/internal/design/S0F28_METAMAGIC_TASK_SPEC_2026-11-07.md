# S0-F28 任务书：Innate Sorcery、Metamagic 十选项与 Sorcerer 高阶能力

| 项 | 值 |
|---|---|
| 切片编号 | S0-F28（独立新任务） |
| 验收载体 | `innate_sorcery`、`metamagic`、`sorcery_incarnate`、`arcane_apotheosis` 与十个 Metamagic 选项 |
| 规则基准 | 官方 SRD 5.2.1，CC-BY-4.0；Sorcerer p65–67，Counterspell p120 |
| 官方文件 | `C:/Users/Administrator/AppData/Local/Temp/SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从全部前置合入后的实际提交开工 |
| 前置任务 | F2、F3、F6、F16、S0-B、D2 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与执行纪律

1. 只实现 Sorcerer 1/2/7/20 级四个特性、Metamagic 在 2/10/17 级的 2/2/2 个新增选项，以及每级替换一个已知选项。
2. 使用 S0-B 的 owner-scoped 多选/替换主干、F2 的 Sorcery Points、F3 的 Sorcerer 施法、F6 的掷骰决策窗、F16 的持续时间与 D2 的资源纪律；不得另建并行选项或资源系统。
3. 十个选项必须逐项产生真实规则行为。仅显示名称、只扣 Sorcery Points 或只修改文案均不算完成。
4. 不读取旧 SP、2014 规则或第三方资料扩张需求； canonical id、事件字段和 source_ref 不本地化。

## 2. 官方英文关键原文与页码

SRD p65–66：

> **Level 1: Innate Sorcery** — As a Bonus Action, you can unleash that magic for 1 minute, during which you gain the following benefits: The spell save DC of your Sorcerer spells increases by 1. You have Advantage on the attack rolls of Sorcerer spells you cast. You can use this feature twice, and you regain all expended uses of it when you finish a Long Rest.

> **Level 2: Metamagic** — You gain two Metamagic options of your choice … You can use only one Metamagic option on a spell when you cast it unless otherwise noted in one of those options. Whenever you gain a Sorcerer level, you can replace one of your Metamagic options with one you don’t know. You gain two more options at Sorcerer level 10 and two more at Sorcerer level 17.

SRD p66：

> **Level 7: Sorcery Incarnate** — If you have no uses of Innate Sorcery left, you can use it if you spend 2 Sorcery Points when you take the Bonus Action to activate it. In addition, while your Innate Sorcery feature is active, you can use up to two of your Metamagic options on each spell you cast.

> **Level 20: Arcane Apotheosis** — While your Innate Sorcery feature is active, you can use one Metamagic option on each of your turns without spending Sorcery Points on it.

Metamagic Options，SRD p66–67：

> **Careful Spell (Cost: 1 Sorcery Point).** Choose a number of those creatures up to your Charisma modifier (minimum of one creature). A chosen creature automatically succeeds on its saving throw against the spell, and it takes no damage if it would normally take half damage on a successful save.

> **Distant Spell (Cost: 1 Sorcery Point).** When you cast a spell that has a range of at least 5 feet … double the spell’s range. Or … a range of Touch … make the spell’s range 30 feet.

> **Empowered Spell (Cost: 1 Sorcery Point).** When you roll damage for a spell … reroll a number of the damage dice up to your Charisma modifier (minimum of one), and you must use the new rolls. You can use Empowered Spell even if you’ve already used a different Metamagic option during the casting of the spell.

> **Extended Spell (Cost: 1 Sorcery Point).** When you cast a spell that has a duration of 1 minute or longer … double its duration to a maximum duration of 24 hours. If the affected spell requires Concentration, you have Advantage on any saving throw you make to maintain that Concentration.

> **Heightened Spell (Cost: 2 Sorcery Points).** When you cast a spell that forces a creature to make a saving throw … give one target of the spell Disadvantage on saves against the spell.

> **Quickened Spell (Cost: 2 Sorcery Points).** When you cast a spell that has a casting time of an action … change the casting time to a Bonus Action for this casting. You can’t modify a spell in this way if you’ve already cast a level 1+ spell on the current turn, nor can you cast a level 1+ spell on this turn after modifying a spell in this way.

> **Seeking Spell (Cost: 1 Sorcery Point).** If you make an attack roll for a spell and miss … reroll the d20, and you must use the new roll. You can use Seeking Spell even if you’ve already used a different Metamagic option during the casting of the spell.

> **Subtle Spell (Cost: 1 Sorcery Point).** You can spend 1 Sorcery Point to cast it without any Verbal, Somatic, or Material components, except Material components that are consumed by the spell or that have a cost specified in the spell.

> **Transmuted Spell (Cost: 1 Sorcery Point).** When you cast a spell that deals a type of damage from … Acid, Cold, Fire, Lightning, Poison, Thunder … change that damage type to one of the other listed types.

> **Twinned Spell (Cost: 1 Sorcery Point).** When you cast a spell, such as Charm Person, that can be cast with a higher-level spell slot to target an additional creature … increase the spell’s effective level by 1.

Counterspell 的触发条件，SRD p120：

> **Casting Time:** Reaction, which you take when you see a creature within 60 feet of yourself casting a spell with Verbal, Somatic, or Material components

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 基线目录只有 Sorcerer 的 `draconic_resilience`，没有本切片四个特性或十选项 | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` |
| 职业授予和数值查询集中在 Resolver；选项必须由前置扩展，而非客户端伪造 | `src/rulesets/dnd2024/features/resolver.py::features_for`、`feature_value`；`features/models.py::Dnd2024ClassFeatureCatalog._parse_feature` |
| 升级 preview/apply 分离，history 保存 choices/diff | `src/rulesets/dnd2024/progression/advancement.py::preview_next_level`、`apply_next_level` |
| 施法校验掌握真实 slot、目标数、动作经济与伤害选择 | `src/rulesets/dnd2024/combat/validation.py::_validate_spell` |
| 法术攻击、豁免、伤害、升环与多目标统一在 `_spell_events` | `src/rulesets/dnd2024/combat/resolution.py::_spell_events` |
| Counterspell 窗口当前按原法术是否含 V/S/M 决定，保存完整 cast intent | `src/rulesets/dnd2024/combat/reaction_spells.py::_counter_spell_window`、`_finish_counter_spell` |
| 长短休分别恢复职业资源和法术位；没有本切片状态 | `src/rulesets/dnd2024/resting/engine.py::complete_short_rest`、`complete_long_rest` |
| 攻击/法术事件由 reducer 机械落账；不可在重放时重掷 | `src/rulesets/dnd2024/combat/reducer.py::_reduce_event` |

## 4. 精确实现契约

### 4.1 授予、选项与资源

1. 四个主特性分别使用 `owner=class:sorcerer`、1/2/7/20 级和 `srd-5.2.1:p65–66` 对应 source_ref；十选项使用稳定的 owner-scoped option ref，不得全局短 id 冲突。
2. `innate_sorcery` 声明两次使用、Long Rest 全恢复、Bonus Action 和一分钟状态。激活后 Sorcerer 法术 DC +1、Sorcerer 法术攻击优势；非 Sorcerer 来源法术不受影响。
3. Metamagic 在 2 级选两个，10、17 级各再选两个；同一角色不可重复知道同一选项。每获得一个 Sorcerer 等级可替换恰好一个已知选项，也可不换；新选项必须尚未知道。
4. preview、apply、逐级升级、高等级历史重建、en/zh-CN 投影必须一致。替换只改选项所有权，不撤销已经发生的历史施法。
5. Sorcery Points 只能由服务端按选项真实费用支付；不足、重复支付、bool/负数/客户端提交余额均拒绝。Long Rest 恢复由 F2 负责，本切片不得复制公式。

### 4.2 施法声明、例外与免费次数

1. 服务端生成可用 Metamagic 声明，客户端只提交已拥有 option ref 及该选项需要的合法目标/骰索引/伤害类型。一次施法默认最多一个不同选项。
2. Empowered 与 Seeking 可在已用另一选项后加入；Sorcery Incarnate 仅在 Innate Sorcery 有效时把不同选项总上限提高为两个。任何组合都不得重复使用同一选项或绕过各选项触发时机。
3. Arcane Apotheosis 仅在 Innate Sorcery 有效且当前是持有者自己的回合时，使其中一个实际使用的选项费用为 0；每回合一次，用后记账，其他选项照常付费。它不把上限变成三个，也不免除其他资源。
4. 施法被 Counterspell 时，已声明的 Metamagic  Sorcery Points 不因“法术位不消耗”而自动退还；动作经济按 Quickened 后的真实 casting time 落账。重放不得再次扣点或掷骰。

### 4.3 十选项逐项行为

1. **Careful**：仅对迫使其他生物豁免的法术；选择 `1..max(1, Cha mod)` 个实际目标。被保护者自动成功，且若该法术原本成功减半伤害则改为 0；不抹去非伤害效果之外的规则，也不保护施法者，除非其本身是合法的其他目标语义。
2. **Distant**：Touch 改为 30 尺；其他原本至少 5 尺的范围翻倍。校验、available_intents 与结算读取同一有效范围，不能只改事件显示。
3. **Empowered**：伤害初次掷出后开启窗口；选择不超过 `max(1, Cha mod)` 枚实际伤害骰，逐枚重掷并强制采用新值。保留未选骰、固定加值、重击和抗性顺序，记录 before/after 与来源。
4. **Extended**：仅一分钟及以上法术；持续时间在服务端翻倍且封顶 24 小时。若需 Concentration，持有者为维持该法术的 Concentration 所做豁免具有优势，而不是所有 Con 豁免。
5. **Heightened**：选一个实际目标，该目标对这次法术的所有豁免均劣势；逐目标记录来源，不影响其他目标，也不把劣势加到攻击或能力检定。
6. **Quickened**：仅原 casting time 为 Action 的法术改为 Bonus Action。本回合已经施放 1+ 环法术时不可声明；声明后本回合不得再施放 1+ 环法术，包括免费施法。戏法不受该附加限制。
7. **Seeking**：法术攻击 miss 后重掷该次 d20，必须使用新骰；保持原攻击加值、优劣势来源审计和命中规则，不对伤害骰或武器攻击开放。
8. **Subtle**：删除 V/S/M，但保留被消耗或标有价格的 M。以修改后的组件集合决定 Counterspell 是否可开窗口：无 V/S/M 时不可仅因“看见施法”反制；仍有例外 M 时仍可反制。
9. **Transmuted**：仅当原伤害类型属于六类列表；改为其余五类之一，作用于该次法术对应类型的全部伤害，再进入抗性/免疫结算。
10. **Twinned**：仅对具有“升环增加一个目标”声明的法术，把本次**有效法术等级**设为实际槽位等级 +1，并由既有升环规则计算目标和所有有效等级效果。不是免费复制目标，不是固定多选一个，不消耗更高真实槽位；禁止沿用作废的 2014 双目标规则。

### 4.4 Sorcery Incarnate 与状态持久化

1. Innate Sorcery 剩余次数为 0 时，7 级持有者可额外支付 2 Sorcery Points 激活；次数大于 0 时仍走普通激活，不多收 2 点。
2. 一分钟状态、每回合免费使用、资源与选择均进入 canonical/战斗状态并支持存档、加载、历史重建和 apply/replay。结束、到期或长休不得留下 DC/优势或免费权限。
3. 所有效果必须同时经过 available_intents/validation/resolution/reducer；UI 只消费服务端结果，不自算价格、范围或有效等级。

## 5. 改动白名单

仅允许：

1. canonical class feature catalog 及 en/zh-CN 对应 locale。
2. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` 与 `rest_catalogs/srd_recovery.json` 中本切片必要声明。
3. `src/rulesets/dnd2024/features/models.py`、`features/resolver.py`、`progression/advancement.py`：仅接入前置多选/替换及本特性查询。
4. `src/rulesets/dnd2024/combat/view.py`、`validation.py`、`resolution.py`、`check_windows.py`、`reaction_spells.py`、`reducer.py`、`engine.py`。
5. `src/rulesets/dnd2024/resting/engine.py`：仅本切片状态/资源恢复接线。
6. 新建 `tests/rulesets/test_dnd2024_metamagic.py`；必要时仅追加 `test_dnd2024_counterspell.py`、`test_dnd2024_resting.py` 的本切片回归。
7. 本任务书仅追加执行报告。不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. 真实 Sorcerer 1 级获得 Innate Sorcery；2/10/17 级选项总数分别为 2/4/6；其他职业不能伪造。
2. 每级可替换一个未知重复选项；不换、重复知道、替换多个、错误 owner、跳级提交均按契约处理，历史重建一致。
3. Innate 激活消耗 Bonus Action 和一次使用，一分钟内 Sorcerer DC +1、法术攻击优势；非 Sorcerer 法术不变。
4. 两次后普通激活不可用；短休不恢复，长休恢复两次；7 级零次数时可付 2 SP 激活，有点数时不多扣。
5. Careful 在 Cha mod 为负/0/正时分别允许至少一个/相应数量目标；自动成功且 half-on-success 伤害为 0，其他目标不变。
6. Distant 对 Touch 得到 30 尺，对 5 尺以上范围翻倍；投影、验证、事件和实际距离结算一致，Self/0 尺不误用。
7. Empowered 在伤害窗口选择法定骰数，逐骰 before/after 强制采用新值；固定分量、重击和其他骰不变。
8. Extended 对不足一分钟拒绝；一分钟和一小时翻倍，超过 24 小时封顶；仅维持该法术的 Concentration 豁免优势。
9. Heightened 收 2 SP，仅选定目标对该次法术的每次豁免劣势；其他目标和其他效果不受影响。
10. Quickened 收 2 SP 并改 Bonus Action；已放 1+ 环法术后拒绝，用后禁止本回合再放 1+ 环法术，戏法仍合法。
11. Seeking 仅在法术攻击 miss 后开放，收 1 SP、重掷一个 d20、采用新值；命中或武器攻击不开放。
12. Subtle 对普通 V/S/M 法术不产生 Counterspell 窗口；保留消耗/ priced M 时仍产生；原法术与反制回归不变。
13. Transmuted 接受六种原类型并改为另外五类，全部相关伤害及抗性使用新类型；列表外伤害拒绝。
14. Twinned 对 Charm Person 等合法升环目标法术有效等级 +1，目标数和升环效果按该等级计算；真实槽位只扣原等级，非法术/无升环目标声明者拒绝。
15. 无 Incarnate 时第二选项拒绝；激活后允许两个不同选项；Empowered/Seeking 例外不复制同一选项。
16. 20 级激活 Innate 后自己的每回合首个实际使用选项免费，第二个付费；他人回合、状态结束和下一回合边界正确。
17. Counterspell 成功/失败均不重复扣点或重掷；Metamagic 点数不退，真实法术位遵循既有反制规则。
18. 所有十选项均验证点数不足、未拥有、非选项输入、非法字段和重复提交 fail closed。
19. 每批事件 apply/replay、save/load 后资源、条件、目标、范围、伤害和每回合免费计数一致，reducer 零 RNG。
20. 中英文标签、source_ref、升级历史、可用意图、战斗/探索排除面及既有 Counterspell、Overchannel、施法和 rest 测试通过。

## 7. 非目标与停止条件

非目标：Font of Magic 本体、Sorcerous Restoration、Draconic 子职、其他职业超魔、2014 Twinned、多职业规则、前端美术、通用规则 DSL。

出现以下情况停止并报告，不得假实现：前置未合入或接口不符；真实施法来源无法区分 Sorcerer；有效等级需要客户端数值才能成立；决策窗无法保存原始骰与 Metamagic 身份；组件过滤无法与 Counterspell 共用同一事实；需要白名单外系统或修改索引才能完成；测试 5–16 中任一选项只能靠文案或手造事件通过。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_metamagic.py -q
python -m pytest tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_resting.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features src/rulesets/dnd2024/combat src/rulesets/dnd2024/progression/advancement.py src/rulesets/dnd2024/resting/engine.py tests/rulesets/test_dnd2024_metamagic.py
git diff --check
```

使用项目实际 Python 解释器并记录路径；不得删除或放松既有断言换绿。

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 实际基线 / 分支 / 最终提交：
- 四个主特性与十选项的授予、替换和中英文声明：
- 每选项实际行为、费用、事件与对应测试编号：
- Sorcery Incarnate 双选项与 Apotheosis 免费计数：
- Subtle×Counterspell、Twinned 有效等级与 RNG 证据：
- apply/replay、save/load、rest、reducer 与历史重建：
- 测试 1–20 逐项结果及全部验收命令输出：
- 改动文件白名单核对、设计偏差与未决事项：
