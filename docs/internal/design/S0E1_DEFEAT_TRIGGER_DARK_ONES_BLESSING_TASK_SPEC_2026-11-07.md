# S0-E1 任务书：敌人归零触发与 Dark One's Blessing

| 项 | 值 |
|---|---|
| 切片编号 | S0-E1（独立新任务；不执行、不覆盖旧 S-P4） |
| 唯一验收载体 | Fiend Patron 3 级 `dark_ones_blessing` |
| 通用能力 | 伤害来源把存活敌人降至 0 HP 时，结算该来源实际拥有的被动特性 |
| 规则基准 | 官方 SRD 5.2.1，2025-05-01，CC-BY-4.0；Dark One's Blessing 在 p76 |
| 代码基线 | 祖先基线 `integration/dnd2024-srd@35653f81`；必须先执行并合入 S0-A |
| 前置任务 | `S0A_SUBCLASS_GRANT_IMPROVED_CRITICAL_TASK_SPEC_2026-11-07.md`（S0-A） |
| 建议分支 | `feat/dnd2024-s0e1-dark-ones-blessing` |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 执行纪律与范围锁定

1. 本任务书是本切片的唯一需求来源。不要读取旧 S-P4 或其他施工计划扩张范围；仓库根 `AGENTS.md`、`docs/ENGINEERING_RULES.md` 与相关架构文档自动适用。
2. 开工时记录 S0-A 合入后的实际集成提交，并从该提交新建干净工作树。不要直接在已有 `df-int` 工作树施工，不要清理无关未跟踪文件。
3. 若 S0-A 尚未合入，停止。本任务依赖真实的 `features.subclass_ref` 授予、owner-scoped 查询和战斗 actor 的子职投影；仅向 JSON 加条目不会生效。
4. 本切片只实现 **伤害来源亲自把敌人降至 0 HP → 来源获得临时 HP**。不实现旧 S-P4 所写的“他人击败、10 尺内共享”；该效果不属于 SRD 5.2.1 的 Dark One's Blessing。
5. 不实现 Fiend Patron 的法术列表、Dark One's Own Luck、Fiendish Resilience、Hurl Through Hell，也不实现敌人击晕、死亡系统或通用 trigger DSL。
6. 不使用 `.rulebooks`、open5e、2014 SRD 或第三方译文作为规则依据；第 2 节官方英文原文是本任务的规则基准。
7. 本切片可与 S0-D3 分别基于 S0-A 施工。若实际基线已含 S0-D3 且接入其命中后 continuation 需要修改本任务白名单外文件，停止并请求集成裁决，不得绕过掷后窗口提前触发。

## 2. 官方规则与本切片语义

SRD 5.2.1 p76，Fiend Patron：

> **Level 3: Dark One's Blessing**
>
> When you reduce an enemy to 0 Hit Points, you gain Temporary Hit Points equal to your Charisma modifier plus your Warlock level (minimum of 1).

锁定以下语义：

1. 授予等级是 **Warlock 3 级**，且角色必须实际选择 `subclass:fiend_patron`；不是 1 级，也不是所有 Warlock 自动拥有。
2. 只有实际造成本次归零的来源触发自己的特性。队友、同伴或另一位 Warlock 击败敌人时，本持有者不获得临时 HP，无论距离多近。
3. 没有距离、可见性、Reaction、动作、法术位或每回合次数要求。远程击杀与近战击杀使用同一规则。
4. 触发边界是目标在本次结算前 `hp > 0`，本次最终 HP 伤害累计后 `hp - damage_taken <= 0`。已经 0 HP 的敌人不能再次触发。
5. 目标必须是当前模型中的 `kind == "enemy"`。玩家或同伴降至 0 HP 不触发；敌人类型、是否死亡、是否最后一个敌人均不改变本特性的触发条件。
6. 数值为 `max(1, current_charisma_modifier + warlock_level)`。使用结算时的属性与真实 Warlock 等级；负魅力调整值也触发，只是结果保底为 1。
7. 使用抗性、豁免减半、Uncanny Dodge 与命中触发 rider 全部结算后的最终 HP 伤害。未造成 HP 伤害、只命中、只造成临时 HP 变化或只施加状态均不触发。
8. 临时 HP 不是治疗：不增加普通 HP，不受最大 HP 限制，不让 0 HP 角色恢复意识，也不清除死亡豁免或状态。
9. 既有 reducer 对重复获得临时 HP 采用 keep-max；本任务沿用这一确定性产品决策，不相加、不新增选择窗口。
10. 整个触发是确定性的，不掷骰、不消耗 RNG。

