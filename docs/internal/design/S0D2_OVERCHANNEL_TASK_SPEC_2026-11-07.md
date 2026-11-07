# S0-D2 任务书：Overchannel + 长休作用域递增使用计数

版本：v1（2026-11-07）

状态：待执行；本文是执行契约，不表示功能已实现。

执行侧须知：

1. 本任务书是本切片的唯一需求来源。不要读取旧 S-P2/S-P3 或其他施工计划来扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. **前置依赖**：S0-A 与 S0-D1 必须已完成并合入 `integration/dnd2024-srd`。从同时包含两者的集成分支最新提交新建干净工作树和分支 `feat/dnd2024-s0d2-overchannel`，在执行报告中记录两个前置提交和本任务基线；不要猜提交号，不要使用已有 `df-int` 工作树。
3. 本任务只实现 Evoker 14 级 Overchannel、它的可选施法声明、长休前使用计数和递增自伤。不是补齐 Wizard、Evoker、全部伤害法术或通用使用次数系统。
4. 旧版“每次使用都按 2d12/环收费”的简化已废止。第一次长休后使用必须无自伤，第二次才从 2d12/环开始，之后每次增加 1d12/环。
5. 不使用 open5e 或 2014 数据作为规则依据；下方引用的官方 SRD 5.2.1 原文即本任务规则依据。

---

## 1. 目标与完成定义

打通以下端到端链路：

```text
Wizard 实际选择 subclass:evoker
→ 14 级获得 Overchannel
→ 对合法的 Wizard 列表 1–5 环伤害法术显式声明 overchannel: true
→ 本次施法造成的所有伤害骰取最大，固定分量、重击翻倍、豁免减半和抗性顺序不变
→ 实际完成施法时记录一次长休作用域使用
→ 第一次无自伤；第二/三/……次在施法后立即掷 2/3/……d12 × 实际法术位环阶的黯蚀自伤
→ 自伤忽略抗性与免疫，但仍由临时 HP 吸收，并走既有 HP/昏迷/专注链路
→ 短休不清零，完成长休后下一次重新免费
```

复用 S0-D1 的 `maximize_dice_nodes()`，不得再写第二个 AST 最大化实现。沉淀一个最小、通用、owner-scoped 的 `features.usage` 长休计数读写约定，供后续 Holy Ward 等同类机制复用。

完成不等于“能提交一个开关”。必须同时验证真实子职授予、法术列表与环阶门槛、三种现有伤害模式、首次/递增自伤、RNG 顺序、事件 apply/reducer 重放、反制法术和长短休。

## 2. 官方规则事实（SRD 5.2.1）

