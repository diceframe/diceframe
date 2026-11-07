# S0-F16 任务书：法术学派、职业与伤害类型修饰

| 项 | 值 |
|---|---|
| 切片编号 | S0-F16（独立新任务，不覆盖旧 SP） |
| 验收载体 | Evoker `potent_cantrip`、`sculpt_spells`、`empowered_evocation`；Draconic `elemental_affinity` |
| 通用能力 | 戏法未中/成功豁免半伤、Evocation 保护目标、单次伤害骰加属性、固定元素抗性 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-C、S0-D1、S0-D2、S0-F14 已合入；真实子职、法术来源和骰窗可用 |
| 建议分支 | `feat/dnd2024-s0f16-spell-modifiers` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从前置实际集成提交开工，复用真实 spell ref、学派、职业来源、骰窗和伤害事件。
2. 四项特性完整实现；只改文案、对所有法术一律加值或按法术名硬编码均不合格。
3. 法术目标集、可见性、豁免、伤害和抗性由服务端决定；客户端只能选择合法保护对象或是否应用加值。
4. Sculpt Spells 若需要当前不存在的 AoE 几何/区域成员模型，必须停止整个切片验收，不得用单目标列表冒充区域。

## 2. 官方英文关键原文与页码

SRD p82，Evoker 3 级：

> **Potent Cantrip.** Your damaging cantrips affect even creatures that avoid the brunt of the effect. When you cast a cantrip at a creature and you miss with the attack roll or the target succeeds on a saving throw against the cantrip, the target takes half the cantrip’s damage (if any) but suffers no additional effect from the cantrip.

SRD p82，Evoker 6 级：

> **Sculpt Spells.** When you cast an Evocation spell that affects other creatures that you can see, you can choose a number of them equal to 1 plus the spell’s level. The chosen creatures automatically succeed on their saving throws against the spell, and they take no damage if they would normally take half damage on a successful save.

SRD p82，Evoker 10 级：

> **Empowered Evocation.** Whenever you cast a Wizard spell from the Evocation school, you can add your Intelligence modifier to one damage roll of that spell.

SRD p70，Draconic 6 级：

> **Elemental Affinity.** Choose one of those types: Acid, Cold, Fire, Lightning, or Poison.

> You have Resistance to that damage type, and when you cast a spell that deals damage of that type, you can add your Charisma modifier to one damage roll of that spell.

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| 法术 catalog 提供 level、school、class、range、casting time 等元数据 | `spells/catalog.py::Dnd2024SpellCatalog.get` |
| 攻击法术与豁免法术分别经统一结算 | `combat/resolution.py::_spell_events` |
| spell ref、目标和施法成本在服务端验证 | `combat/validation.py::_validate_spell` |
| actor view 区分 class spell refs 与其他来源，并提供施法属性 | `combat/view.py::_spell_caster`、`_player_view` |
| 伤害类型、抗性和 half_on_success 已有声明先例 | `combat/resolution.py::_resist_damage`、`content/combat_catalogs/srd_combat_core.json` |
| 多目标法术有 target_count/repeat_targets，但不等于完整 AoE 几何 | `combat/view.py::_available_spells`、spell effect catalog |
| 特性真实子职授予与参数由 feature boundary 处理 | `features/resolver.py::features_for`、`has_feature` |
| S0-D2/F14 的骰窗和法术修饰接口是硬前置 | 前置实际执行结果；不得假定未核实函数名 |

## 4. 精确实现契约

### 4.1 共享过滤与事件审计

1. 增加四项 canonical declaration、owner/等级/source_ref/双语 locale；真实 Evoker/Draconic 子职分别授予。
2. 判定顺序固定为：实际施法者与特性 → spell level/cantrip → 实际法术职业来源 → school → 当前伤害类型 → 目标/伤害 roll 实例。
3. 所有修饰在事件中记录 feature_id、owner、spell_ref、属性加值和被修改的 roll；客户端不得提交加值、学派、抗性或豁免结果。
4. “one damage roll”以实际伤害掷骰实例为边界，不是每个目标、每束射线或每个伤害事件自动各加一次；多目标共享骰时也不得复制属性加值。

