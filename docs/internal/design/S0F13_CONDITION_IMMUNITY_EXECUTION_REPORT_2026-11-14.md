# S0-F13 执行报告：条件免疫、抑制与移除

日期：2026-11-14
分支：`feat/dnd2024-s0f13-condition-immunity`
基线：`feat/dnd2024-s0f12-save-survival@a27fdb40`（F12 三个提交之上）
范围：Mindless Rage（狂战士 6）、Self-Restoration（武僧 10）、Aura of Courage（圣武士 10）

---

## 1. 结论

三项特性都落在「声明 + 一处机械」上，并且**区分了三件不同的事**：

| 语义 | 特性 | 落点 | 事件证据 |
| --- | --- | --- | --- |
| 免疫（阻止新条件落地） | Mindless Rage、Aura of Courage | `_condition_immunity_blocker` | `check.resolved.blocked_condition` + `blocked_by_feature` / `blocked_by_aura` |
| 移除（真的删掉条目） | Mindless Rage 进入时、Self-Restoration 回合末 | `_condition_gain_cleanup_events` / `_self_restoration_removal_events` | `condition.removed {reason: feature:<id>}` |
| 抑制（条目保留、机械失效） | Aura of Courage | `_effective_conditions` | 无事件：canonical 不变，视图变化 |

这条区分是本切片的核心。把三者混成一件会立刻出问题：免疫若写成了移除，光环离开时就再也回不来；抑制若写成了移除，恐惧的持续时间、来源与重复豁免都会丢；移除若写成了抑制，条件永远不会真正消失。

验证：新增 **30 passed**；规格第 8 节回归集 **251 passed**；`ruff` 干净；`mypy` 102 文件 0 错；全量 `tests/rulesets` 见 §7。

## 2. 先补的前提：`charmed` / `frightened` 原本没有任何机械声明

这是本切片最先撞上的事实，也是任务书没有预料到的一点：条件注册表里没有 `charmed`、没有 `frightened`（只有 `incapacitated` / `paralyzed` / `stunned` / `unconscious` 与若干 buff）。也就是说「光环里的恐惧不生效」这句话在此之前**无法被观察**：一个没有机械效果的条件，被拦截和不被拦截在结算上完全一样。

因此先补两条声明（`srd_combat_core.json`）：

```json
"charmed": {"cannot_attack_source": true},
"frightened": {
  "attack_modes": [{"side": "actor", "mode": "disadvantage"}],
  "cannot_approach_source": true
}
```

`attack_modes` 是既有字段（`blurred` / `invisible` 已经在用），两个 `cannot_*` 是新字段。它们要被验证器接受，必须改 `combat/catalog.py` 的闭集白名单——**这是本切片唯一一个白名单外的文件**，理由见 §9.1。

两条限制都按「条件的来源」解释，而不是按条件 id 硬编码：

- `cannot_attack_source`：`_validate_attack` 从有效条件里取出声明了该标志的条目，读它的 `source_actor_id`，目标等于来源时拒绝；
- `cannot_approach_source`：`_validate_move` 比较移动前后的距离，靠近来源时拒绝。

因此代码里没有 `if condition == "frightened"`，将来一条「不能靠近某物」的新条件只需要一行声明。

## 3. 免疫：一个入口，两种来源

原来只有光环免疫一个来源，且只接在法术结算的那一条路径上。Mindless Rage 是「跟着自身状态走的免疫」，如果照抄就会变成第二个平行实现，于是合并成一个入口：

```python
def _condition_immunity_blocker(instance, combat, target, condition_id):
    aura = self._aura_condition_immunity_source(...)      # 站在谁的灵光里
    if aura is not None:
        return {**aura, "scope": "aura"}
    passive = self._passive_condition_immunity(...)       # 自己带着的免疫
    if passive is not None:
        return {**passive, "scope": "feature"}
    return None
```

`_passive_condition_immunity` 读的是声明（`immune_conditions`）与门控（`immune_while_condition: "rage"`），门控读的是**有效条件**——被光环抑制掉的条件不能反手点亮第二个免疫。

事件形状保留向后兼容：`blocked_by_aura` 仍是原来那三个字段（既有测试用精确相等比较它），新来源写 `blocked_by_feature`；`scope` 只用于内部分流，不落到事件里。客户端消息文案从 "has no effect inside an aura" 改成 "has no effect on this creature"，因为没有光环的免疫也会走到这里。

## 4. 抑制：canonical 不动，读口统一

```python
def _effective_conditions(instance, combat, actor):
    suppressed = self._suppressed_condition_codes(instance, combat, actor)
    if not suppressed:
        return actor["conditions"]
    return {code: value for code, value in actor["conditions"].items() if code not in suppressed}
```