官方基准：`SRD_CC_v5.2.1.pdf`，SHA-256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`。Evoker，印刷页 82：

> **Level 14: Overchannel**
>
> You can increase the power of your spells. When you cast a Wizard spell with a spell slot of levels 1–5 that deals damage, you can deal maximum damage with that spell on the turn you cast it.
>
> The first time you do so, you suffer no adverse effect. If you use this feature again before you finish a Long Rest, you take 2d12 Necrotic damage for each level of the spell slot immediately after you cast it. This damage ignores Resistance and Immunity.
>
> Each time you use this feature again before finishing a Long Rest, the Necrotic damage per spell level increases by 1d12.

必须锁定的规则边界：

1. 只有实际选择 `subclass:evoker` 的 14 级 Wizard 获得该特性；13 级、未选子职、其他子职或通过其他 owner 伪造均不获得。
2. **Wizard spell 由官方法术目录的 `class_refs` 是否包含 `class:wizard` 判定，不由 `school == evocation` 判定。** 不得把本特性误写成“只最大化塑能法术”。
3. 只接受实际使用 1–5 环法术位的施法。戏法、免费施法的 `slot_level == 0`、6 环及以上法术位均不可用；五环法术使用六环槽也不可用。
4. 目标法术必须具有伤害效果。纯治疗、增益、传送、解除、临时 HP 等不因为属于 Wizard 列表就可使用。
5. 是否使用是施法时声明的可选选择，不是拥有特性后自动最大化。省略或 `false` 完全保持旧行为。
6. “deal maximum damage”只把伤害骰取最大，固定伤害分量保留。重击先翻倍骰数再逐骰最大化；豁免成功后对最大化伤害执行既有减半；目标抗性在最后按既有顺序结算。
7. 同一次施法的所有目标、所有射线和本回合内该法术伤害均最大化；不能只对第一个目标生效。
8. 只要实际完成一次已声明的合法施法，就记录使用并在需要时结算自伤。攻击未命中、豁免导致零伤害不撤销这次使用。
9. 被 Counterspell 成功反制的法术没有完成施法，不记录 Overchannel 使用、不最大化、不结算自伤；反制失败或无人反制时沿原 intent 正常生效。
10. 第 N 次使用（从 1 开始）：`N == 1` 无自伤；`N >= 2` 掷 `N × 实际 slot_level` 个 d12。自伤在施法效果之后立即结算，不是下回合开始，也不消耗动作或反应。
11. 自伤是 Necrotic damage，忽略 Resistance 和 Immunity。临时 HP 不是抗性或免疫，仍按既有规则先吸收伤害；HP 降至 0 与专注检定仍走既有公共管线。
12. 只有完成 Long Rest 才清零。Short Rest、战斗结束、回合结束、升级、切换语言均不清零。

## 3. 基线现状（已核实）

| 事实 | 位置 |
|---|---|
| 子职表在 Wizard 14 级授予 `overchannel`，子职 id 为 `evoker` | `content/subclass_catalogs/srd_subclasses.json` |
| S0-A 后子职特性经 `features.subclass_ref` 和 owner-scoped key 授予；`has_feature()` 支持可选 `owner` | `features/models.py`、`features/resolver.py` |
| S0-D1 提供 `maximize_dice_nodes()`，可把 AST 骰节点变为最大值常量并保留固定分量 | `combat/action_adapter.py` |
| 法术目录的每行同时具有 `level`、`school`、`class_refs`；列表归属与学派是独立字段 | `spells/catalog.py`、`content/spell_catalogs/srd_spells.json` |
| 当前可结算的 Wizard 1–5 环伤害法术是 `burning_hands`、`chromatic_orb`、`magic_missile`、`scorching_ray`、`thunderwave`；恰好都属于 evocation，但这不是规则门槛 | 上述两个 catalog |
| 法术伤害分布在 `spell_attack`、`saving_throw`、`automatic_damage` 三个分支 | `combat/resolution.py::_spell_events` |
| 重击在伤害求值前调用 `double_dice_counts()`；豁免减半后调用 `_resist_damage()` | 同上 |
| `_spell_events()` 在同次施法内逐目标累计伤害，最后判断胜利；Counterspell 成功后不会进入 `_spell_events()`，失败后会携带原 cast intent 进入 | `combat/resolution.py`、`combat/reaction_spells.py` |
| `resource.changed` 已支持 HP、骰迹、伤害类型；reducer 自己处理临时 HP、0 HP 和状态，不会在落账时再次掷骰 | `combat/reducer.py` |
| rest 引擎目前只恢复职业资源与法术位，没有通用长休特性使用计数 | `resting/engine.py`、`features/resources.py` |
| S0-A 的战斗特性代理只传递 `subclass_ref`；本任务需再传递使用计数，不复制整份角色历史 | `combat/view.py` |
| 公共 feed 有独立字段白名单，本任务不改变该投影 | `runtime.py::_recent_combat_events` |

行号不是契约。若前置任务合入后的所有权 API、最大化 helper、法术伤害入口或 rest 结构与上述边界不同，按 §7 停止，不要建立第二套规则路径。

## 4. 精确实现契约

### 4.1 内容声明

在 `classes` 中新建 `wizard`，class source_ref 使用 `srd-5.2.1:p77:wizard-features`，追加：

```json
{
  "id": "overchannel",
  "owner": "subclass:evoker",
  "minimum_level": 14,
  "source_ref": "srd-5.2.1:p82:overchannel",
  "summary": "When you cast a Wizard spell with a level 1–5 spell slot that deals damage, you can maximize its damage. Later uses before a Long Rest deal escalating Necrotic damage to you.",
  "parameters": {
    "maximum_slot_level": 5,
    "usage_scope": "long_rest",
    "self_damage_die": 12,
    "initial_self_damage_dice": 2
  }
}
```

标签：英文 `Overchannel`；中文 `超限引导`。同时补齐 Wizard / 法师的目录 label。canonical 与中英文 locale 同步更新；不得只改其中一份。

该特性不是独立动作，不新增 capability、资源池或 `resources.class` 条目。`parameters` 是规则参数，不允许客户端提交它们来改变自己的自伤公式。

### 4.2 通用长休使用计数

在 `features/resources.py` 增加最小的 owner-scoped 计数读写辅助，不修改现有职业资源声明或 `resource_state()` 语义。

canonical 角色使用以下结构；未使用过的角色可以完全省略 `usage`：

```json
{
  "features": {
    "subclass_ref": "subclass:evoker",
    "usage": [
      {
        "owner": "subclass:evoker",
        "feature_id": "overchannel",
        "scope": "long_rest",
        "count": 2
      }
    ]
  }
}
```

行为契约：

1. 身份是 `(owner, feature_id, scope)`，不得只用全局短 `feature_id`。本切片只接受 `scope == "long_rest"`。
2. 缺失记录表示 `count == 0`。`count` 必须是非 bool 的非负整数；重复身份、错误类型、负数、未知 scope 均 fail closed，不得静默归零。
3. 提供只读 `feature_usage_count(character, owner, feature_id, scope)` 供 validation/resolution 使用；不要通过扫描升级历史推导使用次数。
4. reducer 的应用辅助按事件中的 `before_count` 校验当前计数，再写入 `after_count == before_count + 1`；不匹配抛既有 `EventBatchError`，避免乱序批次重复消费。
5. 不在建卡时为所有特性预建零值记录，不增加 manifest/content_version，不做存量迁移。旧存档缺失字段自然表示零。
6. `complete_short_rest()` 对 `features.usage` 完全不变。`complete_long_rest()` 对所有合法 `scope == "long_rest"` 记录清零；可删除零值记录以保持存档精简，但事件与后续读取必须等价于零。
7. 长休每实际清零一条记录，追加稳定事件：`type: "reset_feature_usage"`、`owner`、`feature_id`、`scope`、`before`、`after: 0`。没有非零记录时不制造事件。短休不得发出该事件。

### 4.3 战斗 actor 投影

在 `combat/view.py` 基于 S0-A 最小扩展：

1. `_player_view()` 从 canonical `character.features.usage` 提取内部 `feature_usage` 列表，每项只含 `owner`、`feature_id`、`scope`、`count`；缺失时使用空列表。
2. `_feature_character()` 返回的 `features` 同时包含 S0-A 的 `subclass_ref` 和本切片的 `usage` 深拷贝。
3. 不把客户端 intent 中的计数、法术参数或自伤公式带入 canonical，也不把整份 grant/advancement 历史复制进 actor。
4. companion 与 player 复用同一投影；enemy 不具备该特性，不新增 enemy 使用入口。

### 4.4 施法声明与校验

`cast_spell` intent 新增可选布尔字段：

```json
{"overchannel": true}
```

在 `combat/validation.py::_validate_spell` 中：

1. 省略与 `false` 完全等价，不要求拥有特性；非 bool 值抛 `CombatIntentError`。
2. 为 `true` 时，用 S0-A API 显式检查 `(subclass:evoker, overchannel)`；无所有权直接拒绝。
3. 为 `true` 时，依次要求：`"class:wizard" in spell["class_refs"]`、实际 `slot_level` 为 1–5、当前 effect 声明了伤害。不得检查或硬编码 `school`。
4. 使用当前真实计数辅助读取状态；坏状态 fail closed。不得让客户端提交 `count`、`use_index` 或自伤骰数。
5. 保留既有法术准备、法术位、目标、动作经济、伤害类型、反制和 pending decision 校验。Overchannel 不额外消耗动作、 Bonus Action、Reaction、法术位或职业资源。
6. 当前没有走 `triggered_damage` 的合法 Wizard 1–5 环列表法术，本任务不修改 `triggered_spells.py`。若前置基线已新增这种入口，按 §7 停止，不得漏掉或擅自复制实现。

### 4.5 伤害最大化与使用落账

在 `combat/resolution.py::_spell_events` 中集中判定一次，不在三个分支分别重新计算所有权或计数：

1. 只有 `intent.get("overchannel") is True` 且校验已通过时启用。对应的 `dnd2024.spell.cast` 事件增加 `"overchannel": True`；未使用时保持旧事件形状。
2. 在 `spell_attack`、`saving_throw`、`automatic_damage` 三个分支中，构造完整基础/升环 AST 后调用 S0-D1 的 `maximize_dice_nodes()`：
   - spell attack：先按既有规则对重击 AST 调 `double_dice_counts()`，再最大化，再求值；
   - saving throw：先最大化并求值，再按既有规则处理成功减半和抗性；
   - automatic damage：构造 AST 后直接最大化，再求值。
3. 本次施法每个目标、每道射线都使用同一启用状态。伤害 `resource.changed` 仅在实际启用时增加 `"overchannel": True`；骰迹因骰节点常量化为 `[]`，攻击/豁免 D20、Bless 和专注检定骰迹不受影响。
4. 在全部法术效果事件之后、最终 victory 事件之前，追加一次稳定事件：

```json
{
  "type": "dnd2024.feature_usage.changed",
  "actor_id": "player:<uid>",
  "owner": "subclass:evoker",
  "feature_id": "overchannel",
  "scope": "long_rest",
  "spell_ref": "spell:<id>",
  "slot_level": 1,
  "before_count": 0,
  "after_count": 1,
  "use_index": 1
}
```

5. `before_count` 来自施法前 canonical 状态，`use_index == before_count + 1`。一次施法无论多少目标/射线只追加一次；未命中、全部豁免成功或伤害被减为 0 也照常追加。
6. `use_index == 1` 时不产生自伤 `resource.changed`，也不消耗自伤 RNG。`use_index >= 2` 时，以既有公式 AST/roller 掷 `use_index * slot_level` 个 d12，向施法者本人追加：
   - `type: "resource.changed"`、`resource: "hp"`、`target_id == actor_id`；
   - `delta == -amount`、`amount` 为骰总和、`damage_type: "necrotic"`、`rolls` 为实际骰迹；
   - `overchannel: True`、`ignore_resistance: True`、`ignore_immunity: True`。
7. 自伤**不得调用 `_resist_damage()`**，由 reducer 按原始伤害落账，因此抗性与免疫不会减少它。不得在本任务发明通用免疫系统；临时 HP 仍由既有 reducer 吸收。
8. 自伤后调用既有 `_concentration_events(actor, amount, rng)`，保持伤害导致专注检定的公共行为。自伤造成的 0 HP、昏迷、死亡豁免等继续由 reducer 处理。
9. 只有 `_spell_events()` 真正执行才产生上述事件。Counterspell pending/success 阶段不得提前计数；反制失败后的原 intent 必须保留 `overchannel` 标志。
10. 在 `combat/reducer.py` 注册 `dnd2024.feature_usage.changed`，调用 §4.2 应用辅助；`resource.changed` 和其余 reducer 顺序不改。事件重放不得再次掷骰、再次最大化或再次计算自伤。
11. 不修改公共 feed 白名单。`overchannel` 等审计字段可被现有投影过滤，黯蚀自伤的既有公开字段仍正常显示。

### 4.6 改动白名单

只允许改动：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resources.py`
5. `src/rulesets/dnd2024/combat/view.py`
6. `src/rulesets/dnd2024/combat/validation.py`
7. `src/rulesets/dnd2024/combat/resolution.py`
8. `src/rulesets/dnd2024/combat/reducer.py`
9. `src/rulesets/dnd2024/resting/engine.py`
10. 新建 `tests/rulesets/test_dnd2024_overchannel.py`
11. `tests/rulesets/test_dnd2024_resting.py`：仅允许追加通用长休使用计数与既有 rest 排除面断言
12. 本任务书：只允许追加 §10 执行报告

