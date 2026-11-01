# 切片任务书 S-P12：护甲受训门控（Armor Training）

- 阶段：1C 共用原语
- 主计划条目：`docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` §1C 切片表 S-P12 ——"护甲受训门控｜未受训穿甲 → 力量/敏捷检定劣势 + **不能施法**；含力量要求（速度 -10）、隐匿劣势、穿脱时长；直接影响战斗引擎与施法校验（Armor Training 专长同源）"
- 前置依赖：改动 `combat/resolution.py`，与 1B 的 P6（misty_step）同文件。**必须在 P6 合入、工作区干净后开工**；当前工作区 resolution.py / check_modes.py / validation.py / engine.py 的未提交修改是 P6 会话半成品，一律不得触碰。本任务书锚点基于 `1009be03`，执行侧须重读工作区版本核对行号漂移。
- 状态：未开工

## 1. 规则语义与原文出处

### 1.1 本切片要落地的规则

1. **护甲受训（Armor Training）核心条款**：
   - 出处：`.rulebooks/dnd5r/玩家手册2024/装备/护甲.htm` L11-19 ——"如果你穿上了没有受训的轻型、中型或重型护甲，你在所有涉及力量或敏捷的 D20 检定上都具有劣势，并且你不能施法。"
   - 语义拆解（"D20 检定" = 2024 术语，涵盖攻击检定、属性检定、豁免检定三类）：本切片落地其中**攻击检定**与**豁免检定**两个面（属性检定面归 P10，见 §1.2 裁剪 4）；"不能施法"覆盖主动施法意图与受击反应施法。
   - 攻击检定面的作用域推论（已核实代码，锚点 A12）：玩家/companion 的**武器与徒手攻击掷骰**的 ability 决策全为 str 或 dex（远程→dex、近战→str、finesse/武艺取高者），因此未受训劣势对武器攻击掷骰**无条件适用**、无需按 ability 过滤；而 `spell_attack` 分支的攻击用施法属性（int/wis/cha），**不适用**——这是本切片不把劣势来源塞进通用 `attack_mode` 的根本原因（锚点 A13：法术攻击也调用 `attack_mode`，通用注入会误伤）。
2. **盾牌受训**：
   - 出处：同文件 L21-22 ——"你必须拥有盾牌的受训才能从一面盾牌中获得其护甲等级的增益。"
   - 语义：未受训持盾 → `derive_armor_class` 不计盾牌 +2；盾牌没有轻/中/重类别，不参与"劣势+不能施法"条款。
3. **力量要求（速度罚值）**：
   - 出处：同文件 L8 ——"如果护甲列表中的力量一列为某款护甲标注出了力量属性值，那么穿戴者必须要有等于或高于这一数值的力量属性，否则移速减少 10 尺。"
   - 语义：所穿任一护甲 `strength_minimum` 超过当前力量 → 移速 -10（多件罚值不叠加，就是 10；脱甲即恢复）。落点：canonical 基础速度不变，战斗 actor 的 `speed` 净值投影时扣减（见 §3.4），回合移动预算消费端零改动（锚点 A19）。
4. **隐匿劣势（Stealth）**：
   - 出处：同文件 L8 ——"如果护甲列表中的隐匿一列为某款护甲标注出了'劣势'，那么穿戴者在着装这款护甲时所进行的敏捷（隐匿）检定将具有劣势。"
   - 语义：与受训与否无关——受训穿链甲同样隐匿劣势。本切片落地**数据 + 派生标记**；隐匿检定的运行时消费在 S-P8（Hide 动作）合入后接线（见 §1.2 裁剪 2）。
