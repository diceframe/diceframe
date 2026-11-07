# S0-F37 任务书：12 职业、12 子职与选项行为最终覆盖审计

| 项 | 值 |
|---|---|
| 切片编号 | S0-F37（最终审计；独立新任务） |
| 验收载体 | 动态生成 198 行 `(owner_ref, local_id)` 矩阵、专项选项矩阵、全链路证据与机器报告 |
| 规则基准 | 官方 SRD 5.2.1，职业与子职 p28–82；专项 Monk p50–51、Rogue p63、Warlock p70–74、Sorcerer p65–67 |
| 官方文件 | `SRD_CC_v5.2.1.pdf`；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `integration/dnd2024-srd@35653f81`；实际执行必须从全部前置完成后的集成提交开工 |
| 前置任务 | S0-A 至 S0-E3、F1 至 F36 **全部完成并合入** |
| 状态 | 待执行；本文是审计契约，不表示任何规划特性已经实现 |

## 1. 目标、范围与审计纪律

1. 本任务只建立并运行覆盖审计，不借审计之名修特性、补法术、改 reducer 或重写内容。发现缺口就使审计失败或形成明确 guided 记录，不得顺手改产品代码换绿。
2. 从当前 HEAD 的 progression/subclass catalogs 动态生成 198 行主矩阵；每行必须指向真实实现与已执行行为测试，或进入机器可读的 guided 白名单并给出具体理由。
3. 另审计 Monk’s Focus 三子能力、28 个 Invocation、10 个 Metamagic、Cunning Strike 六个选项及重复对象/升级变体；它们是主矩阵父特性的子证据，不重复计入 198。
4. “有 catalog 行”“有资源池”“有 capability”“有英文/中文名称”“有事件类型”都不等于行为完成。没有行为测试的 deterministic 行必须失败。
5. 禁止手填 198 行静态完成表、复制旧 SP 的完成声明或把 F1–F36 任务书当作实现证据。执行报告只消费脚本生成结果。
6. 不使用 `.rulebooks`、open5e、2014 规则或第三方译文；唯一规则依据为指定 PDF。

## 2. 官方英文关键原文与页码

Monk’s Focus，SRD p50–51：

> You start knowing three such features: **Flurry of Blows, Patient Defense, and Step of the Wind**, each of which is detailed below.

> **Flurry of Blows.** You can expend 1 Focus Point to make two Unarmed Strikes as a Bonus Action.
>
> **Patient Defense.** You can take the Disengage action as a Bonus Action. Alternatively, you can expend 1 Focus Point to take both the Disengage and the Dodge actions as a Bonus Action.
>
> **Step of the Wind.** You can take the Dash action as a Bonus Action. Alternatively, you can expend 1 Focus Point to take both the Disengage and Dash actions as a Bonus Action, and your jump distance is doubled for the turn.

Cunning Strike，SRD p63：

> When you deal Sneak Attack damage, you can add one of the following Cunning Strike effects. Each effect has a die cost, which is the number of Sneak Attack damage dice you must forgo to add the effect. You remove the die before rolling, and the effect occurs immediately after the attack’s damage is dealt.

> **Poison (Cost: 1d6)** … **Trip (Cost: 1d6)** … **Withdraw (Cost: 1d6)**.

> **Improved Cunning Strike.** You can use up to two Cunning Strike effects when you deal Sneak Attack damage, paying the die cost for each effect.

> **Devious Strikes.** … **Daze (Cost: 2d6)** … **Knock Out (Cost: 6d6)** … **Obscure (Cost: 3d6)**.

Invocation 主干，SRD p70–71：

> You can’t replace an invocation if it’s a prerequisite for another invocation that you have. … You can’t pick the same invocation more than once unless its description says otherwise.

Metamagic 主干，SRD p66：

> You can use only one Metamagic option on a spell when you cast it unless otherwise noted in one of those options.

> Whenever you gain a Sorcerer level, you can replace one of your Metamagic options with one you don’t know.

