# 交接：GAP-43 剩余步骤与大文件拆分

| 项 | 值 |
|---|---|
| 日期 | 2026-11-25 |
| 适用 main | `f2d86b1a`（#848 合入后） |
| 相关 issue | #843（GAP-43 提案与口径）、#851（本交接） |
| 相关 PR | #845（守卫）、#848（成本模型第一步前半）、#849（resolver 校验拆分）、#841（S0-F30）、#844（任务书，在途）、#847（Metamagic，在途） |
| 任务书 | `docs/internal/design/GAP43_ACTIVATION_TASK_SPEC_2026-11-24.md`（在 #844 里） |
| 性质 | 维护者内部交接；不是对外契约，不得从玩家文档/网站/`diceframe-content` 引用 |

## 1. 这份文档怎么用

这是给接手结构性工作的开发者的施工说明。它整合了三件事：

1. 今天已经落地的三块基础（守卫、成本模型、resolver 校验拆分）和它们**不能**解决的问题；
2. 剩余的结构任务，按依赖关系排序，每条带范围、验收命令和已知坑；
3. 判断"文件会不会继续膨胀"的标准和治理规则。

设计口径以 #843 的回复和 GAP-43 任务书为准；本文只补充执行事实和顺序。发现本文与 `docs/ARCHITECTURE_*.md` / `docs/ENGINEERING_RULES.md` 冲突时，以后两者为准。

## 2. 现在的事实

### 2.1 已合入 main

- 8 条特性/修复 PR（#837、#840、#836、#838、#839、#832、#834、#842）。
- **#845 守卫**：`scripts.architecture_fitness.scan_combat_feature_coupling` 以 shrink-only 基线钉住 D&D 战斗包里的四类耦合——`action.kind` 比较、`feature_id`/`granted_by` 比较、`has_feature`/`feature_value` 字面量、意图键开关。当前基线 **296 条**，意图类型 **33 个**。
- **#848 成本模型（GAP-43 第一步前半）**：`CapabilityCost` 三个正交字段 `slot` / `resources` / `timing`；`action` / `turn_start` 保留为只读别名；目录加载器新旧两种写法都读、混写拒绝；7 处散落的槽位可用性判断收成 `slot_available(economy, slot)`；能力扣费收成 `_pay_cost()`；9 条空成本能力的现状用测试固定。
- **#841 S0-F30**（维护者补完后合入）：main 合入、冲突解决、守卫基线登记、决策路径的 Hurl 端到端测试。`attack_hit_triggers` 只留在待定决策的伤害事件上，不再出现在 wire 事件里。

### 2.2 在途

- **#849**：`resolver.py` 的声明校验拆分（2915 → 1847 行，新模块 `features/validation.py` 1085 行）。纯搬家，等 CI。
- **#844**：GAP-43 任务书。待修三处引用（register 里的文件名断链、`economy.get(cost)` 行号已过时、"8 条空成本"应为 9 条），并补执行记录。
- **#847**：Metamagic（+2891/−40，26 文件）。CI 失败原因是守卫基线：新增 `metamagic` 意图开关与 `arcane_apotheosis` / `sorcery_incarnate` / `innate_sorcery` / `metamagic` 字面量，以及意图类型集合变化。需要 review 后登记基线（登记必须写明由哪一步迁移删除）或改成声明。

### 2.3 文件规模（`f2d86b1a`）

| 文件 | 行数 | 说明 |
|---|---:|---|
| `src/rulesets/dnd2024/combat/resolution.py` | 7624 | `_class_capability_events` 281 行、24 个 `action.kind` 分支 |
| `src/rulesets/dnd2024/combat/validation.py` | 3616 | 能力校验与攻击校验混居 |
| `src/rulesets/dnd2024/combat/engine.py` | 3281 | 混入编排与具体特性 |
| `src/rulesets/dnd2024/features/resolver.py` | 2915 | #849 合入后 1847；同时新增 `features/validation.py` 1085 |
| `src/webui/api.py` | 4290 | 401 个方法里 323 个是纯委托；`__init__` 995 行接线 |
| `src/plugin_host/host.py` | 2679 | 124 个方法；安装事务是横切面 |

## 3. 判断标准：不是行数，是"改几个中央文件"

文件可以继续变大——`ENGINEERING_RULES` §21.5.10 明确不设 `max-lines` 硬门槛。要治理的是**中央化增长**：

- 病态：新增一个特性要往同一个 7000 行文件加一个分支，并改 validation + resolution 至少三处。
- 健康：新增一个特性 = 目录数据（JSON/参数）或一个新 handler 文件 + 一行注册；中央文件不涨。

目标：**新增一个特性，中央文件改动数 0–1 个**。在 GAP-43 第二/三步落地之前，文件还会继续涨；落地之后中央文件应趋于平稳。