5. **未受训护甲的 AC 照常计算**：RAW 未说未受训护甲不给 AC——穿未受训板甲 AC 依然是 18。`derive_armor_class` 的 armor 分支不动；只有盾牌分支接受训判定（条款 2）。
6. **职业受训数据**（`.rulebooks/dnd5r/玩家手册2024/角色职业/<职业>/<职业>.htm` Armor Training 行）：战士 L20（轻中重+盾）、野蛮人 L17（轻中+盾）、牧师 L19（轻中+盾）、圣武士 L21（轻中重+盾）、游侠 L20（轻中+盾）、德鲁伊 L21（轻+盾）、吟游诗人 L20（轻）、游荡者 L20（轻）、魔契师 L16（轻）、法师 L17（无）、术士 L19（无）、武僧 L23（无）。与 `classes/*.json` 现有 `armor_proficiencies` 值一致（锚点 A21/A22），本切片不改职业数据。
7. **受训判定的数据完备性要求**：受训按类别判定（`armor_category:light/medium/heavy/shield`）；穿着的护甲若缺类别数据则无法判定，**fail closed**——视为不可判定的非法目录数据，派生期抛错（§3.2），不允许静默当作受训。

### 1.2 边界与裁剪（显式声明，禁止执行者自行扩大/缩小）

1. **裁剪：穿脱时长（don/doff）**。轻甲 1 分钟、中甲穿 5 卸 1、重甲穿 10 卸 5、盾牌操作动作（护甲.htm L39/L62/L99/L129）。live 装备变更是角色卡层操作而非战斗内意图，当前无任何时长/回合消耗系统可消费该数据；无消费者的 schema 字段不落。迁移债见 §8。
2. **裁剪：隐匿劣势的运行时消费**。S-P8（Hide 动作与隐匿检定）任务书已落盘、实现未合入，HEAD 基线没有隐匿检定掷骰路径。本切片交付 `stealth_disadvantage` 的 item 数据、派生标记与 actor 投影；hide 检定的双骰消费由 SP8 实现时接线（消费 `actor["armor_training"]["stealth_disadvantage"]`）。迁移债见 §8。
3. **裁剪：Armor Training 三个专长**（轻甲运用/中甲运用/重甲运用，通用专长.htm L321-333/L385-397/L258-270）。advancement catalog 与 `content/feats/` 均无这三个专长的声明（锚点 A23），内容补充归阶段二；本切片落地的 `armor_category_refs` 消费原语到位后，专长只需 parameters 扩键 + derivation 落账，不需要新管线。
4. **裁剪：属性检定面的力/敏劣势**。"所有涉及力量或敏捷的 D20 检定"的 ability check 面需要通用属性检定 intent，归 P10（主计划已注明 Remarkable Athlete 等归 P10）。本切片的豁免面覆盖 str/dex 豁免，不新建检定 intent。
5. **裁剪：其余 9 种护甲内容**（布甲/兽皮甲/鳞甲/胸甲/半身板甲/环甲/板条甲/板甲）。阶段二内容填充批次按本切片定义的 item schema 补数据；本切片用现有 5 件（4 甲 + 1 盾，覆盖轻/中/重 + 盾 + 力量要求 + 隐匿劣势全部验证维度）作端到端验证载体。其中 chain_mail 同时具备重甲、力量 13、隐匿劣势三个属性，是主验证载体。
6. **裁剪：Medium Armor Master（敏 16 中甲 AC 敏捷上限 3）/ Heavy Armor Master（重甲钝刺挥砍减熟练值）**。独立专长机械，归各自后续切片（前者 S-P5 族 AC 管线扩展，后者伤害减免原语），不在本切片。
7. **裁剪：前端展示**。`armor_training` 是 canonical 内部机械段 + 事件 `advantage_sources` 可审计字段，前端不消费（已 grep 验证 frontend-v2 无 `armor_training` 命中）；legacy mirror（`sheet["armor_training"]`）不加，等有真实消费者再镜像。
8. **保留现状：不拒穿**。未受训穿甲在 RAW 下合法（代价是门控），建卡 validation 与 live 装备层**不加**任何拒绝逻辑；`derive_armor_class` 对多甲穿脱的"取最优一件"非法 live state 兜底保持。
9. **保留现状：职业受训值域不校验**。`armor_proficiencies` 的值（light/medium/heavy/shield）经 `f"armor_category:{item}"` 包装后精确匹配判定，非法值天然不匹配 → 未受训（fail closed 方向正确），不加目录校验。
10. **不适用面显式排除**：怪物/enemy actor 不参与（statblock 直接携带终值 AC/速度，无护甲受训概念，锚点 A7 敌方构造无 equipment_refs）；武僧"武术"的 unarmored 前提与本切片不相交（前提是"没穿甲"，门控是"穿了没受训的甲"）。
11. **事件契约零新增**：攻击/豁免的劣势走既有 `advantage_mode` + `advantage_sources` 事件字段（runtime 白名单已含，锚点 A24），不新增事件字段、不改 runtime 投影白名单。

