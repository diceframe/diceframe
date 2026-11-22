# S0-MM3a 任务书：攻击的多段伤害与附带效果

| 项 | 值 |
|---|---|
| 切片 | S0-MM3a（攻击 schema 扩展） |
| 前置 | S0-MM2（目录） |
| 基线 | 叠在 #829（S0-MM2）之上 |
| 数据依据 | `S0MM3_ATTACK_SCHEMA_2026-11-21.md`、`S0MM3_TRAITS_ACTIONS_SURVEY_2026-11-21.md` |
| 状态 | 待执行 |

## 1. 范围

把 `attacks[]` 从"一条伤害 + 一个类型"扩到能表达全量 390 条 SRD 攻击：

1. `damage` 改成数组（1–2 段）：`[{"dice": "2d6+4", "type": "slashing"}, {"dice": "1d6", "type": "fire"}]`。
2. 新增 `riders[]`：命中之后对目标做的事。首期支持四种 kind：
   - `condition`：施加条件（可带 `save`）
   - `grapple`：擒抱（带 `escape_dc`）
   - `push`：推离（带 `distance`）
   - `prone`：击倒（可带 `save`）
3. 保留旧的单段字段 `damage` + `damage_type` 作为**兼容输入**，装配期归一到 `damage[]`。

## 2. 为什么

`damage_type` 单值表达不了 30 条两段伤害；而 **约 240 处附带效果**（Prone 59 / Grappled 58 / Restrained 37 / Poisoned 24 …）今天在契约里完全没有位置——不补这一层，MM3 之后的每个怪物都只能"打出纯伤害"，等于把玩法砍掉一半。

## 3. 契约

- `damage`：数组，长度 1–2；每项 `dice` 匹配 `NdM[+K]` 或固定值 `N`；`type` 属伤害类型闭集。
- `riders`：数组，长度 0–3；`kind` 闭集；`condition` 必须在条件目录内；`escape_dc` / `save.dc` 为 1–30；`push.distance` 为 5–60。
- 归一：给了 `damage` 数组就用数组；只给旧字段则合成单元素数组。**两者都缺** → 拒绝。
- 结算：多段伤害**各自掷、各自过抗性/免疫/易伤**（RAW：同一攻击的不同伤害类型分别结算）。
- `riders` 在**命中确认后**按声明顺序应用；条件施加仍走 `condition.applied`，因此条件免疫照常拦截（MM1 的 `condition.blocked`）。

## 4. 测试

1. 单段攻击照旧（回归）。
2. 两段伤害：火/斩分别过抗性，只有对应那段被减半。
3. `riders` 顺序：先击倒后擒抱，事件顺序与声明一致。
4. 条件免疫拦截：目标免疫 Prone 时出现 `condition.blocked`，其余 rider 不受影响。
5. 旧输入兼容：只有 `damage` + `damage_type` 的敌人开战不报错。
6. `damage` 与旧字段都缺 → 拒绝。
7. 非法 `kind` / 越界 `escape_dc` / 非法伤害类型 → 拒绝。
8. 目录全量重生成后 135 条仍可加载（回归）。

## 5. 不做

- Multiattack（MM3b）
- 存档型动作（MM3c）
- 特质（MM3d）
- `riders` 里"目标被吞下"这类复合状态（8 条），登记缺口
