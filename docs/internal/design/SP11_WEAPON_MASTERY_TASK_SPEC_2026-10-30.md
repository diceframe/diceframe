# S-P11 任务书：武器精通词条（Weapon Mastery）声明链与命中后效果

| 项 | 值 |
|---|---|
| 切片编号 | S-P11 |
| 主计划出处 | `docs/internal/DND2024_FULL_EXPERIENCE_PLAN.md` L144（原语行：武器精通词条 8 种——缓速/迅击/推离/侵扰/削弱/失衡/横扫/擦掠；特性授予 → 武器档案精通位 → 命中后效果声明链，效果侧骑 S-P2/S-P10/P6/条件 rider；Weapon Mastery 也是 Fighter 1 级基础特性需求） |
| 原语 | 武器精通声明链：特性解锁（weapon_mastery）→ 武器档案 mastery 位 → 命中/失手后效果分发注册表；本切片端到端落地 4 词条（擦掠/削弱/侵扰/缓速），其余 4 词条显式裁剪 |
| 代表特性 / 验证载体 | Weapon Mastery（战士 1 级，端到端职业载体；barbarian/paladin/ranger/rogue 同批纯数据声明） |
| 编写日期 | 2026-10-30 |
| 代码基线 | commit `4ef1d33d`（HEAD）。工作区存在 P7/P8 在途未提交改动（含 `srd_combat_core.json` +1 行），行号映射见 §2.2 |
| 依赖契约 | P1（attack_sources 数据驱动化已合入——vex/sap 侧骑前提）；P6（misty_step 已合入——本切片未用位移，为 push 裁剪项的前置）；SP2 §3.8（RNG 顺序：graze 伤害为零骰固定值，不进骰迹）；S-P9（`_feature_character` + `has_feature` 解锁检查模式）；切片 3（damage_event held / `_submit_hit_damage` 延迟弹出模式） |
| 状态 | 待执行；**建议排在 P7/P8 合入之后**（本切片改动 resolution.py 两个区域，其中 `_submit_hit_damage` 位于 P7/P8 的 +39 偏移区内，见 §2.2） |

---

## §1 规则语义与原文出处（2024 版，显式裁剪）

### 1.1 Weapon Mastery（武器精通，战士 1 级）——授予面

原文：`.rulebooks/dnd5r/玩家手册2024/角色职业/战士/战士.htm` L78-79：

> **1级：武器精通 Weapon Mastery** 你对武器的训练使你能够运用三种自选的简易或军用武器的精通词条。每当你完成一次长休时，你可以重新演练武器技巧，来改变你所选择的其中一个武器类型。
> 当你到达特定的战士等级时，你还可以使用更多种类武器的精通词条，详见战士特性表中武器精通一栏。

数值（同文件 L45-65 特性表"武器精通"列）：1 级 3、4 级 4、10 级 5、16 级 6。`srd_classes.json` fighter 段 `"weapon_mastery": [3,3,3,4,4,4,4,4,4,5,5,5,5,5,5,6,6,6,6,6]` 轨道已逐级在位；barbarian/paladin/ranger/rogue 同名轨道亦在（barbarian `[2,2,2,3,...,4,...]`，L48/L60）。

解锁前提原文：`.rulebooks/dnd5r/玩家手册2024/装备/精通词条.htm` L8：

> 每把武器都具有自己的精通词条。一个角色必须拥有能够解锁精通词条的特性（例如**武器精通**特性）才能使用这类词条。

### 1.2 本切片落地的 4 种词条（效果面原文，均出自 `装备/精通词条.htm`）

1. **擦掠 Graze**（L11-12）：「当你以这把武器对一个生物发动的攻击检定**失手**时，你仍可以对目标生物造成一定伤害。其数额等同于此次攻击所使用的属性调整值，其伤害类型与你所使用的武器一致，并且这个伤害只能通过属性调整值的提高而增长。」
   - 失手面固定伤害：= 该次攻击所用属性调整值（负修正取 0），伤害类型同武器；无伤害骰（rolls 空）。
   - 受抗性结算（`_resist_damage`）与专注豁免（伤害>0 触发）、可参与击杀（1 HP 敌人可被擦掠打死 → victory 检查）。