`_suppressed_condition_codes` 只做两件事：取出「有声明能免疫掉的条件」与「这个角色确实挂着的条件」的交集，再逐个问 `_aura_condition_immunity_source`。没有任何声明能免疫 `poisoned` 时，它不做投影、不进距离计算——这是让「每个施加路径都问一次」变得付得起的关键。

接进的读口：

| 读口 | 用途 | 文件 |
| --- | --- | --- |
| `condition_flags` | 失能/速度/自动失败门控 | `validation.py` |
| `attack_mode`（新增可选 `combat=`） | 攻守双方的优劣势来源 | `check_modes.py` + `resolution.py` 两处调用 |
| `condition_check_bonus_rolls` | 攻击加值骰 | `resolution.py` |
| `_restriction_sources` | 攻击目标与移动方向限制 | `validation.py` |

`attack_mode` 在拿到 `combat` 时用 `_effective_target` 包一层视图再交给纯函数 `attack_sources`，这样既没有把 `combat` 塞进纯函数，也没有出现「攻击已抑制、移动没抑制」的分裂。

**没有接的读口**：`effective_speed` / `_fresh_economy`。它们只读 `speed_zero` / `speed_modifier`，而 `charmed`、`frightened` 都不声明速度效果，接与不接的答案完全相同；`_fresh_economy(actor)` 目前拿不到 `instance`/`combat`，硬接需要改十余个调用点，属于「为了对称而改」，本轮不做（§9.4）。

## 5. Mindless Rage：进入时清理，之后才免疫

两条规则，两个落点：

1. **进入狂暴**：`action.kind == "rage"` 之后追加 `_condition_gain_cleanup_events(instance, actor, "rage")`。它遍历该角色拥有的特性，找 `removes_conditions_on_gaining_condition == "rage"`，对声明的每一项**如果当前确实挂着**就发一条 `condition.removed`，`reason` 为 `feature:mindless_rage`。两个条件各一条事件，可以分别审计；不存在的条件不发事件（不制造"清空了空气"的记录）。
2. **Sustain Rage**：`action.kind == "rage_sustain"` 不调用清理。续期不是再次进入，RAW 也不允许它在同一回合重复触发。
3. **免疫**：从进入的那一刻起，`charmed`/`frightened` 被 `_condition_immunity_blocker` 拦在落地之前，`condition.applied` 根本不产生。
4. **结束狂暴**：`rage` 条目消失后门控不成立，新条件正常落地；之前被移除的不会回来（删除是不可逆的）。

## 6. Self-Restoration：一次一项，服务端重验

- `end_turn` 意图新增可选 `self_restoration_condition`；缺省或 `null` 表示不使用，引擎**从不**替玩家挑一项。
- 校验（`_validate_self_restoration`）依次确认：角色确实拥有某条 `removable_at == "own_turn_end"` 的特性、所选值在该特性的 `removable_conditions` 白名单里、且角色**当前确实挂着**这个条件。三条任一不满足就拒绝，状态不动。
- 结算在 `_end_turn_events` 里，且在重复豁免 tick **之后**：若这条条件已经在本次 tick 里被豁免掉，就不再产生第二条移除事件（否则一次"结束"会留下两条针对同一条件的删除记录）。
- 面板可见性：`available_intents` 的 `end_turn` 条目在不适用时不出现 `self_restoration_options` 字段，适用时给出「白名单 ∩ 实际条件」；客户端因此不需要自己实现一份白名单。

**Exhaustion 的第二效果没有实现，只留了声明**。仓库里不存在任何提高 Exhaustion 的路径（`grep exhaustion` 只有长休的 `-1`），也就没有「不进食」的来源可言。按任务书 4.3.5，猜一个来源比不做更糟，所以：声明里写了 `exhaustion_sources_ignored: ["food_drink"]`，没有消费者；测试只钉住「长休仍然只减一级」这条不回归，并在 §9.3 明确记成未做项。

## 7. 验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增用例 | `pytest tests/rulesets/test_dnd2024_condition_immunity.py -q` | 30 passed |
| 规格回归集 | 任务书 §8 的 5 个文件 + `paladin_auras` / `aura_devotion` / `invisibility` | 251 passed |
| 全量 | `pytest tests/rulesets -q -n 4 --dist loadfile` | 1913 passed / 11 failed（全部为 Windows 既有基线） |
| 静态检查 | `ruff check src/ tests/rulesets` | All checks passed |
| 类型检查 | `mypy`（102 文件） | 0 errors |
| 空白 | `git diff --check` | 干净 |

