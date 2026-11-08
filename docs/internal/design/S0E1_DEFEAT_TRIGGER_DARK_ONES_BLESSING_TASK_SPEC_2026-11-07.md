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

## 执行报告（2026-11-08）

- 实际基线 / 分支 / 提交：S0-A 链头 `73d11263`；实际施工点 `feat/dnd2024-s0e2-paladin-auras@4db71cbd`（E2 尚未合入，本切片 stack 在其上，两者共用同一 S0-A 基线）；分支 `feat/dnd2024-s0e1-dark-ones-blessing`，提交 `e291791c`（切片本体）+ `8f3182fd`（RNG 断言从"两次相同"收紧为"各两枚"）+ `d0db4810`（trigger 常量由加载器与结算共享）。上游未推送（S0 系列只留本地）。

- 改动文件对照白名单（§5 九项）：
  1. `content/class_feature_catalogs/srd_class_features.json` — 改 1 条声明（载荷四键、`source_ref` 改 p76、摘要改回官方措辞）。
  2. `locales/en/...`、3. `locales/zh-CN/...` — **未改**：两个标签（`Dark One's Blessing` / `黑暗赐福`）本来就在，且本切片没有新增 id。
  4. `features/resolver.py` — 只替换 `_validate_defeat_trigger_parameters` 与其三个模块级常量（四键名、trigger 名、退役键），未动授予逻辑。
  5. `combat/resolution.py` — 触发辅助重写 + 三个法术伤害分支改去重口径。
  6. `combat/reaction_spells.py`、7. `combat/triggered_spells.py` — 仅调用点改名（辅助签名少了 `prior_damage`）。
  8. `tests/rulesets/test_dnd2024_dark_ones_blessing.py` — 重写，48 条。
  9. 本任务书 — 只追加本节。
  - 白名单外：无。`reducer.py` / `engine.py` / `view.py` / `features/models.py` / runtime / 前端 / 其余目录均未触碰。

- 官方依据：SRD 5.2.1 p76（Fiend Patron，Level 3 Dark One's Blessing）。`source_ref` 实际写入 `srd-5.2.1:p76:dark-ones-blessing`（原为 `p58`，属错的页号）。

- 特性身份与所有权：
  - 声明仍在 `warlock` 内容段下，但 `owner = subclass:fiend_patron`、`minimum_level = 3`；`features_by_key[(subclass:fiend_patron, dark_ones_blessing)]` 唯一，短 id 无法跨 owner 冒领。
  - 三条边界实测：3 级术士拥有、2 级没有；把 `features.subclass_ref` 清空或改成 `subclass:great_old_one` 后失去该特性且击杀不触发；盗贼伪造 `subclass:fiend_patron` 仍不拥有（`_selected_subclass_ref` 只认本职业自己的子职 ref，盗贼的 ref 与术士子职表不等）。
  - locale 未改，标签随声明 id 命中。

- 参数校验（fail-closed，全部在目录加载期）：
  - 合法集合就是四键：`trigger` = `enemy_reduced_to_zero_hp`、`temp_hp_ability` ∈ 六属性、`temp_hp_class_ref` = 存在的 `class:<id>`（查 progression 表）、`temp_hp_minimum` 为非 bool 的 `int >= 1`。
  - 缺任一键 → 拒绝（"凑不到公式"的特性会静默算错，故不接受部分声明）。
  - 未知 trigger、非法 ability、无 `class:` 前缀、指向不存在的职业表、`minimum` 为 `0` / `True` / `1.0` → 全部拒绝，共 13 组参数化证据。
  - **已退役键**（`defeat_temp_hp_ability` / `defeat_temp_hp_add_class_level` / `defeat_share_range_ft`）被显式拒绝，而不是当未知字段忽略——忽略等于让"10 尺共享"以死字段形式回到目录里。
  - `trigger` 只在取值属于本载荷、或已声明 `temp_hp_*` 时被认领；别的特性声明自己的 `trigger` 名不受影响（有专门用例锁定，避免与后续切片的 trigger 语义互相绑死）。