2. **削弱 Sap**（L19-21）：「当你用这把武器**命中**一个生物时，直到你的下一回合开始前，该生物的下一次攻击检定将具有劣势。」
   - 命中（无论伤害是否为 0）→ 目标获得"下一次攻击劣势"的一次性状态。引擎侧 = `condition.applied next_attack_disadvantage`（目录已有，`consume_on: attack_resolved`），精确 RAW（被打者的下一次任何攻击劣势，无目标限定）。
3. **侵扰 Vex**（L27-29）：「当你用这把武器命中了一个生物并**对其造成伤害**时，直到你的下一回合结束前，你对该生物的下一次攻击检定将具有优势。」
   - 命中**且伤害 > 0**（与 sap 的门槛差异是 2024 原文差异，必须保留）→ 引擎侧 = `condition.applied next_attack_advantage` 挂目标（目录已有）。**近似声明**：现有机制无"仅限命中者本人"的 scope 谓词，本切片近似为"下一位攻击该目标的攻击者获得优势"（宽松方向：可能让队友受益；执行侧不改 check_modes 加 scope，登记 §8 债 D3）。
4. **缓速 Slow**（L22-23）：「当你用这把武器命中了一个生物并**造成伤害**时，你可以将对方的速度减少10尺，直至你的下一回合开始。如果该生物被拥有缓速词条的武器多次命中，其速度削减量也不会因此超过10尺。」
   - 命中且伤害 > 0 → `condition.applied slowed_10`（目录已有 `speed_modifier: -10`）；同一 condition id 重复 applied 天然去重（conditions 为 dict），"不叠加超过 10 尺"精确 RAW。
   - duration："直至你的下一回合开始"（攻击者下回合开始）——现有 duration 枚举含 `caster_next_turn_end`（chill_touch L37）、`target_turn_start`（ray_of_frost L30）、`after_next_attack`（L35）等；本切片新增枚举值 `attacker_next_turn_start`（精确 RAW，reducer duration tick 小改），不复用 `caster_next_turn_end` 的宽松近似。
   - "你可以"（可选）→ 实现为自动生效（不弹决策）：省略决策窗口，方向对玩家宽松（多一次 -10 不至于破坏平衡；决策弹窗成本高于收益），登记 §8 债 D4。

### 1.3 声明链架构语义（主计划 L144 的三层）

1. **特性授予层**：`weapon_mastery` 特性声明进入 `srd_class_features.json` 五个职业段（fighter 端到端载体；barbarian/paladin/ranger/rogue 同形纯数据——progression 轨道与 1 级特性 id 均已在位）。无 capabilities、无资源池、无 parameters（解锁型特性）。
2. **武器档案精通位**：`srd_combat_core.json` 15 条武器档案各加 `"mastery"` 字段，值为本武器的词条 id（对照 2024 武器表逐条核录，见 §3.1）。经 `view.py _available_attacks` 的 profile 构造随武器档案进入攻击结算。
3. **效果分发注册表**：代码注册表 `WEAPON_MASTERY_EFFECTS`：词条 id → {效果 kind, 触发面（on_hit / on_miss）, 门槛（requires_damage）, 目标 condition 与 duration}。本切片注册 8 种词条、实现 4 种；注册表外的词条 id fail closed（档案里出现未注册词条 → 无效果，不崩）。

### 1.4 解锁近似（核心显式裁剪）

RAW 的"三种自选武器类型 + 长休重选一个"需要 build 期选择 UI 与 rest 期改选链路。本切片近似为**装备即精通**：拥有 `weapon_mastery` 特性 → 当前使用的武器档案 mastery 词条即生效；数量上限（3/4/5/6）不 gate。偏离方向：对玩家宽松（全部装备武器生效，超出自选数量），无结算结构破坏（效果面本身是完整的）；选择/重选链路登记 §8 债 D1。

### 1.5 显式裁剪清单（本切片不做，全部登记 §8 迁移债）

