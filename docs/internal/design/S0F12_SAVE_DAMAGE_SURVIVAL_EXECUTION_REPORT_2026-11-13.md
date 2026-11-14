# S0-F12 执行报告：豁免优势、Evasion、邪魔韧性与无情狂怒

日期：2026-11-13
分支：`feat/dnd2024-s0f12-save-survival`
范围：Danger Sense（野蛮人 2）、Evasion（武僧 7 / 游荡者 7）、Fiendish Resilience（邪魔宗主 10）、Relentless Rage（野蛮人 11）
关联：`S0F10_ACTION_ROUTING_STOP_NOTE_2026-11-13.md`（F10 停止判定；F12 不依赖行动经济与遮蔽模型，因此可以独立交付）

---

## 1. 结论

四条特性全部按「声明 + 机械」落地，没有一条写成只在前端展示的文案：

| 特性 | 声明位置 | 参数 | 机械落点 |
| --- | --- | --- | --- |
| Danger Sense | 野蛮人 2 | `save_advantage_abilities: ["dex"]` | `_saving_throw_sources` |
| Evasion | 武僧 7、游荡者 7（各自 owner） | `half_on_success_save: "dex"` | 豁免分支的伤害改写 |
| Fiendish Resilience | `subclass:fiend_patron` 10 | `excluded_damage_types: ["force"]` | 休息引擎 + `_resist_damage` 第三来源 |
| Relentless Rage | 野蛮人 11 | `save_ability: con` / `base_dc: 10` / `dc_step: 5` / `hp_multiplier: 2` | reducer 归零前拦截 + `_finish_relentless_rage` |

locale 标签（`危险感知` / `反射闪避` / `邪魔韧性` / `无情狂怒`）在 `locales/{en,zh-CN}/class_feature_catalogs/srd_class_features.json` 中已经存在，本轮没有新增文案。

验证结果：新增测试 35 passed；规格第 8 节的回归集 167 passed；`ruff check` 与 `mypy`（102 文件）干净；全量 `tests/rulesets` 结果见 §7。

## 2. 豁免优势只在一个地方汇总

原来有三处各自内联「狂暴给力量豁免优势 / 未受训护甲给力敏豁免劣势」：法术豁免、状态重复豁免、以及命中结算路径上的豁免。加 Danger Sense 时如果照抄，就会变成三处同逻辑、三处各漏一半。

因此新增 `_saving_throw_sources(actor, ability)` 作为唯一聚合点：

- 狂暴（`rage` 条件 + `str`）→ advantage；
- 未受训护甲（力/敏）→ disadvantage；
- Danger Sense：读声明 `save_advantage_abilities`，命中当前豁免项才给 advantage，**失能时不查询**（失能本身就是 RAW 的剥夺条件，提前 return 比在来源里过滤更难写错）。

返回的是来源列表，交给既有的 `mode_from_sources` 归一，所以：

- 两个优势来源仍然只有两骰（来源列表是证据，不是第二次重掷）；
- 优势 + 劣势相消回到单骰（这条是老行为，本轮只是换了个入口，回归集覆盖）；
- 事件里 `advantage_sources` 只写实际命中的来源，前端不需要自己猜为什么掷了两骰。

## 3. Evasion 只改写它自己那一类分支

`_evasion_applies(target, ability)` 同时要求三件事：目标是玩家/同伴、未失能、声明值等于当前豁免项。真正的伤害改写发生在「效果允许敏捷豁免半伤」的分支里：

```python
half_on_success = bool(effect.get("half_on_success"))
if half_on_success and self._evasion_applies(target, save_ability):
    damage = 0 if succeeded else damage // 2
elif succeeded:
    damage = damage // 2 if half_on_success else 0
```

这样有两个必须保住的性质：

1. **不是「所有敏捷豁免都减半」**：`sacred_flame` 是敏捷豁免但成功不减伤（没有 `half_on_success`），Evasion 必须完全不插手，失败照吃满伤害。
2. **只减半一次**：Evasion 改写完之后才走 `_resist_damage`，而抗性本身只应用一次。12 → Evasion 6 → 抗性 3，三层减半是典型写错法，测试用「12/6/3」把顺序钉住。

失能时另有既存规则：敏捷豁免自动失败（不掷 d20），此时 Evasion 也失效，伤害吃满 12。这两件事在同一条路径上，测试同时断言 `auto_failed is True`、`rolls == []`、`amount == 12`。

