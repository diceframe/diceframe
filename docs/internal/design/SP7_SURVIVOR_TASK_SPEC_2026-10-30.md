# 切片 S-P7 任务书：Survivor 百折不挠（回合开始再生 + 死亡豁免优势/阈值）

版本：v1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片**硬依赖 S-P1**（`features_for` 子职所有权合并、`min_feature_value` API、`srd_class_features.json` fighter 区域、`test_dnd2024_class_features.py` 同文件）——S-P1 未合入则停止。同时与 1B 进行中的 P6（misty_step）同文件 `combat/resolution.py`，**必须在 P6 合入、工作区干净后开工**。本任务书锚点基于 `1009be03`（P5 已合入、P6 之前），执行侧须对受 P6 影响的文件自行重核行号。S-P4、S-P5 与本切片共同触碰 `resolution.py` / `features/resolver.py` / `srd_class_features.json`，三切片串行执行，不得并行开改。

---

## 1. 目标

为 D&D 2024 规则集打通 **勇士（Champion，战士代表子职）18 级特性百折不挠（Survivor）** 的完整闭环：特性声明 → 回合开始再生 → 死亡豁免优势与 18-20 视同 20 → 测试。

本切片沉淀两项通用原语：

1. **回合开始再生（turn-start regeneration）**：特性参数 `turn_start_regen_base` + `turn_start_regen_ability`（成对），回合推进事件 `dnd2024.turn.advanced` 之后、新行动者满足门控时构造 `resource.changed hp` 治疗事件。后续任何"回合开始自愈"类特性（如 future 巨魔再生）注册同参数即可，不另建管线。
2. **死亡豁免优劣势与阈值（death save advantage & threshold）**：布尔参数 `death_save_advantage` 驱动死亡豁免双骰取高（复用既有 rage 豁免的事件形状），数值参数 `death_save_crit_threshold` 复用 S-P1 的 `min_feature_value` 消费模式把"投出 20 的好处"扩展到 18-20。后续任何死亡豁免优势来源（专长、魔法物品）注册同参数即可。

**规则语义（2024 版中文规则书原文，已核实）**：

> **18级：百折不挠 Survivor** 你持续战斗的能力达到巅峰，赋予了你以下好处：
> **蔑视死亡 Defy Death。** 你进行死亡豁免时具有优势。此外，若你在死亡豁免上投出了18~20时，都会获得相当于投出20的好处。
> **英气风发 Heroic Rally。** 你的回合开始时，若你已浴血且拥有至少1生命值，你恢复5＋你的体质调整值的生命值。

辅助语义：

- 已浴血（Bloodied）＝生命值等于最大生命值一半或更少（`.rulebooks/dnd5r/玩家手册2024/进行游戏/生命值.htm` L7）。
- 死亡豁免通用规则（`.rulebooks/dnd5r/玩家手册2024/进行游戏/生命值降至0点.htm` L33-39）：d20 不加任何调整值；10 及以上记 1 次成功，9 及以下记 1 次失败；投出 20 恢复 1 HP 且成功/失败计数归零；投出 1 记 2 次失败；3 次成功稳定，3 次失败死亡。

出处：

- `.rulebooks/dnd5r/玩家手册2024/角色职业/战士/勇士.htm` L46-47（百折不挠，两项子效益全文）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/生命值.htm` L7（浴血定义）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/生命值降至0点.htm` L33-39（死亡豁免规则）。

### 1.1 必须锁定的规则边界

