# S-P6 任务书：光环邻接被动（Aura of Devotion 魅惑免疫；光环原语首个端到端）

| 项 | 值 |
|---|---|
| 切片编号 | S-P6 |
| 主计划出处 | `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` L139（原语行：光环：邻接范围被动/免疫｜Devotion 魅惑免疫、Holy Nimbus；先用 1D 距离，不提前引入 2D） |
| 原语 | 光环邻接被动：特性声明 → 1D 距离谓词 → 范围内盟友的被动效果；首个效果面为**条件免疫拦截**（condition applied 前的光环检查） |
| 代表特性 / 验证载体 | 奉献灵光 Aura of Devotion（奉献之誓圣武士 7 级，魅惑免疫，端到端唯一载体）；Holy Nimbus 显式裁剪（§1.5） |
| 编写日期 | 2026-10-30 |
| 代码基线 | commit `4ef1d33d`（HEAD）。工作区存在 P7/P8 在途未提交改动，行号映射见 §2.2；免疫拦截点位于其偏移区，建议本切片排在 P7/P8 合入后执行 |
| 依赖契约 | SP1/SP2（子职特性所有权与参数消费 API——aura_of_devotion 是子职特性，`has_feature`/`feature_value` 沿用）；切片 3/1B（saving_throw 分支的 condition.applied 产生路径）；无 P6（misty_step）位移依赖——本切片只读 1D 距离，不位移 |
| 状态 | 待执行 |

---

## §1 规则语义与原文出处（2024 版，显式裁剪）

### 1.1 Aura of Devotion（奉献灵光，奉献之誓 7 级）——本切片端到端代表

原文：`.rulebooks/dnd5r/玩家手册2024/角色职业/圣武士/奉献之誓.htm` L50-52：

> **7级：奉献灵光 Aura of Devotion**
> 你与位于你守护灵光内的盟友具有魅惑免疫。陷入魅惑的盟友进入灵光时，该状态在灵光内会暂时无效。

机械拆解：

- **范围载体**："守护灵光"指圣武士 6 级职业特性 Aura of Protection 的灵光（10 尺，theater 1D 距离 `|Δposition| ≤ 10`）。主计划裁示：先用 1D 距离原语（`engine._distance`），不提前引入 2D。
- **受益者**：圣武士本人 + 10 尺内**盟友**（同侧 player/companion；敌方不受此光环）。
- **效果**：魅惑免疫——`charmed` condition 不会被应用于受光者。这是**条件免疫拦截**原语：在 condition 即将 applied 的时刻，检查受益者是否处于"免疫该 condition 的光环"内，是则拦截。
- **suppression 语义**（"陷入魅惑的盟友进入灵光时，该状态暂时无效"）：已在魅惑中的盟友走入灵光 → 魅惑在灵光内被压制、离开恢复。**显式裁剪**（§1.5 裁剪 3）：当前 `charmed` 在 conditions 注册表**没有任何机械声明**（无 `implies_incapacitated` 等条目，纯标记位），压制与否无可观察机械差异；待 charmed 获得机械消费后评估。
- **授予等级 7**（paladin 职业等级；`srd_subclasses.json` oath_of_devotion `"7": ["aura_of_devotion"]` 幽灵 id 已在位）。
- 免疫光环是**被动**：无动作、无消耗、无持续时间管理（光环随持有者位置动态生效）——区别于 applied condition 的静态生命周期，这正是"邻接被动"作为独立原语的原因。

### 1.2 光环原语的一般架构语义

- **声明**：特性目录声明光环参数（`aura_range` + `aura_immune_conditions`），代码提供**一个通用求值器**：给定 combat 与目标 actor，返回其当前免疫的 condition 集合——遍历所有 player/companion actor，`has_feature` 光环特性 + 参数非空 + 同侧 + `_distance ≤ aura_range` → 集合并集。**参数驱动即注册表**：未来新光环（Ancient 恶意免疫等）= 目录新条目，零引擎分支。
- **消费点**：condition 即将 applied 的每个构造点（本切片只接 saving_throw 法术分支的两个构造点，见 §3.3）。拦截 = 不发 `condition.applied`，并在该次 `check.resolved` 事件附 `blocked_by_aura` 字段（"仅变化时携带字段"先例），feed 可见、reducer 零新事件类型。
- **与 attack_modes 优劣势注册表的关系**：优劣势光环（如未来 Aura of Protection 的豁免加值）是另一消费面（check_modes/reducer 的豁免管线），不在本切片；本切片只立"免疫拦截"消费面，豁免加值光环登记债 D2（主计划 S-P10 的豁免侧扩展后评估）。
- **fail closed**：光环参数缺失/非法（`aura_range` 非正 int、`aura_immune_conditions` 非字符串列表）→ 该特性不产出免疫，不抛错。

