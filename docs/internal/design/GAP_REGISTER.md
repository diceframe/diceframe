# 缺口登记册（GAP-n）

> 本文件记录**调研与实现过程中发现的、当前切片不打算修的**真实缺口。
> 每条必须带：可核对的证据位置、它挡住什么、归到哪一片。
> 新发现的缺口先登记，再决定是否立项——**不许只在对话里说过就算**。

更新规则：

1. 一条缺口只写一次；被修复后状态改为 `已修` 并注明 PR/提交，不删行。
2. 「归属」写具体的片名或阶段；写不出来的，说明它还没有人接。
3. 证据要能 grep 到。凭印象的条目一律不收录。

| # | 缺口 | 证据 | 挡住什么 | 归属 | 状态 |
|---|---|---|---|---|---|
| GAP-1 | 无视线模型（line of sight / 遮蔽探测） | `grep -rn 'line_of_sight\|obscured' src/rulesets/dnd2024/` 无命中 | Hide 的第三前提（"不在任何敌人视线内"）、战前潜行、F11 的远程重定向 | 阶段三（2D 地图 / 迷雾） | 未开工 |
| GAP-2 | 技能检定系统是占位符 | `combat/resolution.py::_hide_events` 用 `DC = 10 + 存活敌人最高感知**豁免**` 冒充被动察觉 | Hide、开锁、拆陷阱、扒窃、所有将来的技能检定 | 独立片「检定工具」 | 未开工 |
| GAP-3 | 战斗内无通用技能检定原语 | `combat/primitives.py::INTENT_TYPES` 无 `ability_check`；`check.resolved/ability_check` 只在 dispel 等处内联 | 任何"花动作掷技能"的新动作都要重抄一遍掷骰段 | 并入 GAP-4 任务书（`_skill_check_events`） | 已立项 |
| GAP-4 | 无 GM 发起检定的入口 | `webui/services/game_controls.py::check_request_for_action` 只接受玩家 `text`；DC 取 `game_config.difficulty` 全局值 | "DM：这里有一把锁，DC 15" 这个最自然的裁定动作无法表达 | 草案 `GAP4_GM_CHECK_REQUEST_TASK_SPEC_2026-11-20.md`（**尚未提交**，只在本地工作区） | 未开工 |
| GAP-5 | 无 Surprised / 遭遇前状态 | `grep -rn 'surprised' src/` 无命中；`combat.start` 不接受"谁先动手" | 战前潜行的产物（突袭）无消费者 | S0-F10C 的前置 | 未开工 |
| GAP-6 | Utilize（操作）动作未实现（**数据已存在，缺意图**） | `INTENT_TYPES` 无 `utilize`；数据齐：`equipment_catalogs/srd_tools.json` 35 个工具带 `utilize: [{task, dc, ability}]`（含 `thieves_tools` 的 Pick a lock / Disarm a trap，DC 15、ability dex）；`srd_adventuring_gear.json` 另有 Ball Bearings / Bell / Caltrops / Chain 的操作原文与 DC | Fast Hands ②、所有"操纵物件"的行为 | S0-F10E | 未开工（**不需要对象模型**，见 §附注 A） |
| GAP-7 | 物品激活只能治疗 | `items/activation.py::_EFFECT_KEYS = {"mode", "healing"}`；`ACTIVATION_COSTS = {"action", "bonus_action"}` | 魔法物品无一可用样本（135 条物品中激活声明 0 条） | S0-F10D | 未开工 |
| GAP-8 | 标准动作→附赠动作的转换能力缺失 | 能力声明只支持固定成本（`capabilities[].cost`），无"把某个动作改按附赠动作支付"的通用形状 | Fast Hands（把操作/使用魔法物品从标准动作改附赠动作） | S0-F10D | 未开工 |
| GAP-9 | 轻度遮蔽没有消费者 | 战斗引擎无感知检定（GAP-3 的直接后果）；`lightly` 只有事实层与投影 | "微光/薄雾里感知有劣势"这条规则无处生效 | 随 GAP-2/GAP-3 | 未开工 |
| GAP-10 | 猜位置攻击未建模 | `effective_obscurement` 只给"看不见"，不产生"目标位置未知" | 看不见目标时"猜错即失手"这半条规则 | 目标可知性（未立项） | 未开工 |
| GAP-11 | Cutting Words 伤害面与受击反应是两条路径 | `check_windows.py::_damage_window_offer` 与 `reaction_spells.py::REACTION_FEATURE_EFFECTS` 并存 | 每加一个"花反应改伤害"的特性都要再分叉一次 | 独立重构片 | 已尝试、已回退 |
| GAP-12 | 玩家侧感官无统一投影 | 只有特性声明 `blindsight_range_ft`；`_actor_view` 不带 `senses` | 玩家角色的 darkvision / truesight 在遮蔽判定里读不到 | S0-C2D（未写） | 未开工 |
| GAP-13 | 可用性门槛字段只有 hide 一个消费者 | `engine.py::available_intents` 的 `unavailable_reason` 只给 hide | 第二个"有环境门槛"的动作出现时需抽象 | S0-F10A 之后 | 未开工 |
| GAP-14 | 无"本回合是否移动过"的观察方法 | 标记本身**已存在**：`reducer.py::position.changed` 在行动者花掉移动时写 `economy.moved = True`（每回合 economy 重建）；缺的只是读取方法 | Steady Aim 的第一前提（"you haven't moved during this turn"）无法权威判定 | S0-F10A（片内加 `has_moved_this_turn()`） | 已定：只加读方法，不加状态 |
| GAP-15 | 战前潜行（遭遇前）无任何入口 | `src/rulesets/dnd2024/exploration/engine.py` 无技能检定、无 stealth；`combat.start` 不接受"谁先动手/谁在潜行" | 网团最常见的"确认对方没发现你 → 掷隐匿 → 突袭"整条实践无法表达 | S0-F10C（前置仍是 GAP-5） | 未开工 |
| GAP-16 | 无 GM 裁定原语（施加/移除条件） | `INTENT_TYPES` 无任何条件写入意图；`gm_command` 只解析资源修正与复活（`_parse_gm_resource_change` / `_parse_gm_revive_command`）；条件只由法术/特性路径产生 | 用铁蒺藜铺地、锁链束缚、DM 临时裁定中毒/目盲等**全部无法落地** | 草案 `GAP16_GM_CONDITION_TOOL_TASK_SPEC_2026-11-20.md`（**尚未提交**，只在本地工作区） | 未开工 |
| GAP-17 | 无"发声"事件，Hide 的四类终止之一没有产生者 | `_hide_events` 依赖 `source="hide"` 的 invisible；终止路径里"高于耳语的声音"没有任何事件能触发它（铃铛、喊叫都无处表达） | 铃铛等 Utilize 的叙事后果无法影响隐藏 | 同 GAP-16 | 未开工 |
| GAP-18 | 区域只有遮蔽等级，没有进入触发/困难地形 | `combat.zones` 字段仅 `{level, origin, radius_ft, magical, ...}`；无 `on_enter` / `difficult_terrain` / `speed_zero` | 铁蒺藜、滚珠、油腻术这类"铺开一块地"的操作无机械后果 | 阶段三（zone 扩展） | 未开工 |
| GAP-19 | Heightened Focus 的「Step 携带生物」未实现 | `class_feature_catalogs/srd_class_features.json` 里 `heightened_focus.parameters` 只有 `flurry_strikes` 与 `patient_defense_temp_hp_dice`；付费 Step 携带 5 尺内自愿 Large 或更小生物的能力没有任何事件形状（缺"移动另一个 actor 的 position"的载体） | 付费 Step 少一条效果；被携带者随行、不扣其移动、不引发借机攻击三条都无从表达 | S0-F10A 后续（本片已登记为未实现） | 未开工 |

