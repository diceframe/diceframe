# S0-MM1 任务书：怪物事实层（尺寸、速度、防御、感官、CR）

| 项 | 值 |
|---|---|
| 切片编号 | S0-MM1（MM 线第 1 片，与 F 系列并列） |
| 验收载体 | `enemy` / `monster` 契约扩展：`size`、结构化 `speeds`、`resist/immune/vulnerable`、`condition_immunities`、`senses`、`cr`/`xp`/`proficiency_bonus`，以及未知字段 fail closed |
| 规则基准 | 官方 SRD 5.2.1 怪物 stat block 版式（Size / AC / HP / Speed / Abilities / Saves / Skills / Resistances / Immunities / Vulnerabilities / Condition Immunities / Senses / Languages / CR / PB） |
| 官方文件 | `SRD_CC_v5.2.1.pdf`，SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 代码基线 | `feat/dnd2024-s0f39-target-selection@498cce90` |
| 前置任务 | 无独立前置（F15 的伤害边界、F18 的感官链已合入，本切片复用它们） |
| 状态 | 待执行；本文是执行契约，不表示功能已实现 |

## 1. 范围与完成定义

1. 本切片只扩展**事实层**：一条 MM stat block 能被完整、忠实、不可伪造地装进 `enemy` 契约，并被投影出去。
2. 每个新字段必须说明它的**消费者**。分两类：
   - **A 类（本切片必须接线）**：`resist/immune/vulnerable` → 既有 `_resist_damage` 边界；`condition_immunities` → 意图批次收尾的免疫过滤；`senses.blindsight` → 既有观察链；`cr/xp` → 投影 + AI 草稿保留。
   - **B 类（本切片只装不接，必须登记）**：`size`、`speeds` 的 fly/swim/climb/burrow、`senses.darkvision/truesight/tremorsense`。消费者分别是 F17/F32、阶段三 2D 地图、F30 光照与 MM4 战术层。**未接线的字段不得在 UI 或文档里宣称可用**，执行报告必须逐条列出"装而未用"清单。
3. 未知字段一律拒绝。当前行为是静默丢弃（实测：带 `size`/`cr`/`xp`/`traits` 的敌人通过校验且字段被忽略），这条比缺少字段更危险，必须在本切片一并关闭。
4. 不新增任何内容（stat block 库由 MM2 负责）、不实现遭遇预算公式（MM3）、不实现 traits/施法/Multiattack（MM4）。

## 2. 结构依据

本切片依据 SRD 5.2.1 怪物条目的**字段版式**，不逐字引用正文。执行时以规则书 stat block 为准逐字段核对；本文档只规定"哪些字段、什么形状、谁来消费"，不替代原文。

字段清单（按版式顺序）：Size、Armor Class、Hit Points、Speed、能力值六项、Saving Throws、Skills、Damage Resistances、Damage Immunities、Damage Vulnerabilities、Condition Immunities、Senses、Languages、Challenge（CR + XP + PB）、Traits、Actions、Bonus Actions、Reactions、Legendary Actions。

本切片覆盖：Size、Speed、Damage Resistances/Immunities/Vulnerabilities、Condition Immunities、Senses、Challenge（CR/XP/PB）。
不覆盖：Skills（无权威检定入口，见 §7）、Languages（无语言消费者）、Traits/Actions 之外的战术字段。

## 3. 定点代码事实（基线 `498cce90` 已核实）

