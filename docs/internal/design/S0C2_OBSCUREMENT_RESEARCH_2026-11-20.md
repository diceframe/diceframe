# S0-C2 遮蔽调研：两级定义、来源清单、结算路径与失效条件

| 项 | 值 |
|---|---|
| 目的 | 为"遮蔽效果"切片定清 SRD 口径与来源面，决定引擎要接哪些事实 |
| 规则基准 | SRD 5.2.1「Obscured Areas」+「Light」+「Vision and Light」；法术原文取自本仓库 `srd_spell_descriptions.json` |
| 代码基线 | `origin/main@e376f292` + C2B（遮蔽声明层） |
| 结论摘要 | 遮蔽只有两级；来源全部是**区域**而不是"某个生物身上的状态"；决定后果的不是遮蔽本身，而是**观察者能否看见**——所以引擎必须把「区域 → 生物所处等级」与「感官 → 是否被看见」分成两层 |

## 1. 定义（逐字）

### 1.1 两级，没有第三级

【SRD 5.2.1 词汇表，本仓库未收录该章节】

- **Lightly Obscured**：依赖视觉的 Wisdom (Perception) 检定有 **Disadvantage**。
- **Heavily Obscured**：试图看清该区域内的东西时，视为处于 **Blinded** 状态。

两级是**介质**造成的"看不清/看不见"，都假定空间连续、光能穿过或被散射。实体阻挡（墙、石柱、关门）不在这里，它由 **total cover** 表达（"不能被直接选为目标"）——这也是我们上一轮把 `opaque` 从闭集里删掉的依据。

### 1.2 环境如何落成这两级

【同上】

| 光照 | 遮蔽 |
|---|---|
| Bright Light | 无 |
| Dim Light（微光：烛光边缘、暮色、树荫） | **Lightly Obscured** |
| Darkness（黑暗：无光源的夜、地窖） | **Heavily Obscured** |

所以"自然环境造成的遮蔽"只有一个来源：**光**。没有光就没有遮蔽，雾/植被属于 DM 裁量（原文举例：中等雾气、稀疏枝叶 → lightly；浓雾、密林 → heavily）。

### 1.3 后果链（已确认，不再讨论）

`Heavily Obscured` → 看不见 → 攻击该目标有 Disadvantage；反过来被看不见的攻击者打，防守方给攻击者 Advantage；Hide 的前提（2024）是"处于 Heavily Obscured 或 ¾/Total Cover 之后"。
`Lightly Obscured` → 只影响**依赖视觉的感知检定**，不影响攻击。

这条链的输入端只有一个问题：**这个观察者能不能看见那个目标**。遮蔽等级只是这个问题的输入之一（另一个是感官）。

## 2. 来源清单（本仓库实测）

### 2.1 法术区域（10 条，全部只有描述文本，没有机械效果）

| 法术 | 环 | 遮蔽等级 | 区域 | 附带效果 | 原文要点（bundle） |
|---|---|---|---|---|---|
| `fog_cloud` | 1 | **Heavily** | 20 尺球 | — | "The Sphere is Heavily Obscured." |
| `darkness` | 2 | **Heavily**（魔法黑暗） | 15 尺球 / 15 尺 Emanation | 驱散 2 环及以下的明亮/微光 | "magical Darkness… **Darkvision can't see through it**, and nonmagical light can't illuminate it."；"Covering that object with something opaque… blocks the Darkness."；与 ≤2 环的 Bright/Dim Light 重叠时**驱散对方** |
| `web` | 2 | **Lightly** | 20 尺立方 | 困难地形 | "The webs are Difficult Terrain, and the area within them is Lightly Obscured." |
| `sleet_storm` | 3 | **Heavily** | 40 尺柱 | 困难地形、熄灭明火 | "The area is Heavily Obscured, and exposed flames in the area are doused." |
| `stinking_cloud` | 3 | **Heavily** | 20 尺球 | 中毒豁免 | "The cloud is Heavily Obscured." |
| `cloudkill` | 5 | **Heavily** | 20 尺球 | 体质豁免 5d8 毒 | "Its area is Heavily Obscured." |
| `insect_plague` | 5 | **Lightly** | 20 尺球 | 困难地形、体质豁免 | "its area is Lightly Obscured and Difficult Terrain" |
| `incendiary_cloud` | 8 | **Heavily** | 20 尺球 | 火焰伤害 | "The cloud's area is Heavily Obscured." |
| `storm_of_vengeance` | 9 | **Heavily** | 360 尺柱 | 困难地形、**区域内远程武器攻击不可能** | "the area is Difficult Terrain and Heavily Obscured, ranged attacks with weapons are impossible there" |
| `guards_and_wards` | 6 | **Heavily** | 被守护的走廊 | 多种守卫效应 | "Fog fills all the warded corridors, making them Heavily Obscured." |

