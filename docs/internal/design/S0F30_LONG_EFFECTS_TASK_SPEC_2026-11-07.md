# S0-F30 任务书：跨日、位面与长时状态特性

| 项 | 值 |
|---|---|
| 切片编号 | S0-F30（独立新任务） |
| 验收载体 | `quivering_palm`、`hurl_through_hell`、`intimidating_presence`、`dragon_wings`、`holy_nimbus` |
| 规则基准 | 官方 SRD 5.2.1；p30、p52、p57、p70、p76 正文 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | F14、F20、F21、F23、F28、D2 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与完成定义

1. 实现五个特性的完整资源回充、持续时间、延迟触发、回合开始伤害和可用性；不得只投影名称或消耗资源。
2. F14/F20/F21/F23/F28 提供持续状态、对应职业主干、资源和光环；D2 提供事件/资源纪律。不得绕过真实 owner、装备或法术位。
3. 位面身份、Same Plane 判定、光照与 sunlight 是硬规则边界。前置若没有权威系统，对应部分必须标 `guided` 并停止开放相关自动化；整个特性不得宣称完整，更不能默认“永远同位面”或把 sunlight 当普通 Bright Light。
4. 指定页段中的相邻页用于上下文；正文实际页码为 Intimidating Presence p30、Quivering Palm p52、Holy Nimbus p57、Dragon Wings p70、Hurl Through Hell p76。

## 2. 官方英文关键原文与页码

SRD p30：

> **Level 14: Intimidating Presence** — As a Bonus Action … each creature of your choice in a 30-foot Emanation originating from you must make a Wisdom saving throw (DC 8 plus your Strength modifier and Proficiency Bonus). On a failed save, a creature has the Frightened condition for 1 minute. At the end of each of the Frightened creature’s turns, the creature repeats the save, ending the effect on itself on a success. Once you use this feature, you can’t use it again until you finish a Long Rest unless you expend a use of your Rage (no action required) to restore your use of it.

SRD p52：

> **Level 17: Quivering Palm** — When you hit a creature with an Unarmed Strike, you can expend 4 Focus Points to start these imperceptible vibrations, which last for a number of days equal to your Monk level. The vibrations are harmless unless you take an action to end them. Alternatively, when you take the Attack action on your turn, you can forgo one of the attacks to end the vibrations. To end them, you and the target must be on the same plane of existence. When you end them, the target must make a Constitution saving throw, taking 10d12 Force damage on a failed save or half as much damage on a successful one. You can have only one creature under the effect of this feature at a time. You can end the vibrations harmlessly (no action required).

SRD p57：

> **Level 20: Holy Nimbus** — As a Bonus Action … for 10 minutes or until you end them (no action required). Once you use this feature, you can’t use it again until you finish a Long Rest. You can also restore your use of it by expending a level 5 spell slot (no action required).
>
> **Holy Ward.** You have Advantage on any saving throw you are forced to make by a Fiend or an Undead.
>
> **Radiant Damage.** Whenever an enemy starts its turn in the aura, that creature takes Radiant damage equal to your Charisma modifier plus your Proficiency Bonus.
>
> **Sunlight.** The aura is filled with Bright Light that is sunlight.

SRD p70：

> **Level 14: Dragon Wings** — As a Bonus Action … The wings last for 1 hour or until you dismiss them (no action required). For the duration, you have a Fly Speed of 60 feet. Once you use this feature, you can’t use it again until you finish a Long Rest unless you spend 3 Sorcery Points (no action required) to restore your use of it.

SRD p76：

> **Level 14: Hurl Through Hell** — Once per turn when you hit a creature with an attack roll … The target must succeed on a Charisma saving throw against your spell save DC, or the target disappears and hurtles through a nightmare landscape. The target takes 8d10 Psychic damage if it isn’t a Fiend, and it has the Incapacitated condition until the end of your next turn, when it returns to the space it previously occupied or the nearest unoccupied space. Once you use this feature, you can’t use it again until you finish a Long Rest unless you expend a Pact Magic spell slot (no action required) to restore your use of it.

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| 子职表已列五个特性，canonical 特性定义尚缺 | `content/subclass_catalogs/srd_subclasses.json`、class feature catalog |
| 徒手命中、Focus、攻击次数和伤害在统一结算链 | `combat/resolution.py::_attack_events`、`_submit_hit_damage`、`_class_capability_events` |
| 条件支持持续、回合边界、重复豁免与失能级联 | `combat/resolution.py::_save_repeat_events`、`_end_turn_events`；`combat/reducer.py::_reduce_event` |
| 位置、敌人回合开始、光环与资源支付已有入口 | `combat/engine.py::_distance`、`_fresh_economy`；`combat/resolution.py::_start_combat_event` |
| 长休恢复职业资源和法术位，短休语义独立 | `resting/engine.py::complete_short_rest`、`complete_long_rest` |
| 基线 `src/rulesets/dnd2024` 没有 plane、sunlight、bright_light、swim_speed 或 truesight 的权威运行时字段 | 基线源码定点扫描；不得据此虚构系统 |
| 探索与战斗各有事件入口，跨日效果不能只存在战斗内存 | `exploration/engine.py::resolve_intent`、`apply_batch`；`combat/reducer.py` |