- 共享触发辅助：
  - `combat/resolution.py::_enemy_zero_hp_feature_events(instance, combat, source_actor_id, target, damage_taken)`。
  - 五个前置守卫：目标必须 `kind == "enemy"`；本次结算伤害 `> 0`；目标结算前 `hp > 0`；`hp - damage_taken <= 0`；来源必须是 `player` / `companion`。
  - **只解析来源自己的特性**：不再遍历 `combat["initiative"]`，因此没有"旁观者在 10 尺内也拿"这条路径，也没有任何距离/可见性判定。
  - 公式 `amount = max(temp_hp_minimum, ability_modifier(当前 abilities[ability]) + class_level)`；等级来自 `class_state()`，且必须与 `temp_hp_class_ref` 的职业一致，否则抛 `CombatIntentError`（不静默改用总角色等级）。
  - 不掷骰、不改 canonical、不直接写 HP/temp HP；只构造事件，由既有 reducer 落账。辅助内没有 `dark_ones_blessing` / `fiend_patron` / `warlock` 字面分支。

- 七个最终伤害接入点（全部保留，伤害口径与去重方式）：
  1. 普通攻击直接提交 `resolution.py::_resolve_committed_hit` — 最终 `damage`（已含抗性/重击/骑乘）。
  2. 法术攻击 `_spell_events` 的 `spell_attack` — 该目标的 `dealt[target_id]` 累计值。
  3. 豁免伤害 `saving_throw` — 豁免减半与抗性之后的同一累计值。
  4. 自动伤害 `automatic_damage` — 同一累计值。
  5. Uncanny Dodge 最终提交 `reaction_spells.py::_finish_hit_reaction` — `decision["attacker_id"]` 与减半后的 `amount`。
  6. Shield 未改判/放弃后的最终提交（同函数末尾）— `decision["attacker_id"]` 与最终 `damage_event["amount"]`。
  7. Divine Smite 收尾 `triggered_spells.py::_finish_hit_spell` — `decision["actor_id"]` 与基础伤害 + rider 的 `total`。
  - pending 创建点一律不调用辅助；`dnd2024.decision.pending` 事件之后才可能出现在同一批次的伤害事件里。

- 多目标 / 多射线：
  - 去重边界改为批次自己的 `defeated_targets`：只有当 `hp - dealt <= 0` 且该 id 尚未在集合里时才把 id 加入并调用辅助。集合本来就服务于胜利判定，语义不变。
  - 同一目标多束：第 1 束未归零不触发，跨过 0 HP 的那一束触发一次，后续束照旧产生伤害事件但不二次授予（2 束 / 3 束参数化）。
  - 两个目标同一批次各自归零：两条审计事件，temp HP 仍由 reducer keep-max，不累加。

- 事件与临时 HP：
  - `resource.changed(resource="temp_hp")`，字段：`target_id`、`source_actor_id`、`defeated_actor_id`、`delta`、`amount`、`rolls: []`、`feature_id`、`owner`、`ability`、`ability_modifier`、`class_ref`、`class_level`。
  - 顺序：最终 HP 伤害 → 既有专注事件 → 本特性 temp HP → 既有 victory。
  - reducer 侧未改：`max(旧值, amount)` 即 keep-max（低值/等值保留，高值替换）；伤害先扣 temp HP 再扣 HP；temp HP 不写 `unconscious` / `stable` / `death_saves`，也不恢复意识。

- RNG：`CountingRng` 对同一场景（1 HP 哥布林 + 匕首）分别在"有该特性"（3 级 Fiend 术士）与"无该特性"（盗贼）下计数，两次抽取数相同；各 decline / 未触发路径均不额外取骰。

- §6 测试矩阵 1–30 逐项（文件 `tests/rulesets/test_dnd2024_dark_ones_blessing.py`，48 条）：