全量结果：`1913 passed, 11 failed in 412.24s`。11 条失败与 F12 记过的基线完全一致，全部落在 `test_dnd2024_character_builder.py`（`RulesetBundleError: unresolved reference 'ability:con' in background:soldier`，其中 1 条是同一错误经 HTTP 冒烟放大成 404）。该批失败在 F12 已用 base 工作树对照确认是 Windows 环境既有问题，本轮新增失败数为 0；通过数从 1882 增至 1913，正好等于新增 30 条用例加子职矩阵补的一行。

## 8. 规格条目 → 测试映射

| # | 规格要求 | 证据 | 状态 |
| --- | --- | --- | --- |
| 1 | 三项特性在正确职业/子职与等级授予；低于等级无资格；双语标签正确 | `test_the_three_declarations_arrive_with_their_owners`（owner/level/source_ref/参数逐项）、`test_a_level_below_grants_nothing`、`test_the_labels_exist_in_both_locales`、`test_the_declarations_fail_closed_when_malformed`（8 种坏形状） | 完整 |
| 2 | Rage 中新增 Charmed/Frightened 被免疫且无机械减益；其他条件不被泛化免疫 | `test_a_raging_berserker_blocks_a_new_charmed`（真实 `charm_person`）、`test_mindless_rage_does_not_immunize_anything_else`（poisoned/stunned/paralyzed/blinded） | 完整（charmed 端到端；frightened 见 §9.2） |
| 3 | 进入 Rage 同时清除已有 Charmed 与 Frightened，各一条可审计事件 | `test_entering_the_rage_ends_what_was_already_there` | 完整 |
| 4 | Sustain 不重复清理；Rage 结束后新条件可生效；已移除不恢复 | `test_sustaining_the_rage_does_not_clear_again`、`test_after_the_rage_ends_the_condition_lands_again` | 完整 |
| 5 | 无该特性的野蛮人保持原行为，免疫不外泄 | `test_a_barbarian_without_the_subclass_keeps_the_condition` | 完整 |
| 6 | 武僧回合末可移除一项；`condition.removed` 与来源准确 | `test_the_turn_end_removes_the_chosen_condition_only`、`test_the_panel_is_told_which_conditions_may_be_removed` | 完整 |
| 7 | 三个合法条件一次只删所选一个；重复结束不能多删 | `test_the_turn_end_removes_the_chosen_condition_only`、`test_a_second_turn_cannot_remove_the_same_condition_twice` | 完整 |
| 8 | 缺省不移除；非法/他人/未持有/非本回合均拒绝 | `test_no_choice_means_no_removal`、`test_an_illegal_choice_is_refused`、`test_a_character_without_the_feature_cannot_use_it`（非本回合由既有的 `it is not this actor's turn` 守卫覆盖） | 完整 |
| 9 | 不移除 Exhaustion / Stunned / 非白名单条件 | `test_exhaustion_and_stunned_are_never_on_the_list` | 完整 |
| 10 | 不进食不增加 Exhaustion；其他来源与长休不减回归 | `test_forgoing_food_is_declared_but_has_no_producer_yet` | **部分**：仓库无 Exhaustion 增长路径（§9.3） |
| 11 | 圣武士自己与光环内盟友免疫 Frightened；敌人与光环外不免疫 | `test_the_paladin_and_an_ally_inside_are_immune_to_frightened` | 完整 |
| 12 | 光环内新施加 Frightened 不产生机械效果 | `_condition_immunity_blocker` 断言（同一入口，charmed 已端到端） | 部分（§9.2） |
| 13 | 已有 Frightened 盟友进入后条目仍在，攻击劣势与不能接近暂停 | `test_an_existing_frightened_is_kept_but_loses_its_teeth`（canonical 保留来源/剩余回合；有效视图剔除；攻击模式从 disadvantage 变空） | 完整 |
| 14 | 离开光环后同一来源实例恢复效果，不新增/不刷新/不复制 | `test_leaving_the_aura_restores_the_same_entry`（逐字段 deepcopy 比较） | 完整 |
| 15 | 圣武士失能使光环失活；恢复后重算 | `test_an_incapacitated_paladin_switches_the_aura_off` | 完整 |
| 16 | 抑制期间重复豁免成功会真正移除，离开后不复活 | `test_a_suppressed_condition_can_still_be_removed_for_good`（用 Self-Restoration 在光环内真实移除，再走出光环） | 完整（用移除通道代替重复豁免，见 §9.2） |
| 17 | 移动进出、回合推进、条件到期、owner 变化都按当前事实计算 | 同 14/15 + `test_the_entry_and_its_suppression_survive_a_save_and_load` | 完整（无缓存：抑制是每次现算） |
| 18 | 多个圣武士光环不叠加、不误删 | `test_two_paladins_sharing_a_body_do_not_stack_or_delete` | 完整 |
| 19 | 抑制对 `condition_flags`、攻守模式、速度、意图验证一致 | `test_suppression_reads_the_same_in_flags_modes_and_validation`、`test_a_frightened_ally_cannot_walk_towards_its_fear`、`test_a_charmed_creature_cannot_attack_its_charmer`、`test_an_existing_frightened_is_kept_but_loses_its_teeth` | 完整（速度未接，见 §9.4） |
| 20 | 存档/加载、apply/replay 后 canonical 与有效视图一致；重放不重复移除 | `test_the_entry_and_its_suppression_survive_a_save_and_load`、`test_replaying_the_removal_batch_does_not_remove_twice` | 完整 |
| 21 | 并发/过期 version 的 end_turn 与条件施加 fail closed | `test_a_stale_version_is_refused_without_a_partial_write` | 完整 |
| 22 | 既有 Rage / Paladin Aura / Hold Person / Invisible / Stunned 回归 | 规格回归集 251 passed | 完整 |