### 1.3 Holy Nimbus（至圣光轮，奉献之誓 20 级）——显式裁剪，登记债

原文：同文件 L62-74。附赠动作激活，持续 10 分钟，灵光获得三项增益：至圣守护（被邪魔/不死生物迫使的豁免具有优势）、光耀伤害（敌人在灵光内开始回合 → 承受 cha mod + PB 光耀伤害）、阳光（灵光变明亮光照视为阳光）；长休一次，可耗五环法术位重置。

裁剪理由（四重原语依赖，非单切片体量）：

1. Holy Ward 需要**豁免优势来源注册**（现有豁免优势仅 rage 的 str save 硬编码路径，无来源注册表）；
2. 光耀伤害需要 **turn_start 触发型伤害**原语（S-P4 是击败/归零触发，不同触发面）+ 距离谓词；
3. 阳光需要**光照系统**（战术层阶段三后）；
4. 使用计数需要**长休作用域计数 + 法术位重置**（与 SP2 裁剪的 Overchannel 计数同族债）。

整件登记 §8 债 D1，归"豁免优势来源 + 回合触发伤害"两原语齐备后的组合批次。

### 1.4 奉献之誓其余特性（全部保持幽灵，零触碰）

oath_of_devotion_spells（3 级常备法术表）、Sacred Weapon（3 级引导神力：攻击 +cha mod 至少 +1、伤害类型选择光耀、光照——S-P10 加值族 + 光照批次）、Smite of Protection（15 级：圣斩后灵光内半身掩护——cover 机制缺失）。

### 1.5 显式裁剪清单（本切片不做，全部登记 §8 迁移债）

1. **Holy Nimbus 整件**（§1.3），债 D1。
2. **豁免加值类光环**（Aura of Protection 本体：10 尺内盟友豁免 +cha mod——它是 Devotion 灵光的范围载体，但主计划 L139 点名的效果面是"被动/免疫"；豁免加值需 S-P10 豁免侧管线扩展），债 D2。
3. **suppression 压制语义**（§1.1；charmed 无机械消费，零行为损失），债 D3。
4. **敌方光环**（enemy kind 的光环能力）：敌方不产光环（求值器只遍历 player/companion），归敌方能力批次。
5. **2D 光环几何**（主计划裁示 1D 先行）：格子/形状/遮蔽归阶段三。
6. **多光环交互的显示层**：面板/feed 展示"你正处于哪些光环内"归 UI 批次（本切片事件面已可观察 `blocked_by_aura`）。

---

## §2 代码事实锚点表

### §2.1 锚点（基线 4ef1d33d；未注明者即 HEAD）