## 按片索引

- **S0-F10A**：GAP-14（本片内解决）、GAP-13（后续抽象）
- **S0-F10B**：GAP-3（本片抽出通用检定函数）
- **S0-F10C**：GAP-5（前置）、GAP-1
- **S0-F10D**：GAP-6（① 依赖工具表，已有数据）、GAP-7、GAP-8
- **S0-F10E**：GAP-6（② 操作动作本身）
- **独立片「检定工具」**：GAP-2、GAP-4（并消化 GAP-9）
- **阶段三**：GAP-1
- **未立项**：GAP-10、GAP-11、GAP-12、GAP-13

## 附注 A：为什么 Utilize 不需要对象模型

Utilize 不是"面向某个对象实体"的动作，而是**"这件东西需要一整个动作来用"**的判定：

- 平时与物件的接触是 free interaction（每回合一次，可随移动/攻击附带），拔剑、开门、丢下东西都属于它；
- 只有当物件声明"requires an action"时，才升格为 Utilize 动作。

因此引擎只需要三件事，都不涉及物体实体：

1. `utilize` 意图：`{actor_id, item_ref?, description, dc?}`，花一个动作；
2. 若带 `item_ref` 且该物品在工具/装备表里声明了对应 `task`，用表里的 `dc` 与 `ability` 掷一次检定（复用 GAP-3 的 `_skill_check_events`）；
3. 发一条审计事件 `dnd2024.utilize.performed`，结果由 GM 描述——链条、铃铛、油灯这类没有机械后果的操作，到事件为止。

少数**确实会产生状态**的（铁蒺藜铺成区域、链子束缚生物），才需要后续接 `combat.zones` 或条件；那是它们的副作用，不是 Utilize 的前提。