SRD p72–74 的 Invocation 选项共 28 项，SRD p66–67 的 Metamagic 选项共 10 项；脚本必须从 canonical 声明生成完整清单并与官方数量对账，不能只抽查常用选项。

## 3. 定点代码事实与数量口径（基线已核实）

| 事实 | 文件与函数 |
|---|---|
| `features_by_level` 为每职业 20 行，目录加载校验职业集合与 bundle 一致 | `src/rulesets/dnd2024/progression/catalog.py::Dnd2024ProgressionCatalog.from_bundle` |
| `snapshot()` 给出当前等级 gained features、tracks 和 slot profile | `progression/catalog.py::snapshot` |
| subclass catalog 每职业一个 SRD 子职，`feature_ids_by_level` 给出真实授予 | `progression/catalog.py::from_bundle`、`subclass_choice`；`content/subclass_catalogs/srd_subclasses.json` |
| 升级 preview/apply 与 history 保存 gained ids、choices、diff | `progression/advancement.py::preview_next_level`、`apply_next_level` |
| 基线历史 grant 是 id/class_ref/level；审计执行时必须使用 S0-A 后的完整 owner 身份，不得退回短 id | `progression/advancement.py::apply_next_level`；S0-A 实际接口 |
| 特性所有权、数值与投影集中于 Resolver | `features/resolver.py::features_for`、`feature_value`、`feature_views` |
| 战斗/探索/rest/装备分别有不同投影和写入边界，单点测试不足以证明全链路 | `combat/view.py`、`combat/reducer.py`、`exploration/engine.py`、`resting/engine.py`、`character/reconciliation.py` |
| 基线不存在本任务要求的动态 198 行审计脚本或机器报告 | `scripts/` 与测试目录基线核对；本任务新建，不伪称已有 |

### 3.1 为什么主矩阵是 140+58

基线 progression 按 `(class, feature_id)` 去重有 188 项。主矩阵排除每职业四个通用/结构条目：`*_subclass`、`subclass_feature`、`ability_score_improvement`、`epic_boon`，共 `12×4=48`；剩余 **140** 个职业行为特性。四个通用族不能消失，必须在 §4.5 单独形成 48 行通用成长审计。

| 职业 | 主矩阵行数 | 职业 | 主矩阵行数 |
|---|---:|---|---:|
| Barbarian | 16 | Bard | 9 |
| Cleric | 8 | Druid | 10 |
| Fighter | 12 | Monk | 19 |
| Paladin | 14 | Ranger | 14 |
| Rogue | 15 | Sorcerer | 7 |
| Warlock | 9 | Wizard | 7 |
| **合计** | **140** | 12 职业 | 12 |

| 子职 | 行数 | 子职 | 行数 |
|---|---:|---|---:|
| Path of the Berserker | 4 | College of Lore | 4 |
| Life Domain | 5 | Circle of the Land | 5 |
| Champion | 6 | Warrior of the Open Hand | 4 |
| Oath of Devotion | 5 | Hunter | 5 |
| Thief | 5 | Draconic Sorcery | 5 |
| Fiend Patron | 5 | Evoker | 5 |
| **合计** | **58** | 12 子职 | 12 |

数量是官方 SRD 冻结验收值。若执行 HEAD 生成数量不同，停止并查明内容漂移；不得改预期、删行或把新增项藏进 guided 来凑数。

## 4. 精确实现契约

### 4.1 新建审计入口与输入

新建 `scripts/audit_dnd2024_srd_coverage.py`，只使用标准库与项目现有公开加载器：

1. 参数至少含 `--ruleset <path>`、`--junit-xml <path>`、`--json-out <path>`、`--markdown-out <path>`；默认 ruleset 指向 `templates/rulesets/dnd2024_srd`，不得硬编码当前工作树绝对路径。
2. 通过项目 bundle/progression loader 读取 canonical progression、subclass、class feature、combat/spell/rest catalogs；记录实际 HEAD、content_version、输入文件 SHA256 和 PDF 基准 SHA256。
3. 加载真实 pytest JUnit XML，建立 `classname+name` 到标准 pytest nodeid 的可靠映射；不存在、失败、error、skipped 的测试均不能作为 deterministic 通过证据。
4. 不 import 客户端、不启动服务器、不修改角色或生产内容；脚本本身不写 canonical 数据。
5. 输出 JSON 是唯一完整数据源，Markdown 由同次内存结果渲染；禁止手改 Markdown 后仍称通过。

