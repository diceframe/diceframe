# S0-F32 任务书：Eldritch Invocation 主干与八个核心选项

| 项 | 值 |
|---|---|
| 切片编号 | S0-F32（独立新任务） |
| 验收载体 | Invocation 多选/替换/依赖闭包及八个核心选项 |
| 规则基准 | 官方 SRD 5.2.1；Warlock p70–74（主干 p70–71，选项正文 p72–74） |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；从前置合入后的实际提交开工 |
| 前置任务 | S0-B、F1、F14、F16、F18、D1 |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与完成定义

1. 建立 Invocation 的真实多选、等级数量、逐级替换、前置校验和依赖闭包，供 F33–F35 复用；不另建短 id 选项系统。
2. 本切片实现 Agonizing Blast、Devil’s Sight、Eldritch Mind、Eldritch Spear、Fiendish Vigor、Lessons of the First Ones、Repelling Blast、Witch Sight 的真实效果。
3. 可重复 Invocation 必须绑定不同对象；普通 Invocation 不可重复。替换不能破坏仍拥有选项的前置依赖。
4. 指定 p73–75 并不覆盖全部选项正文；Agonizing Blast、Eldritch Mind 等实际在 p72，必须按官方 PDF 正文验收。

## 2. 官方英文关键原文与页码

Invocation 主干，SRD p70–71：

> **Prerequisites.** If an invocation has a prerequisite, you must meet it to learn that invocation.
>
> **Replacing and Gaining Invocations.** Whenever you gain a Warlock level, you can replace one of your invocations with another one for which you qualify. You can’t replace an invocation if it’s a prerequisite for another invocation that you have. … You can’t pick the same invocation more than once unless its description says otherwise.

选项原文，SRD p72–74：

> **Agonizing Blast** — Prerequisite: Level 2+ Warlock, a Warlock Cantrip That Deals Damage. Choose one of your known Warlock cantrips that deals damage. You can add your Charisma modifier to that spell’s damage rolls. Repeatable … choose a different eligible cantrip.

> **Devil’s Sight** — Prerequisite: Level 2+ Warlock. You can see normally in Dim Light and Darkness—both magical and nonmagical—within 120 feet of yourself.

> **Eldritch Mind** — You have Advantage on Constitution saving throws that you make to maintain Concentration.

> **Eldritch Spear** — Prerequisite: Level 2+ Warlock, a Warlock Cantrip That Deals Damage. Choose one of your known Warlock cantrips that deals damage and has a range of 10+ feet. When you cast that spell, its range increases by a number of feet equal to 30 times your Warlock level. Repeatable … choose a different eligible cantrip.

> **Fiendish Vigor** — Prerequisite: Level 2+ Warlock. You can cast False Life on yourself without expending a spell slot. When you cast the spell with this feature, you don’t roll the die for the Temporary Hit Points; you automatically get the highest number on the die.

> **Lessons of the First Ones** — Prerequisite: Level 2+ Warlock. … gain one Origin feat of your choice. Repeatable … choose a different Origin feat.

> **Repelling Blast** — Prerequisite: Level 2+ Warlock, a Warlock Cantrip That Deals Damage via an Attack Roll. Choose one of your known Warlock cantrips that requires an attack roll. When you hit a Large or smaller creature with that cantrip, you can push the creature up to 10 feet straight away from you. Repeatable … choose a different eligible cantrip.

> **Witch Sight** — Prerequisite: Level 15+ Warlock. You have Truesight with a range of 30 feet.

