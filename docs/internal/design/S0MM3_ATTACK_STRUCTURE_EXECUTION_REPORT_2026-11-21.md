# S0-MM3 执行报告：攻击结构、Multiattack、存档型动作与八个特质

| 项 | 值 |
|---|---|
| 任务书 | `S0MM3A_ATTACK_RIDERS_TASK_SPEC_2026-11-21.md`、`S0MM3B_MULTIATTACK_TASK_SPEC`、`S0MM3C_SAVE_ACTIONS_TASK_SPEC`、`S0MM3D_TRAITS_TASK_SPEC` |
| 数据依据 | `S0MM3_ATTACK_SCHEMA_2026-11-21.md`、`S0MM3_TRAITS_ACTIONS_SURVEY_2026-11-21.md` |
| 分支 | `feat/dnd2024-s0mm3a-attack-riders`，叠在 #829（S0-MM2）之上 |
| 状态 | 已执行；四片矩阵全过；**4 处契约偏差**（§5）、**9 条新缺口**（§6） |

## 1. 交付

| 交付 | 位置 |
|---|---|
| 攻击伤害段读取（新旧两种形状归一） | `combat/primitives.py::attack_damage_parts` / `normalize_attack_profile` |
| 附带效果四种 kind + 尺寸门槛 + 时长 | `primitives.py::RIDER_KINDS` / `RIDER_DURATIONS`；结算在 `resolution.py::_statblock_rider_events` |
| 第二段伤害独立过抗性 | `resolution.py::_extra_damage_events` / `_damage_instance_event` |
| 攻击契约扩展（`damage[]` / `riders[]` / `aliases[]`） | `combat/validation.py::_validate_attack_profile` |
| Multiattack 三原语 + 生物内别名 | `validation.py::_validate_multiattack`；展开在 `view.py::_multiattack_parts`；结算在 `resolution.py::_multiattack_events` |
| 存档型动作 + 半伤骰池 | `validation.py::_validate_save_action`；结算在 `resolution.py::_save_action_events` |
| `recharge` / `per_day` | `resolution.py::_turn_start_recharge_events`；记账在 `reducer.py`（`dnd2024.action.used` / `.recharge_rolled`） |
| 八个特质 + 闭集校验 | `primitives.py::TRAIT_IDS` / `TRAIT_PARAMS`；校验在 `validation.py::_validate_enemy_traits` |
| 特质消费者 | 豁免优势 `_saving_throw_check`（magic / legendary）、攻击优势 `_attack_check_mode`（pack_tactics）、移动 `_movement_events`（flyby）、速度 `_with_climb_speed`（spider_climb） |
| 生成器 | `scripts/rulesets/import_srd_beasts.py`（伤害段 / rider / Multiattack / 存档型动作 / 特质） |
| 目录重生成 | `templates/.../monster_catalogs/srd_monsters.json` |
| 条件词汇 | `templates/.../combat_catalogs/srd_combat_core.json`：补 `poisoned` / `grappled` / `restrained` / `blinded` / `deafened` |
| 测试 | `test_dnd2024_attack_riders.py`（21）、`test_dnd2024_multiattack.py`（17）、`test_dnd2024_save_actions.py`（16）、`test_dnd2024_traits.py`（18） |

## 2. 逐片验收

### 2.1 MM3a（8 项）

| # | 要求 | 结果 |
|---|---|---|
| 1 | 单段攻击照旧 | ✅ 一次命中仍是一个 `resource.changed`；装配后 `damage` 只有数组一种形状 |
| 2 | 两段伤害各自过抗性 | ✅ 斩击被狂暴抗掉一半、火焰全额；两段各自独立掷、独立 `resisted` |
| 3 | rider 顺序 | ✅ 先 `prone` 后 `grappled`，事件顺序 = 声明顺序；`escape_dc` 记在条件上 |
| 4 | 条件免疫拦截 | ✅ rider 产出的就是普通 `condition.applied`，走 S0-MM1 的同一道闸门（并修好那道闸门的消费者，见 §4） |
| 5 | 旧输入兼容 | ✅ `damage` + `damage_type` 的敌人照常开战，装配成单元素数组，`damage_type` 不再保留 |
| 6 | 两者都缺 → 拒绝 | ✅ 报 `enemy attack requires damage` |
| 7 | 非法 kind / 越界 DC / 非法类型 → 拒绝 | ✅ 另外测了「数组与旧字段同时出现」「4 条 rider」「非规范条件名」 |
| 8 | 目录重生成后仍可加载 | ✅ 135 条全过；目录侧 `(riders, multipart) == (16, 22)` 钉死 |

