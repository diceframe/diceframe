# S0-F23 任务书：武器增益、控制与 Smite 光环

| 项 | 值 |
|---|---|
| 切片编号 | S0-F23（独立新任务；不执行、不覆盖旧 SP） |
| 唯一验收载体 | `sacred_weapon`、`smite_of_protection`、`abjure_foes`、`sear_undead`；并回归 `paladins_smite` 的 Divine Smite 授予与施法 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Cleric p37-38、Paladin p55-57、Cover p179；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`（`35653f8149717c19d2a9b3333ef9cf28569793e7`） |
| 前置任务 | F13 条件免疫/抑制；F14 命中 rider 与伤害类型；F22 Channel 与 Turn Undead；S0-E2 Aura of Protection；无自动光照前置 |
| 建议分支 | `feat/dnd2024-s0f23-channel-control` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 从前置任务实际集成提交开工；共享 Channel、具体武器身份、Divine Smite cast 事件、Aura 和 Turn Undead 状态任一缺失即停止。
2. 四项特性必须完成攻击内激活、脱手结束、Half Cover、三选一控制及 Sear 伤害特例，不得只写 catalog。
3. F14 不提供光照系统。Sacred Weapon 的 20 尺 Bright Light 和额外 20 尺 Dim Light 只保留可审计 guided 事实；不得伪造视野/照明判定，需要自动判定时停止。
4. 不实现 Holy Nimbus、其他 Smite、其他光环或通用恐惧系统重构；仅使用官方 PDF，不采用第三方异常文本。
5. 所有攻击、豁免、目标、资源、装备和伤害结果由服务端权威生成，重放不得重复扣费。

## 2. 官方英文关键原文与页码

SRD 5.2.1 p56，Oath of Devotion：

> **Level 3: Sacred Weapon.** When you take the Attack action, you can expend one use of your Channel Divinity to imbue one Melee weapon that you are holding with positive energy. For 10 minutes or until you use this feature again, you add your Charisma modifier to attack rolls you make with that weapon (minimum bonus of +1), and each time you hit with it, you cause it to deal its normal damage type or Radiant damage.
>
> The weapon also emits Bright Light in a 20-foot radius and Dim Light 20 feet beyond that. You can end this effect early (no action required). This effect also ends if you aren’t carrying the weapon.

SRD 5.2.1 p57：

> **Level 15: Smite of Protection.** Whenever you cast Divine Smite, you and your allies have Half Cover while in your Aura of Protection. The aura has this benefit until the start of your next turn.

SRD 5.2.1 p55，Paladin：

> **Level 9: Abjure Foes.** As a Magic action, you can expend one use of this class’s Channel Divinity ... target a number of creatures equal to your Charisma modifier (minimum of one creature) that you can see within 60 feet ... Each target must succeed on a Wisdom saving throw or have the Frightened condition for 1 minute or until it takes any damage. While Frightened in this way, a target can do only one of the following on its turns: move, take an action, or take a Bonus Action.

SRD 5.2.1 p37，Cleric：

> **Level 5: Sear Undead.** Whenever you use Turn Undead, you can roll a number of d8s equal to your Wisdom modifier (minimum of 1d8) and add the rolls together. Each Undead that fails its saving throw against that use of Turn Undead takes Radiant damage equal to the roll’s total. This damage doesn’t end the turn effect.

SRD 5.2.1 p179，Cover：Half Cover 提供 +2 AC 与 +2 Dexterity saving throws；多个掩蔽只取最有利的一种。

## 3. 已核实的定点代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 四个 id 已在职业/子职表列出，canonical 定义尚不存在 | `content/progression_catalogs/srd_classes.json`；`content/subclass_catalogs/srd_subclasses.json`；class feature catalog |
| Paladin Channel 池已有短休+1/长休全恢复声明 | `content/rest_catalogs/srd_recovery.json`；`features/resources.py::from_bundle` |
| 武器攻击从真实 `weapon_ref` 构造 attack bonus、damage formula 和 damage type | `combat/resolution.py::_attack_events`、`_submit_hit_damage` |
| Divine Smite 是 `triggered_damage` 法术，施法/伤害有独立管线 | `content/combat_catalogs/srd_combat_core.json`；`combat/triggered_spells.py::_triggered_spell_events`、`_finish_hit_spell` |
| 条件元数据可携带伤害类型和期限，抗性统一只应用一次 | `combat/resolution.py::_resist_damage`；`combat/reducer.py::_reduce_event` |
| S0-E2 前置契约提供光环范围与条件免疫查询，但没有 Sacred/Smite 效果；具体签名以 E2 合入代码为准 | S0-E2 的 `combat/resolution.py` 光环辅助契约；`combat/check_modes.py::attack_sources` |
| 装备协调以 canonical ref 处理携带状态，不能用本地化名称绑定武器 | `character/reconciliation.py::reconcile`、`_project_equipped_refs` |
| Turn Undead 的基础状态、伤害结束和共享 Channel 由 F22 提供 | `combat/resolution.py` 的 F22 接入点；`combat/reducer.py::_apply_hp_change` |
| combat feed 对字段显式白名单，guided 光照不得冒充自动判定 | `runtime.py::_recent_combat_events` |

## 4. 精确行为与数据契约

### 4.1 Sacred Weapon 攻击动作内激活

1. Oath of Devotion 3 级，在一次真实 Attack action 的首个攻击意图中可声明 `activate_sacred_weapon=true`；武器必须是当前持有的一件 Melee weapon，目标 `weapon_ref` 与本次攻击一致。
2. 激活不是独立动作，也不能在 Dodge、移动、借机攻击或攻击结算完成后补开；同批次支付一次 Paladin Channel 和正常攻击成本。激活后即使首击未命中，增益仍持续。
3. 权威条件 `sacred_weapon` 绑定精确 `weapon_ref`、持有者、`bonus=max(1,Cha modifier)`、10 分钟到期和来源；不得以武器显示名或类别绑定，不能同时有两件生效。
4. 只有持有者用该 ref 的攻击骰增加上述 bonus；徒手、其他武器、其他角色持用同一名称武器均不受益。bonus 是独立攻击加值，不修改属性或熟练。
5. 每次用绑定武器命中时，可选择 `normal|radiant`；normal 保留武器原类型，radiant 只把该武器本次伤害类型改为 Radiant。选择影响整次该武器伤害，不复制额外骰，也不改变其他独立 rider 的既定类型。
6. 再次使用本特性先结束旧绑定再建立新绑定；无动作可主动提前结束；装备协调发现该 ref 不再 carrying 时立即移除，重新拾起不会自动恢复。
7. 10 分钟到期、再次使用、主动结束和脱手都生成审计事件并存档。光照字段仅 `bright_light_radius=20`、`dim_light_beyond=20`、`automation="guided"`；基线和 F14 均无光照查询，不得让其参与 AC、攻击、Hide 或感知。

### 4.2 Smite of Protection

1. Oath of Devotion 15 级在**每次真实 cast Divine Smite** 后触发，无论使用法术槽或 Paladin's Smite 免费施法；不是 Channel，不另扣资源。
2. 触发事件含 `source_spell_ref="spell:divine_smite"`、cast 批次 id 和 `feature_id`；即使后续伤害被抗性降低，只要法术确实施放就生效。未完成的 hit-spell 选择或施法被有效阻止时不触发。
3. 在持有者的 Aura of Protection 内，持有者和盟友获得 Half Cover，直到持有者下一回合开始；离开光环即失去，重新进入且效果未到期则恢复。
4. Half Cover 通过统一 AC 与 Dexterity save 查询提供 +2，不与 Three-Quarters/Total Cover 叠加；只取最高掩蔽。多个 Smite of Protection 实例不叠加，期限按最新合法施法刷新。
5. Aura 因 Incapacitated  inactive 时本效果也不提供掩蔽；S0-E2 的范围扩展和盟友判定继续生效，不在本切片复制一套光环半径。

### 4.3 Abjure Foes 的三选一控制

1. Paladin 9 级，Magic action+1 Channel；目标必须恰为 `max(1,Cha modifier)` 个由施法者选择的可见生物，每个在 60 尺内。少选、多选、重复、不可见或超距均整批拒绝。
2. 每目标分别进行 Wisdom save，DC 为 Paladin spell save DC；失败者获得 `abjure_foes` 来源的 Frightened，持续 1 分钟或直到其实际受到正数伤害，成功者无状态。
3. 该 Frightened 保留通用恐惧效果，并额外限制目标自己的回合：只能选择移动、执行一个 action、执行一个 Bonus Action 三者之一；不是 Incapacitated，不禁止反应，不强制移动。
4. 选择移动后可进行合法移动但不能动作/附赠动作；选择动作后不能附赠动作，也不能借 Action Surge 获得第二个动作来绕过限制。移动 0 尺仍算选择移动，不能借此兼做动作。
5. 任意正数伤害落地立即移除该目标 Abjure 状态；抗性减到 0 不算实际受伤。持续到期、伤害结束、存档和重放保持同一状态。
6. 与 Aura of Courage 等 Frightened immunity 交互时按统一条件免疫规则处理；被免疫目标不能仍保留“只三选一”的隐藏限制。

### 4.4 Sear Undead 与 Turn 特例

1. Cleric 5 级每次使用 Turn Undead 时可选择附加 Sear；选择后掷 `max(1,Wisdom modifier)` 个 d8 并求和，只掷一次总伤害。
2. 仅该次 Turn Undead 中 Wisdom save 失败的 Undead 承受此 Radiant damage；成功者和非 Undead 不受伤，不另做豁免或攻击。
3. 事件顺序为各目标 Turn save → 失败状态待提交 → Sear damage → Turn 状态最终保留，且每目标可审计 `turn_effect_preserved=true`。RNG 只在选择 Sear 时消耗对应 d8。
4. Sear damage **不触发** Turn Undead 的“takes any damage 即结束”；其他来源的后续正数伤害仍正常结束。Sear 自身仍处理抗性、HP、专注、击败和战斗结束。
5. Supreme Healing 不最大化 Sear，因为这是伤害而非治疗；Disciple/Blessed Healer 不触发。未选择 Sear 时 Turn 行为与 F22 完全一致，不消耗额外骰。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resolver.py`
5. `src/rulesets/dnd2024/combat/resolution.py`
6. `src/rulesets/dnd2024/combat/triggered_spells.py`
7. `src/rulesets/dnd2024/combat/check_modes.py`
8. `src/rulesets/dnd2024/combat/engine.py`
9. `src/rulesets/dnd2024/combat/reducer.py`
10. `src/rulesets/dnd2024/combat/validation.py`
11. `src/rulesets/dnd2024/combat/view.py`
12. `src/rulesets/dnd2024/character/reconciliation.py`：仅绑定武器脱手结束
13. `src/rulesets/dnd2024/runtime.py`
14. 新建 `tests/rulesets/test_dnd2024_channel_control.py`
15. 本任务书：只允许追加执行报告