## 3. 已核实的代码事实

| 事实 | 位置 |
|---|---|
| 当前 `features_for()` 尚未合并 `features.subclass_ref`，所以基线上只加 JSON 不会授予本子职特性 | `features/resolver.py::features_for`；由 S0-A 修复 |
| Fiend Patron 表已在 3 级列出 `dark_ones_blessing`，但 canonical 特性定义尚不存在 | `content/subclass_catalogs/srd_subclasses.json`、特性 catalog |
| Fiend Patron 的目录来源已是 `srd-5.2.1:p76:fiend-patron` | 同上 |
| `_last_hostile_defeated()` 只判断“是否为最后一个存活敌人”，不能作为单目标击败触发器 | `combat/engine.py::_last_hostile_defeated` |
| 普通攻击在最终伤害落地后做专注与胜利判定；伤害可能先被 Shield / Uncanny Dodge / Divine Smite pending 持有 | `combat/resolution.py::_attack_events` |
| 直接法术有 spell attack、saving throw、automatic damage 三类伤害；`dealt` 已按目标累计同一批次的多道伤害 | `combat/resolution.py::_spell_events` |
| `defeated_targets` 当前只用于胜利判定，可在首次跨过 0 HP 时同时作为本批次去重边界 | 同上 |
| Shield / Uncanny Dodge 最终落账有两个提交点；在 pending 创建时尚不能触发击败 | `combat/reaction_spells.py::_finish_hit_reaction` |
| Divine Smite 将基础伤害与 rider 相加后做一次专注和胜利判定；触发必须使用同一 `total` | `combat/triggered_spells.py::_finish_hit_spell` |
| `resource.changed(resource="temp_hp")` 已由 reducer 消费，玩家/同伴 keep-max，敌人目标 fail closed | `combat/reducer.py::_apply_temp_hp_change` |
| 普通 HP 伤害先由临时 HP 吸收；获得临时 HP 不改普通 HP 或意识状态 | `combat/reducer.py::_apply_hp_change` |
| actor view 带当前 abilities；`_feature_character()` 提供 Resolver 所需 build / resources / equipment 切片 | `combat/view.py` |
| `class_state()` 返回单职业角色的 `(class_id, level)`；当前无职业/多职业或残缺角色返回 `None` | `features/resolver.py::class_state` |
| `ability_modifier()` 已被 `combat/resolution.py` 导入，无需新增属性公式实现 | `character/builder.py`、`combat/resolution.py` |
| S0-A 后 `features_for()`、`feature_value()` 与特性定义支持 owner-scoped 身份，战斗 actor 能看到实际 `subclass_ref` | S0-A 契约及其执行结果 |

## 4. 实现契约

### 4.1 特性声明与目录

在 canonical 特性 catalog 的 `classes.warlock.features` 增加：

```json
{
  "id": "dark_ones_blessing",
  "owner": "subclass:fiend_patron",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p76:dark-ones-blessing",
  "summary": "When you reduce an enemy to 0 Hit Points, gain Temporary Hit Points equal to your Charisma modifier plus your Warlock level (minimum of 1).",
  "parameters": {
    "trigger": "enemy_reduced_to_zero_hp",
    "temp_hp_ability": "cha",
    "temp_hp_class_ref": "class:warlock",
    "temp_hp_minimum": 1
  }
}
```

