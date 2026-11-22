# S0-F25 执行报告：变形地基（面板 / 状态 / 接线）

| 项 | 值 |
|---|---|
| 切片 | S0-F25 的地基部分（GAP-21 / GAP-23；GAP-22 见 §4） |
| 基线 | 叠在 #829（MM2）之上 |
| 状态 | 地基完成，**玩家暂不可用**（无形态攻击数据，见 §4） |

## 1. 交付

| 交付 | 位置 |
|---|---|
| 统一面板 + 四种合并策略 | `forms/panel.py`（`StatPanel` / `compose` / `WILD_SHAPE_POLICY` / `POLYMORPH_POLICY`） |
| 变形状态与限制 | `forms/state.py`（`FormState` / `assume_form` / `restrictions` / `wild_shape_state`） |
| 角色级持久化 | `combat/reducer.py`：`dnd2024.form.assumed` / `.reverted` 写入 `ruleset_character.forms` |
| 视图合成 | `combat/view.py::_shaped_view`（找形态 → 合成 → 回填既有键） |
| 技能派生修正 | #829 的 `fix` 提交（D-1） |
| 裸属性检定 | `ability_check` 意图，成本 `action` / `bonus_action` / `none` |

## 2. 为什么是"策略表"而不是分支

Wild Shape 与 Polymorph 只差**哪些槽被替换**：前者保留 HP / HD / 心智三项 / 类型 / 语言，后者全换。四种策略（`keep` / `replace` / `max` / `union`）表达完所有情况，引擎只有一条合成路径；新增 Shapechange 是第三张表。

**未在表里出现的槽一律 `keep`**：默认替换会让一次变形悄悄多给一个没人审过的槽。

## 3. 验证

```
form_panel                                                          23 passed
全量 tests/rulesets                                      2683 passed（14:21）
ruff / mypy                                                        干净
```

端到端（6 级德鲁伊 → 狼）：`STR 14 / DEX 15 / CON 12` 来自形态，`WIS 16` 保留，`HP 39` 保留，`AC 15`（取高），`attacks = [bite-0]`，`spellcasting_allowed = False`。`ruleset_character.forms.active` 落库。

## 4. 未完成（明确记录）

1. **形态没有可用的攻击**：`attacks[]` 只能表达"单次攻击"，而野兽真正的玩法依赖特质与动作（Multiattack / 豁免型 / rider）。这一项属 MM3（特质与动作），在此之前 F25 对玩家**仍不可用**。
2. **装备三态仅记录不消费**：`equipment_mode` 已存，`merge` 时尚未真正屏蔽装备加值。
3. **时长到期未处理**：`duration_hours` 已存，没有"休息/时间推进时自动恢复原形"的消费者。
4. **读档路径未验证**：`forms` 落在 `ruleset_character`，但存档 schema 是否保留未知键尚未验证——需要一条真实的 save/load 往返用例。
5. **`actor_view` 只覆盖玩家**：companion 与敌人的形态入口未接（当前也没有这种效果）。

## 5. 自查清单（本项目固定六条）

- 等价性：玩家侧派生未改数值（既有 2683 条全绿）
- 多消费者一致性：`restrictions()` 是唯一施法门控来源
- 跨特性交互：`form` 与 `condition` 分层——条件在最上层，变形不清 buff
- 递归/回路：Fleet Step 那类自授予未涉及；形态不会再变形（无嵌套入口）
- 条件生命周期：`steady_aim_slow` 等由既有机制处理，未新增
- 测试是否安慰剂：断言落在 `actor_view` 的最终数值与来源表，不落在中间结构


---

## 6. 第二轮：按修订稿补缺口（2026-11-22）

依据 `S0F25_SPEC_REVISION_2026-11-22.md` 的 R1–R6 顺序。

### 6.1 本轮完成

| # | 缺口 | 落地 |
|---|---|---|
| R1 | 已知形态 | `known_form_count()`（4/6/8）、`bootstrap_known_forms()`（SRD 推荐四形态，按等级 CR/Fly 过滤）、`learn_form()`（满 4 个时拒绝新增）；`form.assume` 校验形态**必须在已知列表内** |
| R2 | 装备逐件三态 | `FormState.equipment_modes: {item_ref: drop\|merge\|wear}`，旧存档单值归一到 `"*"`；`mode_for()` / `merged_item_refs()`；视图投影 `merged_item_refs` |
| R3 | 变形中施法 | 2–17 级**禁止一切施法**；18 级按材料文本判定："worth/gp" 与 "consum*" 两类禁止；组件数组缺失 fail closed |
| R4 | 结束原因 | `form.reverted` 带 `reason`；**再变形先原子结束旧形态**（`reason="reshaped"`） |

### 6.2 本轮未完成（明确记录）

