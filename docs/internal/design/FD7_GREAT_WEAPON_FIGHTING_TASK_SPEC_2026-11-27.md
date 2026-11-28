# FD7 任务书：巨武器战斗（Great Weapon Fighting）

- 日期：2026-11-27
- 状态：进行中
- 决策：**官方口径** —— 只作用于该武器的伤害骰，不包含偷袭、印记、至圣斩等附伤
- 边界：只补这一条专长的机械；顺带修 FD6a 引入的战斗风格白名单回归

## 1. 规则原文与判决

### 1.1 原文

`.rulebooks/dnd5r/玩家手册2024/专长/战斗风格专长.htm` L31-32：

> 巨武器战斗 Great Weapon Fighting
> 战斗风格专长（先决：战斗风格特性）
> 当你用双手持握的一把近战武器发动了一次攻击并为其进行伤害掷骰时，若该武器具有**双手**或**多用**词条，那么你便可以将伤害骰投出的 1 和 2 **都视为 3**。

2024 版把 2014 的「重掷」改成了「视为 3」。因此本切片**不需要**伤害骰重掷原语（GAP-54 仍然只挡 Savage Attacker 与 2014 版语义），也不需要第二次 RNG 或玩家决策。

### 1.2 三种可能的读法

| 读法 | 内容 | 结果 |
|---|---|---|
| A. 武器伤害骰（采纳） | 只作用于本次攻击的武器主骰 | 贤者谏言对 2014 版的判决；2024 版措辞未推翻它 |
| B. 该次攻击的全部伤害骰 | 连偷袭、猎人印记、至圣斩、命中 rider 一起吃 | 网团常见房规，爽感更好 |
| C. 所有伤害骰 | 包括不是由这次攻击产生的伤害 | 明显过宽，不予考虑 |

**采纳 A。** 理由：

1. 战斗风格描述的是「你用这把武器的方式」，不是「这次攻击总共掷了几颗骰」。这个依据在 2024 没有变——GWF 仍是武器训练。
2. 引擎默认值必须是可辩护的参考实现。默认包附伤时，被放大最多的是多骰 build（偷袭、至圣斩、印记），等于系统性地奖励特定流派；所有 DPS 基线与官方模组对照都会随 build 偏移。
3. 「网团都这么判」是真实需求，但它属于**桌子的解释**，不属于 SRD 事实。把它写成默认值会让「官方 SRD」与「房规」混在同一份内容包里，以后无法对照。

读法 B 登记为 **GAP-58**，不删。它的前置条件不是「多写几行」，而是先把攻击附伤统一到一个 settlement 上下文（见 §3）。

### 1.3 不引入 `scope` 参数的原因

不声明 `two_handed_melee_damage_die_scope: "weapon_dice" | "attack_dice"`：

- `attack_dice` 分支在附伤路径接完之前就是「读了一半」的状态，而参数注册表的完整度门禁不允许 deterministic 专长含未读完的参数；
- 更实际的是半成品风险：主伤害里的 hex / 偷袭会被钳，`hit_riders`、`extra_damage_parts`、至圣斩不会。玩家以为开了房规，实际只开了一半。

作用域等 GAP-58 的前置完成后，再作为独立参数加入。

## 2. 契约

### 2.1 声明

`templates/rulesets/dnd2024_srd/content/advancement_feat_catalogs/srd_advancement_feats.json`：

```json
"great_weapon_fighting": {
  "category": "fighting_style",
  "source_ref": "srd-5.2.1:p88:great-weapon-fighting",
  "requires": [{"kind": "feature", "id": "fighting_style"}],
  "repeatable": false,
  "parameters": {"two_handed_melee_damage_die_minimum": 3}
}
```

为什么进 `parameters` 而不是 `passive_effects` / `capabilities`：

- `passive_effects` 的白名单只管派生数值（当前只有 `armor_class_bonus`），GWF 是战斗内掷骰；
- `capabilities` 是主动行动，GWF 没有行动；
- 与 `archery.ranged_attack_bonus`、`two_weapon_fighting.light_extra_attack_ability_damage` 同族：**条件写进参数名，数值写进值**。

### 2.2 注册表

`features/parameter_registry.py` 新增：

