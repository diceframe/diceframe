# S0-F13 任务书：条件免疫、抑制与移除

| 项 | 值 |
|---|---|
| 切片编号 | S0-F13（独立新任务，不覆盖旧 SP） |
| 验收载体 | Berserker `mindless_rage`、Monk `self_restoration`、Paladin `aura_of_courage` |
| 通用能力 | 条件免疫、进入光环后的暂时抑制、回合末单条件移除、饮食 Exhaustion 例外 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81` |
| 前置任务 | S0-E2 已合入，Aura of Protection 的范围、盟友与失能状态可查询 |
| 建议分支 | `feat/dnd2024-s0f13-condition-immunity` |
| 状态 | 待执行；本文不表示功能已实现 |

## 1. 执行纪律与范围

1. 从 S0-E2 实际集成提交开工；不得重写其光环模型来迁就本切片。
2. 三项特性完整实现；条件存储、有效机械效果和移除事件必须区分。
3. 所有条件变更经服务端事件和 reducer；客户端只能声明合法选择，不能提交“免疫成功”或直接删除条件。
4. Aura 的核心验收是“已有 Frightened 保留但暂时无效”，不是进入时删除、离开时恢复副本。

## 2. 官方英文关键原文与页码

SRD p30，Berserker 6 级：

> **Mindless Rage.** You have Immunity to the Charmed and Frightened conditions while your Rage is active. If you’re Charmed or Frightened when you enter your Rage, the condition ends on you.

SRD p52，Monk 10 级：

> **Self-Restoration.** Through sheer force of will, you can remove one of the following conditions from yourself at the end of each of your turns: Charmed, Frightened, or Poisoned.

> In addition, forgoing food and drink doesn’t give you levels of Exhaustion.

SRD p55，Paladin 10 级，并须结合同页 Aura of Protection：

> **Aura of Courage.** You and your allies have Immunity to the Frightened condition while in your Aura of Protection. If a Frightened ally enters the aura, that condition has no effect on that ally while there.

> **Aura of Protection.** You radiate a protective, unseeable aura in a 10-foot Emanation that originates from you. The aura is inactive while you have the Incapacitated condition.

SRD p183 的 Immunity 定义：“If you have Immunity to a damage type or a condition, it doesn’t affect you in any way.”

## 3. 已核实的代码事实

| 事实 | 文件与函数 |
|---|---|
| Rage capability 已有 forbidden condition 与真实动作结算 | class feature catalog、`combat/resolution.py::_class_capability_events` |
| 条件通过 `condition.applied`/`condition.removed` 写入 canonical state | `combat/reducer.py::_reduce_event` |
| 攻击、失能、速度等机械效果读取条件注册表和持有者条件 | `combat/check_modes.py::condition_flags`、`attack_sources`、`effective_speed` |
| 每个自己的回合结束已有统一事件构造入口 | `combat/resolution.py::_end_turn_events` |
| 回合/条件到期及专注相关条件清理由引擎辅助处理 | `combat/engine.py::_expire_conditions`、`_remove_concentration_conditions` |
| 移动改变 positions，是光环进入/离开重算的权威触发 | `combat/resolution.py::_movement_events` |
| 长休本身会减少一级 Exhaustion，本特性不能误改成通用免疫 | `resting/engine.py::complete_long_rest` |
| 当前条件值可为标量或含来源的字典；Aura 不得删除原值 | `combat/view.py::_player_view`、condition reducer |
| Aura of Protection 的真实范围/失能接口来自 S0-E2 执行结果 | 前置任务；不得假称本基线已有该接口 |

## 4. 精确实现契约

### 4.1 共享条件语义

1. 增加三项 canonical declaration、owner/minimum level、source_ref 和双语 locale；不通过 localized name 判断资格。
2. 明确区分：免疫阻止新条件影响；移除产生 `condition.removed` 并删除已有条件；抑制只改变有效机械视图，canonical 条件与来源保持不变。
3. 提供共享“有效条件”查询，输入当前 actor/combat 与 S0-E2 光环事实，输出供 `condition_flags`、攻击模式、速度和意图验证使用的条件视图。
4. 不得在 reducer 中按职业名硬编码；若某事件必须被阻断，在服务端规则结算生成可审计的 blocked 结果，状态不变。
5. 抑制期间原条件持续时间、重复豁免和来源实例继续按原规则运行，不因抑制暂停或重置。

### 4.2 Mindless Rage

1. Rage 有效时持有者对 Charmed、Frightened 免疫；新施加事件不改变 canonical 条件，不产生攻击/检定减益。
2. 实际进入 Rage 时，对当时已有的 Charmed 和 Frightened 各生成一次移除；两者可同时移除，不受 Self-Restoration 的“一项”限制。
3. Sustain Rage 不是再次进入，不重复产生清理事件；Rage 结束后免疫消失，但已进入时被移除的条件不恢复。
4. 其他条件和 Rage 本身不被误删；0 HP 等原有 Rage 终止路径保持。

### 4.3 Self-Restoration

1. 每个自己的回合末最多移除自己一个 Charmed、Frightened 或 Poisoned；不能移除他人的条件，不能在他人回合使用。
2. 在 `end_turn` 意图增加可选 `self_restoration_condition`，服务端从当前实有条件与三项白名单重验；缺省表示不使用，不自动删除多个。
3. 合法选择在 `_end_turn_events` 中只生成一个带来源的 `condition.removed`，随后继续原回合推进；重复/并发意图由 version 与当前状态拒绝。
4. Exhaustion 不在回合末移除列表。第二效果只阻止“不进食或不饮水”来源的 Exhaustion level，不影响窒息、长休减少或其他来源。
5. 饮食来源必须由权威 hazard/effect provenance 标识；未知来源不得为了通过测试而一律视为饮食，也不得一律免疫 Exhaustion。

### 4.4 Aura of Courage

1. 仅当真实 Aura of Protection 有效时生效，范围、盟友、owner 自身和 Incapacitated 失活完全复用 S0-E2；Aura Expansion 若前置已提供，范围随其变化。
2. 自己及盟友在有效光环内对 Frightened 免疫；新施加的 Frightened 不影响他们。敌人、光环外生物和非盟友不适用。
3. 已 Frightened 的盟友进入后，原 condition 条目、来源、恐惧对象与持续时间全部保留；有效条件视图排除 Frightened，因此攻击/检定 Disadvantage 和不能接近均暂停。
4. 离开光环或光环失活后，若原条件仍有效则立即恢复机械效果；不得生成“重新获得”事件、延长持续时间或制造第二个来源。
5. 在光环内被合法移除/重复豁免成功的 Frightened 仍真正删除，离开后不得复活；多个光环只决定有效抑制，不叠加额外效果。

## 5. 改动白名单

1. canonical class feature catalog 及 en、zh-CN locale。
2. `src/rulesets/dnd2024/combat/check_modes.py`、`resolution.py`、`validation.py`、`engine.py`、`reducer.py`、`runtime.py`。
3. `src/rulesets/dnd2024/features/resolver.py`：仅本批特性查询。
4. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`：仅必要条件声明。
5. 新建 `tests/rulesets/test_dnd2024_condition_immunity.py`；本任务书仅追加执行报告。
6. S0-E2 文件若接口缺失不得在本任务补写；需要其他文件时停止。

