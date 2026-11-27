# 任务书：S0-F7C 武器词条 Cleave（授予式第二击）

| 项 | 值 |
|---|---|
| 切片 | S0-F7C（F7 的最后一条词条） |
| 验收载体 | 词条 `cleave`：命中后对**另一个**目标再打一次 |
| 规则基准 | SRD 5.2.1 p90（Cleave）、p89（Light，对照） |
| 代码基线 | `0b12b0ea` + S0-F7R（`73ea8502`） |
| 关系 | 与 F7R 同族：`push` / `topple` / `nick` 已在 `weapon_masteries.py`，本条补齐第四条 |
| 状态 | 已实现（词条 + 校验 + 结算 + 动作行 + 瓦片）；三档触发策略见 GAP-51 |

## 1. 归属（先定死，避免又分叉）

Cleave **不属于** `FeatureCapability`（它不花动作槽/资源），**不属于** `WINDOW_REGISTRY`（它不改骰面），**不属于** trigger/decision 层（不需要反应经济与 offer 生命周期）。

它归 **`WEAPON_MASTERY_EFFECTS`（词条注册表）**——和 push/topple 同族；执行面复用"免费的第二次攻击"，也就是 **Horde Breaker 的 `second_target_of` 那条通道**。

> GAP-43 的 trigger/window 一侧与本片无关：那份任务书 §1 明确把 trigger 划在 Activation 之外（"保持现状…不共用类型"），修订记录也记了"并入型被否"。

## 2. 规则原文

> **Cleave.** If you hit a creature with a melee attack using a weapon that has Cleave, you can make an attack roll against a **second creature within 5 feet of the first** that is **also within your reach**. On a hit, the second creature takes the weapon's damage, but **don't add your ability modifier** to that damage unless that modifier is negative. You can make this extra attack **only once per turn**.

三条硬约束：第二目标距**第一目标** ≤5 尺、且在你**触及**内；伤害**不加属性调整**（负值仍加）；**每回合一次**。

## 3. 定点代码事实

| 事实 | 位置 |
|---|---|
| `attack_history` 只记 `{actor_id, target_id, round, turn_index}`，**不记命中与武器** | `reducer.py:112` |
| 攻击成本事件已带 `weapon_ref` | `engine.py::_attack_cost_event` |
| 攻击检定事件带 `success`，不带武器 | `attack_resolution.py:686` |
| 免费第二击的现成先例（不付动作） | `validation.py:1140` + `engine.py:303`（`second_target_of`） |
| 免属性调整的现成写法 | `attack_resolution.py:710` `modifier = min(0, modifier)` |
| 免扣费的现成写法 | 本片 F7R 的 `free` 标记 |

## 4. 契约

### 4.1 声明

```python
"cleave": {"kind": "granted_attack", "on": "hit", "target_within_ft": 5, "once_per_turn": True}
```

### 4.2 续击字段

第二击是**独立的 attack 意图**，带 `cleave_from: <第一击的目标 id>`。不带该字段就是普通攻击，行为不变。

### 4.3 校验（服务端唯一入口）

1. 武器的 mastery 是 `cleave`，且角色解锁 `weapon_mastery`；
2. `attack_history` 本回合存在一条：`actor` 自己、`target_id == cleave_from`、`hit == true`、`weapon_ref` 的 mastery 是 `cleave`；
3. 第二目标 ≠ 第一目标；
4. `distance(第二目标, 第一目标) <= 5`；
5. `distance(攻击者, 第二目标) <= 武器 range`；
6. 本回合没有用过 cleave（`granted_attacks[actor]["cleave"]`）；
7. 跳过动作门禁（与 `second_target_of` / `light_extra_attack` 同一处理）。

### 4.4 结算与记账

- `_attack_cost_event(free=True, cleave_extra=True)`：不扣动作、不扣 `attacks_remaining`，但**照记账本**；
- 伤害：`modifier = min(0, modifier)`（复用 Light 那条）；
- reducer：`attack.spent` 记下本次武器 → `check.resolved{kind: attack}` 把 `hit` 与 `weapon_ref` 写进 `attack_history`；`cleave_extra` 置 `granted_attacks[actor]["cleave"]`。

### 4.5 入口

`available_intents` 增加 `type: "cleave"` 的一行：仅当 4.3 的 1–2 成立且存在合法第二目标时出现，`weapons` 为该武器，`targets` 只放合法目标，`cleave_from` 为第一击目标，`cost: ""`。面板按 `light_attack` 的同一形状渲染瓦片。

## 5. 白名单

`combat/weapon_masteries.py`、`combat/hit_resolution.py`（如需）、`combat/validation_attack.py`、`combat/reducer.py`、`combat/engine.py`、`combat/attack_resolution.py`、`combat/availability.py`、`combat/primitives.py`（仅新增意图字段闭集）、`frontend-v2/.../Dnd2024CombatPanel.vue`、`combatCopy.ts`；测试 `test_dnd2024_weapon_mastery.py`。

## 6. 逐项测试

1. 同回合：第一击命中后，第二击合法且不扣动作/`attacks_remaining`。
2. 第二击伤害不含属性调整（负值仍含）。
3. 第二目标距第一目标 >5 尺 → 拒绝。
4. 第二目标不在触及内 → 拒绝。
5. 第一击未命中 → 拒绝。
6. 第一击用的不是 Cleave 武器 → 拒绝。
7. 每回合一次：第二次被拒。
8. 不带 `cleave_from` 的普通攻击行为不变。
9. `attack_history` 记下了 `hit` 与 `weapon_ref`（Horde Breaker 不动）。

## 6.5 执行中发现的落点问题

1. **`attack_history` 缺"命中"与"武器"**：Cleave 的前提正是这两条，而它原来只记
   "谁打了谁"。归约里 `dnd2024.attack.spent` 先记武器（`pending_attack_weapon`），
   下一条 `check.resolved{kind: attack}` 把它与 `success` 一起写进历史行。
   兼容：旧行没有这两个字段，读方按"未命中/无武器"处理。
2. **`granted_attack` 不是命中效果**：`_mastery_effect_entry` 原来假定每条词条都会
   产出条件。给 Cleave 加了分支返回空条目，命中路径据此跳过，否则命中就 KeyError。

## 7. 非目标

- 三档触发策略（随机/询问/不触发）：**不做**，见 GAP-51。Cleave 的"不触发"就是不发续击意图。
- 把 `second_target_of` 搬进声明表：另一步，本片不动 Horde Breaker。
