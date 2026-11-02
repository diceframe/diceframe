# 切片 S-P1 任务书：Improved Critical（暴击阈值 + 子职特性所有权）

版本：v1（2026-10-30）
执行侧须知：本任务书是唯一需求来源。只参考本任务书列出的源文件；不要读取 `docs/internal/` 下其他计划文档。仓库根的 `AGENTS.md` 与 `docs/ENGINEERING_RULES.md` 自动适用。
依赖声明：本切片改动 `combat/resolution.py`，与 1B 进行中的 P6（misty_step）同文件。**必须在 P6 合入、工作区干净后开工**；若 P6 尚未合入，停止。本任务书锚点基于 `1009be03`（P5 已合入、P6 之前），执行侧须对受 P6 影响的文件自行重核行号。

---

## 1. 目标

为 D&D 2024 规则集打通 **勇士（Champion，战士代表子职）的精通重击 / 高效重击** 的完整闭环：子职特性声明 → 子职特性所有权 → 武器/徒手攻击检定 19–20（3 级）或 18–20（15 级）造成重击 → 伤害骰翻倍复用既有管线 → 测试。

本切片沉淀两项通用原语：

1. **子职特性所有权（subclass feature ownership）**：`features_for` 把子职目录 `feature_ids_by_level` 中等级 ≤ 角色等级的特性并入 granted 集合。这是 S-P 系列所有子职特性的共同前置，S-P4（黑暗赐福）、S-P5（龙族体魄）、S-P7（百折不挠）等直接依赖本切片落地后的合并结果。
2. **暴击阈值修改（crit threshold）**：特性参数 `crit_threshold`，攻击分支取已拥有特性的最小阈值，`critical = natural >= threshold`。后续消费者（传奇武器词条、其它扩展）复用同一参数，不另建管线。

**规则语义（SRD 5.2.1，2024 版，规则书原文已核实）**：

- 精通重击（3 级）："你使用武器或**徒手打击**进行的攻击检定在 d20 中掷出 19 或 20 时即可造成重击。"
- 高效重击（15 级）："你使用武器或徒手打击进行的攻击检定在 d20 中掷出 18-20 时即可造成重击。"
- 通用规则：攻击检定的 d20 掷出 20（天然 20）时无论调整值与目标 AC 如何都命中且为重击；掷出 1（天然 1）时无论调整值如何都失手。
- 重击通用规则：投掷两次该攻击的伤害骰并累加，再如常加上调整值；附带的其它伤害骰（如偷袭）也掷两次。

出处：

