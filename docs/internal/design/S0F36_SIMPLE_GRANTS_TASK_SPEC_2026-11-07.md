# S0-F36 任务书：语言、熟练、额外戏法与常备法术授予

| 项 | 值 |
|---|---|
| 切片编号 | S0-F36（独立新任务） |
| 验收载体 | `primal_knowledge`、`divine_order`、`druidic`、`primal_order`、`deft_explorer`、`thieves_cant`、`bonus_proficiencies` |
| 规则基准 | 官方 SRD 5.2.1；p29、p35、p37、p42、p59、p62 正文 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | S0-B、F1、F4 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与完成定义

1. 七特性必须产生真实语言/技能/Expertise/护甲武器训练/额外 cantrip/检定加值/常备 Speak with Animals 投影与行为，而非只显示名称。
2. S0-B 提供 owner-scoped 选择，F1 提供熟练与语言推导，F4 提供常备法术；不在七处各写一套建卡或升级逻辑。
3. Druidic 隐藏信息属于探索/叙事边界：若无法自动识别阅读者、调查和魔法解读，必须明确 `guided`，但语言所有权与自动发现可测试部分不得虚构。
4. 指定 p41/p58/p61 主要是表头/相邻上下文；实际正文分别为 p42/p59/p62，以同一官方 PDF 为准。

## 2. 官方英文关键原文与页码

Barbarian，SRD p29：

> **Level 3: Primal Knowledge** — You gain proficiency in another skill of your choice from the skill list available to Barbarians at level 1. In addition, while your Rage is active … whenever you make an ability check using one of the following skills, you can make it as a Strength check even if it normally uses a different ability: Acrobatics, Intimidation, Perception, Stealth, or Survival.

Cleric，SRD p37：

> **Level 1: Divine Order** — **Protector.** … proficiency with Martial weapons and training with Heavy armor. **Thaumaturge.** You know one extra cantrip from the Cleric spell list. In addition … a bonus to your Intelligence (Arcana or Religion) checks. The bonus equals your Wisdom modifier (minimum of +1).

Druid，SRD p42：

> **Level 1: Druidic** — You know Druidic, the secret language of Druids. … you always have the Speak with Animals spell prepared. You can use Druidic to leave hidden messages. You and others who know Druidic automatically spot such a message. Others spot the message’s presence with a successful DC 15 Intelligence (Investigation) check but can’t decipher it without magic.

> **Level 1: Primal Order** — **Magician.** You know one extra cantrip from the Druid spell list. In addition … a bonus to your Intelligence (Arcana or Nature) checks. The bonus equals your Wisdom modifier (minimum bonus of +1). **Warden.** … proficiency with Martial weapons and training with Medium armor.

Ranger / Rogue / College of Lore：

> **Level 2: Deft Explorer** (SRD p59) — **Expertise.** Choose one of your skill proficiencies with which you lack Expertise. You gain Expertise in that skill. **Languages.** You know two languages of your choice from the language tables in “Character Creation.”

> **Level 1: Thieves’ Cant** (SRD p62) — You know Thieves’ Cant and one other language of your choice, which you choose from the language tables in “Character Creation.”

> **Level 3: Bonus Proficiencies** (SRD p35) — You gain proficiency with three skills of your choice.

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 七特性在基线 canonical feature catalog 中均未完整声明 | `content/class_feature_catalogs/srd_class_features.json` |
| builder 汇总 background/class/species/Skilled skills，并计算 skill_values | `character/derivation.py::CharacterDerivationMixin.derive` |
| 基线推导会汇总 class `armor_proficiencies`，但没有独立的 `derive_armor_training_state`；本切片须把新增训练接入同一推导与施法校验，不能虚构既有统一函数 | `character/derivation.py::CharacterDerivationMixin.derive`；`combat/validation.py::_validate_spell` |
| 武器类别熟练进入 actor profile 和攻击命中 | `combat/view.py::_player_view`；`combat/resolution.py::_attack_events` |
| 法术选择已有 cantrip capacity、prepared 与列表归属校验 | `spells/selection.py::requirements`、`validate`、`configure` |
| 特性 grant 可注入 spell_refs，常备法术需复用该边界 | `features/resolver.py::actor_projection`；`combat/view.py::_player_view` |
| 探索施法另有 spell_refs，不能只改战斗投影 | `exploration/engine.py::_spell_refs` |
| Rage 状态和 D20 来源已有公共路径 | `combat/check_modes.py`；`combat/resolution.py`；`character/derivation.py` |
| 基线语言目录不能证明 Druidic/Thieves’ Cant 已有完整秘密语言行为 | `content/languages/` 与探索实现；需逐项验收而非推断 |

