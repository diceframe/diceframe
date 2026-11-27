# 切片 S0-F32 执行报告：祈唤主干与八项核心

- 任务书：`S0F32_INVOCATION_CORE_TASK_SPEC_2026-11-07.md`（维护者资料，未入库；下文 §n 均指该任务书）
- 开发基线：`0b12b0ea`（main，含 #888 对 `resolution.py` 的拆分；本片随后把法术侧改动落在
  `spell_resolution.py` / `validation_spells.py`，池编排落在 `progression/choices.py`）
- 状态台账：`S0_INVOCATION_STATUS_2026-11-26.md`（28 项祈唤逐项四档状态）
- 缺口登记：`GAP_REGISTER.md` GAP-44（法术攻击无多束抽象）

## 1. 落地内容

| 文件 | 改动 |
|---|---|
| `src/rulesets/dnd2024/features/option_pool.py`（新，~700 行） | 按职业表计数的选项池：数量差额、逐级替换、依赖闭包、选项前置、单对象绑定、容量（按可选取次数）、`shortfall`、`legacy` 豁免 |
| `src/rulesets/dnd2024/features/models.py` | `FeatureChoice` 增 `count_track` / `replace_per_level`；`choice` 块改闭集键；池选项参数闭集（`INVOCATION_PARAMETER_KINDS`）、效果键唯一性 |
| `src/rulesets/dnd2024/progression/advancement.py` | preview 规划池；apply 原位替换池记录；`missing_choices` 不含池（见 §4） |
| `src/rulesets/dnd2024/character/builder.py` / `derivation.py` | 建卡投影 `invocation_choices`、校验并写入与升级同形状的记录 |
| `src/rulesets/dnd2024/runtime.py` | 存档重放传 `allow_legacy_pool` |
| `src/rulesets/dnd2024/features/resolver.py` | `bound_objects` / `owns_binding`；`_selected_feature_options` 与 `feature_selection` 共用 `parse_ref` |
| `src/rulesets/dnd2024/combat/invocation_effects.py`（新） | 六项效果的判定：按声明参数找载体，不认特性名 |
| `combat/resolution.py` / `engine.py` / `validation.py` / `check_modes.py` | 伤害加值、射程、推挤、temp HP 最大化、专注优势、Truesight |
| `frontend-v2` 三个文件 | 升级面板：`{ref, object_ref}` 选择 + 绑定下拉 + 替换槽；建卡面板：祈唤槽 |
| `tests/rulesets/test_dnd2024_invocation_core.py`（新，42 例） | 主干、六项效果、建卡、老卡、容量、同批依赖、卡面视图、持久化 |

## 2. 语义

- **数量**：`track(target) − owned`，所以 1 级没选不会作废，2 级一次补齐；track 不变的等级不要选择。
- **容量**：按"还能选几次"求和（可重复选项每绑一个对象多一次），并把"同一批里新获得的依赖项"算进去（F34 的 Blade + Thirsting 同批成立）。
- **替换**：每获得一个职业等级一次，`drop` 支持 `{ref, object_ref}`（可重复选项靠它改绑），闭包按 `current` 判定。
- **绑定**：一次选择是 `{ref, object_ref}` 或裸 ref；所有权与展示读同一套解析。
- **记录**：一个池一条记录，`selected_refs` 是当前集合（替换要能删），逐级提交留在 `progression.history`。

## 3. 与任务书的偏差

### 偏差 1：新增两个模块，超出 §5 白名单

`features/option_pool.py` 与 `combat/invocation_effects.py` 不在任务书白名单里。原因：`advancement.py` 是热点文件（§21.5.1 只允许 +100），把编排塞进去会立刻撞棘轮；`invocation_effects` 是为了让 combat 不硬编码特性名（GAP-43 耦合守卫）。**已获维护者批准**（2026-11-27 会话）。

### 偏差 2：1 级建卡的作答不阻断

任务书 §6.1 要求"各级数量严格等于 track"。建卡界面对这个槽**投影但不强制**：未作答时不给记录、首次升级按表补齐。理由是与既有 Expertise 槽的约定保持一致（`plan_skill_choices` 对未作答同样容忍，并在注释里标为缺口）。作答时按升级路径同一套规则校验。

### 偏差 3：`shortfall` 不阻断升级

任务书 §6.1 的"严格等于"在内容不全时不成立：本片只声明 8 项，12 级要 8 个，两把伤害戏法下最多凑 7 个。此时升级不拦，差额记在需求槽的 `shortfall`，并有用例钉住容量=7。**F33–F35 补齐后应把用例期望改成 0**，而不是留着豁免。

### 偏差 4：老卡豁免（`allow_legacy_pool`）

本切片之前建的 2 级以上魔契师卡没有池记录，逐级 replay 会被必填槽拒掉、存档打不开。replay 路径现在允许这类卡跳过并种下 `legacy: true` 记录（粘性，玩家补齐后消失），且**槽位照样投影待补数量**（早期版本把 `count` 设 0，等于永久 0 个祈唤，已修）。正常升级不走这条路径。

