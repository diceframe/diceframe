# S0-F25 任务书修订：与已实现地基对齐

| 项 | 值 |
|---|---|
| 原任务书 | `S0F25_WILD_SHAPE_TASK_SPEC_2026-11-07.md`（docs/dnd2024-s0-specs 分支） |
| 修订原因 | 原书写于 MM2/MM3、`forms/` 抽象之前；六处已与实现分叉，照原文做会做错 |
| 状态 | 生效（本文件覆盖原文的对应条目，其余仍按原文） |

## 1. 六处必须对齐的分叉

| # | 原任务书 | 现状 | 修订 |
|---|---|---|---|
| 1 | 新目录 `content/creature_catalogs/srd_beasts.json` + locale 三份 + `character/creature_catalog.py` | `content/monster_catalogs/srd_monsters.json`（135 条 CR0–1 全类型）+ `monsters/catalog.py`，由 `scripts/rulesets/import_srd_beasts.py` 生成 | **以 `monster_catalogs/srd_monsters.json` 为唯一形态数据源**；Wild Shape 用 `catalog.find(creature_type="beast", cr_max=..., allow_fly=...)`。不再新建 creature_catalogs |
| 2 | 事件 `dnd2024.wild_shape.assumed` / `.reverted` | `dnd2024.form.assumed` / `.reverted`（Wild Shape 与 Polymorph 共用） | **统一用 `form.*`**；reason 里区分 `effect_id="wild_shape"` |
| 3 | 活跃状态含 `known_form_refs`、`equipment_modes`（逐 item） | `FormState` 只有 `equipment_mode` 单值，没有 known forms | **按原文改**：`equipment_modes: {item_ref: drop\|merge\|wear}`；`known_form_refs` 单独存于 `ruleset_character.forms.known`（不属于某次变形，跨变形存在） |
| 4 | 施法限制按组件判定：18 级只放行"无指定价格、不消耗材料" | `restrictions()` 只有 `beast_spells` 布尔 | **补组件元数据判定**，缺 `material_cost_specified` / `material_consumed` 时 fail closed |
| 5 | 白名单只到 `character/creature_catalog.py` | 新增了 `forms/panel.py`、`forms/state.py`、`monsters/catalog.py` | 白名单扩充这三个文件与 `monster_catalogs/srd_monsters.json` |
| 6 | 结束条件：到期 / 再变形 / 失能 / 死亡 / 主动离开 | 只有主动 `form.revert`，且无 `reason` 字段 | **补四种自动结束**，事件带 `reason`：`expired` / `reshaped` / `incapacitated` / `death` |

## 2. 仍按原文执行的部分（无需改）

- §4.1.2–4.1.5 形态数量 4/6/8、CR 上限 1/4→1/2→1、8 级前禁 Fly、长休 `wild_shape_replacement={old,new}` 只换一个
- §4.2.3–4.2.4 保留/替换清单与"技能豁免取较高、原熟练用自己的 PB"（已由 `forms/panel.py` 的策略表实现）
- §4.2.5 THP = 等级、keep-max、再变形先结束旧形态再付一次
- §4.3.3 持续效果跨形态保留、不产生 `concentration.ended`
- §4.4.1–4.4.5 Wild Resurgence 双向、Evergreen、Nature Magician（`source` 字符串与 2N 槽位规则原样保留）
- §4.4.6 Longevity 只声明 `physical_years_per_decade=1`

## 3. 与 `forms/` 抽象的衔接（原文未写，补充）

1. **方案合成**：`forms.panel.compose(character_panel, form_panel, policy)` 是唯一合成入口；Wild Shape 用 `WILD_SHAPE_POLICY`，Polymorph 用 `POLYMORPH_POLICY`。
2. **状态归属**：`ruleset_character.forms = {active: {...}, known: {wild_shape: [...]}}`。`active` 存本次变形的 ref 与开关，`known` 存跨变形的已知形态。数值一律不落存档。
3. **攻击资格**：形态的 `attacks[]` 要出现在可用动作里，不能只在视图里换字段；`available_intents` 增加形态攻击来源（#835 已提供 MM 攻击 schema）。
4. **装备屏蔽**：`merged` 的装备必须从 AC / 攻击 / 熟练 / 效果计算中排除，而不是只从 UI 隐藏。

## 4. 剩余工作的执行顺序（按可独立验证切分）

```
R1  已知形态：known_form_refs 选择 + 建卡/升级校验 + 长休替换
R2  装备逐 item 三态 + merged 真正屏蔽
R3  施法限制按组件判定（含 18 级白名单）
R4  四种自动结束 + reason（到期/再变形/失能/死亡）
R5  Wild Resurgence 双向 + Evergreen
R6  Nature Magician + Longevity 声明
```

R1 与 R2 各自可独立验收；R3 依赖 R2（装备屏蔽要先对）；R5 依赖 F6 的临时槽（已交付）。

## 5. 停止条件（沿用原文并补三条）

1. 形态数据不在目录里（例如只有测试夹具）→ 停止。
2. 需要一个新事件类型才能表达结束原因 → 停止（应复用 `form.reverted` 的 `reason`）。
3. 装备屏蔽要通过"复制一份角色卡"实现 → 停止（应在派生处排除，不是复制）。
4. 施法限制缺组件元数据却选择"一律允许"或"一律禁止" → 停止。
