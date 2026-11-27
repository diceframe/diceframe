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

## 10. 方案修订（2026-11-27，开工前）

原文写于 2026-11-07，基座是 `integration/dnd2024-srd@35653f81`。本节记录开工前的重新核对、范围裁剪与代码形状决定；**与正文冲突时以本节为准**，正文其余部分仍是契约。

### 10.1 语言：留在本片，它是四层而不是一件

**推翻初稿的裁剪决定。** 初稿曾把语言整体移出、登记为"缺口"，理由是它要动 `FeatureChoice`。那不是缺口——没人规定不能动，只是要按层做。语言回到本片范围内。

核实到的现状（2026-11-27）：

| 层 | 现状 |
|---|---|
| 内容 | 只有 **10 条标准语言**（common / common_sign_language / draconic / dwarvish / elvish / giant / gnomish / goblin / halfling / orc），**缺 7 条罕见语言与 2 条秘密语言** |
| `rarity` | 每条都写了 `rarity: "standard"`，但**代码从不读它**（`grep rarity` 在 `src/` 零命中）；`validation.py:249` 把**全部**语言条目当 Standard（`standard_refs` 不看 rarity） |
| 玩家侧 | canonical 存了 `proficiencies.language_refs`（`derivation.py:872`，与技能/工具并列），但 `_player_view` 不投影、角色卡不显示、无任何消费者 |
| 怪物侧 | `languages` 是**自由字符串**（`["Common", "Goblin"]`），`validation.py:758` 只校验"非空字符串、不重复、≤20 条、≤40 字"——**不校验它是不是已知语言**；与玩家的 `language:common` 无共同标识符，也不随 locale 翻译 |
| GM 侧 | 非战斗时拿不到 stat block（GAP-52，本片不做） |

本片做四层，顺序有依赖——**L2 不做，L3 做完也不能互通**（一边 ref 一边字符串）：

- **L1 语言表扩充 + `rarity` 生效**：补 7 条罕见语言（Abyssal / Celestial / Deep Speech / Infernal / Primordial / Sylvan / Undercommon）与 2 条秘密语言（Druidic / Thieves' Cant）；`standard_refs` 与建卡候选列表都按 `rarity == "standard"` 过滤；`rarity` 值域加装载期校验（`standard` / `rare` / `secret`）。
- **L2 标识符统一**：46 条带语言的怪物改成 `language:xxx`；`validation.py` 的语言校验从"非空字符串"改成"必须是已知语言 ref"；显示走 locale。
- **L3 授予通道**：`FeatureChoice` 加 `kind: "language"` / `category: "language"`；固定授予走 `language_sources(character) → {ref: feature_id}`（形状照 `saving_throw_proficiency_sources`）；两者并入 `proficiencies.language_refs` 并保留来源。
- **L4 展示**：`ProfessionalCharacterCenter.vue:762` 已经在渲染 `proficiencies.skill_refs`，语言是兄弟字段，加一行同样的标签（`refName()` 现成）。**战斗 actor view 不加**：语言没有战斗判定，且同属熟练的 `tool_refs` 也不在 actor view 里（`_player_view` 只带 `skill_refs` / `skill_values` / `weapon_category_refs`）。

**本片不做**：Druidic 的隐藏信息实体（原 §4.4.3，标 `guided`）、GM 非战斗查阅 stat block（GAP-52）、"玩家懂不懂这门外语"的判定函数——语言不导向机械化判定，GM 需要的是权威参考条目，不是结算。

原 §4.2.4（Thieves' Cant 的语言选择）、§4.2.5（Deft Explorer 的两个语言）、§4.4.1（Druidic 的固定语言 + 常备 Speak with Animals）因此**重新生效**，按上面的 L1–L4 落地；§4.4.3 保持移出。

### 10.2 语义分类：七项只是四类载体的实例

原文把七项当成七个独立特性写契约。重新核对后，它们落在四类既有载体上，**没有一类是这七项专有的**：

| 载体 | 既有消费者 | 本片消费者 | 状态 |
|---|---|---|---|
| **A. 互斥配置选择** | Hunter Defensive Tactics（#876）、Fighting Style、Metamagic | Divine Order、Primal Order | **载体已有**：`FeatureChoice(kind="feature_option")` + `feature_option_choice(character, feature_id, owner=)` |
| **B. 固定授予** | `saving_throw_proficiency_sources`（`resolver.py:460`） | Protector/Warden 的武器与护甲训练、Druidic 的常备法术 | **范式已有**，照抄一个同形查询 |
| **C. 技能/豁免检定修正** | 半熟练（`half_proficiency_bonus`）、d20 保底（`_skill_check_floor`） | Primal Knowledge 的 Rage 换属性、两个 Order 的 Int 加值 | **需要新建**，见 §10.3 |
| **D. 法术容量与常备** | `spells/selection.py` 的 `cantrip_count`、F4 的 spell_refs | Thaumaturge/Magician 的额外戏法 | 容量增量是唯一要加的点 |
| **E. 语言** | `language_refs` 字段已有、语言目录已有、`saving_throw_proficiency_sources` 是同形范式 | Druidic、Thieves' Cant（固定）、Deft Explorer（两选） | 见 §10.1：四层，L1/L2 是新增的地基 |

