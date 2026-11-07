# S0-F1 任务书：统一 D20 技能熟练、专精与下限

| 项 | 值 |
|---|---|
| 切片编号 | S0-F1（独立新任务，不覆盖旧 SP） |
| 验收载体 | `expertise`、`jack_of_all_trades`、`reliable_talent`、`remarkable_athlete`、`scholar` |
| 通用能力 | 技能加值聚合、半熟练、专精去重、d20 下限、条件优势、暴击后移动 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | Bard p31–34；Ranger p59；Rogue p62–63；Champion p49；Wizard p78；Expertise p182；D20 Test p180 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 硬前置 | S0-B、S0-D3 已合入；owner-scoped `feature_choices` 与真实 D20 掷后窗口可用于角色构建、升级和战斗 |
| 执行模型 | `dsv4.1f` |
| 状态 | 待执行；本文不表示任何功能已实现 |

## 1. 范围纪律

1. 从前置合入后的实际提交新建干净工作树并记录提交；不得清理或覆盖无关改动。
2. 完整实现五项载体及其官方子效果，不得只加目录条目、静态技能值或 UI 标签。
3. 所有检定由服务器以真实熟练、属性、PB 与 RNG 结算；客户端不得提交加值、优势或最终 d20。
4. 只使用本任务引用的官方 PDF；不得混入 2014 版 Jack of All Trades、Remarkable Athlete 或技能规则。
5. 本切片建立唯一技能加值聚合入口，后续能力不得各自复制一套熟练公式。

## 2. 官方英文关键原文与语义边界

SRD p182：

> When you make an ability check with a skill proficiency in which you have Expertise, your Proficiency Bonus is doubled for that check unless the bonus is doubled by another feature.

> You can’t have Expertise in the same skill proficiency more than once.

SRD p32、p59、p62：

> **Jack of All Trades.** You can add half your Proficiency Bonus (round down) to any ability check you make that uses a skill proficiency you lack and that doesn’t otherwise use your Proficiency Bonus.

> **Expertise.** Choose one of your skill proficiencies with which you lack Expertise. You gain Expertise in that skill.

> **Reliable Talent.** Whenever you make an ability check that uses one of your skill or tool proficiencies, you can treat a d20 roll of 9 or lower as a 10.

SRD p49、p78：

> **Remarkable Athlete.** Thanks to your athleticism, you have Advantage on Initiative rolls and Strength (Athletics) checks.

> In addition, immediately after you score a Critical Hit, you can move up to half your Speed without provoking Opportunity Attacks.

> **Scholar.** Choose one of the following skills in which you have proficiency: Arcana, History, Investigation, Medicine, Nature, or Religion. You have Expertise in the chosen skill.

语义边界：专精不是额外再加一次 PB；半熟练只覆盖“缺乏该技能熟练且本次未以其他方式使用 PB”的检定；Reliable Talent 替换 d20 面值而非重掷或加 10；Remarkable Athlete 不强化全部 Strength 检定，并包含暴击后移动。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 技能值当前按“属性调整值 + 有熟练时 PB”推导，没有统一 Expertise/半熟练聚合 | `src/rulesets/dnd2024/character/derivation.py::derive` |
| 升级会重算 saving throws、skill values、initiative 等派生值 | `character/derivation.py`；`progression/advancement.py::_update_derived` |
| 先攻在战斗开始时实际掷 d20，不能仅修改静态先攻字段 | `combat/resolution.py::_start_combat_event` |
| 基线只提供攻击模式与通用来源归一，不能假设已有技能/先攻优势聚合 | `combat/check_modes.py::attack_mode`、`mode_from_sources` |
| D3 前置提供真实掷后窗口，消费已形成的结果，不能代替基础熟练公式；具体函数签名以 D3 合入代码为准 | D3 新建的 `combat/check_windows.py` 与 `tests/rulesets/test_dnd2024_cutting_words.py`；祖先基线尚无该模块 |
| 暴击、命中与移动分别进入既有事件链，暴击后移动须复用这些边界 | `combat/resolution.py::_attack_events`、`_movement_events` |
| 目录声明或 progression 行本身不证明技能效果已接入 | `content/class_feature_catalogs/srd_class_features.json`；`progression/advancement.py` |