### 偏差 5：两项按 §4.3.4 记 `guided`

- **Devil's Sight**：缺光照/魔法黑暗模型（`combat/obscurement.py` 头注释即说明）。
- **Lessons of the First Ones**：升级路径没有 Origin 专长池（`srd_advancement_feats.json` 只有 general / fighting_style / epic_boon），唯一 `deterministic` 的 `skilled` 自己还要选 3 项熟练。

两项都投影出来、带原因、提交被拒，不宣称完整。

## 4. 未决

| 项 | 状态 | 归属 |
|---|---|---|
| `missing_choices` / `repair_choices` 不支持祈唤 | 欠账只能在升级时补，修复入口看不见 | 独立切片（planner + 修复 API + 前端） |
| 面板把依赖项置灰 | 服务端已支持同批依赖，UI 要等 F34 | S0-F34 |
| 祈唤欠账不进 `missing_choices` 的说明 | 已写进状态台账 | — |

## 5. 只看门禁才现形的缺陷

这一片最大的教训：**全量 3197 passed 时，仍有三个 blocker**。都是测试结构看不到的层面。

1. **前端提交不出绑定**：面板只 push 字符串，而带绑定的选项要 `{ref, object_ref}`；2 级要选 3 个、免绑定的只有 2 个，玩家怎么点都过不去。测试没看见，因为测试直接构造 `feature_choices`。
2. **老存档打不开**：replay 逐级重放，老卡的 history 没有池记录 → 必填槽 → `ProgressionCatalogError`。测试没看见，因为没有"旧卡"这个输入。
3. **`eldritch_mind` 等级写错**：原文没有前置行，数据写成 2 级，导致 1 级可选祈唤为 0——而 `shortfall` 兜底把"数据错"显示成"内容不够"。

另外两条自查发现：`_selected_feature_options` 把 dict 元素 `str()` 成垃圾键（所有权恒假）；`feature_selection` 用专长目录校验 `feature_option` 引用（卡面 selection 整片消失）。

## 6. 任务书 §6 逐条验收

| # | 条款 | 状态 |
|---|---|---|
| 1 | 各级数量等于 track；builder/advance/history 一致 | ✅ 用例 3 条（建卡作答、2 级补齐、5 级新增 2 个）；history 一致性用 replay 用例覆盖 |
| 2 | 重复/未知 ref/等级不足/缺前置拒绝；中英与 source_ref | ✅ |
| 3 | 每级最多替换一个；直接与传递依赖被保留时拒绝 | ✅ |
| 4 | 可重复选项必须不同对象；同对象重复拒绝 | ✅（三个可重复项共用同一段判定） |
| 5 | Agonizing 每 roll 加 Cha；多射线逐次加 | ⚠️ 单束 ✅；**多束被 GAP-44 挡住**（数据里 Eldritch Blast 只有一道 1d10） |
| 6 | Spear 仅 10+ 尺绑定；+30×等级；替换/距离边界 | ✅ 含 Distant 先加后乘（270→540）、等级 2/5 各一条 |
| 7 | Repelling 命中 Large 或更小可推 0–10；Huge/miss/非攻击戏法拒绝 | ✅ 四向都测（Huge 不推、miss 不推、豁免型戏法提交被拒） |
| 8 | Devil's Sight 覆盖 Dim/魔法黑暗 | 🟡 按 §4.3.4 记 `guided`，投影带原因 |
| 9 | Eldritch Mind 只给维持专注的 Con save 优势 | ✅ 正例（两骰取高、来源 id）+ 负例（普通 Con 豁免单骰） |
| 10 | Fiendish Vigor 仅对自己免费 False Life；12 点；零 RNG | ✅ |
| 11 | Lessons 授予真实 Origin feat | 🟡 `guided`（无 Origin 池），不宣称完整 |
| 12 | Witch Sight 15 级 30 尺；14 级没有 | ✅ 两侧都测 |
| 13 | apply/replay、save/load 一致；reducer 零 RNG | ✅ 连续两次 normalize 后记录与绑定不变 |
| 14 | 既有 Warlock/False Life/Concentration/回归通过 | ✅ 全量 3260 passed |

## 7. 验证

| 项 | 结果 |
|---|---|
| 全量 `pytest tests/rulesets tests/architecture` | **3350 passed / 0 failed**（基座 `0b12b0ea`） |
| 本切片用例 | 42 条（`test_dnd2024_invocation_core.py`） |
| `ruff check src/ tests/` / `mypy` | 干净 / 120 files |
| 前端 | `vue-tsc -b`（仅既有 `driver.js` 缺包）、`eslint --max-warnings 0`、`vitest` 27 条 |
| 体积棘轮 | resolution +78、validation +45、engine +13、resolver +86、advancement +72，全 ≤100 |