| 事实 | 文件与位置 |
|---|---|
| 敌人契约是本仓库唯一入口，AI 生成与预设装载共用 | `combat/validation.py::validate_enemy_profiles`（`combat.start`、preset、AI 临时遭遇三处调用） |
| 已接受字段：`id/name/creature_type/hp/armor_class/speed/position/initiative_modifier/counterspell_dc/abilities/saving_throws/attacks` | `validation.py:84-145` |
| 未知字段**不校验**（无白名单，多余键被忽略） | 同上；实测带 5 个未知键仍 `ACCEPTED` |
| 敌人视图只有 12 个字段，`skill_values` 恒空、无 `senses`、`spell_refs` 空 | `combat/view.py:92-110` |
| 抗性边界已存在且多来源不叠加 | `combat/resolution.py::_resist_damage`（条件注册表 + 条件元数据 + Fiendish Resilience 三源） |
| 感官链已存在（玩家侧经特性声明） | `combat/check_modes.py::sense_declarations / observer_senses / vision_codes` |
| monster 契约存在但零内容 | `content/contracts.py::validate_monster_profile`；`runtime.py::_core_content_sources` 返回 `[]` 并注释自述"没有 monster 记录" |
| AI 临时遭遇明确"不做 CR 系统"，只用 hp/AC 死限 | `director/temporary_encounter.py:24-40`（hp≤500、AC 8–25） |
| 冒险导入要求怪物只作散文 | `webui/services/adventure_ai_import.py:99` |
| 伤害类型闭集已存在 | `combat/catalog.py::DAMAGE_TYPES` |
| 条件码来源 | `srd_combat_core.json` 的 `conditions` 注册表 + `condition_flags()` |

## 4. 精确实现契约

### 4.1 尺寸 `size`

1. 闭集：`tiny` / `small` / `medium` / `large` / `huge` / `gargantuan`，小写。
2. `enemy` 契约中 `size` 在**新内容里必填**，但装载期不强制：旧 intent / 旧存档没有这一栏时必须继续能打（见 4.7.3）。`monster` 内容契约要求必填。
3. 旧 intent / 旧存档缺 `size` 时**不得默认 medium**：装载成功但记为未知（空串），并提供一个统一判定函数 `size_at_most(actor, "large") -> bool`，未知一律返回 `False`（fail closed）。F17 的 Trip、F32 的 Repelling Blast 都读这一个函数，不许各自比较字符串。
4. 未知尺寸值时（例如 `"big"`）拒绝装载。

### 4.2 速度 `speeds`

1. 形状：`{"walk": 30, "fly": 60, "swim": 30, "climb": 20, "burrow": 0, "hover": false}`；各档 0–200，至少一档 >0。
2. **兼容**：旧的标量 `speed: 30` 等价于 `{"walk": 30}`，继续接受（现有 4 条样本与全部测试用它）。归一化在装载期完成，事件与投影里同时给出 `speeds`（结构）与 `speed`（walk 值，供旧读法）。
3. `hover` 只允许出现在有 `fly` 时，否则拒绝。
4. 消费范围：本切片**只投影**。移动预算仍只读 walk；飞行/游泳/攀爬的移动接入属阶段三，写进 §7 非目标。

### 4.3 防御 `resist_damage_types` / `immune_damage_types` / `vulnerable_damage_types`

1. 三者都是 `DAMAGE_TYPES` 的字符串数组，元素去重，长度 0–20。
2. 互斥校验：同一类型不得同时出现在 resist 与 immune、resist 与 vulnerable、immune 与 vulnerable 之间；同一类型出现两次拒绝。
3. 接线到 `_resist_damage`：`immune → 0`、`vulnerable → ×2`、`resist → 半（向下取整）`，且**仍只有一次结算**：与既有条件来源、Fiendish Resilience 合并后只取一个结论，不叠加。返回的来源标识要能区分 `statblock:immune` / `statblock:vulnerable` / `statblock:resist`。
4. 事件里继续只报"被减半/被免疫/被加倍"一个事实与来源，不新增两个互相矛盾的字段。

### 4.4 条件免疫 `condition_immunities`

1. 类型：条件码字符串数组（闭集来自 `conditions` 注册表），长度 0–40。
2. 接线点：**意图批次收尾处一处**（`Dnd2024CombatEngine._statblock_immunity_events`），不是每个施加点各写一份——免疫是目标的属性，所有生产者应得到同一个答案。被免疫时把 `condition.applied` 改写成纯审计事件 `condition.blocked{target_id, condition, reason:"immune"}`，意图本身照常成功。
3. `condition.blocked` 是零状态审计事件，reducer 没有它的分支即等于忽略（与 P8 的 `dnd2024.spell.countered` 同例）；若后续加分支，不得承载状态变更。
3. 不追溯：本切片不处理"已存在的条件在获得免疫后是否移除"，写进 §7。