## 2. 代码事实锚点（基线 `1009be03`，行号随工作区漂移需重读核对）

| # | 位置 | 事实 |
|---|---|---|
| A1 | `src/rulesets/dnd2024/character/derivation.py` L14-70 | `derive_armor_class` 全公式：unarmored=10+dex；armor item 取 `ac_base`+`use_dex`/`dex_cap` → 多甲取最大不相加；shield → `ac_bonus` 不叠加；未知 item 警告跳过。docstring 声明"建卡与 live 装备调和共用此唯一公式" |
| A2 | derivation.py L148 | 建卡期调用 `derive_armor_class(abilities, item_refs, self.bundle)`——**必改调用点①** |
| A3 | derivation.py L262-266 | `armor_category_refs = [f"armor_category:{item}" for item in class_entity.get("armor_proficiencies", [])]` 落账进 canonical `proficiencies` 段——受训判定的既有数据源 |
| A4 | derivation.py L295-301 | canonical `derived` 段：`armor_class` / `speed = int(species["speed"])`（基础速度）/ `saving_throws`——`armor_training` 新键落同段 |
| A5 | `src/rulesets/dnd2024/character/reconciliation.py` L33 | `_PROJECTED_ITEM_TYPES = {"weapon", "armor", "shield", "focus"}` |
| A6 | reconciliation.py L172-190 | `_project_equipped_refs`：live 装备行 → canonical refs（§64：解析不出规则身份只警告不猜） |
| A7 | reconciliation.py L313-365 | `_reconcile`：取 `canonical["abilities"]` → `derive_armor_class` 重算 → **幂等短路三元组**（previous_refs == item_refs 且 previous_ac == armor_class 且 mirror_ac == armor_class）→ 写 `canonical_equipment["item_refs"]` / `derived["armor_class"]` / mirror `sheet["armor_class"]` + `projection_fields` 刷新 + revision+操作日志。**当前不重算 speed、不感知受训**——**必改调用点②+幂等段** |
| A8 | `src/rulesets/dnd2024/combat/view.py` L53-89 | `_actor_view` 分发：player → `_player_view`；companion → **复用 `_player_view`**（L60-65，改一处两 kind 全覆盖）；enemy L66-83 构造无 equipment_refs / 无 derived 概念（天然不适用） |
| A9 | view.py L131-139 | `_player_view` 玩家 actor：`armor_class` / `speed` 从 `character["derived"]` 静态投影；`equipment_refs` = `equipment.item_refs` |
| A10 | `src/rulesets/dnd2024/combat/validation.py` L407-427 | `_validate_spell`：kind 检查 → spell/effect 存在 → prepared → reaction_defense 拒绝 → trigger → cost → slot → target。"不能施法"门控落点 |
| A11 | `src/rulesets/dnd2024/combat/resolution.py` L249-255 | attack 分支：`check_mode = attack_mode(...)` → adv/dis → 双骰 keep-high/low |
| A12 | resolution.py L200-245 | 玩家/companion attack 分支 ability 决策：`ranged_use→dex else str`、finesse 取高、`ability_choice`（武艺）取高——**全为 str/dex**，武器攻击掷骰无需按 ability 过滤劣势 |
| A13 | resolution.py L406-437 | **spell_attack 分支同样调用 `attack_mode`**（L410，invisibility 感知）——untrained 劣势若进通用 attack_mode 会误伤法术攻击，必须内联在 attack 分支 |
| A14 | resolution.py L474-510 | 敌方施法豁免分支：`save_ability = effect["save"]`；auto_fail 走 `condition_flags`；`rage_advantage`（L490）硬编码双骰取高；L504-507 `advantage_mode`/`advantage_sources` 事件先例 |
| A15 | resolution.py L966-1008 | `_save_repeat_events`（hold person 类重复豁免）同构分支；auto_fail 分支 `bless_bonus: 0` 不掷骰 |
| A16 | `src/rulesets/dnd2024/combat/check_modes.py` L72-97 | `attack_sources`（扫 conditions 注册表 + long_range + gm_grant）/ `mode_from_sources`（modes 集合唯一才返回，否则 ""——优劣势相消语义）——**本切片不改动本文件**，只导入 `mode_from_sources` 复用相消语义 |
| A17 | `src/rulesets/dnd2024/combat/engine.py` L1006-1012 | `_fresh_economy`：`base = actor["speed"]` → `effective_speed(conditions)` → movement 预算。actor.speed 投影为净值后消费端零改动 |
| A18 | `src/rulesets/dnd2024/combat/reaction_spells.py` L42-65 | `_reaction_spell_choices`（Shield 受击反应决策提供窗口）：守卫序列（kind/hp/反应预算/reaction_blocked/shield/incapacitated）后构造 choices——untrained 施法屏蔽落点 |
| A19 | reaction_spells.py L67-123 | `_reaction_feature_choices`（Uncanny Dodge，S-P3 域）：同守卫——**不改动**（特性反应不是施法） |
| A20 | `src/rulesets/dnd2024/features/models.py` L34 + `features/equipment.py` | `EQUIPMENT_REQUIREMENT_KEYS = {"unarmored", "no_shield", "weapon_kinds"}` 封闭键集 + 装备前提唯一求值家——本切片不改装备前提，受训是新维度 |
| A21 | `templates/rulesets/dnd2024_srd/content/items/` | armor item 文件：`leather_armor.json`（ac_base 11/use_dex）、`studded_leather_armor.json`（12/use_dex）、`chain_shirt.json`（13/use_dex/dex_cap 2）、`chain_mail.json`（16/use_dex false）、`shield.json`（ac_bonus 2）——**全部没有 category/strength_minimum/stealth_disadvantage 字段**（本切片核心数据缺口）。items 目录无 locale 镜像（deterministic 数据无 labels） |
| A22 | `templates/rulesets/dnd2024_srd/content/classes/*.json` | 各职业 `armor_proficiencies` 值与规则原文一致（fighter 全 4 项、barbarian/cleric/paladin/ranger 3 项、druid 轻甲+盾、bard/rogue/warlock 仅轻甲、monk/sorcerer/wizard 空）——不改 |
| A23 | `content/feats/` + advancement catalog | 无任何 armored 专长声明（6 文件 = 4 专长：alert/magic_initiate×3/savage_attacker/skilled） |
| A24 | `src/rulesets/dnd2024/runtime.py` L1119-1130 | `_recent_combat_events.allowed_fields` 已含 `advantage_mode` / `advantage_sources`——**零白名单改动** |
| A25 | `tests/rulesets/test_dnd2024_live_equipment_reconciliation.py` L320、L595 | `derive_armor_class` 直接断言 2 处——签名改后**必改测试调用点③④** |
| A26 | `src/rulesets/dnd2024/progression/catalog.py` | class bundle 加载校验不含 `armor_proficiencies` 值域（fail closed 由 ref 精确匹配天然成立） |