### 4.2 198 行动态生成算法

1. 职业行：遍历 12 职业 20 级 `features_by_level`，以 `(class:<class_id>, feature_id)` 去重；排除且仅排除 §3.1 四族结构项，生成 140 行。
2. 子职行：遍历 12 子职的 `feature_ids_by_level`，生成 `(subclass:<subclass_id>, feature_id)`；同一子职同一 local id 重复出现时去重并另外记录所有授予等级，基线必须为 58 行。
3. 每行至少含：`owner_ref`、`local_id`、`class_ref`、`subclass_ref?`、`grant_levels`、`source_ref`、`status`、`implementation`、`behavior_tests`、`guided_reason?`、`guided_negative_tests?`。
4. `implementation` 是数组，每项含真实 `path` 与 `symbol`；脚本用文件存在性和 Python AST/JSON 路径验证，不接受只写模块名或虚构 `complete.py`。
5. `behavior_tests` 是真实 pytest nodeid 数组。deterministic 行至少一个直接覆盖该 owner/local 行为的已Passed测试；共享测试必须通过参数化 id 或明确用例证明覆盖该行，不能用 `test_all_features()` 空泛代替。
6. 行排序固定为 owner_ref、local_id；重复身份、同一 local id 跨 owner 错配、缺失 source_ref/授予等级均失败。输出 198 行及 140/58 分类计数。
7. 实现/测试证据映射可以按 owner/local 声明在机器可读 evidence 文件中，但 **status 不得由该文件手填为 complete**；脚本必须根据实现存在和 JUnit 通过现场计算。

### 4.3 guided 白名单不是完成白名单

新建 `tests/rulesets/fixtures/dnd2024_srd_coverage_guided.json`，只允许记录确实需要 GM/叙事/未建系统的子能力：

1. 每项必须含 owner/local、可选 subfeature_id、官方 source_ref、缺失系统、用户可见限制、后续依赖、负例测试 nodeid；未知字段、空理由、泛化理由“复杂/后续再说”均拒绝。
2. guided 不得覆盖已有真实效果却缺测试的行，不得用于修复数量不符，不得把整职业/整子职打包豁免。
3. 每个 guided 项必须有已执行负例，证明不会提供假按钮、不扣资源、不泄露秘密、不制造空事件；没有负例即审计失败。
4. `not_automated` 与 `guided` 分开计数，报告列出完整清单。它们都不是 deterministic complete，也不得被统计为“198 行全部实现”。
5. 白名单只允许解释现状，不允许把任务书、旧 SP 或计划日期当成缺失系统的证据。

### 4.4 专项子能力矩阵

