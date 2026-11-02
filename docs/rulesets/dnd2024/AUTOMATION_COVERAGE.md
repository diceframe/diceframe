# 5E 2024 SRD 自动化覆盖清单

`automation_level` 表示 DiceFrame 当前对实体的实际处理能力，而不是长期路线图：

- `deterministic`：当前运行时可从 canonical 选择确定性验证或派生。
- `guided`：结构化保存并要求玩家完成选择；尚未自动执行其全部游戏内效果。
- `reference`：只提供合法引用或展示，游戏内效果需要人工裁量。

## M6 角色、成长、战斗与新手战役快照

| 实体目录 | 总数 | deterministic | guided | reference |
| --- | ---: | ---: | ---: | ---: |
| abilities | 6 | 6 | 0 | 0 |
| backgrounds | 4 | 4 | 0 | 0 |
| classes | 12 | 0 | 12 | 0 |
| equipment_packages | 33 | 33 | 0 | 0 |
| feats | 6 | 1 | 5 | 0 |
| items | 50 | 33 | 0 | 17 |
| languages | 10 | 10 | 0 | 0 |
| skills | 18 | 18 | 0 | 0 |
| species | 9 | 0 | 9 | 0 |
| tools | 32 | 32 | 0 | 0 |
| quick character presets | 6 | 6 | 0 | 0 |
| class progression rows | 240 | 240 | 0 | 0 |
| subclass catalog entries | 12 | 0 | 12 | 0 |
| spell metadata rows | 339 | 339 | 0 | 0 |
| advancement feats | 13 | 2 | 11 | 0 |
| rest policies | 2 | 2 | 0 | 0 |
| class feature catalog (Monk, Paladin) | 2 | 2 | 0 | 0 |
| combat weapon profiles | 15 | 15 | 0 | 0 |
| deterministic spell effects | 31 | 31 | 0 | 0 |
| original encounter presets | 3 | 3 | 0 | 0 |
| original starter adventures | 1 | 1 | 0 | 0 |

## 当前确定性边界

已确定性完成：

- 一级角色草稿合法性。
- 标准数组、27 点购点和受约束的 4d6 取高三点数录入。
- 背景 +2/+1 或 +1/+1/+1。
- 职业、物种、背景、技能、工具、语言、起始装备和体型选择。
- Magic Initiate 的法术列表、施法属性、两个戏法和一个 1 环法术选择；所选法术进入角色卡与战斗，按专长所选施法属性计算法术攻击与 DC，1 环法术每长休可免费施放一次（也可用已有法术位施放），无职业施法的角色也能维持专注。
- Skilled 的三项无重复技能/工具熟练选择。
- HP、AC、先攻、速度、熟练加值、豁免、技能修正和被动察觉。
- canonical 角色与旧 UI/Bot 投影；保存边界会重新派生并拒绝篡改值。
- 全部 12 职业的 1–20 级成长表、熟练加值、HP、生命骰、资源上限和法术位。
- SRD 子职、通用专长和史诗恩惠的等级/前置条件选择；单职业可合法成长到 20 级。
- 339 条 SRD 法术机械索引、职业/环位/数量校验、法师法术书与准备法术包含关系。
- 短休/长休的 HP、生命骰、职业资源、法术位、专注、力竭与能力值降低恢复。
- 服务端升级预览/应用与前端升级前后差异界面；高等级提交从一级选择和升级历史重放。
- Intent/EventBatch 的版本校验、原子应用、幂等重放和同 ID 改包拒绝。
- 先攻、轮次、动作/附赠动作/反应/移动、距离、近战/远程攻击、优势/劣势和暴击。
- 15 种武器档案、徒手打击（无装备武器的 player-like actor 也可以攻击）与 31 种法术效果、法术位、戏法成长、升环、专注和常见条件；护盾术在被攻击命中时作为防守方反应决策弹出（敌人回合也会出现），施放后以同一攻击总值对抗提高后的 AC 重新判定。
- Monk 1–2 级的职业特性：武艺（仅在该角色未穿甲、未持盾、且只持 Monk Weapon 时生效；力量/敏捷取高者 + 由成长表派生的 Martial Arts Die，Monk Weapon 同样适用，且只在更优时替换武器伤害骰）、附赠徒手打击（随武艺 eligibility 出现/消失）、专注点、疾风连击、坚守防御与疾风步（含专注增强版本，均不受武艺 eligibility 影响）；两次攻击与资源/附赠动作扣减在同一个 EventBatch 内原子结算，AI 托管与 Companion 沿用同一条权威链。
- Paladin 2 级的 Paladin’s Smite：授予常备 Divine Smite、每长休一次免费施放；近战命中触发的至圣斩使用附赠动作和法术位/免费次数，升环、暴击及邪魔/不死额外骰由同一权威结算链处理。
- 治疗、0 HP、死亡豁免、稳定、死亡、机会攻击待决策和胜负结束。
- canonical HP/资源写入、存档恢复和 LegacyProjection；高级战斗不经过自由文本状态写入。
- 服务端可用动作 API、预设遭遇和按 capability 动态加载的高级战斗界面。
- Session 0 修订、逐成员确认和锁定；改约会清空旧确认，不能沿用过期同意。
- 任务、线索、事实、重要物品和关系的“待确认提案 → GM 确认/拒绝 → 权威记录”，包含 GM 可见性过滤。
- 原创三章新手冒险《灰沼失灯记》：结构化选择、战斗门槛、可关闭教学提示、结局记录、章节摘要与长期记忆投影。
- Session 0、战役、教学与战斗共享同一 state version 和 EventBatch ledger；P2P 只放行玩家侧必要字段。

