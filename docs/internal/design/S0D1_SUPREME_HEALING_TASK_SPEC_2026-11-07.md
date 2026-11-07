# S0-D1 任务书：骰面最大化 + Supreme Healing

版本：v2（2026-11-07，取代 2026-10-30 的 v1）

状态：待执行；本文是执行契约，不表示功能已实现。

执行侧须知：

1. 本任务书是本切片的唯一需求来源。不要读取旧 S-P2/S-P3 或其他施工计划来扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. **前置依赖**：S0-A 必须已完成并合入 `integration/dnd2024-srd`。从包含 S0-A 的集成分支最新提交新建干净工作树和分支 `feat/dnd2024-s0d1-supreme-healing`，在执行报告中记录基线提交；不要猜提交号，不要使用已有 `df-int` 工作树。
3. 本任务只实现 Life Domain 17 级 Supreme Healing，以及它需要的通用“公式 AST 骰节点最大化”变换。不是补齐 Cleric、Life Domain 或全部骰面操纵。
4. v1 的 `.rulebooks` 来源、Overchannel 每次使用均收费的非 SRD 裁剪、D20 窗口契约和重掷范围全部废止。Overchannel、Cutting Words、Dark One's Own Luck、Lucky 与重掷分别由后续独立任务书承接。
5. 不使用 open5e 或 2014 数据作为规则依据；下方引用的官方 SRD 5.2.1 原文即本任务规则依据。

---

## 1. 目标与完成定义

打通以下端到端链路：

```text
Cleric 实际选择 subclass:life_domain
→ 17 级获得 Supreme Healing
→ 使用已准备的 healing 模式法术恢复 HP
→ 公式中的每个骰节点直接取最大面值
→ 固定加值与施法属性调整值照常保留
→ 该治疗不消耗 RNG，事件骰迹为空且带 maximized 标记
→ reducer 落账、重放和既有治疗规则保持一致
```

沉淀一个后续可复用的封闭原语：

- `maximize_dice_nodes(node)`：只把公式 AST 中的 `dice` 节点替换为对应最大值 `constant` 节点，递归保留原有算术结构，不修改输入树。

完成不等于 catalog 出现一条记录或 Resolver 显示特性。必须同时验证实际子职授予、17 级门槛、基础与升环治疗、固定加值、RNG 顺序、事件/reducer，以及无该特性时完全不变的旧行为。

## 2. 官方规则事实（SRD 5.2.1）

官方基准：`SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`。Life Domain，印刷页 40：

> **Level 17: Supreme Healing**
>
> When you would normally roll one or more dice to restore Hit Points to a creature with a spell or Channel Divinity, don’t roll those dice for the healing; instead use the highest number possible for each die. For example, instead of restoring 2d6 Hit Points to a creature with a spell, you restore 12.

必须锁定的规则边界：

1. 只有实际选择 `subclass:life_domain` 的 17 级 Cleric 获得该特性；16 级、未选子职、其他子职或通过其他 owner 伪造均不获得。
2. 最大化对象是**每一枚治疗骰**，不是整个公式直接取一个外部上限，也不是把固定加值乘以骰面。
3. 施法属性调整值、固定治疗加值和升环带来的固定加值照常附加；升环增加的治疗骰也全部最大化。
4. 该特性不消耗动作、反应、资源池或额外 RNG；它是满足条件时的被动骰面变换。
5. 作用范围是“以法术或引导神力恢复 HP”。临时 HP 不是恢复 HP，武器/法术伤害不是治疗，Second Wind 这类职业回复也不是本任务要改写的法术/引导神力入口。
6. 当前 SRD 内容中 Preserve Life 是固定额度、没有治疗骰；不得为了覆盖“Channel Divinity”字样而虚构掷骰或新建引导神力系统。
7. `cannot_regain_hp`、死亡目标校验、法术位消耗、目标数量、HP 上限和 reducer 的既有规则不变。
8. 不沿用 2014 的 14 级授予等级。

## 3. 基线现状（已核实）