## 4. 精确实现契约

### 4.1 选择与所有权共同规则

1. 七特性使用真实 class/subclass owner：Barbarian 3、Cleric 1、Druid 1、Ranger 2、Rogue 1、`subclass:college_of_lore` 3；source_ref 对应 §2。
2. 所有选择保存 canonical ref 和完整 owner/choice identity；preview/apply、builder、高等级历史重建、save/load、en/zh-CN 一致。
3. 已拥有技能、已有 Expertise、非法语言、错误职业列表、数量不足/过多和重复选择均拒绝；不得用客户端提交修正值代替推导。
4. 固定授予与选择授予分开审计；同一来源重复投影不叠加，不同来源合法叠加但同一技能熟练不翻倍。

### 4.2 技能、语言、Expertise 与装备训练

1. **Primal Knowledge**：从 Barbarian 1 级技能列表额外选择一个技能熟练。Rage 激活时，Acrobatics/Intimidation/Perception/Stealth/Survival 的 ability check 可改用 Strength；技能熟练保留，检定事件同时记录原技能、原属性与有效 Strength。Rage 未激活或其他技能不改。
2. **Bonus Proficiencies**：Lore Bard 3 级选择三个任意技能并真实加入 `proficiencies.skill_refs/skill_values`；不是只能从 Bard 列表，也不是 Expertise。
3. **Deft Explorer**：选择一个已熟练且尚无 Expertise 的技能获得 Expertise；再选择两个语言表中的不同语言。Expertise 只翻倍该技能 PB 部分，不加到无熟练技能。
4. **Thieves’ Cant**：固定知道 Thieves’ Cant，并另选一个语言表语言；固定秘密语言不得占用这个额外选择，也不能重复选同一语言获得效果。
5. **Divine Order: Protector** 授予 Martial weapons 与 Heavy armor；**Primal Order: Warden** 授予 Martial weapons 与 Medium armor。训练进入装备、攻击、AC/未受训后果和换装 reconciliation，不是标签。
6. 两 Order 各要求且只选一个选项；不可同时拥有两个，不在后续等级自动重选，除非前置明确提供改选规则。

### 4.3 额外戏法与检定加值

1. **Divine Order: Thaumaturge** 增加一个真实 Cleric cantrip 选择和已知数量；**Primal Order: Magician** 增加一个 Druid cantrip。额外戏法占扩展后的容量，不作为 leveled prepared，不错误增加法术位。
2. Thaumaturge 仅对 Intelligence (Arcana) 与 Intelligence (Religion) checks 加 `max(1, Wis modifier)`；Magician 仅对 Intelligence (Arcana) 与 Intelligence (Nature) checks 加同式。
3. 加值不适用于 Wisdom 版技能、攻击、豁免、伤害或其他技能；Wis 变化后即时重算，不与多个同名来源重复叠加。
4. 额外戏法的选择、替换和已知数量遵循各职业 Spellcasting 原文；本任务不发明 Order 选项的随意重选。

### 4.4 Druidic 与常备 Speak with Animals

1. Druidic 固定授予秘密语言 Druidic；Speak with Animals 始终 prepared，作为 Druid 法术，不占普通 prepared 数，战斗/探索投影一致。
2. 常备不等于免费：正常施法仍需动作/仪式时间、组件和槽位规则；本特性不增加免费次数，也不自动化法术未实现效果。
3. 知道 Druidic 的生物自动发现 Druidic 隐藏信息并可读；不知道者可用 DC 15 Intelligence (Investigation) 发现信息存在，但没有魔法不能解读。
4. 若探索没有隐藏信息实体/阅读权限/魔法判定点，本段标 `guided`，提供明确拒绝或引导状态；不得让所有玩家客户端直接看到秘密正文，也不得以一次普通 Investigation 代替解读魔法。

