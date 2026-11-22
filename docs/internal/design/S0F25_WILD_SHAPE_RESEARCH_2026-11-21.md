# S0-F25 调研：Wild Shape 与变形类法术的现状、缺口与做法

| 项 | 值 |
|---|---|
| 调研对象 | S0-F25 Wild Shape（Druid 核心）、Polymorph / True Polymorph（变形类法术） |
| 日期 | 2026-11-21 |
| 结论 | 现状**没有**任何可复用的变形地基；需新建「数据层 + 形态覆盖层 + 结算层」三件事，Polymorph 复用同一地基、语义另走一条分支 |
| 关联缺口 | `GAP-20`（Beast 目录）、`GAP-21`（形态覆盖派生）、`GAP-22`（形态攻击入玩家行动）、`GAP-23`（变形生命周期与施法门控） |

## 1. 现状（逐条核实）

| 事实 | 证据 |
|---|---|
| 引擎里**没有任何** wild_shape / polymorph / transform 痕迹 | `grep -rni 'wild_shape|polymorph|shapechange|transform' src/rulesets/dnd2024/` 仅命中一句注释 |
| **没有持久 Beast 目录**：敌人 statblock 是 `combat.start` 意图里**内联注入**的临时 dict，不是可复用的内容数据 | `combat/validation.py::validate_enemy_profiles`；`director/temporary_encounter.py::_passthrough_statblock_facts`（MM1 只做透传） |
| 敌人 statblock 事实已含 `abilities / armor_class / speed / saving_throws / attacks[] / senses`——形状足够，但只喂给敌人 | 测试夹具 `goblin()`；`resolution.py` 对 `raw["attacks"]` 的 deepcopy 只在敌人路径 |
| 玩家角色属性、AC、速度、感官**全部**从角色卡派生，无「覆盖」输入 | `character/derivation.py::derive`（`abilities` 861、`armor_class` 897、`speed` 899） |
| 玩家攻击来源只有**装备（武器）**和**职业特性 capability** | `resolution.py` 攻击派生 = 属性修正 + 熟练 + 装备；无「形态攻击」入口 |
| 变身没有事件、没有恢复原形、没有「变形中不可施法」门控、没有「持续效果跨形态保留」 | 事件白名单与 `INTENT_TYPES` 均无 |
| `polymorph` / `true_polymorph` 法术**只有元数据**（等级/学派/职业/成分/专注/时长），无效果原语 | `srd_spells.json` 4208 / 6713 |

## 2. 缺什么

**GAP-20 数据**：持久 Beast 目录（CR0–1 全量 + Fly 形态）。敌人块与可变形块应是同一份数据、同一形状（复用 MM1 的 statblock 事实），只是敌人块内联、Beast 块进目录。

**GAP-21 形态覆盖派生**：actor_view / derivation 需要一个「覆盖表」输入，把 `abilities / size / speed / senses / AC / 攻击` 换成形态的，而**不碰**角色身份、HP/HD、技能与豁免熟练、class features、语言、专长、条件。这是让「持续效果跨形态保留」自动成立的关键——只要不派生那些字段，它们就天然还在。

**GAP-22 形态攻击入玩家行动**：玩家可用行动列表目前 = 装备攻击 + capability。Beast 的 `attacks[]` 要成为第三种来源，且仍走同一套 `_attack_events`（复用敌人攻击结算，而不是再写一套）。

**GAP-23 生命周期与门控**：`form.assumed` / `form.reverted` 事件；变形中 No Spellcasting（18 级 Beast Spells 豁免）；装备 `drop / merge / wear` 三态且 merge 后效果失效；Polymorph 的 HP 归零溢出回原形。

## 3. 怎么做

### 3.1 核心洞察：不要做「第二种角色」

Wild Shape 和 Polymorph 都**不是**新建一个 actor。角色身份仍是 `player:gm`，只是 actor_view 的**派生输入**从「角色卡」变成「角色卡 + 覆盖表」。这样：

- HP、class features、语言、专长、条件、法术专注——**根本不动**，跨形态保留自动成立；
- 覆盖表只回答「哪些字段改用形态的值」，逻辑集中在一个函数，不散到各处。

### 3.2 三件事的落点

1. **数据层**：新建 `content/beast_catalogs/srd_beasts.json`（官方 Animals p344–364，CR0–1 + Fly 形态），字段 = statblock 事实形状 + `source_ref` 页码 + `creature_type`。加载器 fail closed：缺字段、CR/Fly 不符即拒绝，不用测试夹具冒充内容。
2. **派生层**：`derive` 增加可选输入 `form_override`。Wild Shape 覆盖 `{str,dex,con,size,speed,senses,ac,attacks}`、保留其余；Polymorph 覆盖**全部**（含 HP、心智、动作）并追加「不可施法/不可用装备」。两态共用同一个覆盖入口，用 `override_scope` 区分，而不是写两个平行派生。
3. **结算层**：`dnd2024.form.assumed` / `dnd2024.form.reverted` 两个事件；恢复原形时把「覆盖前」的持久字段从状态里读出还原（不是重新算，是还原快照）；Polymorph 用 `overflow_damage` 把形态 HP 归零后的溢出打回真形。

### 3.3 与现有体系的对齐

- **施法门控**：复用「条件/状态禁用动作」的既有机制（`forbidden_condition` 那种），把「变形中」做成一个 gates spellcasting 的 source，而不是在 `_spell_events` 里写 `if in_form`。
- **攻击复用**：形态攻击走 `_attack_events`（敌人攻击已在用），不新建攻击族。
- **资源**：Wild Shape 次数池已存在（`srd_recovery.json` 2–20 级轨道），不用新建。

### 3.4 顺序（最小可验证路径）

```
GAP-20 数据（Beast 目录 + 加载器 + 校验）      ← 纯数据，先做
  └─ GAP-21 覆盖派生（abilities/AC/speed/senses 可换源）
      └─ GAP-22 形态攻击入玩家行动（复用 _attack_events）
          └─ GAP-23 生命周期：assume/revert + 施法门控 + 装备三态
              └─ Polymorph 作为覆盖 scope=全部 的第二个消费者
```

Polymorph 放在最后，不是因为它不重要，而是它要「HP 完全替换 + 溢出」这套语义，比 Wild Shape 多半个量级；先让 Wild Shape 把地基跑通，Polymorph 只是换 scope。

## 4. 与总体目标的关系

12 职业 12 子职全可玩是总目标，Druid 不落地 Wild Shape 就只剩半个职业（Moon Circle 更是全部围绕它）。Polymorph 是跨职业法术（Bard/Druid/Sorc/Wiz），但都吃同一个变形地基。所以这份调研的产出不是「F25 一个片」，而是**一条变形地基切片链**：数据 → 覆盖派生 → 形态攻击 → 生命周期，最后 Polymorph 作为复用的第二消费者收尾。

## 5. 停止条件（未来执行时的硬线）

1. 没有真实 Beast 目录就写 `form.assumed` → 停止。
2. 覆盖派生要改角色身份或 HP/HD → 停止（说明设计错了，变成了「第二种角色」）。
3. 形态攻击没走 `_attack_events` 而是另写一套 → 停止。
4. 「持续效果跨形态保留」要靠额外快照手工搬运 → 停止（派生层设计没对，应天然成立）。
