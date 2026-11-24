# S0-F26 任务书修订：对齐已有的掩蔽与区域基建

| 项 | 值 |
|---|---|
| 原任务书 | `S0F26_LAND_ACTIVE_TASK_SPEC_2026-11-07.md`（`docs/dnd2024-s0-specs` 分支） |
| 修订原因 | 原文写于 `zones` / `cover` / F25 支付接口落地之前，前置判断已过期 |
| 状态 | 生效（本文件覆盖原文对应条目，其余仍按原文） |

## 1. 前置已变：三项原判「缺失」的东西已经存在

| 原文判断 | 现状 |
|---|---|
| 「没有权威 Sphere/Cube」 | `combat.zones[zone_id] = {kind, level, origin, radius_ft, magical, spell_ref, caster_id, concentration, rounds_left}`；`obscurement.py::zone_covers` 已是 `|origin − position| ≤ radius_ft`，正被迷雾/遮蔽使用 |
| 「Half Cover 不能统一进入 AC/Dex save」 | `cover.py`：`COVER_LEVELS` + `cover_row()` 同时给出 `armor_class_bonus` 与 `dex_save_bonus`；AC 已消费（`resolution.py:1479`） |
| 「Wild Shape 支付接口缺失」 | F25 已交付：`resources.class.wild_shape` 有真实 current/maximum，短休 +1、长休清满 |
| 「当前 land 缺失」 | `resting/engine.py::_plan_land_terrain` 已有，`srd_recovery` 的 `land_terrain` 选项在跑 |

**仍然缺的**（本轮要补）：区域到**掩护**的归属（zone 现在只贡献遮蔽）、**Dex 豁免**对掩护的消费、区域形状/尺寸、受益者谓词、到期语义、移动、探索与视图投影。

## 2. 一维投影政策（新增，必须遵守）

原任务书 §7.2 禁止「用与施法者的距离冒充 Cube/Sphere」。这条**继续有效**，但它禁止的是「拿 caster 距离当半径判定」，不是禁止一维本身。

本切片采用：

1. 区域由**服务端声明的原点** `origin`（一维坐标）+ 尺寸定义；成员判定为 `|position − origin| ≤ 半径`（球）或 `≤ 边长/2`（立方，退化投影）。
2. 原点只能落在既有坐标线上；客户端提交的点若不在线上，**取最近点并记录 `projected=True`**，不静默丢弃也不伪造二维。
3. 一维不表达：垂直高度、绕墙、区域可视化形状。任何依赖这些的规则（遮挡、视线）**不在一维里假装成立**。
4. 事件与视图必须带 `projection: "theater_1d"`，让消费方知道这是投影而不是几何真值。

## 3. 区域数据的扩充（覆盖原文 §4.4.1）

```
zone = {
  area_id, shape: "sphere"|"cube", origin, size_ft,     # sphere 用半径，cube 用半边
  owner_id, beneficiaries: "all"|"allies",              # Sanctuary 只给盟友
  half_cover: bool, resistance_from_owner: bool,        # 该区域提供什么
  expires_at_minute: int|null, ends_on_owner_incapacitated: bool,
  feature_id, projection: "theater_1d",
}
```

不新增区域所有者模块：继续住在 `combat.zones`，与遮蔽共用同一本账。探索期区域留到 Z6，先只做战斗内，理由是探索没有位置模型。

## 4. 拆分与验收顺序（替代原文 §6 的矩阵编号）

```
Z1  掩护查询：effective_cover() = GM 声明 ∪ 区域来源，取最高；Dex 豁免接入
Z2  区域结构：shape/size/beneficiaries/expiry + zone.moved 事件
Z3  区域求值：zone_covers 按 shape；zone_members(predicate)
Z4  Land's Aid：区域枚举 + 一次伤害骰 + 每目标独立 CON 豁免 + 一个治疗目标
Z5  Nature's Ward / Sanctuary：抗性接 _resist_damage；Cube 复用 Z1/Z2
Z6  外围：view 投影、feed 白名单、存读往返、探索降级说明
```

**每个 Z 必须自带用例**；Z4 的「一次施放共用一份伤害骰、每目标独立豁免」与 Z5 的「离开 Cube 立即失去抗性与掩蔽」是硬验收。

## 5. 停止条件（沿用原文 + 一维新增）

1. 需要二维几何才能表达的规则（视线、绕墙、垂直）→ 停止，不近似。
2. 区域抗性需要写入盟友角色永久状态 → 停止（必须按位置求值）。
3. 一维投影无法表达目标点 → 停止（例如 GM 想放在线外且拒绝取最近点）。
4. 需要新增第二个区域所有者（与 `combat.zones` 并行）→ 停止。