## 6. 编号测试矩阵

使用真实 Rage、Monk 回合和 S0-E2 Paladin 光环；行为走完整意图/事件/reducer 链，禁止直接删条件作为测试操作。

1. 三项特性在正确职业/子职和等级授予，低一级、错误子职无资格，引文与双语标签正确。
2. Rage 中新增 Charmed/Frightened 被免疫且无机械减益；其他条件不被泛化免疫。
3. 进入 Rage 同时清除已有 Charmed 与 Frightened，各有一次可审计移除事件。
4. Sustain Rage 不重复清理；Rage 结束后新条件可正常生效，已移除条件不恢复。
5. 无 Mindless Rage 的 Barbarian 保持原条件行为，免疫不泄露给其他职业。
6. Monk 回合末可选择移除一个 Charmed、Frightened 或 Poisoned；实际 condition.removed 与来源准确。
7. 同时持有三个合法条件时一次只移除所选一个；重复 end_turn 不能多删。
8. 缺省选择不移除；非法条件、他人条件、未持有条件、非自己回合均拒绝。
9. Self-Restoration 不移除 Exhaustion、Stunned 或其他非白名单条件。
10. 不进食/不饮水来源不增加 Exhaustion；其他来源和既有 Exhaustion 仍按原规则，长休减一级不回归。
11. 10 级 Paladin 自己与光环内盟友获得 Frightened 免疫，敌人和光环外盟友不获得。
12. 光环内新施加 Frightened 不产生机械效果；事件和 canonical state 与选定免疫语义一致。
13. 已 Frightened 盟友进入后原条目仍在，但攻击/检定 Disadvantage 与不能接近均暂停。
14. 离开光环后同一来源实例恢复效果，不新增条件、不刷新持续时间、不复制来源。
15. Paladin Incapacitated 使 Aura of Protection/Courage 失活；恢复后按原条件状态重新计算。
16. 抑制期间重复豁免成功会真正移除条件，离开后不会复活。
17. 移动进出、回合推进、条件到期和 aura owner 变化均只按当前事实计算，无陈旧抑制缓存。
18. 多个 Paladin 光环不叠加数值，不错误删除条件；Aura Expansion 的范围行为复用 S0-E2。
19. 条件抑制对 `condition_flags`、攻击双方模式、速度和意图验证一致，不只修 UI。
20. 保存/加载、apply/replay 后 canonical 条件与有效视图一致，重放不重复移除。
21. 并发/过期 version 的 end_turn 和条件施加 fail closed，无部分状态写入。
22. 既有 Rage、Paladin Aura、Hold Person、Invisible、Stunned 与战斗回归测试通过。

