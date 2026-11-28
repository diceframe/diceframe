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
| GAP-4 | 无 GM 发起检定的入口 | `webui/services/game_controls.py::check_request_for_action` 只接受玩家 `text`；DC 取 `game_config.difficulty` 全局值 | "DM：这里有一把锁，DC 15" 这个最自然的裁定动作无法表达 | `GAP4_GM_CHECK_REQUEST_TASK_SPEC_2026-11-20.md`（已定稿） | 未开工 |
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
| GAP-16 | 无 GM 裁定原语（施加/移除条件） | `INTENT_TYPES` 无任何条件写入意图；`gm_command` 只解析资源修正与复活（`_parse_gm_resource_change` / `_parse_gm_revive_command`）；条件只由法术/特性路径产生 | 用铁蒺藜铺地、锁链束缚、DM 临时裁定中毒/目盲等**全部无法落地** | `GAP16_GM_CONDITION_TOOL_TASK_SPEC_2026-11-20.md`（已定稿） | 未开工 |
| GAP-17 | 无"发声"事件，Hide 的四类终止之一没有产生者 | `_hide_events` 依赖 `source="hide"` 的 invisible；终止路径里"高于耳语的声音"没有任何事件能触发它（铃铛、喊叫都无处表达） | 铃铛等 Utilize 的叙事后果无法影响隐藏 | 同 GAP-16 | 未开工 |
| GAP-18 | 区域只有遮蔽等级，没有进入触发/困难地形 | `combat.zones` 字段仅 `{level, origin, radius_ft, magical, ...}`；无 `on_enter` / `difficult_terrain` / `speed_zero` | 铁蒺藜、滚珠、油腻术这类"铺开一块地"的操作无机械后果 | 阶段三（zone 扩展） | 未开工 |
| GAP-19 | Heightened Focus 的「Step 携带生物」未实现 | `class_feature_catalogs/srd_class_features.json` 里 `heightened_focus.parameters` 只有 `flurry_strikes` 与 `patient_defense_temp_hp_dice`；付费 Step 携带 5 尺内自愿 Large 或更小生物的能力没有任何事件形状（缺"移动另一个 actor 的 position"的载体） | 付费 Step 少一条效果；被携带者随行、不扣其移动、不引发借机攻击三条都无从表达 | S0-F10A 后续（本片已登记为未实现） | 未开工 |
| GAP-20 | 怪物/野兽 statblock 只有内联契约，没有持久目录 | 敌人字段白名单已足够宽（`_ENEMY_KEYS`：abilities / speeds / size / senses / attacks / cr / 抗性 / 条件免疫），但只服务 `combat.start` 的临时敌人；`content/` 下无 monster 目录，`skills` 也不在白名单里 | Wild Shape 的已知形态无可读数据；Polymorph 的目标形态同样无处取；「取较高者」缺 `skills` 字段 | **S0-MM2**（已交付 CR0–1 全类型 135 条目录） | 已修 |
| GAP-21 | 无「形态覆盖」派生层 | `character/derivation.py::derive` 把 abilities/AC/speed 全从角色卡派生，没有 override 输入；`_actor_view` 无 form 概念 | Wild Shape 的「替换物理属性、保留心智/HP/特性」无法表达 | S0-F25（`forms/panel.py`） | 已修 |
| GAP-22 | 形态攻击无法进玩家行动 | 玩家行动 = 装备攻击 + 职业 capability；Beast 的 `attacks[]` 只在敌人路径被消费 | 变形后无法用形态的动作攻击 | S0-F25（`forms/panel.py`） | 已修 |
| GAP-23 | 无变形生命周期与施法门控 | 无 `form.assumed` / `form.reverted` 事件、无「变形中不可施法」gates、无装备 merge/wear/drop | assume/revert、跨形态持续效果保留、Polymorph HP 溢出都无从落地 | S0-F25（事件 + 持久化 + 施法门控已接；装备模式与时长到期未消费） | 部分完成 |

