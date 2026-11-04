# D1 物品身份与装备闭环 · 执行报告

- 对应任务书：`D1_ITEM_IDENTITY_TASK_SPEC_2026-11-03.md`
- 执行日期：2026-11-04
- 代码分支：`feat/dnd2024-1d-items`（叠加在 `pr/1c` 之上）
- 提交：`e9ecb11b`（实现）、`34cbb98f`（验收补齐）
- 状态：**实现与验收完成**（2026-11-04；全量套件、T12 与 browser smoke 均已闭环，见 §7）

## 1. 基线与行号漂移

| 项 | 值 |
|---|---|
| 任务书声明锚点基线 | `9c2ca59d` |
| 实际执行基线（PR #646 head） | `e5ed9f89` |
| 上游 main | `fae083bc`（已被 `pr/1c` 包含，无需 rebase） |

`e5ed9f89` 相对 `9c2ca59d` 只多一条提交（fighting style 构建期可选），未触碰 D1 白名单内任何文件，**锚点未漂移**。逐文件核对结果：

- `combat/catalog.py`：`from_bundle` 校验段与任务书 A4 描述一致，仅新增字段校验，未改动既有 `DICE_RE` / `range` / `mastery` 语义。
- `combat/validation.py`：`_declared_attack` 与 `_validate_lucky_spend` 位置未变，`two_handed` 校验插在两者之间。
- `character/reconciliation.py`：`_resolve_item_id` 的优先级顺序已满足任务书 §3.9 的诉求，**本次零改动**（见 §5 偏差 1）。
- `content/contracts.py`：`validate_item_record` 的 5 字段白名单未变，只新增第 6 个可选字段。

## 2. SRD 装备表计数复核

按 SRD 5.2.1 装备表逐条核对后的实际结果：

| 类别 | SRD 5.2.1 | 基线（`e5ed9f89`） | 本次后 | 差值 |
|---|---|---|---|---|
| 武器 | 38 | 15 | **38** | 0 |
| 护甲（不含盾） | 12 | 4 | **12** | 0 |
| 盾 | 1 | 1 | **1** | 0 |
| 护甲类合计（含盾） | 13 | 5 | **13** | 0 |
| 物品记录总数 | — | 50 | **81** | +31 |

与主计划的差值：主计划原写「护甲 14、仓库 6/14」，实测 SRD 为 13（12 护甲 + 1 盾），基线为 5/13。**主计划该数字已在同批文档中纠正为 13**，任务书 §3.1 要求的「执行侧重算并登记差值」在此闭环。

新增 23 件武器：battleaxe、blowgun、club、crossbow_hand、crossbow_heavy、crossbow_light、dart、glaive、greatclub、halberd、lance、light_hammer、maul、morningstar、musket、pike、pistol、rapier、sling、trident、war_pick、warhammer、whip。
新增 8 件护甲：padded、hide、scale_mail、breastplate、half_plate、ring_mail、splint、plate。

## 3. 白名单符合性（逐文件）

| 文件 | 实际改动 | 是否符合 |
|---|---|---|
| `content/items/*.json` | 新增 31 件（23 武器 + 8 护甲） | ✅ |
| `locales/{en,zh-CN}/items/*.json` | 各新增 31 份，只含显示字段 | ✅ |
| `content/combat_catalogs/srd_combat_core.json` | 武器 15→38；7 件补 `damage_two_handed` | ✅ |
| `combat/catalog.py` (+34) | `damage_two_handed` 骰式校验 + 双手骰必须严格大于单手骰 | ✅ |
| `combat/resolution.py` (+4) | 按 `two_handed` 选择 `damage_two_handed` 或 `damage` | ✅ |
| `combat/validation.py` (+31) | `two_handed` 声明校验：武器须声明该词条，副手（含盾）必须空置 | ✅ |
| `content/contracts.py` (+24) | 新增可选 `ruleset_item_ref`（`item:<id>` 格式），未知字段仍拒绝 | ✅ |
| `content/rewards.py` (+8) | 奖励意图透传 `item_ref` | ✅ |
| `webui/api.py` (+22) | `item_grant` 任务透传 `item_ref` | ✅ |
| `webui/services/characters.py` (+22) | `grant_reward` / `grant_classified_item` 透传，写入背包行 | ✅ |
| `commands/state_items.py` (+76) | 背包合并键含 `item_ref`；取件优先按 ref，按名匹配保留 | ✅ |
| `character/reconciliation.py` | **零改动** | ⚠️ 偏差 1 |
| `tests/rulesets/test_dnd2024_item_identity.py` | 新增 9 例 | ✅ |
| `tests/rulesets/test_dnd2024_equipment_data.py` | 新增 47 例 | ✅ |
| `tests/rulesets/test_dnd2024_weapon_mastery.py` (+25) | 精通表补齐 23 件新武器 | ⚠️ 偏差 2 |
| `commands/player_state_applier.py` (+2) | 装备操作透传 `item_ref` | ⚠️ 偏差 3 |