```python
"two_handed_melee_damage_die_minimum": ParameterSpec(
    "int", stage="combat",
    reader=("rulesets/dnd2024/features/resolver.py",
            "_two_handed_melee_damage_die_minimum"),
    minimum=2, maximum=12,
),
```

先写读者再登记 `stage="combat"`，否则完整度门禁与读者守卫会先红。

### 2.3 投影

- `features/resolver.py`：`_two_handed_melee_damage_die_minimum(character)` 遍历 `features.feat_refs`，取所有声明值的 **max**（重复 ref 不叠加），投影键同名；
- `combat/view.py`：actor 视图透传，默认 `0`。

resolver 只回答「这名角色有没有、值多少」；「这次攻击算不算双手近战」由 combat 判——它才知道武器、握法与意图。

### 2.4 适用性（combat）

在 `hit_resolution.py::_hit_settlement_events` 判定，三个条件同时成立：

1. 近战：`not weapon.get("ranged")`，且非徒手。引擎无法区分「投掷」与「近战使用」（`attack_is_ranged` 的既有注释已说明），投掷长矛沿用这条边界，不在本切片新开一个判断；
2. 双手持握：
   - `weapon_ref in two_handed_weapon_refs(actor, catalog.weapons)`（该纯函数已处理骑枪的骑乘例外；`primitives.py` 单一实现，validation 与结算共用），或
   - `intent.get("two_handed")` 且 `weapon.get("damage_two_handed")`；
3. 非徒手：`not weapon.get("unarmed")`。

**复用 `two_handed_weapon_refs`，不写第二套握法判定。** 这是 FD6a/F17 反复出现的裂缝来源：同一语义两处实现，第二处先漏。`primitives.py` 里的纯函数版本也顺带解决了 mixin 私有方法跨类调用不可类型检查的问题。

### 2.5 掷骰

`combat/action_adapter.py::roll_damage_node` 新增两个可选参数：

```python
def roll_damage_node(node, rng, *, die_floor: int = 0, floored_dice: int = 0):
```

在 dice_roller 内对**前 `floored_dice` 颗**骰取 `max(value, die_floor)`。前置条件由 `attack_damage_node` 保证：主骰在 AST 最前，附加骰按调用方顺序在后（`action_adapter.py` 的既有注释）。

- `floored_dice` = 主骰颗数 × （暴击 ? 2 : 1）；固定值伤害记 0；
- RNG 调用次数与顺序**完全不变**（不是重掷）；
- `amount - sum(rolls) = modifier` 的既有不变量保持不变。

不改 `src/engine/combat_formulas.py`，不新增 AST op：D&D 语义留在 ruleset 层。

### 2.6 事件

伤害事件（仅在实际生效/抬升时）：

```json
"damage_die_floor": 3,
"floored_rolls": [[1, 3], [2, 3]]
```

`rolls` 存生效值，前端可以显示「掷出 1 → 视为 3」。不加这两个字段时事件与旧版逐字节一致。

## 3. 不做什么

- 不覆盖附伤（`hit_riders` / `extra_damage_parts` / `statblock_riders` / 至圣斩独立事件）→ GAP-58；
- 不复用 `maximize_dice_nodes`：它是「骰节点替换为常量」，语义不同，且会误伤额外骰；
- 不引入 `scope` 参数（§1.3）；
- 不改 `archery` / `defense` / `two_weapon_fighting` 的既有机械。

## 4. 测试矩阵

新增 `tests/rulesets/test_dnd2024_great_weapon_fighting.py`：

1. 双手武器 2d6 掷 1/2 → 3/3，amount 同步；
2. 掷 5/6 不变，事件不带 `damage_die_floor`；
3. 暴击 4d6 全部钳制；
4. 多用武器单手不生效；
5. 多用武器 `two_handed` 持用生效；
6. 远程双手武器（重弩）不生效；
7. 投掷不生效；
8. 徒手不生效；
9. 无专长不生效；
10. 猎人印记 / 偷袭的附骰不被钳制（官方口径的边界）；
11. 骑枪骑乘例外不生效；
12. RNG 消耗次数与骰数不变；
13. 参数值域（0 / 非整数被拒）；
14. 事件字段与 `sum(rolls)` 自洽。

## 5. 顺带修复：战斗风格白名单