| GAP-24 | stat block 缺 6 个非特质字段 | `_ENEMY_KEYS` 无 `skills` / `languages` / `proficiency_bonus` / `hit_dice` / `subtype` / `passive_perception`；敌人 `abilities` 存而不参与任何计算 | 非玩家单位无法像玩家一样算豁免/技能；Wild Shape 的「取较高者」缺 `skills` 与 PB | **S0-MM2**（已交付：六字段校验 + 六维派生 + 视图投影） | 已修 |
| GAP-25 | `counterspell_dc` 是把特质压成字段的孤例 | 敌人契约里 `counterspell_dc` 一个标量代表「能用法术反制」，在 `reaction_spells.py` 消费；没有 traits/reactions 容器 | 每加一个怪物特质就要再加一个专用字段，契约会持续膨胀 | 未来「特质 + 反应」通用化（未立项） | 未开工 |
| GAP-26 | `condition.blocked` 只产出、从未被消费 | `engine.py::_statblock_immunity_events` 发这个事件，`reducer.py::_reduce_event` 的无状态白名单里没有它；MM1 的用例只调引擎方法、没走 `apply_batch` | 任何一次真的条件免疫都会让整批事件 apply 失败，攻击直接打不出去 | **S0-MM3a**（已加进无状态白名单） | 已修 |
| GAP-27 | rider 的复合状态无法表达 | `parse_riders` 不解析 "While Grappled, the target has the Restrained condition"；Crocodile / Giant Octopus / Darkmantle 的附着、吞下都没有形状 | 擒抱应当伴随束缚、吞下与附着的整段效果只能留一条 `grappled` | S0-MM4（复合状态），未立项 | 未开工 |
| GAP-28 | rider 的移动前提没有事实来源 | 只有 `economy.moved` 布尔，没有「是否在命中前直线移动 20 尺」；`srd_monsters.json` 里 Boar / Elk / Giant Goat / Warhorse 的 prone 因此未导入 | Pounce 类「撞过去才击倒」的 rider 无法表达 | S0-MM4（移动事实），未开工 | 未开工 |
| GAP-29 | 分档失败没有「差多少」与「第二次」 | `_saving_throw_check` 只返回 `success` 布尔；`parse_riders` / `parse_save_action` 遇到 `Failure by 5 or More`、`Second Failure`、`repeats the save` 整条放弃 | Cockatrice / Death Dog / Homunculus / Pseudodragon / Harpy 这类两阶段效果无法落地 | S0-MM4（分档失败），未开工 | 未开工 |
| GAP-30 | 存档型动作只有「一次豁免 + 一个伤害分支」 | `_save_action_events` 只发一条 `check.resolved` 与两段伤害，没有持续回合、每回合重复伤害、失败后附加条件 | Steam Mephit 的减速、Ghoul 的麻痹、Pseudodragon 的昏迷都无法表达 | S0-MM4，未开工 | 未开工 |
| GAP-31 | 传奇抗性自动生效，没有「拒绝」通道 | `_legendary_resistance_events` 在豁免失败时直接改写成功，不经决策窗口 | GM 无法选择「这次不用传奇抗性」，等于替玩家/怪物做了决定 | S0-MM4（决策窗口统一化），未开工 | 未开工 |
| GAP-32 | Flyby 缺「飞行中」这个前提 | 引擎没有飞行状态；`_movement_events` 见到 `flyby` 就清空威胁表 | 一只被摔到地上的飞禽仍然免借机攻击 | 与「飞行/坠落」系统同批，未立项 | 未开工 |
| GAP-33 | 呼吸与穿缝类特质没有规则消费者 | `amphibious` / `water_breathing` / `amorphous` 只做了声明与视图投影，`grep -rn "amphibious" src/` 只有契约与投影 | 水下窒息、挤过 5 尺缝隙这类判定无处生效（与 GAP-2 的技能检定缺口同源） | 环境与探索规则（未立项） | 未开工 |
| GAP-34 | 模块怪物档案不接受新形状，且预设投影与权威状态不同形 | `content/contracts.py::_validate_attacks` 仍只认 `id / name / damage(str) / attack_bonus / range`，且没有 `actions` / `traits` | 冒险包里手写的怪物无法使用 riders / Multiattack / 存档型动作 / 特质；`encounter_presets()` 仍返回旧形状字符串（`attacks[0].damage == "1d4+1"`），而 `combat.start` 之后权威状态是段数组，同一份数据在开战前后形状不同 | 模块内容契约（未立项） | 未开工 |
| GAP-35 | 条件的感官后果没有消费者 | `condition_flags` 只有 5 个布尔；`blinded` 的「依赖视觉的检定自动失败」、`deafened` 的听觉判定、`petrified` 与 `exhaustion` 的整段效果都没有落点 | 这几个条件目前只有攻击面与速度面，感知面仍是空的 | 随 GAP-2 / GAP-3 的检定系统 | 未开工 |
| GAP-36 | Multiattack 的「替换攻击 / 任意组合」没有原语 | `_validate_multiattack` 只收 `repeat` / `combo` / `choice`；`parse_multiattack` 见到 `replace` / `in any combination` / `Spellcasting` 整条跳过 | CR 0-1 里 6 条 Multiattack（Lion / Pirate / Goblin Boss / Scout / Rust Monster / Dryad）无法导入 | S0-MM4（怪物施法原语之后） | 未开工 |
| GAP-37 | `spider_climb` 只有速度事实，移动不读它 | `_with_climb_speed` 只写 `speeds.climb`；移动预算仍只读 `speeds.walk`，没有「攀爬不额外消耗」的判定 | 攀爬在战斗里仍按步行速度结算，等于没说 | 阶段三（3D/网格移动） | 未开工 |
| GAP-38 | 没有「起身」 | 引擎没有 stand-up；`prone` 只能等 `combat` 时限 | 被击倒的角色在整场战斗里无法站起来 | **S0-MM3e**（按 2024：花一半速度、不是动作；`stand_up` 意图 + `prone.stand_up` 声明） | 已修 |
| GAP-39 | 擒抱无法挣脱 | `escape_dc` 只被写入、无消费者 | 玩家被擒抱后没有反制手段 | **S0-MM3e**（`escape` 意图；DC 读实例、允许的检定与成本读注册表） | 已修 |
| GAP-40 | 存档型动作没有射程与目标上限 | 契约只有 `ability` / `dc` / 两个分支；Satyr 的 90 尺、Constrictor 的 5 尺 Medium 都无法表达 | 任何距离都能吃吐息，目标数量也不受限 | 区域与射程原语（未立项） | 未开工 |
| GAP-42 | 玩家申报骰面（实体骰）没有通道 | 战斗里每个 d20 都来自服务端 rng；`src/engine/dice.py::parse_player_roll` 只服务老通用引擎的聊天掷骰，不绑定任何 `check.resolved` | 桌上掷实体骰的玩家无法把自己的点数交给引擎，只能由服务端重掷 | 需先定信任模型（谁能申报、如何审计、GM 可否覆盖），未立项 | 未开工 |
| GAP-41 | rider 的生物类型前提无法表达 | Ghoul 的麻痹写明「非亡灵非精灵」；`parse_riders` 见到这类前提整条不导入 | 这类 rider 在目录里缺席（宁可缺席也不对所有目标生效） | S0-MM4 | 未开工 |
| GAP-46 | 重新规范化角色卡会把职业资源的当日消耗重置为满 | `runtime.normalize_character_submission()` 重建后调 `_sync_class_resources()` → `_sync_specs`，对"不存在的池"直接 `current = maximum`；`ruleset_characters.py:185/457` 会走这条路径 | 任何"每长休一次"的资源（自然恢复的免费面、诗人的激励、幸运点…）都能靠重新导入角色卡刷新，绕开日计数 | 与"资源首次创建即满"的迁移策略一起定，未立项 | 未开工 |
| GAP-47 | 兼职角色不能休息，也不能举行仪式 | `resting/engine.py:1153 _class_state()` 要求 `build.class_levels` 恰好一行，否则 `RestError("rest recovery currently requires one class")`；`_require_conscious` 之后第一处就撞上它 | 任何 `class_levels` 多于一条的角色卡在休息与仪式上 100% 失败。要修得同时改 `_class_state`、`sync_resources`、progression snapshot、生命骰四处，影响所有休息路径 | 单独立项（休息引擎的通用缺口，不是某一特性的前置） | 未开工 |