## 4. 剩余任务

依赖顺序：**A → B → C → D**（A 是 B 的数据前提；B/C 是 D 的切线前提）。E 可穿插；F/G/H 与 A–D 互不依赖，可并行。

### A. GAP-43 第一步后半：`*_restore` 成本搬进 `cost.resources`

**目标**：4 条 restore 能力不再把真实成本藏在特性参数里，成本只有一个可读位置。

- `holy_nimbus_restore`：5 级法术位；`hurl_through_hell_restore`：1 个契约法术位；`dragon_wings_restore`：3 术法点；`intimidating_presence_restore`：1 次狂暴。
- 需要先给资源模型补"法术槽"这种形状（当前 `ResourceCost` 只有 `resource_id + amount`）：建议 `spell_slot_level: int = 0`，或 JSON `{"spell_slot": 5}`。加载器、可用性判定、支付、投影四处都要读它。
- 删除 `restore_slot_level` / `restore_resource` / `restore_resource_amount` 的参数读取（`validation.py`、`resolution.py`）与目录数据。
- **保留**：`dnd2024.spell.slot_spent` 的 `exchange: True` 语义（不占本回合施法位）。

**验收**：

```bash
python -m pytest tests/rulesets/test_dnd2024_long_effects.py -q      # 4 条 restore 现有行为
python -m pytest tests/rulesets/test_dnd2024_gap43_cost.py -q        # 空成本清单 9 → 5
python -m pytest tests/rulesets -q -n 4 --dist loadfile
python -m pytest tests/architecture -q
python -m mypy && python -m ruff check src/ tests/
```

**风险**：法术槽不是 class resource（在 `actor["slots"]`，按等级索引）；拒绝语（"no level 5 spell slot is available"）不能变；`docs/internal/design/` 里的旧任务书记录不要改写。

### B. GAP-43 第二步：`action.kind` 注册表

**目标**：`_class_capability_events` 的 24 个 `action.kind` 分支变成注册表：每种 kind 一个 handler（`validate` / `resolve` / `project`），自己注册。中央路径只做四件事：查声明 → 校验前提 → `_pay_cost()` → 派发。

- 未注册的 kind 在**装载期**报错，不在结算期静默。
- 单特性 kind（`quivering_palm_end`、`holy_nimbus`、`dragon_wings`、`intimidating_presence`、`end_long_effect`、`restore_feature_use` 等）是正常的：它们描述的是该特性自己的效果物理，**不是架构债**。
- 建议先迁 2–3 个简单 kind 立样板（`hide`、`steady_aim`、`end_long_effect`），再分批；每批一个 PR。
- 迁移会**减少**守卫基线（kind_branch 条目），不要新增。路径变化时基线必须在同一个 PR 同步（见 §6.1）。

**验收**：24 个 kind 逐个走注册表后行为不变（`tests/rulesets` 全绿）；新增一个 kind 只需 handler + 注册（用测试里的小 handler 证明中央文件未改）；`tests/architecture` 全绿。

### C. GAP-43 第三步：`attack_hit` 布尔迁成声明

6 个意图布尔：`hurl_through_hell`、`quivering_palm`、`stunning_strike`、`brutal_strike`、`activate_sacred_weapon`、`light_extra_attack`。

- 迁移目标见任务书 §6.2。
- **注意 #841 已建立的契约**：命中触发声明随"被扣住的伤害事件"走（决策路径拿不到原始意图）；`attack_hit_triggers` 只存在于待定决策的 `damage_event` 上，不上 wire（`_submit_hit_damage` 会摘掉）。迁移时保留这个形状。
- 验收：命中 / 未命中 / 资源不足 / 次数耗尽四条路径行为不变。

### D. GAP-43 第四步：战斗包按 kind 拆文件

在 B/C 之后做，切线才自然：把各 kind 的 handler 与校验搬进 `combat/kinds/*`（或 `combat/resolution/*`），中央 mixin 只留编排。

- 不要按行数平均切；按 handler 家族切。
- 守卫扫描的是整个 `combat/` 包，搬家会改变基线里的 `path` 键 → 同一个 PR 里删旧条目、加新条目，并保持"只许变少"的净值。
- 每个 PR 保持纯移动；行为契约靠现有覆盖（§21.5.12），不为"文件变了"加结构测试。

### E. #841 的 P3：dismiss 的可用性

`quivering_palm_dismiss` 是空成本，角色只要拥有该特性就永远可选；没有振动时点击会发一条 `long_effect.ended`，reducer 静默 no-op。正解是把"有效果可结束"表达成 GAP-43 的 `requires` 前提（与 `targets` 共用写法），不是给 kind 加特例。放在 A/B 之间最合适。

