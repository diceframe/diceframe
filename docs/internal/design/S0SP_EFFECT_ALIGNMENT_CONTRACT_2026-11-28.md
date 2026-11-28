# 法术效应与 S0-EFFECT1 的对接契约

| 项 | 值 |
|---|---|
| 适用 | 法术线（S0-SP*）后续所有效应语义；也适用于任何往 `spell_effects` 加东西的切片 |
| 上游 | `S0EFFECT1_EFFECT_ITEM_TASK_SPEC_2026-11-28.md`（P1 已实现契约与归一，无消费方） |
| 性质 | 内部约定。不是对外契约，不替代 EFFECT1 自己的任务书 |
| 状态 | 生效 |

## 1. 一句话

**effect item 只描述"对某一个目标造成什么"。** 目标集、时机、施法者的选择，都不是它的语义。

上游任务书 §2 已经这么划了：

> 时机属于五个入口……**目标集**属于来源（法术的 `target_count`、怪物动作的目标、攻击的 `target_id`）……`concentration` / `upcast_*` / `target_count` 是 casting-shape，属于来源。

本文件把这条对法术线展开成可执行的三层，并补一件事：**伤害骰的两种掷法**。

## 2. 三层归属

| 层 | 回答什么 | 归属 | 现有字段 |
|---|---|---|---|
| **payload** | 对目标造成什么 | EFFECT1 闭集（`effect_items.EFFECT_ITEM_FIELDS`） | `damage` / `condition` / `duration` / `escape_dc` / `push` / `max_target_size` / `save` / `half_on_success` / `save_repeat` |
| **条件含义** | 某条件在规则上意味着什么 | `conditions` 条目（EFFECT1 §6 明确不管） | `attack_modes` / `check_modes` / `damage_roll_modifier` |
| **施法形态** | 谁的专注、几环、几个目标、施法时选了什么 | 来源侧 | `concentration` / `upcast_*` / `target_count` / 施法时的选择参数 |

判断一个语义该放哪层，只问一句：**它描述"对目标造成什么"吗？** 不是，就往上两层找。

## 3. 伤害骰：两种掷法都要建模

同一句"造成 8d6"，在规则里是两种事实：

| 语义 | 例子 | 掷几次 |
|---|---|---|
| **独立** | `magic_missile` 的每一发、`spiritual weapon` 的每次 | 每个目标各掷一组 |
| **共享** | `fireball`（"each creature … takes 8d6"） | 全场只掷一组，所有目标共用 |

**两种都存在，所以 payload 要能区分**。新增第 10 个字段：

```json
// 独立（缺省）
{"damage": {"dice": "1d4+1", "type": "force"}}

// 共享
{"damage": {"dice": "8d6", "type": "fire"}, "save": "dex",
 "half_on_success": true, "shared_roll": true}
```

规则：

1. `shared_roll` **缺省 false**，即"每个目标各掷"；
2. `true` 时，**同一次来源结算**对多个目标应用这条 item，伤害骰只掷一次；
3. **豁免永远逐目标**——共享的是伤害骰，不是豁免。`fireball` 里每个生物各掷一次敏捷豁免；
4. `half_on_success` 的"半"从**共享的那组骰**算，所以同一豁免结果的目标掉一样多；
5. `shared_roll: true` 但只有一个目标时，与缺省等价；
6. **只管"同一次应用对多个目标"**。同一法术对同一目标多次应用（`eldritch_blast` 的多道光束、`magic_missile` 的多发）是多次应用，每次各掷——那不是 `shared_roll` 要处理的情况，也不要指望它把多发的伤害合并。

**为什么放在 payload 而不是应用点**：这是规则事实，不是实现选择。放应用点会让同一份内容在不同调用方行为不同（一个 AOE 结算共享、另一个不共享），而内容作者无从表达。

**为什么闭集要扩这一个字段**：EFFECT1 的"未知字段一律拒绝"是为了防止"有数据静默失效"。`shared_roll` 有明确的消费者（AOE 结算），不属静默失效，所以扩。

## 4. 应用点的接口要求

EFFECT1 的 `apply_effect_items` 保持**单 target** 签名不变：

```python
apply_effect_items(instance, combat, source, target, items, rng, *, dc) -> list[Event]
```

`shared_roll` 的复用由**调用方**负责——这是"目标集属于来源"的直接推论：

- AOE 结算遍历受影响目标；
- 若 item 声明 `shared_roll`，调用方在**第一次**应用时掷骰并把结果缓存，后续目标传入同一份；
- 若否，每个目标各调一次、各掷一次。

因此应用点需要一个可选参数承载"已掷好的骰"，形状建议 `pre_rolled: dict[int, list[int]] | None`（键是 item 下标，值是骰面）。这条**必须在上游 P2 定接口时一起定**，否则 P4 收敛 AOE 时会发现签名装不下。

## 5. 阶段：什么时候对接到哪一步

| 阶段 | 可以做 | 不可以做 |
|---|---|---|
| **现在**（P1 只有契约与归一，`apply_effect_items` 归 P2） | 用闭集内已有字段；改**语义**不改**形状**（如时长的到期判据）；把 payload 里的特殊形状往外挪 | 新增 payload 字段（`shared_roll` 除外，它已定契约）；接 `apply_effect_items` |
| **P2/P3 落地后** | 接消费方；`on_fail` / `on_success` 按上游形状写 | 自造时机字段 |
| **P4 收敛** | 存量条目逐条迁到 effect item 形状 | 再往 `spell_effects` 顶层加字段 |

## 6. 法术线的三处自纠（按本契约）

| 现状 | 问题 | 改成 |
|---|---|---|
| `condition: {choices, picked}` | 二选一是**施法时选一**，不是 payload；它让 payload 变成多态 | 走施法时的选择参数，payload 只写被选中的那个条件 |
| `condition_on_success: {condition, duration}` | 是 payload 的**时机变体**，却长在 `spell_effects` 顶层 | 改成 `on_success: [{condition, duration}]`（数组，与 payload 逐项结算一致），等 P3 接 |
| `ongoing` | 无内容使用，是死 schema | 删除 |

## 7. 不做

- 不解释对外 Effect DSL（`docs/rulesets/dnd2024/CONTENT_BUNDLE_CN.md` 的白名单），那是独立提案；
- 不把区域几何（cone / radius / line）引入 payload；
- 不让法术线反过来定义 EFFECT1 的闭集——扩字段要走上游任务书的修订，本文件只记录"法术线需要什么、为什么"。
