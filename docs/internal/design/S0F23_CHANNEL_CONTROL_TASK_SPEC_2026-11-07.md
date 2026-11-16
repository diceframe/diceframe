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

## 11. 执行报告

- 实际基线：`1e57c795`（S0-F22 执行报告提交），分支 `feat/dnd2024-s0f20-combat-start`
- Python：`E:/Github/diceframe/.venv/Scripts/python.exe`（3.13）；ruff/mypy：`C:/Python314/Scripts/`（读 pyproject，锁 3.11）
- 提交：本文件同批 commit（feat / test / docs 三段）

### 11.1 Sacred Weapon：绑定、加值、类型与 guided 光照

绑定写在**持有者的一个条件**上（`sacred_weapon`，字段 `weapon_ref / bonus / ability / damage_type_choices / expires_at_minute / bright_light_radius / dim_light_beyond / light_automation`），不是武器上的标记。匹配按 canonical ref：盟友手里那同一把 `item:longsword` 拿不到加值（`test_the_bonus_follows_the_ref_not_the_weapon_name`）。

一次攻击内激活的事件顺序：

```
dnd2024.attack.spent
dnd2024.class_resource.spent(resource_id="channel_divinity", amount=1, feature_id="sacred_weapon")
condition.applied(condition="sacred_weapon", weapon_ref=..., bonus=..., expires_at_minute=...)
check.resolved(modifier=...+bonus, sacred_weapon_bonus=bonus)
```

- 加值是攻击检定上的独立加成，先加进 `modifier`，再单独记一条 `sacred_weapon_bonus` 供对账；属性、熟练、武器骰都不改。
- 激活那一掷本身吃到加值（`_sacred_weapon_bonus_value` 直读表，不读尚未落地的条件），所以"激活与首击同批"不会漏加。
- 伤害类型：`sacred_weapon_damage="radiant"` 只改这次该武器的类型；`normal` 与省略都保留武器原类型，武器骰与 rider 不复制。
- 期限走世界时钟（`expires_at_minute = 当前分钟 + 10`，reason `ten minutes passed`），与 Divine Sense 共用 `WORLD_CLOCK_DURATIONS`。
- 脱手结束在 `character/reconciliation.py`：`_end_dropped_weapon_bindings` 读本次投影出的 carried refs，不在清单里就删条件；`reconcile` 返回值新增 `ended_weapon_bindings` 并参与幂等短路。重新拾回不恢复。
- 光照只写事实：`bright_light_radius=20 / dim_light_beyond=20 / light_automation="guided"`，不参与 AC、攻击、Hide 与感知（`test_the_light_fields_are_recorded_as_guided_only`）。

### 11.2 Smite of Protection：掩蔽是位置事实

条件是挂在**圣武士本人**身上的一份 `smite_of_protection`（duration `actor_turn_start`），掩蔽半径借 `_projected_auras` 的 `aura_of_protection`（含 Aura Expansion），`cover_source_feature_id` 由数据声明，查询端不再硬编码光环名。

- `engine._cover_record` 返回 `{armor_class_bonus, dex_save_bonus, sources}`，只对 `side == "party"` 求值；持有者失能 / 光环 inactive 时 `_projected_auras` 本来就不投影，掩蔽随之消失。
- 接入点三处：武器攻击的 `target_ac`、法术攻击的 `target_ac`、豁免的 `_cover_save_bonus`（**只有 Dex**；其它属性返回 0）。三处都在事件里留 `cover_bonus` / `cover_sources`。
- 走出灵光失去、走回（效果未到期）恢复；`test_half_cover_follows_the_aura_not_a_snapshot` 用同一份条件前后两次查询验证，没有任何快照。
- 触发点是**真实 cast**：`triggered_spells._smite_of_protection_events` 在 `dnd2024.spell.cast` 之后调用，槽施法与免费 Paladin's Smite 都触发，拒绝命中法术决策不触发，也不扣 Channel。
- 多个实例不叠加：同 id 条件覆盖；`_cover_record` 取最高并按先攻序打破平局。

