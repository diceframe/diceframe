# S0-F31 执行报告：Use Magic Device、法术书与 Evocation Savant

| 项 | 值 |
|---|---|
| 任务书 | `S0F31_SPELLBOOK_ITEMS_TASK_SPEC_2026-11-07.md`（本片修订版，见其 §0） |
| 基线 | `f2d86b1a`（2026-11-25，含 #848 成本模型、#849 声明校验拆分） |
| 分支 | `feat/dnd2024-s0f31-spellbook-items` |
| 交付范围 | UMD 同调 + 充能免收、Ritual Adept、Memorize Spell、Evocation Savant；卷轴 `not_automated` |
| 状态 | 四项自动化交付完成；卷轴三项按 §1 停止条件保持非完成 |

## 1. 基线核对：任务书 §3 的三条"缺失"有两条已过时

开工前逐条探测，结论写进任务书 §0：

| 原判断 | 实测（`f2d86b1a`） |
|---|---|
| 无同调容量函数 | `character/attunement.py::attunement_capacity` 已读 `attunement_slot_bonus`，BASE 3 / MAX 10，只是全仓无声明使用 |
| 无充能管线 | `items/charges.py` 有 `spend_charges` / `should_check_destroy` / `destroy_check_events`；`item.charge_spent` 两处发出点 |
| 无 Spell Scroll 物品 | 目录文件有两条描述条目，**运行时 item 实体 115 条里没有卷轴**；条目无 `spell_ref` / 等级 / `activation` |

另两条本次新发现、直接影响排期的事实：

- **`ritual` 零实现**：全仓只有 `spells/selection.py` 一句注释，探索与战斗都没有仪式入口。
- **四条特性没有定义对象**：`classes.*.features` 里没有它们，`feature_value()` 读不到参数。所以第一步是补声明。

## 2. 交付内容与落点

| 项 | 落点 | 关键决定 |
|---|---|---|
| 四条特性声明 | `srd_class_features.json` | 参数只放随特性变化的数字（6、2、1、4） |
| UMD 同调 4 件 | 纯数据 | `attunement_capacity` 早就读参数，代码零改动 |
| UMD 充能 1d6 | `items/charges.py`、`combat/resolution.py`、`exploration/engine.py`、两处事件 schema | 免收发 `charge_waived`，**不发 `amount: 0` 的 charge_spent**（`spend_charges` 拒绝 0 价） |
| Ritual Adept | `exploration/engine.py`、`combat/validation.py` | 同一意图加 `ritual: true`，不新增意图类型；战斗显式拒绝 |
| Memorize Spell | `resting/engine.py`、`runtime.py` | 特性预算不走 `PreparationPolicy`；长休带该参数直接拒绝 |
| Evocation Savant | `spells/selection.py`、`progression/advancement.py` | 免费名额与正常增量两笔账、一份书目；`spellbook_additions` 取和 |
| 卷轴 | 无代码 | `not_automated`，两条用例锁边界 |

## 3. 验收矩阵逐项（任务书 §6，14 条）

| # | 内容 | 结果 |
|---|---|---|
| 1 | Thief 12/13 容量 3/4、非 Thief、跨职业不泄漏 | ✅ 4 条用例 |
| 1b | 短休同调交换、第五件拒绝、死亡解绑、读档一致 | ⬜ 既有 `test_dnd2024_attunement.py` 已覆盖，本片未重复 |
| 2 | 真实耗充能每次先掷 d6；6 免收；1–5 扣原价 | ✅ 3 条用例（探索路径） |
| 2b | 不耗充能路径零 RNG | ⬜ 逻辑上 `charge_waiver_roll` 只在 `maximum/charge_cost` 都在时调用，但未单独断言 RNG 次数 |
| 3 | 多充能只掷一次；免收不触发销毁；apply/replay 不重复扣费 | ⬜ 未覆盖（见 §5） |
| 4–5 | 卷轴戏法/1 环可靠施放、2+ 环 Arcana 检定 | ⛔ `not_automated`（缺物品身份） |
| 6 | 客户端伪造/缺系统 fail closed | 🟡 卷轴两条负例；`charge_waived` 的伪造未测 |
| 7 | 书中未准备 Ritual 可施放、不耗槽 | ✅ 正面 + 3 条负例（非 Ritual / 书外 / 非 Wizard） |
| 8 | 探索投影区分准备与仪式；战斗不压缩 | ✅ 投影两条 + 战斗拒绝一条 |
| 9 | 5 级短休换一个准备法术 | ✅ 正面用例 |
| 10 | 一次换多个/戏法/书外/非 Wizard 拒绝 | 🟡 书外、未准备、无特性已测；"一次换多个"与戏法未单独测 |
| 11 | Evoker 3 级两个免费、跨学派拒绝 | ✅ 正面 + 跨学派负例 |
| 11b | 0 环 / 3 环 / 重复项拒绝 | ⬜ 未单独测（等级下限由 `level < 1` 拦，但无用例） |
| 12 | 新开环阶 +1、未开不加 | 🟡 3 级 =2、4 级 =0 已测；5 级开 3 环的 +1 未测 |
| 13 | 免费名额不占正常两个、不自动 prepared、不扣 GP/时间 | 🟡 数量分离与 prepared 不变由用例隐含；GP/时间本层无机制 |
| 14 | 四特性在 advance/history/save-load/replay 一致 | 🟡 升级路径真实走通；history 与 replay 未单独断言 |