| 文件 | 行号 | 事实 |
|---|---|---|
| `src/rulesets/dnd2024/combat/resolution.py` | L620-660 | `_spell_events` saving_throw 分支中 `condition.applied` 的两个构造点（L630 / L645；具体语义——主 applied 与 save_repeat 路径——执行侧读上下文核对，**两处都接免疫拦截**）。**两行均位于 P7/P8 偏移区**（HEAD ≥L615 = 工作区 −39，见 §2.2） |
| 同上 | L189-355 | `_attack_events`（本切片不触碰——武器攻击不产 condition） |
| `src/rulesets/dnd2024/combat/engine.py` | L1098-1100 | `_distance(combat, first, second)`（theater 1D 距离）——光环距离谓词复用 |
| 同上 | combat roster 来源 | 求值器遍历的 actor 全集：`combat` 内 players/companions/enemies 的遍历先例——`_last_hostile_defeated`（resolution L696-700 enemies 遍历）与 `_hostile_targets`；执行侧选择最贴近的 roster 遍历形状 |
| `src/rulesets/dnd2024/combat/view.py` | L337-362 | `_feature_character`（静态切片）——光环持有者的 `has_feature` 检查输入 |
| `src/rulesets/dnd2024/features/resolver.py` | L476-525 | `feature_value`：直接参数优先（`aura_range` / `aura_immune_conditions` 平铺参数即可读取，零 resolver 改动——区别于 `*_track` 轨道形态） |
| `src/rulesets/dnd2024/combat/reducer.py` | — | condition.applied 的应用路径：拦截=事件根本不产生，reducer 零感知、零改动 |
| `src/rulesets/dnd2024/combat/check_modes.py` | L18-51 | `condition_flags`（conditions 注册表机械聚合——charmed 无声明，是 suppression 裁剪零损失的证据锚点） |
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | L54 | `charm_person`：`{"mode": "saving_throw", "save": "wis", "range": 30, "condition": "charmed", "condition_duration": "1_hour"}`——端到端验证用的施加源 |
| 同上 | L86-102 | `conditions` 注册表：**无 `charmed` 条目**（grep 全文件仅 spell_effects L54 出现）——魅惑纯标记位，immune 拦截无机械冲突；`conditions` 段本切片零改动 |
| `templates/rulesets/dnd2024_srd/content/subclass_catalogs/srd_subclasses.json` | L45-48 | oath_of_devotion：`feature_ids_by_level {"3": [oath_of_devotion_spells, sacred_weapon], "7": [aura_of_devotion], "15": [smite_of_protection], "20": [holy_nimbus]}`——幽灵 id 在位，零改动 |
| `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` | 段落结构 | 现有段：barbarian（L178-207）/ fighter（L137-176 附近）/ rogue（L209-232）/ monk 等；**paladin 段现状执行侧开工时核对**（无则新增段落，monk/rogue 段形状先例；SP3 已新增 bard/warlock 段、SP5 新增 sorcerer 段的先例可能已在途——以工作区为准） |
| 同上 | L7-32 | labels 平铺区（`aura_of_devotion` 键追加先例同 UD） |
| `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` | paladin 段 L156 附近 | paladin 1 级特性列表（零改动；aura_of_devotion 不进 class progression——子职授予走 srd_subclasses 融合路径，SP1/SP2 已建立的子职特性所有权模式） |
| `tests/rulesets/` | charm/睡眠族既有测试 | charm_person / color_spray / sleep 的 saving_throw 分支回归面（执行侧 grep `charm_person` in `tests/rulesets/` 定位文件与用例名，写进执行报告） |

### §2.2 工作区在途改动与行号偏移（P7/P8 会话，未提交）

现状（2026-10-30 核实）：M `catalog.py`（1 行）/ M `resolution.py`（+39）/ M `validation.py`（+5/-2）/ M `srd_combat_core.json`（+1）/ 未跟踪 `tests/rulesets/test_dnd2024_dispel_magic.py`。

- **免疫拦截点 HEAD L630/L645 落在 P7/P8 偏移区**（HEAD ≥L615 = 工作区 −39）——P7/P8 是 counterspell/dispel_magic，极可能就在 `_spell_events` 附近施工；**同区并行冲突风险为 12 切片之最**，故本切片状态行明确建议排在 P7/P8 合入后执行；
- `srd_combat_core.json` +1 行落在 conditions/spell_effects 段内时先核对 charm_person 行是否被触碰；
- 开工时基线重测：`git status` + `git diff -U0 src/rulesets/dnd2024/combat/resolution.py`。

---

## §3 白名单逐文件改动规格

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

paladin 段（现有则扩展，无则新增段落）追加：

```json
{
  "id": "aura_of_devotion",
  "minimum_level": 7,
  "source_ref": "srd-5.2.1:p56:aura-of-devotion",
  "summary": "You and allies within your Aura (10 feet) have Immunity to the Charmed condition.",
  "parameters": {
    "aura_range": 10,
    "aura_immune_conditions": ["charmed"]
  }
}
```

- `minimum_level: 7` 按 paladin 职业等级（子职授予走 `srd_subclasses` 的 `feature_ids_by_level` 融合，SP1/SP2 已建立该所有权模式）；
- 无 `capabilities`、无 `resource_id`（纯被动）；
- labels 平铺区追加 `"aura_of_devotion": "Aura of Devotion"`（若 UD 先例键结构如此）。

### 3.2 `src/rulesets/dnd2024/combat/engine.py`（或独立小模块，执行侧按 owning module 惯例定；engine 为 mixin 宿主可接受）

新增通用求值器：

```python
def _aura_immune_conditions(self, combat, target) -> set[str]:
    """并集求值：遍历 players/companions，has_feature 光环特性且参数合法、
    与 target 同侧且 _distance <= aura_range 时，并入其 aura_immune_conditions。"""
```