1. **横扫 Cleave**（巨斧/戟；档案内巨斧有）：命中后决策窗口 + 第二目标选择（5 尺内候选集）+ 不含属性调整值的二次攻击 + 每回合一次记账——工程量同 S-P9 的 HIT_SPELL feature 化，归"命中后效果扩展批次"（与 S-P9 迁移债 D1 同族），债 D5。
2. **迅击 Nick**（匕首/弯刀/镰刀/轻锤）：依赖轻型词条的双持附赠攻击机制——**当前引擎无双武器战斗 bonus attack**（light 字段仅档案标记），Nick 无宿主任务可改写；归双持/装备词条批次，债 D6。
3. **推离 Push**：**15 条武器档案中无 push 词条载体**（巨棒/长矛/战锤/重弩均未入档）；效果位移 rider 依赖 P6 位移扩展；两者一并归武器档案扩容批次，债 D7。
4. **失衡 Topple**（长棍）：需要 prone 条件族（起身消耗移动、对 prone 目标近战优势/远程劣势的距离谓词——现有 attack_modes 声明无距离谓词）+ 命中后强破豁免（DC 8+调整值+熟练加值）双新原语；归条件系统批次，债 D8。
5. **vex 精确 scope**（仅命中者本人对仅该目标的下次攻击优势）：需要 attack_modes 的 per-pair 校验（对齐 hexed 的 `source_actor_id` meta 模式，check_modes 两读点小改）；本切片宽松近似，债 D3。
6. **slow 可选性**（"你可以"）：不弹决策、自动生效，债 D4。
7. **自选清单与长休重选**（§1.4），债 D1。
8. **多职业其它数值差异**：barbarian 2/3、rogue 2/3、paladin/ranger 2 等数量轨道在"装备即精通"近似下暂不消费，轨道数据保留待债 D1 偿还时启用，债 D1 备注。
9. **敌方（enemy kind）精通**：enemy stat-block attacks 不带 mastery 位，敌方无 weapon_mastery 特性——不在本切片，归敌方能力批次。

---

## §2 代码事实锚点表

### §2.1 锚点（基线 4ef1d33d；未注明者即 HEAD）