### 2.2 MM3b（6 项）

| # | 要求 | 结果 |
|---|---|---|
| 1 | `repeat` 三击 | ✅ 一个意图 → 3 条攻击检定 + 3 段伤害，动作位只扣一次、额度归零 |
| 2 | `combo` 各用各的档案 | ✅ 咬击 1d6 穿刺 + 爪击 1d8 斩击，各自加值与类型 |
| 3 | `choice` 只走一支 | ✅ 未选分支不产生任何事件；未给分支名 → 拒绝 |
| 4 | `attack_id` 不存在 → 拒绝 | ✅ 整条意图被拒，错误指明引用名 |
| 5 | 别名限定生物内 | ✅ `aliases: ["Rend"]` 解析到本体的 Claw；名称匹配大小写不敏感且只在本生物内 |
| 6 | 原子性 | ✅ 第二击射程不足 → 整条意图被拒、第一击也没打、动作还在 |

### 2.3 MM3c（5 项）

| # | 要求 | 结果 |
|---|---|---|
| 1 | 失败全额 | ✅ 4d6 掷四颗、全额落地 |
| 2 | 成功半伤骰池 | ✅ 成功分支掷 **2** 颗（不是 4 颗再除 2）；奇数骰用 `half` 标记独立掷、独立折半 |
| 3 | DC 边界与非法 ability | ✅ `dc` 0/31、`ability: "luck"`、无 `on_fail`、失败分支夹带条件、非法伤害类型、坏 `usage_limits` 全部拒绝 |
| 4 | `recharge` 掷骰决定可用性 | ✅ 用掉即不可用、不可用时不消耗动作也不掷骰；回合开始掷 d6（≥ from 恢复） |
| 5 | `per_day` 耗尽后拒绝 | ✅ 2/日用完第三次被拒，记账 `used: 2 / available: false` |

### 2.4 MM3d（6 项）

| # | 要求 | 结果 |
|---|---|---|
| 1 | 八个特质各自一条最小用例 | ✅ 事实类读视图、`spider_climb` 给 climb = walk、`flyby` 不开借机窗口（对照组：无此特质会开） |
| 2 | `magic_resistance` 只对抗法术 | ✅ 法术豁免出现 `magic_resistance` 优势来源；非法术豁免只掷一颗骰 |
| 3 | `pack_tactics` 需要邻近盟友 | ✅ 目标身边有同伙 → 优势；独处 → 无来源 |
| 4 | `legendary_resistance` 次数耗尽后拒绝 | ✅ 成功改判 + 记 `legendary_resistance: 1`；用尽后照常失败 |
| 5 | 未知特质 id 拒绝加载 | ✅ 另拒绝未知参数、重复 id、非法 `per_day`、非对象条目 |
| 6 | 目录里这 8 个都能加载 | ✅ 47 条特质、七个 id（CR 0–1 没有传奇生物） |

## 3. 数据侧：目录重生成的统计

```
135 条生物（不变）
两段伤害     22 条   （Giant Toad / Spy / Scorpion / Venomous Snake …）
rider        15 条   （prone 5、grapple 6、poisoned 3、charmed 1）
动作         18 条   （Multiattack 11 = repeat 10 + combo 1；save_action 7）
  ├ usage     5 条   （四条吐息 recharge 5-6 / 6，均在 feed 的结构化字段里）
  └ choice    0 条   （契约支持，CR 0-1 没有"二选一"的 Multiattack）
特质         47 条   （pack_tactics 14、water_breathing 7、amphibious 6、
                       magic_resistance 6、flyby 6、spider_climb 6、amorphous 2）
aliases       0 条   （契约支持，CR 0-1 的攻击名都能直接解析）
```