仍为 guided/reference：

- 职业特性、物种特性和专长在战斗/探索中的完整执行（当前已支持下文列出的 Monk、Paladin 与 1C 决策窗口/主动声明；其余未实现的职业、子职特性与专长仍为 guided）。
- 未列入 31 种确定性效果目录的法术（如真知灼见、橡棍术、抗性术等需要额外机制的戏法），以及职业、物种、专长的完整游戏内效果。
- 完整怪物图鉴、网格 VTT、擒抱/推撞等尚未建模的战斗动作。
- 复活效果；普通治疗明确不能令死亡角色复活。
- 任意自由文本冒险的自动规则解释，以及《灰沼失灯记》之外的完整长篇战役内容。

## 1C 战斗决策、主动声明与兼容

- 决策窗口：服务端在攻击检定或伤害结算后产生 pending `check_window` / `damage_window`。战斗面板显示窗口种类、已掷骰面与检定结果或待结算伤害、剩余资源、能力骰面，并按服务端选项提供 **使用能力 / 放弃**。提交只发送 `decision.resolve`，客户端不掷骰、不扣资源；窗口仍只对 `assigned_to` 的拥有者显示。当前接线 Lucky 劣势、Cutting Words 攻击/伤害与 Dark One's Own Luck；Shield、Uncanny Dodge、Counterspell 的既有反应 UI 保持不变。
- 主动声明：攻击、法术攻击与死亡豁免可声明 Lucky 优势；`cast_spell` 可声明 Overchannel 伤害最大化；普通 Hide 走 `action` 的躲藏路径，Cunning Action 的附赠动作躲藏仍是独立 capability。声明入口只在服务端投影显示当前 actor 可用时出现，伪造或过期意图仍由服务端拒绝。
- 资源投影：actor 的 `class_resources` 同时包含职业资源与已拥有的起源专长资源池（例如幸运点、诗人激励、黑暗强运）。界面只消费该公开投影，不读取隐藏角色数据。

## 旧数据与内容版本兼容

- 旧角色 Armor Training：没有 `derived.armor_training` 的存档会在第一次 `combat.start` 前按唯一派生公式重算 AC、护甲训练、速度与施法/攻击/豁免门控；该修复幂等，重复加载或开战不会追加 revision 或操作日志，也不改写无关用户数据。
- 历史战斗事件：旧事件中的 `bless_bonus` 只在公开投影边界归一化为 `check_bonus` / `check_bonus_rolls`，不改写持久化 ledger；新事件继续使用 `check_bonus` 契约。
- 内容版本保持 `srd-5.2.1+r5`：本次修复不新增持久化字段，也不改变 progression/rest/equipment 的一致性要求，因此不做 manifest 版本 bump。若后续内容版本改变角色派生契约，必须同时提供旧角色的升级或重算路径。

## M7 体验与性能验证

- 高级战役、教学和权威战斗共用按 capability 动态加载的三页签工作区；任一时刻只挂载当前页，避免重复轮询和固定高度页面被多块内容挤压。
- 320、640（等价 200% 回流）、768 和 1440 CSS 像素宽度均通过真实浏览器布局检查；关键交互目标不小于 44px，Join 高级快速构筑在 320px 无横向溢出且底部操作可达。
- 页签支持方向键、Home/End 和语义化 `tablist`/`tabpanel`；确认卡可获得焦点并在取消后归还；HP 使用 `progressbar` 语义，当前先攻和状态提示可被辅助技术识别。
- 亮色模式关键正文对比度不低于 4.5:1；`prefers-reduced-motion` 下过渡和动画归零；页面隐藏时战役与战斗轮询暂停。
- `scripts/rulesets/check_frontend_chunks.py` 对高级可选模块设置原始体积门禁：角色构筑器 40 KiB、战役 24 KiB、战斗 24 KiB。M7 构建结果分别约 30.33、15.56、13.77 KiB，均保持为延迟加载分块。

M5/M6/M7 黄金场景、HTTP 重放、重启恢复和浏览器体验验收已经通过；`core:dnd2024` 现已如实声明 `authoritative_intents=true`、`deterministic_combat=true`、`session_zero=true` 与 `tutorial_coach=true`。未列出的内容仍保持 guided/reference，不交给 LLM 猜测。