| 文件 | 行号 | 事实 |
|---|---|---|
| `src/rulesets/dnd2024/combat/resolution.py` | L189-355 | `_attack_events` 全体（HEAD ≤L593 无偏移） |
| 同上 | L249-262 | 掷骰区与 `hit` 判定（**L262 `hit = natural != 1 and (...)` 是 graze miss 面的判定源**） |
| 同上 | L294-341 | 命中结算体：damage_event 构造 L325-329；sneak 的 `feature_use` **延迟记账先例 L333-341**（本切片 mastery_effects 同款 held 模式） |
| 同上 | L342-348 | offer 链（HIT_SPELL / HIT_REACTION）——**mastery 效果必须在伤害最终落地处弹出**（Shield 改判 miss 则效果不生效），不得在 offer 前直接发 condition.applied |
| 同上 | L350-353 | 直通尾段：`_submit_hit_damage` → `_concentration_events` → victory |
| 同上 | L354-355 | `_consume_attack_conditions` + return（miss 分支也走到这里——graze 插入点在 hit=False 分支） |
| 同上 | L741-758 | `_submit_hit_damage`（静态：`feature_use` 弹出转 `dnd2024.feature.used`——**mastery_effects 弹出扩展点**；位于 P7/P8 偏移区，见 §2.2） |
| 同上 | L284-293 | rage sustained 守卫（本切片不触碰） |
| `src/rulesets/dnd2024/combat/reaction_spells.py` | L181-268 | `_finish_hit_reaction`：Shield 改判 miss 的 `return` 路径 L256-257（mastery held 效果天然不弹出的保证）；UD 分支 `_submit_hit_damage` L215（mastery 照常弹出——命中未被取消） |
| `src/rulesets/dnd2024/combat/view.py` | L208-246 | `_available_attacks`：L236-246 profile 构造逐字段拷贝 catalog.weapons 字典——**mastery 键加入拷贝** |
| 同上 | L311-335 | `_declared_attack` / L337-362 `_feature_character`（解锁检查 `has_feature("weapon_mastery")` 的 character 切片来源） |
| `src/rulesets/dnd2024/combat/check_modes.py` | L68-96 | `attack_sources`：registry（conditions）声明驱动（P1 已偿还硬编码债）——`next_attack_advantage` / `next_attack_disadvantage` / `faerie_fire` 均由此进优劣势管线，本切片零改动 |
| `src/rulesets/dnd2024/combat/engine.py` | L1040-1065 | `_consume_attack_conditions`：`consume_on: "attack_resolved"` 的注册表化一次性消耗原语（"the old hard-coded pair lives in catalog data now"）——vex/sap 的消耗侧骑此机制，零改动 |
| 同上 | L1067-1091 | `_concentration_events`（graze 伤害 > 0 触发，复用） |
| 同上 | L1098-1100 | `_distance` |
| `src/rulesets/dnd2024/combat/reducer.py` | duration tick 处理位置 | condition duration 过期（`target_turn_start` / `caster_next_turn_end` / `after_next_attack` / `until_healed` 枚举的消费点）——**`attacker_next_turn_start` 新枚举值落点**；执行侧 grep `condition_duration|turn_start` 定位，锚点行号开工时补 |
| `src/rulesets/dnd2024/features/resolver.py` | L476-525 | `has_feature` / `feature_value`（解锁检查；SP3/S-P9 同款模式） |
| `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json` | L7-23 | `weapons` 15 条：dagger/flail/greataxe/greatsword/handaxe/javelin/longbow/longsword/mace/quarterstaff/scimitar/shortbow/shortsword/sickle/spear（**mastery 位落点**，逐条归属见 §3.1） |
| 同上 | L24 / L25-27 | `unarmed_strike`（无 mastery——徒手没有精通词条，2024 武器表无徒手行）与 `labels` |
| 同上 | L86-99 | `conditions` 注册表：L87 `next_attack_advantage`（side=target, advantage, consume_on=attack_resolved）、L88 `next_attack_disadvantage`（side=actor, disadvantage, consume_on）、L89-91 faerie_fire/dodging/blurred、L99 `slowed_10`（speed_modifier: -10）——**本切片 conditions 段零改动**（三词条全部复用） |
| 同上 | L29-38 | `spell_effects` 的 `condition` + `condition_duration` 传参先例（ray_of_frost slowed_10/target_turn_start、vicious_mockery next_attack_disadvantage/after_next_attack、chill_touch caster_next_turn_end） |
| `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` | L137-176 附近 | fighter 段：second_wind 条目（1A 切片 1 产物）——**weapon_mastery 新条目插入点**；barbarian 段 L178-207、rogue 段 L209-232 同批插入；paladin/ranger 段现状执行侧核对（预期同形） |
| `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` | L48 / L60 | barbarian：1 级特性列表含 weapon_mastery + 轨道 `[2,2,2,3,...]`（零改动，仅引用） |
| 同上 | L114-129 | fighter：1 级特性列表 + 轨道 `[3,3,3,4,...,6]`（零改动） |
| 同上 | L156 / L174 / L189 | paladin / ranger / rogue 同名条目（零改动） |
| `src/rulesets/dnd2024/combat/catalog.py` | weapons/conditions 加载 | catalog 结构消费点（mastery 字段为 weapons 字典的新键，加载器字典透传应零改动；执行侧核对是否白名单字段过滤） |
| `tests/rulesets/` | 现有 spell condition 测试 | ray_of_frost / vicious_mockery / faerie_fire 的既有测试是 sap/vex/slow 侧骑行为的回归面（执行侧 grep `slowed_10|next_attack` 定位文件与用例名补入 §4.2） |

### §2.2 工作区在途改动与行号偏移（P7/P8 会话，未提交）

现状（2026-10-30 核实）：M `catalog.py`（1 行）/ M `resolution.py`（+39）/ M `validation.py`（+5/-2）/ M `srd_combat_core.json`（+1）/ 未跟踪 `tests/rulesets/test_dnd2024_dispel_magic.py`。