`light`（戏法）不造成遮蔽，但它是**反向来源**：20 尺明亮 + 额外 20 尺微光，会把自然黑暗区压回"无/轻度"。同理 `faerie_fire` 给目标挂 Dim Light（10 尺）——那是"给敌人打光"，让隐形失效，不是遮蔽。

**事实**：以上 10 条在 `srd_spells.json` 里有条目，在 `srd_spell_descriptions.json` 里有全文，但在 `combat_catalogs/srd_combat_core.json` 的 `spell_effects` 里**一条都没有**（46 条效果里 0 命中）。也就是说：现在引擎知道"有这些法术"，但不知道它们会造成遮蔽。

### 2.2 环境（非法术）

| 来源 | 等级 | 备注 |
|---|---|---|
| Dim Light（暮色、烛光边缘、月光） | Lightly | 由光源衰减得到，不是独立事实 |
| Darkness（无光源） | Heavily | 同上 |
| 雾气/植被/烟尘 | 由 DM 判 | 原文明确是例子，不是枚举 |

### 2.3 职业特性与感官（不是"造成遮蔽"，而是"改变是否被遮蔽影响"）

| 条目 | 效果 | bundle 现状 |
|---|---|---|
| Blindsight | 不依赖视觉感知范围内的事物 → **忽略遮蔽** | 特性里只有 1 条声明：`blindsight_range_ft: 30`（Druid 系） |
| Darkvision | 微光当明亮、黑暗当微光 → 自然黑暗降一级；**魔法黑暗无效** | 法术 `darkvision` 只给 150 尺范围，无机械效果 |
| Truesight | 看穿正常与魔法黑暗 | 法术 `true_seeing` 只给范围 |
| Devil's Sight（邪术师祈唤） | 魔法与非魔法黑暗都能正常看 | bundle **未收录**该祈唤 |
| 怪物 `senses` | stat block 自带 | MM1 已落地（`senses: {darkvision: 60, blindsight: 30, ...}`） |

## 3. 结算路径：为什么必须分两层

SRD 的遮蔽是**区域属性**，而后果是**观察者-目标对**的属性。两者之间隔着"谁能看见谁"，中间还要过感官。所以引擎的最小正确形状是三层，而不是给每个生物挂一个等级：

```
区域事实（这个格子/这段半径是 Heavily）
   ↓  ∩ 生物位置
生物所处遮蔽等级（C2B 已有：per-actor 声明）
   ↓  ∩ 观察者感官（darkvision / blindsight / truesight）
能否看见（pairwise）
   ↓
后果（攻击劣势、感知劣势、Hide 前提、召唤物可见性）
```

**C2B 做了第二层**（等级与声明），第三层（感官）与第四层（后果）都还没做。

### 3.1 为什么第一层不能省

如果把法术区域直接写成"施法时给区域内生物挂 `heavily`"，会立刻错两处：

1. **生物走进走出**：区域是静态的，生物是移动的。挂状态就必须在每次移动后重算，而重算需要"谁在区域内"的判定——那就是第一层，绕不过去。
2. **区域会消失**：法术结束/专注断掉/被驱散时，挂上去的状态要撤，而撤的时候必须知道"这个 heavily 是这条法术给的"，不能把另一条法术给的也撤掉。

### 3.2 感官为什么是独立的第三层

`Darkvision` 不是"免疫遮蔽"，它**降一级**（黑暗→微光、微光→明亮），且只对非魔法黑暗有效；`Blindsight` 是"完全不走视觉这条链"；`Truesight` 是"魔法黑暗也照看"。三者语义都不同，不是三个布尔能概括的：

