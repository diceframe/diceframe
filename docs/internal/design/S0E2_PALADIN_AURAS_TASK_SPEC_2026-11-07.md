# S0-E2 任务书：Paladin 光环、豁免加值与 Charmed 免疫

| 项 | 值 |
|---|---|
| 切片编号 | S0-E2（独立新任务；不执行、不覆盖旧 S-P6） |
| 验收载体 | `aura_of_protection`、`aura_expansion`、`aura_of_devotion` |
| 通用能力 | 1D Emanation 的实时资格求值、同名光环选择、豁免平坦加值、条件免疫拦截 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；p55–56 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`；必须先执行并合入 S0-A、S0-E1 |
| 前置任务 | S0-A 的 owner-scoped 授予；S0-E1 的战斗特性投影与串行集成基线 |
| 建议分支 | `feat/dnd2024-s0e2-paladin-auras` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 本任务书是本切片唯一需求来源。不要读取旧 S-P6 或其他施工计划扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. 开工时记录 S0-E1 合入后的实际集成提交，并从该提交新建干净工作树。不要直接复用已有施工工作树，不要清理无关未跟踪文件。
3. 若 S0-A 或 S0-E1 尚未合入，停止。尤其不得重新引入“只按短 id 查特性”或“子职 JSON 已自动生效”的旧假设。
4. 本切片实现三个特性：Paladin 6 级 Aura of Protection、18 级 Aura Expansion、Oath of Devotion 7 级 Aura of Devotion。
5. 不实现 Aura of Courage、Abjure Foes、Sacred Weapon、Smite of Protection、Holy Nimbus，也不补 Paladin 其他职业/子职能力。免疫求值器必须可扩展，但不得为未验收特性虚报完成。
6. 使用当前 theater-of-mind 一维位置：`distance = abs(position_a - position_b)`。不引入 2D、格子、墙体、遮挡、光照或 AoE 几何。
7. 不使用 `.rulebooks`、open5e、2014 SRD 或第三方译文作为规则依据；第 2 节官方英文原文是本任务规则基准。

## 2. 官方规则与语义锁定

### 2.1 Aura of Protection（SRD p55）

> **Level 6: Aura of Protection**
>
> You radiate a protective, unseeable aura in a 10-foot Emanation that originates from you. The aura is inactive while you have the Incapacitated condition. You and your allies in the aura gain a bonus to saving throws equal to your Charisma modifier (minimum bonus of +1).
>
> If another Paladin is present, a creature can benefit from only one Aura of Protection at a time; the creature chooses which aura while in them.

### 2.2 Aura Expansion（SRD p55）

> **Level 18: Aura Expansion**
>
> Your Aura of Protection is now a 30-foot Emanation.

### 2.3 Aura of Devotion（SRD p56）

> **Level 7: Aura of Devotion**
>
> You and your allies have Immunity to the Charmed condition while in your Aura of Protection. If a Charmed ally enters the aura, that condition has no effect on that ally while there.

锁定以下行为：

1. 基础半径为 10 尺，持有者本人距离为 0，因此本人也受益；18 级后半径为 30 尺。边界值包含在内。
2. 当前受益者限定为同队 player/companion；敌方不受玩家 Paladin 光环保护。不为 enemy 新建职业特性框架。
3. 光环持有者具有任何 `implies_incapacitated` 的有效条件时，该持有者的整个 Aura of Protection 及其 Aura of Devotion 扩展均失效。不是只有持有者本人失去加值。
4. 豁免加值为 `max(1, 持有者当前 Charisma modifier)`；负调整值同样保底 +1。它适用于法术豁免、回合末重复豁免、受伤专注豁免与死亡豁免，不适用于攻击、能力检定、Counterspell 的 ability check 或 Medicine。
5. 自动失败的 Strength/Dexterity 豁免仍不掷骰、不计算加值；加值不能把自动失败改为普通失败。
6. 多个 Aura of Protection 不相加。为保持被动结算无额外弹窗，服务器从当前有效且覆盖受益者的光环中选择**数值最高**者；同值按 initiative、再按 actor id 稳定选源。官方允许受益者选择，本确定性策略只替代数值完全可由加值比较的选择，不叠加光环，也不抑制各自独立的 Aura of Devotion 免疫。
7. Aura of Devotion 依附于该 Devotion Paladin 自己有效的 Aura of Protection，使用同一 10/30 尺范围和 Incapacitated 门控；不能脱离基础光环独立存在。
8. 在有效 Aura of Devotion 内，新的 Charmed 条件不得被写入。事件流保留原豁免结果，并在对应 `check.resolved` 增加拦截审计字段；不得伪造豁免成功。
9. 已有 Charmed 进入光环时是**暂时无效果，不是移除**。不得发 `condition.removed`、清除持续时间和来源元数据或结束施法者专注；离开光环后原条件继续按剩余持续时间生效。
10. 当前 `charmed` 没有机械 flags。此事实只意味着本切片无需新增“被压制”状态字段，不意味着可以删除条件。未来 Charmed 机械消费者必须经过统一有效条件查询；本切片不得借补全 Charmed 全规则扩大范围。
11. 所有求值均读取结算时的位置、条件、属性和实际特性，不把受益者列表或半径写入永久状态。移动、传送、恢复意识、升级和 reducer 重放后自然得到相同结果。

## 3. 已核实的代码事实

| 事实 | 位置 |
|---|---|
| 基线 `features_for()` 不读取 `features.subclass_ref`，Aura of Devotion 必须等 S0-A 后才可真实授予 | `features/resolver.py::features_for` |
| Paladin progression 已在 6/18 级列出 `aura_of_protection` / `aura_expansion`；Devotion 子职表已在 7 级列出 `aura_of_devotion` | `content/progression_catalogs/srd_classes.json`、`content/subclass_catalogs/srd_subclasses.json` |
| 三个 canonical 特性定义尚不存在，不能仅靠 progression 名称产生效果 | class feature catalog |
| 一维位置与距离辅助已经存在 | `combat/engine.py::_position`、`_distance` |
| `condition_flags()` 已从 catalog 聚合 `implies_incapacitated`；`incapacitated`、`paralyzed`、`stunned`、`unconscious` 均按此声明 | `combat/check_modes.py`、`srd_combat_core.json` |
| 法术 saving throw 目前在目标基础豁免上加 Bless；自动失败分支不掷骰 | `combat/resolution.py::_spell_events` |
| 条件统一在 spell effect 末尾构造 `condition.applied`，包括 save repeat 元数据 | 同上 |
| 回合末重复豁免独立掷骰并构造 `check.resolved` | `combat/resolution.py::_save_repeat_events` |
| 死亡豁免当前是裸 d20，未读豁免调整值或光环 | `combat/resolution.py::_death_save_events` |
| 受伤专注豁免当前在 engine 辅助中结算；调用点横跨 resolution、reaction_spells、triggered_spells | `combat/engine.py::_concentration_events` 及全部调用点 |
| Shield / Uncanny Dodge / Divine Smite 的最终伤害后才调用专注辅助 | `combat/reaction_spells.py`、`combat/triggered_spells.py` |
| `charmed` 目前没有 condition registry 机械声明；`charm_person` 是现成的真实施加源 | `srd_combat_core.json` |
| reducer 只机械应用 `condition.applied/removed`；在事件构造前拦截无需修改 reducer | `combat/reducer.py` |
| combat feed 对事件字段有显式白名单，新审计字段必须登记 | `runtime.py` 的 combat feed 投影 |

## 4. 实现契约

### 4.1 特性声明

在 canonical Paladin 特性段增加以下三个定义；`owner_ref` 使用 S0-A 的身份模型：

```json
{
  "id": "aura_of_protection",
  "owner_ref": "class:paladin",
  "minimum_level": 6,
  "source_ref": "srd-5.2.1:p55:aura-of-protection",
  "summary": "While not Incapacitated, you and allies within 10 feet gain a saving-throw bonus equal to your Charisma modifier (minimum +1); only one such aura benefits a creature at a time.",
  "parameters": {
    "aura_range_ft": 10,
    "aura_save_bonus_ability": "cha",
    "aura_save_bonus_minimum": 1
  }
}
```

```json
{
  "id": "aura_expansion",
  "owner_ref": "class:paladin",
  "minimum_level": 18,
  "source_ref": "srd-5.2.1:p55:aura-expansion",
  "summary": "Your Aura of Protection is now a 30-foot Emanation.",
  "parameters": {
    "aura_expands_feature_id": "aura_of_protection",
    "aura_range_override_ft": 30
  }
}
```

```json
{
  "id": "aura_of_devotion",
  "owner_ref": "subclass:oath_of_devotion",
  "minimum_level": 7,
  "source_ref": "srd-5.2.1:p56:aura-of-devotion",
  "summary": "You and allies in your Aura of Protection have Immunity to Charmed; an existing Charmed condition has no effect while there.",
  "parameters": {
    "aura_source_feature_id": "aura_of_protection",
    "aura_condition_immunities": ["charmed"]
  }
}
```

1. 三个特性都必须由真实 progression/subclass 授予；`minimum_level` 与表一致。Aura Expansion 不是把基础特性改为第二份 30 尺光环，而是覆盖同一光环的范围。
2. 英文/中文 locale 同步：Aura of Protection／守护灵光，Aura Expansion／灵光扩展，Aura of Devotion／奉献灵光。canonical id、owner_ref、参数键和 condition id 不本地化。
3. 不修改 progression、subclass、condition、spell、resource 或 rest catalog；不为 Aura of Devotion 复制一个独立半径。
4. `aura_source_feature_id` 和 `aura_expands_feature_id` 是目录关系声明，不是引擎按特性名写死的分支。

### 4.2 参数校验

在既有声明校验边界增加 fail-closed 校验：

1. `aura_range_ft`、`aura_range_override_ft` 必须是非 bool 的正整数。
2. `aura_save_bonus_ability` 必须是六属性 canonical id；`aura_save_bonus_minimum` 必须是非 bool 的 `int >= 1`，且与 ability 成对出现。
3. `aura_condition_immunities` 必须是非空、去重后的 canonical condition id 字符串数组；本切片只支持 `charmed`。
4. `aura_source_feature_id` / `aura_expands_feature_id` 必须指向同一 catalog 中存在的基础光环定义；不得指向自身或形成扩展环。
5. 声明扩展条件免疫但没有 source、声明范围覆盖但没有目标、目标没有 `aura_range_ft` 均拒绝加载。
6. 校验只针对本组参数，不重写 S0-A/S0-E1 的授予或 trigger 校验，不改 `content_version`。

### 4.3 共享光环求值器

在 combat mixin 宿主中实现两个内部只读辅助；可放 `engine.py`，不得放 reducer：

```python
def _saving_throw_aura_bonus(
    self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
) -> dict[str, Any]:
    ...