## 4. 精确数据与行为契约

### 4.1 身份、选择与持久化

1. Bard `expertise`：2 级选两项已有技能熟练，9 级再选两项；Rogue `expertise`：1 级选两项，6 级再选两项；Ranger `expertise`：9 级选两项。后续选择必须排除已有 Expertise 的技能。
2. Wizard `scholar`：2 级仅从 Arcana、History、Investigation、Medicine、Nature、Religion 中选一项，且必须已有熟练。
3. 每个选择保存于 S0-B 的 `(owner, feature_id, choice_id)` 槽；多选数量、去重和 granted level 由服务器验证。Bard/Ranger/Rogue 同名 `expertise` 不得跨 owner 串槽。
4. Expertise 以独立来源集合持久化或由 canonical choices 确定性重建；重复来源不得令 PB 变成三倍，也不得把未熟练技能变为合法选择。
5. 装备、等级或属性变化后重算派生值；历史角色加载/升级后以同一规则重建，不保留互相矛盾的静态副本。

### 4.2 唯一加值与掷骰顺序

1. 普通技能检定为 `d20 + ability_modifier + proficiency_contribution + other_bonuses`：无熟练贡献 0，熟练贡献 PB，Expertise 贡献 `2×PB`。
2. Jack of All Trades 仅在缺乏该技能熟练且本次没有以其他方式使用 PB 时贡献 `floor(PB/2)`；不得与熟练、Expertise 或另一份半熟练叠加。
3. Jack 不应用于先攻，因为先攻虽为 Dexterity ability check，却不使用一项技能熟练；也不得扩展到 saving throw 或 attack roll。
4. Reliable Talent 仅用于使用持有者技能或工具熟练的 ability check；原始 d20 为 1–9 时以 10 进入总值计算，10–20 不变。它不是重掷，不触发第二次 RNG，也不适用于先攻、豁免或攻击。
5. 下限在加值与掷后窗口前应用；事件同时保留 `raw_d20`、`effective_d20`、替换来源、各加值分项与最终总值。原始天然 1 被替换后不得再按天然 1 结算。
6. Champion 3 级 `remarkable_athlete` 只给 Initiative 与 Strength (Athletics) Advantage；其他 Strength 技能、Strength 豁免和攻击不获得该来源。
7. 多来源 Advantage 仍只按一次优势掷骰；事件列出全部来源但不得额外增加 d20 数量。

### 4.3 Remarkable Athlete 暴击后移动