1. 声明位于 Warlock 内容段，但身份必须显式属于 `subclass:fiend_patron`；授予等级与子职表 3 级一致。
2. 不添加 `defeat_share_range_ft`、他人击败开关、资源成本、capabilities 或冷却字段。
3. `temp_hp_class_ref` 表示公式使用哪个职业的等级，不是额外的特性授予来源；本特性固定为 `class:warlock`。
4. 英文/中文 locale 同步增加标签：英文 `Dark One's Blessing`；中文 `黑暗赐福`。canonical ref、trigger 与参数键不本地化。
5. 不修改 subclass、progression、rest、spell 或 resource catalog。

### 4.2 参数校验

在既有特性声明校验边界增加本组参数的 fail-closed 校验，不改变授予算法：

1. `trigger / temp_hp_ability / temp_hp_class_ref / temp_hp_minimum` 必须四项同时声明；只声明部分字段视为非法。
2. 本切片支持的 `trigger` 只有 `enemy_reduced_to_zero_hp`。
3. `temp_hp_ability` 必须是六属性 canonical id 之一；本特性为 `cha`。
4. `temp_hp_class_ref` 必须是非空、合法的 `class:<id>`，且该 id 存在于 progression catalog；本特性为 `class:warlock`。
5. `temp_hp_minimum` 必须是非 bool 的 `int >= 1`，拒绝 `true`、`1.0`、字符串和零/负数。
6. 校验针对声明该组参数的特性，不全局禁止其他既有 scalar 参数，不改 `content_version`。

### 4.3 共享归零触发辅助

在 `CombatResolutionMixin` 增加一个内部辅助，供所有最终伤害落账点复用。建议签名：

```python
def _enemy_zero_hp_feature_events(
    self,
    instance: Any,
    combat: dict[str, Any],
    source_actor_id: str,
    target: dict[str, Any],
    damage_taken: int,
) -> list[dict[str, Any]]:
    ...
```

行为契约：

1. 任一条件成立即返回空表：目标不是 enemy；目标结算前 `hp <= 0`；`damage_taken <= 0`；或 `target.hp - damage_taken > 0`。
2. `source_actor_id` 是实际攻击者/施法者，不是受击防御者、Reaction 使用者或 GM。只解析这个来源的特性，不枚举全队寻找受益者。
3. 来源必须是 player/companion，且通过 `_actor_view()`、`_feature_character()`、`features_for()` 实际拥有声明该 trigger 的特性。enemy 不新增职业特性框架。
4. 每个符合条件的定义读取当前属性调整值与 `class_state()` 等级；只有 `class_state()` 的职业与 `temp_hp_class_ref` 一致时才使用该等级。有效子职授予却拿不到匹配职业状态属于集成错误，停止并报告，不得静默改用总角色等级。
5. `amount = max(temp_hp_minimum, ability_modifier(source.abilities[ability]) + matching_class_level)`。
6. 不在辅助函数中出现 `dark_ones_blessing`、`fiend_patron` 或 `warlock` 字面分支；这些身份只存在目录、事件审计字段和测试中。
7. 不调用 RNG，不修改 canonical 状态，不直接写 HP/temp HP；只构造事件，由既有 reducer 落账。

### 4.4 事件形状与顺序

每次有效触发追加一条：

```json
{
  "type": "resource.changed",
  "resource": "temp_hp",
  "target_id": "player:<warlock>",
  "source_actor_id": "player:<warlock>",
  "defeated_actor_id": "enemy:<target>",
  "delta": 6,
  "amount": 6,
  "rolls": [],
  "feature_id": "dark_ones_blessing",
  "owner": "subclass:fiend_patron",
  "ability": "cha",
  "ability_modifier": 3,
  "class_ref": "class:warlock",
  "class_level": 3
}
```

1. 示例仅说明 3 级、魅力调整值 +3 的结果；字段数值必须由服务器计算，测试不得把它当固定常量。
2. `target_id` 与 `source_actor_id` 在本特性中相同；保留两者是为审计伤害来源和受益者，不能据此实现队友共享。
3. `delta/amount` 表示本次授予值；既有 temp HP reducer 按 `amount` keep-max，不能把 `delta` 当成必然实际增加值。
4. `owner` 必须来自实际定义，不能由客户端输入或根据名称猜测。
5. 事件顺序为：最终 HP 伤害 → 既有专注事件 → 本特性 temp HP → 既有 victory（若该路径本来会产生 victory）。不得在伤害事件之前发放。
6. 不在 reducer 内实现触发，不给 reducer 增加特性目录依赖；重放只机械应用已生成事件。