- `resolution.py`：HEAD ≤L593 无偏移；HEAD ≥L615 = 工作区 −39。**`_submit_hit_damage`（HEAD L741-758）在偏移区内**——本切片必须改它（mastery_effects 弹出），与 P7/P8 同文件同区域并行风险高，故状态行建议本切片排在 P7/P8 合入后执行；
- `srd_combat_core.json` +1 行：执行侧开工时 `git diff -U0` 确认该行是否落在 weapons/conditions/spell_effects 段内（P7/P8 是 counterspell/dispel 魔法，大概率在 spell_effects 或 conditions 加 `dispel` 相关条目；weapons 段冲突概率低，但必须核实）；
- `catalog.py` 1 行改动：核对是否影响 weapons 字典透传（mastery 键存活）；
- 开工时基线重测：`git status` + `git diff -U0 src/rulesets/dnd2024/combat/resolution.py templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`。

---

## §3 白名单逐文件改动规格

### 3.1 `templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json`

`weapons` 15 条逐条加 `"mastery"` 键（归属对照 2024 武器表 `.rulebooks/dnd5r/玩家手册2024/装备/武器.htm` L30-304 逐条核录）：

| 武器 id | mastery | 表行 | 表行 |
|---|---|---|---|
| dagger | `nick` | 武器表 L37-43（迅击） | 裁剪 D6（无宿主） |
| flail | `sap` | L141-147（削弱） | **本切片生效** |
| greataxe | `cleave` | L155-161（横扫） | 裁剪 D5 |
| greatsword | `graze` | L162-168（擦掠） | **本切片生效** |
| handaxe | `vex` | L51-57（侵扰） | **本切片生效** |
| javelin | `slow` | L58-64（缓速） | **本切片生效** |
| longbow | `slow` | L284-290（缓速） | **本切片生效** |
| longsword | `sap` | L183-189（削弱） | **本切片生效** |
| mace | `sap` | L72-78（削弱） | **本切片生效** |
| quarterstaff | `topple` | L79-85（失衡） | 裁剪 D8 |
| scimitar | `nick` | L218-224（迅击） | 裁剪 D6 |
| shortbow | `vex` | L117-123（侵扰） | **本切片生效** |
| shortsword | `vex` | L225-231（侵扰） | **本切片生效** |
| sickle | `nick` | L86-92（迅击） | 裁剪 D6 |
| spear | `sap` | L93-99（削弱） | **本切片生效** |

- 条目值为已注册词条 id；**未注册/未实现的词条 id 照样写入档案**（cleave/nick/topple），引擎注册表 fail closed 无效果——档案位先落满，后续批次只加效果实现不改档案；
- `conditions` 段、`spell_effects` 段、`labels` 段零改动（若执行侧为 feed 展示需要 mastery 词条中文标签，属 locales 层，不在本切片）；
- `unarmed_strike` 不加 mastery（徒手无精通词条）。

### 3.2 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

fighter / barbarian / rogue / paladin / ranger 五段各追加同形条目（以 fighter 为例，插入 fighter 段 `features` 数组，second_wind 旁）：

```json
{
  "id": "weapon_mastery",
  "minimum_level": 1,
  "source_ref": "srd-5.2.1:p42:weapon-mastery",
  "summary": "You can use the mastery properties of your weapons. (This slice approximates the chosen-weapons list with an 'equipped weapon' unlock; the count track is not yet consumed.)"
}
```

- `source_ref` 各职业取各自页码（fighter p42 / barbarian p28 / rogue p61 / paladin / ranger 执行侧按 `srd_classes.json` 各段 source_ref 页码对齐）；
- 无 `parameters`、无 `resource_id`、无 `capabilities`（纯解锁特性；"装备即精通"近似下轨道暂不消费，summary 如实注明近似——目录 summary 是目录语言事实，不隐瞒）；
- labels 平铺区（L7-32）若按 UD 先例需要，追加 `"weapon_mastery": "Weapon Mastery"`。

### 3.3 `src/rulesets/dnd2024/combat/resolution.py`（核心引擎改动；建议 P7/P8 合入后执行）

**（a）模块级效果注册表**（新模块 `weapon_masteries.py` 或置于 resolution.py 顶部，执行侧按 owning module 惯例定位——独立小模块优先，避免 resolution.py 再膨胀）：

