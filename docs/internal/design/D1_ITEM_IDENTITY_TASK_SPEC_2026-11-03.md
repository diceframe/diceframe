# 切片任务书 D1：物品身份与装备闭环（Item Identity & Equipment Closure）

- 阶段：1D 物品原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1D 切片顺序表 D1 ——"content-pack item → 规则集 `item_ref` 映射；补 8 护甲 + 23 武器 + 词条字段"
- 前置依赖：**必须在 1C（PR #646）合入、工作区干净后开工**。本切片直接延伸 S-P11/S-P12 落地的 `category`/`strength_minimum`/`stealth_disadvantage`/`mastery` 字段，基线早于 1C 会导致数据语义分叉。
- 锚点基线：`9c2ca59d`（PR #646 head，已含 upstream `fae083bc`）。执行侧须重读工作区版本核对行号漂移。
- 状态：实现与验收完成（2026-11-04，`feat/dnd2024-1d-items`）；验收结果见 `D1_ITEM_IDENTITY_EXECUTION_REPORT_2026-11-04.md`

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

本切片是**数据与身份**切片，不引入新的战斗规则。要落地的只有三条语义：

1. **物品身份唯一且可追溯**：
   - 出处：架构约束而非规则书——`src/rulesets/dnd2024/content/contracts.py` `validate_item_record` 注释"v1 只承载身份 / 类别 / 描述；装备与数量的 mechanics 归 character/economy 侧（母方案 §189：不能同一 item 数量两边 authoritative）"。
   - 语义：content-pack 物品（背包叙事层）要获得规则机械，必须携带指向规则集 item 的 canonical ref；**不得**把 `ac_base`/`damage`/`charges` 等机械字段写进 content-pack item。
   - 已有先例：`character/reconciliation.py:_resolve_item_id` 的优先级固定为 `item_ref` > `item_key` > 显示名（旧档兼容），且 reconcile 会把解析出的 `item_ref` 写回 live 行，使显示名不再被咨询。
2. **装备数据以 SRD 5.2.1 装备表为准逐条补齐**（PHB2024 装备章 / SRD 5.2.1 equipment 表，CC-BY 可分发）：
   - 简易近战 10、简易远程 4、军用近战 18、军用远程 6，合计 38；仓库现有 15，本切片补 23。
   - 护甲按 SRD 5.2.1 护甲表补齐；**计数复核义务**：2026-11-03 已将主计划纠正为总计 13（含 shield）、1C 基线 5/13、缺 8。实测仓库为 4 件护甲（`leather_armor`/`studded_leather_armor`/`chain_shirt`/`chain_mail`）+ `shield`，`robe` 是 `item_type: clothing` 不计入。执行侧开工第一天仍须按 SRD 表复核并在执行报告中登记，不得凭旧计划数字增删。
   - 每件物品三份文件：规则集 item JSON + `locales/en/items/` + `locales/zh-CN/items/` 覆盖。display name 只做显示，不做身份。
3. **Versatile（两用）词条与其消费者**：
   - 出处：PHB2024 装备章武器词条（执行侧须在 `.rulebooks` 定位原文行号并回填；本机无 `.rulebooks`，禁止编造行号）。
   - 语义：两用武器单手持用按 `damage` 骰，双手持用按 `damage_two_handed` 骰；双手持用的前提是副手（含盾）为空。
   - 消费者：`combat/resolution.py` 攻击分支按意图声明选择骰式。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **裁剪：`loading` 词条**。仓库无"一次动作多次攻击"消费者，Loading 的语义（同一动作内不得用该武器攻击第二次）无消费者。按"无消费者的 schema 字段不落"，本切片**不落** `loading` 字段。迁移债 D1-1。
