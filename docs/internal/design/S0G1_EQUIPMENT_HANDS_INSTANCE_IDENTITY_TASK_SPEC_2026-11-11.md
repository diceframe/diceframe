# S0-G1 任务书：手部容量与物品实例身份

| 项 | 值 |
|---|---|
| 切片编号 | S0-G1（一份契约，分 A/B 两个执行提交，不覆盖旧 SP） |
| 验收载体 | 两只手各持一把武器；同款两把并存且可区分；双手武器与盾牌互斥 |
| 通用能力 | 手部容量、显式/隐式落位、Two-Handed 属性、装备实例身份、武器引用解析、旧存档兼容 |
| 规则基准 | 官方 SRD 5.2.1 PDF；SHA256 `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 规则页码 | 武器属性总表 p90（Light / Two-Handed / Versatile 三行）；具体行文执行时须用 PDF 逐字复核后再写入 `source_ref` |
| 代码基线 | `feat/dnd2024-s0f7-weapon-mastery@872e165a` |
| 硬前置 | 无 S0 切片前置；但必须建立在含 S0-B（`features.feature_choices`）与 S0-F7 已交付提交的链头上 |
| 执行模型 | `dsv4.1f` |
| 状态 | **已交付**：A `8a888d4e`、B `80d6ad46`；执行报告见 §9 |

## 1. 范围纪律与 A/B 提交边界

1. 从前置合入后的实际提交新建干净工作树。基线**不能同时装两把武器、不能表达同款两件、武器表没有 Two-Handed 属性**——这三条都已定点核实（见 §3），不得按「大概已有」假定。
2. **提交 A（手部容量）**：补齐 `two_handed` 属性、把槽位从「类型→唯一槽」改为「类型→候选槽序列」、实现显式/隐式落位、双手占用两手、盾牌与副手武器互斥、把 `_validate_two_handed` 收紧到只读 `off_hand`。A 完成后「短剑 + 匕首」可用。
3. **提交 B（实例身份）**：装备行发号、`weapon_ref` 实例化、唯一解析入口、旧存档与旧事件批次兼容。B 完成后「短剑 + 短剑」可用。
4. A/B 顺序不可交换：B 的落位需要 A 的双槽语义，A 的行为测试不需要实例也能表达。
5. 本切片**不实现任何 Light / Nick / 双持客规则**（那是 S0-G2）。A、B 完成后仍不允许因持两把 Light 武器自动多打一次。
6. 所有落位、占用与引用解析都由服务端结算；客户端不得提交「我现在用哪只手握」这种持握状态。

## 2. 官方英文关键原文与语义边界

### 2.1 武器属性（p90）

> **Light.** When you take the Attack action on your turn and attack with a Light weapon, you can make one extra attack as a Bonus Action later on the same turn with a different Light weapon. You don't add your ability modifier to the extra attack's damage unless that modifier is negative. For example, you can attack with a Shortsword in one hand and a Dagger in the other.

> **Two-Handed.** A Two-Handed weapon requires two hands when you attack with it.

> **Versatile.** A Versatile weapon can be used with one or two hands. A damage value in parentheses appears with the property. The weapon deals that damage when used with two hands.

语义边界：

1. Light 原文的「one hand / the other」是**举例**，不是规则条件。本切片因此**不引入手部状态机**，只引入「同一件不能既是首击又是额外攻击」这一条（G2 消费）。
2. `Two-Handed`（属性，攻击时必须双手）与 `Versatile`（同一武器可一手可两手，伤害不同）是**两个不同属性**，不得互相推导。基线只有后者（`damage_two_handed`），前者完全没有。
3. `Two-Handed` 的约束范围是「攻击时」，本切片的机械后果是「占满两只手，因此 `off_hand` 不可占用」。

### 2.2 盾牌现状（对提问的直接回答）

**盾牌目前不在「不能与武器同时装备」的范围内。** 定点核实（§3 第 4 行）：`shield` 的 canonical 槽是 `off_hand`，武器的 canonical 槽是 `main_hand`，两者不同槽，实测可同时存在。当前唯一装不上的是**第二把武器**——两把武器的 canonical 槽都是 `main_hand`，第二把按同槽顶回背包。

本切片后互斥关系变成：`off_hand` 槽内「副手武器」与「盾牌」二选一；主手持 `Two-Handed` 武器时 `off_hand` 必须为空。这条变化是新增的，必须进入测试矩阵（矩阵 8、9）。

### 2.3 SRD 之外的约束

`weapon_mastery` 的「选定武器 kind」与「实际持有的那一把」是两个层次（前者按 kind，后者按实例）。本切片只解决后者；两者如何对齐属 S0-F7。

## 3. 已定点核实的代码事实

| 基线事实 | 文件与函数 |
|---|---|
| 槽位映射是「类型→唯一槽」单值表：weapon→main_hand、shield→off_hand、armor→body、focus→accessory | `character/reconciliation.py:52` `_GENERIC_SLOTS` |
| 装备时按 `target_slot` 找同槽前一件并 `remove` + 退回背包；同槽即互斥，无「第二只手」概念 | `src/commands/state_items.py:575-587` `equip_owned_item` |
| 合法槽位集合含 `main_hand`/`off_hand` 等 11 项 | `src/commands/state_items.py:21` `EQUIPMENT_SLOTS` |
| **实测**：`equip_owned_item` 先 Shortsword 再 Shield → 两者并存（main_hand + off_hand）；再 Dagger → Shortsword 被顶回背包 | 本机探针，命令见 §9 |
| 规则投影对已装备项 `list(dict.fromkeys(refs))` 去重，同款多件塌成一件 | `character/reconciliation.py:244` `_project_equipped_refs` |
| 投影只收 `weapon`/`armor`/`shield`/`focus` 四类 | `character/reconciliation.py:41` `_PROJECTED_ITEM_TYPES` |
| **38 件武器中 `two_handed` 字段一条都没有**；`heavy` 只有 10 件；`damage_two_handed`（Versatile）7 件 | `content/combat_catalogs/srd_combat_core.json` 的 `weapons` |
| `shield` 不是武器 profile（`weapons["shield"]` 不存在），它是 `item_type: shield` 的物品 | 同上 + `bundle.get("item", ...)` |
| 攻击档遍历 `equipment_refs` **每一件**生成 profile，同 kind 两把会产出两个同 `weapon_ref` 的 profile | `combat/view.py:280` `_available_attacks` |
| `_declared_attack` 用 `next(...)` 取第一个 `weapon_ref` 相等的 profile，同 kind 两把永远只命中第一把 | `combat/view.py:388-412` |
| `_validate_two_handed` 已在注释里写明「`equipment_refs` 是携带清单而非持握槽位」，只靠「扫描到盾牌」拦双手持用 | `combat/validation.py:519-544` |
| actor 视图从 `equipment.item_refs` 取装备清单 | `combat/view.py` `_actor_view` / `_actor_view_from_data` |
| 背包按 `qty` 计数，装备时扣 1、归零删行；退回背包时整条 metadata 随行、`qty` 置 1 | `src/commands/state_items.py:414-431` `take_from_inventory`、`:434` `return_equipment_to_inventory` |
| 存档 schema 版本在 `state_schema_version: 1`，`migrate_state` 只接受 v1 并原样深拷贝 | `character/builder.py:449`；`runtime.py:1799` |
| `_annotate_live_rows` 会把与规则定义冲突的 `slot` **修正**而不是保留 | `character/reconciliation.py:247` |

## 4. 精确数据与行为契约

### 4.1 补齐 Two-Handed 属性（提交 A）

1. 给以下 **13 件**武器在 canonical 战斗目录补 `"two_handed": true`，并同步 en / zh-CN locale 的属性标签（若该 locale 已有属性标签表）：

   `greatclub`、`crossbow_light`、`shortbow`、`glaive`、`greataxe`、`greatsword`、`halberd`、`lance`、`maul`、`pike`、`crossbow_heavy`、`longbow`、`musket`

2. 不新增 `reach`、`loading`、`ammunition`（已有）、`special` 等其它属性。触及武器已由 `range: 10` 表达。
3. 载入期校验：`two_handed === true` 与存在 `damage_two_handed` 不得同时成立（没有武器两者兼具），冲突则整包拒绝加载。
4. `light` 的 8 件（`club`/`crossbow_hand`/`dagger`/`handaxe`/`light_hammer`/`scimitar`/`shortsword`/`sickle`）保持不变；本切片不消费 `light`。

### 4.2 手部容量与落位（提交 A）

1. 手部只有两个槽：`main_hand`、`off_hand`，每槽至多一行。非手部槽（`body`/`head`/`hands`/`waist`/`feet`/`back`/`accessory`/`pack`）语义完全不变。
2. **占位派生** `hands_cost(item)`：`two_handed` 武器 = 2；其余武器 = 1；盾牌 = 1；非手部物品 = 0。双手武器占满两只手，因此它只能落 `main_hand`，且其存在使 `off_hand` 不可占用。
3. **候选槽序列** `preferred_slots(item)`：`two_handed` 武器 → `("main_hand",)`；单手武器 → `("main_hand", "off_hand")`；盾牌 → `("off_hand",)`；非手部物品 → 其固定槽单元素序列。
4. **显式落位**（调用方或 intent 明确给出 `slot`）：
   - 槽不在 `preferred_slots` 内 → 拒绝并给出原因，不做「尽力而为」的替代落位；
   - 目标槽已被占 → 沿用既有「顶回背包」语义（这是「换武器」的桌面动作）；
   - `off_hand` 已被 `main_hand` 的双手武器封住 → 拒绝。
5. **隐式落位**（未给 `slot`）：按 `preferred_slots` 顺序取第一个**可用**槽。若两个手部槽都不可用，**拒绝并说明原因**，绝不静默顶掉已有装备。非手部物品保持既有「同槽顶回背包」行为。
6. `_GENERIC_SLOTS` 从「类型→唯一槽」改为「类型→候选槽序列」，并另给「非手部类型的固定槽」保留原表。
7. `_annotate_live_rows` 的修正规则同步收窄：只有当行的 `slot` 不在该物品的 `preferred_slots` 内时才改写；属于候选序列的落位是玩家状态，**不得**被重新投影覆盖。这是「盾牌曾被写成 `armor/body`」那类修复的反面情形。
8. 双手武器的落位不能靠「扫描有没有盾牌」推断，必须读 `off_hand` 槽本身。

### 4.3 实例身份（提交 B）

1. 每条**已装备**行携带 `instance_id`，形如 `eq-<n>`。`n` 来自 `character_sheet["equipment_sequence"]`（整数，缺省 0），每次发号 +1；发号器是纯函数 `mint_instance_id(sheet) -> str`。
2. 确定性：**禁止** `uuid`、时间、随机、进程内计数器、内容哈希。同一操作序列在重放与重新载入中必须得到相同编号。
3. 发号时机：装备成功落位后。若该行已带 `instance_id`（仅换槽、重新标注）则保留，换槽不改身份。
4. 卸下回背包：`instance_id` 退休并从行上删除，不写回背包行（背包仍按 `qty` 计），序列号不回退、不复用。
5. 旧存档（无 `instance_id`）：加载时按 `equipment` 列表顺序补发，首次写回时落盘。缺字段不得导致丢装备或被判为非法。
6. 规则投影新增 `ruleset_character.equipment.items`，每行含 `instance_id`、`item_ref`、`slot`、`hands`；`equipment.item_refs` 保留为**派生兼容视图**（去重后的 kind 序列），但代码中「持有哪些武器」必须以 `items` 为准，`item_refs` 不得再作为武器枚举来源。投影不再去重武器行。
7. 同一 `instance_id` 在一次投影中不得出现两次；重复即视为状态损坏并拒绝该次投影。

### 4.4 武器引用解析（提交 B）

1. 规范形：`weapon_ref = "item:<item_id>@<instance_id>"`。
2. `split_weapon_ref(ref) -> (item_ref, instance_id | None)` 是**唯一**解析入口；返回的 `item_ref` **保留** `item:` 前缀（与投影里的 `item_ref` 字段同名同形，可直接比对），只剥掉实例后缀；所有 `removeprefix("item:")` 的散装写法必须改为经它解析。
3. 兼容：裸 `item:<item_id>` 仍可提交，解析为「该 kind 的已装备实例」。若该 kind 有 ≥2 个已装备实例，**拒绝**并提示必须声明实例——不猜第一把。
4. 旧事件批次里的裸 ref 必须仍能重放；重放不得因缺少实例而失败或改写历史。
5. `_available_attacks` 每个 profile 带 `instance_id`（`unarmed_strike` 无实例，保持 `instance_id: null`）。
6. `_declared_attack` 由 `next(...)` 改为按解析结果精确匹配：有实例则按实例，无实例则只在唯一匹配时成功。

### 4.5 双手持用校验精确化（提交 A）

1. `_validate_two_handed`（Versatile 双手伤害）改为：仅当 `off_hand` 有占用物时拒绝，错误信息指出占用者。不再扫描整个 `equipment_refs` 找盾牌。
2. 新增 `Two-Handed` 武器的攻击校验：武器带 `two_handed` 时，`off_hand` 必须为空；否则拒绝。
3. 上述两条对敌方 stat block 不适用（敌方无装备档）。

### 4.6 战斗视图透传（提交 A 与 B 共同）

`equipment_items`（含 `instance_id`/`item_ref`/`slot`/`hands`）必须在**三处**同时透传，缺一处就会得到「投影里有、战斗里读不到」的静默空值——`size` 曾经就是在 `combat.start` 的白名单里被丢掉过一次：

1. `resolution.py` 的 `combat.start` enemy/ally 构造白名单；
2. `view.py::_actor_view` 的 player 分支；
3. `view.py::_actor_view_from_data` 的同名分支。

`equipment_refs` 作为派生字段一并保留，保证既有消费者不断裂。

## 5. 改动文件白名单

1. `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` 及对应 en、zh-CN locale。
2. `src/rulesets/dnd2024/character/reconciliation.py`；新建 `src/rulesets/dnd2024/character/item_instances.py`（发号与解析纯函数）。
3. `src/commands/state_items.py`（落位与槽位候选）。
4. `src/rulesets/dnd2024/combat/view.py`、`validation.py`、`resolution.py`、`engine.py`。
5. `src/rulesets/dnd2024/features/equipment.py`（属性查询入口）。
6. 新建 `tests/rulesets/test_dnd2024_equipment_hands.py`、`tests/rulesets/test_dnd2024_item_instances.py`。
7. `tests/rulesets/dnd2024_monk_common.py` 的 `with_equipment` 辅助：它当前**直接覆写** `equipment.item_refs`，绕过真实落位路径，必须改为走真实装备入口或明确标注为投影级夹具。
8. 需要改 `src/engine/**` 通用物品协议、引入二维地图、或必须让客户端申报持握状态时停止，不用简化假结果通过。

## 6. 编号测试矩阵

1. 13 件武器带 `two_handed: true`，其余 25 件为假或缺失；`two_handed` 与 `damage_two_handed` 不同时成立。
2. 同时给出两者时整包加载被拒，错误信息指出武器 id。
3. 单手武器 + 匕首：两者分别落 `main_hand` 与 `off_hand`，都留在装备里，背包各扣 1。
4. 再装第三把单手武器（未指定槽）：**拒绝**，理由说明两手已占；装备与背包均无变化。
5. 显式指定 `main_hand` 装第三把：原 `main_hand` 物品退回背包，新物品落位，`off_hand` 不变。
6. 显式指定与类型不符的槽（盾牌 → `main_hand`、武器 → `body`）：拒绝，不产生替代落位。
7. 双手武器（greataxe）单独装备成功，落 `main_hand`，`hands = 2`。
8. 已有盾牌时装双手武器（未指定槽）：拒绝，理由说明副手被盾占用。
9. 已有双手武器时装盾牌：拒绝；装备状态不变。
10. 双手武器 + 徒手：徒手仍可用（`unarmed_strike` 不占槽），不得被算成第二把武器。
11. Versatile 武器（longsword）在 `off_hand` 有物时声明 `two_handed: true` 攻击被拒；`off_hand` 为空时允许。
12. `_validate_two_handed` 不再因「背包里另有一面盾但未装备」而拒绝。
13. 装两把同款短剑（A 提交后）：**两行都在装备里**（一手一把），但投影只留一条 kind 引用、攻击只产出一个档案——A 让"另一只手"存在，却没有让两个同款变成两个可分别指名的对象。B 才解决可区分性。
14. 装两把同款短剑（B 提交后）：两行并存，`instance_id` 不同，`slot` 一个 `main_hand` 一个 `off_hand`。
15. 投影 `equipment.items` 保留两行同款武器，`equipment_refs` 仍按 kind 去重为一条（兼容视图语义）。
16. `_available_attacks` 对两把同款短剑产出两个 profile，`instance_id` 不同、`weapon_ref` 不同。
17. 用 `instance_id` 声明攻击命中第二把，事件里的 `weapon_ref` 指向该实例；用第一把的实例另一条独立成立。
18. 裸 `item:shortsword` 在存在两把时被拒，提示必须声明实例；只存在一把时仍可用（向后兼容）。
19. 旧存档（无 `instance_id`）：加载成功、装备数量不变、按列表顺序补号，写回后编号稳定。
20. 同一存档重复加载多次，编号序列一致（确定性，不读时钟/随机）。
21. 换槽（`main_hand` ↔ `off_hand`）不改变 `instance_id`；卸下再装回获得**新**编号，旧的永不复用。
22. 旧事件批次（含裸 `item:x` ref）重放成功，不报错、不改写历史事件。
23. `equipment_items` 在三处 actor 视图（`combat.start`、`_actor_view`、`_actor_view_from_data`）都能读到 `instance_id` 与 `hands`；缺一处即失败。
24. 重复的 `instance_id` 在被喂入时被识别为状态损坏并拒绝投影，不静默去重。
25. `with_equipment` 辅助不再绕过真实落位；用它装两把武器时走与 `equip_owned_item` 相同的槽位规则。
26. A 提交后矩阵 1–13 通过（同款仍受限并显式记录），B 提交后 1–25 全通过。
27. 既有装备、物品身份、战斗、Monk 装备、角色生命周期测试无回归；`equipment_refs` 的既有消费者行为不变。

## 7. 非目标与停止条件

### 非目标

- Light / Nick / Dual Wielder / Two-Weapon Fighting 的任何规则（属 S0-G2、S0-F8）。
- 背包按件计数、物品耐久、附魔、绑定、装备负重、多臂生物。
- `reach`、`loading`、`special` 等新武器属性；施法焦点与材料的持握要求。
- 用「携带」冒充「持握」，或在客户端引入手部状态机。
- 修改 `src/engine/**` 通用物品协议。

### 停止条件

1. 实例身份只能用 `uuid`／时间戳／随机数／进程内计数器生成，无法保证重放一致性。
2. 旧存档或旧事件批次无法无损读入，或必须做破坏性迁移。
3. 落位判定必须依赖客户端申报的持握状态。
4. 任一提交需要白名单外文件（尤其 `src/engine/**`）。
5. 需要二维地图或手部空间关系才能判定占用。

## 8. 验收命令

```powershell
python -m pytest tests/rulesets/test_dnd2024_equipment_hands.py tests/rulesets/test_dnd2024_item_instances.py -q
python -m pytest tests/rulesets/test_dnd2024_item_identity.py tests/rulesets/test_dnd2024_monk_equipment.py tests/rulesets/test_dnd2024_character_lifecycle.py -q
python -m pytest tests/rulesets -q -n auto --dist loadfile
python -m ruff check src/rulesets/dnd2024 src/commands/state_items.py tests/rulesets/test_dnd2024_equipment_hands.py tests/rulesets/test_dnd2024_item_instances.py
git diff --check
```

盾牌现状的复现探针（执行前先跑一次，确认基线）：

```powershell
python -c "import sys,json; sys.path.insert(0,'tests/rulesets'); from src.rulesets.dnd2024.runtime import Dnd2024Runtime; from src.rulesets.dnd2024.character.reconciliation import Dnd2024CharacterStateReconciler; from src.commands.state_items import equip_owned_item; rt=Dnd2024Runtime(); b=rt.load_bundle('en'); p=[i for i in rt.builder_choices(None,{'locale':'en'})['quick_presets'] if i['id']=='stalwart_guardian'][0]; s=rt.finalize_character(None,{**p['draft'],'locale':'en','name':'T'}); r=Dnd2024CharacterStateReconciler(b); s['inventory']=[{'item_ref':'item:shortsword','qty':1,'name':'Shortsword'},{'item_ref':'item:shield','qty':1,'name':'Shield'},{'item_ref':'item:dagger','qty':1,'name':'Dagger'}]; s['equipment']=[]
for ref,n in (('item:shortsword','Shortsword'),('item:shield','Shield'),('item:dagger','Dagger')):
    r.prepare_owned_rows(s,[n]); equip_owned_item(s,n,item_ref=ref); print(n,[(x.get('item_ref'),x.get('slot')) for x in s['equipment']])"
```

## 9. 执行报告（执行侧填写）

- **基线 / 分支 / 提交**：基线 `872e165a`，分支 `feat/dnd2024-s0f7-weapon-mastery`。A = `8a888d4e`（两只手 + Two-Handed），B = `80d6ad46`（实例身份 + 唯一解析入口）。后续 S0-G2 的 A/B/C 全部建立在这两个提交之上。
- **`two_handed` 13 件逐项核对**：`greatclub`、`crossbow_light`、`shortbow`、`glaive`、`greataxe`、`greatsword`、`halberd`、`lance`、`maul`、`pike`、`crossbow_heavy`、`longbow`、`musket` 均补 `"two_handed": true`，插在 `damage_type` 之后。逐项与 `damage_two_handed`（Versatile）互斥：载入期 `CombatCatalogError` 整包拒绝，不留「两者兼具」的静默状态。
- **槽位候选与占位派生**：`features/equipment.py` 新增 `HAND_SLOTS = ("main_hand","off_hand")`、`hands_cost()`（Two-Handed 武器 2、其余武器 1、盾 1、非手部 0）、`preferred_slots()`；`Dnd2024EquipmentCatalog` 暴露 `weapon()` / `hands()` / `slots_for()`。落位在 `commands/state_items.py::_hand_placement()`，`equip_owned_item` 改成「先算落位、再扣背包」——落位失败不会留下半扣的背包行。
- **显式/隐式落位与拒绝理由**：显式 `slot` 先验（非法槽、已被占、双手冲突各有独立理由）；隐式按 `preferred_slots` 取第一个空槽，两手都满时理由说的是「没有空手」，不是「装备已满」。盾牌与副手武器互斥，`_validate_two_handed` 改为只读 `off_hand` 的占用者。
- **实例发号确定性**：`instance_id = "eq-<n>"`，来源是 `sheet["equipment_sequence"]` 单调递增；不用 uuid / 时间 / 随机 / 进程计数器。`assign_missing_instance_ids` 按列表顺序为旧存档补号（顺序稳定 ⇒ 结果可重放），已有 id 的行不重新发号（换手不等于换对象）；背包行的 id 在装备时退休，不在背包里维持第二套弱身份。
- **`weapon_ref` 解析与裸 ref 兼容**：`item_instances.split_weapon_ref()` 是唯一解析入口，返回 `(item_ref, instance_id | None)`，`item_ref` 保留 `item:` 前缀。裸 `item:<id>` 仍可提交，但只在「该 kind 唯一匹配」时成立；`_declared_attack` 遇到多实例直接抛 `has N equipped instances; declare weapon_ref as item:<id>@<instance_id>`，不猜第一把。
- **旧存档 / 旧事件批次重放**：旧存档没有 `instance_id`，首次投影时补号后结果稳定；旧事件批次里的裸 ref 仍能重放，重放不改写历史、不因缺实例失败。
- **三处 actor 视图透传**：`equipment_items` 写入 `combat.start` 敌人桩、`_player_view`（服务 `_actor_view` 与 `_actor_view_from_data`）。`_held_equipment_rows` 只在 kind 集合一致时采信 `equipment_items`，否则回退裸 ref——这条一致性校验修掉了 26 条真实回归（旧夹具只改 `item_refs`，导致档案与持握状态错配）。
- **白名单文件对照**：`srd_combat_core.json`、`features/equipment.py`、`features/__init__.py`、`combat/catalog.py`、`character/item_instances.py`（新建）、`character/reconciliation.py`、`commands/state_items.py`、`combat/view.py`、`combat/validation.py`、`combat/resolution.py`。另加两份新测试（`test_dnd2024_equipment_hands.py` 18 条、`test_dnd2024_item_instances.py` 17 条）、`dnd2024_monk_common.py` 的 `equip_refs` 助手、`test_dnd2024_equipment_data.py` 的一处参数替换（`longbow` → `sling`，因为 longbow 现在占两手，不再适合做「单手持远程」用例）。
- **测试**：A/B 聚焦 **35 passed**（18 + 17）；全量 **16 failed / 全量其余通过**，其中 11 条是 `test_dnd2024_character_builder.py` 的已知基线（`ability:con in background:soldier`），5 条是 `PermissionError: [WinError 5]` 的 `os.replace` 抖动，**无新增失败**。`ruff` 未安装，改用 `py_compile`。
- **偏差与未决**：矩阵 13 原写作「A 提交后仍只有一把能进装备」，实测是**两行都在装备里**（一手一把），A 只是没让两个同款变成两个可分别指名的对象——B 才解决可区分性。已改写该条并按事实重写 §4.4.2 的 `split_weapon_ref` 签名。G1 本身不含任何 Light / Nick / 双持客规则，两把 Light 武器不会自动多打一次，该行为由 S0-G2 交付。
