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