### 4.2 Potent Cantrip

1. 仅持有者施放的 damaging cantrip；非伤害戏法、1 环以上法术和其他人的戏法不适用。
2. 攻击未中或目标豁免成功时，该目标仍受原戏法伤害的 `floor(amount/2)`；伤害骰由服务端正常生成，未中不再因为 miss 而跳过掷骰。
3. 该分支不施加戏法的任何 additional effect，包括条件、位移、持续区域或命中 rider；只保留半伤。
4. 攻击命中或豁免失败走原完整效果；Evasion、抗性等后续合法规则仍可作用于其各自触发点，但不重复把同一分支减半。
5. 多目标戏法逐目标判断 miss/save，不能把对一个目标的半伤扩展到其他目标。

### 4.3 Sculpt Spells

1. 仅 Evocation spell，且只可选择“其他生物”中施法者可见、确实会被本次法术影响者；不能选择自己、未被区域覆盖者或不可见者。
2. 可选择至多 `1 + 实际 spell level` 个，cantrip 为 1；选择数不得超过权威受影响集合，重复 target id 不占多个名额。
3. 被选者对该法术的 saving throw 自动成功，不消耗 d20；若原效果成功豁免会半伤，则改为 0 伤害。
4. 自动成功只改变该法术的 save；原成功仍附带的其他效果不被无依据删除。未被保护者按原骰序和效果结算。
5. 开工门槛：必须存在服务端权威“本次 Evocation 实际影响哪些生物”的集合及可见性。若需要 Cone/Cube/Sphere/Emanation 几何才能确定，本任务停止；不得仅按施法 range、target_ids 或敌我列表近似。
6. 只有在前置已提供显式、可验收的受影响目标集时才可实现该子集；执行报告必须列出实际支持的 spell refs，未覆盖 AoE 不得声明整项完成。

### 4.4 Empowered Evocation 与 Elemental Affinity

