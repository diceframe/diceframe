# W1 任务书：Light 追加攻击按"触发它的那件武器"判定

| 项 | 值 |
|---|---|
| 编号 | **W1**（武器域修订；改写 S0-G2 §4.3 与 S0-F38 §9，不新增切片编号） |
| 依据 | `S0F8_SCOPE_AUDIT_2026-11-26.md` §3.2、§4 R-1；S0-G2 §2.3 / §4.3 |
| 规则基准 | SRD 5.2.1 p90 Light / SAC："a **different** Light weapon" |
| 代码基线 | `origin/main@b6fb7c82` |
| 状态 | **已交付** `9dedd7cd` |

## 1. 缺陷（已核实到代码）

F38（#785）把 Light 追加攻击的准入写成"另一只手里握着另一件 Light 武器"：

- `validation.py::_validate_light_extra_attack` 用 `grip["left"/"right"]` 找 `other`；
- `reducer.py` 的 `combat.light_attacks[actor]` 只记 `{round, turn_index}`，不记武器。

后果是可复现的规则错：5 级 Fighter 持 shortsword + dagger，两击都用 shortsword 后，仍可用 **shortsword** 做追加（另一手有 dagger 即满足），而规则要求追加必须用与触发它的那次不同的 Light 武器。1 级同理。

## 2. 契约

1. `dnd2024.attack.spent` 事件新增 `weapon_ref`（规范形 `item:<id>`，徒手/无档案为空串）。
2. `combat.light_attacks[actor_id] = {round, turn_index, weapon_refs: [...]}`：只在本回合有 Light 武器攻击时存在；`round`/`turn_index` 不匹配即重建；`weapon_refs` 按发生顺序追加、**保留重复项**（同一武器打两次仍只有一个参照点）。
3. 准入（`_validate_light_extra_attack`）：
   - 本次武器必须是 Light；
   - 本回合必须有记录（`round` + `turn_index` 同时相等）；
   - 存在 `used ∈ weapon_refs`，满足 `used ≠ 本次武器` 且 `used` 在武器目录里 `light == true`；
   - Bonus Action 与"仅自己回合"不变。
4. **手位不是条件**：不再读 `hands`；装备身份由 `_declared_attack` 保证。
5. 拒绝串：`the extra attack needs a different Light weapon than the one that enabled it`（原来那句 "the other hand must hold a different Light weapon" 作废）。

## 3. 白名单

`combat/engine.py`（事件载荷）、`combat/reducer.py`（账本）、`combat/validation.py`（准入）、`tests/rulesets/test_dnd2024_weapon_hands.py`。

## 4. 矩阵与用例

| # | 场景 | 用例 |
|---|---|---|
| 1 | 本次武器不是 Light → 拒 | `test_light_extra_attack_needs_a_light_weapon` |
| 2 | 本回合没有 Light 攻击 → 拒 | `test_light_extra_attack_requires_a_prior_light_attack` |
| 3 | 同一把武器给自己做参照 → 拒（哪怕另一手持不同 Light 武器） | `test_the_extra_attack_cannot_use_its_own_enabler`（旧码失败） |
| 4 | shortsword → dagger 合法 | `test_a_light_weapon_is_a_valid_reference_for_the_other_one` |
| 5 | 支付事件带 `weapon_ref`，账本记 `weapon_refs` | 同上 |
| 6 | 账本按 `round` 重建，上一轮不生效 | `test_a_previous_turns_light_attack_does_not_enable_this_one` |
| 7 | TWF 加值仍按风格生效 | `test_light_extra_attack_damage_follows_the_fighting_style` |
| 8 | 战斗结束清账本 | `test_combat_end_clears_the_grip`（既有） |

## 5. 非目标

- 不放宽 Nick / Dual Wielder（归 F7 与 S0-G4）；不改 `light_extra_attack` 意图布尔本身。
- 不改 TWF 参数名（`light_extra_attack_ability_damage` 已有消费点与用例）。
- 不给法术攻击加这条准入（Light 是武器词条）。

## 6. 停止条件

1. 需要客户端申报"哪次攻击触发了追加"才能成立 → 停（判定必须由事件账本回答）。
2. 需要第二套回合账本 → 停（`light_attacks` 与 `feature_usage` 同模式）。
3. 需要改 `attacks_remaining` / `attacks_per_action` → 停。

## 7. 验收

```bash
python -m pytest tests/rulesets/test_dnd2024_weapon_hands.py -q
python -m pytest tests/rulesets/test_dnd2024_fighting_style.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_extra_attack.py -q
python -m ruff check src/rulesets/dnd2024/combat tests/rulesets/test_dnd2024_weapon_hands.py
python -m mypy
```