- 遍历集合只含 player/companion（敌方光环裁剪 §1.5-4）；
- `has_feature(self._feature_character(holder), feature_id)` + `feature_value` 读 `aura_range`（正 int，bool 排除）与 `aura_immune_conditions`（字符串列表）——**求值器不按特性 id 分支**：判定"该特性是否光环"依据 = 参数键存在且合法（参数驱动注册表，fail closed）；
- 光环特性清单从何处枚举：`has_feature` 需要候选 id——候选来自**声明了 `aura_immune_conditions` 参数的目录条目**。执行侧实现取集：目录（`srd_class_features.json` 全特性条目中 parameters 含 `aura_immune_conditions` 者的 id 列表，catalog 加载期构建）或等价的懒扫描；禁止硬编码特性 id 清单。

### 3.3 `src/rulesets/dnd2024/combat/resolution.py`（saving_throw 分支；P7/P8 合入后执行）

- `_spell_events` saving_throw 分支的 condition.applied 构造点（HEAD L630 / L645 两处）：构造前调 `self._aura_immune_conditions(combat, target)`；命中的 condition 在集合内 → **不发 condition.applied**，并在该次 `check.resolved`（saving_throw）事件追加 `"blocked_by_aura": "<feature_id>"`（首个拦截源的特性 id；多源并集时任取其一并在执行报告注明选择规则——建议目录序稳定）；
- 伤害型 saving_throw（有 damage 无 condition）不受影响；`save_repeat` 路径同样拦截（豁免 tick 的再失败也不 applied）；
- 事件结构与字段兼容性：`blocked_by_aura` 仅在拦截时携带（"仅变化时携带字段"先例），reducer/feed 零新事件类型。

### 3.4 明确零改动文件

`check_modes.py`（优劣势光环是债 D2）、`reducer.py`（拦截在事件产生前）、`validation.py`、`view.py`（`_feature_character` 已够用）、`reaction_spells.py`、`srd_subclasses.json`、`srd_classes.json`、`srd_combat_core.json`（conditions/spell_effects 段零改动）、SP3 `check_windows.py`、前端。

---

## §4 测试矩阵

### 4.1 新用例（新建 `tests/rulesets/test_dnd2024_aura_devotion.py`；fixture 对齐 `dnd2024_monk_common` 家族，paladin devotion 7 级 sheet 经 `advance` 构造，多 actor 站位用 position 直设）

| # | 用例 | 断言要点 |
|---|---|---|
| A1 | 敌方 charm_person 打灵光内盟友，豁免失败 | 无 condition.applied；check.resolved（saving_throw）带 `blocked_by_aura: "aura_of_devotion"`；盟友 conditions 无 charmed |
| A2 | 同场景，目标在灵光外（distance > 10） | 正常 applied charmed（1_hour），无 blocked_by_aura 字段 |
| A3 | 目标是圣武士本人 | 免疫（"你与盟友"），同 A1 |
| A4 | 圣武士 6 级（无 aura_of_devotion） | 正常 applied（幽灵未授予，光环不存在） |
| A5 | 敌方目标（不同 side） | 正常 applied（光环只护同侧） |
| A6 | 距离边界 | distance 恰 10 → 免疫；11 → 不免疫 |
| A7 | save_repeat 路径（sleep 族） | 拦截同样生效（tick 再失败不 applied）；非免疫 condition 的 save_repeat 不受影响 |
| A8 | 无关 condition（color_spray blinded） | 光环只拦 charmed（参数白名单），blinded 正常 applied |
| A9 | 双持有人并集（两名 devotion 圣武士） | 任一在 10 尺内即拦截（并集语义；构造成本高时降级为直调 `_aura_immune_conditions` 单测） |
| A10 | 参数 fail closed | 特性声明被测试夹具改为 `aura_range: 0` / 非 list 时，求值器返回空集不抛错（目录级单测，直调求值器） |

### 4.2 回归守护（现有文件，零改动、预期全绿）

- charm_person / color_spray / sleep / hold_person 的 saving_throw 分支既有测试（执行侧 grep 定位文件名与用例名写进执行报告）——免疫拦截对无光环阵容零影响；
- `test_dnd2024_class_features.py`（目录结构：paladin 新段/新条目不破坏现有声明测试）；
- `test_dnd2024_uncanny_dodge.py` / `test_dnd2024_origin_feat_spells.py`（resolution.py 改动的整体回归面，若 P7/P8 已合入则含其 counterspell/dispel 测试）。

---

## §5 验收命令（PowerShell，无 `&&`）

```powershell
python -m pytest tests/rulesets/test_dnd2024_aura_devotion.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_hold_person.py -q
```

全绿后附加全量回归：

