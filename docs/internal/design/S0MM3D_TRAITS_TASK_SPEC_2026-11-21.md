# S0-MM3d 任务书：八个高频被动特质

| 项 | 值 |
|---|---|
| 切片 | S0-MM3d（特质） |
| 前置 | S0-MM3a |
| 基线 | 叠在 #830（S0-F25 地基）之上 |
| 数据依据 | `S0MM3_ATTACK_SCHEMA_2026-11-21.md`、`S0MM3_TRAITS_ACTIONS_SURVEY_2026-11-21.md` |
| 状态 | 待执行 |

## 1. 范围：只做 8 个，覆盖 139 / 326（43%）

| 特质 | 条数 | 需要的机制 |
|---|---|---|
| Magic Resistance | 33 | 对抗法术的豁免优势来源 |
| Amphibious | 31 | 呼吸事实（纯布尔） |
| Legendary Resistance | 27 | 每日次数 + "失败改成功" |
| Pack Tactics | 17 | 邻近盟友 → 攻击优势来源 |
| Spider Climb | 13 | 移动事实 |
| Water Breathing | 8 | 呼吸事实 |
| Flyby | 6 | 飞行离开不引发借机攻击 |
| Amorphous | 4 | 移动事实 |

## 2. 契约

特质统一进 `traits[]`：`{"id": "magic_resistance", "params": {...}}`，**每个特质一个闭集 id**，不允许多种拼写。

- 特质**只声明事实，不直接改数值**：`amphibious` / `water_breathing` / `spider_climb` / `amorphous` 是布尔，被各自的消费者查询；`magic_resistance` / `pack_tactics` 走既有的优势来源机制；`legendary_resistance` 走 usage。
- 未识别的特质 id → **拒绝加载**（fail closed），不忽略。

## 3. 测试

1. 八个特质各自一条最小用例（声明 → 消费点生效）。
2. `magic_resistance` 只对抗法术生效，物理攻击不受影响。
3. `pack_tactics` 需要邻近盟友，独处时不生效。
4. `legendary_resistance` 每日次数耗尽后拒绝。
5. 未知特质 id 拒绝加载。
6. 全量目录：135 条中带这 8 个特质的条目都能加载。

## 4. 不做

Diabolical / Demonic Restoration（11）、Sunlight Sensitivity（5）、Fire Aura 类（20+）、Swarm（7）——缺位面/光照/回合钩子/HP 比例谓词，逐条登记缺口。