| # | 缺什么 | 为什么 |
|---|---|---|
| R1b | 建卡/升级期选择形态、长休 `wild_shape_replacement` | 需要动 builder 与 resting option 的选项面，本轮未展开；当前以"首次使用按推荐四形态兜底 + `learn_form` 运行时入口"过渡 |
| R4b | 失能/死亡**立即**结束变形、时长到期 | 缺"条件施加的单一收口点"（MM3 的 `_hit_tail_events` 才有），在 F25 分支上做会变成多处散点；时长到期需要时间推进的消费者 |
| R5 | Wild Resurgence 双向转换 + Evergreen | 依赖 F6 临时槽与 roll Initiative 钩子，属独立切片 |
| R6 | Nature Magician + Longevity | 同上，且需要特性目录新增参数 |

### 6.3 验证

```
form_panel                                                          34 passed
全量 tests/rulesets                                      2694 passed（15:54）
ruff / mypy                                                        干净
```

### 6.4 本轮的三个实现坑（都只有跑测试才现形）

1. 字符串替换把 `equipment_mode` 的**函数签名默认值**也改成了 `field(...)`，导致 `'Field' object is not iterable`——数据类字段与函数参数同名时，批量替换必须分开处理。
2. `forms/__init__.py` 的 `__all__` 没同步，测试收集期直接 ImportError。
3. `resource.changed` 的临时 HP 事件用 `target_id` 而非 `actor_id`（上一轮已修，本轮复用）。


---

## 7. 第三轮：Wild Resurgence / Evergreen / Nature Magician（2026-11-22）

### 7.1 完成

| 项 | 实现 |
|---|---|
| **Wild Resurgence 双向** | 意图 `wild_resurgence`，`direction=slot_to_use`（必须 `current==0`、每回合一次、消耗一个法术位换回 1 次）与 `direction=use_to_slot`（消耗 1 次换 1 环临时槽、两次长休之间一次） |
| **Evergreen** | 掷先攻的开场事件里扫一遍：德鲁伊 20 级且 `wild_shape.current == 0` → 恢复 1 次；`current > 0` 什么都不做，所以每场最多补 1 次 |
| **Nature Magician** | 意图 `nature_magician`，校验 `1 ≤ N ≤ min(4, 当前次数)`，消耗 N 次并创建 **2N** 级临时槽 |
| **资源账** | 新增 `dnd2024.class_resource.restored`（唯一"加回去"的通道，被 `maximum` 夹住）；`wild_resurgence` / `archdruid` 两条长休资源写入恢复目录 |
| **每回合一次** | `dnd2024.wild_resurgence.used` 在 reducer 里写 `combat.wild_resurgence_turn`，`turn.advanced` 时清掉 |

### 7.2 明确不做

- **Longevity**：按要求视为纯叙事，`physical_years_per_decade` 不落任何机械实现——系统里没有需要统计寿命的来源。
- **`Natural Recovery` 之外的法术位恢复**：仍走 F6 的 `font_slot_created`，不新增槽位账本。

### 7.3 已补齐（2026-11-22 第四轮）

1. **四条特性声明已进目录**：`wild_shape`(2) / `wild_resurgence`(5) / `beast_spells`(18) / `archdruid`(20)，含 summary、`resource_id` 与 `source_ref`，并补了中英文 label。
2. **门控改为按特性拥有**：`form.assume`、`wild_resurgence`、`nature_magician`、`beast_spells`（施法放宽）与 Evergreen 全部改问 `features.has_feature(...)`，不再读等级数字。等级只是"授予条件"，多职业与内容编辑会让两者分叉。

**已收尾（第五轮）**：Nature Magician 的次数来源统一到 `archdruid` 资源（消耗时发 `class_resource.spent(archdruid)`，用完后同一长休内拒绝）。

### 7.4 验证

```
form_panel                                    47 passed（本轮 +6）
全量 tests/rulesets               2707 passed（15:12）
ruff / mypy                                  干净
```


---

## 8. 收尾（2026-11-22）

| 项 | 处理 |
|---|---|
| **merged 装备的真实效果排除** | 除 AC 外，装备在本模型里只有两条效果通道：AC（已重算）与**激活**。新增 `_item_is_merged_in_form`：形态内 merged 的物项**禁止激活**——"没有效果"不是"UI 里藏起来" |
| **Nature Magician 记账** | 消耗 N 次同时扣 `archdruid` 的 1 次长休额度；用完后同一长休内拒绝 |
| **前端** | 升级面板新增「已知野兽形态」一节，走 `requirements` 同一通道；建卡期（1 级）形态数为 0，自然不显示 |

### 8.1 验证

```
form_panel                                    49 passed
全量 tests/rulesets               2709 passed（13:53）
ruff / mypy                                  干净
前端 vue-tsc -b                              无错误
前端 vitest                       1729 passed / 236 files
```

### 8.2 仍然存在、但已不阻塞的项

1. **无甲防御的额外能力加值**未参与形态 AC 重算（druid 没有它；漏传只会低估，不会高估）。
2. **物品被动加值**在本模型里尚不存在（物品只有 AC 与激活两条通道），因此没有更多需要排除的东西；将来若引入"佩戴即加值"，必须同时加排除。