解析只认三类明确句式，其余一律不猜（未解析的按缺口登记，§6）：

1. `If the target is a Medium or smaller creature, it has the Prone condition` → 尺寸门槛
2. `, and the target has the Poisoned condition until the end of its next turn` → 条件 + 时长
3. `Constitution Saving Throw: DC 12. Failure: The target has the …` → 豁免门槛

**第一版生成器有四个读错的地方，评审逐条比对了 feed 原文才暴露**（都已修，各自有用例）：

| 症状 | 根因 | 现在 |
|---|---|---|
| Scorpion `1` 变成 `1d6`、Spider `1`→`1d4`、Flying Snake `1`→`2d4` | 从整条 prose 里找骰式，命中的是 `plus 3 (1d6) Poison` 那一段 | 主伤害只读 ` plus ` 之前的那截 |
| Sprite `40/160` → `40/40`、Bugbear `20/60` → `10/10` | 正则只认 `reach N`，`range A/B` 的射程后半段丢了 | 分两支捕获，并单独找 `or range A/B` |
| 七条吐息 `usage_limits` 全为空 | 只读描述里的 `(Recharge N)`，而 feed 的结构化字段是 `RECHARGE_ON_ROLL: 5` | 优先读结构化字段（5 条吐息拿到 5-6 或 6） |
| Goblin `plus (1d4) … if the attack roll had Advantage` 变成无条件第二段 | 没看第二段后面的限定从句 | 后接 `if` 即整段不导入 |

另外 Ghoul 的麻痹 rider 从目录里去掉了：原文是"目标既非亡灵也非精灵"，那是**生物类型前提**，本片没有这个谓词；导入等于对所有目标生效。

## 4. 修掉两个基线缺陷

**4.1 `condition.blocked` 只产出、从不被消费（S0-MM1 遗留）。** 它从 S0-MM1 起就在
`engine._statblock_immunity_events` 里被生产，但 `reducer._reduce_event` 的白名单里没有它——
**任何一次真的条件免疫都会让整批事件 apply 失败**。MM1 的用例只调引擎方法，从没走
`apply_batch`，所以没被发现。修法：与新增的 `dnd2024.rider.skipped` /
`dnd2024.multiattack.truncated` 一起放进无状态白名单（它们只做审计，不改状态），并补一条
真意图用例（法术打在免疫敌人身上）。登记 `GAP-26`。

**4.2 受击反应收尾路径丢掉 rider（S0-F14/F19 遗留）。** 命中经过护盾术/借机/至圣斩窗口
收尾时，`reaction_spells` 与 `triggered_spells` 各自提交伤害，而 `hit_riders`（Hunter's
Mark 的额外伤害、光耀/神圣打击）只在直通路径的 `_resolve_committed_hit` 里结算。
本片把"第二段伤害 + stat block rider"接进去时才发现这条缺口一直都在。现在四条收尾路径
统一走 `_hit_tail_events`，`damage_multiplier` 类反应（Uncanny Dodge）也按同一倍率缩放
**每一个**伤害实例。

## 5. 偏差（六处，都影响契约）