## 3. 白名单改动规格（逐文件）

### 3.1 `templates/rulesets/dnd2024_srd/content/items/*.json`（4 个 armor 文件；shield 不动）

新增封闭键集 `category` ∈ {"light", "medium", "heavy"}（armor 必填，fail closed 见 §3.2）、`strength_minimum`（int，缺省无要求）、`stealth_disadvantage`（bool，缺省 false）：

- `leather_armor.json`：`"category": "light"`
- `studded_leather_armor.json`：`"category": "light"`
- `chain_shirt.json`：`"category": "medium"`
- `chain_mail.json`：`"category": "heavy", "strength_minimum": 13, "stealth_disadvantage": true`
- `shield.json`：**不改**（盾牌无类别；`armor_proficiencies` 的 `"shield"` 值包装成 `armor_category:shield` 后由 `derive_armor_class` 消费）

其余 9 种护甲内容归阶段二（§1.2 裁剪 5），执行者不得顺手补齐。

### 3.2 `src/rulesets/dnd2024/character/derivation.py`

1. `derive_armor_class` 签名追加**必填 keyword-only 参数** `armor_category_refs: Collection[str]`（无默认值——漏传在调用期即暴露，禁止给"默认受训"的兼容默认值）：
   - shield 分支改为仅当 `f"armor_category:shield" in armor_category_refs` 时计入 `ac_bonus`；
   - armor 分支**不动**（未受训甲 AC 照常，RAW §1.1 条款 5）；
   - 调用点：L148 建卡传本职业 `armor_category_refs`（A3 落账值）。