2. **裁剪：`ammunition` 词条与弹药消耗**。现有 longbow/shortbow 即抽象掉弹药，本切片不改变该语义；弹药计数归 D3 消耗品计数。本切片**不落** `ammunition`/`ammo_item_ref`/`ammunition_item_ref`。迁移债 D1-2。
3. **裁剪：`two_handed` 装备门控**。若只对新增武器落门控，会与既有 `greataxe`/`maul` 类武器不一致（它们 RAW 同样需双手）。门控必须一次性覆盖全部武器，单独切片。本切片**不落** `two_handed`。迁移债 D1-3。
4. **裁剪：`reach` 字段**。触及 10 尺直接用既有 `range: 10` 表达，不新增字段。
5. **裁剪：多币种 CP/SP/EP/GP/PP**。主计划标注"可附带"，本切片明确裁剪：货币改动触及 economy 权威与购买路径，与"物品身份"不同域，不该同 PR。迁移债 D1-4。
6. **裁剪：全部魔法物品内容**。`requires_attunement`、`charges`、随机宝物表分别归 D2/D3/D4；本切片不为魔法物品预留任何字段。
7. **裁剪：魔法物品、+N 注入**。+N 效果骑 S-P10 与切片 5 rider，不在本切片。
8. **保留现状：装备槽权威**。`state_items.py:_finalize_equipment_entry` 已有"canonical 绑定的槽位优先、LLM 显式 slot 不得覆盖"的约束，本切片不得放松。
9. **保留现状：数量权威**。inventory 数量仍归 character/economy 侧；本切片不新增数量字段，不让 content-pack 记录数量。
10. **保留现状：旧档名称回退**。`_resolve_item_id` 的显示名回退仅作旧档兼容，新数据必须直接携带 `item_ref`；不得新增按名字猜测的新路径。
11. **不改职业/专长/子职数据**。新增武器的熟练资格沿用既有 `WEAPON_KINDS`（`simple_melee`/`martial_melee_light`），本切片不扩展武器类别集合。

## 2. 代码事实锚点（基线 `9c2ca59d`，行号随工作区漂移需重读核对）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/bundle.py:_load_entities` L190-224 | bundle 装载只校验通用字段（`schema_version=1`/`kind`/`id`/`source_ref`/`automation_level`）+ `_validate_effects` + 路径逃逸；**item 私有字段（`item_type`/`ac_base`/`category` 等）无装载期 schema 校验**，由消费者 fail closed |
| A2 | bundle.py `_validate_references` L225-256 | 任何以 `_ref`/`_refs` 结尾的字段会被当作 `kind:id` 内部引用并要求可解析。**新增字段命名必须避开该后缀，或保证目标实体真实存在** |
| A3 | `src/rulesets/dnd2024/content/contracts.py:validate_item_record` L175-190 | content-pack item 允许字段仅 `{item_id,name,source_ref,category,description}`，未知字段直接 `CatalogContractError`；`category` 必须在 `ITEM_CATEGORIES` 内 |
| A4 | `src/rulesets/dnd2024/content/rewards.py` L1-19 | 奖励桥只产出 reward intent（name/category/provenance），**绝不直接写 inventory**；`filter_unconfirmed_purchase_grants` 闸门原样生效；数量不在此层 |
| A5 | `src/rulesets/dnd2024/character/reconciliation.py` L33 | `_PROJECTED_ITEM_TYPES = {"weapon","armor","shield","focus"}`：只有这四类会进入 canonical `equipment.item_refs`；`clothing`/`consumable` 等不会 |
| A6 | reconciliation.py `_resolve_item_id` L147-169 | 解析优先级 `item_ref` → `item_key` → 规范化显示名/别名；未知返回 `""` |
| A7 | reconciliation.py `_project_equipped_refs` L172-190 | 解析不出规则身份只记 warning 并忽略其规则效果，**绝不猜、绝不删玩家装备**；返回 `list(dict.fromkeys(refs))`（去重，同款多件塌成一件——D2 的 slot map 决策与此直接相关） |
| A8 | reconciliation.py `_annotate_live_rows` L192-226；调用点 L352-353 | reconcile 对 **equipment 与 inventory 两组行**都回填 `item_ref`/`type`/`slot`，使本地化名字不再被咨询 |
| A9 | `src/commands/state_items.py:equip_owned_item` L421-470 | 背包→装备：`take_from_inventory` 按名取件；未拥有且非 legacy_gain 时 warning 并返回 False；槽位旧装备自动退回背包；条目 metadata（`item_key`/`damage`/`slot` 等）equipment↔inventory 往返不丢 |
| A10 | state_items.py `_finalize_equipment_entry` L380-418 | canonical 存在时其数值字段与槽位优先；`custom_damage` 不得覆盖权威伤害；未知槽位丢弃并由服务端推断 |
| A11 | `src/rulesets/dnd2024/combat/catalog.py:from_bundle` L49-62 | 战斗档案校验：`weapons` 的每个 id **必须是对应 bundle item**（`weapon_id in item_ids`），`damage` 必须匹配 `DICE_RE`，`range` 必须 5–600。**新增武器必须同时新增 item JSON，否则装载即失败** |
| A12 | `src/rulesets/dnd2024/combat/weapon_masteries.py` L1-38 | 每把武器在档案里声明一个 `mastery`；`WEAPON_MASTERY_EFFECTS` 中 `graze/sap/vex/slow` 已实现，`cleave/nick/push/topple` 为 `unimplemented`（声明先行、效果后补，fail closed 无事件无报错） |
| A13 | `src/rulesets/dnd2024/character/derivation.py` L62-63 起 | AC 派生按 `item_type` 分支；S-P12 后缺 `category` 的护甲 fail closed（派生期抛错） |
| A14 | `src/rulesets/dnd2024/character/builder.py` L398-404 | 建卡装备行由 item 的 `item_type` 生成 `type`，仅 `weapon/armor/shield/focus` 进入装备；#646 失败 e2e（引导创建校验）若根因为护甲缺 `category`，其断言转为本切片首条验收用例 |
| A15 | `templates/rulesets/dnd2024_srd/locales/{en,zh-CN}/items/<id>.json` | 每物品两份 locale 覆盖；字段受 `_LOCALE_FIELDS` 限制，只放显示文本 |
| A16 | `src/rulesets/dnd2024/features/equipment.py` L79 | 特性装备前提读 combat catalog；新增武器若改变 category 归属会影响特性资格判定，需回归 |