不需要也不允许修改 `features/models.py`、`features/resolver.py`、`combat/action_adapter.py`、`combat/reaction_spells.py`、`combat/triggered_spells.py`、spell/combat/rest catalog、`runtime.py`、通用公式引擎、前端、manifest 或其他职业内容。若确需白名单外改动，停止并报告，不得先改后解释。

## 5. 测试与夹具约束

1. 新测试通过真实 1 级建卡和 advancement 逐级升级构造 13/14 级 Wizard，并在 3 级真实选择 `subclass:evoker`；不得直接建 14 级角色，不得只向 `class_feature_grants` 塞 id。
2. 法术书、准备法术、法术位、专长/ASI 与子职选择必须经真实数据和公开入口得到。可复用 `test_dnd2024_scorching_ray.py` 的 fixture 模式，但不得修改该文件或把 Wizard 专属静态列表改成不足高等级的伪数据。
3. 战斗测试通过真实 encounter/actor 投影、`validate_intent`、`cast_spell`、事件 apply/reducer；不得 monkeypatch `has_feature()`、最大化 helper、计数或伤害结果。
4. 为连续自伤测试，可在真实升级后的测试角色上把 `resources.hp` 与 `max_hp` 同时提高到足够存活的测试值；不得借此伪造特性、法术位或所有权。需要扣血时仍走真实事件。
5. 使用局部 `SequenceRng` 明确区分 D20、Bless、伤害、自伤和专注骰顺序。第一次无自伤时必须用哨兵证明没有额外自伤掷骰；最大化后的伤害骰本身零 RNG，但合法攻击/豁免仍按原规则消费 D20。
6. 直接 helper 单测与真实战斗端到端都要写。只测 AST、只测事件生成而不 apply、或只测最终 HP 而不断言计数/RNG，都不算完成。
7. 为证明没有误加学派门槛，可对资格判定 helper 使用一个 `class_refs` 含 Wizard、`school` 非 evocation 的合法合成元数据用例；不得篡改官方 SRD 法术的学派来换绿。