**A 的唯一决策是"选项分支放哪"**：Divine Order 的两个选项分别落在装备训练（character）、戏法容量（spells）、检定加值（combat），收不进一个模块。做法是载体共享、消费分别落在 owning module，但把"这个 option 授予什么"收进一处声明表（`{feature_id: {option_id: {...}}}`），不允许多个包各写一遍 `if option == "protector"`。

### 10.3 载体 C 的契约：修正列表 + 原语，计算在代码

**所有掷骰面的加值都是同一个形状**：`mod(有效属性) + 非属性成分`。核实结论（`derivation.py:466-479`、`saving_throws` 构造、`attack_bonus` 构造）：只有裸属性检定是单一成分，技能/豁免/攻击都是合并值——**其中技能恰恰是唯一已经有分解产物的**（`derive_skill_check` 返回 `ability_modifier` / `contribution` / `components`，`derivation.py:667` 只取了 `["total"]`）。

#### 10.3.1 存档存分解

`skill_values` 之外，推导同时输出**分解字段**（每个技能的 `ability_modifier` 与 `contribution`）与豁免的同形分解。旧存档缺这些字段时回退到 `合并值 - mod(默认属性)` 反推，且反推结果必须与分解一致（用例覆盖）。

不做"永久反推"的理由不是性能：`components` 是上游已经知道的权威产物，丢掉再反推等于让下游重推一遍；一旦某个面将来引入非线性项（例如"取较高者"的加值），反推会静默算错。

#### 10.3.2 修正声明（跟着特性走）

修正写在特性参数里，不在代码里写具体特性：

```
check_modifier:
  surface: ability_check | skill_check | saving_throw     # 本片只实现这三种
  scope:   {abilities: [...], skills: [...]}               # 可只给一边，也可两边同时给
  when:    {condition: raging}                             # 可选，缺省 = 总是生效
  replace: {ability: str}                                  # 可选
  add:     {source: <原语>, ...}                            # 可选
```

每个修正必须**至少给出 `replace` 或 `add` 之一**，且 `scope` 不能为空——否则是一条什么都不做的声明，装载期拒绝。

#### 10.3.3 加值原语（有限枚举，计算在代码）

内容是数据，不能执行函数；`(score-10)//2` 是规则函数，其权威必须在代码里。所以"加多少"由**原语 + 参数**表达：

| 原语 | 参数 | 说明 |
|---|---|---|
| `constant` | `value` | 固定整数 |
| `ability_modifier` | `ability`、`minimum?`、`maximum?` | 某属性的调整值，可夹下限/上限 |
| `proficiency_bonus` | `multiplier?`（1 或 2） | 熟练加值，可倍数 |

`minimum` 是**参数不是通用行为**：`max(1, Wis)` 是 Otherworldly Glamour 与 Thaumaturge 各自的原文，Elegant Courtier 没有。`class_level` 原语**不实现**——没有消费者，按"不靠猜测创造字段"留空。

装载期校验：未知原语、未知属性、空 scope、`minimum > maximum`、`replace.ability` 不在六项、`surface` 不在已实现集合——全部拒绝。

#### 10.3.4 有效属性优先于算术替换

修正解析的顺序是"**先定有效属性，再用它重算**"，不是在合并值上做"减原属性加新属性"。原因是攻击面的默认属性本身是动态选出来的（`resolution.py:208-226`：ranged→dex、finesse→str/dex 取高、`ability_choice`→选项里取高），减错一次就把 finesse 的加值算错。既有先例是 `derive_jump_distances(abilities, ability="dex")`（Second-Story Work 换属性算跳远），照它即可。

#### 10.3.5 为未在本片实现的同类语义留形状

以下两条**不在本片范围**，但 §10.3 的形状必须容纳它们，且不需要为它们改代码：