## 3. 白名单改动规格（逐文件）

### 3.1 `templates/rulesets/dnd2024_srd/content/items/*.json`（新增 31 件）

- 23 件武器：`club`、`greatclub`、`light_hammer`、`crossbow_light`、`dart`、`sling`、`battleaxe`、`glaive`、`halberd`、`lance`、`maul`、`morningstar`、`pike`、`rapier`、`trident`、`war_pick`、`warhammer`、`whip`、`blowgun`、`crossbow_hand`、`crossbow_heavy`、`musket`、`pistol`。
  - id 以 SRD 5.2.1 canonical 名为准（下划线连接），执行侧若与现有命名冲突须先登记再改，禁止静默另起 id。
  - 字段：`item_type: "weapon"` + `damage`；近战触及 10 尺不写 `reach`，由战斗档案 `range: 10` 表达。
- 8 件护甲（以 SRD 表复核后为准）：`padded`、`hide`、`scale_mail`、`breastplate`、`half_plate`、`ring_mail`、`splint`、`plate`。
  - 字段必须含 S-P12 三件套：`category`、`strength_minimum`（仅该护甲表列有力量要求时）、`stealth_disadvantage`（仅表列标注劣势时），以及 `ac_base`/`use_dex`/`dex_cap`。
- 全部新文件必须带 `schema_version:1`/`kind:"item"`/`id`/`source_ref`/`automation_level`，source_ref 指向 SRD 5.2.1 对应页（CC-BY 署名，不引用 5etools 或本地规则书文本为分发源）。

### 3.2 `templates/rulesets/dnd2024_srd/locales/{en,zh-CN}/items/*.json`（各 31 份）

- 只写 `_LOCALE_FIELDS` 允许的显示字段；不得携带任何机械字段。

### 3.3 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

- 为 23 件新武器补档案：`category`/`damage`/`damage_type`/`range`（+`long_range`/`thrown_range`/`ranged` 按表）/`mastery`。
- `mastery` 取值必须命中 `WEAPON_MASTERY_EFFECTS`；`cleave/nick/push/topple` 允许声明（unimplemented 语义已有），不得新增第 9 种精通。
- 为既有 `longsword`/`spear`/`quarterstaff` 及新增两用武器补 `damage_two_handed`（见 §3.5）。**两用清单以 SRD 5.2.1 武器表的 Versatile 列逐条核对为准，不凭记忆增删。**

### 3.4 `src/rulesets/dnd2024/combat/catalog.py`

- 在 `from_bundle` 的武器循环中追加 `damage_two_handed` 校验：存在则必须匹配 `DICE_RE`，且只允许出现在声明了两用语义的武器上；非两用武器携带该字段 → fail closed。
- 不为 `loading`/`ammunition`/`two_handed` 增加任何校验分支（字段不落，见 §1.2）。

### 3.5 `src/rulesets/dnd2024/combat/resolution.py` 攻击分支 + `combat/validation.py`