2. 新增模块级函数（紧邻 `derive_armor_class`，同一 authority 谱系）：

   ```python
   def derive_armor_training_state(
       abilities, equipped_item_refs, armor_category_refs, bundle,
   ) -> dict:
   ```

   返回 `{"untrained_armor_worn": bool, "speed_penalty": int, "stealth_disadvantage": bool}`：
   - 遍历 `equipped_item_refs` 中 `item_type == "armor"` 的条目；`category` 缺失或不属于 {"light","medium","heavy"} → 抛 `ValueError`（消息含 item id 与非法值；fail closed，不猜）；
   - `untrained_armor_worn`：任一穿着甲的 `f"armor_category:{category}"` 不在 `armor_category_refs` → True；
   - `speed_penalty`：任一穿着甲 `strength_minimum` > `int(abilities["str"])` → 10，否则 0（多甲不叠加）；
   - `stealth_disadvantage`：任一穿着甲 `stealth_disadvantage` 为真 → True；
   - 盾牌不参与（无类别）；未知 item 引用跳过不判（与 `derive_armor_class` 的警告容错一致，但本函数不产 warnings——派生失败靠 category 校验兜底）。
3. `CharacterDerivationMixin.derive` 段：
   - 调用 `derive_armor_training_state(abilities, item_refs, armor_category_refs, self.bundle)`（`armor_category_refs` 为 A3 落账前的本地列表）；
   - canonical `derived` 段新键 `"armor_training": armor_training`（A4 段）；
   - **`"speed"` 保持 `int(species["speed"])` 不动**——基础速度语义不变，罚值在 view 投影时扣（§3.4），避免 reconciliation 需要存储 base。

### 3.3 `src/rulesets/dnd2024/character/reconciliation.py`

`_reconcile`（A7 段）：

1. `armor_class` 行之前：`armor_training = derive_armor_training_state(abilities, item_refs, canonical_armor_category_refs, self.bundle)`，其中 `canonical_armor_category_refs = list((canonical.get("proficiencies") or {}).get("armor_category_refs") or [])`（缺失=空列表=fail closed 全未受训）；
2. `derive_armor_class` 调用传第 4 参 `canonical_armor_category_refs`；
3. 写入段 `derived["armor_class"] = armor_class` 旁并列 `derived["armor_training"] = armor_training`；
4. **幂等短路条件扩展**：在现有三元组外增加 `previous_training = derived.get("armor_training")` 与 `previous_training == armor_training` 的比较——受训状态变化（换装/力量变化导致）即使 AC 恰好不变也必须穿透幂等（例如皮甲换链甲衫 AC 相同但 training 段不同）；
5. mirror 不加 `sheet["armor_training"]`（§1.2 裁剪 7）；`speed` 不在此重算（§3.2 条款 3 的设计决定）。

### 3.4 `src/rulesets/dnd2024/combat/view.py` `_player_view`

1. actor 新键（A9 段，与 `equipment_refs` 并列）：

   ```python
   "armor_training": deepcopy(character.get("derived", {}).get("armor_training") or {}),
   ```

2. `speed` 投影改为净值：

   ```python
   "speed": max(0, int(character.get("derived", {}).get("speed", 30) or 30)
       - int((character.get("derived", {}).get("armor_training") or {}).get("speed_penalty", 0) or 0)),
   ```

   companion 经 A8 复用自动继承；enemy 构造不涉及。`_feature_character`（L346-362）不投影 `armor_training`——装备前提（A20）不需要它。

### 3.5 `src/rulesets/dnd2024/combat/validation.py` `_validate_spell`

kind 检查（L409）之后、"spell is not deterministically automated" 检查之前插入：

```python
if bool((actor.get("armor_training") or {}).get("untrained_armor_worn")):
    raise CombatIntentError("armor you are not trained in blocks spellcasting")
```