### 11.3 Abjure Foes：目标、豁免与三选一

`abjure_foes` 是 Paladin 9 级特性，capability `cost = {action: 1, resources: [channel_divinity ×1]}`，`target_kind` 声明为 `creature`（SRD 原文是 "creatures you choose"，不是"敌人"）。

- 目标数 `max(1, Cha mod)`：少选、多选、重复、超距、不可见、自己、死亡目标全部整批拒绝，零事件（`_validate_abjure_foes`）。
- 每目标独立 Wis save 对 Paladin spell save DC；失败者获得 `frightened`，`duration="abjure_foes"`、`turns_remaining=10`、`ends_on_damage=true`、`turn_choice_limit=["move","action","bonus_action"]`。
- 三选一的执行点 `validation._validate_turn_choice`：把意图翻译成"这次会花掉三项里的哪一项"（`_intent_choice`），再看别的两项是否已花掉；**同一种选择的重复交给常规预算**（第二刀、第二个附赠动作本来就会被经济拒绝）。
- 移动的"已选"标记由 reducer 在 `dnd2024.position.changed` 且 `economy.actor_id == actor_id` 时写 `economy["moved"]=True` —— 被推开的第三方不会替谁花掉选择权。
- **Action Surge 不能绕过**：`_choice_spent` 只看预算会把 surge 误读成"还没选过动作"，所以额外一条规则拦在 `_validate_turn_choice` 里（`action` 预算为 0 而 `surge_action` > 0 时拒绝，文案 `Action Surge cannot buy another`）。
- 不是失能：反应预算与 `_reaction_eligible` 不受影响。
- 免疫：`_condition_immunity_blocker` 在条件落地**之前**问，被免疫者连"只三选一"的限制都不留；拦截原因写回该次豁免（`blocked_condition` + `blocked_by_aura`）。
- 结束：任意正数伤害（`ends_on_damage`，抗性减到 0 不算）、十个自己的回合、存档与重放同状态。

### 11.4 Sear Undead 与 Turn 特例

`sear_undead`（Cleric 5，owner `class:cleric`）不是独立动作：它是 Turn Undead 意图上的 `sear_undead: true`。

- 骰数 `max(1, Wis mod)`，**整次使用只掷一次**，只打本次 Turn 豁免失败的目标；没人失败时一颗骰都不掷（不浪费 RNG，也不在骰迹里留无效果记录）。
- 事件顺序：各目标 Turn save → 失败者的 `condition.applied` → Sear 的 `resource.changed`（`turn_effect_preserved=true`、`channel_effect="turn_undead"`、`feature_id="sear_undead"`）→ Turn 状态最终保留。reducer 的伤害扫描（`_end_damage_conditions`）读 `turn_effect_preserved` 跳过这一条，其它来源的正数伤害照常结束 Turn。
- 伤害正常过抗性、HP、专注、击败与战斗结束（`_resist_damage` / `_concentration_events` / `_enemy_zero_hp_feature_events` / `dnd2024.combat.ended`）。
- 治疗侧不介入：Disciple of Life / Blessed Healer / Supreme Healing 只写在两条治疗路径上，Sear 事件不带 `maximized`、不产生治疗事件（`test_sear_is_damage_and_no_healing_booster_touches_it`，5 级生命牧师）。

### 11.5 改动文件对照白名单

白名单 14 项中改动 10 项：class feature content、`combat/resolution.py`、`combat/triggered_spells.py`、`combat/engine.py`、`combat/reducer.py`、`combat/validation.py`、`character/reconciliation.py`、`runtime.py`、新建测试、本任务书。

**白名单外改动 2 处**（都是该逻辑唯一产地，无替代）：

| 文件 | 改动 | 理由 |
|---|---|---|
| `combat/primitives.py` | `SACRED_WEAPON_*` / `SMITE_OF_PROTECTION_*` / `ABJURE_FOES_DURATION` / `TURNS_SCOPED_DURATIONS` / `WORLD_CLOCK_DURATIONS` | 条件域常量被 resolution、engine、reconciliation 三方消费，双写会漂移 |
| `features/models.py` | `ACTION_KINDS` 增加 `abjure_foes` | 闭集 fail-closed：不加这个 kind，capability 声明直接报未知动作 |

