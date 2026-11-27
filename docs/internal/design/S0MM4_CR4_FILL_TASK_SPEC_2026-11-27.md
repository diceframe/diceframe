# 任务书：S0-MM4 CR 4 以内的怪物填量

| 项 | 值 |
|---|---|
| 切片 | S0-MM4（MM 线的第二条数据片，接 S0-MM1/MM2/MM3a–d） |
| 载体 | `content/monster_catalogs/srd_monsters.json` 与 `scripts/rulesets/import_srd_beasts.py` |
| 数据源 | open5e v2，`document__key=srd-2024`，本地过滤 `cr <= 4`（API 的 `cr__lte` 不生效） |
| 基线 | `0b12b0ea` |
| 状态 | 已交付：152 条（135 + 17），`incomplete` 78 / `skipped` 66；见 §8 审核记录 |

## 1. 为什么是这一档

CR 0–1 已入库 135 只（= 该档全部）。对全库 331 只用导入器自身的映射函数实跑，各档"完全可映射"的比例：

| 档 | 生物数 | 完全可映射 | 施法 | 传奇动作 | 再生/复原 |
|---|---|---|---|---|---|
| CR 0–1 | 135 | 已入库 | 3 | 0 | 6 |
| **CR 2–4** | **83** | **17（20%）** | 3 | **0** | 2 |
| CR 5–10 | 66 | 13（20%） | 12 | 2 | 10 |
| CR 11–16 | 27 | — | 18 | 11 | 2 |
| CR 17–30 | 20 | — | 16 | 19 | 0 |

CR 2–4 是唯一**完全不需要传奇动作**的档位，所以它是纯数据扩量；再往上一档就要先做再生/复原。

## 2. 范围

1. 导入 CR ≤ 4 的条目，但**只收"完全可映射"的**：即没有任何动作/特质被导入器丢弃的条目。
2. 被丢弃的信息必须**可查**，不能静默：
   - `incomplete`：已入库但少东西的条目（现有 CR 0–1 里有 8 只带施法动作）→ `{id: [丢掉的项]}`；
   - `skipped`：因为少东西而**没有**入库的条目 → `{id: [原因]}`。
3. 不改 `entries` 的既有 135 条形状；不回填、不删除已入库条目（删条目是独立决定）。

## 3. 为什么不把 `unmapped` 放进条目

`monster_catalogs` 的每条都过 `validate_enemy_profiles`，而它的 `_ENEMY_KEYS` 是**闭集**——同一个校验器也是 `combat.start` 的边界。往条目里加字段等于放宽战斗契约，会把"这只怪少了一项能力"带上 wire。所以这两张表放在**目录级**（与 `entries` 平级），加载器只读不校验。

## 4. 导入器契约

```
python scripts/rulesets/import_srd_beasts.py [--max-cr 4]
```

- `--max-cr` 默认 **4**（即当前交付状态）；给 `1` 可复现旧目录。
- `map_record` 不变；新增 `unmapped(record)` 返回该条被丢弃的动作/特质名，`main` 据此分流进 `entries` / `skipped`。
- `incomplete` 只对已入库条目填（CR ≤ 1 的历史条目会被重新计算并如实列出）。
- 排序稳定：`entries` 按 `(cr, name)`，两张表按键排序，输出可 diff。

## 5. 加载器契约

`MonsterCatalog` 增加两个只读方法：

- `incomplete()` → `dict[str, list[str]]`
- `skipped()` → `dict[str, list[str]]`

两者缺省为空表（旧目录没有这两个键也照样加载）。

## 6. 逐项测试

1. 目录仍然通过 `validate_enemy_profiles`（既有 `test_dnd2024_monster_catalog.py` 覆盖）。
2. `len(entries)` 从 135 增长到 152；其中 CR 2–4 的 17 条全部是"无缺口"的。
3. `incomplete` 与 `skipped` 都存在且非空；`incomplete` 里能查到一只已知的 CR ≤1 施法者。
4. 老目录（没有这两个键）加载后两张表为空表。
5. 导入器：`--max-cr 1` 的产物与 `--max-cr 4` 的 entries 是子集关系（不重不漏）。

## 7. 非目标

- CR 5+；传奇动作、再生/复原、Spellcasting 原语（GAP 另记）。
- 页面号：feed 没有页索引，`source_ref` 继续用 creature key。
- 条目级 `unmapped`（要放宽战斗契约，不做）。

## 8. 独立审核记录（2026-11-27）

审核由另一次执行独立完成，找到了四类问题，全部已改：

| 问题 | 修法 |
|---|---|
| `unmapped` 与真实映射不同构：Bonus/Reaction 动作被当成已映射（`elephant` 的 Trample）、攻击型 Recharge 的限次被静默丢弃（`ape` 的 Rock）、rider 未解析却记完整、结构化攻击已产出却被误判成缺口 | 按 `action_type` 过滤后逐条对齐；Recharge 与未解析 rider 计入缺口；"结构化攻击已产出"不再算洞。入库数从 161 降到 152 |
| 目录一扩，另外三个文件里 4 处硬编码断言必红（`len==135`、`(15,22)`、`(10,1)`、特质计数） | 三处一律按 **CR 0–1 档**过滤，保住它们原本的语义（描述的是已交付档），而不是抄新数字 |
| 任务书写的 `--max-cr` 并不存在；`CR_MAX=1` 只能复现 `entries`，不能复现文件 | 真的加 `argparse` 的 `--max-cr`（默认 4）；口径改成"复现 entries" |
| `_gap_table` 的容错会把"表在、形状坏"当成"没有表"，唯一一份账本被手改就静默消失 | 形状不对一律 `MonsterCatalogError`；只有"键不存在"才退化成空表 |

顺带修的：`Bonus_Action` 这种下划线泄漏进账本（改成人话）、`CR <= 1` 的裸魔数（命名 `DELIVERED_CR`）、`fetch_all` 里"CR 0–1"的过时注释、`source_ref` 从 `:animals` 改成 `:stat-blocks`（覆盖面已到食人魔与龙崽）。

另外新增 4 条导入器用例钉住缺口账本本身（Bonus Action 记缺口、攻击型 Recharge 记缺口、普通攻击不算缺口、不可解析的 Multiattack 记缺口）。