```python
WEAPON_MASTERY_EFFECTS: dict[str, dict[str, Any]] = {
    "graze": {"kind": "miss_damage"},                       # 失手面：属性修正值固定伤害
    "sap":  {"kind": "apply_condition", "on": "hit",
             "condition": "next_attack_disadvantage",
             "duration": "after_next_attack"},
    "vex":  {"kind": "apply_condition", "on": "hit", "requires_damage": True,
             "condition": "next_attack_advantage",
             "duration": "after_next_attack"},
    "slow": {"kind": "apply_condition", "on": "hit", "requires_damage": True,
             "condition": "slowed_10",
             "duration": "attacker_next_turn_start"},
    "cleave": {"kind": "unimplemented"},                     # 档案位在、效果 fail closed
    "nick":  {"kind": "unimplemented"},
    "push":  {"kind": "unimplemented"},
    "topple": {"kind": "unimplemented"},
}
```

- 未知词条 id / `"kind": "unimplemented"` → 无任何效果事件（fail closed，不抛错不崩）；
- 解锁检查：效果生效前提 = `self.features.has_feature(self._feature_character(actor), "weapon_mastery")`；enemy 分支天然跳过（enemy 走 stat-block，无 mastery 位）。

**（b）命中面（`_attack_events` hit 分支，L294-341 区域）**：

- damage_event 构造后、offer 链（L342）之前：若解锁 + 武器 mastery ∈ 注册表 + `on == "hit"` + `requires_damage` 满足（damage > 0）→ `damage_event["mastery_effects"] = [ {"condition": ..., "duration": ...}, ... ]`（**held，不直接发事件**——Shield 改判 miss 时随 damage 一起作废）；
- held 弹出：`_submit_hit_damage` 扩展（见 (d)）。

**（c）失手面（hit=False 分支，L354 之前插入 graze 结算）**：

- 若解锁 + mastery == "graze"：`amount = max(0, ability_modifier(该次攻击所用属性))`（负修正归 0）；`amount > 0` 才产生事件；
- 事件流：`resource.changed`（target_id=目标，delta=-amount，amount，damage_type=武器类型，rolls=[]（零骰，SP2 §3.8 顺序：无新骰位）→ `_resist_damage`（类型抗性照走）→ `_concentration_events(target, amount, rng)` → `_last_hostile_defeated` victory 检查；
- 失手不触发任何命中后词条（vex/sap/slow 只在命中面）；
- 失手面的攻击动作消耗、check.resolved 等事件不变。

**（d）`_submit_hit_damage`（HEAD L741-758）扩展**：与 `feature_use` 弹出并列，`mastery_effects` 逐条转 `condition.applied` 事件（`target_id` = damage_event 的 target_id，`duration` = 注册表值）。内部键不进 wire shape（对齐 feature_use 的弹出先例 docstring L749-750）。

**（e）`view.py _available_attacks`（L236-246）**：profile 构造拷贝 `"mastery"` 键（一行级小改；profile 其余字段不动）。

### 3.4 `src/rulesets/dnd2024/combat/reducer.py`

duration tick 处理新增枚举值 `attacker_next_turn_start`（语义：`source_actor_id` 所指定actor 的下回合开始时过期；若现有 duration 实现以事件驱动（condition.applied 的 duration 字符串消费点），对齐 `caster_next_turn_end` 的实现形状）。若执行侧发现现有 duration 机制已可表达"某 actor 下回合开始"（复用 caster_* 语义），允许直接复用并在执行报告注明，无需新增枚举。

### 3.5 明确零改动文件

`check_modes.py`（attack_sources 已数据驱动）、`engine.py`（`_consume_attack_conditions` 已通用）、`validation.py`（无新意图参数；graze 是攻击结算内部行为）、`reaction_spells.py`（mastery held 语义天然正确，无需知情）、`triggered_spells.py`、`srd_classes.json`、`srd_subclasses.json`、`srd_recovery.json`、前端与 feed 投影、SP3 `check_windows.py`。

---

## §4 测试矩阵

### 4.1 新用例（新建 `tests/rulesets/test_dnd2024_weapon_mastery.py`，fixture 对齐 `dnd2024_monk_common` 家族；fighter sheet 经 `advance` 到所需等级）