1. **Monk’s Focus=3**：Flurry、Patient、Step 各有 owner/local 子行；Flurry 验证必须支付 Focus 及两/三次升级，Patient/Step 分别验证免费与 Focus 变体、成本、动作、目标与 Heightened Focus 升级，不因父特性有资源池就通过，也不虚构“免费 Flurry”。
2. **Invocation=28**：Agonizing Blast、Armor of Shadows、Ascendant Step、Devil’s Sight、Devouring Blade、Eldritch Mind、Eldritch Smite、Eldritch Spear、Fiendish Vigor、Gaze of Two Minds、Gift of the Depths、Gift of the Protectors、Investment of the Chain Master、Lessons of the First Ones、Lifedrinker、Mask of Many Faces、Master of Myriad Forms、Misty Visions、One with Shadows、Otherworldly Leap、Pact of the Blade、Pact of the Chain、Pact of the Tome、Repelling Blast、Thirsting Blade、Visions of Distant Realms、Whispers of the Grave、Witch Sight，少一项即失败。
3. **Metamagic=10**：Careful、Distant、Empowered、Extended、Heightened、Quickened、Seeking、Subtle、Transmuted、Twinned；逐项验证费用、时机、真实效果、组合与替换，Twinned 必须是有效等级 +1。
4. **Cunning Strike=6**：Poison、Trip、Withdraw、Daze、Knock Out、Obscure；验证骰成本先移除、效果后结算、DC、工具/体型、持续/重复豁免，以及 11 级两个不同效果的付费组合。
5. 可重复 Invocation 必须另有“不同对象”行为证据；Metamagic/Invocation 替换与依赖闭包不能只测投影。子项允许独立 guided，但父特性只有在所有必需子项 deterministic 时才可整体 complete。
6. 专项矩阵数量单独对账为 3/28/10/6，不加入 198 行总数；专项行也需实现符号和 JUnit 行为证据。

### 4.5 48 个通用成长条目与重复升级面

1. 对 12 职业分别审计 subclass 选择、子职 feature 占位、ASI、Epic Boon，共 48 行；不得因排除出 140 而不报告。
2. `subclass_feature` 是结构占位，不伪装成独立规则效果；其证据必须指向真实子职授予和历史，而非虚构 canonical 特性。
3. ASI/Epic Boon 验证实际专长得主、属性上限、重复等级、choices 与历史；子职选择验证 3 级真实所有权和 owner 隔离。
4. 重复获得的 Extra Attack、Expertise、Weapon Mastery、Improvement 类特性必须验证“再次获得/升级替换/不叠加”规则，不能只看 20 级终值。
5. builder 直建高等级、逐级 advance、history 重建三条路径必须产出相同 `(owner_ref,local_id)`、选择、资源和推导值。

### 4.6 跨职业隔离与全链路审计

1. 对 Spellcasting、Weapon Mastery、Fighting Style、Extra Attack、Expertise、Unarmored Defense、Evasion 等同名特性按 owner 分别测试；切换职业/子职不能继承短 id 效果。
2. 所有 198 行先过 en/zh-CN：label 非空、canonical ref/source_ref 不变、summary 不夸大未自动化行为；locale 不得成为新授予来源。
3. 资源非完成检查：凡只有 maximum/current/recovery/capability 而没有实际消费或被动行为测试的行，自动失败；有资源池不得提高覆盖率。
4. 升级/历史：真实授予等级、重复升级、选择与替换、历史重建必须有一致性证据。
5. 存档/reducer：适用行验证 save/load、事件 apply/replay、重复批次和 reducer 零 RNG；纯声明行必须标明不适用的结构化理由，不能由审计者随意跳过。
6. rest：长短休资源、免费施法、使用计数、持续效果与到期按各自行规则验证；不能只用“长休全恢复”通用断言覆盖特例。
7. 装备/探索/战斗：适用行覆盖换装 reconciliation、探索合法/非法入口、战斗动作/目标/位置/反应；不在某链路适用的行记录机器可读的 `not_applicable` 理由。
8. 报告按链路给出通过/guided/not_automated/fail 数量，不能用单一总百分比掩盖 guided 或缺失链路。

### 4.7 报告、退出码与禁止手填

1. JSON 含 schema_version、HEAD、输入 hash、主矩阵、48 通用行、四个专项矩阵、计数、失败列表和 JUnit 来源；Markdown 是只读视图。
2. 任一数量不符、缺行/重复行、无实现、测试未Passed、guided 无负例、跨 owner 串扰或链路失败时退出非零。
3. 报告不得含 `待办占位标记`、空实现、空测试、空 guided 理由或“假定完成”。未执行审计前只允许存在任务书，不预写完成报告。
4. 白名单/证据文件变更本身必须被测试覆盖：伪造 complete、删除测试、改 JUnit 为 failed、加未知 guided 项均必须失败。
5. 审计通过的含义仅是“所有行均有现场证据或明确非完成边界”，不是宣称 198 行全部自动化。

