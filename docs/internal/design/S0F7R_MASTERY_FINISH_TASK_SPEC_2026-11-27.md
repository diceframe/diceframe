# 任务书：S0-F7R 武器词条收尾（Push / Topple / Nick）

| 项 | 值 |
|---|---|
| 切片 | S0-F7R（F7 的剩余项；F7 原文在 `docs/dnd2024-s0-specs` 的 `S0F7_*`，本片不重写它） |
| 验收载体 | 武器词条 `push` / `topple` / `nick`，以及 Light 追加攻击的**入口** |
| 规则基准 | SRD 5.2.1（p90 武器词条、p89 Light 特性）；`SRD_CC_v5.2.1.pdf` |
| 代码基线 | `0b12b0ea`（含 #888 的 combat 模块拆分） |
| 前置 | F38（在手状态与双持）、#875（Light 追加攻击按 enabler 判定）、#828 |
| 不含 | **Cleave**（另起一片：要新增攻击意图字段与第二目标校验） |
| 状态 | Push / Topple / Nick（含动作行与瓦片）已实现；Cleave 仍为占位（见 §7） |

## 1. 为什么现在做

`combat/weapon_masteries.py` 目前只实现 `graze / sap / vex / slow`，另外四条是显式占位：

```python
"cleave": {"kind": "unimplemented"},
"nick":   {"kind": "unimplemented"},
"push":   {"kind": "unimplemented"},
"topple": {"kind": "unimplemented"},
```

`hit_resolution._weapon_mastery_effect` 对 `unimplemented` **fail closed 返回 None**（不发事件、不报错），于是战斧的 Topple、重弩的 Push 在实战里静默无效。`test_dnd2024_weapon_mastery.py::test_unimplemented_properties_fail_closed` 把这一行为钉着——本片要把它缩到只剩 `cleave`。

## 2. 规则原文

> **Push.** If you hit a creature with this weapon, you can push the creature up to 10 feet straight away from yourself if it is **Large or smaller**.

> **Topple.** If you hit a creature with this weapon, you can force the creature to make a **Constitution** saving throw (DC **8 + the ability modifier used to make the attack roll + your Proficiency Bonus**). On a failed save, the creature has the **Prone** condition.

> **Nick.** When you make the extra attack of the Light property, you can make it as part of the **Attack action** instead of as a Bonus Action. You can use this extra attack only **once per turn**.

> **Light.** … you can make one extra attack as a **Bonus Action** later on the same turn with a **different Light weapon**. You don't add your ability modifier to the extra attack's damage unless that modifier is negative.

## 3. 定点代码事实（基线已核实）

| 事实 | 位置 |
|---|---|
| 词条只在命中后施加条件这一种形状 | `hit_resolution.py` → `damage_event["mastery_effects"] = [{condition, duration, source_actor_id}]` |
| 推挤事件是**有符号标量**，方向由发射点各自算 | `attack_riders.py:350`、`hit_resolution.py:962`、`martial_actions.py:618`、`spell_resolution.py:927`、`:1366`（5 处） |
| reducer 只做标量加法 | `reducer.py:896` `positions[actor] += distance` |
| Light 追加攻击的四条门槛 | `validation_attack._validate_light_extra_attack`：本回合有 Light 攻击 / **另一把** Light 武器 / 自己回合 / `bonus_action >= 1` |
| 附赠动作在成本事件里支付 | `attack_resolution.py:635` `resource="bonus_action" if LIGHT_EXTRA_ATTACK else ""` |
| 账本按回合计，可扩字段 | `reducer.py:797` `combat.light_attacks[actor] = {round, turn_index, weapon_refs}` |
| 面板**发不出** `light_extra_attack` | `Dnd2024CombatPanel.vue` 零命中；规则服务端有、UI 够不到 |
| 面板看不到剩余攻击次数 | `attacks_remaining` 只在 `availability.py` 内部使用，未投影 |

## 4. 契约

### 4.1 推挤：方向归 reducer，事件带 `away_from`

发射点不再算符号，改发**无符号距离 + 相对谁**：

```python
{
  "type": "dnd2024.position.changed",
  "actor_id": target_id,
  "feet": 10,
  "away_from": attacker_id,
  "forced": True,
  "source_rider": "push" | "repelling_blast" | ...,
}
```

- reducer 优先读 `feet` + `away_from`：1D 下 `sign(pos[target] − pos[away_from]) × feet`；2D 落地时只改这一个函数（单位向量 × feet，逐格推进到第一个阻挡物，事件补 `moved_ft`）。
- **过渡期双写**：同一事件继续带 `distance`（有符号），既有用例与旧事件重放不受影响。`distance` 标为 deprecated，2D 实现时删除。
- 五人以上同格时 `sign(0)` 取 `+1`——显式规则，不再藏在比较符里。

### 4.2 Topple：`mastery_effects` 支持"先豁免后条件"

条目扩为：

```python
{"kind": "save_condition", "ability": "con", "dc": <int>,
 "condition": "prone", "duration": "until_stand", "source_actor_id": actor_id}
```

- DC 由命中时已知的攻击加值算出：`8 + proficiency_bonus + 攻击属性调整`，写在条目里，结算时不再反推。
- 条件**仍然挂在 `damage_event` 上等提交**：护盾术把命中改判落空时，豁免与条件都不发生。
- 豁免成功不发 `condition.applied`（与 `_apply_riders` 的约定一致：`check.resolved` 已经把掷骰留在日志里）。

### 4.3 Push 词条：10 尺、Large 或更小