1. **蔑视死亡只作用于死亡豁免**（`_death_save_events`），不作用于任何其它豁免、攻击检定或能力检定——与 rage 的力量豁免优势（`_save_repeat_events` / 法术豁免分支）、运动健将的检定优势（P10 域）完全正交。本切片禁止改动 `_save_repeat_events`。
2. **"20 的好处"分支优先**：判定顺序为 `roll >= threshold`（恢复 1 HP + 计数归零）→ `roll == 1`（2 次失败）→ `roll >= 10`（1 次成功）→ 其余（1 次失败）。threshold ∈ 2..19 时天然 1 永远低于阈值，顺序无冲突。投出 10-17 仍是普通成功，2-9 仍普通失败。
3. **优势双骰只掷一次**：`feature_flag_sources` 返回多个来源时仍是两颗骰取高，不掷三颗；来源列表只是事件 provenance（rage 先例的 `advantage_sources` 形状）。2024 语境下不存在"优势+劣势相消"的组合需求，本切片不实现相消。
4. **英气风发只在新行动者自己的回合开始触发**，挂点在 `_end_turn_events` 返回序列的 `dnd2024.turn.advanced` 之后。他人回合、以及战斗开场（`dnd2024.combat.started` 不发 `turn.advanced`，resolution.py L107）不触发。
5. **门控**：`1 <= hp <= max_hp // 2`（已浴血且至少 1 HP）。hp=0 倒地、`dead`/`stable` 状态、满血、未浴血一律不触发。已浴血取整遵循 `hp <= max_hp // 2` 的整数下除。
6. **再生量不掷骰**：`amount = max(0, base + ability_modifier)`。体质调整值可为负；`amount <= 0` 不发事件（不倒扣）。多源再生取 `turn_start_regen_base` 最大者的 `(base, ability)` 成对组合，不叠加。
7. **再生受 `cannot_regain_hp` 阻断**：构造点检查（对齐既有治疗分支先例），阻断时事件 `delta=0` 并带 `blocked_by: "cannot_regain_hp"` 标注，reducer 零新逻辑。
8. **再生不消耗经济**：纯被动 tick，不碰 action/bonus/spell 经济。
9. **数据驱动**：resolver 与 resolution 零 `survivor` 字符串分支。`advantage_sources` 的 `code` 值是目录声明 id 的数据透传（既有 `{"code": "rage", "mode": "advantage"}` 先例），判定逻辑本身不出现特性名。
10. **`minimum_level` 一致性校验复用 S-P1**：S-P1 已在 `_validate_declarations` 实现"声明等级必须等于子职表授予等级"的通用校验，本切片声明 survivor（18 级）自动受其保护，不重复实现。

### 1.2 本切片的产品策略与裁剪

