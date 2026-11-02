# 1C Review 收尾执行报告

- 实际基线：HEAD=`ba3f6f5d099dc96323e13505e5181ca2359fe069`（分支 `backup/cfp`，与任务书 review 候选 `ba3f6f5` 一致）；未执行 fetch / push / GitHub review，本地无 `origin/main` 引用。
- 开工工作区：`clean=no`。R1–R10 的实现、测试与文档为本任务增量；未发现其他会话的在途修改。
- 语义锚点变化：无。任务书列出的 R1–R10 均在当前代码上可复现或可直接补证据。

## 修复前复现

将新增回归测试复制到 `ba3f6f5d` 的独立 worktree 运行，确认修复前失败：

| 范围 | 命令结果 | 关键失败 |
|---|---|---|
| R1–R4 | `10 failed, 92 passed` | `test_lucky_cancellation_uses_the_new_die_even_when_it_is_higher`、`test_lucky_cancellation_can_turn_the_new_die_into_the_critical`、`test_repeated_beams_on_one_defeated_target_grant_once[2/3]`、`test_failed_concentration_from_the_hit_removes_bless_before_the_counterattack`、`test_hide_invisibility_ends_with_the_combat`、`test_hide_invisibility_does_not_carry_into_the_next_combat`、`test_combat_end_clears_only_combat_and_turn_scoped_conditions` |
| R5 | `4 failed, 13 passed` | `test_legacy_untrained_armor_is_repaired_through_the_production_chain`（AC 18≠16）、`test_legacy_trained_armor_keeps_its_armor_class_and_gates`、`test_repeated_load_and_combat_start_do_not_append_state`、`test_legacy_companions_are_reconciled_or_left_untouched` |
| R6 | `KeyError: 'check_bonus'` | `test_legacy_bless_bonus_is_projected_once_as_check_bonus` |
| R8 | `KeyError: 'maximize_damage_available'` | `test_available_spells_expose_overchannel_only_for_eligible_casts` |

R7（窗口 spend UI）与 R9（窗口生命周期）在基线后端已可用；R7 的缺口是人类 UI，R9 为行为证据要求，按 §2.9 以真实测试证明“零迁移”，未新增迁移代码。

## 工作包