## 6. 编号测试矩阵

### 6.1 内容、所有权与资格

1. canonical/en/zh-CN 三份 catalog 均可加载；Overchannel 的 owner、14 级门槛、source_ref、参数与中英文标签正确。
2. 13 级 Evoker Wizard 无该特性；经真实升级至 14 级后，`has_feature(..., owner="subclass:evoker")` 为 True，feature view 带正确 owner。
3. 未选择子职的 14 级 Wizard、其他子职代理、非 Wizard 伪造 `subclass:evoker` 均不获得该特性。
4. 真实 14 级 Evoker 对 Wizard 列表 1 环伤害法术可声明；省略/false 无需特性且行为与基线一致；非 bool 标志被拒绝。
5. 无特性角色、非 Wizard 列表法术、无伤害法术、戏法、免费施法 slot 0、6 环槽及以上均不能声明；五环法术用六环槽同样拒绝。
6. 合成非 evocation 的 Wizard 列表伤害法术资格为真；仅属于 evocation 但不在 Wizard 列表的资格为假，证明列表与学派没有混用。

### 6.2 三种伤害模式与骰序

7. 首次 Overchannel `magic_missile` 的 `3d4+3` 造成 15 点；固定 +3 保留，伤害 `rolls == []`、事件带 overchannel，使用计数从 0 到 1且无自伤。
8. 二环槽 `magic_missile` 的既有升环公式全部最大化，数值等于原 AST 每枚 d4 取 4、固定分量不变；实际自伤环阶以后续测试的 slot 2 为准。
9. `burning_hands` 基础 3d6 最大化为 18，升环骰也最大化；目标豁免失败受全值，成功按既有 `half_on_success` 减半后再结算抗性。
10. `thunderwave` 与多目标法术的每个目标都最大化；一次施法只记录一次使用，不按目标数量收费。
11. `chromatic_orb` 法术攻击仍正常掷 D20；命中后 3d8 最大化为 24，未命中也记录使用并在第二次以后结算自伤。
12. `scorching_ray` 每道命中射线 2d6 为 12；重击射线先翻倍再最大化为 24；同一施法多射线只记录一次使用。
13. 未声明 Overchannel 的相同法术仍按基线消费伤害 RNG、返回真实骰迹、不带 overchannel 键且不增加计数。
14. 最大化和第一次免费不额外消费伤害/自伤 RNG；第二次及以后自伤严格在法术事件之后按 d12 个数消费，后续专注等随机事件顺序可预测。