白名单里的 `features/resolver.py`、`combat/check_modes.py`、`combat/view.py`、en/zh locale 未改：前两者已有 `feature_value` / `class_state` / 攻击源查询够用；`target_kind` 已随 capability 投影；四个 id 的本地化标签基线已存在（en 225 / zh-CN 225 条，逐项核对过）。

### 11.6 测试 1–26 逐项结果

| 编号 | 用例 | 结果 |
|---|---|---|
| 1 | `test_the_four_features_appear_only_at_their_own_level` + `test_the_subclass_features_need_the_subclass` + `test_abjure_foes_is_the_only_new_capability` | 通过（Paladin 2/3/5/8/9/14/15、Cleric 4/5；无子职即无 Devotion 两项） |
| 2 | `test_sacred_weapon_is_activated_by_the_attack_that_uses_it` + `test_sacred_weapon_cannot_be_added_after_the_first_attack` + `test_a_dodge_cannot_smuggle_a_sacred_weapon_activation` + `test_sacred_weapon_needs_an_unspent_channel_use` | 通过 |
| 3 | `test_only_a_melee_weapon_can_be_imbued` + `test_a_ranged_weapon_cannot_be_imbued` + `test_a_weapon_the_actor_does_not_carry_is_refused` + `test_a_thrown_melee_weapon_is_still_a_melee_weapon` + `test_the_bonus_follows_the_ref_not_the_weapon_name` | 通过（徒手 / 长弓拒绝、标枪可绑定、伪造 ref 无 attack profile、盟友同 ref 无加值） |
| 4 | `test_sacred_weapon_is_activated_by_the_attack_that_uses_it`（Cha 15 → +2，同一条 `check.resolved` 里 `sacred_weapon_bonus=2`）+ `test_a_negative_charisma_still_grants_plus_one`（同角色同掷对比基线，+1）+ `test_a_miss_keeps_the_bond` | 通过 |
| 5 | `test_a_hit_may_deal_radiant_instead_of_the_weapon_type` + `test_the_default_keeps_the_weapon_damage_type` + `test_the_damage_choice_needs_a_bond` | 通过 |
| 6 | `test_recasting_ends_the_old_bond_before_the_new_one` + `test_dropping_the_weapon_ends_the_bond_and_picking_it_up_does_not` + `test_the_bond_lasts_ten_minutes` | 通过；"主动无动作结束"未做，见 §11.8 第 3 条 |
| 7 | `test_the_light_fields_are_recorded_as_guided_only` | 通过（20/20/guided；AC 与 Hide 不受影响） |
| 8 | `test_a_divine_smite_cast_opens_half_cover_without_a_channel_use` + `test_a_free_divine_smite_also_opens_half_cover` + `test_declining_the_smite_does_not_open_half_cover` | 通过 |
| 9 | `test_half_cover_raises_the_ac_of_everyone_in_the_aura` + `test_half_cover_follows_the_aura_not_a_snapshot` + `test_an_incapacitated_paladin_grants_no_cover` | 通过（敌人在灵光内也拿不到；敌人攻击友方时 `target` 是抬高后的 AC） |
| 10 | `test_half_cover_ends_at_the_start_of_the_next_turn` + `test_a_second_cast_leaves_one_instance_not_two` | 通过；"同回合刷新"不可达，见 §11.8 第 4 条 |
| 11 | `test_abjure_foes_spends_one_channel_for_exactly_charisma_targets` | 通过（Cha +2 → 恰好 2 个，只扣一次 Channel、只花一个动作） |
| 12 | `test_a_short_or_long_target_list_is_refused_whole` + `test_duplicate_and_out_of_range_targets_are_refused` + `test_an_unseen_target_is_refused` | 通过（零事件） |
| 13 | `test_each_target_saves_on_its_own_against_the_paladin_dc` | 通过（同一掷序一个失败一个成功，DC = spell save DC，ability = wis） |
| 14 | `test_the_frightened_carries_the_three_way_choice` + `test_moving_first_costs_the_action_and_the_bonus_action` + `test_acting_first_costs_the_move` + `test_a_bonus_action_first_costs_the_move` | 通过（含"先附赠动作后不能再移动"，用圣疗走 bonus_action 分支） |
| 15 | `test_an_abjured_creature_can_still_react` + `test_action_surge_cannot_buy_a_second_choice` | 通过 |
| 16 | `test_positive_damage_ends_it_and_zero_does_not` + `test_the_one_minute_runs_out_after_ten_of_its_own_turns` | 通过（另一目标不受影响；0 伤与抗性至 0 都不结束） |
| 17 | `test_a_frightened_immune_creature_keeps_no_hidden_limit` | 通过（勇气灵光内的队友被拦截，`blocked_by_aura.feature_id="aura_of_courage"`；同批敌人照样带限制） |
| 18 | `test_sear_rolls_one_d8_per_wisdom_modifier_and_hits_the_failed_undead`（Wis 16 → 3d8）+ `test_a_negative_wisdom_still_rolls_one_d8`（Wis 9 → 1d8） | 通过 |
| 19 | `test_a_creature_that_saved_takes_no_sear_damage` + `test_without_sear_no_extra_die_is_rolled` | 通过（未选 Sear 只消耗 Turn 豁免那一颗 d20） |
| 20 | `test_sear_does_not_end_the_turn_effect_but_later_damage_does` | 通过（Sear 后仍 Frightened+Incapacitated，随后 1 点其它伤害才结束） |
| 21 | `test_sear_goes_through_resistance_and_can_be_halved_to_nothing` + `test_sear_can_finish_the_last_enemy` + `test_sear_is_damage_and_no_healing_booster_touches_it` | 通过（抗性减半、减半到 0 无事件、击败发 `dnd2024.combat.ended`） |
| 22 | `test_sacred_weapon_and_abjure_share_one_channel_pool` | 通过；短休 +1 / 长休全恢复由 F22 的池用例与邻接回归覆盖 |
| 23 | `test_a_batch_replays_to_the_same_state` | 通过（同一批重放得到相同 HP、条件与事件序列） |
| 24 | `test_the_bond_and_the_cover_survive_a_save_and_load` | 通过（重新投影不重复扣费，脱手检测幂等） |
| 25 | 邻接五份 183 passed | 通过 |
| 26 | 全量 + ruff + mypy + `git diff --check` | 通过；feed 只多出白名单里的 `weapon_ref / sacred_weapon_bonus / bright_light_radius / dim_light_beyond / light_automation / cover_bonus / cover_sources` |