| 感官 | 对 Lightly | 对 Heavily(自然) | 对 Heavily(魔法) |
|---|---|---|---|
| 普通视觉 | 劣势 | 看不见 | 看不见 |
| Darkvision | 无影响 | 降为 lightly | **无效**（darkness 明文） |
| Blindsight | 无影响 | 无影响 | 无影响 |
| Truesight | 无影响 | 无影响 | 无影响 |

注意 Darkvision 这一行：它把"看不见"变成"看得见但有劣势"，`sight_disadvantage` 仍为真——这说明 C2B 的两个派生布尔里，`sight_disadvantage` 与 `blocks_sight` 不能由一个布尔合并（我在 C2B 里是分开的，正好对得上）。

## 4. 失效与例外清单

| 情形 | 结论 | 依据 |
|---|---|---|
| 黑暗中的 Darkvision | 自然黑暗降为 lightly，不是"无遮蔽" | Darkvision 定义 |
| **魔法黑暗 + Darkvision** | **无效**，仍然看不见 | `darkness` 原文："Darkvision can't see through it" |
| Truesight 看魔法黑暗 | 有效，能看见 | Truesight 定义 |
| Blindsight 看任何遮蔽 | 有效 | Blindsight 定义（不依赖视觉） |
| 用碗/头盔盖住黑暗源物体 | 黑暗被阻断，区域消失 | `darkness` 原文 |
| `darkness` 与 ≤2 环的 Bright/Dim Light 重叠 | **对方被驱散**；≥3 环的光不受影响 | `darkness` 原文 |
| `daylight`（3 环）与 `darkness` 重叠 | 光不灭（≥3 环），黑暗也不被驱散 —— 两者并存，需裁定谁覆盖 | 推论，规则未明说 → 属 DM 裁定 |
| `sleet_storm` 熄灭明火 | 火光消失 → 该区域失去"明亮"来源，自然转入黑暗 | `sleet_storm` 原文 |
| 遮蔽 vs 掩护 | 两件事：遮蔽管"看不看得见"，掩护管"挡不挡得住"；一名角色可以同时处于 heavily obscured 与 half cover | 定义 |
| 听力/震颤感知 | 遮蔽只作用于"看"，不影响听声辨位 | 推论 |

## 5. 本项目现状（事实，供切片判断）

| 层 | 现状 |
|---|---|
| 区域系统 | **不存在**。战斗只有一维 `positions`（theater 模式），没有 zone/area 管线 |
| 光照 | **不存在**。没有 Bright/Dim/Darkness 事实，`light` 法术无效果 |
| 遮蔽声明 | C2B 已做（`combat.set_obscurement`，per-actor，GM 手填） |
| 感官（玩家） | 只有一条特性声明 `blindsight_range_ft: 30`；无 darkvision/truesight 统一投影 |
| 感官（怪物） | MM1 已落 `senses`，但没有任何消费者 |
| 法术效果 | 上面 10 条法术的 `spell_effects` 全空 |
| 消费者 | **零**。攻击劣势、感知劣势、Hide 前提都没读遮蔽 |

## 6. 结论：遮蔽效果该拆成三片，不能一片做完

| 切片 | 内容 | 依赖 | 判断 |
|---|---|---|---|
| **C2C 区域源** | 最小 zone 事实（球/柱/立方 + 等级 + 来源法术 id），法术结束/专注断/驱散时撤销；由它自动给区域内生物遮蔽等级 | 需要"谁在区域内"的距离判定（已有 `_distance`） | 可做，先只接 `fog_cloud` + `darkness` 两条 |
| **C2D 感官合并** | `sight_obscurement(observer, target)`：读观测者 senses，按 §3.2 表降级/忽略；魔法黑暗标记随区域来源携带 | C2C 的"魔法黑暗"标记；感官数据 | 可做，先只接 darkvision（怪物 `senses` + 玩家特性）与 blindsight |
| **C2E 消费者** | 攻击劣势/优势、Perception 劣势、Hide 前提 | C2B + C2D | 必须最后做 |

**不建议跳过的顺序**：先做 C2E（消费者）会立刻发现"没有区域就没有等级来源"，只能靠 GM 手填，等于把每个法术的遮蔽变成桌子上的口述——这正是 S0-F10 当初停止的那条理由。

**建议的第一刀**：`fog_cloud` 一条法术 + 一名走进走出区域的生物 + 一个声明了 `blindsight` 的观测者。这三件事同时验证了区域、撤销、感官三层，且不需要光照系统。