- **战斗开场首行动者再生裁剪**：`combat.started` 是"战斗开始"事件而非"回合开始"，开场即已浴血且 hp>=1 的首行动者将错过第一次再生。扩展开场事件序列影响所有既有消费者，收益（罕见场景的一次 tick）不匹配风险——**明确裁剪**，测试矩阵锁定该行为，迁移债记录于 §8。
- **死亡豁免不吃 bless**：2024 RAW 中 bless 可加豁免检定，但现有引擎死亡豁免为纯 d20（无 bless 消费点），这是既有决定。本切片不扩大也不缩小，S-P10 泛化条件加值时如需覆盖死亡豁免须回头扩展（迁移债）。
- survivor 无玩家可选行为：再生与死亡豁免优势全部被动自动生效，零新增前端交互，零 UI 配置。
- 非 champion 途径的死亡豁免优势/回合再生（future 专长、魔法物品、巨魔再生）不在本切片实现，但两项原语的参数注册天然向其开放。
- `check.resolved`（kind=death_save）只在 `crit_threshold != 20` 时追加 `crit_threshold` 字段（对齐 S-P1 攻击事件的字段策略），最小化对既有事件消费方的扰动；`dnd2024.death_save.resolved` 既有字段零改动。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| `_end_turn_events` 结构：胜利/团灭检查 → `_save_repeat_events`（前行动者**状态**重复豁免，hold person 类，与死亡豁免正交）→ 跳过 dead/stable 与归零敌人队友 → 构造 `next_view` → 返回 `[*events, {turn.advanced...}]` | `combat/resolution.py` L1015-1062 |
| `_death_save_events`：单骰 `rng.randint(1,20)`；`roll==20 → hp=1, successes=failures=0`；`roll==1 → failures+=2`；`roll>=10 → successes+=1`；事件对 `check.resolved(kind=death_save, rolls=[roll], natural, target=10, success=roll>=10)` + `dnd2024.death_save.resolved {roll, successes, failures, stable, dead, hp}`；末尾未死未醒则自调 `_end_turn_events` | `combat/resolution.py` L1064-1096 |
| 优劣势双骰既有形状先例（rage 力量豁免）：`first`/`second = randint if advantage else None`、`roll = max`、`rolls = [v for v in (first, second) if v is not None]`、`advantage_mode: "advantage"` + `advantage_sources: [{"code": "rage", "mode": "advantage"}]` | `combat/resolution.py` L494-509（法术豁免分支）、L975-1013（`_save_repeat_events` 同款） |
| 治疗事件既有形状与 `cannot_regain_hp` 阻断：`delta/amount` 正数 + `healing: True`，阻断时 `delta=0` + `blocked_by: "cannot_regain_hp"` | `combat/resolution.py` L564-603 |
| resolution 读特性参数既有模式：`self.features.feature_value(self._feature_character(actor), "rage", "rage_damage", 0)` | `combat/resolution.py` L314-318 |
| `_actor_view` 携带 `hp`/`max_hp`/`conditions`（玩家从 `resources.max_hp`，敌人独立字段） | `combat/view.py` L75、L126 |
| `_feature_character` 携带 build/abilities/class resources/equipment（看不到顶层 features） | `combat/view.py` L337-362 |
| `ability_modifier` 纯函数 | `character/builder.py`（`advancement.py` 既有 import 先例） |
| champion 子职表已含 `"18": ["survivor"]`，**子职目录零改动** | `templates/rulesets/dnd2024_srd/content/subclass_catalogs/srd_subclasses.json` L33-36 |
| S-P1 沉淀：`features_for` granted 并入 `_subclass_feature_ids(class_id, level)`；公共 API `min_feature_value(character, key, default)` | `features/resolver.py`（S-P1 任务书 §3.3，合入后为准） |
| 特性声明 JSON 结构：labels + classes.\<class\>.features[]（被动特性无 capabilities，先例 sneak_attack / improved_critical） | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`；locale 版本在 `locales/en` 与 `locales/zh-CN` 同名文件 |
| `_validate_declarations`：跨 bundle 校验的既有家 | `features/resolver.py` L527-556 |
| `dnd2024.combat.started`（round 1 + initiative）不发 `turn.advanced`——开场边界的事实依据 | `combat/resolution.py` L107 |
| reducer 对 `resource.changed hp` 正负 delta 与治疗既有落账（上限 clamp、temp 吸收），本切片零 reducer 改动 | `combat/reducer.py` L388-408 |
| 测试基建：`SequenceRng` 序列控骰；战斗搭建先例 `from tests.rulesets.test_dnd2024_combat import _instance, _start, SequenceRng`；`advance(runtime, sheet, level)` 构造高等级角色 | `tests/rulesets/dnd2024_monk_common.py` L23、`test_dnd2024_combat.py` L20、`test_dnd2024_narrative_health.py` L12、`test_dnd2024_sneak_attack.py` L26-32 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

`classes.fighter.features` 追加一条被动声明（无 `capabilities`）：

```json
{
  "id": "survivor",
  "minimum_level": 18,
  "source_ref": "srd-5.2.1:p49:survivor",
  "summary": "You have Advantage on Death Saving Throws, treat a roll of 18-20 on one as a 20, and while Bloodied you regain 5 plus your Constitution modifier Hit Points at the start of each of your turns.",
  "parameters": {
    "death_save_advantage": true,
    "death_save_crit_threshold": 18,
    "turn_start_regen_base": 5,
    "turn_start_regen_ability": "con"
  }
}
```

`labels` 追加 `"survivor": "Survivor"`。子职目录（`srd_subclasses.json`）零改动。

### 3.2 locale 文件（两个）

`locales/en/class_feature_catalogs/srd_class_features.json` 与 `locales/zh-CN/class_feature_catalogs/srd_class_features.json` 的 labels 同步追加；zh-CN 用规则书译名：百折不挠。

### 3.3 `src/rulesets/dnd2024/features/resolver.py`

1. 新增公共 API `max_feature_value_source(character, key, default) -> tuple[int, str]`：扫描 `features_for(character)` 返回的定义，收集 `parameters[key]` 为 `int`（非 `bool`）的条目，取值最大者，返回 `(该值, 该声明 id)`；无命中返回 `(default, "")`。这是回合再生"多源取最大且知晓来源"的聚合口；与 S-P1 的 `min_feature_value` 是对偶关系——允许（但不强制）把两者实现为共享私有枚举的薄封装，若 S-P1 已留此结构则并入，否则独立实现。
2. 新增公共 API `feature_flag_sources(character, key) -> list[str]`：返回 `features_for(character)` 中 `parameters[key]` 为 truthy 的声明 id 列表（按 `features_for` 既有顺序）；无命中返回 `[]`。布尔 flag 的跨特性查询口，`advantage_sources` 的 provenance 直接由该返回值构造。
3. `_validate_declarations` 追加四条 fail closed 校验（存在才校验）：
   - `death_save_advantage` 必须 `is True`（`False`/非布尔一律 `ClassFeatureError`——想关闭就不要声明）；
   - `death_save_crit_threshold` 必须 `isinstance(v, int) and not isinstance(v, bool) and 2 <= v <= 19`；
   - `turn_start_regen_base` 必须 `isinstance(v, int) and not isinstance(v, bool) and v >= 0`；
   - `turn_start_regen_ability` 必须是 `ABILITY_IDS` 内的字符串，且 **与 `turn_start_regen_base` 成对出现**（声明其一而缺另一个即 `ClassFeatureError`）。
4. minimum_level == 子职表授予等级的校验已由 S-P1 落地，本切片不重复实现。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

1. **`_end_turn_events` 尾部**（基线 L1061-1062 的返回语句）：在 `dnd2024.turn.advanced` 事件之后追加再生事件，改为：
   ```python
   return [*events, {turn.advanced 事件原样}, *self._turn_start_regen_events(next_view)]
   ```
   `next_view` 是该方法既有构造，直接复用；`turn.advanced` 事件体一字不动。
2. **新增私有辅助 `_turn_start_regen_events(self, actor) -> list[dict]`**：
   - 门控：`int(actor["hp"]) >= 1` 且 `int(actor["max_hp"]) > 0` 且 `int(actor["hp"]) <= int(actor["max_hp"]) // 2`，不满足返回 `[]`；
   - `character = self._feature_character(actor)`；`base, source_id = self.features.max_feature_value_source(character, "turn_start_regen_base", 0)`；`base <= 0` 返回 `[]`；
   - `ability = str(self.features.feature_value(character, source_id, "turn_start_regen_ability", "con"))`（`source_id` 非空由上一行的命中保证；`ability` 合法性由目录校验保证，不做二次防御）；
   - `amount = max(0, base + ability_modifier(int(character["abilities"].get(ability, 10) or 0)))`；`amount <= 0` 返回 `[]`；若 `character["abilities"]` 取值路径与 `_feature_character` 实际形状不符，以 S-P1 合入后的 `crit_threshold` 消费代码同款方式取能力分数；
   - 阻断：`blocked = "cannot_regain_hp" in actor["conditions"]`；
   - 返回单元素列表（对齐治疗分支形状 + S-P4 的 feature_id 来源标注先例）：
     ```python
     [{
         "type": "resource.changed", "resource": "hp",
         "target_id": actor["actor_id"],
         "delta": 0 if blocked else amount, "amount": 0 if blocked else amount,
         "healing": True, "feature_id": source_id,
         **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
     }]
     ```
     `rolls` 不带（再生无骰）。