1. **`damage` 是数组，但旧形状长期共存**：任务书写"装配期归一到 `damage[]`"，实现是**装配期归一 + 读取层兼容**。原因：冒险包、AI 临时敌人、Wild Shape 形态数据都还是旧形状，只归一不兼容会让它们全线失效。归一后权威状态里只有数组，`damage_type` 不再保留。
2. **rider 多了两个字段**：`max_target_size`（"Medium or smaller"）与 `duration`（"until the end of its next turn"）。任务书的契约里没有它们，但 16 条 rider 里 10 条带尺寸门槛、4 条带时长——没有它们就只能整条不导入，狼咬不动 Medium 以上的目标。
3. **半伤在奇数骰时用 `half` 标记**：任务书要求"显式骰池、不做自动减半"。偶数骰（4d6→2d6）按显式池表达，与任务书一致；奇数骰（3d4）不存在等价的整数骰池，`count // 2` 会把期望从 3.75 压到 2.5。做法是允许伤害段带 `half: true`：**独立掷这一池，再向下取整**（等于 RAW 的 `floor(总伤/2)`，且绝不复用失败分支的骰值）。
4. **传奇抗性自动生效**：任务书要"每日次数 + 失败改成功"，但"可以改为成功"是一个选择，而引擎没有面向 GM 的拒绝通道。现在失败且有次数时自动改写并在 `check.resolved` 里留 `legendary_resistance` 与掷骰原值。拒绝通道登记 `GAP-31`。
5. **新增 5 条条件定义改变了既有战斗结果**：`poisoned` / `grappled` / `restrained` /
   `blinded` / `deafened` 之前只是名字，registry 里没有机械落点。现在 `blinded` 给攻击者
   优势、`poisoned` 给攻击者劣势、`grappled` 速度归零——一条既有用例
   （`test_a_blinded_rogue_is_not_offered_uncanny_dodge`）因此要改掷骰序列。这是补齐规则，
   不是回归，但属于"本片顺带改变的既有行为"，必须在这里说清。
6. **无类型的旧形状伤害仍按 `bludgeoning` 记**：`damage` 只有字符串、没有 `damage_type` 时
   （AI 临时敌人、模块怪物档案），读取层补 `bludgeoning`。留空会让 `_resist_damage` 认为
   "没有类型"，等于绕过全部抗性与免疫——那是 fail open，比一个明确的缺省更危险。

## 6. 未导入 / 装而未用（逐条登记）

| 项 | 现状 | 缺口 |
|---|---|---|
| 复合状态（Grappled 同时 Restrained、吞下、附着） | 只落 `grappled`，其余整段未解析 | GAP-27 |
| 擒抱的挣脱 | `escape_dc` 只写不读；`grappled` 速度归零后没有挣脱动作 | GAP-39 |
| 起身 | 没有"站起来"这个动作，击倒靠遭遇结束清除 | GAP-38 |
| 移动前提的 rider（"moved 20+ feet straight"） | Boar / Elk / Giant Goat / Warhorse 的 prone 未导入 | GAP-28 |
| 分档失败 / 重复豁免 | Cockatrice / Death Dog / Homunculus / Pseudodragon 整条未解析 | GAP-29 |
| 存档型动作的持续后果 | 只做一次豁免 + 两段伤害 | GAP-30 |
| 传奇抗性的拒绝通道 | 自动生效 | GAP-31 |
| Flyby 的"飞行中" | 无飞行状态，见 `flyby` 即免借机 | GAP-32 |
| `amphibious` / `water_breathing` / `amorphous` | 只声明与投影，无规则消费者 | GAP-33 |
| 模块怪物档案 | `content/contracts.py` 不认新形状；预设投影仍是字符串，权威状态是数组 | GAP-34 |
| `blinded` / `deafened` / `petrified` / `exhaustion` 的感知面 | 只有攻击面与速度面 | GAP-35 |
| Multiattack 的替换 / 任意组合（6 条） | 未导入 | GAP-36 |
| `spider_climb` 的移动折扣 | 只有 climb 速度事实，移动仍读 walk | GAP-37 |
| 存档型动作的射程与目标尺寸 | 契约没有这两个字段（Satyr 90 尺、Constrictor 5 尺 Medium） | GAP-40 |
| 条件的感知面（blinded 的目视检定自动失败等） | 只有攻击面 | GAP-35 |
| 生物类型前提的 rider（Ghoul"非亡灵非精灵"） | 整条不导入 | GAP-41 |
| `aliases` / `choice` | 契约支持，CR 0-1 数据为零 | — |

## 7. 验证