- `.rulebooks/dnd5r/玩家手册2024/角色职业/战士/勇士.htm` L20-21（精通重击）、L41-42（高效重击）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/攻击检定.htm` L35-37（天然 20/天然 1）；
- `.rulebooks/dnd5r/玩家手册2024/进行游戏/重击.htm` L8（伤害骰翻倍）。

### 1.1 必须锁定的规则边界

1. **阈值只作用于武器与徒手攻击检定**。原文限定"使用武器或徒手打击"，法术攻击（`spell_attack` 分支）不享受阈值扩展，`critical = roll == 20` 原样保留。
2. **天然 1 永远失手**，与阈值无关：`hit = natural != 1 and (critical or total >= target_ac)` 的组合判定保持不变。阈值 18 时天然 1 仍失手，不因"1 < 18"变成重击。
3. **阈值不叠加，取最小**：15 级勇士同时拥有两个特性，阈值是 18 而不是 17。引擎在已拥有特性的 `crit_threshold` 参数中取最小值，缺省 20。
4. **麻痹/昏迷升级路径不变**：`crit_on_hit_within_5` 仍是"命中且非重击才升级为重击"，失手不因阈值变宽而升级。
5. **重击伤害翻倍零新逻辑**：`attack_damage_node(critical=critical)` 既有机制对主骰、附加骰（hex/偷袭）统一翻倍，阈值变宽后自动生效。
6. **所有权按 (class_id, level) 推导，不读 `features.subclass_ref`**：本 bundle 子职目录校验保证每个职业恰好一个代表子职，升级链在 3 级强制选择该子职（`advancement.py` L98-103 校验非代表子职即报错）；而战斗边界 `_feature_character` 只携带 build/abilities/resources/equipment，根本看不到 `features.subclass_ref`。因此合并只依据主职业 + 等级 + 子职目录，直接建卡与走升级链的角色获得一致结果。
7. **声明等级与子职表授予等级必须一致**：特性声明的 `minimum_level` 与子职目录 `feature_ids_by_level` 的键不一致时 fail closed（目录加载期报错），不允许"声明 3 级、实际 7 级才给"的漂移。
8. **幽灵特性保持幽灵**：子职目录中的其它未声明 id（如 heroic_warrior）合入 granted 后因无 catalog 定义被跳过，行为不变；只有本切片显式声明的两个特性生效。

### 1.2 本切片的产品策略与裁剪

- 勇士 3 级的 **运动健将（Remarkable Athlete）** 不做：先攻/力量（运动）优势属"优劣势来源推广到豁免/检定"（P10 域），重击后移动属位移原语（P6 域）。本切片只交付暴击阈值半面。
- 勇士 7 级 **额外战斗风格**（专长域）、10 级 **英雄激励**（资源池）不做，保持幽灵。
- 暴击阈值不做前端 UI 配置：它是特性数据，不是玩家可选行为；玩家触发路径就是既有 Attack 按钮 / 徒手打击 / 借机攻击，零新增交互。
- `check.resolved` 事件只在阈值 ≠ 20 时追加 `crit_threshold` 字段，最小化对既有事件消费方的影响。
- 阶段二引入 48 子职后，"每职业唯一代表子职"不再成立，本切片的所有权推导必须改为 `subclass_ref` 感知——这是已声明的迁移债，记录在任务书内，不在本切片预防性实现。

## 2. 现有代码契约（已核实，直接引用不要重造）

| 事实 | 位置 |
|---|---|
| 攻击分支：adv/dis 归一 → bless → `total` → `critical = natural == 20` → `hit = natural != 1 and (critical or total >= target_ac)` | `combat/resolution.py` L250-262（基线 1009be03） |
| 麻痹/昏迷 crit_on_hit_within_5 升级（命中且非重击才升级） | `combat/resolution.py` L264-269 |
| 伤害构造：`attack_damage_node(critical=critical, extra_dice_formulas=...)` 统一翻倍 | `combat/resolution.py` L297-311；`action_adapter.py` |
| spell_attack 分支 `critical = roll == 20`（本切片不动） | `combat/resolution.py` L423 |
| 徒手打击与借机攻击都复用 `_attack_events`（同一消费点） | `combat/resolution.py` L227、L875、机会攻击复用处 |
| 引擎读特性的既有模式：`self.features.feature_value(self._feature_character(actor), "rage", "rage_damage", 0)` | `combat/resolution.py` L314-318 |
| `class_state`：单职业 (class_id, level)，1..20，否则 None | `features/resolver.py` L91-117 |
| `features_for`：granted = `_granted_feature_ids(class_id, level)`，**只读主职业表，子职特性目前不进任何机械判定** | `features/resolver.py` L123-146、L497-506 |
| 子职目录已加载并强校验：每职业恰一个子职、`feature_ids_by_level` 等级与 id 格式合法 | `progression/catalog.py` L116-150、L212-217 |
| 战士代表子职 champion 的特性等级表：3=[improved_critical, remarkable_athlete]，15=[superior_critical]，18=[survivor] | `templates/.../content/subclass_catalogs/srd_subclasses.json` L33-36 |
| 升级链强制选择代表子职，非法选择报错 | `progression/advancement.py` L82-103 |
| `_feature_character` 只带 build/abilities/class resources/equipment（看不到顶层 features） | `combat/view.py` L337-362 |
| 特性 parameters 任意标量键值（`_require_scalar`），无封闭键集 | `features/models.py` L266-271 |
| `_validate_declarations`：跨 bundle 校验的既有家（granted_spell_id、class 表存在性、_format 模板） | `features/resolver.py` L527-556 |
| 特性声明 JSON 结构：labels + classes.\<class\>.features[]（被动特性可省 capabilities，先例 sneak_attack） | `templates/.../content/class_feature_catalogs/srd_class_features.json`；locale 版本在 locales/en 与 locales/zh-CN 同名文件 |
| 测试基建：`advance(runtime, sheet, level)` 构造高等级角色；SequenceRng 控骰 | `tests/rulesets/test_dnd2024_sneak_attack.py` L26-32；`tests/rulesets/dnd2024_monk_common.py` |

## 3. 改动清单（白名单，逐文件）

只允许改动以下文件。除 §8 的执行报告豁免外，`git status` 出现白名单外任何变更即为失败。

### 3.1 `templates/rulesets/dnd2024_srd/content/class_feature_catalogs/srd_class_features.json`

`classes.fighter.features` 追加两条被动声明（无 `capabilities`）：

```json
{
  "id": "improved_critical",
  "minimum_level": 3,
  "source_ref": "srd-5.2.1:p49:improved-critical",
  "summary": "Your attack rolls with weapons or Unarmed Strikes score a critical hit on a d20 roll of 19 or 20.",
  "parameters": {"crit_threshold": 19}
}
```

```json
{
  "id": "superior_critical",
  "minimum_level": 15,
  "source_ref": "srd-5.2.1:p49:superior-critical",
  "summary": "Your attack rolls with weapons or Unarmed Strikes score a critical hit on a d20 roll of 18-20.",
  "parameters": {"crit_threshold": 18}
}
```

`labels` 追加：`"improved_critical": "Improved Critical"`、`"superior_critical": "Superior Critical"`。

### 3.2 locale 文件（两个）

`locales/en/class_feature_catalogs/srd_class_features.json` 与 `locales/zh-CN/class_feature_catalogs/srd_class_features.json` 的 labels 同步追加；zh-CN 用规则书译名：精通重击、高效重击。

### 3.3 `src/rulesets/dnd2024/features/resolver.py`

1. 新增私有 `_subclass_feature_ids(class_id, level)`：读 `self.progression.subclasses[class_id]["feature_ids_by_level"]`，等级键 `int(k)` 在 `1 <= k <= level` 内的 id 全并入集合；progression 为 None 或无该职业子职时返回空集。等级键格式由 progression 目录加载校验保证，不做二次防御。
2. `features_for` 的 granted 改为 `_granted_feature_ids(class_id, level) | _subclass_feature_ids(class_id, level)`；其余过滤逻辑（catalog 定义存在、minimum_level、option_of）原样。
3. 新增公共 API `min_feature_value(character, key, default)`：扫描 `features_for(character)` 返回的定义的 `parameters[key]`（`int` 且非 `bool`），取最小值；无命中返回 default。这是本切片的阈值消费口，也是后续其它"取最优参数"特性（不限暴击）的注册原语。
4. `_validate_declarations` 追加两条 fail closed 校验：
   - `parameters` 含 `crit_threshold` 时必须 `isinstance(v, int) and not isinstance(v, bool) and 2 <= v <= 19`，否则 `ClassFeatureError`；
   - 特性 id 出现在所属职业子职表 `feature_ids_by_level` 的等级 L 处时，`minimum_level` 必须等于 L，否则 `ClassFeatureError`（声明与授予漂移即拒绝）。

### 3.4 `src/rulesets/dnd2024/combat/resolution.py`

攻击分支（基线 L255-262，P6 合入后自行重核）：

1. 在 `critical = natural == 20` 处改为：
   ```python
   crit_threshold = self.features.min_feature_value(
       self._feature_character(actor), "crit_threshold", 20,
   )
   critical = natural >= crit_threshold
   ```
   `hit` 组合判定、crit_on_hit_within_5 升级、bless、adv/dis 全部不动。
2. `check_event` 在 `crit_threshold != 20` 时追加 `"crit_threshold": crit_threshold`（等于 20 时不加，避免对既有事件消费方的无谓扰动）。
3. spell_attack 分支（基线 L423）一行不动。

### 3.5 测试

新文件 `tests/rulesets/test_dnd2024_improved_critical.py`（战士构造模式照 `test_dnd2024_sneak_attack.py` 的 `advance` 法；徒手用 fighter 徒手打击）。测试矩阵见 §4。

允许的最小既有测试更新：`tests/rulesets/test_dnd2024_class_features.py` 若存在"枚举 owned 特性全集"的断言，按本切片预期追加 improved_critical / superior_critical；**不得**改动与子职无关断言的语义。

回归命令涉及：`test_dnd2024_class_features.py`、`test_dnd2024_progression.py`、`test_dnd2024_combat.py`、`test_dnd2024_sneak_attack.py`、`test_dnd2024_unarmed_strike.py`、`test_dnd2024_rage.py`、`test_dnd2024_smite.py`、`test_dnd2024_advancement_access.py`。

## 4. 测试矩阵

| # | 场景 | 断言 |
|---|---|---|
| 1 | 目录校验：`crit_threshold` 为 0、20、21、字符串、布尔 | `ClassFeatureError`（加载期 fail closed） |
| 2 | 目录校验：声明 minimum_level=7 但子职表在 3 级授予（用临时 fixture bundle） | `ClassFeatureError` |
| 3 | 所有权：advance 到 3 级的战士 | `has_feature(improved_critical)` True；`has_feature(superior_critical)` False |
| 4 | 所有权：builder 直接建 5 级战士（不走升级链，无 subclass_ref） | `has_feature(improved_critical)` True（证明按 class+level 推导） |
| 5 | 所有权：14 级战士无 superior_critical，15 级拥有 | True/False 边界 |
| 6 | 所有权：2 级战士无 improved_critical；3 级游荡者/法师无任何 champion 特性 | 职业与等级隔离 |
| 7 | 3 级战士武器攻击天然 19 | `critical=true`、必中、主伤害骰翻倍（SequenceRng 取固定面）、`check.resolved` 含 `crit_threshold: 19` |
| 8 | 15 级战士天然 18 与 19 | 均 critical，阈值 18（两特性并存取最小，不是 17） |
| 9 | 3 级战士天然 1 | 仍失手，无伤害事件（阈值不救天然 1） |
| 10 | 2 级战士天然 19 | 不重击，按 `total >= AC` 正常判命中 |
| 11 | 15 级战士借机攻击天然 19 | critical（同一消费点生效） |
| 12 | 3 级战士徒手打击天然 19 | critical（unarmed 路由） |
| 13 | 麻痹目标 5 尺内、天然 14 命中 | 仍升级为重击（升级路径未被阈值改动破坏）；天然 1 失手不升级 |
| 14 | 3 级战士天然 19 对高 AC 目标 | 必中（重击蕴含命中，与 AC 无关） |
| 15 | 法术攻击路径不受影响 | 结构断言：spell_attack 分支仍 `roll == 20`；以既有 spells 测试回归为准 |

## 5. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_improved_critical.py -q
.venv\Scripts\python.exe -m pytest tests/rulesets/test_dnd2024_class_features.py tests/rulesets/test_dnd2024_progression.py tests/rulesets/test_dnd2024_combat.py tests/rulesets/test_dnd2024_sneak_attack.py tests/rulesets/test_dnd2024_unarmed_strike.py tests/rulesets/test_dnd2024_rage.py tests/rulesets/test_dnd2024_smite.py tests/rulesets/test_dnd2024_advancement_access.py -q
```