### 4.5 最终伤害接入点

只在以下最终落账点接入；不得在等待中的 decision 创建点提前触发：

1. **普通攻击直接提交**：`_attack_events()` 无命中后 pending 的分支，使用 `actor_id`、target 和最终 `damage`。
2. **法术攻击伤害**：`_spell_events()` 的 `spell_attack` 分支，使用施法者 `actor_id` 与该目标的 `dealt[target_id]` 累计值。
3. **豁免伤害**：`saving_throw` 分支，在豁免减半和抗性之后使用同一累计值。
4. **自动伤害**：`automatic_damage` 分支，使用同一累计值。
5. **Uncanny Dodge 最终提交**：`_finish_hit_reaction()` 的 feature 分支，使用 `decision.attacker_id` 与减半后的 `amount`。
6. **Shield 未改判或 decline 后最终提交**：同一函数的末尾分支，使用 `decision.attacker_id` 与最终 damage event 的 `amount`。
7. **Divine Smite 最终提交**：`_finish_hit_spell()` 使用 `decision.actor_id` 与基础伤害加 rider 的 `total`；施放和 decline 都经此同一收尾，不重复触发。

多目标/多射线约束：

1. 只有目标首次满足归零条件时，将其加入 `defeated_targets` 并调用辅助；同一批次后续射线看到该 id 已存在时不得再次触发。
2. 同一目标多束累计才归零时，在第一束不触发，在跨过 0 HP 的那一束触发一次，传入累计 `dealt`。
3. 不同目标各自归零时各触发一次；多个 temp HP 事件仍由 reducer keep-max，而不是数值累加。
4. 既有 `dealt`、条件消费、专注、胜利和 RNG 顺序保持不变；不得为触发器重掷或预掷任何骰。
5. 普通攻击、反应和 Divine Smite 不在本任务修复或重构既有 victory 行为，只把触发放在既有胜利判定之前。

### 4.6 与既有临时 HP 的闭环

1. 复用 `resource.changed(resource="temp_hp")`，不新建事件类型或状态字段。
2. 新值低于/等于既有值时保留旧值；高于既有值时替换；不得相加。
3. 后续伤害继续先扣 temp HP，再扣普通 HP；本任务只加闭环测试，不改 reducer 算法。
4. 0 HP 持有者若通过合法路径触发，仍只获得 temp HP；不添加 HP 门槛，也不恢复意识。
5. 长休清除、战斗外持续和其他 temp HP 来源保持原行为，不在本任务修改。

## 5. 改动白名单

只允许修改以下文件；需要任何其他文件时停止并报告：

1. `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`
2. `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`
3. `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`
4. `src/rulesets/dnd2024/features/resolver.py`：仅允许新增 §4.2 参数校验，不重写 S0-A 授予逻辑
5. `src/rulesets/dnd2024/combat/resolution.py`
6. `src/rulesets/dnd2024/combat/reaction_spells.py`
7. `src/rulesets/dnd2024/combat/triggered_spells.py`
8. 新建 `tests/rulesets/test_dnd2024_dark_ones_blessing.py`
9. 本任务书：只允许追加第 10 节执行报告

明确不改 `combat/reducer.py`、`combat/engine.py`、`combat/view.py`、`features/models.py`、rest/runtime/前端及 subclass/progression/rest/spell/resource catalog。若 S0-A 的实际交付未提供所需 owner-scoped 投影，停止，不在本任务补做 S0-A。

## 6. 测试矩阵

新测试必须通过真实 `resolve_intent -> apply_batch` 链，不只调用触发辅助。角色通过真实升级链到 3 级并选择 Fiend Patron；允许在战斗 fixture 设置位置、条件、装备和 RNG，但不得伪造 temp HP 事件或跳过最终伤害路径。

### 6.1 声明、所有权与校验

