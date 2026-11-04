# 切片执行报告 D2：同调系统（Attunement）

- 日期：2026-11-04
- 分支：`feat/dnd2024-2d-attunement`，基线 `34cbb98f`（D1 实现 head = `pr/1c` `e5ed9f89` + D1）；复审后通过 merge `3088963a` 同步 D1 新 head（含最新 `pr/1c`），修正提交 `77f7fd10`
- 任务书：`D2_ATTUNEMENT_TASK_SPEC_2026-11-03.md`
- 状态：实现与验收完成（T1–T18 全部有对应覆盖）；复审修正见 §10

## 1. 基线与行号漂移

任务书写明基线为“D1 合入后的 main”，实际 1C/D1 尚未合入：D2 分支按 D1 head `34cbb98f` 继续叠加（与 D1 相同策略，基于 `pr/1c` `e5ed9f89`）。复审时 `pr/1c` 已前进到 `9b2428e8`，D2 通过 merge `3088963a` 同步 D1 新 head（`733b5d05`），未 force push；合入顺序仍为 1C → D1 → D2。

任务书锚点行号以 `9c2ca59d` 为参照，执行时按当前代码重读并重新定位：

| 锚点 | 实际落点（当前分支） | 漂移 |
|---|---|---|
| A1/A2 | `character/derivation.py` `derive_armor_class` 及其两个调用点（建卡、`reconciliation._armor_rules`） | 无结构性漂移 |
| A3 | `derivation.py` canonical `derived` 段 | 已加入 `attunement_slots` / `attuned_refs` |
| A4/A5 | `reconciliation.py` `_armor_rules` 与幂等短路 | 短路条件扩展见 §4.1 |
| A7/A8 | `resting/engine.py` 短休 / 长休 | 短休新增可选参数；长休未改 |
| A9 | `runtime.py` `complete_rest` | 透传 `attunement` + 类型/长休校验 |
| A10/A11 | `combat/reducer.py` 两处死亡写入点；复活处理器不改 | 已接入清理；事件补录见 §4.2 |
| A14/A15 | bundle 物品 schema 校验；事件白名单 | 校验落在装备目录构建；事件登记见 §4.2 |

`.rulebooks` 在本执行环境不存在，无法回填 DMG2024 原文行号；未编造行号。

## 2. 实现摘要

新增 `src/rulesets/dnd2024/character/attunement.py`，作为同调唯一 owning module：

- `validate_item_declaration` / `requires_attunement`：`requires_attunement` 只允许 bool，缺失视为 false，其它类型 fail closed。
- `attuned_refs`：唯一门控读取入口；缺失/畸形段降级为空集并 warning，不把旧档视为已同调。
- `attunement_capacity`：基础 3 + 所有已获得特性的 `attunement_slot_bonus`（经 resolver 的 `features_for` / `feature_value`），范围 0–10，越界 fail closed。
- `attunement_state_for_projection`：live 只读投影；可选 `capacity` 供 reconciliation 注入。
- `count_requires_attunement_copies` / `owned_ref_counts`：装备 + 背包合计数量守卫。
- `apply_short_rest_attunement`：先解绑后绑定、全量校验后一次性写入；拒绝抛 `AttunementError`。
- `unattune_item`：非短休主动解绑，reason=manual。
- `clear_attunement_on_death`：清空 refs，返回被清列表。

接线：

- `derive_armor_class` 新增 `attuned_refs` 参数；护甲/盾的 `ac_bonus` 仅在已同调时计入，`ac_base`/DEX/`dex_cap` 照常。
- `derived` 新增 `attunement_slots`、`attuned_refs`、`attunement_pending`；建卡容量按派生计算、集合为空；canonical 新增 `attunement: {"refs": []}`。
- `reconciliation`：迁移缺失段；写回规范化段；live mirror 新增 `sheet["attunement"] = {refs, capacity, pending}`；`_live_projection` 同步镜像，避免重复投影多记 revision。
- 短休：`complete_short_rest(..., attunement=None)`；非法同调变更记录 `attunement_rejected` 审计事件，短休资源恢复不回滚。
- `runtime.complete_rest` 透传并做类型校验；长休携带 attunement 直接拒绝；新增 `runtime.unattune_item`。
- 死亡：`reducer.py` 两处写入点调用 `clear_attunement_on_death`；`combat/engine.py` 在批次后按死亡前后差异补 `unattune_item` 事件并写入 ledger，同时立刻 reconcile 该角色，使 AC 回落到未同调值。
- `Dnd2024EquipmentCatalog.from_bundle` 对每个 item 调用 `validate_item_declaration`，非法声明在目录构建时即失败。

## 3. 测试矩阵结果

新增 `tests/rulesets/test_dnd2024_attunement.py`，复审后 21 个用例；合成 fixture 只在测试内构造，未写入 SRD bundle。