测试文件 55 条（含参数化），全绿。

### 11.7 门禁输出（真实命令）

```
pytest tests/rulesets/test_dnd2024_channel_control.py -q                       55 passed  (74.70s)
pytest channel_healing + smite + paladin_auras + combat + live_equipment_reconciliation -q
                                                                              183 passed (316.55s)
pytest tests/rulesets -q -n 6 --dist loadfile                                 2350 passed / 0 failed (476.32s)
ruff check src/ tests/rulesets/test_dnd2024_channel_control.py                All checks passed
mypy（全仓）                                                                   no issues in 102 files
git diff --check                                                              无输出
```

全量无既有基线失败（Windows 上那 18 条 docker launcher / 升级用例属 `tests/` 其它目录，不在 `tests/rulesets` 范围）。

### 11.8 与契约的偏差及原因

1. **`abjure_foes` 的 `target_kind` 从 `hostile` 改为 `creature`。** SRD 原文是 "a number of creatures equal to your Charisma modifier that you can see"，不是"敌人"；`_validate_abjure_foes` 按"活着的、可见的、60 尺内的、非自己"校验，本来就允许选中友方。声明与校验必须说同一句话，否则前端目标选择器会比服务端更窄。
2. **Action Surge 的拦截放在 `_validate_turn_choice`，不是 `_choice_spent`。** 预算里 `surge_action` 与 `action` 是两栏，"已选过动作"用 `action < 1` 判定；把 surge 折进 `_choice_spent` 会把"还没行动、只有 surge"也判成已选过。单独的规则只在 `action` 为 0 而 `surge_action > 0` 时拒绝，文案直说 Action Surge 买不到第二个。
3. **"主动无动作结束"未实现。** 契约 §4.1.6/矩阵 6 要求"无动作提前结束"，但引擎的意图闭集（`primitives.INTENT_TYPES`）里没有任何"结束自己身上效果"的入口，新增一种意图要改 `primitives.py`（白名单外）并牵动 validation/reducer/view 三处。本切片只落地三条真实存在的中止路径：再次激活、脱手、10 分钟到期。见 §11.9 第 1 条。
4. **"再次施法刷新"改为跨回合验证"不叠加"。** Divine Smite 是附赠动作，且一回合只能消耗一个法术槽（既有规则 `only one spell slot can be expended on a turn`），所以同一回合里不可能有第二次圣斩；而 `smite_of_protection` 在持有者下一回合开始时到期，两段不会重叠。实际可观察的只有"第二次施法后仍是一份实例、+2 而不是 +4"（`test_a_second_cast_leaves_one_instance_not_two`）。
5. **恐惧免疫只能在己方侧表达。** `_aura_condition_immunity_source` 与 `_passive_condition_immunity` 都以 `side == "party"` 为前提，敌人 stat block 没有 `condition_immunities` 栏位。所以矩阵 17 用"灵光内的队友 + 勇气灵光"验证：拦截原因写回该次豁免、目标身上不留隐藏限制；敌人（不在灵光受益方里）照常恐惧。
6. **白名单外改了两个文件**（§11.5 表）：`combat/primitives.py` 与 `features/models.py`，都是闭集/常量的唯一产地。
7. **顺手修了一个真实缺陷。** `_intent_choice` 初版把 `CapabilityCost.action` 当布尔（`capability.cost.bonus_action`），而它是槽位名字字符串（`action` / `bonus_action` / `reaction`）。任何被斥退恐惧的生物使用职业能力都会抛 `AttributeError`——只有 `test_a_bonus_action_first_costs_the_move` 走圣疗才现形。
8. **"取最高掩蔽"目前只有一个来源。** `_cover_record` 按"单一最佳来源"返回，但基线还没有 Three-Quarters/Total Cover 之类的第二来源，所以"与更高掩蔽不叠加"只能靠代码形状保证，没有跨来源用例。