### 4.5 推导与持久化

1. 建卡、升级、装备变更、rest、战斗 actor 和探索 actor 均从 canonical 特性选择重新推导；不复制可漂移的数值快照。
2. 所有效果有 owner/source_ref；中英文只改显示，不改 ref、技能公式或法术所有权。
3. 非战斗秘密语言可 guided，但每个 guided 子能力必须在执行报告和 F37 审计中保持可见，不能因此把整行标 deterministic complete。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；必要的 canonical/en/zh-CN language entries；`features/models.py`、`features/resolver.py`；`character/builder.py`、`character/validation.py`、`character/derivation.py`、`character/reconciliation.py`；`progression/advancement.py`；`spells/selection.py`；`combat/view.py`、`validation.py`、`resolution.py`、`check_modes.py`；`exploration/engine.py`；`resting/engine.py` 的投影接线；新建 `tests/rulesets/test_dnd2024_simple_grants.py`；本任务书仅追加执行报告。

不得改 reducer 来伪造静态熟练，不改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. 七特性真实职业/子职与等级授予、非 owner 排除、en/zh-CN/source_ref 和历史重建正确。
2. Primal Knowledge 只允许 Barbarian 1 级列表额外一技能；重复/列表外/数量错误拒绝。
3. Rage 时五种指定技能可改为 Strength 且保留熟练；非 Rage、其他技能、攻击/豁免不被改写。
4. Divine Order 必须单选；Protector  Martial/Heavy 在攻击、穿甲和换装中生效，Thaumaturge 不获得这些训练。
5. Thaumaturge 多一个 Cleric cantrip，Arcana/Religion 的 Int 检查加 max(1,Wis)；其他检定不加，Wis 变化重算。
6. Primal Order 必须单选；Warden Martial/Medium 真实生效，Magician 不获得装备训练。
7. Magician 多一个 Druid cantrip，Arcana/Nature 的 Int 检查加 max(1,Wis)；与 Thaumaturge 范围不串。
8. 两额外戏法均校验职业列表、0 环、容量与替换，不增加 prepared/槽位；builder/advance 一致。
9. Deft Explorer 仅选已熟练且无 Expertise 的技能，PB 部分正确翻倍；另选两个合法不同语言。
10. Thieves’ Cant 固定秘密语言+另一个语言选择，额外选择不被固定项占用，重复和未知 ref 拒绝。
11. Bonus Proficiencies 恰三个任意技能，真实影响 skill_values；不是 Expertise，不与同来源重复叠加。
12. Druidic 固定语言与 Speak with Animals 常备在战斗/探索一致，不占普通准备数且仍按正常施法收费。
13. Druidic 阅读者自动发现/解读；非阅读者 DC15 Int(Investigation) 只发现存在，无魔法不能解读；缺系统时 guided 且秘密不泄露。
14. 所有效果在建卡、升级历史、装备 reconciliation、rest、save/load、中英文切换后一致，无客户端数值注入。
15. 既有 Rage、AC/护甲训练、武器攻击、法术选择、探索、Lore Bard 与 progression 回归通过。

## 7. 非目标与停止条件

非目标：其他职业基础特性、通用秘密信息系统、完整 Speak with Animals 效果、所有语言/专长、多职业和前端改版。

停止条件：S0-B/F1/F4 未合入；秘密语言会泄露给无权限客户端；额外戏法破坏准备数量；装备训练需复制第二套公式；guided 部分被要求伪称完整；需要白名单外文件。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_simple_grants.py -q
python -m pytest tests/rulesets/test_dnd2024_character_builder.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_spells.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_simple_grants.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 七特性逐项选择、投影和真实行为：
- Druidic 秘密信息 guided 边界与权限证据：
- 测试 1–15 逐项结果、历史/装备/rest/save/load：
- 白名单、验收输出、偏差与未决事项：