- [x] R1 Lucky：`_spend_check_window` 在优势与 Lucky 劣势抵消后以新骰作为重判 natural；中立路径继续取低。测试 `test_lucky_cancellation_uses_the_new_die_even_when_it_is_higher`、`test_lucky_cancellation_can_turn_the_new_die_into_the_critical`、`test_lucky_cancels_advantage_on_an_enemy_attack`，并保留 `test_straight_through_attacks_and_death_saves_keep_their_exact_dice`、`test_an_attack_roll_that_is_already_at_disadvantage_offers_no_lucky_window` 等直通 RNG 回归。
- [x] R2 Dark One's Blessing：多射线按“首次跨过击败边界”去重，去重键包含被击败目标；伤害事件不吞并。测试 `test_repeated_beams_on_one_defeated_target_grant_once[2/3]`、`test_a_later_beam_that_first_crosses_the_threshold_grants_once`。
- [x] R3 Retaliation：反击使用本次伤害与专注结算后的派生状态。测试 `test_failed_concentration_from_the_hit_removes_bless_before_the_counterattack`、`test_successful_concentration_from_the_hit_keeps_bless_for_the_counterattack`。
- [x] R4 Hide：`combat.ended` 清理全部 `duration == "combat"` 条件。测试 `test_hide_invisibility_ends_with_the_combat`、`test_hide_invisibility_does_not_carry_into_the_next_combat`、`test_combat_end_clears_only_combat_and_turn_scoped_conditions`。
- [x] R5 Armor Training：在 `combat.start` 前由 runtime 对 canonical party/companion 执行幂等 reconciliation，复用唯一派生公式；内容版本保持 `srd-5.2.1+r5`。测试 `test_legacy_untrained_armor_is_repaired_through_the_production_chain`、`test_legacy_trained_armor_keeps_its_armor_class_and_gates`、`test_repeated_load_and_combat_start_do_not_append_state`、`test_legacy_companions_are_reconciled_or_left_untouched`、`test_legacy_repair_keeps_the_content_version`。
- [x] R6 历史事件：公开投影把 `bless_bonus` 归一化为 `check_bonus`（旧形状无骰迹，不虚构 `check_bonus_rolls`），不改写 ledger；前端 `CombatLiveBar` 只在现有 `check_bonus` 上显示一次。测试 `test_legacy_bless_bonus_is_projected_once_as_check_bonus`、`test_current_check_bonus_and_zero_legacy_events_are_unchanged`、`CombatLiveBar.test.ts` 的 legacy 渲染用例。
- [x] R7 窗口 UI：`Dnd2024CombatPanel` 渲染 `check_window` / `damage_window` 的种类、骰迹、检定/伤害、资源与能力骰面，按服务端 `options` 提交 `spend` / `decline`；Shield / 触发法术 / Counterspell 特化 UI 不变。组件测试 `renders a check window and submits spend through the decision API`、`renders a damage window with its held damage and submits decline`、`does not show a window to a player who does not own it`；真实 UI 测试见下方 e2e。
- [x] R8 主动声明：attack / spell attack / death save 的 Lucky 优势、`cast_spell` 的 Overchannel 最大化、普通 `hide` action 均由服务端投影驱动。测试 `test_lucky_declaration_is_projected_on_attack_and_death_save_actions`、`test_lucky_declaration_is_projected_only_on_spell_attack_rows`、`test_declaring_lucky_without_a_pool_is_rejected_for_every_surface`、`test_available_spells_expose_overchannel_only_for_eligible_casts`。
- [x] R9 生命周期：`test_a_pending_check_window_survives_a_save_load_and_settles_once`、`test_a_pending_damage_window_keeps_its_follow_up_through_a_save_load`、`test_reset_drops_a_pending_window_roll_traces_and_reaction_locks`、`test_replaying_a_settled_window_intent_is_free`、`test_a_round_entry_restore_drops_the_window_and_a_replay_spends_once`、`test_a_legacy_save_without_pending_decisions_loads_and_still_opens_windows`。通用序列化已满足窗口 shape，无需迁移。
- [x] R10 交付：补齐 en / zh-CN class feature 与 Lucky feat overlay、canonical 资源 label、窗口/声明按钮与错误文案；更新 `docs/rulesets/dnd2024/AUTOMATION_COVERAGE.md`；PR 风险声明见文末。

## 验证

- 聚焦 pytest：`/tmp/dfvenv/bin/python -m pytest tests/rulesets/test_dnd2024_check_windows.py tests/rulesets/test_dnd2024_dark_ones_blessing.py tests/rulesets/test_dnd2024_retaliation.py tests/rulesets/test_dnd2024_cunning_action.py tests/rulesets/test_dnd2024_armor_training.py -q` → `119 passed in 21.92s`。
- 相关聚焦 pytest（feat/class/monk/windows）：`138 passed in 16.45s`。
- `tests/rulesets`：`1006 passed, 36 warnings in 474.44s`。
- ruff：`ruff check src/ scripts/e2e_dnd_window_fixture.py scripts/prepare_e2e_data.py` → `All checks passed!`。
- mypy：仓库配置目标 `mypy` → `Success: no issues found in 102 source files`。
- 全仓 pytest：`7980 passed, 13 failed, 7 skipped, 3 errors in 749.05s`。失败全部为既有环境/依赖问题，并已在 `ba3f6f5d` worktree 复现：
  - Docker launcher / data generation：容器要求 `cp311`，当前解释器为 `cp314`。
  - `tests/test_runtime_diagnostics.py::test_runtime_log_archive_contains_all_retained_logs_and_masks_secrets`：任务书记录的共同基线已知日志归档顺序失败，基线复现。
  - `tests/test_tts.py`：环境未安装 `edge_tts`。
  - 说明：初次运行时 `pydantic-ai-slim==1.107.7`（`requirements.txt` 已声明）缺失导致 assistant 模块 47 项失败并有一项永久阻塞；安装后全仓结果如上。
