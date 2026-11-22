# S0-MM3c 任务书：存档型动作

| 项 | 值 |
|---|---|
| 切片 | S0-MM3c（存档型动作） |
| 前置 | S0-MM3a |
| 基线 | 叠在 #830（S0-F25 地基）之上 |
| 数据依据 | `S0MM3_ATTACK_SCHEMA_2026-11-21.md`、`S0MM3_TRAITS_ACTIONS_SURVEY_2026-11-21.md` |
| 状态 | 待执行 |

## 1. 范围

187 条动作不是攻击，而是"目标做一次豁免，失败吃效果"。它们需要一个新的动作 kind：

```jsonc
{"kind": "save_action",
 "ability": "dex", "dc": 15,
 "on_fail": [{"damage": [{"dice":"8d6","type":"fire"}]}],
 "on_success": [{"damage": [{"dice":"4d6","type":"fire"}]}]}   // 半伤
```

首期支持：**单一豁免 + 伤害（含半伤）**。不含：多目标区域、失败后附加条件、连续回合效果。

## 2. 契约

- `ability` 属六维；`dc` 1–30。
- `on_fail` / `on_success` 只允许 `damage` 事件；命中判定的**掷骰由服务端做**，客户端只提交动作 id。
- 半伤在 `on_success.damage` 里按骰式子集表达，**不做"自动减半"**——SRD 写的是明确的另一个骰池（8d6 → 4d6），减半会算错取整。
- `usage_limits`（113 条动作带）首期只做 `recharge` 与 `per_day` 两种。

## 3. 测试

1. 失败：全额伤害。
2. 成功：半伤骰池（不是把失败值除以 2）。
3. DC 边界与非法 ability。
4. `recharge` 掷骰决定可用性；不可用时不消耗动作。
5. `per_day` 次数耗尽后拒绝。

## 4. 不做

区域多目标、失败后附加条件、`Spellcasting` 型动作。