| GAP-43 | 触发式行动没有统一入口 | `check_windows.py::WINDOW_REGISTRY` 17 条只覆盖"改骰"一类（surface 为 attack/attack_check/attack_damage/ability_check/save），`attack_hit` 组在 main 上仍是 4 个意图布尔（`stunning_strike` / `brutal_strike` / `activate_sacred_weapon` / `light_extra_attack`），另 2 个随 #841 引入（`hurl_through_hell` / `quivering_palm`），`_attack_hit_events` 里 `if intent.get("hurl_through_hell")` 按 feature id 分支；`check_windows.py:27-29` 的纪律"never a branch in resolution.py"只作用在 post 面 | 新增一个触发式行动要改 validation + resolution 至少三处；同一特性两套机制（Quivering Palm 施加走意图布尔、结束走 capability）；反应类（Shield）靠条件有无表达可用性，"要不要自动用"没有位置 | `GAP43_ACTIVATION_TASK_SPEC_2026-11-24.md`（已定稿） | 已执行（#848、#853–#857、#863；2026-11-25） |
| GAP-44 | 法术攻击没有"多束/多射线"抽象层 | `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` 里 `eldritch_blast` 只有 `{mode: spell_attack, damage: 1d10, range: 120}`，没有束数，也没有随等级变形的键（该 catalog 无 `cantrip_*` 键）；对照 `scorching_ray` 用的是 `target_count: 3` + `upcast_target_count`，表达的是"选 3 个目标"，不是"同一目标吃 3 束" | SRD 2024 的 Eldritch Blast 在 5/11/17 级各多一束——**现在 11 级魔契师每回合只掷 1d10**；S0-F32 §6.5「多射线逐次加」因此写不出合法用例，Agonizing Blast 的主场景无法验收 | 法术子系统（未开工）：要先定"法术攻击"的语义与抽象层——束数从哪来、命中判定是每束一次还是共用一次、每束的伤害/骑乘/专注如何落账 | 未开工 |
| GAP-48 | `attack_declarations` 没有面向玩家的投影与 UI | 服务端只有 `engine.py:658 _attack_state_options()` 投影 reckless / brutal / studied；`grep -rn "attack_declarations\|brutal_strike\|stunning_strike\|sacred_weapon" frontend-v2/src` **零命中**，`Dnd2024CombatPanel.vue:893 stageAttack()` 只发 `weapon_ref` / `two_handed` / `spend_luck` | Reckless Attack、Brutal Strike、Stunning Strike、Sacred Weapon、Quivering Palm 这些**已经实现的声明**，玩家在面板上都无法声明，只能靠 API 手工传；诡诈打击是第六个受害者 | S0-F17e 只落地了 Cunning Strike 这一条：`_attack_state_options()` 新增 `cunning_strike` 投影（`available` / `effects` / `count` / `costs` / `dice` / `names`），面板按 effect 列表渲染。Reckless Attack、Brutal Strike、Stunning Strike、Sacred Weapon、Quivering Palm 仍是 API-only —— 它们的载荷形状各不相同（布尔 / 列表 / 武器选择），一张通用卡需要先定 schema | 部分（2026-11-26） |
| GAP-49 | 借机攻击没有声明通道 | `resolution.py:5900` 的 `opportunity_attack` 窗口在 `resolve` 分支构造的 `attack_intent` 只有 `actor_id` / `target_id` / `weapon_ref`（或 `attack_id`），answer intent 也只有 `option` | 规则允许在借机攻击里打出偷袭并选诡诈打击（或 Brutal Strike），引擎无法表达；「每回合」账本本身是对的（`feature_usage` 按 round+turn_index 记账），缺的只是声明入口 | 未立项（需先想清 answer intent 如何携带声明） | 未开工 |
| GAP-50 | 坠落没有触发通道 | `src/rulesets/dnd2024/` 下 `falling` / `fall_damage` 零命中；决策 kind 只有 `hit_reaction_spell` 与 `counter_spell`，没有"你正在坠落"这种窗口；也没有让 GM 声明一次坠落的意图类型 | Monk 4 的 Slow Fall 无法实现——规则要求它在**真实坠落**时提供 Reaction，F11 任务书 §7 停止条件第 3 条因此成立；任何"GM 直接扣血 + 施加 Prone"的替代都不满足反应窗口 | 需要新意图类型 + 新决策 kind，属 GAP-43 的 kind 注册表之后（先定 window registry 再开口子） | 已登记 |
| GAP-51 | 触发式能力没有策略层（询问 / 自动 / 不触发） | `check_windows._window_assigned_to` 只有席位级分叉：真人派给玩家，其余派给 GM；没有触发器级偏好存储。命中后的 offer 是三处手写（`_hit_spell_offer` / `_hit_reaction_offer` / `_damage_window_offer`），既没有注册表，也没有"候选目标"的数据形状。自动作答阶梯（`_allied_automatic_intent`）是**确定性优先级**，没有随机选择，也没有骰迹 | 玩家无法对单个触发器选"每次都问 / 自动挑一个 / 从不"；Cleave、Smite、Shield、Lucky 这些触发式能力只能"每次都问"，托管席位则被确定性代打 | 策略层（未立项）。前置：先把命中后 offer 那一层数据化（触发器 → 候选集）；若做"自动随机选"，必须走引擎 rng 并把选择记进事件流，否则 replay 不一致 | 未开工 |
| GAP-53 | `condition` 没有"攻击检定劣势"的作用域 | `attack_modes` 的声明只认 `side` / `mode` / `negated_by` / `consume_on` / `within_ft` / `beyond_ft`，没有 `abilities`；`attack_sources` 也不知道本次攻击用的是力量还是敏捷（finesse / 远程 / 力量各有分支） | `ray_of_enfeeblement` 的 Enfeebled 原文是 "Disadvantage on Strength-based **D20 Tests**"（2024 术语含攻击检定），SP2 只能表达属性检定与豁免那两半；文案已改准为 "ability checks and saving throws" 以免向玩家承诺未实现的行为 | 要给 `attack_modes` 加 `abilities` 作用域，并让 `attack_mode` 接收本次攻击的属性；前置是先定"攻击属性"的单一来源 | 已登记 |
| GAP-54 | `spell_effects` 不能"成功时也施加条件" | 条件的施加由 `spell_resolution` 按 `mode` 决定：`saving_throw` 只在**失败**时施加。原文里 `ray_of_enfeeblement` 成功豁免仍给"下次攻击劣势" | 成功分支整段无法表达，那条法术在目标豁免成功时等于空放；SP2 只落了失败分支 | 需要 effect 层声明"成功时施加某条件"，或把 `condition` 拆成 `on_fail` / `on_success` 两个位置 | 已登记 |
| GAP-55 | `1_minute` 这类时长不会自然到期 | `primitives.TURNS_SCOPED_DURATIONS` 认的是 `one_minute`，而 `spell_effects` 的数据写 `1_minute` / `1_hour` / `10_minute`，`conditions.py` 的 `turns_remaining` 因此不递减 | `blindness_deafness` 这类无专注的强条件只会被重复豁免结束，不会因时长结束；既有 `1_hour` 有同样问题（P5 记作"GM 叙事层管理"） | 统一时长词汇（数据侧或解释器侧二选一），或让 `as_condition_text()` 的解析与 `TURNS_SCOPED_DURATIONS` 共用一张表 | 已登记 |
| GAP-56 | 每回合自动伤害没有落点 | `spell_effects` 只能表达"施放那一瞬"；`save_repeat` 覆盖"每回合结束再豁免"，但不覆盖"施法者回合开始时对仍在法术内的目标再结算一次伤害" | `phantasmal_force` 原文只有"你的每回合 2d8"、**没有初始伤害**，SP2 因此整条退回 | 需要"法术实例 + 目标集合"的运行时记录（当前 concentration 只记 spell_ref 与来源），再加一个施法者回合开始的 tick | 已登记 |
| GAP-57 | 伤害骰没有减值通道 | 伤害拼池在 `hit_resolution` / `spell_resolution`，只做加值（`extra_damage_parts`）与暴击翻倍，没有"减 1d8、最低 0"的位置 | `ray_of_enfeeblement` 的 "subtracts 1d8 from all its damage rolls" 无法表达；SP2 只落了检定劣势那半 | 在拼池处加一个"条件减骰"阶段，并想清它与暴击翻倍的顺序（暴击翻倍的是基础骰，减值应在之后） | 已登记 |
| GAP-58 | 法术不能授予动作 | `spell_effects` 没有"之后每回合可用附赠动作/魔法动作再做一次"的位置；那属 capability 而非 tick | `heat_metal`（附赠动作再伤害）、`vampiric_touch`（魔法动作再攻击 + 自身回一半）无法表达 | 走 capability 授予通道；另需 `heal_self_fraction` 让 `healing` 能结算给施法者 | 已登记 |

