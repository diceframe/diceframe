# S0-F26 执行报告：Circle of the Land 主动能力

| 项 | 值 |
|---|---|
| 任务书 | `S0F26_LAND_ACTIVE_TASK_SPEC_2026-11-07.md` + 修订稿 `S0F26_SPEC_REVISION_2026-11-23.md` |
| 状态 | 工作树，未提交、未开 PR（按要求） |
| 验证 | 全量 `tests/rulesets` **2722 passed**（11:48）；`ruff` / `mypy` 干净；`test_dnd2024_land_active.py` **9 passed** |

## 1. 交付

| 切片 | 内容 |
|---|---|
| Z1 掩护 | 区域可提供 Half Cover：`_zone_cover_level()` 直接返回完整 `cover_row`（与 GM 声明同一条路径）；`beneficiaries=allies` 时敌人拿不到 |
| Z2 区域结构 | `zone.created` 接收 `shape/size_ft/owner_id/beneficiaries/half_cover/resistance_from_owner/expires_at_minute/ends_on_owner_incapacitated/feature_id/projection/ward_type/rounds_left`；`rounds_left=0` 表示**按世界时钟**裁剪，不再被回合数提前删掉；新增 `zone.moved` 与 `zone.removed` |
| Z3 求值 | `obscurement.zone_members()`：sphere 用半径、cube 用边长/2，按 positions 稳定排序，接收受益者谓词 |
| Z4 Land's Aid | 意图 `land_aid`：action + 1 次 wild_shape；目标必须在 10 尺球内（**越界拒绝而不是静默过滤**）；每目标独立 CON 豁免；**一次施放共用一份 2d6/3d6/4d6**；半伤向下取整；治疗独立掷骰，`cannot_regain_hp` 时归零并带 `blocked_by` |
| Z5 Ward / Sanctuary | `ward_resistance_type` 投影（自己按 land；盟友在 `resistance_from_owner` 区域内按区域记录的 `ward_type` 借用）；`_resist_damage` 里只有一次减半 |
| 到期 | `_zone_expiry_events()` 挂在每条意图的公共收尾：`expires_at_minute` 到期或持有者失能/死亡 → `zone.removed` |
| 数据 | `srd_class_features.json` 新增 `lands_aid`(3) / `natures_ward`(10) / `natures_sanctuary`(14)，owner 均为 `subclass:circle_of_the_land` |

## 2. 明确取舍

1. **一维投影**：成员 = `|position − origin| ≤ 尺寸`，区域带 `projection: "theater_1d"`。未用"与施法者距离"冒充半径。
2. **cube 用边长/2**：一维下退化为区间，与"在区域内"语义一致，但与 2D 角落覆盖不同，已在修订稿写明。
3. **区域记录 `ward_type`**：一分钟内不可能长休换 land，因此建立时快照即可；避免了"区域求值还要回读持有者角色"的环形依赖。
4. **`land_terrain` 与 `ward_resistance_type` 走视图**：`_feature_character(view)` 是代理对象，不含 `spellcasting`；`_player_view` 又没有 `combat`，所以 ward 分享只能在 `_actor_view` 里补。

## 3. 评审后修掉的（第一轮独立评审）

| # | 问题 | 后果 |
|---|---|---|
| 1 | `_nature_sanctuary_events` 用了未定义的 `character` | 放 Cube 时 `NameError`，所有 Sanctuary 路径必炸 |
| 2 | `zone_covers` 未在 view 导入 | 区域掩护/分享一访问就崩 |
| 3 | 区域掩护塞进 `best` 后走光环覆盖并集，缺 `_rank` | `_cover_record` KeyError |
| 4 | `zone.moved` 无校验 | 可传任意坐标瞬移 |
| 5 | `zone.removed` 无处理、到期无消费者 | Sanctuary 永不消失 |
| 6 | `zone.created` 丢 `ward_type` | 盟友永远借不到抗性 |
| 7 | 治疗不查 `cannot_regain_hp` | 该被禁止的治疗照常回血 |
| 8 | 区域目标靠静默过滤 | 客户端可选区域外目标，事件仍显示成功 |
| 9 | 庇护所默认 `rounds_left=10` | 按回合计时，10 回合后被回合裁剪而非时钟 |
| 10 | 测试有安慰剂项 | 敌人掩护恒为 none、zone_members 谓词被放松、到期用例不验证移除 |

## 4. 本轮补掉的两项

1. **Ward 的 Poisoned 免疫**：`natures_ward` 声明 `parameters.immune_conditions = ["poisoned"]`，并新增 `_feature_immunity_events()` 在 `apply_batch` 统一拦截 —— 免疫是**目标属性**，法术/rider/地形/GM 声明任何生产者都得到同一答案。
   - 顺带修掉一个既有缺陷：`condition.blocked` 事件从未被 reducer 接受，任何免疫拦截到达落账时都会抛 `unsupported event type`（等于敌人 stat block 免疫也走不通）。
2. **区域存读往返**：`combat.zones` 经 JSON 序列化→`replace_state` 后仍在，Cube 的 `shape/half_cover/ward_type` 与「区域掩护 = half」都被用例钉住。

## 4b. 未完成（明确记录）

2. **探索模式**：区域只存在于 combat 状态，`exploration/engine.py` 不认；Sanctuary 在战斗外无法维持。
3. **视图与 feed**：zones 未投影到 actor view（UI 看不到 Cube 范围）；`_recent_combat_events` 白名单未核。
4. **存读往返**：`combat.zones` 是否随存档往返未见用例。
5. **`damage_targets` 为空但区域内有人**时按"必须选择目标"拒绝——这是本轮加的约束，需与前端对齐（目前没有 UI）。
6. **区域掩护不区分方向**：一维模型下"在区域内"即得 Half Cover，与 SRD 的"区域提供掩护"一致，但无法表达"从某侧射击"。

## 5. 下一步

按修订稿 Z6：Poisoned 免疫、探索期区域、视图/feed 投影、存读往返，各配一条真实路径用例。