## 4. 测试矩阵 T1–T15 逐条结果

验收命令与输出：

```
pytest -q tests/rulesets/test_dnd2024_item_identity.py \
          tests/rulesets/test_dnd2024_equipment_data.py \
          tests/rulesets/test_dnd2024_weapon_mastery.py \
          tests/rulesets/test_dnd2024_live_equipment_reconciliation.py
→ 102 passed in 11.10s
（9 + 49 + 16 + 28；`34cbb98f` 后 equipment_data 增加 T12 两例）

pytest -q tests/rulesets
→ 1065 passed in 393.74s
```

| # | 覆盖用例 | 结果 |
|---|---|---|
| T1 | `test_reward_bridge_carries_identity_into_the_backpack` | ✅ 背包行获得 `item_ref`，未写入任何机械字段 |
| T2 | `test_reconcile_is_idempotent_for_identity_and_revision` | ✅ 二次 reconcile 不刷 revision、不改 `type`/`slot` |
| T3 | `test_unknown_ruleset_ref_warns_without_guessing_or_deleting` | ✅ 只 warning，不猜、不删、不进装备投影 |
| T4 | `test_content_pack_item_rejects_mechanics_and_bad_refs` | ✅ 机械字段与非法 `item_ref` 均 `CatalogContractError` |
| T5 | `test_same_name_different_identity_never_merges_or_guesses` | ✅ 同名不同 ref 不合并，带 ref 奖励不被无 ref 旧行吸收 |
| T6 | `test_display_name_is_never_consulted_after_identity_is_known` | ✅ 已知身份后不再读显示名 |
| T7 | `test_new_weapon_is_a_usable_catalog_entry` / `test_the_srd_weapon_table_is_complete` | ✅ 38 件逐件可装载，`damage` 合 `DICE_RE`，`mastery` 命中注册表 |
| T8 | `test_new_armor_derives_the_srd_armor_class` / `test_armor_without_category_still_fails_closed` | ✅ 12 件 AC 正确；缺 `category` 派生期抛错 |
| T9 | `test_strength_requirement_reduces_speed_and_stealth_is_projected` | ✅ 力量不足 → 速度 −10；隐匿劣势投影正确 |
| T10 | `test_two_handed_swaps_to_the_bigger_die` / `test_two_handed_is_rejected_when_the_other_hand_is_busy` | ✅ 单手 1d8、双手 1d10；持盾或副手有武器时拒绝 |
| T11 | `test_impossible_two_handed_declarations_fail_closed` | ✅ 非两用武器携带、骰不再变大、非法骰式均装载失败 |
| T12 | `test_two_handed_critical_doubles_the_swapped_dice` / `test_two_handed_die_feeds_the_maximize_pipeline` / `test_two_handed_attack_keeps_the_conditional_check_bonus` | ✅ 骰式切换、重击翻倍（`[10,10]`）、S-P2 最大化（单手 8 / 双手 10 / 重击后双手 20）与 S-P10 祝福 d4 加值均断言 |
| T13 | `test_every_new_record_has_a_display_only_locale` | ✅ 31 件 en/zh-CN 齐全且只含显示字段 |
| T14 | `test_loot_to_equipment_to_combat_numbers_end_to_end` | ✅ 战利品入包 → 装备 → AC 18 / `armor_training.speed_penalty` 10 |
| T15 | 既有 `test_dnd2024_live_equipment_reconciliation.py` 28 例 | ✅ 零断言改动通过 |