- **Otherworldly Glamour**（妖精游侠 3）：`surface: ability_check` + `scope.abilities: [cha]` + `add: {ability_modifier, wis, minimum: 1}`。它要求 `scope` 能**只按属性**限定——本片没有内容用它，但必须实现并有测试覆盖，否则将来接它仍要动代码。
- **Elegant Courtier**（5e 武士 7）：`surface: ability_check` + `scope.skills: [persuasion]` + `add: {ability_modifier, wis}`（无下限）。顺带证明 `minimum` 必须是可选参数。

**攻击与伤害面（刃契锁的魅上攻/伤）不进本片，也不进代码**：`surface` 的枚举**只实现 `ability_check` / `skill_check` / `saving_throw`**。原因是一条硬纪律——枚举值一旦存在，内容写了就会静默无效，那是"内容声明了但没人读"的假契约。攻击/伤害将来是"多一个 surface + 一个默认属性选择函数"，写在文档而不写在代码里。

### 10.4 白名单修订

原 §5 保留，追加：

1. `features/models.py` 的修正声明解析与校验（新结构，不改既有 `FeatureChoice` 的 kind）。
2. `character/derivation.py` 的分解输出与修正消费。
3. `combat/check_modes.py` 或等价的修正解析模块（新增）。
4. `combat/resolution.py` 的 `_ability_check_events`、`_skill_check_events`、`_saving_throw_check` 三处消费点。

追加语言相关（§10.1）：

5. `templates/rulesets/dnd2024_srd/content/languages/` 新增 9 条（7 罕见 + 2 秘密）与其 en / zh-CN locale。
6. `templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json`：46 条 `languages` 改为 ref。
7. `character/validation.py`（rarity 过滤、语言 ref 校验）、`combat/validation.py`（怪物语言校验）、`content/catalog.py`（语言查询与 rarity）。
8. `features/models.py` 的语言 kind、`features/resolver.py` 的 `language_sources`、`character/derivation.py` 的合并。
9. **前端**：`frontend-v2/src/features/rulesets/ProfessionalCharacterCenter.vue` 的熟练区加语言标签（仅此一处）。

**仍然不允许**：改 `reducer` 伪造静态熟练、改索引/旧 SP、把语言做成判定入口。

### 10.5 测试矩阵修订

原 §6 的 15 条按 §10.1 裁剪后，追加：

16. 分解字段与 `skill_values` 一致；旧存档缺分解时反推结果与分解相同（同一张卡两条路径对照）。
17. 修正声明的装载期拒绝：空 scope、未知原语、未知属性、`minimum > maximum`、`surface` 不在集合内、只有 scope 没有 replace/add。
18. `scope.abilities` 单独生效（无技能限定）——为 Otherworldly Glamour 留形状的证据。
19. `add` 的 `minimum` 可省略（Elegant Courtier 形态）且省略时不夹下限。
20. 同一技能同时命中"替换"与"加值"两条修正时，顺序为"先替换属性、再加值"，且结果与分开算一致。
21. 修正的作用域隔离：`surface` 不串（技能修正不影响豁免，反之亦然）；Rage 未激活时 Primal Knowledge 的替换不生效。

语言相关（§10.1）：

22. `rarity` 生效：建卡三选**只能**选 standard；罕见语言即使存在于目录也不出现在候选里；`rarity` 非法值在装载期被拒。
23. 9 条新语言有 en / zh-CN 显示名，且 ref 不随 locale 变化。
24. 46 条怪物语言全部是已知 ref；写一个不存在的 ref（如 `language:klingon`）在装载期被拒。
25. 怪物语言的显示走 locale：同一份内容在 en / zh-CN 下显示不同文字、ref 相同。
26. 固定授予：Druidic / Thieves' Cant 让 `language_refs` 真的多出对应 ref，且**不出现在建卡三选**里（秘密语言不能被当标准选）。
27. 可选授予：Deft Explorer 的两个语言必须是合法 ref、互不重复、不被固定项占用；数量不足/过多/重复/未知 ref 全部拒绝。
28. 玩家投影：`_player_view` **不**带语言（有断言防止它被顺手加进去），角色中心按 `proficiencies.language_refs` 渲染。
29. 来源保留：同一语言由特性与建卡同时给出时不重复出现，来源仍可追溯。

### 10.6 停止条件补充

7. 若"分解输出"需要改 `ruleset_character` 的存档形状且没有向后兼容读取路径 → 停止，先与维护者确认迁移口径。
8. 若实现 `scope.abilities` 必须改动 `skill_values` 的静态语义（把运行时修正烧进推导产物）→ 停止，那说明形状选错了。
9. 若怪物语言 ref 化需要改动 stat block 的既有字段名或存档形状（而不只是值的形式）→ 停止并先与维护者确认。
