# 切片 S-P4 任务书：Dark One's Blessing（击败/归零触发 + 临时生命值）

版本：v1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：
1. **依赖 S-P1（子职特性所有权）已合入**——`features_for` 必须已把子职目录 `feature_ids_by_level` 并入 granted 集合，否则 `has_feature("dark_ones_blessing")` 恒为 False。若 S-P1 尚未合入，停止。
2. 本切片改动 `combat/resolution.py`，与 1B 进行中的 P6（misty_step）同文件。**必须在 P6 合入后开工**。
3. 临时生命值原语（`temp_hp` 事件与 reducer keep-max）已由 1B P4（false_life）落地，本切片只消费不重建。
本任务书锚点基于 `1009be03`，执行侧须自行重核行号。

---

## 1. 目标

为 D&D 2024 规则集打通 **黑暗赐福（Dark One's Blessing，邪魔宗主 3 级）** 的完整闭环：特性声明 → 敌人被降至 0 HP 触发 → 特性持有者获得临时生命值 → 临时生命值先吸收后续伤害 → 测试。

本切片沉淀一项通用原语：

**击败/归零触发（defeat trigger）**：当一次伤害把一个存活敌人降至 0 HP 时，引擎在每个伤害落账点统一询问特性边界——"哪些参战者的已拥有特性声明了击败触发载荷"。载荷以封闭参数集声明（见 §3.1），不出现具体特性名分支。后续消费者（如其它"击败时"特性、装备词条）复用同一触发点与参数。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 黑暗赐福（3 级）："当你将一个敌人的生命值降至 0 时，你获得等于你的魅力调整值＋你的魔契师等级的**临时生命值**（最低为 1）。而若其他人将一个位于你 10 尺内的你的敌人的生命值降至 0 时，你也会获得此增益。"
- 临时生命值通用规则：优先失去临时生命值；临时生命值无法叠加，已有临时生命值时再获得更多须在保留原值与取新值之间二选一（本引擎按 P4 决策固定为保留较高者）；临时生命值既不是生命值也不是治疗，满生命值也可获得；生命值为 0 时获得临时生命值不能令你恢复意识。

出处：

- `.rulebooks/dnd5r/玩家手册2024/角色职业/魔契师/邪魔宗主.htm` L31-32（黑暗赐福）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/临时生命值.htm` L10-21（优先失去/不叠加/不是治疗）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/生命值降至0点.htm` L8（生命值降至 0 的含义）。

### 1.1 必须锁定的规则边界

1. **触发条件是"活着被降至 0"**：目标 `kind == "enemy"`、本次伤害前 `hp > 0`、`hp - damage <= 0`。已归零敌人再受伤不重复触发（防止鞭尸刷临时生命值）。
2. **两条授予路径，同一持有者单次结算只发一个事件**：路径 a：`attacker_id == 持有者`（无距离限制）；路径 b：攻击者是他人，且被击败敌人与持有者的距离 ≤ 分享半径（10 尺）。两个邪魔魔契师互为"他人"时各自独立结算。
3. **伤害口径与既有击败判定一致**：触发使用与 `_last_hostile_defeated` 相同的最终落账伤害（抗性、豁免减半、Uncanny Dodge 修正、至圣斩 rider 全部算完之后的值）。
4. **数值公式**：`max(1, 魅力调整值 + 魔契师等级)`。魅力调整值可为负，"最低为 1"是公式内保底，不是触发门槛。
5. **不叠加 = keep-max**：同一次结算或多次触发授予的临时生命值保留较高者，不累加——这是 P4（false_life）已锁定的确定性简化，本切片沿用，事件里不携带"玩家选择保留旧值"的决策。
6. **临时生命值先吸收伤害**：reducer 既有行为（`_apply_hp_change` 中 temp 优先吸收），本切片不改。
7. **触发只认敌人**：玩家/同伴归零（无论谁打的）不触发。"你的敌人"按阵营语义 = `kind == "enemy"` 的目标，本引擎玩家/同伴与敌方无中间阵营。
8. **事件顺序**：临时生命值事件排在造成归零的伤害事件之后、同批次内 `dnd2024.combat.ended` 之前；reducer 顺序应用，先落账归零再授予。战斗是否结束不影响已获得的临时生命值。
9. **授予对象只能是玩家/同伴**：reducer 对敌人目标 fail closed（`EventBatchError`），特性持有者本身来自 `combat["initiative"]` 的 player/companion 枚举，天然满足。