```powershell
python -m pytest tests/rulesets -q
```

## §6 禁止事项

1. 禁止实现 suppression 压制（charmed 无机械消费，债 D3；不实现假的"压制"标记）。
2. 禁止在求值器中硬编码特性 id 或 condition 白名单（参数驱动，候选来自目录声明；fail closed）。
3. 禁止新增事件类型 / 改 reducer（拦截 = 事件不产生；`blocked_by_aura` 是 check.resolved 的可选字段）。
4. 禁止实现 Holy Nimbus 任一子效果（债 D1 整件裁剪；豁免优势来源、turn_start 触发伤害、光照、长休计数四原语都不在本切片偷做）。
5. 禁止实现 Aura of Protection 豁免加值（债 D2——那是豁免管线加值面，与免疫拦截是不同消费点）。
6. 禁止给敌方开光环能力、禁止引入 2D 几何（主计划裁示 1D 先行）。
7. 禁止触碰 P7/P8 在途区域（`resolution.py` HEAD ≥L615 恰含本切片拦截点——除非按状态行建议已在其合入后开工）。

## §7 停止条件

1. 开工时 `git status` 与 §2.2 不符 → 重读基线重锚；P7/P8 的 resolution.py 改动与 saving_throw 分支的 condition.applied 构造点重叠或重构 → 停止，按实际结构重新定位拦截点，勿按本任务书行号猜测。
2. `srd_class_features.json` 已出现 paladin 段（或 aura_of_devotion 条目）—— 其它会话已施工 → 停止请示，避免双写。
3. `charmed` 在 conditions 注册表已获得机械声明（例如 P 系列给 hold 系加了门控）→ suppression 裁剪的前提变化，停止重新评估 §1.5-3。
4. 求值器需要 engine 之外的 roster 遍历接口（combat 结构与 §2.1 假设不符）→ 停止按实际 roster 形状重设计。
5. 侧翼切片（SP2/SP3/SP10）的在途实现对 `check.resolved` 字段有冲突性扩展 → 停止请示。
6. A1/A2 两个最小用例先行无法通过（拦截点语义与锚点不符）→ 停止，回报实际 `_spell_events` 结构。

## §8 执行报告模板与迁移债登记

### 执行报告（执行侧填写）

- 改动文件清单（对照 §3 白名单，逐文件：实际 diff 摘要 / 与规格的偏差及原因；求值器最终归属模块与光环候选集的构建方式）；
- §4 矩阵逐用例结果（A1-A10 + 回归清单，含 §4.2 定位到的 charm/睡眠族测试文件名）；
- §5 验收命令原样回贴（两次）；
- 行号偏移复核结果（§2.2 是否仍成立；拦截点在 P7/P8 合入后的实际行号）；
- 未尽事项与建议。

### 迁移债登记（本切片产生的显式债务）

| 债 | 内容 | 归属 |
|---|---|---|
| D1 | Holy Nimbus 整件（Holy Ward 豁免优势来源 + 光耀伤害 turn_start 触发 + 阳光光照 + 长休计数/五环重置四原语依赖） | 豁免优势 + 回合触发伤害批次（阶段二组合件） |
| D2 | 豁免加值类光环（Aura of Protection 本体：10 尺内豁免 +cha mod；Devotion 灵光的范围载体本体） | S-P10 豁免管线扩展批次 |
| D3 | suppression 压制语义（已魅惑者进灵光暂时无效、离开恢复；待 charmed 获得机械消费后评估） | 条件机械批次（与 P2 hold 门控同族） |
| D4 | 光环的面板/feed 可视化（"你正处于哪些光环内"投影） | UI 批次（阶段二） |
| D5 | 奉献之誓其余幽灵特性：oath_of_devotion_spells（常备法术表）、Sacred Weapon（攻击 +cha mod 至少 +1/光耀伤害选择/光照）、Smite of Protection（半身掩护） | 分别归法术表直填 / S-P10+光照 / cover 机制批次 |
| D6 | 敌方光环能力（求值器当前只遍历 player/companion） | 敌方能力批次（阶段二） |
| D7 | 光环持有者失能/死亡时是否抑制光环未建模：求值器只按参数 / 同侧 / 1D 距离判定，`dead` / `incapacitated` 持有者仍投影免疫。需回原书核对 Aura of Protection 的压制条款后再决定实现，本切片不猜测 | 条件 / 状态门控批次（先核对原文） |
| D8 | 敌方施法路径缺失导致正向拦截无法走完整 `resolve_intent`：A1/A2/A3/A4/A6 直接调用 `_spell_events` 验证，A5/A8 走真实施法但目标是敌方负例；真正的"敌方 charm_person 打盟友"端到端需敌方 spell profile | 敌方能力批次（阶段二） |
| D9 | 条件免疫拦截只接 `saving_throw` 分支；`spell_attack` / `automatic_damage` / `buff` / `debuff` 等其它 condition 产生模式仍绕过光环 | 条件免疫消费面扩展批次（随对应法术 / 特性首次需要时） |