### 6.3 递增自伤、状态与 reducer

15. 长休周期内连续使用一环槽：第一次无自伤，第二次掷 2d12，第三次掷 3d12，第四次掷 4d12；每批事件的 `before_count/after_count/use_index` 连续且准确。
16. 第二次使用二环槽掷 4d12，第三次使用五环槽掷 15d12；公式由实际槽位和 use_index 决定，不读取法术基础环阶或客户端数值。
17. 自伤目标是施法者本人，类型为 necrotic，骰迹长度等于应收 d12 数；伤害在法术效果之后、victory 之前，且一次施法只有一条自伤事件。
18. 施法者具有 necrotic Resistance/Immunity 测试元数据时自伤不减免，事件不标 resisted；临时 HP 仍按既有 reducer 优先吸收，剩余部分才扣 HP。
19. 自伤导致 HP 为 0 时既有 unconscious/死亡状态正常落账；施法者专注时自伤调用公共专注检定管线，不因“自己造成”而跳过。
20. 攻击未命中、豁免使伤害为 0、目标抗性减免均不撤销使用计数和自伤；非法 intent 在验证失败时不产生任何事件或计数。
21. 每批事件 apply 与 reducer 重放得到相同 HP、临时 HP、法术位、计数和条件；重复 apply 同 batch 不重复扣血或增加计数，重放不调用 RNG。
22. 错误 before_count、重复身份、负数/bool/错误类型 count、未知 scope 均 fail closed；不得覆盖合法计数或把坏存档当作首次免费。