| # | 结果 | 证据 |
|---|---|---|
| 1 | 通过 | `test_feature_is_owned_from_level_three`（3 级有、2 级无） |
| 2 | 通过 | `test_without_the_subclass_choice_the_kill_grants_nothing`（清空 / 改子职各跑一次真实击杀） |
| 3 | 通过 | `test_another_class_cannot_forge_the_subclass_ref`（盗贼伪造 owner，既有 Resolver 断言也有真实击杀） |
| 4 | 通过 | `test_labels_and_source_ref_match_the_srd`（en / zh-CN 标签 + p76 `source_ref`；trigger / owner / class_ref 保持不本地化） |
| 5 | 通过 | `test_the_trigger_payload_is_one_closed_four_key_set`（13 组：缺键 4、未知 trigger、非法 ability、无前缀、不存在的职业表、minimum 0/True/1.0、两种退役键） |
| 6 | 通过 | `test_the_declared_payload_is_exactly_the_four_keys` + `test_a_foreign_trigger_key_is_not_claimed_by_this_payload`；既有 catalog 校验由全套测试回归 |
| 7 | 通过 | `test_holder_kill_grants_temp_hp_with_complete_audit_fields`（12 个审计字段逐个断言，amount = 魅调 +2 + 3 级 = 5） |
| 8 | 通过 | `test_a_non_lethal_hit_grants_nothing` / `test_a_miss_grants_nothing` / `test_a_condition_only_spell_grants_nothing`（Color Spray 只施加 Blinded，0 HP 伤害） |
| 9 | 通过 | `test_negative_charisma_still_grants_one`（CHA 6 → -2 + 3 → 保底 1） |
| 10 | 通过 | `test_the_amount_follows_the_current_level_and_charisma`（真实升级到 4 级，`class_level=4`，amount 用结算时属性计算） |
| 11 | 通过 | `test_a_teammates_kill_grants_nothing_at_any_distance`（队友击杀，5 尺与 60 尺各一次，均无事件） |
| 12 | 通过 | `test_a_ranged_kill_needs_no_distance`（60 尺灼热射线击杀照常授予） |
| 13 | 通过 | `test_defeated_enemy_cannot_be_farmed`（攻击已归零目标被 validation 拒绝，无第二次授予） |
| 14 | 通过 | `test_a_player_brought_to_zero_grants_nothing`（敌方把队友打到 0，无事件）+ `test_ordinary_combat_emits_no_temp_hp`（无特性来源击杀） |
| 15 | 通过 | `test_two_spell_beams_each_kill_once_and_keep_max`（scorching ray，施法者为来源） |
| 16 | 通过 | `test_saving_throw_kill_grants_temp_hp`（豁免成功减半后仍归零）+ `test_a_halved_save_that_stops_short_of_zero_grants_nothing`（原始骰面足够、结算后不足） |
| 17 | 通过 | `test_automatic_damage_kill_grants_temp_hp` + `test_automatic_damage_that_does_not_zero_grants_nothing`（magic missile，40 HP 目标） |
| 18 | 通过 | `test_repeated_beams_on_one_defeated_target_grant_once`（2/3 束）+ `test_a_later_beam_that_first_crosses_the_threshold_grants_once` |
| 19 | 通过 | 同 15（两目标两事件，temp HP 仍为 5 而非 10） |
| 20 | 通过 | `test_an_opportunity_attack_kill_grants_once`（敌方移动触发借机攻击，窗口打开时无事件，结算后恰好 1 条） |
| 21 | **未实现（不可达）** | 见「遗留问题」第 1 条：Shield / Uncanny Dodge 的最终提交点要求"防御者是敌人"才能触发本特性。接线在 `reaction_spells.py:301/354/382`；"pending 创建时不得提前触发"由 20 的窗口断言与 7 的前置守卫覆盖。 |
| 22 | **未实现（不可达）** | 同理：Divine Smite 需要 Paladin，单职业 Fiend 术士不可能同时是 Paladin。接线在 `triggered_spells.py:164`，取的是基础伤害 + rider 的 `total`。 |
| 23 | **未实现（不可达）** | 同 22（decline 与 cast 共用 `_finish_hit_spell` 同一收尾，不存在重复触发点）。 |
| 24 | 通过 | `test_existing_temp_hp_is_kept_when_the_grant_is_lower_or_equal`（既有 8 与 5 两种，事件仍记本次 amount） |
| 25 | 通过 | `test_a_higher_grant_replaces_the_old_temp_hp` |
| 26 | 通过 | `test_temp_hp_absorbs_damage_before_hp` |
| 27 | 通过 | `test_a_dying_source_only_gains_temp_hp`（0 HP 无法行动，故直调辅助后走 `apply_batch`；hp 仍为 0、`unconscious` 与 `death_saves` 不变） |
| 28 | 通过 | `test_temp_hp_event_precedes_victory`（伤害 < temp HP < victory，victory 只 1 条，结束后不回滚） |
| 29 | 通过 | `test_a_replayed_batch_grants_nothing_twice`（同批次二次应用 `duplicate=True` / `applied=False`，temp HP、HP、敌人 HP 全不变） |
| 30 | 通过 | `test_the_trigger_consumes_no_dice`（有/无特性抽取数相同）+ 契约 §8 回归清单全绿 |