---

## §9 S-P6 执行报告（2026-11-02）

### 9.1 改动文件清单（对照 §3 白名单）

| 文件 | 实际改动 | 偏差 |
|---|---|---|
| `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` | paladin 段 `paladins_smite` 后追加 `aura_of_devotion`（`minimum_level: 7`、`source_ref`、`summary`、`parameters.aura_range: 10`、`parameters.aura_immune_conditions: ["charmed"]`）；无 `capabilities`、无 `resource_id`；labels 平铺区追加英文标签 | 无 |
| `src/rulesets/dnd2024/combat/engine.py` | 新增 `_aura_immune_conditions(instance, combat, target)`：候选 = 目录中声明了 `aura_immune_conditions` 参数的全部特性（每次求值懒扫描，无硬编码 id）；遍历 `_all_targets` 中与目标同侧的 actor，校验 `has_feature`、`feature_value` 类型（`aura_range` 正 int 且排除 bool，`aura_immune_conditions` 非空字符串列表）、`_distance <= aura_range`，返回 `condition -> 首个目录序来源 feature id` 的并集 | 有：规格签名为 `set[str]`，实际返回 `dict[str, str]`（见 9.1 偏差 2）；签名为 `(combat, target)`，实际多加 `instance`（见偏差 3） |
| `src/rulesets/dnd2024/combat/resolution.py` | saving_throw 分支记录本次豁免事件的索引（自动失败 L692 / 掷骰 L729）；在统一 `condition_applies` 构造点前调用求值器（L892-899），命中则不产生 `condition.applied`，并在该豁免事件追加 `blocked_by_aura`；仅 `mode == "saving_throw"` 接入 | 有：规格预期两个构造点（L630 / L645），P7/P8 合入后已是单一统一构造点（见偏差 1） |
| `tests/rulesets/test_dnd2024_aura_devotion.py` | 新建 14 个用例（A1–A10，其中 A10 参数化 4 条），覆盖灵光内/外、本人、6 级、敌方、10/11 尺边界、repeat save、非免疫 condition、双持有人并集、参数 fail closed | A9 按 §4 允许降级为直调求值器；正向用例直调解析器（见偏差 4） |

偏差与原因：

1. **拦截点数量**：P7/P8 已合入，`_spell_events` 的所有失败初始施加路径收敛到单一 `condition_applies` 块（resolution.py L891 附近）；自动失败与掷骰路径都记录 `save_check_index`。`save_repeat` tick 本身从不重新 `condition.applied`，A7 用失败 tick 直接验证了这一结构事实，满足原意（初始施加与 tick 均不产生免疫 condition）。
2. **求值器返回映射而非集合**：`blocked_by_aura` 需要来源 feature id，返回 `condition -> feature_id` 可在一次遍历中同时给出集合语义与来源；多来源时取目录序首个，规则稳定。直调求值器测试按映射断言。
3. **求值器签名增加 `instance`**：同侧 roster 使用 `_all_targets(instance, combat)`（现有最贴近的 players/companions/enemies + side 遍历形状）；仅凭 combat 只能看到 initiative，不能保证覆盖全部 party actor。
4. **正向用例直调 `_spell_events`**：本引擎无敌方 spell profile（`validation._validate_spell` 要求 caster 有法术列表 / 位），无法通过 `resolve_intent` 构造"敌方 charm_person 打盟友"。A1/A2/A3/A4/A6 直接调用 intent 路径同一个权威解析器 `_spell_events`，只跳过 validation；A5（敌方目标）与 A8（color_spray）仍走真实 `resolve_intent`。该测试缺口登记 D8，不把正向用例描述为敌方施法端到端。
5. **候选集构建**：规格允许"目录加载期构建或等价懒扫描"；实现选择每次求值懒扫描 `catalog.features`，避免改 resolver（§3.4 零改动清单）且特性目录规模很小。无硬编码特性 id / condition 白名单。

### 9.2 §4 矩阵逐用例结果