3. **`_death_save_events`**（基线 L1064-1096）：
   ```python
   character = self._feature_character(actor)
   advantage_sources = self.features.feature_flag_sources(character, "death_save_advantage")
   crit_threshold = int(self.features.min_feature_value(
       character, "death_save_crit_threshold", 20,
   ))
   first = int(rng.randint(1, 20))
   second = int(rng.randint(1, 20)) if advantage_sources else None
   roll = max(first, second) if second is not None else first
   ```
   分支把 `if roll == 20:` 改为 `if roll >= crit_threshold:`（恢复 1 HP + 计数归零语义原样），其余分支（`roll == 1` 双失败、`roll >= 10` 成功、else 失败）与 `stable`/`dead` 判定一字不动；
   `check.resolved` 事件的 `rolls` 改为 `[value for value in (first, second) if value is not None]`；当 `advantage_sources` 非空时追加 `advantage_mode: "advantage"` 与 `advantage_sources: [{"code": sid, "mode": "advantage"} for sid in advantage_sources]`（rage 先例形状）；当 `crit_threshold != 20` 时追加 `"crit_threshold": crit_threshold`（S-P1 攻击事件同款字段策略）；
   `dnd2024.death_save.resolved` 事件既有字段一字不动。
4. 如 resolution.py 尚未 import `ability_modifier`，新增 `from src.rulesets.dnd2024.character.builder import ability_modifier`（纯函数依赖，允许）。