- 两用消费者：`attack` 意图新增可选 `two_handed: bool`（默认 false）；仅当武器声明 `damage_two_handed` 时允许为 true。
- 当前 actor 只有去重后的 `equipment_refs`，没有副手槽位。故本切片采用**保守门控**：装备中存在盾或另一把武器时拒绝 `two_handed=true`；不得声称已经实现了精确副手判定，也不得为此夹带 slot map 改造。
- 伤害骰选择：`two_handed` 为 true 时用 `damage_two_handed`，否则 `damage`；重击翻倍、S-P2 骰最大化、S-P10 加值管线必须对该骰式同样生效。
- 不得把该判断下沉到通用 `attack_mode` 或敌方攻击路径（敌方 statblock 无两用概念）。

### 3.6 `src/rulesets/dnd2024/content/contracts.py`

- `validate_item_record` 新增**可选**字段 `ruleset_item_ref`：格式必须 `item:<id>`，未知字段拒绝规则不变；该字段只表达身份映射，不携带机械。
- 不在这里校验目标是否存在于 bundle（contracts 层无 bundle 上下文）；存在性校验由既有 reconciliation 消费侧完成。

### 3.7 `src/rulesets/dnd2024/content/rewards.py`

- `reward_intent_from_ref` 在记录声明 `ruleset_item_ref` 时，把它翻译为 reward intent 的可选 `item_ref`；未声明时输出保持原样。
- 此处只透传身份，不读取或复制规则 item 的任何机械字段，不直接写 inventory，既有 proposal/GM 闸门不变。

### 3.8 `src/webui/api.py` + `src/webui/services/characters.py` + `src/commands/state_items.py`

这三处是当前实际断点：`_queue_adventure_reward_intents` 只保留 name/category，`grant_reward` 与 `grant_classified_item` 也只按名字入库。必须全链修复，不能只改 contracts/rewards 两处：

- `_queue_adventure_reward_intents` 的 `named` reward 保留可选 `item_ref`，作为**不透明身份 metadata**；通用 webui 层不得解释 D&D 规则。
- `grant_reward` → `grant_classified_item` → `add_owned_equipment_to_inventory` / `append_inventory_item` 逐层透传并写入背包行；普通 LLM/玩家叙事奖励入口不得新增提交该字段的能力。
- 背包合并键必须包含 `(item_ref or "")`：同名但 ref 不同的行不得合并，带 ref 的奖励也不得被同名无 ref 旧行吸收。数量仍由既有 character/economy 权威维护。
- 装备操作在同名多行时优先按 `item_ref` 取件；只有名字且无法唯一定位时 fail closed，不猜。`take_from_inventory` 的按名匹配保留为旧档兼容。
- 槽位权威、`legacy_gain` 兼容路径、metadata 往返语义全部保留。

### 3.9 `src/rulesets/dnd2024/character/reconciliation.py`

- **预期零实现改动**：既有 `_resolve_item_id` 已以 `item_ref` 为最高优先级，`_annotate_live_rows` 已会回填 type/slot。本切片只追加测试证明该能力被奖励链真正喂到。
- 不得在 reconciliation 再读取一次 `ruleset_item_ref`；那是 content-pack 合同字段，必须在 §3.7 已翻译成通用 `item_ref`，避免第二套身份优先级。
- 若目标 ref 不存在，沿用既有 warning + 不给规则效果 + 不删行；二次 reconcile 不得重复改写或刷 revision。

### 3.10 tests

- 新增 `tests/rulesets/test_dnd2024_item_identity.py`（身份映射与 fail closed）。
- 新增 `tests/rulesets/test_dnd2024_equipment_data.py`（31 件数据 + 档案 + locale + 两用消费者）。
- 既有 `tests/test_dndmod_rewards.py`、冒险奖励端到端测试与 `tests/rulesets/test_dnd2024_live_equipment_reconciliation.py` 只允许追加用例，不得改写既有断言语义。
- 若 A14 的 #646 e2e 根因坐实为护甲缺字段，在 `frontend-v2/e2e/dnd2024-workspace.spec.ts` 的对应用例上补注释指向本切片，不改动其断言语义。