| # | 用例（测试函数） | 结果 |
|---|---|---|
| A1 | `test_ally_inside_aura_is_immune_and_the_check_names_the_aura` | PASS：无 `condition.applied`；`check.resolved` 带 `blocked_by_aura: "aura_of_devotion"`；落账后无 charmed |
| A2 | `test_ally_outside_aura_is_charmed_without_the_aura_field` | PASS：11 尺正常 applied charmed（`1_hour`），无 `blocked_by_aura` |
| A3 | `test_the_paladin_is_immune_too` | PASS：目标为圣武士本人，同 A1 |
| A4 | `test_level_six_paladin_has_no_aura` | PASS：6 级未授予光环，正常 applied |
| A5 | `test_enemy_target_inside_range_is_not_immune` | PASS：真实 `resolve_intent`，敌方目标在灵光内仍被魅惑 |
| A6 | `test_aura_boundary_is_exactly_ten_feet` | PASS：恰 10 尺免疫；11 尺不免疫 |
| A7 | `test_repeat_save_failure_does_not_reapply_a_condition`；`test_sleep_is_not_immune_and_its_repeat_save_never_reapplies` | PASS：失败 tick 不产生 applied；sleep incapacitated 正常 applied，非免疫 condition 不受影响 |
| A8 | `test_color_spray_blinded_is_not_blocked_by_the_aura` | PASS：真实 `resolve_intent`，blinded 正常 applied，无 `blocked_by_aura` |
| A9 | `test_two_paladins_union_in_range` | PASS（§4 允许降级）：gm / 第二名圣武士交替在 10 尺内，求值器均返回 `{"charmed": "aura_of_devotion"}` |
| A10 | `test_malformed_aura_declarations_yield_no_immunity`（4 条参数化） | PASS：`aura_range: 0` / `True`、`aura_immune_conditions` 非 list / 含非字符串 → 空映射、不抛错 |

回归定位（§4.2）：

- charm_person / color_spray 在既有 `tests/rulesets/` 中**没有专门用例**（仅作为测试夹具法术列表出现）；其 saving_throw 分支由本切片新文件覆盖。
- sleep 回归：`tests/rulesets/test_dnd2024_combat.py::test_failed_save_applies_condition_but_successful_save_does_not`；`tests/rulesets/test_dnd2024_hold_person.py` 的 sleep / repeat-save 用例（原文 L370、L580、L592、L602）。
- `test_dnd2024_hold_person.py` 全文件、`test_dnd2024_class_features.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_origin_feat_spells.py`、`test_dnd2024_counterspell.py`、`test_dnd2024_dispel_magic.py` 全绿。

### 9.3 §5 验收命令与结果（两次）

执行侧（Linux / `/tmp/dfvenv` Python 3.11，命令与 §5 相同）：

```powershell
python -m pytest tests/rulesets/test_dnd2024_aura_devotion.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_hold_person.py -q
# 109 passed in 44.93s

python -m pytest tests/rulesets -q
# 938 passed, 36 warnings in 376.82s (0:06:16)
```

审核侧复跑（Linux / Python 3.11）：

```powershell
python -m pytest tests/rulesets/test_dnd2024_aura_devotion.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_hold_person.py -q
# 109 passed in 43.17s

python -m pytest tests/rulesets -q
# 938 passed, 36 warnings in 367.53s (0:06:07)
```

### 9.4 行号偏移复核（§2.2）

开工时 `git status` 干净，P7/P8 已合入，HEAD 为 `6c9bb22`；§2.2 的在途 P7/P8 偏移已不再是现状。任务书预期的 `condition.applied` 两个构造点（HEAD L630 / L645）实际为单一统一构造点：

- `resolution.py` L602：`save_check_index` 初始化；L692（自动失败）/ L729（掷骰）记录豁免事件索引；L892-899：光环拦截判定与 `blocked_by_aura` 写入，`condition.applied` 原构造点紧随其后；
- `engine.py` L1165：`_aura_immune_conditions` 求值器；
- `srd_class_features.json` L23：label；L153：`aura_of_devotion` 条目。

未出现 §7 任一停止条件；`srd_class_features.json` 的 paladin 段为既有段扩展（本切片新增唯一 aura 条目），无其它会话双写。

### 9.5 未尽事项与建议