- locales/typecheck/lint/vitest/build：`locales:check` 通过（核心 en/zh-CN 2830 keys；社区语言既有缺口不变）；`typecheck` 通过；`lint` 通过；`npm test` → `206 files / 1412 tests passed`；`npm run build` 通过。
- focused e2e：`run_e2e.py e2e/dnd2024-workspace.spec.ts --project=desktop -g "Lucky spend|Cutting Words damage window"` → `2 passed (19.5s)`。
- 完整 workspace spec：`run_e2e.py e2e/dnd2024-workspace.spec.ts` → `20 passed, 2 failed`；失败仅为既有 `guided creation`（桌面/移动），在 `ba3f6f5d` worktree 同样失败。
- browser smoke：`admin-ui / adventure-panel / dnd2024-workspace / flows / lorebook-golden / peer-connect / responsive / shell` → `103 passed, 3 failed, 4 skipped`。失败分类：
  - `guided creation` 桌面/移动：`ba3f6f5d` 基线 smoke 同样失败（`100 passed, 2 failed, 4 skipped`）。
  - `responsive.spec.ts` “phone character actions can scroll clear of bottom navigation”：本任务未改动该页面；当前树与基线树单独运行均通过，基线完整 smoke 未复现，属既有顺序/时序 flake。
- 前端分块体积门禁 `scripts/rulesets/check_frontend_chunks.py` 仍报告 `Dnd2024CombatPanel` 等超预算；`ba3f6f5d` 基线同样失败（战斗 76.49 KiB > 24 KiB），本次 UI 增量使其变为 83.43 KiB。该门禁不在 §5 验收命令内，记录为既有问题与既有文档数字过期，不通过删减功能规避。

## 最终审查

- changed files：见下方“提交与 changed-file 清单”；`git status --short` 为空。
- 公开契约/迁移变化：
  - 新增公开投影字段 `lucky_advantage_available`（attack / death_save / spell_attack 行）、`maximize_damage_available`（法术行）；`combat.actors[].class_resources` 纳入已拥有的起源专长资源池。均为 additive，不改变既有字段语义。
  - 决策事件 shape、通用事件 schema、storage/codec、rollback/replay 与多人权限契约未改；无需数据迁移，内容版本保持 `srd-5.2.1+r5`。
  - 公开说明：`docs/rulesets/dnd2024/AUTOMATION_COVERAGE.md`；未链接 `docs/internal/`。
- 停止条件：
  - §7.2.3 命中：通用 rollback 不恢复 `ruleset_character` 中的职业/专长资源，若为窗口 abort 补资源恢复需改 generic rollback 语义，超出本任务 owning 边界。未实施，改为保留 `test_replaying_a_settled_window_intent_is_free` 证明幂等重放，并在此登记为残余风险。
  - 其余 §7.2 条件未命中。
- 残余风险/迁移债：回滚中的窗口资源恢复仍由通用 rollback 边界缺失（同上）；Docker/edge-tts/日志归档/分块门禁为环境或既有问题，不阻塞本任务完成定义。
- 最终 changed-file 与运行时产物检查：`git diff --check` 通过；无 lockfile、`static-v2` 构建产物、日志或测试缓存进入 changed-file list；公开文档无 `docs/internal/` 链接。
- 独立 review：独立审查代理（只读）于 2026-11-02 给出 FAIL，唯一问题是 R6 兼容层虚构 `check_bonus_rolls`；提交 `52617c5d` 修复后复审 PASS。审查同时确认公开文档不应使用内部切片代号，已在 `7947d2e8` 修正。执行模型不声称 upstream approve。

### 提交与 changed-file 清单

提交（基于 `ba3f6f5d`）：