## 3. 定点代码事实（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| Warlock progression 有 Invocations track，基线没有选项所有权主干 | `content/progression_catalogs/srd_classes.json`；`progression/advancement.py::preview_next_level` |
| S0-B 前的 `ClassFeatureDefinition` 不表达多选/替换；本任务必须消费前置而非绕过 | `features/models.py::Dnd2024ClassFeatureCatalog._parse_feature` |
| 法术归属、目标和范围由 view/validation/resolution 共用 | `combat/view.py::_available_spells`；`combat/validation.py::_validate_spell`；`resolution.py::_spell_events` |
| 法术伤害 AST 在 spell attack/save/automatic 分支构造 | `combat/resolution.py::_spell_events` |
| False Life 已有 `2d4+4`、self、temp_hp 效果 | combat core `spell_effects.false_life`；`resolution.py::_spell_events` |
| 优劣势来源可统一归一；专注豁免有既有入口 | `combat/check_modes.py::mode_from_sources`；`combat/engine.py::_concentration_events` |
| 专长所有权已有 `feat_refs`，Origin feat 选择不得伪造 | `progression/advancement.py::_feat_options`；`character/derivation.py::derive` |
| 基线没有权威 Truesight 字段；视觉必须由 F18 提供，不得只写 summary | `combat/check_modes.py`、`combat/view.py` 基线核对 |

## 4. 精确实现契约

### 4.1 Invocation 所有权、数量与替换

1. 主特性 `eldritch_invocations` 使用 `class:warlock`、1 级和 p70 source_ref；每个选择保存 `(owner, feature_id, choice_id, invocation_ref, object_ref?)`，重复选项还有独立槽位。
2. 当前数量严格来自 Warlock Invocations track；升级只要求补足新增槽位。未拥有、超数量、未知 ref、错误 owner、重复普通选项均拒绝。
3. 每获得一个 Warlock 等级可替换一个现有 Invocation，也可不换；新选项必须在替换后的角色状态上满足等级、已知戏法、专长和 Invocation 前置。
4. 替换前计算仍拥有选项的依赖闭包：若被替换者是任一保留选项的直接/传递前置，拒绝。不得靠固定名单遗漏 F33–F35 的 Pact/Thirsting Blade 依赖。
5. Agonizing Blast、Eldritch Spear、Repelling Blast 的每次选择绑定不同 eligible cantrip；Lessons 绑定不同 Origin feat。替换对象或失去对象资格时 fail closed，不保留无对象效果。
6. preview/apply、builder 高等级历史重建、save/load、en/zh-CN 均从同一记录推导；客户端不能直接提交效果数值。

### 4.2 三个戏法绑定选项

1. **Agonizing Blast**：仅所选、已知且属于 Warlock 的伤害戏法；每次该法术伤害 roll 加当前 Charisma modifier。多射线/多目标每个伤害 roll 分别加，不是每道施法只加一次；其他戏法不变。
2. **Eldritch Spear**：仅所选已知 Warlock 伤害戏法且原范围至少 10 尺；有效范围增加 `30×当前 Warlock level`。投影、距离校验和结算一致，Touch/Self/低于 10 尺不合法。
3. **Repelling Blast**：仅所选、需要攻击 roll 的 Warlock 戏法命中 Large 或更小目标时，可选择把目标直线远离至多 10 尺。Huge+ 不生效；未命中不生效；0–10 尺由合法声明决定，不能斜向或朝施法者。
4. 三个可重复选项的不同选择按各自对象独立生效；同一戏法重复绑定不叠加两次。替换后效果立即跟随新对象，历史事件不被重写。

### 4.3 其余五项真实效果

1. **Devil’s Sight**：持有者在 120 尺内于 Dim Light、非魔法 Darkness 和魔法 Darkness 中均正常视觉；超出 120 尺不扩展，也不授予 Truesight 或看穿隐形。必须进入 F18 的观察/攻击来源链。
2. **Eldritch Mind**：仅为维持 Concentration 的 Constitution saving throw 增加 Advantage；普通 Con save、死亡豁免、其他属性豁免和攻击不受影响，多来源仍只两骰。
3. **Fiendish Vigor**：False Life 只能对自己、无 Pact slot、按该特性基础施法；临时 HP 的每个骰自动取最大值，固定 +4 保留，基础为 12。伤害骰 RNG 不消耗，动作/组件/目标与临时 HP 不叠加规则不变。
4. **Lessons of the First Ones**：每次选择真实授予一个不同 Origin feat 到 `feat_refs`，触发其既有实际效果；未自动化专长必须标 `effect_not_implemented` 且不可选择，不得空授予。替换时撤销旧 feat 所有权及效果，再授予新 feat。
5. **Witch Sight**：15 级起获得 30 尺 Truesight，进入视觉、隐形、变形/幻象等已有权威判定。若 F18 未提供 Truesight 语义，停止该选项自动化并标 `guided`，不得将 Witch Sight 标完整。

