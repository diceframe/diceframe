# 切片 5 任务书：Sneak Attack（谓词求值 + 条件伤害）

版本：v1（2026-10-29）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。

---

## 1. 目标

为 D&D 2024 规则集实现 **Sneak Attack（偷袭，游荡者 1 级）** 的完整闭环：catalog 被动特性声明 → 命中谓词求值 → 条件伤害骰合流 → 每回合使用记账 → Shield / Uncanny Dodge / Divine Smite 决策链正确结算 → 测试。

本切片同时沉淀两项通用原语：

1. **多个附加伤害骰可按确定顺序合并到同一伤害 AST，并在重击时全部翻倍**；
2. **按攻击者与实际回合记录特性使用**，而不是误用“当前行动者”的动作经济。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 每个回合一次，当你的攻击检定命中一个生物时，可以造成额外伤害；
- 攻击必须使用自身具有 **Finesse（灵巧）** 或 **Ranged（远程）** 属性的武器；
- 通常需要本次攻击检定具有优势；
- 没有优势时，若**你的至少一名盟友在目标 5 尺内、该盟友未失能、且你的本次攻击检定没有劣势**，仍可造成额外伤害；
- 额外伤害类型与武器伤害类型一致；
- 骰数随游荡者等级增长：1/2 级 1d6，3/4 级 2d6，直至 19/20 级 10d6；
- 通用重击规则：重击时额外伤害骰也翻倍。

出处：

- `.rulebooks/dnd5r/玩家手册2024/角色职业/游荡者/游荡者.htm` L61-81（等级表）、L90-91（偷袭正文）；
- `.rulebooks/dnd5r/玩家手册2024/术语汇编/状态.htm` L75-80（失能）、L87-103（麻痹/石化蕴含失能）、L127-139（震慑/昏迷蕴含失能）。

### 1.1 必须锁定的规则边界

1. **盟友是攻击者的盟友，不是目标的盟友**；判断的是盟友到目标的距离，不是盟友到攻击者的距离。攻击者本人不算自己的盟友。
2. 读取归一后的 `check_mode["mode"]`：优势与劣势同时存在时模式为 `""`，本次攻击视为既无优势也无劣势，因此仍可能走盟友路线。
3. 投掷非灵巧近战武器不会让武器变成远程武器。不得用 `ranged_use` 代替 `weapon["ranged"]`。
4. 偷袭骰并入本次武器伤害，不生成独立伤害事件；因此沿用武器伤害类型，也会被 Uncanny Dodge 一并减半。
5. 只有最终仍结算这次命中伤害时才消耗每回合一次的使用。Shield 令原命中最终落空时不得消耗。
6. “每回合”不是“每轮”：自己的回合已用后，在敌人回合的借机攻击仍可再用一次。

### 1.2 本切片的产品策略与裁剪