| # | 结果 | 覆盖 |
|---|---|---|
| T1 | ✅ | 未同调 +1 链甲衫：AC 15（13+DEX2），无 +1；`derived.attunement_pending` 与 `sheet.attunement.pending` 含该 ref |
| T2 | ✅ | 短休绑定：AC 16、`derived.attuned_refs` 含 ref、events 含 `attune_item` |
| T3 | ✅ | 满 3 件再绑第 4 件：`attunement_rejected`、refs 不变 |
| T3b | ✅ | 旧档已有 5 件、容量仅 3：解绑 1 件仍成功（新增绑定才受容量约束），便于存量为失配档收敛 |
| T4 | ✅ | 基础 3；特性 `attunement_slot_bonus=3` → 4 件可绑（容量 6）；`"3"` 非整数与 9（容量 12）均抛 `AttunementError` |
| T5 | ✅ | 满 3 件同短休“解绑 A + 绑定 D”：原子完成，A 失效、D 生效，事件顺序 `unattune(swap)` → `attune` |
| T6 | ✅ | 重复绑定同一 ref：拒绝、canonical 不变、revision 不刷（复审新增断言） |
| T7 | ✅ | 解绑未同调 / 未持有 / 非魔法物品：分别拒绝；非魔法物品与绑定同因“does not require”拒绝，产生 `attunement_rejected` 审计事件（见 §4.3） |
| T8 | ✅ | 同款两件（两行或单行 `qty: 2`）：绑定与解绑均拒绝，reason 含 copies |
| T9 | ✅ | `runtime.unattune_item`：立即失效、无需短休、reason=manual、AC 回落 |
| T10 | ✅ | 活动战斗 `validate_rest_context` 抛“战斗进行中不能休息” |
| T11 | ✅ | HP 0 角色短休抛 `RestError`，同调不发生 |
| T12 | ✅ | 两条死亡路径（死亡豁免失败 / 0 HP 受伤）：refs 清空、每件一条 reason=death 事件、ledger 同步、AC 立即回落 |
| T13 | ✅ | 复活后 `dead` 清除、同调保持空、AC 保持未同调值 |
| T14 | ✅ | 无 `attunement` 段：空集 + warning + 不删装备 + 不自动同调；畸形 refs 同样规范化；非 dict 段在休息投影中降级为空集而不抛错（复审新增） |
| T15 | ✅ | 同调不变时 reconcile 返回 None（无 revision/日志）；换绑后 canonical 与 `derived.attuned_refs` 同步重算（复审新增断言） |
| T16 | ✅ | 同调与解绑全链路无 RNG；短休入口不接收也不消费随机数 |
| T17 | ✅ | 长休保留同调，仅恢复生命骰；长休携带 attunement（含空对象）拒绝，短休非对象拒绝 |
| T18 | ✅ | 既有 rulesets 全量回归零断言改动通过 |

## 4. 白名单符合性与偏差裁决

### 4.1 幂等短路

任务书要求“五元组：追加 previous_attuned”。实际比较为：`previous_refs`、`previous_ac`、`mirror_ac`、`previous_training`、`previous_attuned`、`previous_slots`、`previous_pending`、canonical 段规范化状态、mirror 同调投影。理由：

- 容量可随特性变化，不比较 `previous_slots` 会漏重算；
- `attunement_pending` 参与展示，必须一起比较；
- 旧档/畸形段需要一次性写回，否则“降级为空集”永远停留在内存。

未改变短路语义方向：一致则不写、不刷 revision。

### 4.2 死亡事件补录位置

任务书 §3.7 写“在 reducer 两处写入点追加事件”。reducer 是事件应用器，没有追加到批次的出口；实际裁决：

- 状态清理仍严格发生在 `reducer.py` 两处死亡写入点（不新增第三处）；
- `combat/engine.py` 在批次应用后比较死亡前后的 `attuned_refs`，按每件一条 `{"type":"unattune_item","item_ref":...,"reason":"death"}` 补入返回批次与权威 ledger；
- 同一循环内 reconcile 该角色，使 `armor_class` 立即回落到未同调值。

未触发任务书停止条件“死亡清理需要改到第三处”。

### 4.3 拒绝事件

任务书 §3.4 要求“events 中必须记录拒绝原因”，而 T7 措辞“不产生事件”指不产生 `attune_item`/`unattune_item`。实现按 §3.4 记录 `{"type":"attunement_rejected","item_ref":...,"reason":...}` 纯审计事件；不进入 canonical，不刷 revision。

### 4.4 事件白名单

- `runtime._recent_combat_events` 白名单加入 `attune_item` / `unattune_item`，字段白名单加入 `item_ref`。
- 两类事件纯审计、零状态；不会新增战斗 intent，也不改变 reducer 的 state 语义。

### 4.5 装备 schema 校验落点

任务书只要求导出 `validate_item_declaration`。实际在 `Dnd2024EquipmentCatalog.from_bundle` 构建物品类型表时逐条校验，使非法 `requires_attunement` 在规则集加载期 fail closed，而不是等某个消费者偶然读到。