## 7. 非目标与停止条件

### 非目标

- 全条件系统重写、所有条件免疫、恐惧来源的 2D 视线编辑器。
- Restoring Touch、其他 Paladin/Monk/Berserker 特性、饮食后勤 UI。
- 以删除后重建、暂停所有持续时间或全局移除 Frightened 实现 Aura。

### 停止条件

1. S0-E2 未合入，或无法查询 Aura of Protection 的有效范围、盟友与失能状态。
2. 条件机械读取点无法统一使用有效视图，出现攻击已抑制但移动/验证未抑制的分裂。
3. 饮食 Exhaustion 缺少来源且必须靠猜测才能区分；不得改成全 Exhaustion 免疫。
4. 回合末选择无法通过权威意图验证，或需要客户端直接删除条件。
5. 需要白名单外文件、状态迁移或前端私有规则才能完成。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_condition_immunity.py -q
python -m pytest tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_resting.py tests/rulesets/test_dnd2024_class_features.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/combat src/rulesets/dnd2024/features/resolver.py tests/rulesets/test_dnd2024_condition_immunity.py
git diff --check
```

## 9. 交付要求

1. 测试 1–22 逐项映射，分别提供免疫、移除、抑制三类事件证据。
2. 单独展示已有 Frightened 进入/离开光环后的 canonical 不变与机械效果切换。
3. 报告饮食来源边界、回合末最多一项、存档重放与全量回归。
4. 不得把 Aura 写成删除条件，也不得把未识别来源当成已实现。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- Mindless Rage 免疫与进入清理：
- Self-Restoration 选择与 Exhaustion 来源：
- Aura 抑制、持续时间和失活：
- 白名单文件对照：
- 测试 1–22 / 聚焦 / 回归 / 全量 / ruff / diff：
- 存档、重放与并发证据：
- 偏差、停止原因与未决事项：