### 3.5 测试

新文件 `tests/rulesets/test_dnd2024_survivor.py`：战斗搭建照 `test_dnd2024_narrative_health.py` L12 的 `from tests.rulesets.test_dnd2024_combat import _instance, _start, SequenceRng` 先例；18 级战士构造照 `test_dnd2024_sneak_attack.py` 的 `advance` 法。测试矩阵见 §4。

允许的最小既有测试更新：`tests/rulesets/test_dnd2024_class_features.py` 若存在"枚举 owned 特性全集"断言，按本切片预期追加 survivor；**不得**改动与 survivor 无关断言的语义。

回归命令涉及：`test_dnd2024_class_features.py`、`test_dnd2024_combat.py`、`test_dnd2024_improved_critical.py`（S-P1）、`test_dnd2024_rage.py`（优劣势形状回归）、`test_dnd2024_hold_person.py`（`_save_repeat_events` 区域回归）、`test_dnd2024_advancement_access.py`。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`death_save_crit_threshold` 为 1、20、字符串、布尔 | `ClassFeatureError` |
| 2 | 目录校验：`turn_start_regen_base` 为 -1、布尔、字符串 | `ClassFeatureError` |
| 3 | 目录校验：`turn_start_regen_ability` 为 "xyz"、int；有 base 缺 ability、有 ability 缺 base | `ClassFeatureError`（成对校验） |
| 4 | 目录校验：`death_save_advantage` 为 false、1、"true" | `ClassFeatureError` |
| 5 | 所有权：advance 到 18 级的战士 | `has_feature(survivor)` True；17 级 False |
| 6 | 所有权：builder 直建 18 级战士（不走升级链） | `has_feature(survivor)` True（S-P1 推导路径复验） |
| 7 | 再生触发：18 级战士 hp 恰为 `max_hp // 2`（≥1） | `advance_turn` 事件序列为 `[..., turn.advanced, resource.changed]`；再生事件 `resource: hp`、`healing: True`、`amount == 5 + con_mod`、`feature_id == "survivor"`；落账后 hp 增加 |
| 8 | 再生不触发：hp 为 `max_hp // 2 + 1`、满血 | 无 `resource.changed` 再生事件 |
| 9 | 再生不触发：hp=0（倒地）、dead、stable | 无再生事件；hp=0 战士轮到时走死亡豁免 |
| 10 | 再生数值：con 调整值 -3 | amount 为 2；con 调整值构造不出正再生（base=0 fixture）时无事件 |
| 11 | 再生上限：hp + amount 超过 max_hp | 落账 clamp 到 max_hp（reducer 既有，断言终值） |
| 12 | 阻断：战士带 `cannot_regain_hp` 条件且已浴血 | 再生事件存在但 `delta == 0` 且 `blocked_by == "cannot_regain_hp"`，hp 不变 |
| 13 | 时序：前一行动者被 hold person（有 `save_repeat`）且 18 级战士已浴血 | 事件序列 `[check.resolved(repeat_save), ..., turn.advanced, resource.changed(再生)]`——repeat 在前、advanced 居中、再生最后 |
| 14 | 死亡豁免优势：18 级战士 hp=0 做死亡豁免 | `check.resolved` 的 `rolls` 长度 2、`advantage_mode == "advantage"`、`advantage_sources` 含 `{"code": "survivor", "mode": "advantage"}` |
| 15 | 阈值：SequenceRng 投 18 | `dnd2024.death_save.resolved` 的 `hp == 1`、`successes == failures == 0`（视同 20） |
| 16 | 阈值：投 19、投 20 | 19 同 #15；20 原生路径仍工作（`check.resolved` 不带 `crit_threshold` 字段时为 20 缺省路径） |
| 17 | 低等级对照：17 级战士死亡豁免 | `rolls` 长度 1、无 `advantage_mode`；投 18 只是普通成功（successes +1，无恢复） |
| 18 | 天然 1：18 级战士双骰 [1, 1] | `failures` +2；双骰 [1, 15] 时取高 15 → 普通成功（优势不救天然 1 的语义由取高自然保证） |
| 19 | 开场边界：战斗开始时首行动者已浴血且 hp≥1 | `combat.started` 之后**无**再生事件（裁剪行为锁定） |
| 20 | 职业隔离：18 级游荡者/法师（advance 到 18） | `has_feature(survivor)` False；死亡豁免单骰 |