- 满足条件时，**第一次合法命中自动附加偷袭**，不新增“是否使用偷袭”的决策卡；人类玩家仍点击既有 Attack 按钮或既有借机攻击 `decision.resolve`。
- 自动策略意味着本切片不为玩家提供“保留到本回合后一次攻击”的选择 UI；不得擅自新增前端流程。
- 不实现 Cunning Strike、Steady Aim、Evasion 或其他游荡者特性。
- 不实现 2D 地图；5 尺距离复用现有 theater 一维距离。
- 不重构 Hex、Rage、Divine Smite 的既有伤害语义，只保证它们与偷袭组合时不丢骰、不重复结算。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| 游荡者 progression 已在 1 级授予 `sneak_attack`，`sneak_attack_dice` track 已含完整 1–20 级骰数表；**两处置均勿改** | `templates/rulesets/dnd2024_srd/content/progression_catalogs/srd_classes.json` rogue `features_by_level` / `tracks` |
| rogue catalog 当前只有 `uncanny_dodge`；被动特性可省略 `capabilities` | `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json` L208-229 |
| `feature_value(character, feature_id, key)` 会读取 `<key>_track`；`<key>_format` 的唯一占位符是 **`{die}`** | `src/rulesets/dnd2024/features/resolver.py` L184-207、L476-495 |
| `has_feature` 通过 canonical build/progression 判断真实所有权 | `src/rulesets/dnd2024/features/resolver.py` L148-154 |
| 攻击命中分支依次构造附加骰、伤害 AST、Rage 固定加值、骰迹与 `damage_event`，随后进入 Shield/Uncanny Dodge/Divine Smite pending 链 | `src/rulesets/dnd2024/combat/resolution.py` L189-230 |
| `attack_damage_node` 当前只接受单个 `extra_dice_formula`，并在属性修正前对所有骰节点执行重击翻倍 | `src/rulesets/dnd2024/combat/action_adapter.py` L141-160 |
| 当前附加骰仅有 Hex；其调用方和现有 AST 测试是本切片需要同步更新的全部已知调用点 | `src/rulesets/dnd2024/combat/resolution.py` L192-202；`tests/rulesets/test_dnd2024_action_adapter.py` L57-72 |
| 归一优势/劣势来自 `attack_mode`；两种来源同时存在时 `mode_from_sources` 返回 `""` | `src/rulesets/dnd2024/combat/check_modes.py` L18-44 |
| 真实远程武器与 `ranged_use` 是两个不同概念；投掷手斧只有后者可能为真 | `src/rulesets/dnd2024/combat/resolution.py` L113-120；`srd_combat_core.json` weapons `handaxe` / `shortbow` / `shortsword` |
| `_all_targets` 提供所有参战者的 side/hp/position；`_actor_view` 可取 conditions；`_distance` 是一维绝对距离 | `src/rulesets/dnd2024/combat/view.py` L413-436；`engine.py` `_distance` |
| 借机攻击复用 `_attack_events`，攻击者可能不是当前 `economy.actor_id` | `src/rulesets/dnd2024/combat/resolution.py` L718-729 |
| `turn.advanced` 同时给出 round、turn_index 和新行动者 economy；round + turn_index 可标识一个具体回合 | `src/rulesets/dnd2024/combat/resolution.py` L595-638；`reducer.py` L277-285 |
| Shield/Uncanny Dodge 决策持有 `damage_event`；Shield 改判 miss 时不提交伤害，decline/feature 最终提交伤害 | `src/rulesets/dnd2024/combat/reaction_spells.py` L166-248 |
| Divine Smite 决策也持有同一份基础 `damage_event`；最终由 `_finish_hit_spell` 提交 | `src/rulesets/dnd2024/combat/triggered_spells.py` L142-158 |
| reducer 的 `dnd2024.economy.granted` 明确只允许写给当前 economy actor，**不可复用于他人回合的偷袭记账** | `src/rulesets/dnd2024/combat/reducer.py` L136-146 |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

在 rogue 的 `features` 中、Uncanny Dodge 前新增被动声明：

```json
{
  "id": "sneak_attack",
  "minimum_level": 1,
  "source_ref": "srd-5.2.1:p61:sneak-attack",
  "summary": "Once per turn, when you hit with an attack using a Finesse or Ranged weapon, deal extra damage if you have Advantage, or if an ally who is not Incapacitated is within 5 feet of the target and you do not have Disadvantage. The damage type matches the weapon.",
  "parameters": {
    "damage_dice_track": "sneak_attack_dice",
    "damage_dice_format": "{die}d6"
  }
}
```

- 不添加 `resource_id`、`capabilities`、`once_per_turn` 或新 action kind；偷袭不是主动 capability。
- 顶层 `labels` 增加 `"sneak_attack": "Sneak Attack"`。

### 3.2 本地化（两个文件）

- `templates/rulesets/dnd2024_srd/locales/en/class_feature_catalogs/srd_class_features.json`：`sneak_attack: "Sneak Attack"`；
- `templates/rulesets/dnd2024_srd/locales/zh-CN/class_feature_catalogs/srd_class_features.json`：`sneak_attack: "偷袭"`。

### 3.3 `src/rulesets/dnd2024/combat/action_adapter.py`

把 `attack_damage_node` 的单个 `extra_dice_formula` 改为**多个附加骰公式**（建议参数名 `extra_dice_formulas: list[str] | tuple[str, ...] | None`）：

1. 主武器骰节点在前；
2. 按调用方给定顺序依次合并全部附加骰；
3. 合并后再执行既有 `double_dice_counts`，保证重击翻倍所有骰；
4. 属性修正仍在重击处理后追加，不得翻倍；
5. 骰节点顺序就是 RNG 消耗与 `rolls` 输出顺序。