`WEAPON_MASTERY_EFFECTS["push"] = {"kind": "push", "on": "hit", "feet": 10, "size_at_most": "large"}`。目标体型按 `size_at_most` 判定，未知体型 fail closed（不推）。

### 4.4 Nick：不花附赠动作，每回合一次

- 注册表：`"nick": {"kind": "light_extra_as_action"}`。
- `_validate_light_extra_attack`：当**追加攻击所用的武器**带 `nick` 且角色解锁 `weapon_mastery` 时，跳过 `bonus_action >= 1` 这条，改为要求账本里 `nick_used` 为假；"另一把 Light 武器"这条**不变**（Nick 不放松它）。
- `attack_resolution`：同一情形传 `resource=""`，不支付附赠动作。
- reducer：命中账本写入时，若该次攻击带 `light_extra_attack` 且 enabler 是 Nick，置 `light_attacks[actor]["nick_used"] = True`；记录本就按 (round, turn_index) 重建，所以"每回合一次"随回合自动复位。
- 账本字段变化不写存档：`light_attacks` 属于战斗态，不是角色卡。

### 4.5 入口：动作清单里的一行

`available_intents` 增加：

```python
{
  "type": "light_attack",            # 独立 type：面板用 find-by-type，同 type 会互相遮蔽
  "label": "Light 追加攻击" / "Light extra attack",
  "actor_id": ..., "expected_version": ...,
  "weapons": [<除参照武器外的 Light 武器>],
  "targets": <hostile targets>,
  "cost": "bonus_action" | "",       # Nick 时为 ""
  "available": <bool>,
  "unavailable_reason": <str>,
}
```

面板：在「附赠动作」页（Nick 时在「动作」页）渲染一张瓦片，点开复用攻击的 detail 区（选武器、选目标），确认时提交 `attack` 意图并带 `light_extra_attack: true`。

同时把 `attacks_remaining` 投影进视图，让玩家看得见"本动作还剩几次攻击"。

### 4.6 执行中发现的缺陷（本片一并修掉）

1. **Light 追加攻击在真实回合里不可达**。动作门禁要求 `action > 0` 或
   `attacks_remaining ≥ 1`，而追加攻击两者都不占，于是普通攻击把动作花掉之后它必被拒。
   既有用例之所以通过，是因为它们在中途调用了一个会**重置经济**的测试辅助
   （`_turn` → `_fresh_economy`），从来没测到同一回合的真实序列。现在门禁对
   `light_extra_attack` 放行，成本由附赠动作或 Nick 的"每回合一次"单独结账。
2. **`resource=""` 不等于"免费"**。成本事件里空串的含义是"让引擎决定扣哪个"，
   直接传空串会退化成扣 action。Nick 改用显式 `free` 标记，归约据此只跳过扣费、
   仍然记 `light_weapons` 账本（否则"本回合用过哪把"和 `nick_used` 都丢了）。
3. **`_turn` 式的夹具不能证明准入**。新增的 Nick 用例全部在同一回合内连续两击。

## 5. 白名单

`combat/weapon_masteries.py`、`combat/hit_resolution.py`、`combat/attack_riders.py`、`combat/martial_actions.py`、`combat/spell_resolution.py`、`combat/reducer.py`、`combat/validation_attack.py`、`combat/attack_resolution.py`、`combat/availability.py`、`combat/view.py`、`frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue`、`frontend-v2/src/api/types.ts`；测试 `tests/rulesets/test_dnd2024_weapon_mastery.py`、`test_dnd2024_light_extra_attack.py`（新建）。

需要改其它文件 → 停止。

## 6. 逐项测试

1. Push：命中后推 10 尺、方向远离攻击者；目标 Huge 不推；未命中不推；护盾术改判落空后不推。
2. Push 事件同时带 `feet`/`away_from`/`distance`，reducer 结果与只读 `distance` 一致。
3. Topple：Con 豁免 DC = 8+PB+攻击属性；失败施加 Prone；成功不施加；未命中不掷骰。
4. Topple：DC 随等级（PB）与属性变化，不写死。
5. Nick：命中后追加攻击不消耗附赠动作，附赠动作仍可用于别的事。
6. Nick：每回合只能一次，第二次被拒。
7. Nick：没有 Nick 的武器仍按附赠动作支付；`light_extra_attack` 缺省时行为不变。
8. Nick 不放松"另一把 Light 武器"：同一把武器追加被拒。
9. 动作清单：有 Light 攻击账本时出现 `light_attack` 行，`weapons` 不含参照武器；Nick 时 `cost` 为空。
10. 面板：瓦片出现、提交带 `light_extra_attack: true`、`attacks_remaining` 可见。
11. 回归：`test_unimplemented_properties_fail_closed` 参数缩到只剩 `cleave`；既有 push/topple 用例由"什么都不发生"改为断言真实效果。

## 7. 非目标与停止条件

非目标：Cleave；2D 地图（本片只把方向下沉到 reducer）；Tactical Master 的替换入口（见 GAP 登记，内容有、代码无）。

停止条件：需要为 Nick 另建一套动作经济；需要把 `light_attacks` 写进存档；需要在面板里推断职业/等级来决定成本。

## 8. 验收

```
pytest tests/rulesets/test_dnd2024_weapon_mastery.py tests/rulesets/test_dnd2024_light_extra_attack.py -q
pytest tests/rulesets tests/architecture -n 4 --dist loadfile -q
ruff check src/ tests/ && python -m mypy
npx vitest run tests/Dnd2024CombatPanel.test.ts
```
