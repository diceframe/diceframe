# 任务书：S0-MM5 Multiattack 组合池

| 项 | 值 |
|---|---|
| 切片 | S0-MM5（MM 线；接 S0-MM3b 的三原语与 S0-MM4 的缺口账本） |
| 载体 | `multiattack` 动作契约 + 导入器解析 + 目录条目 |
| 规则基准 | SRD 5.2.1（Multiattack 的各种写法） |
| 基线 | `7ee5c2fb`（v3.4.0）+ #900 |
| 状态 | 执行中 |

## 1. 为什么是这一条

#900 的账本里，CR ≤ 4 有 **30 只**因为 Multiattack 解析不出来而进不了库。按句式细分：

| 句式 | 只数 | 现有三原语能表达吗 |
|---|---|---|
| `N attacks, using A and B in any combination` | **17** | ❌ 需要"从集合里任意挑 N 次" |
| `makes N A or B attacks`（整组二选一） | 2 | ✅ `choice` 两个分支 |
| `makes N A attacks and uses X` | 5 | ❌ 多攻击里混入**非攻击动作** |
| `replace one attack with a use of X` | 3 | ❌ 同上 |
| 池 + `replace one attack with a B attack` | 1（Wereboar） | 池 + 约束 |
| 其余 | 2 | — |

本片只做前两类 + 池的约束字段，**共 19 只**；"多攻击里混入动作"是独立原语（GAP-52），它同时会解决另外 8 只。

## 2. 契约

### 2.1 动作新增 `pool` 形状

```json
"pool": {"attacks": ["scimitar", "pistol"], "count": 2,
         "at_least": [{"attack_id": "bite", "count": 1}],
         "at_most":  [{"attack_id": "gore", "count": 1}]}
```

- `attacks`：2–4 个本生物自己的攻击 id；
- `count`：1–`MULTIATTACK_MAX_ATTACKS`；
- `at_least` / `at_most`：可选约束，用于"两次里至少一次 Bite""最多一次 Gore（替换）"。
  导入器**只在能证明时**才写；证明不了就不导入（fail closed）。

与既有形状的关系：`repeat` = 池的退化（单元素、count=N）；`combo` = 全约束的池；`choice` = 分支。**不合并**它们：合并会让"哪一种"这个信息在数据里消失，而投影与 UI 都要用它。

### 2.2 意图新增 `sequence`

`type: "multiattack"` 的意图在 `pool` 形状下带 `sequence: ["scimitar", "pistol"]`：

- 长度必须等于 `pool.count`；
- 每个 id 必须在 `pool.attacks` 里；
- 必须满足 `at_least` / `at_most`；
- 顺序即结算顺序（逐次攻击、逐次掷骰）。

`repeat` / `combo` / `choice` 形状不接受 `sequence`（多余字段一律拒绝）。

### 2.3 展开

`_multiattack_parts` 对 `pool` 按 `sequence` **相邻同名合并**成 `(attack, count)` 对，下游结算逻辑不变。

### 2.4 投影

`available_intents` 的 `multiattack` 行在池形状下补 `pool: {"attacks": [{"id","name"}], "count", "at_least", "at_most"}`，让面板能渲染选择器而不是猜。

## 3. 导入器

`parse_multiattack` 增加两个分支：

- `... using A and B in any combination` / `using A or B in any combination` → `pool`（2 个 id、count 取自句首数字）；
- `makes N A or B attacks`（整组）→ `choice`，两个分支 `a×N` / `b×N`；
- `replace one attack with a § attack`（攻击而非"use of"）→ 池 + `at_most`；
- 含 `uses ` / `use of` / `Spellcasting` 的仍**不解析**（那是 GAP-52）。

## 4. 逐项测试

1. 池：`sequence` 合法 → 按顺序打两次，掷骰两次。
2. 池：`sequence` 长度不符 / 含池外 id / 重复超限 → 拒绝。
3. 池：`at_least` / `at_most` 违反 → 拒绝。
4. `repeat`/`combo`/`choice` 带上 `sequence` → 拒绝（字段闭集）。
5. 契约：`pool` 的 `attacks` 少于 2 / 引用本生物没有的攻击 → 目录加载失败。
6. 导入器：`in any combination` → pool；`N A or B attacks` → choice；`uses ` → 不解析（进 skipped）。
7. 目录：条目数 ≥ 171（152 + 19），且 `skipped` 减少 ≥ 19。
8. 投影：池形状的 `multiattack` 行带 `pool`。

## 5. 非目标

- **GAP-52**：多攻击里混入非攻击动作（`and uses X` / `replace ... with a use of X`），8 只。
- 池的前端选择器：本片只投影 `pool`，面板接线随页面改动一起做（若未做，面板仍可提交既有形状，池形状显示为不可选而不是错打）。
- CR 5+。

## 6. 独立审核与修复（2026-11-28）

审核逐条核过后，本文有三处**承诺与代码不符**，已按事实订正：

| 原文 | 实际 |
|---|---|
| §3 "`replace one attack with a § attack` → 池 + `at_most`" | **没有这个分支**。含 `replace` / `uses` 的句子现在整条不解析（此前会被当池收下、把"还能替换一次"吞掉——已修） |
| §3/§4.6 "`makes N A or B attacks` → `choice`" | 用了 `.match`，真实句子以 `"The X makes …"` 开头，永远匹配不到。改 `.search` 后 `warrior-veteran` 才真的进库 |
| §4.5 "引用本生物没有的攻击 → 目录加载失败" | 契约只看 id 形状，引用在展开时回答；测试按展开时拒绝写 |

§4.7 的"≥171 / skipped 减少 ≥19"也不成立：17 只 "in any combination" 里多数还有别的缺口，实际只进 2 只。

已修（代码）：池的 `sequence` 只允许池形状（`view.py` 显式拒绝）；导入器不再吞 `replace` 子句。

仍未做（登记）：池成员的引用**在契约层**核对（需要把 `attack_ids` 传进 `_validate_multiattack_action`）；`at_least`/`at_most` 的**聚合可行性**（`sum(at_least) <= count`、同一攻击上下界互斥）。
