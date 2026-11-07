# S0-F8 任务书：三职业战斗风格与戏法替代选项

| 项 | 值 |
|---|---|
| 切片编号 | S0-F8（独立新任务，不覆盖旧 SP） |
| 验收载体 | Fighter/Paladin/Ranger `fighting_style`；Archery、Defense、GWF、TWF、Blessed Warrior、Druidic Warrior |
| 通用能力 | 风格选择、条件攻击/AC/伤害修正、Light 额外攻击、跨列表戏法与升级替换 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Fighter p47–48；Paladin p54；Ranger p58–59；Fighting Style feats p87–88 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-B、S0-F7 已合入；通用 feat/选项槽支持 owner、去重和升级替换，Weapon Mastery 可供组合回归 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树；不得只接受 feat id 或保留空参数就宣称风格完成。
2. Fighter 1、Paladin 2、Ranger 2 的三个 `fighting_style` owner 分开；不得重复实现 Champion `additional_fighting_style`。
3. Archery/Defense/GWF/TWF 必须有真实战斗或派生效果；Blessed/Druidic 必须有真实戏法来源与施法属性。
4. 任何已暴露但尚未实现效果的选项在构建/升级提交时以 `effect_not_implemented` 拒绝，整个选择事务不落地。
5. 所有攻击、AC、双手状态、Light 额外攻击与法术合法性由 canonical 装备和服务器结算决定。

## 2. 官方英文关键原文与语义边界

SRD p87–88：

> **Archery.** You gain a +2 bonus to attack rolls you make with Ranged weapons.

> **Defense.** While you’re wearing Light, Medium, or Heavy armor, you gain a +1 bonus to Armor Class.

> **Great Weapon Fighting.** When you roll damage for an attack you make with a Melee weapon that you are holding with two hands, you can treat any 1 or 2 on a damage die as a 3. The weapon must have the Two-Handed or Versatile property to gain this benefit.

> **Two-Weapon Fighting.** When you make an extra attack as a result of using a weapon that has the Light property, you can add your ability modifier to the damage of that attack if you aren’t already adding it to the damage.

SRD p54：

> **Blessed Warrior.** You learn two Cleric cantrips of your choice ... The chosen cantrips count as Paladin spells for you, and Charisma is your spellcasting ability for them.

SRD p59：

> **Druidic Warrior.** You learn two Druid cantrips of your choice ... The chosen cantrips count as Ranger spells for you, and Wisdom is your spellcasting ability for them.

两项替代选项均规定“whenever you gain [Paladin/Ranger] level, you can replace one ... with another [Cleric/Druid] cantrip”。语义边界：GWF 是把每个伤害骰的 1/2 视为 3，不是重掷；TWF 只覆盖 Light 产生的 extra attack，不给任意第二击加属性；戏法替代项不是额外 Fighting Style feat。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| Archery/Defense/GWF/TWF 有 feat 条目，但基线没有 `ranged_attack_bonus` 或对应 Fighting Style 测试，条目本身不证明行为完成 | `content/advancement_feat_catalogs/srd_advancement_feats.json` 的四个对应条目；`tests/rulesets/` 定点检索 |
| AC 由统一函数消费，Defense 必须进入推导而非只改展示 | `character/derivation.py::derive_armor_class` |
| 装备类型、武器 category/ranged/Light 由 canonical bundle 判定 | `features/equipment.py::Dnd2024EquipmentCatalog.from_bundle`、`weapon_matches_kind` |
| 攻击校验已有统一入口，但基线没有 `_validate_two_handed`；本切片须增加真实双手/Versatile 校验，不能信任客户端布尔值 | `combat/validation.py::_validate_attack` |
| 攻击与最终伤害提交有共享事件链，可保留原骰与 GWF 有效骰 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| 普通戏法/法术选择和升级由统一入口处理 | `spells/selection.py`；`progression/advancement.py::_update_spellcasting` |

## 4. 精确数据与行为契约

### 4.1 三身份与未实现选项