```
ruff check src/ tests/ scripts/rulesets                 All checks passed
mypy                                                    102 files, no issues
test_dnd2024_attack_riders.py                           24 passed
test_dnd2024_multiattack.py                             19 passed
test_dnd2024_save_actions.py                            17 passed
test_dnd2024_traits.py                                  18 passed
test_dnd2024_srd_importer.py                             6 passed
test_dnd2024_escape_and_stand_up.py                     15 passed
全量 tests/rulesets（135 个文件，分两批 -n 8）            2756 passed / 0 failed
                                                         （1651 + 1105；叠在 #829 上的树上）
```

全量一次跑完需要 13-15 分钟，本机在这条会话里被中断过两次，因此按文件列表切成
`test_*.py` 的第 1-70 个与第 71-134 个两批跑完；两批文件不重叠，合起来就是全部。

首轮实施后全量是 2755 passed；评审提出 6 个必须修的问题，修完新增 18 条用例（反应收尾、
条件跨战斗泄漏、端到端免疫、截断额度、批次内 roll_id、存档往返、生成器六条），
上表的数字是修复后的。

改到的既有用例（都是形状变更导致，覆盖未减少）：`test_dnd2024_rage.py`、`test_dnd2024_stoneskin.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_monster_catalog.py`、`test_dnd2024_monster_facts.py`、`test_dnd2024_check_windows.py`。

## 8. 评审发现并已修的问题（子 Agent 审核轮）

| 严重度 | 问题 | 修法 |
|---|---|---|
| 阻塞 | 命中经护盾/至圣斩窗口收尾时，第二段伤害与**全部** rider 消失（四条收尾路径各自提交主伤害） | 抽出 `_hit_tail_events`，四条路径统一调用；`damage_multiplier` 类反应按同一倍率缩放每个实例 |
| 阻塞 | 无 `duration` 的 rider 条件战后永久留在角色卡上（11/16 条） | rider 缺省时长改为 `combat`，并在执行报告登记 GAP-38（没有起身/挣脱动作） |
| 阻塞 | 擒抱无法挣脱：`escape_dc` 无消费者 | 归 GAP-39（本片不做），报告与缺口册写清 |
| 阻塞 | 去掉 `bludgeoning` 缺省后，无类型攻击绕过全部抗性 | 旧形状（无 `damage_type`）恢复 `bludgeoning` 缺省；数组形状必须显式给类型 |
| 阻塞 | 生成器完全不读 feed 的结构化 `usage_limits` | 优先读 `RECHARGE_ON_ROLL` / `PER_DAY`；目录里现在 5 条吐息带真时限 |
| 阻塞 | 目录数据三处静默错误（主伤害被第二段顶替、射程丢后半段） | 见 §3 的表；四个读法各配一条生成器用例 |
| 应该修 | 截断后的剩余额度可被 Multiattack/存档型动作复用 | 额度只兑现 `attack`；另加"窗口未答完时拒绝新意图"的用例 |
| 应该修 | 一个 Multiattack 批次内 `roll_id` 重复 | 收集后按 `(kind, actor)` 重排 |
| 应该修 | `target_turn_end` 在目标自己回合命中时提前一回合到期 | 记录 `expires_after`，多撑一个回合边界 |
| 应该修 | recharge 的上界被忽略 | 判定改为 `from <= roll <= to` |
| 应该修 | `traits.params` 非对象时静默降级 | 校验期拒绝 |
| 应该修 | 模块级 `_validate_multiattack` 与实例方法同名 | 改名为 `_validate_multiattack_action` |
| 应该修 | 无存档往返用例（ENGINEERING_RULES §15.1） | 补 `test_usage_survives_a_save_and_load_round_trip` |

未修（登记为缺口，见 §6）：射程/目标上限、生物类型前提、条件的感知面、复合状态、
分档失败、传奇抗性的拒绝通道。

## 9. S0-MM3e 追加：从条件内部解除它（挣脱 / 起身）

评审讨论后补的一片，解决 GAP-38 与 GAP-39。