### 1.2 本切片的产品策略与裁剪

- 黑暗赐福是**被动触发**，零新增玩家交互：触发路径是既有 Attack / 施法 / 借机攻击按钮，人类玩家不需要任何额外点击。
- 邪魔宗主其余特性不做：`fiend_spells`（纯法术列表数据，归阶段二内容批）、`dark_ones_own_luck`（6 级，骰面操纵，归 S-P2）、`fiendish_resilience`（10 级，选择型抗性，P5 管线消费者）、`hurl_through_hell`（14 级，后置）。
- 击晕变体（"把生物生命值降到 1 并昏迷"）不做：引擎无敌人击晕语义。
- 临时生命值的"长休清除"与战斗外持续归 rest/快照生命周期管理，本切片不碰。
- 分享半径（10 尺）复用 1D 剧场距离，不引入 2D。
- GM 直伤/环境伤害若存在独立落账点，不在本切片接入（见 §7 停止条件）。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| **伤害落账点共 7 处**（本切片的接入清单，逐一核实在案） | — |
| ① 武器攻击直发：`_submit_hit_damage` + concentration + `_last_hostile_defeated` | `combat/resolution.py` L347-353 |
| ② 法术攻击 per-target：damage_event append + concentration + last_hostile | `combat/resolution.py` L450-462 |
| ③ 豁免 per-target（half_on_success 后、抗性后） | `combat/resolution.py` L524-535 |
| ④ 自动伤害 per-target（magic missile） | `combat/resolution.py` L537-556 |
| ⑤ ⑥ 命中反应决策落账（Shield decline / feature 两分支） | `combat/reaction_spells.py` L215、L265；决策携带 `attacker_id`/`target_id` |
| ⑦ 至圣斩 rider 落账（total = 主伤害 + rider） | `combat/triggered_spells.py` L158-165 |
| 只有武器攻击分支产生命中挂起决策（`_hit_reaction_offer`/`_hit_spell_offer` 唯一调用点） | `combat/resolution.py` L342-343 |
| `_submit_hit_damage` 定义 | `combat/resolution.py` L697 |
| `_resist_damage` 在事件构造前完成（触发拿到的已是最终伤害） | `combat/resolution.py`（P5 落地） |
| reducer 临时生命值：keep-max、敌人目标 fail closed | `combat/reducer.py` L367-386 |
| reducer 伤害吸收：temp 优先扣 | `combat/reducer.py` L388-408 |
| `_last_hostile_defeated` 是"最后一个敌人"判定（victory 用），**不是**单目标击败——本切片不得复用它做触发条件 | `combat/engine.py` L1216-1227 |
| 参战者枚举：`combat["initiative"]`（actor id 列表，含 player:/companion:/enemy:） | `combat/engine.py` L688、L999-1003 |
| 一维距离 `_distance(self, combat, first, second)` 按 actor id | `combat/engine.py` L1098 |
| `_actor_view(instance, combat, actor_id)`；视图带 abilities/conditions/hp/kind | `combat/view.py` |
| `_feature_character(actor)` → build/abilities/class resources/equipment | `combat/view.py` L337-362 |
| `class_state(character)` → (class_id, level)，魔契师等级从 state[1] 取 | `features/resolver.py` L91-117 |
| `has_feature` / `features_for`（S-P1 合并后子职特性可见） | `features/resolver.py` L123-154 |
| 特性参数标量自由键 + `_validate_declarations` 跨 bundle 校验 | `features/models.py` L266-271；`features/resolver.py` L527+ |
| `ability_modifier` 已在 resolution.py 导入 | `combat/resolution.py` L8 |
| 魔契师代表子职：fiend_patron，3 级授予 dark_ones_blessing | `templates/.../content/subclass_catalogs/srd_subclasses.json` L68-73 |
| 测试基建：`advance(runtime, sheet, level)`、SequenceRng、goblin 档案 | `tests/rulesets/test_dnd2024_sneak_attack.py` L26-32；`tests/rulesets/dnd2024_monk_common.py` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