### F. `plugin_host/host.py` → `plugin_host/manifest_validation.py`

**搬什么**（≈330 行，全部是静态方法，可安全成为模块函数）：

- 包校验：`_validate_ruleset_catalogs`（947–983）、`_validate_prebuilt_ui`（2275–2311）、`_validate_rulebooks`（2314–2388）、`_validate_adventure_packages`（2391–2431）。
- schema / 权限 / 值 helper：`_validate_schema`（2587–2593）、`_plugin_type`（2601–2602）、`_has_entrypoint`（2605–2607）、`_validate_entrypoint`（2610–2615）、`_validate_manifest_permissions`（2618–2628）、`_validate_runtime_permissions`（2631–2636）、`_sensitive`（2648–2650）、`_normalize_value`（2653–2664）、`_validate_required`（2667–2670）。
- 常量：`_RULEBOOK_ID_RE`、`_ALLOWED_CONTROLS`、`_ALLOWED_PERMISSIONS`（仅这些函数用；`_ID_RE` 还被 `_load_runtime` 用，留在 host 或随文件工具搬）。

**已知约束**：

- 三个静态校验用类名调 `PluginHost._ensure_inside`。把 `_ensure_inside` 搬进新模块（改名 `ensure_inside`），host 里保留 `_ensure_inside = staticmethod(ensure_inside)`，这样 `self._ensure_inside(...)` 的 20+ 调用点和 `_extract_zip` 都不用动。
- `_plugin_permission_details`（实例方法）直接读 `_ALLOWED_PERMISSIONS` → 改成调用新模块的函数或导入该常量。
- `_load_runtime` 必须留成实例方法：6 个测试文件 + `scripts/package_plugin.py`、`scripts/validate_content_module.py` 通过 `host._load_runtime` 调它，且测试大量 monkeypatch 它。
- 错误文案与 `t("plugin_host.host.*")` key 不变；现有 key 可以从任何模块使用（`test_localization_messages.py` 会校验目录镜像与 key 定义）。
- 新模块要加进 `pyproject.toml` 的 mypy 文件列表。

**验收**：

```bash
python -m pytest tests/test_plugin_host.py tests/test_plugin_lifecycle.py \
  tests/test_plugin_install_crash_recovery.py tests/test_plugin_requirements.py \
  tests/test_plugin_security.py tests/test_validate_content_module.py -q -n 4
python -m pytest tests/architecture tests/test_localization_messages.py -q
python -m mypy && python -m ruff check src/ tests/
```

### G. `plugin_host/host.py` → 文件/zip 工具

**搬什么**（≈90 行，纯静态）：`_extract_zip`（2485–2518）、`package_files`（2521–2560）、`_find_install_root`（2563–2571）、`_atomic_json`（2581–2584），以及 `_ID_RE`（若 F 未搬）。

**约束**：

- `PluginHost.package_files` 是公开的类级入口（`services/content_pack_export.py`、`scripts/package_plugin.py` 调用）→ 保留 `package_files = staticmethod(package_files)` 别名。
- 测试 monkeypatch `PluginHost._extract_zip.__globals__`（`tests/test_plugin_lifecycle.py:313, 325`）→ 类上保留 `_extract_zip = staticmethod(_extract_zip)`，指向同一个函数对象。
- `_rename_dir_with_retry` 必须留在 `host.py`（测试按模块路径 monkeypatch 它，且调用点在安装事务里）。

### H. `src/webui/api.py`：只拆有真正 owner 的簇

**不要动 `__init__` 的 995 行接线**（架构文档 §50.4：保留 facade compatibility，不因文件大就拆出新的跨服务耦合）。可拆的是两簇：

1. **规则 / 规则集加载**（K14，2151–2338，≈190 行）：`_load_world_template`、`_load_rule_for_game`、`_resolve_ruleset_check`、`_agent_ruleset_runtime_ids`、`_visible_ruleset_domains`、`_load_runtime_for_game`、`_adventure_runtime_adapter`、`_queue_adventure_reward_intents`、`_project_game_rule_id`、`_load_rule_by_id`。
   - 不能放进 `src/webui/services/`：里面有 `"core:dnd2024"` 字面量，会触发 concrete-ruleset 扫描（services 不许认识 dnd2024）。放 `src/webui/` 顶层的新模块（例如 `webui/rule_loading.py`）。
   - `_load_rule_for_game` 被 16 个依赖 dataclass 绑定，测试有 6 处直接调 → 搬家后要么保留同名薄方法，要么同 PR 改测试。