**声明分两层，缺一不可。** 条件**类型**（注册表）说"能不能解除、用什么、花什么成本"：
`grappled.escape = {cost: "action", checks: [{ability: "str", skill: "athletics"}, {ability: "dex", skill: "acrobatics"}]}`；
条件**实例**（角色卡上那一条）带 `escape_dc` —— DC 是施加者的事实，同一条件不同怪不同。
只在注册表写 DC 会让所有擒抱共用一个数；只在实例写又会让"能不能挣脱"变成施加者的自由裁量。
rider 没写 DC 的实例（例如手写数据）**不允许挣脱**，不替它编数字。

**没有把后果塞进裸检定。** 已有的 `ability_check` 契约是"调用方声明成本与 DC，引擎只回答成败"；
加一条"成功就清条件"会让它不再是裸检定，也会给下一个功能开同样的先例。所以另开两个意图，
它们的后果来自**状态声明**而不是调用方：

| 意图 | 形状 | 成本 |
|---|---|---|
| `escape` | `{condition, check: "str"\|"dex"}` | 注册表声明的 `action`/`bonus_action`；失败也照花 |
| `stand_up` | `{}` | `floor(生效速度 / 2)` 移动，不占动作 |

两者都复用 `check.resolved`（`kind: "escape"`）与 `condition.removed`，不加新事件类型；
起身的移动消耗走 `position.changed` 的 `movement_cost`（零距离），与所有移动同一本账。

**谁提交：人类席位 AI GM 代交不了。** 现有权限是"人类席位的意图只能本人提交，只有 AI 托管
席位才由 GM 代交"。所以链路是：面板从注册表 + 实例现算出 `escape`/`stand_up` 入口 → 玩家本人
提交 → 服务端掷骰结算 → AI GM 只叙述结果。要让"玩家说一句'我挣脱'"直接生效，得另开申报通道。

**玩家申报点数（实体骰）没有路径**，登记 GAP-42：战斗里每个 d20 都来自服务端 rng，
`src/engine/dice.py::parse_player_roll` 只服务老通用引擎的聊天掷骰、不绑定 `check.resolved`。
要开需先定信任模型（谁能申报、如何审计、GM 可否覆盖）。

顺带补了 `prone` 真正的规则后果：注册表新增 `within_ft` / `beyond_ft` 距离门槛，
5 尺内攻击有优势、5 尺外有劣势；距离未知时该声明按 fail closed 跳过（不凭"大概在近处"给优势）。

## 10. 自查清单（项目固定六条）

- **等价性**：玩家武器路径仍产出一条伤害实例，数值逐字节不变（既有回归全绿）；`damage` 读取统一走 `attack_damage_parts`。
- **多消费者一致性**：条件免疫只有一道闸门（`_statblock_immunity_events`），rider 不另写一套；尺寸判定只有 `size_at_most`。
- **跨特性交互**：`attack.spent` 的额度让 Multiattack 与 Attack action 共用同一套次数记账；窗口挡在中间时剩余攻击仍留在额度里。
- **递归/回路**：Multiattack 展开只调用 `_attack_events`，不再进入 `_multiattack_events`，没有自嵌套入口。
- **条件生命周期**：新增 `target_turn_end` 时长（"目标自己回合结束"）与 `attacker_next_turn_start`（来源回合开始），两者都由 `_expire_conditions` 按持有者/来源各自的回合兑现，战斗结束一并清除。
- **测试是否安慰剂**：断言落在事件流（`resource.changed` 的 `amount` / `damage_type` / `rolls` 长度、`condition.applied` 的顺序、`check.resolved` 的 `advantage_sources`），不落在视图字段；三处"对照组"存在（无 flyby 会开窗、无同伙不给优势、法术免疫走真意图）。
- **窗口与延迟提交**：命中被窗口挡下时，第二段伤害与 rider 与 `feature_use` 一起延迟，护盾术改判落空就一条都不落地；窗口答完的路径调用同一个 `_hit_tail_events`。