| GAP-52 | 非战斗时 GMAgent 拿不到 stat block | GM 上下文走 `instance.to_llm_view()` → `GameContextProjector.project()`，字段只有 `scene` / `players` / `away_players` / `combat_state`；而 `combat_state` 来自 `module_hooks.combat_view()`，没有战斗投影时返回 `neutral_combat_view()`（`{"combat_state": "none", "combat_active": false, 空先攻}`）——**里面没有 `enemies`**。怪物信息的唯一入口是战斗中的 actor 视图（`view.py:172` 带出 stat block 的 `languages`），战斗一结束就随 `combat_state` 一起清空 | 探索、社交、偷听对话这些非战斗场景里，GM 无法“翻 MM 看这个怪物会什么语言”，也看不到 CR / 抗性 / 特质；它只能依赖作者写进 lorebook 的内容或自己的记忆。注意这**不是语言独有的问题**，语言只是受影响字段之一 | 需要一条按需查阅通道（按 id / 名字 / CR 查精简卡），而不是把 stat block 塞进叙事上下文——后者会让每次叙事都带上用不到的整张卡 | 已登记 |


## 按片索引

- **S0-F10A**：GAP-14（本片内解决）、GAP-13（后续抽象）
- **S0-F10B**：GAP-3（本片抽出通用检定函数）
- **S0-F10C**：GAP-5（前置）、GAP-1
- **S0-F10D**：GAP-6（① 依赖工具表，已有数据）、GAP-7、GAP-8
- **S0-F10E**：GAP-6（② 操作动作本身）
- **独立片「检定工具」**：GAP-2、GAP-4（并消化 GAP-9）
- **阶段三**：GAP-1、GAP-37
- **未立项**：GAP-10、GAP-11、GAP-12、GAP-13
- **S0-MM3a**：GAP-26（本片内解决）
- **S0-MM3e**：GAP-38、GAP-39（本片内解决）
- **S0-MM3b/c/d 的遗留 + 后续 MM 片**：GAP-27 – GAP-37、GAP-40 – GAP-42
- **未立项（资源日计数）**：GAP-46
- **未立项（兼职休息）**：GAP-47
- **S0-F17**：GAP-48（F17e 内解决）、GAP-49（登记，本片不修）

## 附注 A：为什么 Utilize 不需要对象模型

Utilize 不是"面向某个对象实体"的动作，而是**"这件东西需要一整个动作来用"**的判定：

- 平时与物件的接触是 free interaction（每回合一次，可随移动/攻击附带），拔剑、开门、丢下东西都属于它；
- 只有当物件声明"requires an action"时，才升格为 Utilize 动作。

因此引擎只需要三件事，都不涉及物体实体：

1. `utilize` 意图：`{actor_id, item_ref?, description, dc?}`，花一个动作；
2. 若带 `item_ref` 且该物品在工具/装备表里声明了对应 `task`，用表里的 `dc` 与 `ability` 掷一次检定（复用 GAP-3 的 `_skill_check_events`）；
3. 发一条审计事件 `dnd2024.utilize.performed`，结果由 GM 描述——链条、铃铛、油灯这类没有机械后果的操作，到事件为止。

少数**确实会产生状态**的（铁蒺藜铺成区域、链子束缚生物），才需要后续接 `combat.zones` 或条件；那是它们的副作用，不是 Utilize 的前提。