不改 progression/subclass/rest/spell catalog、前端或通用照明系统；需要自动光照时停止并报告系统缺口，不得将其写成 F14 职责。

## 6. 编号测试矩阵

1. 四特性按 Devotion 3/15、Paladin 9、Cleric 5 真实授予；低一级、其他子职/职业无入口。
2. Sacred Weapon 只能在真实 Attack action 内激活，支付一次 Paladin Channel；非攻击动作和攻击后补开被拒绝。
3. 只能绑定当前持有的具体 Melee weapon；Ranged、未携带、其他角色武器和伪造 ref 均失败。
4. Cha +2/负数时攻击 bonus 分别为 +2/+1，仅绑定武器生效；命中与未命中均保持持续效果。
5. 每次命中可选原伤害类型或 Radiant，普通武器骰和其他 rider 不复制；重放保留选择。
6. 再次激活结束旧武器绑定；主动无动作结束、脱手立即结束、重新拾回不恢复，10 分钟准确到期。
7. 光照字段记录 20/20 guided；无自动光照系统时 AC、攻击、Hide 和感知结果完全不受其影响。
8. Smite of Protection 在槽施法和免费 Divine Smite 均触发，不扣 Channel；未实际 cast 不触发。
9. 光环内持有者和盟友 Half Cover，AC/Dex save +2；光环外、敌人和失能光环不适用。
10. Half Cover 与更高掩蔽不叠加，多个实例只取最高；下一回合开始准时结束，再次施法刷新。
11. Abjure Foes 为 Magic action+一次 Channel，目标数为 max(1,Cha)，全部可见且 60 尺内。
12. 少选、多选、重复目标、超距或不可见时零事件；恰好选足数量时多目标只扣一次 Channel。
13. 每目标 Wis save 独立，失败者 Frightened、成功者无状态；DC 使用 Paladin spell save DC。
14. 失败目标回合选择移动后不能动作/附赠动作，选择动作后不能移动/附赠动作，选择附赠动作同理。
15. Abjure 不是 Incapacitated，反应仍按通用规则；Action Surge 不能绕过三选一限制。
16. 正数伤害立即结束 Abjure；抗性至 0 不结束，1 分钟到期结束，其他目标状态互不影响。
17. Frightened immunity 目标不会留下隐藏三选一；Aura of Courage 等既有交互无回归。
18. Sear 在 Wis +3/-1 时分别掷 3d8/1d8，仅 Turn 失败 Undead 受该总 Radiant damage。
19. Sear 只对失败目标结算，成功者和非 Undead 无伤；未选择 Sear 不消耗 d8，Turn 基线不变。
20. Sear 伤害后 Frightened+Incapacitated 仍保留，后续其他正数伤害才结束 Turn 状态。
21. Sear 伤害正常经过抗性、HP、专注和击败；Supreme Healing/Disciple/Blessed Healer 不介入。
22. Channel 资源在 Sacred/Abjure 与 Divine Sense 等选项间共享，短休+1、长休全恢复，无免费重复。
23. 所有攻击、施法、控制和 Sear 批次 apply/replay 得到相同 RNG、HP、条件和事件顺序。
24. 存档加载后武器绑定、光环期限、Abjure/Turn 状态和脱手检测正确，重复投影无副作用。
25. Divine Smite、Aura of Protection/Devotion、Cutting Words、Uncanny Dodge、Rage 和装备协调回归通过。
26. 全量 rulesets、ruff、diff 通过；公共 feed 不暴露伪造光照自动判定或未许可字段。