## 4. 精确实现契约

### 4.1 通用状态纪律

1. 五特性使用真实 `subclass:path_of_the_berserker`、`warrior_of_the_open_hand`、`oath_of_devotion`、`draconic_sorcery`、`fiend_patron` owner 与 14/17/20 级门槛，canonical/en/zh-CN 同步。
2. 长休使用、每回合使用、持续效果和目标身份均 owner-scoped；回充支付在服务端完成。资源不足、重复恢复、非对应槽位/资源、客户端提交计数均拒绝。
3. 持续效果携带创建时间/等级快照、来源、目标和明确到期规则，跨战斗、探索、存档和长休按规则存在；不得因 `combat.ended` 一概清除跨日状态。
4. reducer 只应用事件；重放不掷骰、不再次支付、不再次推进倒计时。公开投影只给有权查看者必要字段。

### 4.2 Quivering Palm

1. 17 级 Open Hand Monk 的 Unarmed Strike 命中后，可支付 4 Focus Points 给该目标施加振动；未命中不支付。持续天数为施加时 Monk level，跨日保存。
2. 同时最多一个目标。对新目标施加前不得悄悄覆盖旧目标；若无合法“先无害结束”选择则拒绝新施加。无害结束不耗动作、不造成伤害、不触发豁免。
3. 致命结束有两种真实入口：Action；或自己的 Attack action 中放弃一次尚未执行的攻击。第二种必须实际减少本次攻击数，不得保留攻击又触发。
4. 致命结束要求持有者与目标同位面；满足后目标 Con save，失败 10d12 Force，成功减半。无权威 plane id 或同位面比较时，此入口为 `guided`/不可用，不得默认成功。

### 4.3 Hurl Through Hell

1. 14 级 Fiend Warlock 每回合攻击命中时可使用一次；失败/成功均视为已使用。目标 Cha save 对持有者 spell save DC。
2. 失败时非 Fiend 受 8d10 Psychic，Fiend 不受该伤害；目标消失并 Incapacitated 至持有者下一回合结束，然后返回原空间或被权威规则确定的最近空位。
3. 保存原位置、消失状态与返回时点；期间不可被当作仍在原位置普通目标。位面穿越叙事可 guided，但消失、失能、伤害和返回若自动化必须完整一致；缺权威位面/位置语义时停止该特性自动化。
4. 长休恢复，或不耗动作支付一个真实 Pact Magic spell slot 恢复；不得接受普通法术位代替。

### 4.4 Intimidating Presence 与 Dragon Wings

1. Intimidating Presence 耗 Bonus Action，选择 30 尺 Emanation 内生物；DC=`8+Str mod+Proficiency Bonus`。失败 Frightened 一分钟，每个受影响者自己回合末重复 Wis save，成功只移除其自身效果。
2. 使用后 Long Rest 恢复，或不耗动作支付一次 Rage use 恢复；支付不进入 Rage、不改变既有 Rage 持续时间。
3. Dragon Wings 耗 Bonus Action，获得 60 尺 Fly Speed 一小时，可无动作主动结束；速度必须进入战斗与探索的同一推导，不能只写 condition 文案。
4. 长休恢复，或不耗动作支付 3 Sorcery Points 恢复；F28 资源主干必须真实存在。翅膀结束立即撤销速度，save/load 不残留。

### 4.5 Holy Nimbus