## 4. 邪魔韧性：状态放角色，不放在战斗里

选择结果写在 `character.features.fiendish_resilience.damage_type`，理由是这个选择跨战斗存活、而且只能在休息时改：

- **短休与长休都能改选**（RAW：每次短休或长休结束时选择）；
- **不选就不动**：参数为 `None` 表示「保持原样」，第一次休息没有选择时**不默认任何类型**（不默认 fire、不默认上次的相反），角色就是没有这条抗性；
- **非法选择先拒绝、后落账**：`_plan_fiendish_resilience` 在任何 HP/槽位变化之前完成校验（类型必须在 `DAMAGE_TYPES` 里、不能是声明排除的 `force`、角色必须真的有这条特性），非法时抛 `RestError` 且角色状态一字不改；
- **作为第三个抗性来源**：`_resist_damage` 先扫条件来源（有 `resist_damage_types` 的条件），全部不命中后才查角色上的选择，返回 `"fiendish_resilience"` 作为事件里的 `resistance` 证据。放在最后意味着它永远不会和条件抗性叠成四分之一。

## 5. 无情狂怒：拦截点在 reducer，不在 resolution

「掉到 0 HP 时能不能救回来」这件事，只有 reducer 知道答案：临时 HP 先吸收，扣完才可能真的到 0。如果在 resolution 里判断「这次伤害会不会致死」，就会漏掉临时 HP，也会在伤害还没落地时开窗。

### 5.1 伤害事件带标记

`_relentless_rage_marker` 只在同时满足「玩家/同伴 + 处于狂暴 + 有这条特性 + 有野蛮人职业等级」时返回标记，内容是：

- `dc`：声明的 `base_dc`，若角色状态里存了 `next_dc` 则用它（DC 阶梯的持久化）；
- `dc_step` / `hp_amount`（= `hp_multiplier × 野蛮人职业等级`，按**职业等级**不是总等级）；
- `assigned_to`：玩家本人；AI 托管的席位给 GM。

它**不掷骰、不开决策**。四个伤害出口（武器命中、法术豁免、法术命中/自动伤害、状态重复）都把标记挂到 `damage_event["relentless"]` 上。

### 5.2 reducer 的归零前拦截

```python
temp = int(resources.get("temp_hp", 0) or 0)
if delta < 0 and temp > 0 and not event.get("healing"):
    absorbed = min(temp, -delta)      # 临时 HP 先吸收
    resources["temp_hp"] = temp - absorbed
    delta += absorbed
next_hp = max(0, min(maximum, before + delta))
relentless = event.get("relentless")
if isinstance(relentless, dict) and next_hp <= 0 and before > 0 \
        and not event.get("relentless_deferred"):
    self._open_relentless_rage(snapshot, target_id, before, event, relentless)
    return                            # HP 不写、昏迷不发生、狂暴没结束
resources["hp"] = next_hp
```

顺序是刻意的：临时 HP 吸收发生在拦截**之前**，这样「临时 HP 挡住这一击」根本不会开窗；拦截发生在写 HP、加 `unconscious`、结束狂暴**之前**，所以窗口挂起时角色仍然是「3 HP、狂暴中、清醒」。

挂起的决策带 `damage_event`（复制并标 `relentless_deferred: True`），`decision_id` 由 `round / turn_index / 拦截前 HP` 派生——同一回合重放到同一次伤害会命中同一个 id，不会开出第二个窗。

`_apply_hp_change` 里临时 HP 吸收新增 `not event.get("healing")` 条件，是因为成功恢复复用 `resource.changed` 事件形状表示「HP 改为某值」，不能被当成伤害再被临时 HP 吸收一次。

### 5.3 结算与事件顺序

成功（`option=use` 且豁免通过）时的事件序列：

```
dnd2024.decision.cleared            { option: "use" }
check.resolved                      { kind: saving_throw, ability: con, target: 10, rolls: [15], success: true }
dnd2024.feature_state.set           { feature_id: relentless_rage, values: { next_dc: 15 } }
resource.changed (hp)               { delta: +19, healing: true, relentless_rage: true, hp_after: 22 }
```

失败时最后一条换成挂起的那条伤害事件（原样提交，`relentless_deferred` 仍在，所以不会再开一次窗）；`decline` 只有 cleared + 伤害事件，**且不写 `next_dc`**。

三点需要固定住：