## 9. 偏差 / 未决 / 只看门禁才现形的缺陷

1. **白名单外一个文件：`src/rulesets/dnd2024/combat/catalog.py`**。条件注册表是闭集校验（未知字段直接拒绝加载），而 `charmed`/`frightened` 需要的两个限制没有任何既有字段能表达。替代方案只有两条，都比改校验器差：在 `_validate_attack` 里写死 `"frightened" in conditions`（违反「不按条件名分支」），或者不做「不能接近」这一半（任务书 13 项明确要求它）。改动只有三处：允许字段集合加两个名字、布尔标志循环加两个名字、「每个条目必须声明点什么」的清单加两个名字。

2. **`frightened` 没有内容能施加，因此"新施加被拦"只能测到入口层**。本包的 `spell_effects` 里没有任何法术会施加 `frightened`（`charm_person` 是唯一施加 `charmed` 的），也没有恐惧类冒险内容。所以：`charmed` 走真实施法端到端覆盖整条拦截分支；`frightened` 用同一个入口函数断言。这不是两条实现，而是同一条分支的两种验证深度。

3. **Exhaustion 第二效果未实现**。仓库里没有提高 Exhaustion 的路径（唯一写入点是长休的一级递减），也就没有「不进食/不饮水」这个来源。猜一个来源或改成「全 Exhaustion 免疫」都被任务书明确禁止，因此只留声明、不加消费者，测试只钉住长休不回归。要真正实现，需要冒险/hazard 侧的饮食追踪，属于白名单外的工作。

4. **速度读取点没有接有效条件**。`effective_speed` / `_fresh_economy` 只读 `speed_zero` / `speed_modifier`，而当前可被免疫的条件都不声明速度效果，接与不接答案相同。将来若有「被恐惧则速度减半」这类声明（2024 规则里没有），必须同时改这两处与 `_fresh_economy` 的签名。

5. **`charmed` 只拦住了攻击掷骰**。「不能以伤害性能力或魔法效果指定施法者」还应该覆盖需要对施法者做豁免的伤害法术；这需要在 `_validate_spell` 里读法术的伤害声明，本轮没做。当前实现下，被魅惑的角色仍能对施法者施放 `burning_hands` 这类需要豁免的伤害法术。

6. **没有来源的旧条目不受限制**。条件值可以是标量（历史形状）或字典；`_restriction_sources` 需要 `source_actor_id` 才能把限制绑定到具体对象，标量条目无法绑定，因此两条限制对它不生效。所有服务端施加路径都会写 `source_actor_id`，这只影响手工构造或极旧的存档。

7. **两处既有测试因本切片而修改**，都记录了原因：
   - `test_dnd2024_paladin_auras.py` 的 malformed 矩阵原来用 `["frightened"]` 当"不支持的条件"，现在它合法了，换成 `["blinded"]` 保持同一条边界；
   - `test_dnd2024_class_features.py` 的子职清单补上 `("class:barbarian", 6, "mindless_rage", "subclass:path_of_the_berserker")`，否则新子职特性游离在矩阵之外。

8. **`Self-Restoration` 与重复豁免的先后**：两者都在自己回合结束触发，实现上移除排在重复豁免之后，并且如果同一条条件已被豁免掉就不再发移除事件。任务书没有规定这个顺序，这里的选择是"不产生两条针对同一条件的删除记录"；如果将来有特性在同一个时刻要求不同的先后（例如"先移除再豁免"），需要显式建模而不是依赖事件顺序。

9. **未做**：F10（行动经济路由）仍停在缺遮蔽/视线模型上，F11（伤害反应）与它同一缺口；本切片没有动那部分。
