# S0-F7 任务书：武器精通身份、八词条与 Tactical Master

| 项 | 值 |
|---|---|
| 切片编号 | S0-F7（一份契约，可分 A/B 两个执行提交，不覆盖旧 SP） |
| 验收载体 | Barbarian/Fighter/Paladin/Ranger/Rogue `weapon_mastery`；Fighter `tactical_master` |
| 通用能力 | 武器 kind 选择、长休换选、熟练门控、Cleave/Graze/Nick/Push/Sap/Slow/Topple/Vex |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Barbarian p28–30；Fighter p47–49；Paladin p53–54；Ranger p57–59；Rogue p61–62；mastery/武器表 p90–92 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-B 已合入，可持久化 owner-scoped 武器 kind 多选与长休换选 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律与 A/B 提交边界

1. 从前置合入后的实际提交新建干净工作树；基线只有 progression 数量轨道、通用攻击与装备基础，没有 Weapon Mastery 处理器或八词条行为。本任务需要新建共享模块并完整落地，不能把目录中的同名法术条件误当作近似实现。
2. **提交 A**：五职业选择/数量/长休、真实武器熟练门控、Tactical Master，以及 Graze/Sap/Slow/Vex；四个词条都必须有行为测试，不得以 capability 或文案代替。
3. **提交 B**：实现 Cleave/Nick/Push/Topple 及全部交互，跑通八词条与总回归。A/B 可分别提交，但只有 B 完成后 S0-F7 才可验收。
4. 所有命中、未中、伤害、豁免、位置、动作经济与持续状态由服务端结算；客户端不得提交结果或伪造已选武器。
5. p91 武器表是唯一 weapon→mastery 映射依据，逐武器消费 canonical combat catalog，不另写一份易漂移的私有表。

## 2. 官方英文关键原文与语义边界

SRD p29、p48：

> **Barbarian Weapon Mastery.** ... two kinds of Simple or Martial Melee weapons ... Whenever you finish a Long Rest, you can practice weapon drills and change one of those weapon choices.

> **Fighter Weapon Mastery.** ... three kinds of Simple or Martial weapons ... Whenever you finish a Long Rest, you can practice weapon drills and change one of those weapon choices.

SRD p54、p58–59、p62 的 Paladin/Ranger/Rogue 均为两种有熟练的武器，并写明：

> Whenever you finish a Long Rest, you can change the kinds of weapons you chose.

SRD p48：

> **Tactical Master.** When you attack with a weapon whose mastery property you can use, you can replace that property with the Push, Sap, or Slow property for that attack.

SRD p90：

> **Cleave.** If you hit a creature with a melee attack roll using this weapon, you can make a melee attack roll with the weapon against a second creature within 5 feet of the first that is also within your reach ... don’t add your ability modifier to that damage unless that modifier is negative ... only once per turn.

> **Graze.** If your attack roll with this weapon misses a creature, you can deal damage ... equal to the ability modifier you used ... the damage can be increased only by increasing the ability modifier.

> **Nick.** When you make the extra attack of the Light property, you can make it as part of the Attack action instead of as a Bonus Action ... only once per turn.

> **Push.** If you hit a creature with this weapon, you can push the creature up to 10 feet straight away from yourself if it is Large or smaller.

> **Sap.** If you hit a creature ... that creature has Disadvantage on its next attack roll before the start of your next turn.

> **Slow.** If you hit ... and deal damage ... reduce its Speed by 10 feet until the start of your next turn ... the Speed reduction doesn’t exceed 10 feet.

> **Topple.** ... Constitution saving throw (DC 8 plus the ability modifier used to make the attack roll and your Proficiency Bonus). On a failed save, the creature has the Prone condition.

> **Vex.** If you hit ... and deal damage ... you have Advantage on your next attack roll against that creature before the end of your next turn.

语义边界：Slow/Vex 必须实际造成伤害，Sap/Push/Topple 只需命中；Tactical Master 是当次攻击的替换而非叠加。Graze 不掷武器伤害骰，负调整值不产生伤害。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 五职业 progression 已有 Weapon Mastery 数量轨道，但这只证明授予/数量声明存在 | `content/progression_catalogs/srd_classes.json` 的各职业 `weapon_mastery` track |
| canonical class feature catalog 尚无 `weapon_mastery` 或 `tactical_master` 定义，需在本切片补齐 | `content/class_feature_catalogs/srd_class_features.json` |
| 基线没有 `combat/weapon_masteries.py`，也没有 `_weapon_mastery_effect`、`_miss_mastery_events` 或八词条结算 | `src/rulesets/dnd2024/combat/` 文件清单与 `resolution.py` 定点检索；本切片新建共享模块，不虚构旧实现 |
| 攻击和最终伤害提交有真实共享入口，miss/hit 词条须分别接入并保留骰迹 | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| 武器/物品 canonical 类型及 Light 等属性来自 combat catalog 与 item 定义 | `features/equipment.py::Dnd2024EquipmentCatalog.from_bundle`、`weapon_matches_kind` |
| 既有 weapons profiles 尚无 mastery 映射，应补充同一 canonical 战斗目录而非新建平行 JSON | `content/combat_catalogs/srd_combat_core.json` 的 `weapons` |
| 长休与升级已有权威入口，可执行换选和数量 track | `resting/engine.py::complete_long_rest`；`progression/advancement.py::_update_derived` |