### 6.4 反制、休息与排除面

23. Counterspell 成功时不产生 overchannel 伤害、使用事件或自伤，计数不变；反制失败/无人反制时原 intent 正常最大化并计数。
24. 短休后计数与下一次自伤档位不变；完成长休后长休作用域记录清零并各发一条 reset 事件，下一次使用重新免费且无自伤 RNG。
25. 战斗结束、回合结束、升级和中英文 bundle 切换不清零；长休不影响其他 scope 的合法记录，也不改变既有职业资源/法术位恢复语义。
26. 治疗、临时 HP、传送、buff/dispel、武器伤害、Second Wind 等路径不读取该开关，不最大化、不增加 Overchannel 计数。
27. 公共 feed 字段白名单不变；新使用事件不意外进入公共 meaningful feed，黯蚀自伤仍以既有 `resource.changed` 公共字段展示。
28. S0-A、S0-D1 与既有职业特性、升级、战斗、Scorching Ray、反制法术、临时 HP 和 rest 回归保持通过；不得修改既有断言来接受行为漂移。

## 7. 停止条件

出现以下任一情况，停止实现并报告，不得自行扩大范围：

1. S0-A 或 S0-D1 未合入，owner-scoped 子职所有权或 `maximize_dice_nodes()` 尚不可用。
2. 为构造 14 级 Evoker Wizard 必须绕过一级建卡、伪造升级历史或修改无关升级/法术选择系统；先报告真实测试基建缺口。
3. 三个伤害分支之外已存在当前合法的 Wizard 1–5 环伤害入口，导致本任务白名单无法覆盖真实行为。
4. 为保留 Counterspell 后的原始选择必须修改 reaction 管线，或反制成功路径已经提前调用 `_spell_events()`。
5. 为自伤忽略免疫必须实现新的通用免疫系统，或现有 HP reducer 无法在不改公共语义的情况下保持临时 HP/0 HP 行为。
6. 为通过测试必须修改通用公式引擎、前端、manifest、法术/战斗/rest catalog、其他职业内容或做存量迁移。
7. 既有失败集发生变化但无法证明与基线环境有关；不要顺手修 `character_builder` 的既有环境失败。

