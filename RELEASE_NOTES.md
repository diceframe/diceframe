# DiceFrame v2.5.7-beta.1

> 预发布版本：修复专业战斗工具的滚动、剧情遭遇绑定与检定重复惩罚。

## 中文

### 修复内容

- **专业战斗工具在弹窗内无法滚动**：滚动规则原本指向弹窗的直接子元素，但弹窗里多了一层宿主容器，弹窗又是 `overflow: hidden`，低高度窗口下动作卡与确认按钮被裁掉且无法滚动。现在由当前工具面板自己承担滚动，`1280×620`、`1024×600`、`390×700` 三档都能滚到动作区与结算日志；移动端的横向卡片带与键盘 PageDown 保持可用。
- **剧情遭遇被静默替换成训练预设**：活动冒险包内没有绑定合法遭遇时，战斗工具会退回通用目录（地精巡逻、骷髅与狼）并默认选中第一条。现在改为明确的「当前剧情尚未配置专业战斗遭遇」，不提供开战入口，叙事给出的预设也不再写入请求；GM 想打自由遭遇必须显式选择「脱离冒险，准备自由遭遇」。战斗开始后模式与冒险绑定会写进权威状态，角色、先攻与可用动作全部来自同一个 encounter。
- **检定同源重复惩罚**：同一情境事实被模型同时写进情境 DC、优势/劣势与环境修正时不再全部执行，只保留一条渠道（优先级：优势/劣势 > DC > 修正），被丢弃的原值记录在 `planner_dropped`；非零环境修正必须给出独立理由，否则按 0 处理。缺少优势/劣势理由只记录审计信息，不改写掷骰方式。
- **检定结果更可解释**：修正明细现在列出「属性加值 / 熟练加值 / 情境修正」，结算卡显示「判定来源：难度依据 / 掷骰方式依据 / 环境修正依据」（中英日三语），可以直接从总额倒推每项来源。
- **DC 档位以规则表为准**：检定规划 prompt 原先写死「简单 8 / 普通 10 / 困难 15 / 极限 20」，与规则表（`easy 10 / normal 15 / hard 20 / extreme 25`）不一致，导致模型把普通档 DC 15 当成"困难"上报。现在统一以 `ruleset.dc_table` 为准，默认取普通档，只有任务本身客观更难或更易时才偏离并要求说明依据。

### 升级提示

- **无存档迁移**：新增字段是可选的（`combat.mode`、`combat.adventure_binding`、检定来源字段）；旧存档读取时缺省为空，既有判定结果不变。
- 建议升级重要战役前备份完整 `data/` 目录。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.7-beta.1-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.7-beta.1-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.7-beta.1-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 校验文件。

## English

### Fixes

- **The professional combat tool could not scroll inside its dialog**: the scroll rule targeted the dialog's direct children, but a host wrapper now sits in between and the dialog uses `overflow: hidden`, so at short viewport heights the action cards and confirm button were clipped and unreachable. The active tool panel now owns the vertical scroll: `1280×620`, `1024×600` and `390×700` can all reach the action area and resolution log, while the mobile horizontal card lanes and keyboard PageDown keep working.
- **Story encounters were silently replaced by training presets**: when an active adventure package had no bound encounter, the combat tool fell back to the generic catalog (goblin patrol, skeleton and wolf) and preselected its first entry. It now reports an explicit "the current story has no prepared encounter" state, exposes no start action, and never writes a narrative-suggested preset into the request. To fight a free encounter the GM must explicitly choose "leave the adventure and prepare a free encounter"; once combat starts the mode and adventure binding are persisted in authoritative state, and actors, initiative and available actions all come from the same encounter.
- **Duplicate penalty channels on checks**: when the model wrote the same situational fact into the situational DC, advantage/disadvantage and the environment modifier, all three used to apply. Only one channel is kept now (priority: advantage/disadvantage > DC > modifier) and the discarded values are recorded in `planner_dropped`. A non-zero environment modifier requires an independent reason, otherwise it is treated as 0. A missing advantage reason is only recorded for audit and never rewrites the roll mode.
- **More explainable checks**: the modifier breakdown now lists "ability bonus / proficiency or skill bonus / circumstance modifier", and the check card shows "resolution sources" for difficulty, roll mode and environment modifier.
- **DC bands follow the ruleset table**: the planner prompt hard-coded "easy 8 / normal 10 / hard 15 / extreme 20", contradicting the ruleset table (`easy 10 / normal 15 / hard 20 / extreme 25`) and making the model report the normal DC 15 as "hard". Bands now come from `ruleset.dc_table`, defaulting to the normal tier, and any deviation must be justified.

### Upgrade notes

- **No save migration**: the new fields are optional (`combat.mode`, `combat.adventure_binding`, check source fields). Older saves read them as empty and existing results are unchanged.
- Back up the complete `data/` directory before upgrading important campaigns.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.7-beta.1-windows-portable.zip`
- **Source users**: `DiceFrame-v2.5.7-beta.1-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.7-beta.1-docker-update-linux-amd64.zip`
- Verify downloads with the `SHA256SUMS` file attached to the Release.