- D7：光环持有者 `dead` / `incapacitated` 时的光环抑制未建模。任务书 §3.2 只要求参数 / 同侧 / 距离；本切片未擅自加状态门控。建议下一个接触光环的切片先核对 Aura of Protection 原文，再决定压制实现。
- D8：敌方施法端到端缺口见 9.1 偏差 4；敌方 spell profile 落地后，应把 A1/A2/A3/A4/A6 平移回 `resolve_intent` 正向路径。
- D9：条件免疫消费面目前只覆盖 saving_throw；未来出现 spell_attack / automatic_damage 施加魅惑的法术时再扩展。
- `blocked_by_aura` 多来源时取目录序首个来源；当前只有 `aura_of_devotion` 一个声明者，尚无可观察的多来源差异。
- 性能：每次目标施加条件时扫描目录特性 + `_all_targets`，规模小且无缓存；若后续光环 / 队伍规模显著增长，可考虑在 resolver 加载期缓存候选 id（不改语义）。

---

## §10 S-P6 审核记录（2026-11-02）

- 基线核对：功能提交 `8bcd7e5`，4 个文件与 §3 白名单全等；`check_modes.py` / `reducer.py` / `validation.py` / `view.py` / `reaction_spells.py` / `srd_subclasses.json` / `srd_classes.json` / `srd_combat_core.json` / `check_windows.py` / 前端均零 diff。`origin/backup/cfp` 仍为 `6c9bb22`，功能提交只在本地分支。
- 定向复跑：109 passed（43.17s）；全量复跑：938 passed（367.53s），与执行侧一致（938 = S-P11 基线 924 + 本切片 14）。
- 规则语义核对：光环范围 10 尺且含边界；圣武士本人（距离 0）受保护；仅同侧 player/companion 投影，敌方目标不受保护；`aura_immune_conditions` 仅 `charmed`，blinded / incapacitated 不受影响；参数非法 fail closed；无事件类型新增，reducer 零改动。
- 代码边界核对：求值器无特性 id 硬编码；候选来自目录参数；`blocked_by_aura` 仅在拦截时出现；saving_throw 伤害仍正常落账（只跳过 condition 施加）。
- 审核发现（3 项，均不阻塞、不改代码）：D7 持有者状态门控、D8 敌方施法端到端测试缺口、D9 条件免疫消费面仅 saving_throw。均已登记，未在审核轮修复。
- 主计划同步：1C 汇总由 9/12 更新为 10/12；S-P6 行更新为"执行完成并审核通过"。下一片按推荐顺序为 S-P12（护甲受训门控），其后 S-P8（Hide 动作与附赠动作路由）。

### 10.1 独立审核（2026-11-02，独立会话）

与 §9/§10 同会话记录分离的复核轮，全部事实重新验证：

- 基线：工作树干净，HEAD `072f0a7` = `origin/backup/cfp`（上方"仍为 6c9bb22"为 docs 推送前时点描述，现已同步）。
- 改动面：`git diff 6c9bb22..072f0a7` 全量对 §3 白名单全等；§3.4 零改动清单（含前端）逐一验证零 diff。
- §6 禁止事项逐条复核无违反：源码无 suppression / holy_nimbus / aura_of_protection 残留；求值器候选来自目录参数懒扫描，无特性 id 或 condition 白名单硬编码；`blocked_by_aura` 为既有 check.resolved 的可选字段，无新事件类型。
- 代码语义复核通过：`_all_targets` 仅遍历 players/companions（敌方不投影）；fail closed 覆盖 bool 排除、正 int、非空字符串列表；`_distance <= aura_range` 含边界且本人距离 0 自覆盖；`save_check_index` 在多目标循环内逐迭代重置，自动失败（L692）与掷骰（L729）两路径均正确指向本目标豁免事件；拦截仅 `mode == "saving_throw"`，命中则 `condition.applied` 不产生；`_save_repeat_events`（L1357）tick 失败只发 check 事件从不重新 applied，与 A7 断言一致。
- 测试独立复跑：定向五文件 109 passed（44.92s）；`tests/rulesets` 全量 938 passed（364.91s），两次结果与 §9.3 登记一致。
- §9 行号（resolution L602/692/729/892-899、engine L1165、catalog L23/L153）与实际 diff 逐一对上。
- 注记（不阻塞、不改代码）：§9.1 偏差 2 表述"多来源取目录序首个"在多持有人场景的严格优先序实为 roster 遍历序（外层 ally）先于目录序（内层 feature，`setdefault`）；当前目录仅 `aura_of_devotion` 一个光环条目，两种描述不可区分，未来新增第二个免疫光环时按实际序修正表述或补 roster 序断言。
- 结论：审核通过，无新增迁移债（D7-D9 已覆盖）。