- 消息固定为该字符串（可审计、可测试断言）；
- 该门控覆盖**所有**主动施法意图（含 healing/buff/teleport/stabilize 等全 mode）；
- `reaction_defense` 的主动施法拒绝（L418）在其后，顺序无冲突。

### 3.6 `src/rulesets/dnd2024/combat/resolution.py` attack 分支

L251 `check_mode = attack_mode(...)` 之后、L252 `advantage, disadvantage = ...` 之前注入（**内联，不进 check_modes.py**，理由锚点 A13）：

```python
if bool((actor.get("armor_training") or {}).get("untrained_armor_worn")):
    sources = [*check_mode["sources"], {"code": "untrained_armor", "mode": "disadvantage"}]
    check_mode = {"mode": mode_from_sources(sources), "sources": sources}
```

- `mode_from_sources` 从 `.check_modes` 导入（L249 既有 import 旁追加）——优劣势相消（faerie_fire 优势 + untrained 劣势 → mode ""）自动复用既有语义；
- 武器/徒手攻击全为力/敏掷骰（A12），无 ability 过滤；`spell_attack` 分支（L406-437）**不注入**；
- 事件字段（L276-278）自动携带 mode/sources，零契约改动。

### 3.7 `src/rulesets/dnd2024/combat/resolution.py` 豁免两分支

敌方施法豁免（L474-510）与 `_save_repeat_events`（L966-1008）同构改造，每分支掷骰段改为：

```python
untrained_disadvantage = bool(
    (target.get("armor_training") or {}).get("untrained_armor_worn")
) and save_ability in {"str", "dex"}
```

- 四组合掷骰：none → 单骰（现行为）；仅 rage 优势 → 双骰取高（现行为）；仅 untrained → 双骰取低；**rage 优势 + untrained 劣势同时 → 单骰（优劣势相消，RAW）**；
- 事件字段按组合写（扩展 L504-507 先例）：`advantage_mode` ∈ {"advantage", "disadvantage", ""}，`advantage_sources` = `[{"code": "rage", "mode": "advantage"}]`（rage 时）与 `[{"code": "untrained_armor", "mode": "disadvantage"}]`（untrained 时）按实际生效项拼接；相消时 `advantage_mode: ""`、sources 含两项；
- auto_fail 分支（两处）**不动**——自动失败不掷骰，先于一切优劣势；
- concentration 豁免（engine.py L1070-1092）是 con 豁免，不在 str/dex 集合，**无需改动**（执行侧加一个注释性断言测试即可）。

### 3.8 `src/rulesets/dnd2024/combat/reaction_spells.py` `_reaction_spell_choices`

守卫段（L43-45 与 L46 之间语义位，具体插入点执行时按最新行号）追加一行：

```python
if bool((defender.get("armor_training") or {}).get("untrained_armor_worn")):
    return []
```

- 未受训穿甲者受击时**不提供** Shield 决策（不能施法含反应施法）；
- `_reaction_feature_choices`（A19）**不加**——Uncanny Dodge 是特性不是施法。

### 3.9 tests

- 新增 `tests/rulesets/test_dnd2024_armor_training.py`（与 `test_dnd2024_live_equipment_reconciliation.py` 同级）：T1-T9、T17；
- combat 消费用例（T11-T16、T18）并入 `tests/rulesets/rulesets/test_dnd2024_combat.py`（`SequenceRng` 基建 L20 既有）；
- 更新 `tests/rulesets/test_dnd2024_live_equipment_reconciliation.py` L320/L595 两处 `derive_armor_class` 调用传受训 refs（既有断言期望值不变——那些测试的装备组合盾牌是否受训须逐处核实，期望值按规则重推）。

## 4. 测试矩阵