### 4.5 感官 `senses`

1. 形状：`{"darkvision": 60, "blindsight": 30, "tremorsense": 30, "truesight": 30}`，各档 0–300，缺省不出现。
2. 接线：`blindsight` 与 `truesight` 进入 `observer_senses`，与 F18 玩家侧声明同一条链、同一条距离边界（30 内抵消、31 外不抵消）；敌人不再固定"没有感官"。
3. `darkvision` 只投影（光照模型未实现，属 F30），登记为装而未用；`tremorsense` 同属 B 类。

### 4.6 挑战等级 `cr` / `xp`

1. `cr` 形状：字符串，闭集 `"0" "1/8" "1/4" "1/2" "1"` … `"30"`；整数提交归一化成字符串；未知值拒绝。
2. **`xp` 由内容提供，不由代码推导。** 依据是 stat block 本身就印着 `Challenge 3 (700 XP)`——XP 是这一条怪物的事实，与 hp、AC 同类。代码里不做 CR→XP 表：SRD 不含该表（它在 DMG），硬编码一份就变成"用记忆造数据"。
3. 因此 `xp` 可选但与 `cr` 绑：给了 `xp` 就必须给 `cr`；`xp` 取值 0–355000。第三方内容可为 `xp` 附 `source_ref`，审计时按来源抽查。
4. `proficiency_bonus` **暂不实现**：它由 CR 推导（7 段），而当前没有任何消费者（怪物没有技能检定、没有施法 DC 投影）。等 MM4 的战术层需要它时再落地，避免再造一个"装了没人用"的字段。
5. 消费者：投影（GM 侧可见 CR/XP）；AI 临时遭遇的草稿保留模型给出的 `size` / `cr`（过去在白名单里被丢弃）。

### 4.7 失败关闭与兼容

1. 未知字段：拒绝，错误串包含 `enemy <id>` 与全部未知字段名（排序），便于 GM 自查。
2. 已知字段越界：沿用现有 `CombatIntentError` 风格，错误串带字段名与允许范围。
3. 允许的兼容差异只有一项：旧数据缺新字段。缺 `size`/`senses`/防御三组/`cr` 时装载成功并记为缺省；**缺 `cr` 时 `xp`/`proficiency_bonus` 也一并缺省**，不得推导出 0 或 +2。
4. 4 条既有样本与全部现有测试必须继续通过；`srd_training_encounters.json` 与 `greymoor_encounters.json` 在本切片内补齐 `size`、`cr`（这是它们本来就该有的字段，不算新增内容）。

## 5. 改动白名单