直接更新全部调用点与既有测试，不保留旧参数兼容层。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

#### A. 偷袭谓词求值

新增内部辅助逻辑，输入至少包含 `instance / combat / actor / target / weapon / check_mode`，返回本命中可用的偷袭骰式或 `None`。按以下顺序判断：

1. 攻击者真实拥有 `sneak_attack`；否则无效果；
2. 当前攻击者在这个具体回合尚未记录使用 `sneak_attack`；
3. `weapon.get("finesse") or weapon.get("ranged")` 为真；不得使用 `ranged_use`；
4. 满足以下任一条件：
   - `check_mode["mode"] == "advantage"`；
   - `check_mode["mode"] != "disadvantage"`，且存在合格盟友；
5. 用 `feature_value(..., "sneak_attack", "damage_dice", "")` 读取骰式。已拥有特性却无法解析出合法骰式时 fail closed 抛 `CombatIntentError`，不得静默变成无效果或猜 1d6。

合格盟友必须同时满足：

- 不是攻击者本人；
- 与攻击者 `side` 相同；
- 存活且未处于 defeated/dead；
- 与**目标**的既有 `_distance(...) <= 5`；
- conditions 不含 `incapacitated / paralyzed / petrified / stunned / unconscious`。后四项按规则原文蕴含失能处理，不得只检查字面 `incapacitated`。

#### B. 伤害骰合流

在现有 hit 分支中：

- 建立有序附加骰列表：Hex 保持在前，满足条件时偷袭追加在后；
- 将完整列表交给 §3.3 的 `attack_damage_node`；
- 保持 Rage 固定加值位于重击翻倍之后；
- 保持单个 `resource.changed` 伤害事件、武器 `damage_type`、既有 crit/rolls/concentration/victory 语义；
- 初始 hit 时尚未最终提交伤害，**不得立即追加特性使用事件**。

#### C. 每回合使用的延迟提交载荷

当本次伤害含偷袭时，在服务器生成的 `damage_event` 上携带内部使用元数据（建议键 `feature_use`），内容至少为：

```json
{
  "actor_id": "实际攻击者 actor_id",
  "feature_id": "sneak_attack",
  "round": "当前 combat.round",
  "turn_index": "当前 combat.turn_index"
}
```

该元数据由服务端构造并随 pending decision 的深拷贝传递；客户端不能通过 intent 提供或修改它。

新增统一的“提交命中伤害”辅助方法，供 §3.4、§3.5、§3.6 的所有最终伤害提交点调用：

1. 从 damage event 取出并移除内部 `feature_use` 元数据，保持实际 `resource.changed` 事件形状干净；
2. 先提交 `resource.changed`；
3. 若有元数据，紧随其后生成 `dnd2024.feature.used`（字段同上述元数据）；
4. 没有元数据时行为与现状完全相同。

**不得**在 Shield 改判 miss 的分支调用该提交辅助方法。Uncanny Dodge 减半、Shield decline、Divine Smite decline/cast 都仍属于最终命中伤害提交，必须保留使用记录。

#### D. 回合使用查询

使用 `combat["feature_usage"]` 查询“该 actor 是否在 round + turn_index 对应的回合已用该 feature”。不得读取当前行动者 economy，也不得用 `once_per_turn` capability 字段代替该账本。

### 3.5 `src/rulesets/dnd2024/combat/reaction_spells.py`

仅修改最终提交基础伤害的路径：

- Uncanny Dodge 减伤后、无后续 decision 时，经统一提交辅助方法输出 damage + feature use；
- Shield decline 或 Shield cast 后仍命中、无后续 decision 时同样处理；
- Shield 改判 miss 直接返回，不提交伤害也不记录偷袭；
- 转发 `next_decision` 时继续深拷贝原载荷，不提前记录使用；
- 不改 AC、反应成本、伤害倍率、选项或 AI 选择语义。

### 3.6 `src/rulesets/dnd2024/combat/triggered_spells.py`

`_finish_hit_spell` 的基础伤害改经统一提交辅助方法输出：

- Divine Smite decline/cast 均保留基础伤害中的偷袭与使用记录；
- 额外法术伤害仍按现有独立事件追加；
- 不改法术成本、重击、专注或胜利结算。

