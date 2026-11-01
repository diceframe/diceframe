# 切片 R1 任务书：复活家族探索层入口（revivify 战斗外 + 家族空响止血）

版本：v1.1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：P9 已交付并提交（`db48c7ef`）——combat catalog `revive` 模式、`dnd2024.character.revived` 事件与 reducer 处理器（清 dead/unconscious/stable/death_saves 四状态、hp=max(1,hp) clamp、仅 player、**不依赖 combat state**）。本切片复用该契约，reducer 零改动。
背景：P9 后体验审查确认两个缺口——B：战斗外 revivify 被探索层显式拒绝（工程术语消息）；C：复活家族法术无 combat 效果条目 → 探索层叙事兜底**扣法术位但 canonical 不动**（静默失败）。本切片同时修复两者。

---

## 1. 目标

1. **revivify 战斗外可施放**（玩家自助）：探索层 `cast_spell` 新增 revive 支持，对同队 dead player 产出 `spell.cast` + `dnd2024.character.revived{hp:1}`，复用 P9 reducer 落地。
2. **复活家族不再空响**：建立探索层 revive 语义表，五法术（revivify / raise_dead / reincarnate / resurrection / true_resurrection）全部有条目；结算分支实现子集，**遇未实现语义 → 显式拒绝 + 不扣法术位 + 玩家可读消息**，不再落入叙事兜底。
3. 战斗内 revivify（P9 闭环）与探索层 healing/buff 既有行为零回归。

## 2. 已核实事实（直接引用，不要重造）

- 探索层 `cast_spell` 现状（`src/rulesets/dnd2024/exploration/engine.py` L206-317）：有 combat 效果条目但 mode 不在探索支持集（healing/buff）→ 显式拒绝 `no exploration-safe deterministic effect`；无效果条目 → 叙事兜底（扣位 + `spell.cast` + `spell.narrative`）。
- revivify 的 combat 条目（`srd_combat_core.json`）：`{"mode": "revive", "range": 5, "target_count": 1}`——P9 产物，**本切片不动它**。
- GM 复活命令（`src/commands/round_effects.py` `apply_revive_commands`）在非战斗上下文测试通过（`test_dnd2024_narrative_health.py`）——但它走 legacy 链路，**不经过探索层**。探索层有独立的事件落地器 `_reduce_event`（v1.1 修正：v1 误写为「证明 reducer 处理器战斗外可用」；实际需要镜像处理器，见 §8.5 偏差 1）。
- 五法术元数据在 `srd_spells.json` 已存在；本切片**不改该文件**。
- 探索层无动作经济，只有法术位消耗；无位置/射程概念（战斗外 revive 不校验 5 尺）。

## 3. 白名单

- `src/rulesets/dnd2024/exploration/engine.py`：revive 校验与结算挂接。
- `src/rulesets/dnd2024/exploration/revive_effects.py`（新建）：revive 语义表 + 结算辅助。
- `tests/rulesets/test_dnd2024_exploration_revive.py`（新建）。

**禁止**：改 combat catalog / reducer / resolution / validation / P9 任何文件；改 `srd_spells.json`；给探索层加硬编码法术拒绝清单（语义表即唯一数据源）；把未实现语义 silently 降级为部分生效；改探索层非 revive 路径的既有消息。

## 4. 语义表与实现子集

`revive_effects.py` 内置常量表（五法术、规则书语义稳定；为 5 条数据新建模板 catalog 机制属过度工程，未来探索效果增多再外移，执行报告记录此决策）：

| 法术 | 语义 | 第一版实现 |
|---|---|---|
| revivify | `{hp: 1}` | ✅ 完整实现 |
| raise_dead | `{hp: 1, cleanse: ["poisoned"], penalty: "minus4_rest_decay"}` | hp+cleanse 可实现；**penalty 依赖通用 D20 聚合（SP10）与长休衰减钩子——未就位则整法术显式拒绝**，不得漏惩罚放行 |
| resurrection | `{hp: "full", cleanse: ["poisoned"], penalty: "minus4_rest_decay", caster_cost: "..."}` | 同上，penalty/caster_cost 未就位则拒绝 |
| reincarnate | `{new_body: true}` | 显式拒绝（角色重建超出本切片） |
| true_resurrection | `{hp: "full", summon_by_name: true}` | 显式拒绝（唤名目标不在实体册，永久 GM 通道） |

