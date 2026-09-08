# DiceFrame v2.5.4

> 战斗扩展正式版。v2.5.4 合并 Issue 212 的通用战斗能力、`freeform_wuxia` 首个规则接入，以及战斗结算与剧情叙事的衔接修复。升级重要战役前，请先备份完整的 `data/` 文件夹。

## 中文

### 本版重点：通用战斗扩展

- **公式化战斗效果**：新增受限 JSON 公式 DSL，服务端统一计算伤害、资源消耗和属性效果，客户端提交的数值不会成为结算依据。
- **资源池与护盾**：支持 HP、内力等规则资源，以及独立护盾池；伤害按规则先处理护盾，再处理 HP。
- **行动条调度**：新增 round-robin、initiative 和 threshold（ATB）调度器。阈值行动条由服务器推进，速度、阈值、溢出和消耗策略由规则声明。
- **D&D 2024 适配**：D&D 2024 的伤害与治疗骰式接入通用公式求值，法术位、专注、豁免、濒死和胜负判定仍由 D&D 规则运行时负责。

### `freeform_wuxia` 战斗接入

- 规则模板可以声明战斗调度器、资源池和动作目录。
- 内置内力掌、回春丹、属性 Buff 和护盾配置示例。
- 新增服务端权威战斗动作接口、GM 操作、目标选择、库存消耗和存档持久化。
- 管理端新增战斗扩展编辑器，游戏面板显示资源、行动条和参与实体。

### 剧情衔接与可靠性

- 已结算的战斗动作会作为可信事实交给下一次 GM 叙事，明确描写 NPC 受击、伤害和后果。
- 叙事生成失败时保留待叙事事件；成功写入剧情后才消费，避免重复扣除或重复结算。
- 历史日志中的状态变动会继续进入后续上下文，旧存档不会因为升级而丢失已有战斗结果。
- 加强战斗事务、快照回滚、NPC 目标投影、编辑器布局和多人可见性处理。

### 升级提示

- 本版新增的战斗扩展状态是增量字段，旧存档可以继续加载；重要战役升级前仍建议备份 `data/`。
- 只有声明 `combat` 能力的规则会启用战斗扩展；其他规则保持原有玩法。
- 当前通用动作目录使用 `spell:*` 等 canonical action id 表达具体法术，状态类效果仍需后续规则能力继续扩展。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.4-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.4-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.4-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 校验文件。

## English

### Highlights: generic combat extension

- **Formula-driven combat effects**: a restricted JSON formula DSL evaluates damage, resource costs, and stat effects on the server; client-supplied totals are never authoritative.
- **Resource pools and barriers**: rules can declare HP, qi, and other pools, including an independent barrier pool that absorbs damage before HP.
- **Turn scheduling**: round-robin, initiative, and threshold (ATB) schedulers are available. Threshold advancement is server-authoritative and configured by the ruleset.
- **D&D 2024 adapter**: D&D damage and healing dice use the generic formula evaluator while spell slots, concentration, saves, dying, and victory remain D&D-owned.

### `freeform_wuxia` combat consumer

- Rule templates can declare schedulers, resource pools, and action catalogs.
- The built-in example includes qi palm, healing pill, stat buffs, and barriers.
- Server-authoritative actions, GM control, target selection, inventory consumption, and persistence are wired end to end.
- The admin editor and play panel expose the declared combat capability without reimplementing mechanics in the frontend.

### Narrative continuity and reliability

- Resolved combat actions are handed to the next GM narration as trusted facts, so NPC hits and consequences are described explicitly.
- Failed narration keeps pending events; successful persistence consumes them exactly once, preventing duplicate mechanics.
- Historical state changes remain available to later context, including saves created before this release.
- Combat transactions, snapshot rollback, NPC projection, editor layout, and multiplayer visibility are hardened.

### Upgrade notes

- The new combat extension fields are additive, so existing saves remain loadable. Back up `data/` before upgrading important campaigns.
- The extension is enabled only for rules that explicitly declare the `combat` capability; other rules keep their existing behavior.
- Concrete spells use canonical action ids such as `spell:*`; status effects remain a follow-up capability.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.4-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.4-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.4-docker-update-linux-amd64.zip`
- Verify downloads with the `SHA256SUMS` file attached to the Release.