1. Fighter 1、Paladin 2、Ranger 2 分别建立 owner-scoped `fighting_style` 选择；普通选项必须为满足 Fighting Style Feature 前提的合法 feat，重复不可重复 feat 按 S0-B 拒绝。
2. Champion 的 `additional_fighting_style` 使用自己的 owner/choice_id，不复用或覆盖本切片三职业槽，也不在本任务新增第二套实现。
3. Archery/Defense/GWF/TWF 只有全部效果接入后才可接受；catalog 若暴露其他无执行器风格，选择验证返回稳定 `effect_not_implemented`，角色、法术与 feat 状态均不改变。
4. Blessed Warrior 仅作为 Paladin Fighting Style 的替代选项，Druidic Warrior 仅作为 Ranger 替代；二者不是 feat，不与普通 feat 同时占同一个选择，也不让 Fighter 选择。

### 4.2 四个战斗风格

1. **Archery**：仅使用 Ranged weapon 的攻击 roll 加 2；不加伤害，不因投掷 Melee weapon 或手持 Ranged weapon 做近战而获得。
2. **Defense**：实际穿着 Light/Medium/Heavy armor 时 AC 加 1；只持 Shield、无甲或装备未穿戴不加， armor 变化后立即由 `derive_armor_class` 重算。
3. **GWF**：以双手持用具有 Two-Handed 或 Versatile 的 Melee weapon 发动攻击时，该攻击的每个伤害骰原值 1/2 以 3 参与伤害；不是重掷、不消耗 RNG，不改攻击 d20 或属性调整值。
4. Versatile 单手持用不适用 GWF；仅 Heavy 而没有规定属性不适用；非该武器攻击的独立伤害不适用。该攻击附带的伤害骰若属于同一 damage roll 则逐骰适用。
5. 事件保留每个 `raw_die` 与 `effective_die`、来源和最终伤害；多个 GWF 来源不得把 1/2 先变 3 后再追加其他伪加成。
6. **TWF**：因 Light property 产生的 extra attack 若伤害尚未加本次所用属性调整值，则加入一次；已加（包括负值按 Light 原规则已加）不得重复。普通 Attack action 的第二次攻击、Extra Attack 或 Cleave 不因此自动获得。

### 4.3 戏法替代选项

1. Blessed Warrior 从 Cleric cantrip 列表真实学习两项不同戏法，计为 Paladin spells，施法属性 Charisma；Druidic Warrior 从 Druid 列表学习两项，计为 Ranger spells，施法属性 Wisdom。
2. 戏法不计入普通 1 环以上准备容量，不凭空授予 spell slots；其攻击/豁免 DC 使用对应新施法属性而非原列表属性。
3. 每次获得对应职业等级最多替换其中一项为同来源列表的合法 cantrip；不能一次升级换两项、跨列表或换成 1 环以上法术。
4. 替换只撤销自己的旧戏法来源，同名戏法若另有合法来源仍保留；无实际效果的戏法继续遵守通用 `not_automated` 施法边界。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`advancement_feat_catalogs/srd_advancement_feats.json`、`combat_catalogs/srd_combat_core.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/character/builder.py`、`character/derivation.py`、`progression/advancement.py`、`spells/selection.py`。
3. `src/rulesets/dnd2024/features/resolver.py`、`features/equipment.py`。
4. `src/rulesets/dnd2024/combat/check_modes.py`、`resolution.py`、`validation.py`、`reducer.py`、`view.py`。
5. 新建 `tests/rulesets/test_dnd2024_fighting_style.py` 与 `tests/rulesets/test_dnd2024_warrior_cantrips.py`。
6. 需要实现其他风格、改动 Nick 私有逻辑或白名单外法术效果时停止。

## 6. 编号测试矩阵