- **DC 递增写在豁免结果之后、成功/失败之前**：成功与失败都是「一次实际使用」，都要 +5；放弃不是使用，不递增。
- **只掷一个 d20**：一次 Con 豁免就是一次豁免，成功恢复既不补骰也不重开受伤反应（`_CountingRng` 断言 `calls == 1`）。有优势/劣势时仍按 `mode_from_sources` 掷两骰，这个分支与其它豁免共用同一段。
- **成功恢复不是治疗**：HP 直接改为 `2 × 野蛮人等级`（并按最大 HP 截断），没有治疗事件、不触发「受到治疗」类反应；事件上的 `healing: true` 只用于让临时 HP 吸收逻辑跳过它，`relentless_rage: true` 供需要区分的地方使用。

### 5.4 客户端只能选「用不用」

`decision.resolve` 的客户端载荷里出现 `total` / `dc` / `level` / `hp` 这类字段一律忽略：DC 来自决策记录（决策记录来自声明 + 角色状态），豁免值来自服务端掷骰，恢复量来自野蛮人职业等级。测试专门伪造了 `total: 30, dc: 1, level: 20, hp: 99`，结果仍然是 DC 10、按真实骰子判失败、`next_dc` 变 15。

### 5.5 决策窗口互斥

挂起期间除了 `decision.resolve` 与 `combat.message`，其它 intent 一律被 validation 拒绝（`resolve the relentless rage decision first`）。这与既有的 `hit_reaction` / `hit_reaction_spell` 窗口同一条规则，避免「窗口还开着就结束回合」把挂起伤害丢掉。

## 6. 特性状态的白名单

新增事件 `dnd2024.feature_state.set`，写进既有 `character.features.*`，键集合是闭集：

```python
FEATURE_STATE_KEYS = {
    "relentless_rage": frozenset({"next_dc"}),
    "fiendish_resilience": frozenset({"damage_type"}),
}
```

三点约束：

1. reducer 只接受这两个 feature_id、只接受集合内的字段，未知字段抛 `EventBatchError`（fail closed），避免它退化成「任意写 character」的后门；
2. 只能写玩家/同伴，敌人没有 canonical 角色；
3. 同一个闭集同时是 combat actor view 的投影范围——战斗读不到的状态就不该由战斗写。这条对称性是本轮特意保住的性质：如果 view 不投影 `next_dc`，`_relentless_rage_marker` 会读到空值然后把 DC 退回 10，重放和读档都会悄悄重置阶梯。

## 7. 验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增用例 | `pytest tests/rulesets/test_dnd2024_save_damage_survival.py -q` | 35 passed |
| 规格回归集 | `pytest tests/rulesets/test_dnd2024_{rage,resting,combat,spells,class_features,invisibility}.py -q` | 167 passed |
| 全量 | `pytest tests/rulesets -q -n 4 --dist loadfile` | 1882 passed / 11 failed（全部为 Windows 既有基线，见下） |
| 静态检查 | `ruff check src/ tests/rulesets` | 通过 |
| 类型检查 | `mypy`（102 文件） | 0 errors |
| 空白 | `git diff --check` | 干净 |

全量结果：`1882 passed, 11 failed in 508.86s`。11 条失败全部落在 `test_dnd2024_character_builder.py`，根因是 `RulesetBundleError: unresolved reference 'ability:con' in background:soldier`（其中 1 条是同一错误经 HTTP 冒烟放大成 404），与本切片无关。

对照验证：新建 base 工作树（`git worktree add /c/tmp/f12-base d49010c4`，即本分支的父提交）单跑同一文件，得到**完全相同的 11 条失败 / 28 passed**。所以本轮新增失败数为 0，全量通过的绝对数是 1882。

## 8. 规格条目 → 测试映射

按任务书 §6 的 22 条逐项对照。「完整」= 有走真实链路的行为断言；「部分」= 关键的一半有断言、另一半只能结构说明或当前没有可写路径。