1. `a313bfaf` `fix(dnd2024): correct 1C cross-feature combat resolution`（R1–R4）
2. `946c2fbb` `fix(dnd2024): preserve legacy character and combat event behavior`（R5/R6）
3. `4b329cb4` `feat(frontend): expose 1C combat decisions and declarations`（R7–R9）
4. `8954bdd6` `docs(dnd2024): close 1C compatibility and rollout notes`（R10）
5. `52617c5d` `fix(dnd2024): stop fabricating legacy check bonus rolls`（独立 review 修复）
6. `7947d2e8` `docs(dnd2024): clarify legacy bonus projection in public coverage`（独立 review 修复）

changed-file（A=新增，M=修改）：

```text
A docs/internal/design/1C_REVIEW_REMEDIATION_EXECUTION_REPORT_2026-11-02.md
A docs/internal/design/1C_REVIEW_REMEDIATION_TASK_SPEC_2026-11-02.md
M docs/rulesets/dnd2024/AUTOMATION_COVERAGE.md
M frontend-v2/e2e/dnd2024-workspace.spec.ts
M frontend-v2/src/api/types.ts
M frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue
M frontend-v2/tests/CombatLiveBar.test.ts
M frontend-v2/tests/Dnd2024CombatPanel.test.ts
A scripts/e2e_dnd_window_fixture.py
M scripts/prepare_e2e_data.py
M src/rulesets/dnd2024/character/reconciliation.py
M src/rulesets/dnd2024/combat/check_windows.py
M src/rulesets/dnd2024/combat/engine.py
M src/rulesets/dnd2024/combat/reaction_spells.py
M src/rulesets/dnd2024/combat/reducer.py
M src/rulesets/dnd2024/combat/resolution.py
M src/rulesets/dnd2024/combat/triggered_spells.py
M src/rulesets/dnd2024/combat/view.py
M src/rulesets/dnd2024/features/resolver.py
M src/rulesets/dnd2024/runtime.py
M templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json
M templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json
A templates/rulesets/dnd2024_srd/locales/en/feats/lucky.json
M templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json
A templates/rulesets/dnd2024_srd/locales/zh-CN/feats/lucky.json
M tests/rulesets/test_dnd2024_armor_training.py
M tests/rulesets/test_dnd2024_campaign.py
M tests/rulesets/test_dnd2024_check_windows.py
M tests/rulesets/test_dnd2024_cunning_action.py
M tests/rulesets/test_dnd2024_dark_ones_blessing.py
M tests/rulesets/test_dnd2024_die_manipulation.py
M tests/rulesets/test_dnd2024_retaliation.py
M tests/rulesets/test_dnd2024_smite.py
```

修复后复核：`tests/rulesets` → `1006 passed`；`test_dnd2024_campaign.py` → `32 passed`；`ruff check src/rulesets/dnd2024/runtime.py` → `All checks passed!`。

## PR 风险声明

- **Ruleset Mechanics**：Lucky 抵消语义、多射线首次击败去重、Retaliation 伤害后状态、combat-scoped 条件清理、窗口与主动声明均为 D&D 2024 ruleset owning module 内的权威结算；generic engine 未新增具体特性分支。
- **Persisted Data**：pending `check_window` / `damage_window` 沿用现有序列化形状；save/load、reset、重放测试通过。未新增持久化字段。
- **Migration**：无数据迁移。旧角色 Armor Training 在第一次 `combat.start` 前幂等重算；历史 `bless_bonus` 只在公开投影归一化，不改写 ledger。
- **Compatibility / Breaking**：新增 additive 投影字段；旧角色、旧事件、旧存档均可继续读取。无 breaking API 变更。
- **Multiplayer / Turn Authority**：窗口仍只对 `assigned_to` 的拥有者展示与结算；客户端只提交既有 decision option，不掷骰、不扣资源；GM `gm_pending` 与 AI 决策路径不变。
- **Architecture**：改动留在 ruleset combat/character owning module 与前端 ruleset 面板；未穿透私有 API，未把 `WebAPI` 当 service locator。
- **UI only**：新增窗口与声明控件、核心 zh/en 文案；Shield / 触发法术 / Counterspell 特化交互保持原样。