### 4.4 可观察性与持久化

1. 有效戏法范围、伤害加值、视觉来源、优势来源、最大骰和强制位移都在事件/投影中带 invocation identity 和 object_ref。
2. 所有随机数只在 resolution 生成；reducer 不掷骰。升级替换、存档加载、战斗/探索和重放不得复制 feat 或临时 HP。
3. available_intents 只能提供当前合法对象；未自动化效果不得以“guided”名义开放确定性提交入口。

## 5. 改动白名单

仅允许 canonical/en/zh-CN class feature catalogs；必要 advancement feat catalog 的 Origin 可选状态；`features/models.py`、`features/resolver.py`、`features/resources.py`；`progression/advancement.py`；`spells/selection.py`；`character/derivation.py`、`character/reconciliation.py`；`combat/view.py`、`validation.py`、`resolution.py`、`check_modes.py`、`reducer.py`、`engine.py`；`exploration/engine.py`；`resting/engine.py`；新建 `tests/rulesets/test_dnd2024_invocation_core.py`；本任务书仅追加执行报告。

不得改索引、旧 SP、其他文档、前端、manifest 或通用引擎；F33–F35 选项数据不属于本切片。

## 6. 逐项编号测试

1. Warlock 各级 Invocation 数量严格等于 track；真实 builder/advance/history 一致，其他职业不能提交。
2. 普通选项重复、未知 ref、等级不足、缺已知戏法/专长前置均拒绝；中英文和 source_ref 正确。
3. 每级最多替换一个且可不换；直接和传递依赖被保留时拒绝，合法替换后闭包与效果更新。
4. 三个可重复戏法选项和 Lessons 必须选不同对象；同对象重复、换槽串号、伪造 object_ref 均拒绝。
5. Agonizing 对绑定 Warlock 伤害戏法的每个伤害 roll 加 Cha；多射线逐次加，非绑定/非 Warlock/非戏法不加。
6. Eldritch Spear 仅接受原范围 10+ 尺的绑定伤害戏法，增加 30×Warlock level；升级、替换和距离边界一致。
7. Repelling 仅在绑定攻击戏法命中 Large 或更小目标时可推 0–10 尺直线远离；Huge、miss、非攻击戏法拒绝。
8. Devil’s Sight 在 120 尺内覆盖 Dim/普通/魔法 Darkness，超过范围及隐形/幻象不误用，来源事件可审计。
9. Eldritch Mind 只让维持 Concentration 的 Con save 优势；普通 Con、其他豁免和攻击保持基线骰数。
10. Fiendish Vigor 仅对自己免费 False Life，基础临时 HP 为 12、伤害骰零 RNG；动作、组件、既有临时 HP 规则不变。
11. Lessons 授予真实 Origin feat 及效果；不同实例不同 feat，未自动化选项不可提交，替换撤销旧效果且不重复。
12. Witch Sight 15 级获得 30 尺 Truesight并影响权威视觉；14 级没有。若系统缺失，明确 guided 且不宣称完整。
13. 所有效果 apply/replay、save/load、升级历史、战斗/探索和 rest 后状态一致，reducer 零 RNG。
14. 既有 Warlock、False Life、Origin feat、Concentration、战斗和 progression 回归通过，无全局同名特性串扰。

## 7. 非目标与停止条件

非目标：F33 的施法 Invocation、F34 的 Blade、F35 的 Chain/Tome、其余 Invocation、多职业和前端改版。

停止条件：S0-B/F1/F14/F16/F18/D1 未合入；数量无法从 track 对账；依赖闭包需要硬编码才能正确；对象绑定可被客户端伪造；Truesight/魔法黑暗没有权威模型却要求标完整；需要白名单外文件。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_invocation_core.py -q
python -m pytest tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_combat.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_invocation_core.py
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 多选、替换、依赖闭包和重复对象规则：
- 八选项逐项真实效果及 guided 边界：
- 测试 1–14 逐项结果、RNG、apply/replay、save/load：
- 白名单、验收输出、偏差与未决事项：