`classes.warlock.features`（若 warlock 键不存在则新建，结构照 fighter）追加被动声明（无 `capabilities`）：

```json
{
  "id": "dark_ones_blessing",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p58:dark-ones-blessing",
  "summary": "When you reduce an enemy to 0 HP, or someone else reduces your enemy to 0 HP within 10 feet of you, you gain temporary hit points equal to your Charisma modifier plus your Warlock level (minimum 1).",
  "parameters": {
    "defeat_temp_hp_ability": "cha",
    "defeat_temp_hp_add_class_level": true,
    "defeat_share_range_ft": 10
  }
}
```

`labels` 追加 `"dark_ones_blessing": "Dark One's Blessing"`。参数语义（封闭枚举，引擎与校验按此消费，不出现特性名）：

- `defeat_temp_hp_ability`：临时生命值公式使用的属性缩写（魅力调整值来源）；
- `defeat_temp_hp_add_class_level`：是否把持有者的主职业等级加进公式；
- `defeat_share_range_ft`：路径 b 的分享半径（英尺）；缺省表示只有路径 a。

### 3.2 locale 文件（两个）

`locales/en` 与 `locales/zh-CN` 的 `class_feature_catalogs/srd_class_features.json` labels 同步追加；zh-CN 用规则书译名：黑暗赐福。

### 3.3 `src/rulesets/dnd2024/features/resolver.py`

`_validate_declarations` 追加 fail closed 校验（只针对声明了相关键的特性）：

- `defeat_temp_hp_ability` 必须是合法属性缩写（六属性集合的 str 成员）；
- `defeat_temp_hp_add_class_level` 必须是 `bool` 且 `not isinstance(v, int)` 之外显式判断（即拒绝 `1/0` 伪布尔与字符串）；
- `defeat_share_range_ft` 必须是 `int`（非 bool）且 `1 <= v <= 120`；
- 声明了以上任一键但缺失 `defeat_temp_hp_ability` 的载荷视为非法（fail closed，不允许"只声明半径不声明公式"）。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

新增一个触发辅助（建议签名 `_enemy_defeated_feature_events(instance, combat, attacker_id, target, damage) -> list[dict]`）：

1. 守卫：`target["kind"] != "enemy"`、`target["hp"] <= 0`（已倒地）、`target["hp"] - damage > 0`（未归零）任一成立即返回空表；
2. 枚举 `combat["initiative"]` 中 `player:` / `companion:` 前缀的 actor，`_actor_view` 取视图，`self.features.features_for(self._feature_character(actor))` 找声明了 `defeat_temp_hp_ability` 参数的特性定义；
3. 资格：`attacker_id == 该 actor id`，或 `defeat_share_range_ft` 存在且 `self._distance(combat, actor_id, target_id) <= 半径`（target_id 用被击败敌人）；
4. 数值：`amount = max(1, ability_modifier(int(actor["abilities"][ability])) + (class_state[1] if add_class_level else 0))`，`class_state` 缺失（同伴残表）则该特性不生效，跳过；
5. 返回事件：`{"type": "resource.changed", "resource": "temp_hp", "target_id": 持有者, "amount": amount, "feature_id": 定义 id}`（事件在 reducer 走既有 `_apply_temp_hp_change`，keep-max 自动生效）。

在 §2 列出的 **7 处落账点**接入：每处紧跟既有 `_last_hostile_defeated` 判定（或其位置语义）之后，用与该判定相同的最终伤害值调用辅助并 extend。①-④ 在 resolution.py；⑤⑥ 在 `reaction_spells.py`（decision 里取 `attacker_id`/`target_id`，`_actor_view` 重建 target 视图）；⑦ 在 `triggered_spells.py`（用 total = 主伤害 + rider）。

### 3.5 `src/rulesets/dnd2024/combat/reaction_spells.py` 与 `src/rulesets/dnd2024/combat/triggered_spells.py`

仅 §3.4 所述的两处/一处接入调用，不改其它逻辑。

### 3.6 测试

新文件 `tests/rulesets/test_dnd2024_dark_ones_blessing.py`（魔契师构造：builder 建卡 + `advance` 到 3；SequenceRng 控骰保证击杀与不击杀两种局面）。测试矩阵见 §4。