（`test_dnd2024_character_builder.py` 上游既有失败不计。）

## 6. 禁止事项

1. 禁止把 spell_attack 的 crit 判定改为读阈值——2024 原文限定武器与徒手。
2. 禁止在引擎或 resolver 硬编码 `improved_critical` / `superior_critical` 字符串分支——必须走 `crit_threshold` 参数 + `min_feature_value`。
3. 禁止把子职特性 id 塞进主职业 `features_by_level` 来绕过合并——那是内容漂移，不是所有权原语。
4. 禁止让 `crit_threshold` 进入优劣势管线或 condition 元数据——它既不是 advantage 也不是加值。
5. 禁止修改 `_feature_character` 去携带 `features.subclass_ref`——本切片设计决定：所有权按 (class_id, level) 推导（§1.1 第 6 条）。
6. 禁止实现运动健将、额外战斗风格、英雄激励、幸存者（Survivor 属 S-P7）。
7. 禁止在天然 1 时产生任何伤害或重击事件。

## 7. 停止条件

1. P6（misty_step）尚未合入，`combat/resolution.py` 基线与本任务书锚点无法对齐；
2. 子职目录结构、升级链强制选择逻辑与本任务书 §2 描述冲突（例如已支持多子职）；
3. `crit_on_hit_within_5` 升级路径与阈值判定组合出现本任务书未覆盖的语义冲突；
4. `check.resolved` 新增字段被既有投影/前端契约显式拒绝（白名单外需要改动才能兼容）；
5. `features_for` 合并导致既有职业（monk/paladin/rogue/barbarian）的幽灵特性意外获得定义并生效——报告并等待裁决，不得自行扩大范围。

## 8. 执行报告模板

执行完成后在本文件追加"执行报告"节：变更文件清单、测试矩阵逐项结果、偏差与遗留（特别是 `min_feature_value` API 的最终签名、check 事件字段、既有测试更新范围）、迁移债确认（48 子职后所有权须改 subclass_ref 感知）。