| 事实 | 位置 |
|---|---|
| 子职表在 Cleric 17 级授予 `supreme_healing`，子职 id 为 `life_domain` | `content/subclass_catalogs/srd_subclasses.json` |
| S0-A 后子职特性经 `features.subclass_ref` 和 owner-scoped key 授予；`has_feature()` 支持可选 `owner_ref` | `features/models.py`、`features/resolver.py` |
| class feature catalog 当前没有 Cleric 条目 | `content/class_feature_catalogs/srd_class_features.json` |
| `double_dice_counts()` 已提供深拷贝并递归遍历公式 AST 的同族先例 | `combat/action_adapter.py` |
| `roll_damage_node()` 通过通用求值器消费 AST，并按骰顺序返回骰迹 | `combat/action_adapter.py` |
| 法术治疗先构造基础/升环骰式，再附加施法属性调整值，最后求值 | `combat/resolution.py` 的 `mode == "healing"` 分支 |
| 当前真实治疗法术为 `cure_wounds: 2d8` 与 `healing_word: 2d4`，二者均有同面骰升环和施法属性加值 | `content/combat_catalogs/srd_combat_core.json` |
| `false_life` 使用独立 `temp_hp` 分支；职业能力治疗使用另一段 `_class_capability_events` | `combat/resolution.py` |
| 治疗 `resource.changed` 已携带 `healing` 与 `rolls`，reducer 只消费既有数值字段 | `combat/resolution.py`、`combat/reducer.py` |
| 公共 feed 有字段白名单，本任务不改变该投影 | `runtime.py::_recent_combat_events` |

行号不是契约。若 S0-A 合入后的所有权 API、治疗分支或事件结构与上述边界不同，按 §7 停止，不要建立第二套治疗或所有权路径。

## 4. 精确实现契约

### 4.1 内容声明

在 `classes` 中新建 `cleric`，class source_ref 使用 `srd-5.2.1:p36:cleric-features`，追加：

```json
{
  "id": "supreme_healing",
  "owner_ref": "subclass:life_domain",
  "minimum_level": 17,
  "source_ref": "srd-5.2.1:p40:supreme-healing",
  "summary": "When you would roll dice to restore Hit Points to a creature with a spell or Channel Divinity, use the highest number possible for each die instead of rolling.",
  "parameters": {}
}
```

标签：英文 `Supreme Healing`；中文 `极效治疗`。同时补齐 Cleric / 牧师的目录 label。canonical 与中英文 locale 同步更新；不得只改其中一份。

该特性是“拥有即生效”的被动能力，不新增 intent 参数、capability、资源池或前端开关。

### 4.2 AST 变换

在 `combat/action_adapter.py` 新增并导出：

```python
def maximize_dice_nodes(node: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy whose dice nodes are constants at their maximum value."""
```

行为契约：

1. 输入必须是 dict，否则沿 `double_dice_counts()` 抛 `CombatIntentError`。
2. `{"op": "dice", "formula": "NdM"}` 变为 `{"op": "constant", "value": N * M}`；公式仍由既有 `_parse_ndm()` 严格校验，非法骰式不得静默变成 0。
3. `add/subtract/multiply/min/max` 递归处理 `args`；常量和其他已知节点保持语义不变，未知节点沿既有遍历策略原样保留，不在本任务扩张通用 AST。
4. 返回新树，不修改调用方传入的 AST；不得把 D&D 规则写进 `src/engine/combat_formulas.py`。
5. 先 `double_dice_counts()` 再最大化时，结果等于翻倍后的每枚骰取最大；本任务不把该组合接入治疗，只锁定通用函数行为。
6. constant-only AST 经 `roll_damage_node()` 求值应返回对应固定值和空骰迹；若通用引擎不具备该既有能力，按 §7 停止。

### 4.3 法术治疗消费点

只修改 `combat/resolution.py` 的 `mode == "healing"` 分支：

1. 在完成基础/升环骰式及 `heal_ability_modifier_node()` 之后、`roll_damage_node()` 之前，通过 S0-A 的所有权 API 查询：

```python
maximized = self.features.has_feature(
    self._feature_character(actor),
    "supreme_healing",
    owner_ref="subclass:life_domain",
)
if maximized:
    healing_node = maximize_dice_nodes(healing_node)
```