## 5. 改动白名单

只允许新建/修改：

1. `scripts/audit_dnd2024_srd_coverage.py`。
2. `tests/rulesets/test_dnd2024_srd_coverage_audit.py`。
3. `tests/rulesets/fixtures/dnd2024_srd_coverage_evidence.json`：仅实现与测试映射，不允许手填 complete。
4. `tests/rulesets/fixtures/dnd2024_srd_coverage_guided.json`：仅 §4.3 非完成白名单。
5. 由脚本生成 `docs/internal/design/S0F37_COVERAGE_AUDIT_REPORT_2026-11-07.json` 与同名 `.md`。
6. 本任务书：只允许追加执行报告。

明确不改任何 `src/`、templates、既有测试、索引、旧 SP、其他文档、manifest、content_version 或前端。审计发现的实现缺口另开任务，不在本切片修复。

## 6. 逐项编号测试与验收矩阵

1. 从真实 catalogs 生成 12 职业、12 子职、140+58=198 行；逐职业/子职数量与 §3.1 完全一致。
2. 四族结构项恰好形成 48 行通用成长审计；没有从报告中消失，也没有混入 140。
3. 同一职业重复等级去重但保留全部 grant_levels；新增/删除/重复 owner/local 行均使脚本失败。
4. 198 行不是静态源：修改临时复制 catalog 的授予项会改变生成结果并触发数量/证据错误，原仓库不被修改。
5. 每行 implementation path/symbol 经 AST/JSON 验证；不存在文件、函数、类或 JSON key 均失败。
6. deterministic 行的行为测试必须在真实 JUnit 中Passed；missing/failed/error/skipped/仅存在源码均未通过。
7. 共享参数化测试能按 nodeid 区分 owner/local；一个无参数的 `test_all_features()` 不能覆盖全部行。
8. guided 项字段、官方 source_ref、缺失系统、后续依赖和Passed负例完整；未知字段、空理由、打包豁免均失败。
9. not_automated/guided 不计为 complete；报告分别列出并证明无假按钮、资源消费、秘密泄露或空事件。
10. Monk Focus 恰三项；Flurry 的 Focus 成本、Patient/Step 的免费与付费分支、动作/目标、Heightened Focus 和 Focus 恢复各有行为证据，零 Focus Flurry 被拒绝。
11. Invocation 恰 28 项，逐项有实现与行为/guided 证据；可重复项不同对象、前置与依赖闭包、替换均有测试。
12. Metamagic 恰 10 项，逐项费用/时机/行为有证据；双选项组合、Apotheosis、Subtle×Counterspell、Twinned effective level +1 单列。
13. Cunning Strike 恰六项，骰成本、顺序、DC、工具/体型、持续效果及 11 级两效果组合有证据。
14. 子项缺失/未通过会阻止父特性整体 complete，但专项数量不重复加入 198。
15. Spellcasting 等跨职业同名特性按 owner 隔离；伪造其他 class/subclass owner 不获得效果。
16. ASI、Epic Boon、子职选择/占位在 12 职业真实升级与历史中一致；重复升级不重复授予或错误叠加。
17. builder 高等级、逐级 advance、history 重建的身份、选择、资源与推导值一致；差异使审计失败。
18. en/zh-CN 标签完整，canonical id/source_ref 不本地化；summary 不得宣称 guided/not_automated 效果已实现。
19. 只有资源 maximum/current/recovery/capability 而无行为测试的负例行被判定非完成。
20. 适用行的 save/load、apply/replay、重复批次和 reducer 零 RNG 有Passed证据；`not_applicable` 必须结构化且非空。
21. 适用行的 Short/Long Rest、装备 reconciliation、探索和战斗正/负路径有证据；不能用一条通用 rest 测试覆盖特殊倒计时。
22. JSON/Markdown 数量、状态、失败列表完全一致；手改其中一个文件后 hash/重新生成校验能发现，不以手改结果验收。
23. 负例夹具分别模拟缺行、重复行、假实现、假测试、失败 JUnit、非法 guided、数量漂移和跨 owner 串扰，脚本全部非零退出。
24. 在全部前置真实集成 HEAD 上运行聚焦测试、全量 tests/rulesets、动态审计、ruff 和 diff check；报告无 待办占位标记、无空证据、无未解释失败。