注：#7、#12、#13 的战斗中变更 hp 用测试内直接改 combat state 或既有测试同款伤害路径，照 `test_dnd2024_combat.py` / `test_dnd2024_narrative_health.py` 既有做法，不得为测试新增生产代码通道。

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_survivor.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_improved_critical.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_hold_person.py tests/rulesets/test_dnd2024_advancement_access.py -q
```

（`test_dnd2024_character_builder.py` 上游既有失败不计。）

## 6. 禁止事项

1. 禁止把再生、优势、阈值逻辑写进 reducer 或任何投影层——reducer 纯机械落账，特性逻辑只存在于事件构造点（`_end_turn_events` / `_death_save_events`）。
2. 禁止在 resolver 或 resolution 出现 `survivor` 字符串判定分支——必须走 `turn_start_regen_*` / `death_save_*` 参数与 `max_feature_value_source` / `feature_flag_sources` / `min_feature_value`。`advantage_sources` 的 `code` 与 `feature_id` 是声明 id 的数据透传，不是逻辑分支。
3. 禁止改动 `_save_repeat_events` 与 rage 的优劣势路径——蔑视死亡只在死亡豁免生效，2024 原文限定明确。
4. 禁止给死亡豁免引入 bless 消费或任何调整值加成——保持纯 d20 现状（§1.2 迁移债）。
5. 禁止把英气风发做成掷骰或可配置公式——规则原文是固定 `5 + 体质调整值`，扩展只通过目录参数，不通过代码分支。
6. 禁止在本切片实现 heroic_warrior、remarkable_athlete、additional_fighting_style 或 champion 其它未声明特性。
7. 禁止修改 `dnd2024.death_save.resolved` 事件的既有字段语义（`hp`/`successes`/`failures`/`stable`/`dead`）与 `turn.advanced` 事件体。
8. 禁止对战斗开场路径做任何扩展来实现开场再生——该边界已明确裁剪（§1.2）。

## 7. 停止条件

1. S-P1 或 P6 尚未合入，`features/resolver.py` / `combat/resolution.py` 基线与本任务书锚点无法对齐；
2. reducer 对 `resource.changed hp` 正 delta（healing、带 `feature_id`、无 `rolls`）的既有落账存在缺陷，修复需触碰 `combat/reducer.py`（白名单外）——报告并等待裁决；
3. `dnd2024.death_save.resolved` 的下游消费方（reducer / 投影 / 前端契约）对"非 20 骰触发 `hp: 1`"存在隐藏断言，修复超出白名单——报告并等待裁决；
4. 测试搭建中发现 18 级战士无法通过 `advance` 或 builder 构造（升级链/建造器在 18 级存在 S-P1 未覆盖的缺口）——报告并等待裁决，不得为测试顺手修生产代码；
5. `srd_class_features.json` 中 survivor 已被其它并行切片声明（工作区冲突信号）；
6. 既有回归测试因 18-20 分支扩展或 turn.advanced 追加事件而系统性失败，且根因不在白名单文件内。

## 8. 执行报告模板

执行完成后在本文件追加"执行报告"节：变更文件清单、测试矩阵逐项结果、偏差与遗留（`max_feature_value_source` / `feature_flag_sources` 的最终签名、`check.resolved` 事件最终字段、`_turn_start_regen_events` 的取能力分数方式、既有测试更新范围）、迁移债确认（48 子职后所有权须改 subclass_ref 感知；bless×死亡豁免待 S-P10 裁决；战斗开场回合开始钩子缺失；开场再生裁剪的已知边界）。