| # | 文件 | 用例 | 断言要点 |
|---|---|---|---|
| T1 | armor_training | chain_mail 数据加载 | category=heavy / strength_minimum=13 / stealth_disadvantage=true；4 甲 + shield bundle 可读 |
| T2 | armor_training | 盾牌受训 AC | fighter（含 shield 受训）+2；wizard（无）盾牌不计入；`derive_armor_class` 新签名 |
| T3 | armor_training | 未受训甲 AC 照常 | wizard 穿皮甲 AC = 11+dex（不受无 light 受训影响） |
| T4 | armor_training | 全受训零门控 | fighter 穿链甲衫持盾：全 false / penalty 0 / 可施法 / attack 无劣势来源 |
| T5 | armor_training | wizard 穿链甲 | str 12 → untrained_armor_worn=True + speed_penalty=10 + stealth_disadvantage=True |
| T6 | armor_training | 力量达标 | str 13 → speed_penalty=0（其余门控不变） |
| T7 | armor_training | fail closed | armor 无 category（临时破坏目录）→ `ValueError` 含 item id |
| T8 | armor_training | 建卡落账 | derivation 后 canonical `derived.armor_training` 段存在且正确；`derived.speed` 仍是种族速度 |
| T9 | armor_training | reconciliation 换装 | live 换 chain_mail（str12）→ AC 16 + training 段更新；二次 reconcile 幂等返回 None；皮甲换链甲衫（AC 同为 13+2cap? 按 AC 恰好相等组合构造）幂等被 training 段穿透 |
| T10 | armor_training | actor 投影 | actor.speed = derived.speed - penalty（下限 0）；armor_training 段 deepcopy |
| T11 | combat | 施法门控 | wizard 未受训重甲 cast_spell → `CombatIntentError("armor you are not trained in blocks spellcasting")`；受训甲正常施放 |
| T12 | combat | 反应屏蔽 | 未受训重甲受击 → `_reaction_spell_choices` 返回空（无 Shield 决策）；受训正常提供 |
| T13 | combat | 攻击劣势 | untrained attack：`advantage_sources` 含 `{"code": "untrained_armor", "mode": "disadvantage"}`、双骰取低；叠加 faerie_fire 优势 → mode "" 单骰 |
| T14 | combat | 豁免劣势 | untrained str 豁免双骰取低 + `advantage_mode: "disadvantage"`；wis 豁免单骰不受影响 |
| T15 | combat | 豁免相消 | rage + untrained 的 str 豁免 → 单骰、`advantage_mode: ""`、sources 两项；save_repeat 分支同构一例 |
| T16 | combat | 法术攻击不误伤 | untrained 施法者（若能施法：使用受训轻甲+未受训重甲组合不可行——改用自定义：untrained 甲在场但 spell_attack 用 int/wis 属性）→ 单骰、sources 无 untrained_armor |
| T17 | armor_training | companion | companion `ruleset_character` 含 derived 段时 `_actor_view` 投影继承（若 party 结构不支持，按 §7 停止条件 4 处理） |
| T18 | 回归 | 全套 | `derive_armor_class` 既有 2 处测试调用更新后全绿；无 events 字段白名单变化；concentration（con）路径零 diff |