## 8. 非目标

1. 不实现 Supreme Healing 之外的另一套最大化 helper，也不修改 S0-D1 的治疗行为。
2. 不实现 Cutting Words、Dark One's Own Luck、Lucky、Bardic Inspiration 或任何 D20/伤害掷后决策窗口。
3. 不实现 Savage Attacker、Great Weapon Fighting、Healer 等重掷机制。
4. 不实现 Evocation Savant、Potent Cantrip、Sculpt Spells、Empowered Evocation 或其他 Wizard/Evoker 特性。
5. 不把 Overchannel 限定为 Evocation school，不新增非 Wizard 列表来源、免费施法或六环以上支持。
6. 不实现通用抗性/免疫重构，不让自伤绕过临时 HP，也不另建死亡或专注管线。
7. 不做前端 UI、可用性列表新字段、公共 feed 扩张、存量迁移、content_version 变更或新存档版本。
8. 不把所有职业特性改造成通用计数系统；本任务只提供后续可复用的最小长休计数约定。

## 9. 验收命令

使用前置任务相同的既有解释器，不新建 venv。Git Bash 下执行：

```bash
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_overchannel.py tests/rulesets/test_dnd2024_resting.py -q --basetemp=.ptmp-s0d2-focused
/e/Github/diceframe/.venv/Scripts/python.exe -m pytest tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_die_manipulation.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_spells.py tests/rulesets/test_dnd2024_scorching_ray.py tests/rulesets/test_dnd2024_counterspell.py tests/rulesets/test_dnd2024_false_life.py -q --basetemp=.ptmp-s0d2-regression
/c/Python314/Scripts/ruff check src/rulesets/dnd2024/features/resources.py src/rulesets/dnd2024/combat/view.py src/rulesets/dnd2024/combat/validation.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/reducer.py src/rulesets/dnd2024/resting/engine.py tests/rulesets/test_dnd2024_overchannel.py tests/rulesets/test_dnd2024_resting.py
/c/Python314/Scripts/ruff format --check src/rulesets/dnd2024/features/resources.py src/rulesets/dnd2024/combat/view.py src/rulesets/dnd2024/combat/validation.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/reducer.py src/rulesets/dnd2024/resting/engine.py tests/rulesets/test_dnd2024_overchannel.py tests/rulesets/test_dnd2024_resting.py
```

不得用删除、跳过或放松既有断言来换绿。若临时目录已存在，换一个新的任务专属 basetemp，不批量删除其他任务目录。

## 10. 执行报告模板（执行侧完成后追加）

```markdown
## 执行报告（YYYY-MM-DD）

- S0-A / S0-D1 前置提交、本任务基线 / 分支 / 最终提交：
- 实际变更文件：
- 设计偏差及理由（没有则写“无”）：
- §6 测试矩阵：逐项列出 1–28 的通过情况，不得只写“测试通过”：
- 聚焦测试：命令、passed/failed/error 数：
- 回归测试：命令、passed/failed/error 数：
- ruff：结果：
- 既有失败与基线对比：
- 三种伤害模式、重击/豁免/抗性与固定分量核对：
- 首次免费、递增自伤、实际槽位与 RNG 顺序核对：
- Counterspell、事件 apply/reducer 重放与长短休核对：
- 排除面、存档与公共 API/feed 兼容说明：
- 未解决问题（没有则写“无”）：
- 最终 git status 与 changed-file 白名单核对：
```