### 3.7 `src/rulesets/dnd2024/combat/reducer.py`

新增 `dnd2024.feature.used` 事件及 `combat["feature_usage"]` 状态：

- 新战斗初始化为空表；读取既有战斗状态时使用空表缺省，不要求存档迁移；
- 推荐结构：每个 actor 只保留最近一个回合的记录：

```json
{
  "actor_id": {
    "round": 2,
    "turn_index": 1,
    "feature_ids": ["sneak_attack"]
  }
}
```

- 当事件 round/turn_index 与该 actor 旧记录不同，替换为新回合记录；
- 同一 actor、同一回合、同一 feature 的重复提交抛 `EventBatchError`，不得静默覆盖；
- 不同 actor 互不影响，不同 round/turn_index 互不影响；
- `turn.advanced` 不必清空全表，查询必须同时匹配 round + turn_index。

### 3.8 测试

新增 `tests/rulesets/test_dnd2024_sneak_attack.py`，修改：

- `tests/rulesets/test_dnd2024_class_features.py`；
- `tests/rulesets/test_dnd2024_action_adapter.py`。

可复用 `dnd2024_monk_common` 的 `SequenceRng / advance / monk_instance / goblin / with_equipment`，以及 `test_dnd2024_uncanny_dodge.py` 的 rogue quick preset 模式。测试 fixture 可增加第二名玩家或 companion 来构造盟友与反应链；不得为测试方便修改生产校验规则。

#### A. catalog / AST

1. 1 级 rogue 拥有被动 `sneak_attack`，没有新增主动 capability；1/3/5/19 级 `damage_dice` 分别为 `1d6 / 2d6 / 3d6 / 10d6`；中英文标签正确。
2. 更新既有 rogue 断言：4 级 rogue 现在仍拥有 Sneak Attack；5 级同时拥有 Sneak Attack 与 Uncanny Dodge，不得继续断言特性为空或只有 Uncanny Dodge。
3. AST 单附加骰旧行为不回归；多个附加骰按“主骰 → Hex → 偷袭”消耗 RNG，普通与重击均正确，属性修正不翻倍。

#### B. 武器、优势与盟友谓词

4. 优势 + Finesse 武器命中：附加 1d6，伤害类型同武器，事件顺序为伤害后 `feature.used`，账本归属实际攻击者。
5. 优势 + 真正 Ranged 武器命中可触发；优势 + 非灵巧近战武器不可触发。
6. 非灵巧 Thrown 武器（handaxe）在投掷距离命中，即使有优势也不可触发，锁定不得使用 `ranged_use`。
7. 未命中不掷偷袭骰、不记录使用；同回合后续合法命中仍可使用。
8. 无优势、无劣势、合格盟友距目标 5 尺内时可触发；攻击者本人、超过 5 尺的盟友、目标的盟友均不能冒充合格盟友。
9. 盟友处于 `incapacitated / paralyzed / petrified / stunned / unconscious` 任一状态时不可走盟友路线（可参数化）。
10. 最终为 disadvantage 时，即使有合格盟友也不可触发；优势与劣势相抵为 `""` 且有合格盟友时可触发。

#### C. 重击、组合伤害与每回合账本

11. 重击翻倍武器骰与偷袭骰；Hex + Sneak Attack 同时存在时三组骰都保留且都翻倍，不覆盖 Hex。
12. 同一回合第二次合格命中不再附加偷袭，也不再发 `feature.used`；进入该攻击者的新回合后恢复。
13. 自己回合已用偷袭后，在敌人回合通过**真实 opportunity attack decision 链**命中，可再次附加；使用记录仍属于 rogue，而非当前 enemy economy actor。

#### D. pending 决策链

14. **Shield 改判 miss**：初始命中已滚出含偷袭的暂存伤害，但 Shield 最终令攻击落空后，无 HP 伤害、无 `feature.used`、账本仍未使用；随后合法命中仍可使用。
15. **Uncanny Dodge**：含偷袭的总伤害被整体减半（向下取整），不是只减半武器骰；最终仍记录一次偷袭使用。
16. **Divine Smite pending**：基础伤害在 decision 中暂存后，decline 与 cast 路径均不丢偷袭、不重复 `feature.used`。