## 5. 偏差与裁决

**偏差 1 — `character/reconciliation.py` 未改动（任务书 §3.9 要求改）。**
核到 `_resolve_item_id` 已实现「显式 `item_ref` 优先，其次 `item_key`，最后显示名」的契约（同文件 L150–157 注释即此意），本次只需在更上游把 `item_ref` 写进行上即可，函数本身无需改。
裁决：**接受偏差**，D1 的效果由 T1–T3、T6 覆盖。若后续切片要改该优先级，须重开此条。

**偏差 2 — `test_dnd2024_weapon_mastery.py` 改了既有测试文件（任务书 §3.10 只列两个新文件）。**
该文件里 `MASTERY_BY_WEAPON` 是硬编码的精通表，武器从 15 扩到 38 后该表必然不全，测试会红。改动只是**追加 23 件条目**，未改任何断言语义。
裁决：**接受**，符合「只允许追加用例」的约束。任务书 §3.10 漏列此文件，属任务书缺陷，已在主计划登记。

**偏差 3 — `commands/player_state_applier.py` +2 行（不在 §3.8 白名单）。**
`equip_owned_item` 新增 `item_ref` 关键字参数后，`player_state_applier` 的调用点必须同步透传，否则装备操作仍然丢身份。
裁决：**接受**，这是 §3.8 改动的必要连带。任务书白名单不完整。

**偏差 4 — T12 初期只覆盖部分断言。**
已由 `34cbb98f` 补齐 S-P2 骰最大化与 S-P10 条件加值断言，闭环；见 §7。

## 6. 迁移债（本切片产生，后续切片不得顺手处理）

| # | 债务 | 归属 |
|---|---|---|
| D1-1 | `loading` 词条未落（无多次攻击消费者） | Extra Attack 切片落地后回补 |
| D1-2 | 弹药计数未落，远程武器仍抽象弹药 | D3 消耗品计数 |
| D1-3 | `two_handed` 装备门控未落（当前只校验副手空置，未覆盖「双手武器占用双手」的持久状态） | 独立装备门控切片 |
| D1-4 | 多币种 CP/SP/EP/GP/PP 未落 | 独立货币切片 |
| D1-5 | canonical 装备 `dict.fromkeys` 去重，同款多件塌成一件 | D2 同调的 set→slot map 决策 |

## 7. 验收补记（2026-11-04，`34cbb98f`）

1. **全仓测试已跑完**：`pytest -q --cov=src` → **8176 passed, 7 skipped**，总覆盖率 **81.64%**（阈值 60%），用时 30:38。`tests/rulesets` 单独复跑 1065 passed。原 §4 的 100 passed 缺口由本次全量结果关闭；`tests/rulesets/test_bundle_loader.py` 的物品期望值已随数据从 50 更新为 81（该断言此前是 D1 数据落地后唯一的全量红灯）。
2. **T12 已补齐**：`test_two_handed_die_feeds_the_maximize_pipeline`（S-P2：单手 8 / 双手 10 / 重击后 20，空 RNG 证零消耗）与 `test_two_handed_attack_keeps_the_conditional_check_bonus`（S-P10：祝福 d4 = 3，伤害仍取双手 d10）。
3. **browser smoke 已复跑**：`e2e/dnd2024-workspace.spec.ts` 在 desktop 与 mobile 两个 project 下各 **11 passed**，覆盖引导创建（原 #646 红灯用例）与 1C 的 Lucky spend、Cutting Words damage window 两个窗口用例。
4. **PR 未创建**：`feat/dnd2024-1d-items` 已推送至 origin（`e9ecb11b..34cbb98f`），PR body 待补（本报告可整段引用）。

## 8. 完成判据

D1 转为「完成」需同时满足：全量套件绿 + T12 断言补齐 + e2e 复跑结论明确。三项均已在 `34cbb98f` 后满足，D1 验收闭环。D2–D4 仍按主计划顺序未开工。