## 5. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_armor_training.py tests/rulesets/test_dnd2024_live_equipment_reconciliation.py -x -q
python -m pytest tests/rulesets/rulesets/test_dnd2024_combat.py -x -q
python -m pytest tests/rulesets -x -q
git grep -n "untrained_armor" -- src        # 确认无 check_modes.py 命中
git grep -n "armor_training" -- src         # 确认改动面收敛在白名单文件
```

## 6. 禁止事项

1. 禁止改动 `check_modes.py`（`attack_sources` / `attack_mode` / `mode_from_sources` 保持通用；untrained 是带 ability 域限制的装备事实，通用注入会误伤 spell_attack——与 long_range"positional fact"先例不同，本条有 ability 域约束，必须内联在 attack 分支）。
2. 禁止给未受训穿甲添加任何"拒穿"逻辑（建卡 validation / live 装备层 / 装备前提 A20 一律不动；RAW 允许穿，代价就是门控）。
3. 禁止在 `derive_armor_class` 内做速度罚值（单一职责：AC 公式；罚值归 `derive_armor_training_state`）。
4. 禁止在豁免分支引入按 ability 以外的过滤或新 condition 机制（不进 conditions 注册表；`untrained_armor` 是 code-generated 来源，同 long_range 谱系）。
5. 禁止给 `derive_armor_class` 的新参数提供默认值（"默认受训"的兼容默认值 = fail open）。
6. 禁止在 src 业务逻辑出现 `"chain_mail"` 等具体护甲 id 字面量（测试与 item json 除外；消费一律经目录数据）。
7. 禁止给 mirror sheet / 前端新增 `armor_training` 投影（无消费者；`_recent_combat_events` 白名单零改动）。
8. 禁止顺手补齐其余 9 种护甲内容、Armor Training 三专长、穿脱时长字段（§1.2 裁剪 1/3/5）。
9. 既有约定自动适用：未知键/值 fail closed、不猜目录、行号漂移以工作区重读为准、不修改无关契约。

## 7. 停止条件

出现以下任一情况，停止并上报，不得带疑继续：

1. P6 未合入或 combat 目录工作区仍不干净——本切片不动 `src/rulesets/dnd2024/combat/` 任何文件。
2. `derive_armor_training_state` 的 category 校验与既有 `automation_level: "reference"` 的 item（如 robe 是 clothing，不在范围）或未来 content 注入的 armor 冲突——报错路径无法区分"数据缺失"与"合法新形态"时停止。
3. reconciliation 幂等穿透后出现 revision 无限增长（training 段含不可序列化值或每次 deepcopy 产生不等值）——停止排查。
4. party companion 的 `ruleset_character` 结构与玩家 derived 段不同构（`.get()` 链路抛 KeyError 或 companion 无装备权威导致门控语义不成立）——T17 裁剪并记录，companion 消费另行设计。
5. 升级路径（advancement 完成后）若不重建 canonical `derived` 段导致 `armor_training` 缺失——停止上报（canonical 完整性问题，超出本切片范围，不得用 reconciliation 兜底掩盖）。
6. `derive_armor_class` 在工作区出现 HEAD 之外的新调用方——逐一更新后才算完成，遗漏即停止。

## 8. 执行报告模板 + 迁移债登记

### 执行报告（执行侧完成后填写，随 PR 提交）

```
## S-P12 执行报告
- 基线：HEAD=<commit>（P6 合入后重核行号漂移：<文件:旧行→新行列表>）
- 改动文件清单（须与 §3 白名单一致）：
  - [ ] §3.1 items ×4        - [ ] §3.5 validation.py
  - [ ] §3.2 derivation.py    - [ ] §3.6 resolution.py (attack)
  - [ ] §3.3 reconciliation   - [ ] §3.7 resolution.py (saves ×2)
  - [ ] §3.4 view.py          - [ ] §3.8 reaction_spells.py
  - [ ] §3.9 tests ×3 文件
- 测试矩阵：T1…T18 逐项 ▢ 通过 / 裁剪原因
- 验收命令输出摘要（4 条命令）
- 停止条件触发记录（无 / 条目号+处理）
- 与 SP10 实现的合并顺序说明（两者都改 resolution 豁免/攻击段，说明以哪个为基）
```

### 迁移债登记（本切片显式产生，禁止在后续切片里"顺手"处理）

| # | 债 | 归属 |
|---|---|---|
| D1 | 穿脱时长（don/doff）数据与消费：等战斗内穿脱意图或角色卡层时长系统 | 1D 物品原语后段 / webui 装备层 |
| D2 | `stealth_disadvantage` 的 hide 检定双骰消费：读 `actor["armor_training"]["stealth_disadvantage"]` | S-P8 实现切片接线 |
| D3 | Armor Training 三专长（Lightly/Moderately/Heavily Armored）：advancement catalog 补声明 + parameters 扩 `armor_training_categories` 键（S-P10 已建 parameters 封闭键集先例）+ derivation 落账 armor_category_refs | 阶段二内容填充批次 |
| D4 | 其余 9 种护甲 item 内容（含 5 个 stealth_disadvantage 甲、2 个力量 15 甲）按本切片 schema 补齐 | 阶段二内容填充批次 |
| D5 | 属性检定面的力/敏劣势（"D20 检定"第三面） | P10 属性检定切片 |
| D6 | Medium Armor Master（中甲敏上限 3）/ Heavy Armor Master（重甲减伤） | S-P5 族 AC 扩展 / 伤害减免切片 |
| D7 | 前端展示 armor_training 状态（若玩家需要看到"未受训"警告） | 有真实需求时镜像 mirror + UI |
```