1. Fiend Patron 3 级 Warlock 拥有 `(subclass:fiend_patron, dark_ones_blessing)`；2 级没有。
2. 3 级 Warlock 缺失 `subclass_ref` 或选择其他子职时没有该特性，击杀不触发。
3. 非 Warlock 即使伪造 `subclass:fiend_patron` 也不能获得该特性；owner 与 class 归属不能被短 id 绕过。
4. 英文/中文标签正确；`source_ref` 恰为 p76，内部 trigger、owner 和 class_ref 不本地化。
5. 参数缺项、未知 trigger、非法 ability/class_ref、bool/浮点/字符串 minimum、minimum < 1 均在目录加载时 fail closed。
6. 既有职业、子职、progression 与 resource catalog 校验保持通过，未添加共享半径或资源成本。

### 6.2 核心触发与来源边界

7. 3 级 Fiend Warlock 以普通武器攻击把存活敌人降至 0 HP，敌人伤害事件后获得 `max(1, 魅调 + 3)` temp HP，审计字段完整。
8. 同一攻击未把敌人降至 0 HP、miss、伤害为 0 或只施加状态时不触发。
9. 魅力调整值为负且公式结果小于 1 时仍触发，amount 为 1。
10. 真实升级后按新的 Warlock 等级和当前魅力重算，不使用建卡缓存或固定 3 级值。
11. 队友/同伴击败敌人时，Fiend Warlock 即使在 10 尺内也不获得 temp HP；只有实际来源自己的特性可被触发。
12. 自己远程击败敌人不受距离限制；不新增可见性检查、Reaction 或资源消耗。
13. 对已经 0 HP 的敌人重复伤害不再次触发；若既有 validation 拒绝该意图，则断言拒绝且无 temp HP 事件。
14. 玩家/同伴被降至 0 HP 不触发；无 Fiend 特性的来源击败敌人也不触发。

### 6.3 全伤害路径与多目标

15. spell attack 击败敌人触发，使用施法者而不是目标或防御者作为来源。
16. saving throw 法术在豁免减半与抗性后按最终伤害判定；分别覆盖“原始骰面足够但最终伤害未归零”和“最终伤害归零”。
17. automatic damage 法术击败敌人触发；最终伤害为 0 或不足以归零时不触发，且不因 automatic 模式跳过存活守卫。
18. 多射线对同一目标：前两束未归零不触发，累计跨过 0 HP 的那一束只触发一次，后续束不重复。
19. 同一次多目标施法击败两个敌人时各产生一条审计事件，但持有者 temp HP 仍按 keep-max 而非相加。
20. 借机攻击复用普通攻击最终提交路径，亲手归零时只触发一次。
21. Shield decline/未改判与 Uncanny Dodge 最终提交分别覆盖：减半后未归零不触发，减半后仍归零才触发；pending 创建时不得提前触发。
22. Divine Smite 施放时用基础伤害加 rider 的 total 判定：基础伤害不足、合并后归零时触发一次。
23. Divine Smite decline 时只用最终基础伤害判定；归零触发一次，不归零不触发，不能因持有过 rider offer 重复触发。

### 6.4 临时 HP、顺序与重放

24. 已有 temp HP 高于新授予值时保留旧值；等于时也保留，不累加。
25. 已有 temp HP 低于新授予值时替换为新值，事件仍记录本次 amount。
26. 获得 temp HP 后受到伤害，先扣 temp HP；吸收足够时普通 HP 不变，超出部分才扣普通 HP。
27. 0 HP 持有者获得 temp HP 后普通 HP 仍为 0，unconscious/stable/death_saves 不被该事件改写。
28. 在本来会胜利的路径中，事件顺序为最终伤害、既有专注事件、temp HP、victory；战斗结束不回滚已授予 temp HP。
29. live 与 reducer 重放后的 canonical temp HP/HP/敌人状态一致；重复应用同一批次不再次授予或吸收伤害。
30. 无特性直通、各类 decline 和触发路径均不新增 RNG 消耗；普通战斗、False Life、Shield、Uncanny Dodge、Divine Smite、Sneak Attack 与既有 victory 回归保持原行为。

## 7. 明确非目标