| # | 用例 | 断言要点 |
|---|---|---|
| M1 | greatsword 失手（1 级，15 尺外命中线） | check.resolved success=false 后出现 resource.changed：amount = 力量调整值、damage_type=slashing、rolls=[]；目标 hp 减该值；hit=False 时无 mastery held 字段泄漏到事件 |
| M2 | graze 抗性与专注 | 目标对 slashing 抗性 → amount 减半；目标专注 → concentration check.resolved 触发 |
| M3 | graze 击杀 | 1 HP 敌人被擦掠 → `dnd2024.combat.ended` victory |
| M4 | graze 负修正 | 力量 8 战士失手 → 无 resource.changed（max(0)） |
| M5 | longsword 命中（伤害 0 门槛豁免） | sap 不设 requires_damage：0 伤害命中（全抗）仍 applied next_attack_disadvantage |
| M6 | sap 消耗 | 目标下回合攻击掷骰带 disadvantage 来源（check.resolved advantage_mode/sources）+ 攻击结算后 condition.removed（consume_on） |
| M7 | handaxe 命中且伤害 > 0 | applied next_attack_advantage（挂目标）；下次对它攻击 advantage + consume_on 移除 |
| M8 | handaxe 命中但伤害 0（全抗归零） | **不** applied（requires_damage 门槛——与 M5 形成对照，2024 原文差异的守护测试） |
| M9 | javelin 命中伤害 > 0 | applied slowed_10；目标 effective_speed -10；attacker 下回合开始时 condition 过期（推回合验证） |
| M10 | 解锁门控 | 法师（无 weapon_mastery 特性）持同武器命中 → 无任何 mastery 事件 |
| M11 | Shield 改判 miss | 玩家持 sap 武器攻击有 Shield 的目标 → HIT_REACTION resolve cast 改判落空 → **无** next_attack_disadvantage（held 正确作废；本用例构造攻守双方：攻击者=玩家 fighter、防守者=玩家法师带 Shield——若 PvP offer 不可构造，用 enemy 带 Shield 特性的等价路径不可行时，降级为直调 `_submit_hit_damage` 单测 + 白名单注明） |
| M12 | UD 减半并存 | sap/vex 武器命中 → defender 用 UD 减半 → mastery 照常 applied（命中未被取消） |
| M13 | 裁剪词条 fail closed | greataxe（cleave）/quarterstaff（topple）命中 → 无额外效果事件、无异常 |
| M14 | 归属守护 | 15 条武器档案 mastery 值与 §3.1 表逐条全等（目录断言测试） |

### 4.2 回归守护（现有文件，零改动、预期全绿）

- ray_of_frost slowed_10 / vicious_mockery next_attack_disadvantage / chill_touch duration 的既有法术测试（执行侧 grep 定位：`slowed_10|next_attack|caster_next_turn` in `tests/rulesets/`，文件与用例名写进执行报告）——condition 复用与 duration 新枚举的回归面；
- `test_dnd2024_uncanny_dodge.py` / `test_dnd2024_origin_feat_spells.py`（Shield 路径，M11 的反向守护）；
- `test_dnd2024_class_features.py`（目录结构守护：五段新条目不破坏现有声明测试）；
- `test_dnd2024_sneak_attack.py` / `test_dnd2024_stoneskin.py`（damage_event feature_use 通道被扩展后行为不变）。

---

## §5 验收命令（PowerShell，无 `&&`）

```powershell
python -m pytest tests/rulesets/test_dnd2024_weapon_mastery.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_stoneskin.py -q
```

全绿后附加全量回归：

```powershell
python -m pytest tests/rulesets -q
```

## §6 禁止事项