## 4. 精确数据与行为契约

### 4.1 五职业身份、选择与换选

1. 每个选择保存武器 canonical kind（如 `item:greataxe`，而非当前某一件装备实例）、owner 和 granted level；实际使用还必须当前持有该 kind、具有相应武器熟练且特性有效。
2. 数量 track：Barbarian 1/4/10 级为 2/3/4；Fighter 1/4/10/16 级为 3/4/5/6；Paladin、Ranger、Rogue 均为 2。Barbarian 只能选 Simple/Martial Melee，其他按各自官方武器类别与熟练限制。
3. Barbarian/Fighter 每次 Long Rest 最多改变一个既有选择；Paladin/Ranger/Rogue 每次长休可改变这些选择。选择数量不得重复，换选不得遗留旧 kind 的 mastery。
4. 五职业同名 `weapon_mastery` 以 owner 隔离，多职业时按实际规则和合法来源聚合，不得因一个全局布尔值让所有装备获得 mastery。
5. Tactical Master（Fighter 9）每次攻击可在原属性确实可用时，将原属性替换为 Push/Sap/Slow 之一；原属性不再触发，不能同时使用原属性与替换属性，也不能选择 Cleave/Graze/Nick/Topple/Vex。
6. 本切片不重写通用攻击次数，但必须回归五职业各自 owner 的 `extra_attack`、Fighter 的 `two_extra_attacks` 与 `three_extra_attacks`，确保 Weapon Mastery/Cleave/Nick 不增加普通 Extra Attack 次数，也不跨职业串用同一短 id。

### 4.2 八词条真实结算