## 4. 测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| T1 | content-pack 物品（带 `ruleset_item_ref`）经奖励桥入包 | 背包行获得 `item_ref`；不写入任何机械字段；数量仍由 economy 侧权威 |
| T2 | 同一背包行二次 reconcile | `item_ref`/`type`/`slot` 不变，revision 不刷，幂等 |
| T3 | `ruleset_item_ref` 指向不存在的 item | 只 warning、不给规则效果、不删除行、不猜最像物品 |
| T4 | content-pack item 携带未知字段 | `CatalogContractError`（fail closed 不回退） |
| T5 | 同名不同 ref 入包，随后装备/卸回 | 不同 ref 不合并、带 ref 奖励不被无 ref 旧行吸收；按 ref 装备后 `damage`/`slot` 往返不丢，不从显示名重猜 |
| T6 | 中文/英文显示名与 canonical id 不一致 | 机械判定只读 ref，locale 名不参与 |
| T7 | 23 件新武器逐件装载 | 每件都是 bundle item、`damage` 合 `DICE_RE`、`range` 5–600、`mastery` 命中注册表 |
| T8 | 8 件新护甲逐件派生 AC | 轻/中/重与 `dex_cap` 正确；缺 `category` 的构造样本派生期抛错（S-P12 语义不回归） |
| T9 | 新护甲的力量要求与隐匿劣势 | `strength_minimum` 超力量 → 速度 -10；`stealth_disadvantage` 投影正确 |
| T10 | 两用武器单手/双手 | 单手 `damage`、双手 `damage_two_handed`；副手有盾时 `two_handed=true` 被拒 |
| T11 | 非两用武器携带 `damage_two_handed` | 装载 fail closed |
| T12 | 两用 + 重击 + S-P2 最大化 + S-P10 加值 | 骰式切换后四条管线行为一致 |
| T13 | 全部 31 件 locale 覆盖 | en/zh-CN 均存在且只含显示字段；缺失显示名不阻断机械 |
| T14 | 战利品 → 装备 → 一次攻击/AC 端到端 | 从 content-pack 奖励到战斗数值的链路闭环（本切片的核心验收） |
| T15 | 既有回归 | `test_dnd2024_live_equipment_reconciliation.py` 与 1C 全部目标测试零断言改动通过 |

## 5. 验收命令

```bash
python -m pytest -q tests/rulesets/test_dnd2024_item_identity.py tests/rulesets/test_dnd2024_equipment_data.py
python -m pytest -q tests/rulesets/test_dnd2024_live_equipment_reconciliation.py
python -m pytest -q tests/rulesets -k "armor_training or weapon_mastery or class_features or combat"
python -m pytest -q --cov=src
```

前端仅在改动 `frontend-v2/e2e` 注释时运行对应检查；本切片不新增前端功能。

## 6. 禁止事项

- 禁止把机械字段（`ac_base`/`damage`/`charges`/`requires_attunement`）写进 content-pack item。
- 禁止新增 `loading`/`ammunition`/`two_handed` 字段（无消费者，见 §1.2）。
- 禁止用显示名、别名、模糊匹配为未知物品指定规则身份。
- 禁止为新增字段使用 `_ref`/`_refs` 后缀而不提供可解析目标（A2）。
- 禁止修改既有测试的断言语义来"让测试通过"。
- 禁止在同一 PR 中夹带多币种、魔法物品、+N 注入、随机表或阶段二内容批量填充。
- 禁止改动 1C 未合入分支上的文件内容；本切片基线必须是含 1C 的 main。

## 7. 停止条件

- SRD 5.2.1 装备表与主计划计数不一致且无法确认以哪份为准 → 停下来登记，不猜数据。
- 新增武器需要第 9 种武器精通或新的伤害类型 → 停止，回到主计划补原语，不在本切片扩注册表。
- 两用消费者需要改敌方攻击路径或通用 `attack_mode` → 停止，说明作用域推论被推翻。
- 身份闭环要求 content-pack 层持有数量或机械 → 停止，违反母方案 §189。
- #646 的 e2e 失败根因与本切片无关 → 不阻塞本切片，但必须在执行报告中写明根因，不得顺手修。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

- 基线 commit 与行号漂移核对结果
- SRD 装备表计数复核结果（与主计划 5/13、15/38 的差值）
- T1–T15 逐条结果与输出
- 白名单符合性（逐文件列出实际改动）
- 偏差与审核裁决

### 迁移债登记（本切片显式产生，禁止在后续切片里"顺手"处理）

| # | 债务 | 归属建议 |
|---|---|---|
| D1-1 | `loading` 词条未落（无多次攻击消费者） | 多次攻击/Extra Attack 切片落地后回补 |
| D1-2 | 弹药计数未落，远程武器仍抽象弹药 | D3 消耗品计数 |
| D1-3 | `two_handed` 装备门控未落，需一次覆盖全部武器 | 独立装备门控切片 |
| D1-4 | 多币种 CP/SP/EP/GP/PP 未落 | 独立货币切片（economy 权威） |
| D1-5 | canonical 装备去重导致同款多件塌成一件 | D2 同调的 set→slot map 决策 |