1. 禁止在 offer 链之前直接发命中后词条的 condition.applied（Shield 改判 miss 的正确性依赖 held 弹出；违反即 M11 红）。
2. 禁止改动 `check_modes.py` / `engine._consume_attack_conditions` / conditions 目录既有条目（三词条全部复用；vex 的 scope 精确化是债 D3，不在本切片偷做半吊子 scope）。
3. 禁止为 cleave/nick/push/topple 留半实现（`"kind": "unimplemented"` fail closed 是唯一合法占位；档案位先行是显式设计，不是未完成）。
4. 禁止给 mastery 效果加新决策窗口、新意图参数、新骰位（graze 零骰；SP2 §3.8 顺序不因本切片出现第 6 位）。
5. 禁止动 enemy stat-block 路径与 15 条武器档案的既有字段（damage/range/properties 等）。
6. 禁止把"装备即精通"近似悄悄扩大为数量 gate 或选择 UI（债 D1 边界）。
7. 禁止触碰 P7/P8 在途文件区域（`resolution.py` HEAD ≥L615 / `validation.py` ≥L470 / `srd_combat_core.json` 的 spell_effects 段）——除非按状态行建议已在其合入后开工。
8. 禁止在 `srd_class_features.json` 为 weapon_mastery 声明 capabilities 或 resource（解锁型特性，无动作无池）。

## §7 停止条件

1. 开工时 `git status` 与 §2.2 不符（P7/P8 已合入/新增未跟踪文件）→ 重读基线、按偏移表重锚后再继续；`srd_combat_core.json` 的 +1 行落在 weapons/conditions 段内 → 停止请示。
2. `reducer.py` 的 duration 机制与 §3.4 假设不符（无枚举消费点 / duration 不是字符串驱动）→ 停止，按实际机制重新设计 `attacker_next_turn_start` 而非猜测。
3. M11 的 Shield 改判场景无法按现有 offer 结构构造（side 限制）→ 停止请示，不得为测试改引擎 side 规则。
4. `catalog.py` 的 weapons 加载对未知键 fail closed（mastery 键被拒）→ 停止，先扩展加载器白名单并请示（与 §3.3(a) 模块归属一并确认）。
5. 侧骑三词条（sap/vex/slow）任一与既有法术测试语义冲突（复用 condition 反向破坏 ray_of_frost / vicious_mockery 行为）→ 停止请示。
6. SP2/SP10 在途实现与本切片的 damage_event/mastery_effects 字段冲突 → 停止请示。

## §8 执行报告模板与迁移债登记

### 执行报告（执行侧填写）

- 改动文件清单（对照 §3 白名单，逐文件：实际 diff 摘要 / 与规格的偏差及原因；`weapon_masteries` 注册表最终归属模块）；
- §4 矩阵逐用例结果（M1-M14 + 回归清单，含 §4.2 定位到的法术 condition 回归文件名）；
- §5 验收命令原样回贴（两次）；
- 行号偏移复核结果（§2.2 是否仍成立；`attacker_next_turn_start` 枚举的 reducer 落点行号）；
- 未尽事项与建议。

### 迁移债登记（本切片产生的显式债务）

| 债 | 内容 | 归属 |
|---|---|---|
| D1 | "三种自选 + 长休重选一个"的选择链（build 期选择 + rest 期改选 + 数量轨道消费 3/4/5/6 与 barbarian 2/rogue 2 等）；当前"装备即精通"宽松近似 | build/rest 选择批次（阶段二） |
| D3 | vex 的精确 scope（仅命中者本人对该目标下次攻击优势）——需 attack_modes per-pair 校验（对齐 hexed source_actor_id meta 模式） | check_modes 谓词扩展批次 |
| D4 | slow 的"你可以"可选性（自动生效 → 决策窗口化） | UX 批次（收益评估后决定是否值得弹窗） |
| D5 | Cleave 横扫：命中后决策 + 第二目标候选集 + 无属性修正二次攻击 + 每回合一次记账 | 命中后效果扩展批次（与 S-P9 债 D1 同族） |
| D6 | Nick 迅击：依赖轻型词条双持附赠攻击机制（引擎未建模 light 双持 bonus attack） | 双持/武器词条批次 |
| D7 | Push 推离：15 条档案无载体武器（巨棒/长矛/战锤/重弩未入档）+ 位移 rider | 武器档案扩容 + P6 位移扩展批次 |
| D8 | Topple 失衡：prone 条件族（起身移动消耗、对 prone 的距离谓词攻击优劣势）+ 命中后强破豁免（DC 8+调整值+熟练加值） | 条件系统批次 |
| D9 | mastery 词条的中文标签与 feed 展示（labels/locales 层） | 阶段二 i18n 批次 |