## 4. 验证输出

```
pytest tests/rulesets/test_dnd2024_spellbook_items.py     26 passed
pytest tests/rulesets/test_dnd2024_progression.py \
       test_dnd2024_feature_choices.py test_dnd2024_resting.py \
       test_dnd2024_choice_repair.py test_dnd2024_spellbook_items.py   125 passed
pytest tests/rulesets/test_dnd2024_item*.py test_dnd2024_tool_items.py 98 passed
ruff check src/ tests/                                    All checks passed
mypy                                                      102 files, no issues
```

## 5. 偏差与未决

**必须记住的三条**

1. **卷轴三项没有交付**，且不是"差一点代码"：运行时没有卷轴物品实体，条目也没有
   `spell_ref`。要做必须先有物品身份（谁装的哪个法术、几环、用后是否崩解），那是
   物品系统的事，不在本片白名单内。
2. **`charge_waived` 与 `spell.cast.ritual` 是新事件字段/类型**。两份 schema
   （`combat/reducer.py`、`exploration/engine.py`）已同步登记，但**前端与审计日志
   还没有消费它们**——目前只保证不破坏 apply。
3. **仪式的时间与组件没有强制**。探索层没有时钟可推，"10 分钟"只体现在"不花槽、
   必须读书里的那一条"；施法时间与材料消耗留给叙事层。这条写进任务书 §4.3 的
   偏差里，不能在验收时宣称"完整实现"。

**未覆盖但已知可行的**

- 多充能只掷一次、免收不触发销毁检查：代码上 `spent = 0` 会让 `remaining = current`，
  `should_check_destroy` 只在 `remaining == 0` 时为真，因此天然不触发；但缺用例。
- 5 级开 3 环的免费名额 +1：`free_spellbook_allowance` 的差集逻辑覆盖，缺用例。
- 一次短休换多个：`_plan_memorize_spell` 只接受一对（`replace_ref`/`with_ref`），
  提交两个需要两次休息；缺"多个"的负例。

**一次几乎交付出去的破坏性契约变更（必读）**

Evocation Savant 的第一版把免费名额**并进了** `spellbook_additions`（3 级从 2 变成 4），
于是所有既有升级夹具（只交正常那几个）立刻不合法：全量跑出 **214 failed / 37 error**，
集中在 paladin_auras、overchannel、hold_person、stoneskin、free_casting 等 12 个用
Wizard 升级路径的文件。

根因不是实现错，而是**语义错**：SRD 写的是 "you **can** add"，免费名额是可选的。
把它并进增量等于宣布"必须多加"，这是对既有契约的破坏。现在的做法：

- `spellbook_additions` 仍为正常值（2），`free_spellbook_picks` 单独给（2，表示上限）；
- 校验接受 `正常..正常+免费` 区间，多出来的部分才必须是 Wizard Evocation；
- 新增一条用例锁住"只交正常那两个也合法"。

教训：动 `requirements` 里的数字前，先问"这个字段是下限还是上限"。改成上限是兼容的，
改成下限会让所有既有调用方同时失效。

**与上游 GAP-43 栈的关系**

`_activate_item_events` 在栈顶分支里仍在 `resolution.py`（未搬走），所以本片的
充能改动不会因搬家失效；但 rebase 到栈合入后的 main 时会有行号冲突，属预期。