def _aura_condition_immunity_source(
    self, instance: Any, combat: dict[str, Any], target: dict[str, Any], condition_id: str,
) -> dict[str, str] | None:
    ...
```

共同规则：

1. 只遍历当前 player/companion 持有者，解析其 `active_features_for()`；敌人和无有效 class state 的角色不产生职业光环。
2. 基础光环候选来自实际拥有且声明 `aura_range_ft + aura_save_bonus_*` 的定义；持有者 `condition_flags(...)[\"incapacitated\"]` 为真时跳过其基础光环及所有依附扩展。
3. 有效范围取该持有者对同一基础特性的合法 `aura_range_override_ft`；没有覆盖时用基础值。范围不是全局最大值，而是每个持有者分别计算。
4. 受益者必须为基础光环持有者本人或同队盟友，且当前距离 `<= range`。同一持有者的多个扩展不复制基础豁免加值。
5. `_saving_throw_aura_bonus` 返回 `{bonus, sources}`：`bonus` 无覆盖时为 0；有覆盖时只取最高值，`sources` 仅包含被选中的一个来源。来源至少含 `feature_id`、`owner_ref`、`source_actor_id`、`range`、`ability`、`ability_modifier`。
6. `_aura_condition_immunity_source` 独立遍历每个有效基础光环的依附扩展；目标位于任一 Aura of Devotion 内即免疫。多个来源按稳定顺序取首个作审计，不把多个免疫写成多段结算。
7. 两个辅助均不掷骰、不创建事件、不写 canonical 状态；不得出现 `paladin`、`aura_of_protection`、`aura_of_devotion` 字面特性分支，身份只作为目录数据透传。

### 4.4 全部 saving throw 消费点

1. **法术豁免**：非自动失败分支在基础 `modifier` 之外加入 aura bonus；`total = d20 + modifier + bless + aura_bonus`。保留 `modifier` 为角色原豁免值，新增 `aura_bonus` 与 `aura_sources`，不要暗中并入 Bless。
2. **重复豁免**：使用相同辅助和事件字段； aura 按本次回合末结算时的位置/失能状态计算，不使用施放时快照。快照中的 DC 和 save ability 不变。
3. **死亡豁免**：新增 `modifier: 0`、`bless_bonus: 0`、`aura_bonus`、`aura_sources`、`total`；判定普通成功使用 `natural + aura_bonus >= 10`。天然 20 和天然 1 的专属分支优先于普通成功/失败，Survivor 后续阈值不得破坏这一优先级。
4. **专注豁免**：为 `_concentration_events` 增加 `instance`、`combat` 参数，在既有 d20 + 基础 Con save + Bless 上加 aura bonus，并更新全部调用点。不得只在普通攻击路径接入而漏掉 Shield、Uncanny Dodge、Divine Smite 或法术伤害。
5. **自动失败与 ability check**：自动失败分支保持 `rolls=[]`、`total=0`、无 RNG；Counterspell、initiative、Medicine、普通技能检定和攻击检定不调用本辅助。
6. 无光环时新增审计字段使用 `aura_bonus: 0`、`aura_sources: []`，既有数值、骰序与 reducer 行为完全不变。

### 4.5 Charmed 免疫与保留条件

1. 在 spell effect 的 `condition.applied` 统一构造点、append 之前调用免疫辅助；只拦截事件目标当前免疫的 condition。
2. 被拦截时不发该 `condition.applied`，也不因其触发“失能打断专注”等后续效果；Charmed 当前虽非失能，这一顺序仍必须通用正确。
3. 在同一效果对应的 `check.resolved` 上添加：`blocked_condition: \"charmed\"`、`blocked_by_aura: {feature_id, owner_ref, source_actor_id}`。如果某路径没有 check event，则新增一条既有类型 `dnd2024.combat.message` 只作可见审计；本切片的 charm_person 验收路径必须有 check event。
4. 豁免成功/失败、伤害、其他 condition、专注结束和 RNG 顺序保持原样。Charm Person 豁免失败但被免疫时仍是失败豁免。
5. 对已有 Charmed 的移动/传送不发 remove、不写 suppression metadata；通过移动前后对免疫辅助和原始 condition 的联合断言验证“在外有效、在内无效、条件未删”。
6. 当前没有 Charmed 机械 flags，不新增虚假的 `suppressed_by_aura` 永久状态。若执行时发现基线已给 Charmed 增加机械消费者，停止并报告，由集成裁决是否需先建立统一有效条件投影。

### 4.6 可观察性与 reducer

1. 不新增 canonical combat 状态，不新增 reducer 事件类型，不在 reducer 内计算光环。
2. 将 `aura_bonus`、`aura_sources`、`blocked_condition`、`blocked_by_aura` 加入 combat feed 的字段白名单；不得暴露整份 character sheet 或光环候选全集。
3. 重放同一事件序列只落账既有 HP、condition、concentration、death save 状态；豁免数值保留在事件审计中，结果确定。
4. 战斗内 UI 的“当前受益光环列表”不是本切片交付物；feed 中的数值与拦截来源必须可见。

## 5. 改动白名单

只允许修改以下文件；需要任何其他文件时停止并报告：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resolver.py`：仅 §4.2 参数校验
5. `src/rulesets/dnd2024/combat/engine.py`
6. `src/rulesets/dnd2024/combat/resolution.py`
7. `src/rulesets/dnd2024/combat/reaction_spells.py`
8. `src/rulesets/dnd2024/combat/triggered_spells.py`
9. `src/rulesets/dnd2024/runtime.py`：仅 combat feed 字段白名单
10. 新建 `tests/rulesets/test_dnd2024_paladin_auras.py`
11. 本任务书：只允许追加第 10 节执行报告

明确不改 reducer、view、check_modes、validation、features/models、前端及 progression/subclass/combat/spell/resource/rest catalog。若 S0-A 的 owner-scoped 投影不足，停止，不在本切片补做地基。

## 6. 测试矩阵

新测试必须通过真实 `resolve_intent -> apply_batch` 链验证运行时行为；只对纯求值器的非法参数、同值 tie-break 和多光环选择允许直接单元测试。Paladin/Devotion 角色通过真实升级链构造，不得手写免疫事件。

### 6.1 授予、声明与校验

1. Paladin 5 级无 Aura of Protection；6 级有；17 级范围 10；18 级范围 30。
2. Devotion Paladin 6 级无 Aura of Devotion，7 级有；非 Devotion 或缺失 subclass_ref 时没有。
3. 非 Paladin 伪造短 id 或其他子职不能获得这些特性；三个 owner_ref 与等级正确。
4. 英文/中文标签、source_ref 正确；参数键、owner_ref、condition id 不本地化。
5. 非法范围、bonus minimum、ability、缺失成对字段均 fail closed。
6. 空/重复/非字符串/未知 immunity，缺失或自引用 source、扩展环、目标不是基础光环均 fail closed。

### 6.2 Aura of Protection 数值与范围

7. 6 级 Paladin 本人进行法术豁免，加值为 `max(1, Cha mod)`，事件 `modifier` 不含 aura、`aura_bonus` 独立且 total 正确。
8. 同队盟友距离 10 时受益，11 时不受益；18 级时 30 受益、31 不受益。
9. 魅力调整值为负、0、正值时分别得到 +1、+1、对应正值。
10. 敌方目标不受玩家光环；无 Paladin、5 级 Paladin 和光环外目标均为 `aura_bonus=0`。
11. 持有者带 `incapacitated`、`paralyzed`、`stunned` 或 `unconscious` 时，本人及盟友均不获其光环；另一有效 Paladin 的光环仍可用。
12. 两个不同加值光环覆盖同一目标时只取最高值，不相加；sources 只有被选者。移出较高者范围后立即使用另一者。
13. 同值光环按约定稳定选源；重放和重复求值来源一致。
14. Aura Expansion 只扩大拥有它的持有者，不扩大 6 级队友的光环，也不产生第二份豁免加值。

### 6.3 所有豁免面与 RNG

15. 法术 saving throw 的 aura bonus 与 Bless d4 同时存在且分项记录；只增加既有 Bless 的一枚 d4，不增加额外 RNG。
16. 回合末 `save_repeat` 使用结算当时的光环；DC/ability 快照不变，成功仍移除原条件。
17. 受伤专注豁免获得 aura bonus，普通武器、直接法术两条路径均验证。
18. Shield 未改判、Uncanny Dodge 半伤及 Divine Smite 合并伤害后的专注豁免均获得一次且仅一次 aura bonus。
19. 死亡豁免获得 aura bonus；天然 9 在 bonus +1 时可普通成功，天然 1 仍双失败，天然 20 仍恢复 1 HP。
20. 自动失败 Str/Dex 豁免保持 `rolls=[]`、`auto_failed=true`，不消耗 RNG，不附加 aura 改判。
21. Counterspell ability check、Medicine、initiative 和普通攻击不含 aura bonus，骰序不变。
22. 无光环回归场景的 check 数值、事件序列和 RNG 消耗与基线一致。

### 6.4 Aura of Devotion

23. 盟友在 10 尺内 Charm Person 豁免失败：check 仍失败，但无 `condition.applied(charmed)`，审计含 blocked_condition 和唯一来源。
24. 距离 11 时正常获得 Charmed；18 级时 30 内免疫、31 外不免疫。
25. Devotion Paladin 本人免疫；非同队目标、6 级 Devotion、非 Devotion Paladin 不免疫。
26. 持有者 Incapacitated 时 Aura of Devotion 同时失效；盟友仍可能受另一有效 Devotion 光环保护。
27. Blinded、Incapacitated 等其他 condition 不被 Aura of Devotion 拦截；其原伤害与豁免效果不变。
28. 已有 Charmed 的角色进入光环：无 `condition.removed`，原 duration/source/concentration 元数据保留，免疫查询有效；移出后不再免疫，原条件仍在。
29. 免疫拦截不提前结束施法者专注，不清除其他目标既有 Charmed，不发假 suppression 事件。
30. 双 Devotion 光环只产生一次拦截和一条稳定审计来源；豁免 RNG 与无光环失败路径一致。

### 6.5 持久化与回归

31. 对含 aura bonus 的法术豁免、专注和死亡豁免事件 apply 后重放，最终 HP、conditions、concentration、death saves 一致。
32. 战斗 feed 显示 aura 加值及 Charmed 拦截来源，不泄露未白名单字段；存档加载后当前位置求值一致。
33. 既有 Charm Person、Color Spray、Sleep、Hold Person、Bless、Rage、Shield、Uncanny Dodge、Divine Smite、Counterspell 回归通过。

## 7. 非目标与停止条件

### 7.1 非目标

- Aura of Courage、Holy Nimbus、Smite of Protection、Sacred Weapon、Abjure Foes 及 Paladin 全职业补齐。
- Charmed/Frightened 全机械规则、通用 suppression DSL、敌方光环、2D/3D 几何、遮挡、光照。
- 多光环玩家选择弹窗、光环面板、前端重构、通用条件加值 DSL。
- 修正与本切片无关的既有 death save、Bless、concentration 或 condition 缺陷。

### 7.2 停止条件

1. S0-A/S0-E1 未合入，或真实子职授予、owner_ref、actor 投影与契约不符。
2. 当前基线已出现同名特性或另一光环实现，存在并行施工冲突。
3. Charmed 已有机械消费或项目已引入 suppression 状态，§2 的“保留原条件且无机械 flags”前提失效。
4. 存在本任务列出的四类 saving throw 之外的实际 saving throw 结算点，接入需白名单外文件。
5. `_concentration_events` 调用方或事件 schema 已重构，无法在不破坏 pending decision 的情况下传递 instance/combat。
6. 多 Aura of Protection 需要持久化玩家选择才能保持其他已合入规则正确，或最高值确定性策略与既有选择契约冲突。
7. 最小测试 7、23 无法在不改 reducer/view 的情况下通过，或需要伪造 canonical 状态才能构造真实角色。
8. 任何修复需要改白名单外文件、迁移 content_version 或放宽既有事件/reducer 校验。

## 8. 验收命令

以下命令使用项目实际可用的 Python 环境；若仓库没有 `.venv`，记录所用解释器，不得因路径示例而跳过测试。

```powershell
python -m pytest tests/rulesets/test_dnd2024_paladin_auras.py -q
python -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_dark_ones_blessing.py tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_counterspell.py -q
python -m pytest tests/rulesets -q
python -m ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat/engine.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/reaction_spells.py src/rulesets/dnd2024/combat/triggered_spells.py src/rulesets/dnd2024/runtime.py tests/rulesets/test_dnd2024_paladin_auras.py
git diff --check
```

若基线中某个列出的既有测试文件不存在，先核实实际文件名并记录替换，不得创建空回归文件。`test_dnd2024_character_builder.py` 的 11 个已知上游失败仅在 HEAD 对照同样失败且与本切片无关时豁免。

## 9. 交付要求

1. 一个提交/PR 只包含本切片；报告实际基线提交、变更文件、事件字段与 RNG 顺序。
2. 逐项映射测试 1–33，不以“全量通过”代替矩阵证据。
3. 保存聚焦、回归、全量、ruff、diff 检查结果；已知失败必须给 HEAD 对照。
4. 明确说明多光环选择策略、已有 Charmed 保留方式、四个 saving throw 消费面及全部专注调用点。
5. 不在执行报告把 Aura of Courage 或其他未做特性标为完成。

## 10. 执行报告模板（执行侧填写）

- 实际基线 / 分支 / 提交：
- 特性授予与 owner-scoped 身份复核：
- 光环求值器位置、遍历顺序与 tie-break：
- saving throw 接入点逐项说明：
- Charmed 拦截及已有条件保留方式：
- 改动文件对照白名单：
- 测试 1–33 逐项结果：
- 聚焦 / 回归 / 全量 / ruff / diff 输出：
- 与契约的偏差及原因：
- 未决事项（不得把非目标列为已实现）：