| # | 规格要求 | 证据 | 状态 |
| --- | --- | --- | --- |
| 1 | 五项声明的职业/子职、等级、owner、source_ref | `test_the_five_declarations_arrive_with_their_owners`（含 5 条 `(owner, source_ref)` 精确比对）、`test_a_level_below_grants_nothing` | 完整 |
| 2 | Dex 豁免两骰取高、来源可观察；多优势来源仍两骰 | `test_danger_sense_rolls_two_dice_on_a_dexterity_save` | 完整 |
| 3 | 不影响 Str/Con/Wis 豁免、攻击、能力检定、先攻 | 同上的 `_saving_throw_sources` 断言、`test_danger_sense_leaves_other_saves_and_attacks_alone`、`test_danger_sense_never_reaches_weapon_attacks_or_initiative` | 部分：能力检定在战斗里没有独立 intent，只能由「唯一消费点是豁免分支」保证 |
| 4 | 失能不给优势（Paralyzed/Stunned/Unconscious） | `test_an_incapacitated_barbarian_gets_no_danger_sense`（3 个条件） | 完整 |
| 5 | Monk/Rogue 对合法半伤 Dex 效果：成功 0、失败半伤、不重复 | `test_evasion_takes_nothing_on_a_success_and_half_on_a_failure`（两个 owner 参数化） | 完整 |
| 6 | 非 half-on-success 的 Dex 效果、无豁免攻击、其它属性不被改写 | `test_evasion_ignores_effects_that_do_not_allow_half_damage`（sacred_flame）、`test_evasion_does_not_touch_weapon_attacks`、`test_evasion_ignores_other_abilities_even_when_they_allow_half`（thunderwave） | 完整 |
| 7 | 失能时 Evasion 失效、恢复原成功半伤/失败全伤与自动失败 | `test_evasion_is_lost_while_incapacitated` | 完整 |
| 8 | 只改伤害、不删原效果的条件/位移；抗性只应用一次 | `test_evasion_halves_before_resistance_and_only_once` | 部分：抗性顺序完整；「半伤 Dex 效果 + 条件/位移 rider」在现有内容里不存在，没有可断言样本（见 §9.2） |
| 9 | 奇数向下取整；成功 0 不产生虚假治疗或重复专注伤害 | `test_evasion_rounds_odd_damage_down`（13 → 6）、成功 0 那条、`test_a_successful_evasion_half_save_is_not_a_concentration_hit` | 完整 |
| 10 | 短休/长休均可改选，Force 始终拒绝 | `test_a_fiend_rechooses_its_resistance_on_a_short_rest`、`test_a_long_rest_can_rechoose_and_force_is_refused` | 完整 |
| 11 | 非法类型/bool/数组/未知字段/未经休息改选均拒绝且状态不变 | `test_an_invalid_choice_is_refused_without_touching_state`（5 种坏值 + 深比较） | 完整：未知字段与「不经休息改选」没有可写通道（唯一写入口是休息参数与 reducer 白名单） |
| 12 | 无改选保留原值；首次不默认；存档/加载保持同一选择 | `test_a_rest_without_a_choice_keeps_the_old_resistance`、`test_a_first_rest_without_a_choice_does_not_default_a_type`、`test_a_fiend_choice_survives_a_save_and_load`（JSON 往返） | 完整 |
| 13 | 只减半选中类型一次，其它类型与 Force 不受影响，事件含旧/新选择 | `test_only_the_chosen_type_is_halved`、`test_a_fiend_rechooses_its_resistance_on_a_short_rest`（`before/after`） | 完整 |
| 14 | 11 级 Rage 中归零可试 DC10；非 Rage 或非归零不触发 | `test_a_zeroing_hit_opens_the_survival_window_without_changing_hp`、`test_relentless_rage_requires_an_active_rage`、`test_a_non_zeroing_hit_never_opens_the_window` | 完整 |
| 15 | 临时 HP 全吸收不触发；outright death 不走存活分支 | `test_temp_hp_that_absorbs_the_hit_never_opens_the_window` | 部分：系统没有 outright death 判定（§9.3），本切片按非目标不引入死亡系统 |
| 16 | 成功 HP = 2×Barbarian level、不加 Unconscious、不结束 Rage、下一 DC 15 | `test_a_successful_save_replaces_the_drop_with_twice_the_barbarian_level` | 完整 |
| 17 | 连续尝试 10/15/20；失败走正常归零且递增；decline 不递增 | `test_the_dc_ladder_is_ten_then_fifteen_then_twenty`、`test_a_failed_save_falls_back_to_zero_hp_and_still_raises_the_dc`、`test_declining_is_not_a_use_and_leaves_the_dc_alone` | 完整 |
| 18 | 短休/长休重置；升级、普通回合、战斗结束不重置 | `test_a_rest_resets_the_ladder_and_combat_end_does_not`（战斗结束 + 升级 + 长休）、`test_a_successful_save_...`（回合边界） | 完整 |
| 19 | 合法加值/来源合成准确；客户端伪造 total/DC/level/HP 被忽略 | `test_client_cannot_supply_the_total_dc_level_or_hp` | 部分：伪造与「DC/判定值来自服务端」完整；真实光环加值需要圣武士队友，本文件未复测（同一函数由既有光环测试覆盖） |
| 20 | 同一伤害只触发一次；成功不重复受伤反应、不产生额外治疗或 RNG | `test_a_successful_survival_rolls_exactly_one_die`、成功那条的「无治疗事件 + 无反应窗口」断言 | 完整 |
| 21 | save/load、pending decision、apply/replay 的 HP/Rage/条件/DC/RNG 一致 | `test_the_ladder_survives_a_save_and_load`、`test_replaying_the_survival_batch_does_not_raise_the_dc_twice` | 完整（pending decision 不跨存档：读档只带 canonical 角色，窗口由新的归零伤害重新开，DC 从持久化状态读回） |
| 22 | 既有 Rage/Overchannel/Paladin Aura/死亡豁免/休息/抗性测试无回归 | 规格回归集 167 passed + 全量 `tests/rulesets` | 完整 |