判定顺序：条目存在 → 检查语义是否全部在已实现集合内 → 有未实现语义即拒绝（消息指出该法术形式尚未支持），全实现才结算。**hp: "full" 已兼容**（reducer 的 max(1,hp)+max_hp clamp），实现集合含 hp 1/full 与 cleanse poisoned。

## 5. 校验与结算规则

- 目标：同队、player、`conditions.dead` 存在；dying（0 HP 未 dead）拒绝——与 P9 口径一致。
- 资源：消耗对应环位（revivify 3 环、raise dead 5 环、resurrection 7 环），无位拒绝；**显式拒绝的路径一律不扣位**。
- 事件序：`spell.cast` → `dnd2024.character.revived{actor_id 目标, hp}`；cleanse 实现时先 `condition.removed`（poisoned）再 revived 或按 reducer 契约要求排序，以测试锁定。
- 窗口（1 分钟/10 日/100 年）、耗材、老死、遗体状态：维持 GM 叙事裁定，本切片不做任何机械判定（既定口径：无数据放行，不 fail closed）。
- 错误消息：revive 路径的拒绝用简洁玩家可读英文（如 `"revivify requires a dead ally"`、`"this form of revival is not supported yet"`），不用工程术语。

## 6. 测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| 1 | 战斗外 revivify 复活 dead 队友 | hp=1、四状态清理、3 环位消耗、事件序 spell.cast→character.revived、无 combat state 下正常落地 |
| 2 | dying / 存活 / 敌方目标 | 均拒绝且不扣位 |
| 3 | 法术位不足 | 拒绝且不扣位 |
| 4 | reincarnate / true_resurrection | 显式拒绝、不扣位、可读消息、无 spell.narrative 兜底 |
| 5 | penalty 依赖未就位时的 raise_dead / resurrection | 显式拒绝、不扣位（若依赖已就位则改为完整实现断言：hp、cleanse、惩罚 condition） |
| 6 | P9 战斗内 revivify 回归 + 探索层 healing/buff 回归 | 既有测试不动、保持绿 |

## 7. 验收

1. 新测试全绿；2. `pytest tests/rulesets/test_dnd2024_revivify.py tests/rulesets/test_dnd2024_exploration_cast.py -q`（若后者不存在以实际探索层施法测试文件为准）回归绿；3. 全量 `pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py` 零失败。
4. 执行报告写入本文件 §8：变更清单、矩阵逐项结果、penalty 依赖就位状态、语义表内置决策记录、遗留。

## 8. 执行报告

执行时间：2026-10-30。基线：P9 已提交（`db48c7ef` feat / `6aa87b55` docs）。

### 8.1 变更清单（均在 §3 白名单内）

- 新建 `src/rulesets/dnd2024/exploration/revive_effects.py`
  - `REVIVE_SEMANTICS`：五法术语义表（唯一数据源）。
  - `IMPLEMENTED_KEYS = {"hp", "cleanse"}`、`IMPLEMENTED_CLEANSES = {"poisoned"}`。
  - `is_fully_implemented`：任一语义键不在实现集合即整体拒绝。
  - `revive_events`：cleanse 的 `condition.removed` 先于 `dnd2024.character.revived`；`hp: "full"` 解析为目标 `max_hp` 整数。
- `src/rulesets/dnd2024/exploration/engine.py`
  - `_validate`：revive 分支先判实现子集（未实现 → `UNSUPPORTED_MESSAGE`），再判「同队 player + dead」；所有拒绝发生在事件构造前，因此不扣位。
  - `resolve_intent`：`spell.cast` 后走 revive 结算（`spell.slot_spent` → `spell.cast` → `character.revived`），不发 `spell.narrative`。
  - `_reduce_event`：新增 `dnd2024.character.revived`（仅 player、hp≥1 且 ≤max_hp、清 dead/unconscious/stable/death_saves）。
- 新建 `tests/rulesets/test_dnd2024_exploration_revive.py`（10 用例）。
- 未改 combat catalog / combat reducer / resolution / validation / `srd_spells.json`；未给探索层加硬编码法术拒绝清单。

### 8.2 矩阵逐项结果