1. 20 级 Devotion Paladin 耗 Bonus Action 激活十分钟，可无动作结束；Long Rest 恢复，或不耗动作支付真实 level 5 spell slot 恢复，其他环阶不可替代。
2. 激活期间，持有者被 Fiend 或 Undead 迫使进行的所有 saving throw 具有优势；来源生物类型必须来自权威 actor，不由客户端声明。
3. 敌人在 Aura of Protection 内开始其回合时受 `Cha mod + Proficiency Bonus` Radiant damage；每敌人每回合开始一次，不在持有者回合或进入时重复。
4. 光环充满 Bright Light 且属于 sunlight。若前置没有光照/阳光模型，仅可把 Sunlight 子能力登记为 `guided`，不得给吸血鬼等附加未经系统支持的效果，也不得把 Holy Nimbus 整体标完整。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；本切片必要的 combat/rest catalog 声明；`features/models.py`、`features/resolver.py`、`features/resources.py`；`character/derivation.py`、`character/reconciliation.py`；`combat/view.py`、`validation.py`、`resolution.py`、`check_modes.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；`resting/engine.py`；新建 `tests/rulesets/test_dnd2024_long_effects.py`；本任务书仅追加执行报告。

位面或光照若需新模块/新 canonical 字段而前置未提供，停止，不得借白名单自行发明。不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎。

## 6. 逐项编号测试

1. 五特性真实子职/等级授予、非子职排除、en/zh-CN 和 source_ref 正确，历史重建一致。
2. Quivering Palm 仅在 Unarmed Strike 命中后支付 4 Focus；未命中、点数不足、武器命中均不施加。
3. 振动持续天数等于 Monk level，跨战斗、探索、save/load 保留；到期自动消失，长休不无故重置剩余天数。
4. 同时仅一个目标；无害结束无动作/骰/伤害；再次施加不会无审计覆盖旧目标。
5. Action 与 Attack action 放弃一次攻击两种致命入口均正确；后者实际少一次攻击，其他动作不变。
6. 同位面时 Con save、10d12 Force、成功减半准确；异位面拒绝，plane 缺失时明确 guided 且不开放假判定。
7. Hurl 每回合一次，命中后 Wis/Cha 资格与 spell DC 正确；成功无后续效果但使用已消耗。
8. 失败时非 Fiend 8d10 Psychic、Fiend 零伤害；消失、Incapacitated、持有者下一回合结束和原/最近空位返回顺序正确。
9. Hurl 长休恢复及 Pact Magic 槽恢复准确；普通槽、零槽、重复支付和每回合伪造均拒绝。
10. Intimidating Presence 只影响 30 尺内被选择者，DC 公式正确；失败 Frightened 一分钟。
11. 每个受影响者仅在自己回合末重复 Wis save，成功只结束自身；Long Rest/Rage use 两种恢复完整且不误触 Rage。
12. Dragon Wings 一小时 60 Fly Speed，主动结束/到期即时撤销，战斗和探索推导一致。
13. Dragon Wings 长休与 3 SP 恢复准确；短休不恢复使用，点数不足和错误资源拒绝。
14. Holy Nimbus 十分钟、主动结束、长休和 level 5 slot 恢复准确；level 4/6 或免费槽不得冒充。
15. Holy Ward 仅对 Fiend/Undead 来源豁免优势；其他来源和其他检定不受影响。
16. 每个敌人处于光环且开始回合时受一次 `Cha+PB` Radiant；光环外、友军、他人回合不触发，apply/replay 一致。
17. Sunlight 有真实模型时验证 Bright Light+sunlight；没有时测试明确 `guided` 边界并断言不得宣称完整，而非伪造通过。
18. 所有特性验证不足资源、重复事件、回合/存档边界、reducer 零 RNG；既有 Monk、Rage、Paladin Aura、Sorcerer、Warlock、rest 和探索回归通过。

## 7. 非目标与停止条件

非目标：其他子职特性、通用位面旅行、完整光照战斗系统、怪物日光敏感全集、三维地图、多职业和前端表现。

停止条件：任一前置未合入；缺少真实 plane/位置/光照而执行侧试图默认通过；无法持久化跨日目标；无法区分 Pact/五环槽；需要白名单外新系统；测试 5、8、16 只能靠手造状态或假事件完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_long_effects.py -q
python -m pytest tests/rulesets/test_dnd2024_monk_combat.py tests/rulesets/test_dnd2024_monk_resources.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_long_effects.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 五特性逐项行为与 guided 边界：
- 跨日、位面、位置、光照和速度的真实性证据：
- 延迟触发、资源回充、RNG 与 reducer：
- 测试 1–18 逐项结果和全部验收输出：
- 白名单、存档兼容、偏差与未决事项：