1. 持有者实际造成 Critical Hit 后，立即可移动至多 `floor(当前 Speed/2)`；未暴击、仅命中或由他人暴击不触发。
2. 此移动不引发 Opportunity Attacks，作用域只覆盖该次移动；不得写成常驻 OA 免疫。
3. 移动使用当前装备与状态后的 Speed，不得超过额度；Speed 为 0 时不可移动，事件记录来源、额度、路径与 OA 抑制。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` 及对应 en、zh-CN locale：仅本切片声明与文案。
2. `src/rulesets/dnd2024/character/builder.py`、`character/derivation.py`、`progression/advancement.py`。
3. `src/rulesets/dnd2024/features/resolver.py`：仅特性来源与检定投影。
4. `src/rulesets/dnd2024/combat/check_modes.py`、`resolution.py`、`reducer.py`、`view.py`。
5. 新建 `tests/rulesets/test_dnd2024_d20_skills.py`；本任务书仅可追加执行报告。
6. 需要改动通用世界引擎、前端私有公式或白名单外目录时停止。

## 6. 编号测试矩阵

角色须经真实构建/升级与子职链获得特性；检定走真实权威结算，不得手写特性或预期事件冒充实现。

1. Bard 2/9、Rogue 1/6、Ranger 9 的 Expertise 选择数量与已有熟练限制正确，低一级不可用。
2. 同名跨职业 Expertise 的 owner、choice_id 和 granted level 不串扰，重复技能选择被拒绝且事务不变。
3. Scholar 六项白名单准确；未熟练、列表外技能与重复 Expertise 均拒绝。
4. 无熟练、熟练、Expertise 的技能贡献分别为 0、PB、2PB；不同 PB 等级均正确。
5. 两个 Expertise 来源仍只双倍一次；卸下其他效果或重建角色不改变该结论。
6. Jack 在无熟练技能上加 `floor(PB/2)`，PB 2/3/4/5/6 对应 1/1/2/2/3。
7. Jack 对有熟练或 Expertise 技能不叠加；本次已由其他特征使用 PB 时也不再加半 PB。
8. Jack 不用于 Initiative、 saving throw、attack roll 或工具检定。
9. Reliable Talent 对技能与工具熟练把原始 1、9 分别视为 10；原始 10 不变。
10. Reliable Talent 不用于无熟练技能、先攻、攻击或豁免；替换只消费一次 d20 RNG。
11. 下限后的 d20 再叠加正确属性/熟练加值，raw/effective 骰迹及来源完整可重放。
12. 被 Reliable Talent 替换的天然 1 不走天然 1 后果；天然 20 不被破坏。
13. Remarkable Athlete 在 Champion 3 级授予，只给先攻与 Strength (Athletics) 优势。
14. Strength 其他技能、Dexterity (Acrobatics)、Strength save/attack 不获得 Remarkable 优势。
15. 与 Invisible 等其他先攻优势并存时仍只掷两个 d20，来源不丢失且不形成骰数膨胀。
16. 角色升级、存档/加载和历史重建后技能值、先攻模式及选择来源一致。
17. Champion 暴击后获得至多一半当前 Speed 的移动且不引发 OA；普通命中不触发。
18. 暴击后移动超额度、Speed 0 或非法路径均拒绝；OA 抑制不延续到其他移动。
19. 非 Champion、低等级或伪造 owner 不能获得暴击移动与条件优势。
20. 既有攻击、技能、先攻、Lucky/Cutting Words 窗口在基础值改变后仍按正确顺序结算。
21. 非法选择、伪造加值或客户端提交最终 d20 均 fail closed，角色与战斗状态不变。
22. 既有 `check_windows`、advancement、战斗与角色构建测试无回归。

## 7. 非目标与停止条件

### 非目标

- 工具 Expertise、全部职业半熟练、任意 d20 下限、完整探索挑战或被动技能 UI。
- Champion Additional Fighting Style、Superior Critical，以及其他触发式掷后资源。
- 用静态技能数字、目录声明或客户端结果替代真实检定与事件证据。

### 停止条件

1. S0-B 未合入，或 owner/choice 槽无法表达多阶段 Expertise 与 Scholar。
2. 基础检定、先攻和掷后窗口无法建立唯一且可重放的顺序。
3. 暴击后移动无法与当前 Speed、路径和 OA 抑制使用同一权威状态。
4. 需要白名单外文件或修改无关特性才能闭环。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_d20_skills.py -q
python -m pytest tests/rulesets/test_dnd2024_cutting_words.py tests/rulesets/test_dnd2024_advancement_access.py tests/rulesets/test_dnd2024_fighting_style.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/character src/rulesets/dnd2024/progression src/rulesets/dnd2024/features src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_d20_skills.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Expertise/Scholar 选择与 owner 隔离：
- 普通/半熟练/专精公式及去重：
- Reliable Talent 骰迹与 RNG：
- Remarkable 条件优势及暴击移动：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重建与重放证据：
- 偏差、停止原因与未决事项：