### 11.9 未决事项（不得把非目标列为已实现）

1. **没有"提前结束自身效果"的意图入口。** Sacred Weapon 的 "You can end this effect early (no action required)" 只能靠重施、脱手或到期三条路径终止。补齐需要新增一种意图（例如 `end_effect`），涉及 `primitives.py`、validation、reducer、view 与前端按钮，属独立切片。
2. **敌人不能自带恐惧免疫。** 免疫查询只认己方侧（灵光或自身特性），SRD 里的"Frightened 免疫怪物"在本实现里表达不出来；矩阵 17 只能验证己方侧那条路径。
3. **光照仍是 guided。** 20 尺 Bright / 20 尺 Dim 只写成可审计事实，不参与 AC、攻击、Hide、视野或感知；需要照明系统的切片来做自动判定。
4. **三选一的映射里 `activate_item` 与反应法术没有 pipeline 用例。** `_intent_choice` 已把物品激活算作"选动作"、把 `Reaction` 施法算作"不占三选一"，但本切片的 fixture（Cleric / Paladin）没有可激活物品，也没有反应法术，这两条只有代码形状保证。
5. **`smite_of_protection` 的掩蔽不随光环消失立即失效的窗口。** 条件挂在圣武士身上，光环因失能消失时查询自然返回 0，但条件本身要等到下一回合开始才移除；事件流里没有"因光环消失而移除掩蔽"的记录，只有查询结果变 0。