- 聚焦测试：`python -m pytest tests/rulesets/test_dnd2024_dark_ones_blessing.py -q` → **48 passed**。

- 契约 §8 命令行（11 个文件一起）：**290 passed**（183.76s，`EXIT=0`）。

- rulesets 全量：`python -m pytest tests/rulesets -q` → **1451 passed / 11 failed（收集 1462 条，无 skip）**。11 条失败逐条映射后**全部**落在 `test_dnd2024_character_builder.py`（`test_derives_first_level_fighter_deterministically`、`test_finalize_adds_legacy_projection…`、`test_normalize_submission_rederives…`、5 条 `test_rejects_illegal_character_choices`、`test_builder_choices_are_constrained…`、`test_stateless_builder_service…`、`test_builder_http_contract…`），与 E2 基线是同一组，根因仍是 `unresolved reference 'ability:con'`（Windows 保留设备名使 `con.json` 无法落盘）。本切片把用例总数从 1433 提到 1462（E1 文件 19 → 48）。注：该次运行的 `pytest` 汇总行在退出时被 `[safe-delete]` 钩子吞掉，`passed/failed` 数目由 1462 条收集清单与进度行逐字计数（1451 `.` + 11 `F`）得出，退出码为 1（= 有失败），与失败清单自洽。

- ruff / format / diff：`ruff check`（5 个改动文件）**All checks passed**；`git diff --check` **clean**；`ruff format --check` 在**未改动**的文件（`combat/engine.py`、`combat/view.py`、`combat/reducer.py`、`tests/rulesets/test_dnd2024_smite.py`）上同样报 "Would reformat"，说明仓库基线本身不是 ruff-format 干净的，故本切片不把全仓重排当作达标条件，也未做无关格式化。

- 非目标确认（§7 七项）：① 10 尺共享/距离/可见性光环——**已删除**，且退役键在加载期被拒；② Fiend 其余特性未动；③ 敌人职业特性/GM 环境伤害未触碰；④ 临时 HP 决策 UI 未新增（沿用 keep-max）；⑤ 击晕/死亡/复活规则未改；⑥ 无 trigger DSL / 插件 / 前端；⑦ victory、reducer、rest、multiclass、S0-D3 掷后窗口均未改。

- 遗留问题：
  1. **矩阵 21/22/23 不可达**：本特性的所有者只有 Fiend 术士（单职业），而 Shield / Uncanny Dodge / Divine Smite 分属术士自带反应与 Paladin 特性，敌人也没有职业反应。三个接点是"将来有多职业或术士拿到防守反应时才生效"的防线，当前不可能触发，故不构造伪造的敌人反应来绿测。若后续切片引入多职业，应回到这里补真实链路证据。
  2. **feed 不下发本特性的审计字段**：ledger 有全部 12 个字段，但 `runtime.py::_recent_combat_events` 的白名单只留 `resource` / `amount` / `delta` / `target_id`，`feature_id` / `owner` / `ability` / `class_ref` / `class_level` / `defeated_actor_id` 都被裁掉。runtime 不在 §5 白名单，本切片未改；若要前端显示"因何获得"，需另开小切片补白名单与展示。
  3. **既有引擎行为（非本切片引入）**：唯一敌人被借机攻击击杀的那一批次里，`dnd2024.combat.ended` 会先把经济清空，随后 held 的 `dnd2024.position.changed` 因移动预算不足抛 `EventBatchError`。矩阵 20 的用例用两个敌人绕开并记录于此；修它属于胜利/移动顺序的独立问题。
  4. 其余 Fiend 特性（Fiend Spells、Dark One's Own Luck、Fiendish Resilience、Hurl Through Hell）仍属后续范围。
  5. **旧 S-P4 任务书仍在库里**（`SP4_DARK_ONES_BLESSING_TASK_SPEC_2026-10-30.md`），其中仍写着 `defeat_share_range_ft: 10`。本切片按 §1.1「不执行、不覆盖旧 S-P4」与 §5「只追加本节」未改它；若担心后来者照它重写，应另开一次性文档切片给旧任务书加「已废弃」抬头。