`FD6a` 把四个 `fighting_style` 槽的 `enabled_options` 写成字面量 `["feat:archery", "feat:defense"]`，把 main 上已完整实现的 `two_weapon_fighting` 也挡掉了（建卡与升级都报 `not an available fighting style`）。

修法：不再手写清单。解析期对 `category == "fighting_style"` 的 `advancement_feat` 槽，用专长自身的机械载荷（`parameters` / `passive_effects` / `capabilities` 任一非空）推导 `enabled_options`。GWF 补上参数后自动可选；以后新增风格不用改四处 JSON。

守卫测试（`test_dnd2024_fighting_style_slots.py`）：

- `test_the_whitelist_is_what_actually_has_an_effect`：四个槽的可选集 == 有载荷的战斗风格集合；
- `test_advancement_feat_slots_do_not_hardcode_options`：模板里这些槽不得再出现非空清单，防止第二份真相长回来；
- `test_a_style_without_a_payload_is_not_effective`：无载荷的声明 `has_mechanical_effect is False`。

## 6. 未决

- GAP-58 的作用域扩展（房规）；
- 前端是否要显示「1→3」：本期只落事件字段，不改面板。

## 7. 执行记录

### 7.1 落点与偏差

| 计划 | 实际 |
|---|---|
| `roll_damage_node` 加 `die_floor` / `floored_dice` | 另加 `roll_damage_node_with_trace` 返回被抬升的骰；14 个既有调用点零改动 |
| 适用性判定放 `_hit_settlement_events` | **移到 `attack_resolution`**，结果放进 `settlement["die_floor"]`。原因：`hit_resolution` 新增 `intent.get("two_handed")` 会被耦合守卫记为新的 `intent_switch` 债务，而 `attack_resolution` 已有同键债务；窗口结清路径改读 settlement，顺带保证「改判命中不改变握法」 |
| 新建 `feat_parameters.py` | 原计划放进 `resolver.py`，但 `resolver.py` 已 1964 行，超体积棘轮 100 行缓冲；GWF 读者单独成模块，注册表锚点也变成真实读取者 |
| 白名单推导 | 只填**空**清单；显式清单仍校验，另加守卫测试禁止槽内出现非空清单 |

### 7.2 顺带修复的既有回归

`FD6a` 声明化战斗风格槽后，Paladin 2 级升级开始**要求**一个风格选择，但 `smite` / `counterspell` / `scorching_ray` 三个测试的建卡 helper 仍只提交法术选择。这些测试在 FD6a 之后一直是红的，本次按权威 `preview` 补上选择。

同一批：`feat_requirements` 与架构守卫的 `LEGACY_FIELD_BASELINE` 仍把已迁移的 `ability_choice` 当成 legacy；`improved_critical` 的 Fighter 1 级特性列表少了 `fighting_style`。

### 7.3 验证

- `ruff check src/ tests/`：干净；
- `mypy`（pyproject `files` 清单，143 文件）：0 错；
- 新增 `tests/rulesets/test_dnd2024_great_weapon_fighting.py` 11 条：全绿；
- `tests/architecture` + GWF：181 passed；
- 全量 `pytest tests/ -n 6 --dist loadfile`：见 §7.4；
- 本机已知基线（docker launcher / updater / 依赖安装）不复现于 Linux CI。

### 7.4 全量结果

`pytest tests/ -n 6 --dist loadfile`（本机，basetemp 在 E 盘）：

| 轮次 | 结果 | 失败构成 |
|---|---|---|
| 第一次 | 29 failed / 11343 passed / 3 errors | 17 条 FD6a/FD2a 遗留（smite 10、counterspell 3、improved_critical 1、free_casting 1、scorching_ray 1、feat_requirements 1）；2 条架构基线（`resolver.py` 体积、`hit_resolution` 新增 `intent_switch`）；10 条本机 docker/updater 基线 |
| 第二次 | **10 failed / 11362 passed / 3 errors** | 全部是本机既有基线：docker launcher 6、updater MAX_PATH 1、docker data generation 1、self-update reentry 1、runtime dependencies 1，外加 docker 升级 3 errors；Linux CI 不复现 |

第一次的 17 条与 2 条架构失败已在本次修掉（§7.2 / §7.1），第二次没有新增失败。
