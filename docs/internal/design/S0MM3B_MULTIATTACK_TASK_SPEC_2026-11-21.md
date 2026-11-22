# S0-MM3b 任务书：Multiattack 的三种原语

| 项 | 值 |
|---|---|
| 切片 | S0-MM3b（Multiattack） |
| 前置 | S0-MM3a |
| 基线 | 叠在 #830（S0-F25 地基）之上 |
| 数据依据 | `S0MM3_ATTACK_SCHEMA_2026-11-21.md`、`S0MM3_TRAITS_ACTIONS_SURVEY_2026-11-21.md` |
| 状态 | 待执行 |

## 1. 范围

178 条 Multiattack 动作，按形状分成三种原语：

| 原语 | 形状 | 数量 |
|---|---|---|
| `repeat` | 做 N 次同一个攻击 | 54 |
| `combo` | A 若干次 + B 若干次 | 37 |
| `choice` | A+B，**或** C 若干次 | 35 |

其余 52 条（含 `Spellcasting` 替换、`any combination`）首期**不实现**，登记为缺口。

## 2. 契约

```jsonc
{"kind": "multiattack", "repeat": {"attack_id": "rend-0", "count": 3}}
{"kind": "multiattack", "combo": [{"attack_id": "claw-0", "count": 1},
                                  {"attack_id": "tail-0", "count": 1}]}
{"kind": "multiattack", "choice": [
   {"label": "claws+tail", "parts": [{"attack_id":"claw-0","count":1},{"attack_id":"tail-0","count":1}]},
   {"label":"hurl-flame", "parts": [{"attack_id":"flame-0","count":2}]}]}
```

- `attack_id` 必须**指向该生物自己的攻击**（别名匹配限定在生物内，禁全局匹配）。
- 引用解析失败 → 整条 Multiattack 不可用，而不是静默降级成单次攻击。
- 结算：Multiattack 是一个意图，展开成 N 个攻击结算，共享同一批次；任一击被拒绝则整批拒绝（与 `class_capability` 的原子性一致）。

## 3. 测试

1. `repeat` 三击：一个意图产出三个攻击事件、三次伤害。
2. `combo` 两击不同攻击：各自用各自的命中与伤害。
3. `choice` 只选一支：未选分支不产生事件。
4. `attack_id` 不存在 → 意图被拒。
5. 别名：`Rend` 指向该生物的 `Claw` 时按生物内别名解析。
6. 原子性：第二击因资源/目标问题被拒 → 第一击也不落账。

## 4. 不做

`Spellcasting` 替换（22 条）、`any combination`（30 条）→ 缺口。
