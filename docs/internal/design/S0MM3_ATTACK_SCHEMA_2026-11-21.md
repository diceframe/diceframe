# S0-MM3 攻击结构：全量普查与目标 schema

| 项 | 值 |
|---|---|
| 数据 | 331 条生物、**989 个动作**、其中 **390 个带结构化攻击** |
| 结论 | 攻击确实高度统一，但比"命中 + 一条伤害"多**四样东西**：多段伤害、射程两态、附带效果、使用次数 |

## 1. 989 个动作的构成

| 类别 | 数量 |
|---|---|
| 带结构化攻击（`attacks[]`） | 390 |
| Multiattack | 178 |
| 存档型（描述含 `Saving Throw`） | 187 |
| 其余（Spellcasting、治疗、位移、光环等） | 234 |
| 有 `usage_limits`（recharge / 每日） | 113 |

注意：390 + 178 + 187 有重叠（Multiattack 不是攻击；存档型没有 `attacks[]`），所以按"动作的形态"分类比分桶更准确。

## 2. 一个攻击的真实字段（390 条逐条核对）

| 成分 | 出现 | 说明 |
|---|---|---|
| `to_hit_mod` | **390 / 390** | 命中加值，无一例外 |
| `damage_die_count` + `damage_die_type` | **389** | 唯一例外是固定伤害 1 点 |
| `damage_bonus` | 369 | 余下 21 条是无加值骰（如 `1d6`） |
| 伤害类型 | 380 + 40 | **feed 的怪癖**：有加值时类型落在 `extra_damage_type`，无加值时在 `damage_type`。**不是两段伤害** |
| **第二段伤害** | **30** | 真正两段的：`plus 1d6 Fire` |
| `reach` | 345 | 近战 |
| `range` + `long_range` | 65 / 43 | 远程（含普通/长射程） |
| `Melee or Ranged` | 21 | 同一动作两态 |
| 附带效果（`Prone` 59 / `Grappled` 58 / `Restrained` 37 / `Poisoned` 24 / `Incapacitated` 17 / `Blinded` 17 / `Frightened` 13 / `Charmed` 10 / `Swallowed` 7） | 约 240 | 绝大多数挂在命中之后 |
| `escape DC` | 38 | 擒抱/束缚的挣脱 DC |

**没有 `Miss:` 段**（0 条），`Hit:` 只出现 1 次——SRD 的写法是"命中行 + 伤害 + 类型"内联，没有分开的命中/未命中分支。这一点让翻译比预期简单。

## 3. 目标 schema（在现有 `attacks[]` 上加两处）

```jsonc
{
  "id": "rend-0",
  "name": "Rend",
  "to_hit": 7,                                  // 现有 attack_bonus
  "damage": [                                    // ← 从单段改成数组
    {"dice": "2d6+4", "type": "slashing"},
    {"dice": "1d6",   "type": "fire"}            // 仅 30 条有第二段
  ],
  "reach": 5,                                    // 与 range 互斥
  "range": {"normal": 150, "long": 600},         // 远程时用
  "riders": [                                     // ← 新增
    {"kind": "condition", "condition": "prone",
     "save": {"ability": "str", "dc": 13}},
    {"kind": "push", "distance": 10},
    {"kind": "grapple", "escape_dc": 13}
  ]
}
```

`riders` 是本片真正的增量：**约 240 个附带效果、38 个挣脱 DC**，而我们现在的契约只有一个 `damage_type`。

## 4. Multiattack 只有五种形状

| 形状 | 数量 | 例 |
|---|---|---|
| 简单 `N 次 X` | 54 | `two Rend attacks` |
| `A and B`（不同攻击组合） | 37 | `one Flame Whip attack and one Lightning Blade attack` |
| `A or B`（二选一） | 35 | `one Claws and one Tail, or two Hurl Flame` |
| 含 `Spellcasting` | 22 | `three Rend attacks. It can replace one with Spellcasting...` |
| 其它（"any combination"、替换等） | 30 | `two attacks, using Scimitar and Pistol in any combination` |

即：**"做 N 次攻击 X" + "组合 A+B" + "二选一"** 三种原语加一个"可替换为其他动作"的修饰，覆盖 178 条中的 126 条（71%）。

数据一致性：Multiattack 引用的攻击名 168 处，其中 **33 处**在该生物的动作名里找不到——多为组合名（`Rend` 其实是 `Claw`/`Bite` 的合称）或复数。翻译时需要**按生物内的动作表做别名匹配**，不能全局匹配。

## 5. 建议的 MM3 顺序（按"能打"的价值）

```
MM3a  攻击 schema 扩展：damage[] 多段 + riders[]（约 240 处附带效果）
MM3b  Multiattack：N 次 X / A and B / A or B 三种原语
MM3c  存档型动作（187 条，独立的 action kind，不是攻击）
MM3d  特质（见 §6，只做高频被动）
```

**MM3a + MM3b 完成后**，狼/熊/鲨鱼这类"两击 + 击倒"的野兽才真的能打——F25 的形态也才可用。

## 6. 要做哪些特质：8 个覆盖约一半

原则：**只要"被动事实"或"单个布尔"，不要新的动作经济，不要环境前提**。

| 特质 | 条数 | 为什么现在能做 |
|---|---|---|
| Magic Resistance | 33 | 对抗法术的豁免优势，已有优势来源机制 |
| Amphibious | 31 | 呼吸事实，纯布尔 |
| Legendary Resistance | 27 | 每日次数 + "失败改成功"，复用 usage 机制 |
| Pack Tactics | 17 | 邻近盟友 → 攻击优势，优势来源已有 |
| Spider Climb | 13 | 移动事实，纯布尔 |
| Water Breathing | 8 | 纯布尔 |
| Flyby | 6 | "飞行离开不引发借机攻击"，复用现有反应判定 |
| Amorphous | 4 | 移动事实 |

合计 **139 / 326（43%）**，8 个模板。

**暂不做**（需要不存在的前提）：

| 特质 | 条数 | 缺什么 |
|---|---|---|
| Diabolical / Demonic Restoration | 11 | "死在物质位面 vs 本位面"——没有位面事实 |
| Sunlight Sensitivity | 5 | 没有"是否处于阳光"的环境事实 |
| Fire Aura / Corrosive Form 等 | 20+ | 回合起点的区域伤害，需要"相邻即可触发"的钩子 |
| Swarm | 7 | 半血以下伤害减半 + 不能回血，需要"HP 比例"谓词 |

## 7. 未决

1. **`riders` 的条件由谁施加**：命中后自动施加，还是走条件免疫/豁免链？建议复用 `condition.applied` 并让免疫机制照常拦截（MM1 已经做了 `condition.blocked`）。
2. **`escape DC` 的消费者**：挣脱是一个动作，属于 `ability_check`（本片刚做的原语）。
3. **Multiattack 的"替换为 Spellcasting"**（22 条）依赖怪物施法原语，属于 MM3c 之后。