2. 查询必须显式带 owner，防止未来其他职业同名短 id 误授予；不得扫描 `class_feature_grants` 作为运行时授权。
3. 对应治疗 `resource.changed` 仅在实际应用该特性时增加 `"maximized": True`；未应用时不新增该键，保持旧事件形状。
4. 应用后 `rolls == []`，治疗量等于“全部骰面最大值 + 既有固定分量”；不调用治疗骰 RNG。
5. 既有 `max(1, healing)`、`cannot_regain_hp`、成功标记、法术位/动作经济、目标与胜利结算顺序保持原样。
6. 不改 `temp_hp`、伤害、`_class_capability_events`、探索治疗或 rest 治疗入口；未来引导神力骰式治疗应复用同一 AST 原语，但不是本任务验收范围。
7. 不改 reducer。`maximized` 是审计元数据，reducer 继续按既有 `delta/amount/healing` 落账；事件重放不得再次求值或消耗 RNG。

### 4.4 改动白名单

只允许改动：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/combat/action_adapter.py`
5. `src/rulesets/dnd2024/combat/resolution.py`
6. 新建 `tests/rulesets/test_dnd2024_die_manipulation.py`
7. 本任务书：只允许追加 §10 执行报告

不需要也不允许修改 `features/models.py`、`features/resolver.py`、`combat/validation.py`、`combat/reducer.py`、`runtime.py`、通用公式引擎、前端、rest、manifest 或其他法术/职业内容。若确需白名单外改动，停止并报告，不得先改后解释。

## 5. 测试与夹具约束

1. 新测试通过真实 1 级建卡和 advancement 逐级升级构造 16/17 级 Cleric，并在 3 级真实选择 `subclass:life_domain`；不得直接建 17 级角色，不得只向 `class_feature_grants` 塞 id。
2. 可复用既有通用 `advance()` 测试辅助，但不要修改 Monk 专属夹具文件。法术选择、子职选择、法术位和 prepared 状态必须经真实数据与公开入口得到。
3. 战斗测试通过真实 encounter/actor 投影与 `cast_spell` 结算；准备阶段可以布置合法受伤目标和回合，但不得绕过 Resolver 或手工伪造 `has_feature()` 返回值。
4. 使用局部 `SequenceRng` 和“任何 `randint` 调用都失败”的哨兵 RNG 证明零消耗；不得通过放宽随机序列来换绿。
5. AST 单元测试与战斗端到端测试都要写。只测 helper 不算完成，只测最终 HP 而不断言事件/RNG 也不算完成。

## 6. 编号测试矩阵

### 6.1 内容、所有权与 AST

1. canonical/en/zh-CN 三份 catalog 均可加载；Supreme Healing 的 owner、17 级门槛、source_ref 与中英文标签正确。
2. 16 级 Life Cleric 无该特性；经真实升级至 17 级后，`has_feature(..., owner_ref="subclass:life_domain")` 为 True，feature view 带正确 owner_ref。
3. 未选择子职的 17 级 Cleric、其他子职代理、非 Cleric 伪造 `subclass:life_domain` 均不获得该特性。
4. `2d8 → constant 16`、`3d4+2 → 14`，常量与加法结构正确，输入树未改变。
5. `add/subtract/multiply/min/max` 中嵌套骰节点全部最大化，固定节点不被放大；非法骰式 fail closed。
6. `double_dice_counts(2d8)` 后最大化为 32；constant-only 节点求值返回固定值与 `rolls == []`。

### 6.2 法术治疗端到端

7. 17 级 Life Cleric 以 1 环槽施放 `cure_wounds`：治疗量为 `16 + 施法属性调整值`，事件 `maximized is True`、`rolls == []`，目标 HP 正确增加且不超过 max HP。
8. 同一角色以 3 环槽施放 `cure_wounds`：6d8 全部最大化为 48，再加固定施法属性调整值；升环不额外掷骰。
9. 17 级 Life Cleric 施放 `healing_word`：2d4 最大化为 8，再加施法属性调整值；60 尺与 bonus action 等既有行为不变。
10. 基础与升环治疗均使用哨兵 RNG 时成功结算，证明整个治疗骰求值零 RNG；后续需要随机数的事件顺序不因本特性发生额外位移。
11. 16 级 Life Cleric 的同一法术仍按原顺序消耗 RNG、返回实际骰迹，事件不包含 `maximized`；固定加值公式与基线一致。
12. 无子职或其他 owner 的 17 级角色不最大化，证明不是“Cleric 17 级自动获得”或全局短 id 误判。
13. `cannot_regain_hp` 目标仍得到 `delta == 0`、`amount == 0` 和既有 `blocked_by`；若应用了 Supreme Healing，骰迹为空且不消耗 RNG，但不得绕过阻断。
14. 治疗事件 apply 与 reducer 重放得到相同 HP、法术位和后续状态；重放不调用 RNG，也不因额外 `maximized` 字段报错。

### 6.3 排除面与回归

15. `false_life` 仍走 `temp_hp` 分支并正常消耗其骰子 RNG，事件不带 `maximized`，即使测试施法者拥有 Supreme Healing。
16. 法术伤害、武器伤害和职业能力治疗路径没有新增最大化调用；相关既有测试保持通过。
17. 治疗目标死亡校验、法术位不足、未准备、目标数量、动作经济与公共 feed 白名单行为不变。
18. S0-A Improved Critical 与既有职业特性、升级、战斗、反制法术和临时 HP 回归保持通过；不得修改既有断言来接受行为漂移。

## 7. 停止条件

出现以下任一情况，停止实现并报告，不得自行扩大范围：

1. S0-A 未合入，或 `has_feature()` 尚不支持 owner-scoped 子职所有权。
2. 为构造 17 级 Cleric 必须绕过一级建卡、伪造升级历史或修改无关升级/法术选择系统；先报告真实测试基建缺口。
3. 通用公式求值器不能对 constant-only AST 返回空骰迹，或需要修改通用引擎才能满足本任务。
4. 治疗分支已拆成多个权威入口，无法在不建立第二套规则的情况下覆盖当前全部真实 healing 法术。
5. 为通过测试必须修改 reducer、validation、前端、rest、manifest、其他职业内容或做存量迁移。
6. 出现带固定值但没有骰子的 healing 模式法术，导致 `maximized` 事件语义无法按本任务判定；先登记新内容形态，不要猜测。
7. 既有失败集发生变化但无法证明与基线环境有关；不要顺手修 `character_builder` 的既有环境失败。

## 8. 非目标

1. 不实现 Overchannel 及其长休前首次免费、递增自伤或法术列表溯源。
2. 不实现 Cutting Words、Dark One's Own Luck、Lucky、Bardic Inspiration 或任何 D20/伤害掷后决策窗口。
3. 不实现 Savage Attacker、Great Weapon Fighting、Healer 等重掷机制。
4. 不实现 Preserve Life 或其他 Channel Divinity 系统，不把固定额度治疗改成骰子。
5. 不实现 Disciple of Life、Blessed Healer 或其他 Life Domain/Cleric 特性。
6. 不修改武器/法术伤害、临时 HP、Second Wind、探索/rest 治疗或公共 feed 字段。
7. 不做前端 UI、存量迁移、content_version 变更或新存档版本。

## 9. 验收命令

使用前置任务相同的既有解释器，不新建 venv。Git Bash 下执行：

```bash
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_die_manipulation.py -q --basetemp=.ptmp-s0d1-focused
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_resting.py -q --basetemp=.ptmp-s0d1-regression
/c/Python314/Scripts/ruff check src/rulesets/dnd2024/combat/action_adapter.py src/rulesets/dnd2024/combat/resolution.py tests/rulesets/test_dnd2024_die_manipulation.py
/c/Python314/Scripts/ruff format --check src/rulesets/dnd2024/combat/action_adapter.py src/rulesets/dnd2024/combat/resolution.py tests/rulesets/test_dnd2024_die_manipulation.py
```

不得用删除、跳过或放松既有断言来换绿。若临时目录已存在，换一个新的任务专属 basetemp，不批量删除其他任务目录。

## 10. 执行报告模板（执行侧完成后追加）

```markdown
## 执行报告（YYYY-MM-DD）

- S0-A 前置提交、本任务基线 / 分支 / 最终提交：
- 实际变更文件：
- 设计偏差及理由（没有则写“无”）：
- §6 测试矩阵：逐项列出 1–18 的通过情况，不得只写“测试通过”：
- 聚焦测试：命令、passed/failed/error 数：
- 回归测试：命令、passed/failed/error 数：
- ruff：结果：
- 既有失败与基线对比：
- 基础/升环治疗数值与固定分量核对：
- RNG 零消耗、事件 apply/reducer 重放核对：
- 排除面与公共 API/feed 兼容说明：
- 未解决问题（没有则写“无”）：
- 最终 git status 与 changed-file 白名单核对：
```
