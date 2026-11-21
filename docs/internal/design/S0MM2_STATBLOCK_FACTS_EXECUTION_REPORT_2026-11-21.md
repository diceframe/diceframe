# S0-MM2 执行报告：stat block 事实层补全

| 项 | 值 |
|---|---|
| 任务书 | `S0MM2_STATBLOCK_FACTS_TASK_SPEC_2026-11-21.md` |
| 分支 | `feat/dnd2024-s0mm2-statblocks` |
| 基线 | `main@d843f7a4`（含 #809 MM1、#822、#823） |
| 状态 | 已执行（20 条矩阵全过；3 处偏差见 §4） |

## 1. 交付内容

| 交付 | 位置 |
|---|---|
| **CR0–1 全类型目录，135 条** | `templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json` |
| 可复现生成器 | `scripts/rulesets/import_srd_beasts.py`（open5e v2，本地 CR 过滤） |
| 六字段校验 | `combat/validation.py::_validate_statblock_extras` |
| 共用派生函数 | `character/primitives.py::derived_save` / `derived_skill` |
| 敌人生效值装配 | `combat/resolution.py::_statblock_derived_checks` / `_proficiency_bonus_for_cr` |
| 视图投影 | `combat/view.py`（`skills` / `languages` / `subtype` / `hit_dice` / `passive_perception` / `proficiency_bonus`） |
| 目录访问 | `monsters/catalog.py::MonsterCatalog`（`get` / `find` / `all`） |
| 测试 | `tests/rulesets/test_dnd2024_monster_catalog.py`（19 条） |

## 2. 目录构成

```
135 条：beast 70 / humanoid 11 / fey 10 / monstrosity 9 / fiend 6 / undead 6 /
        elemental 6 / dragon 4 / plant 3 / construct 3 / celestial 3 /
        aberration 2 / ooze 1 / giant 1
CR：0×30、1/8×19、1/4×32、1/2×27、1×27
80 条带技能加值，46 条带语言，3 条无攻击（Giant Fly / Seahorse / Shrieker Fungus）
```

## 3. 关键语义

1. **六维真的参与计算**：装配时 `生效豁免 = 声明值 ?? 裸调整值`，`saving_throws_declared` / `saving_throws_derived` 两份都留在状态里，能区分"stat block 印错"与"规则差异"。
2. **覆盖即等价**：stat block 印的技能加值本身就是 `属性调整 + PB`，所以"覆盖"与"派生"在同一算式下自洽。
3. **`proficiency_bonus` 缺省按 CR 表**推导（0–4→2 … 29–30→9），显式给值优先。
4. **`passive_perception` 缺省由 `skills.perception + 10` 推**，两处都缺才留 0。
5. **目录与战斗共用一份 schema**：`MonsterCatalog` 逐条调用 `validate_enemy_profiles`，没有第二份校验。
6. **类型过滤是调用方的决定**：`find()` 默认全类型，`find(creature_type="beast")` 给 Wild Shape 用。

## 4. 偏差（三处，都影响契约）

1. **`source_ref` 没有页码**：open5e 的 v2 feed 不带页码索引，条目写成 `srd-5.2.1:creatures:<key>`，文件级 `source_ref` 用 `srd-5.2.1:p344-364:animals`。**页码精度低于任务书要求。**
2. **放宽了伤害式契约**：`DICE_RE` 只认骰式，而公式层本来就支持固定值（`1` 是常量节点）。Tiny 野兽（老鼠/蝙蝠）全是固定伤害，不改这条一半 Animals 进不来。做法是让校验调用公式层的同一判定（`is_supported_damage_formula`），**不是新写一套正则**。
3. **放宽了 `attacks` 下限 1 → 0**：Giant Fly / Seahorse / Shrieker Fungus 在 SRD 里没有攻击，但它们是合法形态与合法障碍物。没有攻击的敌人只是永远没有 attack 意图。

## 5. 数据侧发现的三个形状问题（只在非野兽身上出现）

| 问题 | 表现 | 处理 |
|---|---|---|
| `languages.data` 是对象数组 | 字符串化后被契约拒绝 | 取 `name`／退化 `key`，去重去空 |
| `speed.hover` 是布尔 | `True` 是 `int` 子类，被转成 `1`，契约报 `hover must be a boolean` | 显式按布尔处理 |
| `cr__lte` 查询参数不生效 | 传了仍返回 CR 0–8 | 本地过滤（与装备导入同一类问题） |

## 6. 测试

```
test_dnd2024_monster_catalog.py                                    19 passed
monster_facts + combat + bundle_loader + channel_control          131 passed
全量 tests/rulesets                                  2653 passed（18:12）
ruff / mypy                                                        干净
```

矩阵 20 条逐项：派生（1–5）、技能覆盖（6–7）、PB（8）、`hit_dice` 形状（9）、`subtype` 不影响类型判定（10）、语言（11）、被动察觉（12）、抗性取整 31→15（13）、三组互斥（14）、目录加载与拒绝（15）、查询（16）、未知字段（17）、旧敌人兼容（18）、玩家派生不变（19，既有测试）、视图投影（20）。

## 7. 装而未用（登记，不宣称可用）

| 字段 | 现状 |
|---|---|
| `hit_dice` | 只校验形状与投影；短休回血/Polymorph 尚未消费 |
| `languages` | 只存与投影；"能否说话"无消费者 |
| `subtype` | 只存与投影；`Beast` 判定仍只看 `creature_type` |
| `saving_throws_declared` / `skills_declared` | 已留字段，**尚未写进事件日志**（审计要等日志侧接线） |

## 8. 未决

1. **`counterspell_dc` 仍是硬编码特质**（`GAP-25`），本片没动。
2. **CR ≥ 2 的 196 条未导入**：`Multiattack` 占全量 54%，`attacks[]` 表达不了，盲导会让"数字完整、玩法残缺"。建议在 traits/actions 切片推进后再按 CR 段导入。
3. **页码缺失**：若要求逐条页码，需要换数据源或人工补 —— 现有 feed 无法提供。