## 7. 非目标与停止条件

### 7.1 非目标

- Holy Nimbus、其他 Smite、Turn Undead 本体、通用光照/视野实现。
- 前端武器拖放、完整恐惧 AI、2D 掩蔽编辑器或其他子职。
- 把 Sear 伤害误当作 Turn 状态结束原因，或把 Half Cover 叠成 +4。

### 7.2 停止条件

1. F13/F14/F22/S0-E2 未合入，或武器 ref、cast 事实、光环范围、Turn 特例接口不足。
2. 脱手不能在装备协调时原子结束绑定，或 10 分钟持续时间无权威表示。
3. Smite 施法被阻止时仍会触发，或 Half Cover 无法接入统一 AC/Dex save 查询。
4. Abjure 三选一必须依靠客户端自律，或 Sear 无法阻止 F22 的通用伤害结束钩子。
5. 自动光照被要求在白名单外实现；不得用 guided 字段冒充判定完成。
6. 核心测试需要伪造特性、事件、装备或目标数据，或基线已有并行双写。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_channel_control.py -q
python -m pytest tests/rulesets/test_dnd2024_channel_healing.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_paladin_auras.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat src/rulesets/dnd2024/character/reconciliation.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_channel_control.py
git diff --check
git status --short
```

记录实际 Python 路径和测试 1–26 映射，单独保存攻击内激活、脱手、Half Cover、三选一及 Sear 不结束 Turn 的证据。

## 9. 交付要求

1. 单提交/PR 只含本切片，报告真实基线、武器绑定与持续状态 schema。
2. 明确 guided 光照未自动化的边界，不得把该缺口隐藏为全功能完成。
3. 报告 Channel 原子性、施法触发顺序、RNG、重放/存档及装备协调结果。
4. 不得把 F22 或其他非目标重复计入本切片实现量。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Sacred Weapon 攻击、绑定、期限与 guided 光照：
- Smite of Protection 光环：
- Abjure Foes 三选一：
- Sear Undead 伤害与 Turn 特例：
- 改动文件对照白名单：
- 测试 1–26 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- RNG、重放、存档与装备证据：
- 与契约的偏差及原因：