1. Empowered 仅当该法术是实际 Wizard spell 且 school 为 Evocation 时可选；其他职业同源法术、非 Evocation、物品效果不加。
2. 选择后对这一次施法的**一个** damage roll 加当前 Intelligence modifier，允许负调整值；不加攻击、DC、治疗或每次后续伤害。
3. Elemental Affinity 在获得特性时从 Acid/Cold/Fire/Lightning/Poison 固定选择一种，存入 canonical owner-scoped 选择；不能每次休息/施法改选，Force/Thunder 等非法值拒绝。
4. 持有者持续获得该类型 Resistance，应用一次且不与其他同类抗性叠加；存档/升级不改变选择。
5. 施放任何实际造成所选类型伤害的 spell 时，可对一个对应该类型的 damage roll 加当前 Charisma modifier；不要求 Sorcerer spell，但不能把加值分配给法术的其他类型或武器攻击。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/resolution.py`、`validation.py`、`reducer.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`、`features/models.py`：仅参数和固定选择查询。
4. `src/rulesets/dnd2024/spells/catalog.py`：仅在现有元数据不足且可用通用字段补足时修改。
5. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要法术效果声明。
6. 新建 `tests/rulesets/test_dnd2024_spell_modifiers.py`；本任务书仅追加执行报告。
7. 新 AoE 地图、遭遇编辑器、前端和未列出的角色 schema 文件不在白名单。

## 6. 编号测试矩阵

以真实 Evoker/Draconic、真实 spell refs 和完整施法链测试；固定骰序，禁止伪造 school/class、受影响集合、豁免或伤害。

1. 四项特性在正确子职/等级授予，低一级与错误子职无效果，source_ref 和双语标签正确。
2. Potent Cantrip 攻击 miss 时仍掷正常伤害并半伤向下取整，命中保持全伤和原效果。
3. Potent Cantrip 目标 save 成功时半伤，失败保持原全伤；无伤害戏法和 1 环法术不触发。
4. Potent 半伤分支不附条件、位移、区域或其他 additional effect，事件能区分原结果与改写。
5. 多目标戏法分别按各目标 miss/save 判断，不共享错误结果，抗性/合法 Evasion 不重复减半。
6. Sculpt 只匹配 Evocation；非 Evocation、非施法者特性或无权威受影响集合时不提供。
7. 保护数精确为 1+spell level，0 环为 1；超额、重复、自己、不可见或不受影响目标均拒绝。
8. 被选者自动 save 成功且不消耗 d20；原成功半伤者改 0，未保护者 RNG 和伤害不变。
9. 原成功豁免仍附带的合法非伤害效果不被擅自移除；无 half-on-success 时不凭空获得额外免伤。
10. Sculpt 的 AoE 门槛有明确验收：无几何/成员模型时记录停止且无“已完成”声明；支持时逐个 spell ref 验证成员集。
11. Empowered 只对真实 Wizard+Evocation 的一次伤害骰加 Int；非 Wizard、非 Evocation、治疗/攻击/DC 不加。
12. 多束/多目标/多段伤害只加一次，不能每个 roll 或每个目标各加；负 Int 按真实值计算。
13. Elemental 固定选择仅五类，Force、Thunder、未知值、运行中改选和客户端伪造均拒绝。
14. 固定抗性在战斗、升级、休息和 save/load 后保持，选中类型只减半一次，其他类型不变。
15. 任意职业 spell 造成所选类型时可加 Cha 至一个对应伤害 roll；非所选类型、武器和非 spell 不加。
16. 混合类型法术的加值不串到另一类型；多 roll 仅一次，加值来源和目标可审计。
17. 同一法术同时符合 Empowered 与 Elemental 时按各自合法过滤和单次 roll 规则结算，不重复或误删来源。
18. 客户端伪造加值、保护目标、自动成功、抗性或 spell 来源均被服务端拒绝/忽略。
19. 可选加值/保护的 pending save/load、decline、重复 resolve 和过期 version 安全，无部分支付或伤害。
20. apply/replay 的 RNG、豁免、伤害、抗性和选择一致；feed 仅暴露白名单字段。
21. 非持有者、低等级角色及 S0-D2/F14 的既有修饰不回归，不错误叠加同类机制。
22. 既有法术、Scorching Ray、Overchannel、Hold Person、抗性、Monk/职业 rider 回归测试通过。

## 7. 非目标与停止条件

### 非目标

- 新 2D AoE 引擎、所有法术补全、通用元素转换、施法 UI 美化。
- 让 Empowered 每个伤害 roll 都加 Int，或让 Elemental 每回合/休息重选。
- 以敌我列表或施法 range 假装 Evocation 区域成员。

### 停止条件

1. 前置未合入，或无法判定实际 Wizard/Cleric 等法术来源与真实 school。
2. Sculpt 缺少权威 AoE 成员或可见性，需要新增白名单外几何模型。
3. 当前伤害事件无法标识“一次 damage roll”，多目标会复制加值。
4. Elemental 固定选择缺少 canonical 持久化边界，只能由客户端每次提供。
5. Potent 无法区分半伤与 additional effects，或需要按法术名硬编码。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_spell_modifiers.py -q
python -m pytest tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_scorching_ray.py tests/rulesets/test_dnd2024_spell_remediation.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features src/rulesets/dnd2024/spells/catalog.py tests/rulesets/test_dnd2024_spell_modifiers.py
git diff --check
```

## 9. 交付要求

1. 测试 1–22 逐项映射，提供学派/职业/类型三重过滤的正面与反例。
2. 对多 roll、多目标展示“一次伤害骰”证据，对 Potent 展示半伤且无附加效果。
3. Sculpt 报告权威目标集与实际支持 spell refs；若停止，整个切片不得标为完成。
4. 保存固定选择、抗性、RNG、存档重放和全量回归输出。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Potent Cantrip：
- Sculpt 目标集、可见性与支持范围：
- Empowered 单次 roll 过滤：
- Elemental 固定选择与抗性：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放与 RNG：
- 偏差、停止原因与未决事项：