## 5. 旧档迁移验证样本

测试 `test_old_malformed_and_current_saves_migrate_idempotently`：

1. 删除 canonical `attunement` 与 live 镜像 → 首次 reconcile 写入 `{"refs": []}`、pending 保留、返回非 None；
2. 第二次 reconcile 返回 None；
3. 把 refs 改成字符串 → reconcile 视为空集并写回规范段，第二次返回 None。

全程未删除装备或背包行。

## 6. 验证命令与结果

```bash
python -m pytest -q tests/rulesets/test_dnd2024_attunement.py      # 复审后 21 passed
python -m pytest -q tests/rulesets -k "rest or reconciliation or armor or combat or equipment"  # 复审后 281 passed
python -m pytest -q tests/rulesets                                    # 原执行：1083 passed
python -m pytest -q tests/test_rest_activity.py tests/test_assistant_party_rest_integration.py  # 11 passed
ruff check <changed files>                                        # All checks passed
```

全仓：见 §7（完成后回填；复审修正后未复跑全量）。

## 7. 全仓回归

```bash
python -m pytest -q --cov=src
# 8194 passed, 7 skipped, 1126 warnings in 1768.05s (0:29:28)
# Total coverage: 81.66%（要求 60%）
```

相对 D1 基线（8176 passed / 7 skipped / 81.64%）：新增 18 个用例全通过，无既有断言改动；覆盖率 +0.02pp。

> 以上为原执行提交 `a046e8c9` 的数据。复审修正 `77f7fd10` 后未复跑全量，已复跑聚焦 21 passed 与相关子集 281 passed；全量结论待 PR 前最终复跑。

## 8. 迁移债

| # | 债务 | 归属建议 |
|---|---|---|
| D2-1 | canonical 装备去重、同款多件不可表达；同调按 ref 记录，守卫按 `qty` 求和（单行 `qty: 2` 也拒绝） | canonical 装备 set → slot map 独立切片 |
| D2-2 | `attunement_restriction`（施法者/职业/生物类型限定）未落 | 阶段二对应内容需要时连同消费者落地 |
| D2-3 | 离开 100 尺 24 小时自动结束未落 | 战术层/世界时钟具备后 |
| D2-4 | 诅咒物品的“无法主动结束同调”未落 | 诅咒物品独立切片 |
| D2-5 | 激活与充能的同调门控未接线 | D3 必须消费 `attuned_refs` |
| D2-6 | 物品只有单个 `ac_bonus`，魔法盾的“基础盾加值 + 魔法加值”无法拆分；当前按任务书字面整段门控 | 魔法盾内容进场前补 `base/magic` 拆分或独立字段 |
| D2-7 | 主动解绑只有 `runtime.unattune_item`，无 webui route/服务入口；任务书裁剪了前端面板，真实玩家当前只能经短休换绑 | 交互切片补最小服务入口，或明确 runtime 为唯一入口 |
| D2-8 | 物品定义被内容删除后，围绕该 ref 的同调只能拒绝解绑，容量无法释放 | 内容迁移/删除流程需要清理同调段 |

## 9. 未闭环 / 后续

- PR 未创建：分支推送后补 PR body（本报告可整段引用）。
- 1C/D1 尚未合入；D2 以堆叠分支交付。复审时 D2 通过 merge `3088963a` 同步 D1 新 head（含最新 `pr/1c`），合入顺序仍为 1C → D1 → D2。

## 10. 复审修正（2026-11-04，`77f7fd10`）

1. **数量守卫（T8 缺陷）**：`owned_ref_counts` 原按“行”计数，单行 `qty: 2` 可绕过重复绑定守卫；现按 `qty` 求和，并新增单行两件的绑定/解绑拒绝用例。
2. **投影唯一入口**：`runtime._live_projection` 原直接读取 `canonical["attunement"]["refs"]`；现改经 owning module 的 `attunement_state_for_projection`。非 dict 段降级为空集，不再在短休/长休投影时抛 `AttributeError`（新增休息投影用例）。
3. **容量 0**：镜像容量不再被 `or 3` 吞掉；`derived.attunement_slots` 畸形值安全回退为 3。
4. **长休携带空对象**：`attunement={}` 现在与任何非 None 载荷一样拒绝；短休非对象仍拒绝。
5. **解绑文案**：未同调 / 未持有 / 非魔法物品分别给出准确 reason；新增非魔法物品解绑用例。
6. **测试强化**：T6 增加 revision 不刷断言；T15 增加 `derived.attuned_refs` 重算断言。
7. **验证**：聚焦 `test_dnd2024_attunement.py` **21 passed**；相关子集 **281 passed**；ruff 全绿；全量套件未在本次复审复跑。
8. **债务更新**：D2-1 明确为按 `qty` 求和；新增 D2-7（解绑入口）与 D2-8（内容删除后的同调残留）。