2. **内容源同步**（3885–3978，≈95 行）：`refresh_content_sources`、`_sync_plugin_adventure_sources`、`_sync_module_catalogs`、`module_catalog`、`module_content_sources`。
   - 同样有 dnd2024 lazy import → 不能进 `services/`。
   - `_sync_plugin_adventure_sources` 有 14 个测试调用点。
   - `test_gameplay_packages.py:201–216` 用字符串 allowlist 钉住 `settle_adventure_completion` 出现的位置（api.py L816/L3377），这项不在本簇，别碰。

**验收**：`python -m pytest tests/test_game_query_routes_http.py tests/test_adventure_resolver_sources.py tests/test_module_compendium.py tests/test_module_install_routes.py -q` + `tests/architecture` + `tests/test_architecture_boundaries.py`。

### I. 守卫基线与趋势

- **评审规则**：守卫基线只允许"登记 + 写明由哪一步迁移删除"。无说明的扩容不合并。
- **可选（建议）**：CI 里输出大文件/目录的行数趋势（仅报告，不设门槛，§21.5.10 允许）。不要引入 `max-lines` 硬门。

## 5. 建议顺序

```text
现在   #849 合入（resolver 校验拆分）
       #844 修引用并合入（任务书数字对齐）
       #847 review + 守卫基线登记（或改声明）

然后   A（restore 成本）→ B（kind 注册表，2–3 批）→ C（attack_hit 声明）→ D（按 kind 拆文件）
并行   F → G（host.py）；H（api.py 两簇）；E（dismiss requires）穿插在 A/B 之间
```

## 6. 工程约束与坑

### 6.1 守卫基线

- 精确匹配 `(path, kind, target)`：多一条失败，少一条也失败。
- 搬家会改 `path` 键：同一 PR 里删旧加新，净值只许减少。
- 新增条目必须能指向"哪一步迁移会删掉它"（#841 的 14 条已按此注明）。
- 别用改基线的方式让特性 PR 通过；先问"这个分支能不能改成声明/ handler"。

### 6.2 类型检查与测试清单

- `pyproject.toml` 的 `mypy files` 是显式清单；新模块不加进去就退出 CI 类型检查。
- 全量后端命令（本机验证用）：

```bash
python -m pytest -q -n 4 --dist loadfile --ignore=tests/test_external_adventure_import.py -p no:cacheprovider
python -m pytest tests/architecture -q
python -m mypy
python -m ruff check src/ tests/
```

- 纯移动不需要新增结构测试（§21.5.12），但不能丢失现有契约覆盖；测试可改名/合并（§15）。
- 事件形状变化必须有"逐字节一致"的对比测试（成本/支付迁移尤其如此）。

### 6.3 私有成员与测试耦合

- `api.py`：约 500 处测试引用私有属性（`_parse_key` 33 个文件、`_handler` 80、`_reg` 50…），且 `access_control.py` 调 `api._parse_key`。搬家时要么保留同名属性/薄方法，要么同 PR 改测试。
- `host.py`：`_extract_zip.__globals__`、`_rename_dir_with_retry` 模块路径 monkeypatch、`_RESTART_*` 常量 patch。
- `resolver.py`：原本 5 处私有校验方法调用已在 #849 改成直接调函数。

### 6.4 路径与目录约束

- `api.py` 的 `Path(__file__).parent.parent.parent / "templates" / ...` 与 `host.py` 的 `parents[2]` 依赖文件深度；搬家要同时调整或保留相对位置，并用测试验证。
- 消息目录镜像源模块路径；新增模块若引入**新** key，需要对应 `locales/<pkg>/<module>.{zh-CN,en}.yml`（复用现有 key 不需要）。
- `tests/architecture/test_dependencies.py`、`test_gameplay_packages.py`、`test_runtime_modules.py` 有路径/allowlist 钉；先读再动。

### 6.5 网络

GitHub 的 git 传输时断时续（`api.github.com` 正常）。push/fetch 用重试循环；不要因为一次失败就换方案。

## 7. 参考

- #843：GAP-43 口径（不合并 trigger/window；kind 注册表；`turn_start` 独立；restore 成本迁移；护栏前置）
- `docs/internal/design/GAP43_ACTIVATION_TASK_SPEC_2026-11-24.md`（#844）
- `docs/ENGINEERING_RULES.md` §15（测试契约）、§16（PR 大小）、§21.5.5（route/service 所有权）、§21.5.6（依赖方向）、§21.5.7（扩展点优先）、§21.5.9（存量债）、§21.5.10（规模是趋势信号）、§21.5.12（测试不保护文件形状）
- `docs/ARCHITECTURE_CN.md` §7（WebAPI/service 边界）、§36（plugin host 边界）、§50.4（不盲目拆 `__init__`）
- `tests/architecture/combat_coupling_baseline.py`、`scripts/architecture_fitness.py`