测试构造说明：若 Shield/Uncanny Dodge 的同侧目标场景被正常 attack validation 拒绝，可参照 Rage 测试的既有例外，直接调用 `_attack_events` 生成服务器事件，再经 `apply_batch` 验证；该例外只用于决策链测试，普通攻击行为仍必须走 `resolve_intent → apply_batch`。

## 4. 前置步骤（动代码之前）

```powershell
# 基线必须全绿，否则停下报告。已知豁免仅适用于全量 rulesets 中的
# test_dnd2024_character_builder.py 11 个上游既有失败。
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_action_adapter.py tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_origin_feat_spells.py -q
```

## 5. 验收标准

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_action_adapter.py -q
.venv\Scripts\python.exe -m pytest tests -q -k "sneak_attack"
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_uncanny_dodge.py tests/rulesets/test_dnd2024_origin_feat_spells.py tests/rulesets/test_dnd2024_combat.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets -q    # 除已知 11 个上游豁免外全绿
git status   # 变更仅限白名单与执行报告
```

验收时执行报告必须明确给出：测试数量、完整结果、11 个豁免是否仍全部属于 `test_dnd2024_character_builder.py`，以及白名单逐文件对照。

## 6. 禁止事项

- 不改 `progression_catalog` / `rest_catalog`（授予等级与骰数 track 均已存在）；
- 不改 `features/models.py`、`features/resolver.py`、`check_modes.py`、`view.py`、`engine.py`、`validation.py`；
- 不新增 ACTION_KIND、主动 capability、前端按钮或偷袭 pending decision；
- 不改 `frontend-v2/`；人类入口是既有 Attack / opportunity attack，伤害由服务端自动附加；
- 不把使用标记写入 economy，不按“每轮一次”实现，不让客户端提交优势、盟友、武器资格或使用标记；
- 不用 `ranged_use` 判断武器资格，不把攻击者本人或目标的盟友当作攻击者盟友；
- 不让偷袭覆盖 Hex，不新建第二套骰子求值/重击逻辑，不把偷袭拆成不同伤害类型的独立事件；
- 不在初始 hit 时提前消耗；Shield 最终 miss 不得留下使用记录；
- 不为旧参数或旧事件增加兼容回退，不新增第三方依赖，不修改本任务书之外的文档。

## 7. 停止条件（触发即停，写入执行报告，不要猜）

1. rogue progression 未在 1 级授予 `sneak_attack`，或 `sneak_attack_dice` track 与 §1 等级曲线不符；
2. `feature_value` 不能按 §3.1 声明解析骰式，或必须修改 resolver 公有契约；
3. `attack_damage_node` 还存在白名单外调用方，无法在不扩大改动面的情况下完成多附加骰签名更新；
4. 发现新的最终提交基础 `damage_event` 路径，导致无法保证“伤害提交才消耗、Shield miss 不消耗”；
5. 现有 pending 深拷贝不能保留使用元数据，或必须信任客户端输入才能恢复该元数据；
6. 必须修改 `view.py / engine.py / validation.py / frontend-v2/` 或新增地图能力才能实现本规格；
7. 基线测试非绿（全量 rulesets 的已知 11 个上游失败除外）；
8. 需要改变 Shield、Uncanny Dodge、Divine Smite、Hex 或 Rage 的既有规则语义才能组合偷袭。

## 8. 执行报告（完成后必须填写，仓库根 `EXECUTION_REPORT_SLICE5.md`）

```markdown
# 切片 5 执行报告
- 任务书版本：v1
- 分支：feat/sneak-attack

## changed-file list（与白名单逐项对照）
...

## 规则边界自检
- 武器/优势/盟友谓词：...
- 重击与 Hex 组合：...
- 每回合与他人回合借机攻击：...
- Shield / Uncanny Dodge / Divine Smite：...

## 测试输出（完整粘贴）
...

## 停止条件触发情况（无则写“无”）
...

## 规格偏离（无则写“无”，逐项说明原因与替代方案）
...

## 未完成项
...
```

注意：`EXECUTION_REPORT_SLICE5.md` 是白名单唯一豁免项（临时产物，审核后删除）。

## 9. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-10-29 | 初版；锁定多附加骰 AST、归一优势谓词、攻击者盟友 5 尺路线、按 actor + round/turn_index 记账，以及所有命中后决策链的延迟消耗语义；前端零改动 |