1. Fighter1/Paladin2/Ranger2 的 owner 与选择数量正确，低等级/错误职业不可提交。
2. 三身份互不串槽，Champion `additional_fighting_style` 不覆盖也不复制主 `fighting_style`。
3. 未实现风格返回 `effect_not_implemented`， feat、戏法和角色状态均原子不变。
4. Archery 只给 Ranged weapon 攻击 +2，不加伤害；Melee/投掷 Melee/近战使用 Ranged weapon 均不滥用。
5. Defense 穿 Light/Medium/Heavy 时 AC +1，脱甲、只持盾或仅携带未穿 armor 时不加。
6. Defense 与无甲/其他 AC 候选由统一推导处理，装备变化和重建后结果一致。
7. GWF 双手 Two-Handed 与双手 Versatile 均把伤害骰 1/2 视为 3，3–骰面最大值不变。
8. GWF 不重掷且不请求额外 RNG；事件同时保留 raw/effective 骰和最终伤害。
9. GWF 对同一攻击 damage roll 的每个合法伤害骰逐骰生效，不改攻击骰或属性调整值。
10. Versatile 单手、无规定属性武器、非攻击独立伤害均不获得 GWF。
11. TWF 对 Light extra attack 尚未加入的属性调整值加一次，正负值均不重复。
12. TWF 不强化普通 Extra Attack、任意第二击或 Cleave；Light 原有攻击条件仍须满足。
13. Blessed Warrior 仅 Paladin 可选，恰两项不同 Cleric cantrips，计为 Paladin 且用 Charisma。
14. Druidic Warrior 仅 Ranger 可选，恰两项不同 Druid cantrips，计为 Ranger 且用 Wisdom。
15. 两替代项不是 feat、不与同槽普通风格并存、不授予法术位或占普通准备容量。
16. 戏法攻击与 save DC 分别使用 Cha/Wis；原 Cleric/Druid 的 Wis 不错误沿用。
17. 每次对应职业升级最多替换一项同列表 cantrip；跨列表、高环、重复或一次换两项拒绝。
18. 替换只撤销自己的来源，其他来源提供的同名戏法保留，旧施法属性不残留。
19. 无执行器戏法保持 `not_automated`，施放拒绝不扣资源；已有自动化戏法正常结算。
20. 非法双手声明、伪造装备属性、客户端伤害骰或 AC 加值均被服务端拒绝。
21. 升级、存档/加载与历史重建后 feat 选择、AC、戏法来源和施法属性一致。
22. GWF/TWF 与 Weapon Mastery 同攻时按各自条件结算，不重复属性或改写 mastery 原骰。
23. Archery/Bless、Weapon Mastery、战斗、法术选择和升级的组合及既有回归通过。
24. 全部新增测试同时核验事件与最终状态，不能仅以 catalog/角色字段存在通过。

## 7. 非目标与停止条件

### 非目标

- 其他 Fighting Style feats、Champion Additional Fighting Style 新实现、完整双持/Weapon Mastery 重写。
- 新戏法效果、法术位授予、2014 版 GWF 重掷或任意双手伤害加成。
- 接受空效果选项后再以文案补验收，或由前端计算风格数值。

### 停止条件

1. S0-B 未合入，或 owner/choice 无法区分三职业与 additional 风格。
2. 双手、Light extra attack 或 armor 穿着状态无法由 canonical 装备判定。
3. 戏法来源/施法属性无法进入真实攻击、豁免 DC 与升级替换。
4. 需要白名单外文件，或仍有已接受但无效果的选项。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_fighting_style.py tests/rulesets/test_dnd2024_warrior_cantrips.py -q
python -m pytest tests/rulesets/test_dnd2024_weapon_mastery.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_advancement_access.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/character src/rulesets/dnd2024/progression src/rulesets/dnd2024/spells src/rulesets/dnd2024/features src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_fighting_style.py tests/rulesets/test_dnd2024_warrior_cantrips.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 三 owner 与 additional 风格隔离：
- Archery/Defense 条件与派生证据：
- GWF 原骰视 3 与 TWF 属性加值：
- Blessed/Druidic 来源、属性与升级替换：
- `effect_not_implemented` 原子拒绝：
- 白名单文件对照：
- 测试 1–24 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重建与重放证据：
- 偏差、停止原因与未决事项：