1. 旧版“他人击败且位于你 10 尺内”共享效果，以及任何距离/可见性光环。
2. Fiend Spells、Dark One's Own Luck、Fiendish Resilience、Hurl Through Hell。
3. 敌人职业特性、GM 环境伤害、探索/叙事伤害或即死效果的新触发框架。
4. 临时 HP 保留旧值/取新值的玩家决策 UI；本切片继续使用既有 keep-max。
5. 敌人击晕、死亡豁免、玩家复活或 0 HP 规则重构。
6. 通用 trigger DSL、插件注册表、前端组件或 feed 版式。
7. 既有 victory、reducer、rest、multiclass 或 S0-D3 掷后窗口改造。

## 8. 验收命令

在干净工作树中使用项目既有 Python 环境；Windows 下为 pytest 指定独立 basetemp：

```powershell
python -m pytest tests/rulesets/test_dnd2024_dark_ones_blessing.py tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_scorching_ray.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_resting.py --basetemp=.ptmp-s0e1 -q
python -m pytest tests/rulesets --basetemp=.ptmp-s0e1-all -q
ruff check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/reaction_spells.py src/rulesets/dnd2024/combat/triggered_spells.py tests/rulesets/test_dnd2024_dark_ones_blessing.py
ruff format --check src/rulesets/dnd2024/features/resolver.py src/rulesets/dnd2024/combat/resolution.py src/rulesets/dnd2024/combat/reaction_spells.py src/rulesets/dnd2024/combat/triggered_spells.py tests/rulesets/test_dnd2024_dark_ones_blessing.py
git diff --check
git status --short
```

基线已知的 `character_builder` 环境失败不得被“顺手修复”；若出现，应与同一基线复跑对照并如实记录。不得删除或放宽失败测试来制造通过。

## 9. 停止条件

出现任一情况立即停止并报告，不自行扩大范围：

1. S0-A 未合入，或实际实现没有提供选择感知的子职所有权及战斗 actor 投影。
2. 官方原文被解释为 1 级授予、队友共享或 10 尺触发；这些均与本任务锁定的 SRD 5.2.1 p76 不符。
3. 需要修改 reducer、view、engine 或白名单外 catalog 才能复用现有 temp HP 与职业投影。
4. 发现 §4.5 之外的战斗 HP 伤害最终落账点，无法在不漏触发/重复触发的情况下完成闭环。
5. 多射线累计、Shield/Uncanny Dodge 或 Divine Smite 的最终伤害与触发值不一致。
6. 需要新增 RNG、资源池、Reaction、距离/可见性判断或临时 HP 决策才能完成。
7. S0-D3 已合入且命中后 continuation 与白名单冲突，或任何路径会在伤害最终落地前触发。
8. 出现重放重复授予、敌人 temp HP 写入、客户端伪造数值或既有失败集无法归因等正确性问题。

## 10. 执行报告模板

完成后把以下内容追加到本文件；不得只口头声称完成：

```text
## S0-E1 执行报告（<日期>）
- 基线：<S0-A 合入后的完整 commit>
- 分支 / HEAD：<branch> / <commit>
- 改动文件：<逐项列出；与白名单比较>
- 官方依据：SRD 5.2.1 p76；source_ref=<实际写入值>
- 特性身份与所有权：<目录、Resolver、actor 投影结果>
- 参数校验：<合法集合与 fail-closed 结果>
- 共享触发辅助：<签名、归零守卫、来源与公式>
- 七个最终伤害接入点：<逐项列位置、伤害口径和去重方式>
- 多目标/多射线：<累计与一次性证据>
- 事件与临时 HP：<字段、顺序、keep-max、吸收、0 HP 行为>
- RNG：无特性 / 各 decline / 各触发路径实测均无新增消耗
- §6 测试矩阵：1–30 逐项填写 通过/失败/未实现及证据，不得汇总跳项
- 聚焦测试：<命令与 passed/failed 数字>
- rulesets 全量：<命令与 passed/failed 数字；失败逐项归因>
- ruff / format / diff 检查：<结果>
- 非目标确认：§7 七项均未越界
- 遗留问题：<没有则写“无”；其余 Fiend 特性仍属后续范围>
```