1. `src/rulesets/dnd2024/combat/validation.py`（敌人契约、fail closed、CR/防御/感官校验）
2. `src/rulesets/dnd2024/combat/view.py`（敌人投影）
3. `src/rulesets/dnd2024/combat/resolution.py`（仅 `_resist_damage` 的 statblock 来源接线）
4. `src/rulesets/dnd2024/combat/check_modes.py`（`observer_senses` 读取敌人感官）
5. `src/rulesets/dnd2024/combat/engine.py`（敌人装配处归一化 `speeds`/`size`）
6. `src/rulesets/dnd2024/combat/primitives.py`（如需要，放 `size_at_most` 与尺寸闭集）
7. `src/rulesets/dnd2024/content/contracts.py`（`monster` / `npc_statblock` 契约同步扩展）
8. `src/rulesets/dnd2024/director/temporary_encounter.py`（CR 边界）
9. `src/rulesets/dnd2024/combat/catalog.py`（若 CR 表需要装载入口）
10. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_challenge_ratings.json`（新增，CR→XP）
11. `templates/rulesets/dnd2024_srd/presets/encounters/srd_training_encounters.json`、`templates/adventures/lanterns_of_greymoor/content/encounters/greymoor_encounters.json`（补 `size`/`cr`）
12. 新建 `tests/rulesets/test_dnd2024_monster_facts.py`；本任务书只追加执行报告

不得改：`reducer.py`（唯一例外：`condition.blocked` 在忽略集里登记一行，纯审计、零状态）、移动/几何代码、前端、`templates/rulesets/*/content` 的其它目录。

## 6. 逐项编号测试

1. `size` 闭集：六个值全部接受；`"big"`、`""`、`null`、大写 `"Large"` 全部拒绝（大小写敏感）。
2. 缺 `size` 的旧 intent 装载成功且投影为未知；`size_at_most(actor, "large")` 返回 `False`（fail closed），六个值各自的边界正确（large ≤ large 为真、huge 对 large 为假）。
3. `speed` 兼容：标量 30 → `speeds.walk=30` 且投影仍是 `speed=30`；对象五档 + `hover` 正确；全 0 拒绝；`hover` 无 `fly` 拒绝。
4. 防御闭集：非 `DAMAGE_TYPES` 拒绝；同类型跨组重复拒绝；组内重复拒绝。
5. 抗性结算：immune → 0 且来源可审计；vulnerable → ×2；resist → 向下取整；三者不叠加；与既有条件抗性同时存在时只结算一次。
6. 条件免疫：对免疫条件施加被拦截，产出 `condition.blocked{reason:"immune"}` 且不产出 `condition.applied`；非免疫条件照常。
7. 感官：敌人 `blindsight:30` 在 30 尺内抵消 Invisible/Blur 带来的劣势、31 尺外不抵消；`truesight` 同 F18 语义；无 `senses` 的敌人行为与本切片前完全一致。
8. CR 表：`"1/8"` 解析为 1/8 且 XP 正确；`cr="0"` 的 XP 为 0；PB 各段边界（4/5、8/9、12/13、16/17、20/21、24/25、28/29）正确；表里缺 CR 时装载失败。
9. 未知字段 fail closed：`sizes`、`cr_xp`、`traits` 等任何多余键都被拒绝，错误串同时列出全部未知字段。
10. 既有 4 条样本装载成功、投影含新字段、行为（先攻/攻击/伤害）与切片前逐项一致。
11. AI 临时遭遇路径受同一校验：越界 CR、未知字段同样被拒；合法草稿仍可确认开局。
12. 旧存档/旧事件兼容：`speed` 为标量、无 `size`、带 `saving_throws` 的战斗可继续结算，无新增必填字段导致 replay 失败。

## 7. 非目标与停止条件

**非目标**：stat block 内容库（MM2）、XP 预算与组队规则（MM3）、traits/施法/Multiattack/传奇动作（MM4）、Skills 与 Languages（无权威消费者）、飞行与游泳移动（阶段三）、光照与 Darkvision 效果（F30）、已存在条件在获得免疫后的追溯移除。

**停止条件**：
1. 若发现 `speed` 在事件或存档里被当作标量写死且无法在装载期归一化（例如 position/movement 事件直接读 `actor["speed"]` 之外的第二处副本），停止并报告——不得顺手改造移动系统。
2. 若任一 A 类字段找不到"已有边界"可接（例如 `_resist_damage` 被证明只接受条件来源），改为 B 类登记并说明，不得新造第二条抗性判定。
3. 若 CR→XP 表在规则书里无法逐行核对（只能靠第三方数据），停止该字段，保留 `cr` 不保留 `xp`。
4. 需要改白名单外文件（尤其 `reducer.py`、前端、移动代码）时停止。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_monster_facts.py -q
python -m pytest tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_combat_start.py tests/rulesets/test_dnd2024_damage_reactions.py tests/rulesets/test_dnd2024_invisibility.py -q
python -m pytest tests/rulesets -q
ruff check src/rulesets/dnd2024 tests/rulesets/test_dnd2024_monster_facts.py
mypy
git diff --check
```

## 9. 执行报告模板（执行侧填写）

- 前置提交 / 基线 / 分支 / 最终提交：
- 字段 → 消费者对照表（A 类接线点、B 类装而未用清单）：
- fail closed 的边界（未知字段、越界、旧数据缺字段）：
- 测试 1–12 逐项结果（含 4 条既有样本的逐项对比）：
- 白名单、验收输出、偏差与未决事项（尤其 CR→XP 表来源、`size` 在 F17 之前的可用性声明）：