| # | 结果 | 证据 |
|---|---|---|
| 1 | 通过 | `test_revivify_revives_a_dead_player_out_of_combat`：事件序 slot_spent→cast→revived、hp=1、四状态清空、3 环位 1→0、`combat.status == "none"` |
| 2 | 通过 | dying / living / enemy 三用例均 `revivify requires a dead ally`，且 slot 不变 |
| 3 | 通过 | `the selected spell slot is not available`，slot 不变 |
| 4 | 通过 | reincarnate / true_resurrection 均 `UNSUPPORTED_MESSAGE`、无 events、slot 不变 |
| 5 | 通过 | raise_dead / resurrection 均 `UNSUPPORTED_MESSAGE`、无 events、slot 不变；`is_fully_implemented` 断言为 False |
| 6 | 通过 | 既有 `tests/test_dnd2024_exploration.py` 6 用例、`tests/rulesets/test_dnd2024_revivify.py` 10 用例全绿 |

### 8.3 penalty 依赖就位状态

- `penalty: "minus4_rest_decay"`：**未就位**（无通用 D20 聚合 SP10、无长休衰减钩子）→ raise_dead / resurrection 整法术显式拒绝。
- `caster_cost`：同族未就位。
- `new_body`（reincarnate）：角色重建未就位 → 显式拒绝。
- `summon_by_name`（true_resurrection）：实体册无唤名目标 → 显式拒绝。
- 当前唯一全实现法术为 revivify；`hp: "full"` 机制已就绪（按目标 `max_hp` 转整数），待 penalty/caster_cost 就位后即可启用。

### 8.4 语义表内置决策记录

五条数据内置于代码常量，不新建模板 catalog：为 5 行数据引入 schema、loader、迁移面属过度工程。未来探索效果面扩大再外移；届时语义表仍是唯一数据源，不得改为分散的硬编码拒绝分支。

### 8.5 偏差、数据缺口与遗留

- **偏差 1（架构）**：探索层有独立 `_reduce_event`，并非复用 combat reducer。故 `character.revived` 处理器镜像在 `exploration/engine.py`（白名单允许），未改 combat reducer；语义与 P9 一致。
- **偏差 2（契约）**：§4 称 reducer 已兼容 `hp: "full"`；实测 combat reducer 执行 `int(event.get("hp"))`，字符串会抛错。已由 `revive_events` 将 `"full"` 解析为目标 `max_hp` 整数后发出，未改 reducer。
- **数据缺口**：死亡时刻、死因、遗体状态仍无 canonical 字段——窗口/耗材/老死/遗体维持 GM 叙事裁定（与 P9 v1.1 口径一致，无数据放行、不 fail closed）。
- **遗留**：raise_dead / resurrection 待 SP10 与长休衰减钩子；reincarnate 待角色重建；true_resurrection 待实体册唤名。
- **验收**：定向回归 `test_dnd2024_exploration_revive.py + test_dnd2024_revivify.py + tests/test_dnd2024_exploration.py` = **26 passed**；全量结果见 §8.6。
- **工作树**：R1 改动之外另有其它任务的既有未提交改动（`reaction_spells.py`、`view.py`、`test_dnd2024_stoneskin.py`、`srd_spells.json`、P2/P4–P8 任务书），`git diff` 核实均为行尾差异（0 内容变更），本切片未触碰。

### 8.6 全量验收

`pytest tests/rulesets -q --deselect tests/rulesets/test_dnd2024_character_builder.py`：**623 passed, 39 deselected, 0 failed**（= P9 基线 613 + R1 10），执行端 `.venv` 环境运行（13:48 启动，14:09 完成写入缓存）。

审核端取证说明：执行端起了两个相同命令的进程写同一 `.pytest_cache`，`lastfailed` 中 rulesets 范围（deselect 除外）失败为 **0**——cache 内 `tests/architecture/`、`character_builder` 等条目为历史运行与范围外残留（pytest 只清除本次跑过且通过的条目）；`nodeids` 计数因双进程竞争混入历史子集运行，不作精确证据。定向复跑（审核端 `.venv`）：`test_dnd2024_exploration_revive.py` + `test_dnd2024_revivify.py` 20 passed + `tests/test_dnd2024_exploration.py` 6 passed = 26 passed，与执行端报告一致。

## 9. 修订记录

- v1（2026-10-30）：初版。源起 P9 后体验审查（场合 B 硬拒绝 / 场合 C 扣位空响）与用户裁定的分界线——确定性结算机械化、事实裁定归 GM。
- v1.1（2026-10-30）：审核修正 §2 事实误判——GM 复活命令走 legacy 链路不经过探索层，探索层有独立 `_reduce_event`，需镜像处理器（执行端偏差 1 的正确处理依据）。实现与测试不受影响。