## 9. 偏差 / 未决 / 只看门禁才现形的缺陷

1. **恢复事件复用了 `resource.changed`**：`healing: true` 在这里的语义是「这是一次 HP 上调，别让临时 HP 吸收它」。前端 `CombatLiveBar.vue` 会把 `healing === true` 渲染成治疗反馈（绿色），从玩家视角这是想要的效果（HP 确实涨了），但如果将来有「受到治疗时触发」的反应判定，必须用 `relentless_rage` 标记而不是 `healing` 来区分——目前没有这类反应，所以只留了标记、没有接消费方。
2. **发现了独立的既有缺陷（不在本切片范围）**：`thunderwave` 的 `push` 在 `if succeeded and push` 分支触发（`resolution.py:1489`），按 SRD 应在豁免**失败**时推离 10 尺。修它属于效果语义变更，超出 F12 白名单与非目标，本轮只记录、不修，也没有拿它当 Evasion 的 rider 证据。同一条也解释了 §8 第 8 项为什么只能标「部分」。
3. **outright death 没有模型**：任务书 §4.3.2 要求触发前排除「一次性致死」，但全仓没有这个判定（检索 `outright` / `massive` 为空），停止条件 5 描述的正是这个缺口。本切片既不引入死亡系统（非目标），也就无法为这一子项写出真实测试；`_open_relentless_rage` 只在 `before > 0` 且 `next_hp <= 0` 时开窗，不做「是否当场死亡」的判断。
4. **`_saving_throw_sources` 只聚合「代码事实 + 一条声明」**：未来若出现豁免**加值**（圣武士光环、装备），它走既有的 aura/`check_bonus` 通道，不要塞进 sources，否则优势与加值会被混成一件东西。`_finish_relentless_rage` 里两者已经分开计算（`sources` 决定骰数、`bonus` 与 `aura` 加成总值）。
5. **多职业野蛮人没有测试**：`hp_amount` 取 `class_levels` 里 `class:barbarian` 的等级，不是总等级（RAW 正确），但没有「战士 5 / 野蛮人 11」这类样本；结构上只依赖那一行的 `level`，风险低但确实未覆盖。
6. **`excluded_damage_types` 的校验只在休息引擎**：声明里写 `force`，战斗侧的 `_resist_damage` 只比对类型字符串。如果将来有第二条路径能写 `damage_type`（例如 GM 面板直接改角色），排除规则会被绕过——目前唯一写入口是休息引擎与 reducer 白名单，后者只允许 `damage_type` 字段、不校验取值。
7. **Evasion 不处理「成功才减半、失败吃满」的逆向效果**：现有内容里 `half_on_success` 是布尔、语义固定，没有为逆向形状留分支；将来出现时应在 effect 上新增字段，而不是让 Evasion 去猜。
8. **测试夹具的两个修正值得记一笔**：`_advance` 原来把所有 ASI 都投 CON，升到野蛮人 12 级时撞上 `con above 20`；现在改为「在允许项里挑第一个还有余量的属性」。`_CountingRng` 原来只数 `randint` 总次数，无法区分 d20 与伤害骰；现在分别记 `calls` 与 `d20`，才能断言「一次豁免恰好一个 d20」。
9. **未做**：F10（行动经济路由）与 F11（遮蔽/掩蔽/视线）都停在「缺 Total Cover / Hide 模型」上，停止说明已写明两者应一起补模型，本轮没有动那部分。