## 7. 非目标与停止条件

### 7.1 非目标

- 修复审计发现的任何职业、法术、物品或子职缺陷。
- 追求“198/198 deterministic”而删除合法 guided 边界。
- 多职业、非 SRD 子职、2014 内容、前端覆盖率或叙事质量评分。
- 手抄官方 198 条全文、手写静态完成矩阵、更新任务索引。

### 7.2 停止条件

1. S0-A 至 E3 或 F1–F36 任一未完成、未合入或只有任务书没有执行证据。
2. 当前 catalogs 无法生成 140+58，或 12 职业/12 子职与冻结 SRD 口径不一致。
3. S0-A 后仍无完整 owner 身份，或需要猜测 owner 才能生成矩阵。
4. JUnit 无法可靠映射到 pytest nodeid，或执行侧试图用源码字符串搜索代替真实通过结果。
5. 需要修改生产代码、既有测试、索引或其他文档才能让审计通过。
6. guided 被用来掩盖数量、缺测试、未实现资源行为或已知错误。
7. 198 行由手工静态列表维护，或报告允许空理由、待办占位标记、未执行测试。
8. 最小负例 4、6、8、23 不能证明审计会失败时，停止交付，不得宣称脚本有效。

## 8. 验收命令

使用项目实际 Python 解释器并记录路径；以下为 PowerShell 形状，临时目录使用本任务专属新目录：

```powershell
python -m pytest tests/rulesets/test_dnd2024_srd_coverage_audit.py -q --basetemp=.ptmp-s0f37-unit
python -m pytest tests/rulesets -q --basetemp=.ptmp-s0f37-full --junitxml=.ptmp-s0f37-junit.xml
python scripts/audit_dnd2024_srd_coverage.py --ruleset templates/rulesets/dnd2024_srd --junit-xml .ptmp-s0f37-junit.xml --json-out docs/internal/design/S0F37_COVERAGE_AUDIT_REPORT_2026-11-07.json --markdown-out docs/internal/design/S0F37_COVERAGE_AUDIT_REPORT_2026-11-07.md
python -m ruff check scripts/audit_dnd2024_srd_coverage.py tests/rulesets/test_dnd2024_srd_coverage_audit.py
git diff --check
```

不得用删除、skip、xfail 或放松既有断言换绿。若上游确有失败，审计报告必须保留失败并非零退出；不能用基线豁免把本矩阵行为测试标 Passed。

## 9. 交付与执行报告模板（执行侧填写）

1. PR 只含 §5 文件；JSON/Markdown 必须由报告中的同一命令生成，且附上 HEAD、输入 hash 和 JUnit 文件 hash。
2. 测试 1–24 逐项映射，分别展示 198 数量对账、48 通用行、3/28/10/6 专项对账和负例失败证据。
3. 分别报告 deterministic/guided/not_automated/fail 数量，禁止只写“覆盖率 100%”。

```markdown
## 执行报告（YYYY-MM-DD）

- 全部前置提交 / 实际基线 / 分支 / 最终提交：
- PDF、ruleset、JUnit 与输入文件 hash：
- 198 行生成算法及 140/58、48、3/28/10/6 对账：
- deterministic / guided / not_automated / fail 数量与完整清单：
- 跨职业隔离、重复升级、en/zh-CN 与资源非完成审计：
- 升级/历史/存档/reducer/rest/装备/探索/战斗链路结果：
- 测试 1–24 逐项结果及负例退出码：
- 聚焦 / 全量 / 动态审计 / ruff / diff 原始输出：
- 改动文件白名单核对、设计偏差与未决事项：
```