1. **Cleave**：近战武器命中后，可对距首目标 5 尺内且在自己 reach 内的第二目标，用同一武器做一次近战攻击；第二目标伤害不加了正属性调整值，但负值仍加。每回合至多一次，不递归触发第二次 Cleave。
2. **Graze**：攻击未中时可造成等同本次攻击所用属性调整值的伤害，类型同武器；调整值小于等于 0 时伤害 0。除改变该属性调整值外不得增加，不附加武器骰、其他伤害 bonus 或 on-hit 词条。
3. **Nick**：Light 属性产生的 extra attack 改为 Attack action 的一部分而非 Bonus Action，每回合一次；不额外增加攻击次数，仍遵守 Light 的不同武器等原有限制。
4. **Push**：命中 Large 或更小目标时可将其直线远离 0–10 尺；无需伤害或豁免，Huge+ 不可，位置/障碍由权威状态验证。
5. **Sap**：命中即使伤害 0 也使目标下一次攻击在其下一个攻击时消费 Disadvantage；若未攻击则在攻击者下回合开始到期，不强化其他生物的攻击。
6. **Slow**：命中且实际造成伤害后，目标 Speed 减 10 至攻击者下回合开始；多个 Slow 来源同期总减益不超过 10，最低有效 Speed 不为负。
7. **Topple**：命中后由目标真实做 Constitution save，DC=`8+本次攻击属性调整值+PB`；失败才获得 Prone，成功不获得，事件保留 DC 分项和豁免骰。
8. **Vex**：命中且实际伤害后，攻击者对其下一回合结束前、针对同一目标的下一次攻击获得 Advantage；攻击后消费，不用于其他目标或无限延续。
9. 每词条的来源、目标、触发攻击 id、期限与消费状态均持久化；重复事件、存档/加载和重放不得重复伤害、位移、豁免或模式效果。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`、`combat_catalogs/srd_combat_core.json` 及对应 en、zh-CN locale。
2. 新建 `src/rulesets/dnd2024/combat/weapon_masteries.py`；修改同目录 `resolution.py`、`check_modes.py`、`primitives.py`、`validation.py`、`reducer.py`、`view.py`、`engine.py`。
3. `src/rulesets/dnd2024/features/equipment.py`、`features/resolver.py`、`progression/advancement.py`、`resting/engine.py`。
4. 新建 `tests/rulesets/test_dnd2024_weapon_mastery.py` 与 `tests/rulesets/test_dnd2024_tactical_master.py`。
5. 需要二维地图、完整 Light 重写或白名单外物品系统时停止，不用简化假结果通过。

## 6. 编号测试矩阵

1. 五职业在 1 级获得 owner-scoped Weapon Mastery，Fighter 9 获得 Tactical Master，错误 owner/等级不可用。
2. 五职业数量 track 精确，升级增加选择槽但不自动复制旧选择或突破上限。
3. Barbarian 只能选合法 Simple/Martial Melee；Rogue 等无熟练武器 kind 被选择/使用校验拒绝。
4. 只有当前装备、已选且有熟练的武器获得 mastery；未选同属性武器和徒手不获得。
5. Barbarian/Fighter 每长休只能换一个 kind；Paladin/Ranger/Rogue 可换选，旧 kind 立即失效。
6. p91 所有武器 profile 的 mastery 与官方表逐项一致，未知/缺失属性 fail closed。
7. Tactical Master 只换 Push/Sap/Slow，原属性不叠加；非法替换、未选武器和跨攻击持续均拒绝。
8. Cleave 首目标命中后可攻击合法第二目标，首目标未中、第二目标相同或超 5 尺/reach 不触发。
9. Cleave 第二击使用同一武器和真实攻击骰，正属性调整值不加、负值保留，每回合一次且不递归。
10. Graze 只在未中触发，伤害等同所用属性调整值且类型正确，无武器骰或其他增伤。
11. Graze 调整值 0/负数不造成伤害或治疗；命中时不触发 Graze。
12. Nick 将 Light extra attack 纳入 Attack action，不扣 Bonus Action，不增加额外攻击总数且每回合一次。
13. Nick 仍遵守 Light 的不同武器等限制；Tactical Master 替换 Nick 后不再获得其动作经济。
14. Push 命中 Large/Medium 等合法目标可直线远离至多 10 尺，Huge+、非法路径或超距拒绝。
15. Push 无需伤害；Tactical Push 与原 Push 不叠加成 20 尺。
16. Sap 命中但伤害 0 仍生效，只影响目标下一次攻击并在攻击者下回合开始到期。
17. Sap 的下一次攻击无论命中/未中都消费，不影响其他攻击者或其他生物的攻击。
18. Slow 必须实际伤害，Speed 恰减 10 至攻击者下回合开始；零伤害/未中不生效。
19. 多次 Slow 不累积超过 10，最低 Speed 不为负，其他速度 bonus 的派生仍正确。
20. Topple 使用真实 Constitution save 与 `8+攻击属性+PB` DC；失败 Prone、成功无 Prone。
21. Topple 无需伤害但需命中；DC 随攻击所用属性和 PB 正确变化。
22. Vex 必须实际伤害，只强化持有者对同一目标的下一次攻击，并在该攻击后消费。
23. Vex 不强化对其他目标的攻击，持续至攻击者下回合结束而非错误地在开始提前过期。
24. 八词条事件均含来源/攻击 id/目标/期限，重复 apply 不重复结算。
25. 存档/加载与重放后 Cleave 每回合、Nick 动作经济、Sap/Slow/Vex 状态一致。
26. 伪造武器 kind、客户端 DC/伤害/位置、非法第二目标均 fail closed 且状态不变。
27. A 提交后 Graze/Sap/Slow/Vex 与身份测试通过，B 提交后八词条及 Tactical 全部测试通过。
28. 既有 Light、战斗、休息、升级与装备测试无回归；五职业 `extra_attack` 及 Fighter `two_extra_attacks`/`three_extra_attacks` 次数不被词条改写，Two-Weapon Fighting 的组合回归由后续 F8 验收。

## 7. 非目标与停止条件

### 非目标

- 完整二维战场、所有武器通用属性、Brutal Strike、Studied Attacks 或战斗机动。
- 用“拥有 weapon_mastery”全局布尔值、文案或 `unimplemented` 占位冒充八词条。
- 让 Tactical Master 叠加原属性、任意替换为八词条或永久更换武器表。

### 停止条件

1. S0-B 未合入，或武器 kind/owner/数量与长休换选无法持久化。
2. 权威位置无法判定 Cleave 的 5 尺/reach 或 Push 的直线远离，需客户端声明。
3. Light extra attack 无法与 Nick 的动作经济保持同一结算链。
4. A/B 任一提交需要白名单外文件，或八词条中仍有占位却准备交付。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_weapon_mastery.py tests/rulesets/test_dnd2024_tactical_master.py -q
python -m pytest tests/rulesets/test_dnd2024_fighting_style.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_monk_equipment.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features src/rulesets/dnd2024/resting src/rulesets/dnd2024/progression tests/rulesets/test_dnd2024_weapon_mastery.py tests/rulesets/test_dnd2024_tactical_master.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / A 提交 / B 提交：
- 五职业身份、数量 track 与长休换选：
- p91 武器映射核对：
- Cleave/Graze/Nick/Push 证据：
- Sap/Slow/Topple/Vex 证据：
- Tactical Master 替换不叠加：
- 白名单文件对照：
- 测试 1–28 / A/B 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放与 RNG 证据：
- 偏差、停止原因与未决事项：