回归：`test_dnd2024_false_life.py`、`test_dnd2024_smite.py`、`test_dnd2024_uncanny_dodge.py`、`test_dnd2024_sneak_attack.py`、`test_dnd2024_combat.py`、`test_dnd2024_stoneskin.py`、`test_dnd2024_class_features.py`、`test_dnd2024_progression.py`。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`defeat_temp_hp_ability` 非法属性 / 缺公式 / `defeat_share_range_ft` 越界（0、121、字符串）/ 伪布尔 | `ClassFeatureError` |
| 2 | 所有权：advance 到 3 级魔契师 `has_feature("dark_ones_blessing")`；2 级 False | 边界 |
| 3 | 路径 a：魔契师亲手武器击杀敌人（伤量 >= 剩余 HP） | 敌人归零后魔契师 `temp_hp == max(1, 魅调 + 3)`；事件含 `feature_id` |
| 4 | 路径 a 反例：伤害未致死 | 无 temp_hp 事件 |
| 5 | 路径 b：同伴（另一玩家）击杀敌人，魔契师与该敌人距离 ≤ 10 | 魔契师获得 temp HP |
| 6 | 路径 b 反例：距离 > 10 | 无事件 |
| 7 | 路径 b 半径断言：正好 10 尺（theater 距离 10） | 触发（≤ 语义） |
| 8 | 公式保底：魔契师魅力调整值为负（构造极端魅力） | `temp_hp == 1` |
| 9 | 不叠加：已有 8 temp HP 再触发 6 | 仍为 8（keep-max） |
| 10 | 先吸收：魔契师 6 temp HP 后受 4 伤害 | temp_hp 归零、hp 不动（reducer 既有） |
| 11 | 不可重复触发：对已归零敌人再造成一次伤害 | 无新 temp_hp 事件（或校验拒绝该攻击，记录实际行为） |
| 12 | 多目标击杀：scorching_ray 双 beam 各杀一个敌人 | 两次触发但 temp HP 保持单次值（keep-max），敌人计数正确 |
| 13 | 法术击杀（saving_throw 路径，如 inflict_wounds） | 触发 |
| 14 | 非持有者击杀、无魔契师在场 | 无任何 temp_hp 事件（普通战斗回归不变） |
| 15 | 授予不救濒死：魔契师 hp 为 0 时获得 temp HP | temp_hp 记账但 hp 仍 0、无恢复意识语义（reducer 既有） |
| 16 | 事件顺序：归零伤害事件先于 temp_hp 事件，victory 最后 | reducer 应用顺序断言 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_dark_ones_blessing.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_false_life.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_stoneskin.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py -q
```

## 6. 禁止事项

1. 禁止在引擎或 resolver 出现 `dark_ones_blessing` 字符串——触发走参数，特性名只允许出现在数据、labels、事件 `feature_id` 与测试里。
2. 禁止复用 `_last_hostile_defeated` 做触发条件——它判定的是"最后一个敌人"，语义不同。
3. 禁止在 reducer 里实现触发——reducer 只做机械应用，特性逻辑属于结算期（事件构造点）。
4. 禁止给临时生命值引入新的玩家决策（保留/取新二选一）——keep-max 是 P4 锁定的确定性决策。
5. 禁止把触发接入 §2 清单之外的伤害路径（发现新路径即停止，见 §7）。
6. 禁止实现 fiend_patron 其余特性或"击晕"变体。
7. 禁止把 `defeat_share_range_ft` 缺省视为"全图共享"——缺省即只有路径 a。

## 7. 停止条件

1. S-P1 未合入（`features_for` 无子职合并，特性恒为幽灵）；
2. P6 未合入（resolution.py 基线漂移）；
3. 发现 §2 七处之外的伤害落账点（如 GM 直伤工具、环境伤害）无法绕过——报告并等待裁决；
4. `combat["initiative"]` 结构与本任务书锚点不符（无法枚举参战者）；
5. 事件顺序要求（伤害 → temp_hp → victory）与既有 pending/决策链路出现不可调和的冲突。

## 8. 执行报告模板

执行完成后在本文件追加"执行报告"节：变更文件清单、测试矩阵逐项结果（特别是矩阵 11 的实际行为）、7 处接入点的最终行号、偏差与遗留（特别是：同伴残表 `class_state` 为 None 的实际表现、keep-max 在多次触发下的断言方式）。
