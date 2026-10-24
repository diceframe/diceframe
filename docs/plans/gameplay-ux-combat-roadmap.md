# DiceFrame 游戏化游玩桌面与战斗空间路线图

状态：待确认方案；当前分支只用于方案准备，尚未修改产品代码。

分支：`codex/gameplay-ux-combat-roadmap`

## 1. 目标

把 DiceFrame 从“功能齐全的程序化页面”推进到“有游戏感的数字桌面”，同时保留规则系统、多人权限和存档 authority 的清晰边界。

目标不是复制某一个 VTT，而是组合四种体验：

- 新手能在几分钟内进入一局并知道下一步做什么；
- 熟悉规则的玩家能看到检定、资源、事件和规则解释；
- GM 能拥有独立的准备、诊断和私密信息工作台；
- 不同规则、冒险和未来插件可以使用自己的面板，但不复制第二套状态或结算逻辑。

## 2. 当前事实与不能混淆的边界

### 2.1 现有游玩页已经有可复用骨架

当前 `frontend-v2` 已有：

- `PlayView`：主编排器；
- `GameTimeline`：长叙事、流式内容、检定卡、回合和新内容提示；
- `ActionComposer` / `CombatMessageComposer`：行动提交；
- `PartyRail`、`CharacterQuickStatus`、`PlayUtilityMenu`：状态和工具入口；
- `GmWorkspace`：GM 工作台；
- `RulesetPlayHost` + ruleset registry：规则特化玩法工具；
- `MapWorkspace` + `MapGraph`：地点关系图和地图资产；
- D&D `CombatLiveBar`、initiative、actor cards、action cards 和单轴战斗轨道。

因此第一阶段应是重新组合和定义 surface contract，而不是重写 `PlayView`。

### 2.2 现有战斗确实是单轴 theater

D&D 2024 战斗当前使用：

- `combat.position_mode = "theater"`；
- actor 的 `position` 是整数；
- 距离是 `abs(position_a - position_b)`；
- 移动是单轴加减；
- 前端是横向 5 尺距离带。

已有战斗没有真正的：

- `x/y` 战斗坐标；
- 格子占用和体积；
- 障碍与路径；
- 视线和遮挡；
- AoE 几何；
- 轨迹、测距和空间投影。

现有 `MapGraph` 的 `x/y` 只是地点关系图的力导向布局，不能复用为战斗坐标。

### 2.3 GAL 当前不是状态缺陷

当前 GAL 已经通过 `playMode = "gal"`、`solo = true` 和 `rule = "gal_narrative"` 联动。后续只讨论入口和视觉层级，不重新设计 GAL 状态逻辑。

## 3. 目标信息架构：Game Table

采用“同一张桌面，两种视图”的结构。

```text
GameTable
├── Stage                 当前叙事 / 场景 / 战斗地图
├── ActivityDock          Chat / Dice / Initiative / Notes / Session
├── PlayerRail            角色、队伍、资源、状态
├── ActionSurface         当前合法行动、检定、选择、结果
└── GmWorkbench           GM 私密信息、节点、诊断、控制
```

### 3.1 新手默认层

默认只显示：

- 当前场景和叙事；
- 当前角色的必要状态；
- 3～5 个推荐或合法行动；
- 清晰的提交、等待、完成、失败和重试状态；
- 检定结果与下一步。

### 3.2 专业层

通过抽屉、停靠面板、快捷键或 GM 工作台展开：

- 原始骰式和难度；
- 规则解释；
- 事件序列和状态差异；
- 世界书触发记录；
- 冒险节点和来源锚点；
- 权限和私密投影；
- 批量操作、导入导出和插件面板。

专业层和新手层必须使用同一套 projection/action contract，不能做两套玩法。

## 4. 不屎山的架构原则

1. `GameInstance` 不保存 UI 布局、面板开关或临时前端状态。
2. `PlayView` 只做组合和委托，不新增规则分支。
3. 后端只提供 capability、projection 和合法 action；后端不传前端组件名或路由名。
4. 前端内部使用 `PlayPanelDescriptor` 组合面板：`id / slot / audience / priority / capability / component`。
5. 规则特化 UI 继续使用 `RulesetRuntime.gameplay_view`、`available_intents`、`RulesetPlayHost` 和 registry。
6. 所有写操作继续进入 `ApplicationAction` 或 ruleset intent API；前端不能结算骰子、距离、HP 或状态。
7. 叙事地点图和战斗战术图是两个不同的投影，不共享坐标语义。
8. 新能力优先添加 capability/adapter/registry，不在 generic engine 里添加 `if dnd/coc/gal`。
9. 旧存档和旧客户端默认继续走 theater；无法安全解释的数据必须 fail closed。

## 5. 战斗空间方案

### 5.1 兼容模型

保留现有：

```json
{
  "position_mode": "theater",
  "position": 30
}
```

新增空间能力只在明确声明后出现：

```json
{
  "position_mode": "grid",
  "board": {
    "id": "scene:chapel",
    "width": 24,
    "height": 18,
    "cell_size": 5,
    "distance_metric": "dnd2024"
  },
  "spatial_position": {
    "x": 7,
    "y": 4,
    "footprint": {"w": 1, "h": 1}
  }
}
```

实际字段名需在契约 PR 中确定；不能直接把 `position:number` 改成 union，以免污染旧存档、旧事件和所有现有消费者。

### 5.2 authority

二维战斗由对应 ruleset runtime / reducer 拥有：

- 坐标和占用；
- 移动成本和路径；
- 对角线规则；
- 射程和近战范围；
- 障碍、视线和遮挡；
- AoE；
- 强制位移；
- 玩家、GM、敌人的可见投影。

前端只发送目标格、路径或 action intent。服务端重新计算合法性、距离和影响范围。

### 5.3 分阶段做 2D

先做小型 square-grid vertical slice：

- 一个战斗地图；
- 玩家和敌人 Token；
- 目标格选择；
- 可达格高亮；
- 移动成本和占用校验；
- 射程判断；
- theater fallback。

之后再加：

- 障碍和路径；
- LOS / fog / per-player visibility；
- AoE 预览；
- 推拉和轨迹；
- 地形；
- 战斗事件回放。

不要第一步就做完整 VTT 地图编辑器。

## 6. PR / 任务拆分

每项都应可单独测试和审查，前一项稳定后再做后一项。

### PR-0：方案与契约

- ADR：GameTable、panel descriptor、二维战斗 capability；
- 明确 viewer/audience、responsive slot 和数据 owner；
- 明确 `theater | grid | zone` 空间模式；
- 不改存档、不改规则结算。

### PR-1：GameTable shell

- 桌面端 Stage + Dock + Rail；
- 移动端 bottom sheet / drawer；
- 面板焦点和响应式断点；
- 视觉 token、密度、主题、reduce-motion；
- 保持现有 API 和行为不变。

### PR-2：行动与生成状态

统一 `idle / submitting / streaming / pending / committed / error / retry`：

- 行动提交；
- 流式剧情；
- 检定等待；
- 手动骰；
- Push / reaction；
- 恢复和错误重试。

解决长时间“正在生成”但用户不知道发生什么的问题。

### PR-3：Panel registry

- 把 Chat、Dice、Initiative、Notes、Session、Tactical、GM 面板声明式注册；
- panel visibility 由 audience 和 capability 决定；
- 不把 D&D/CoC/GAL 条件堆进通用组件；
- 先做前端内部 registry，稳定后再考虑服务端可选 projection。

### PR-4：二维战斗后端 vertical slice

- 新空间 contract、schema/version 和事件类型；
- theater adapter 保持旧存档；
- D&D grid board、坐标、占用、移动、距离、范围；
- stale version、权限、重放和 viewer projection；
- 旧存档禁止猜测二维坐标。

### PR-5：TacticalBoard 前端

- `TheaterBoard` 和 `GridBoard` 两个渲染器；
- 地图、Token、当前回合、可达格、目标高亮；
- 点击/拖拽只生成 intent；
- 玩家、GM、隐藏敌人的不同投影；
- 无法使用二维能力时回到 action list，不显示假地图。

### PR-6：战斗行动体验

- Initiative strip；
- action economy；
- 目标选择；
- 范围/射程/路径提示；
- 反应和职业能力；
- 专家抽屉中的公式、事件和规则说明；
- 无合法动作时显示原因和下一步。

### PR-7：Chat / Dice / Initiative / Notes / Session dock

- 右侧桌面 Dock；
- 移动端折叠顺序；
- GM 私密笔记和玩家可见笔记分开；
- 聊天、骰子和战斗结果使用同一事件/结果投影；
- 快捷键和焦点管理。

### PR-8：CoC 调查体验

- 线索、人物、地点、未解决问题；
- SAN、压力和当前调查目标；
- 证据状态和来源解释；
- 使用 adventure/ruleset projection，不把线索状态写进 Vue local state。

### PR-9：GAL / 叙事模式体验

- GAL 作为明确玩法入口或玩法徽标；
- 场景、对白、选项和转场；
- 继续复用 `playMode = gal` 的现有 authority；
- 不新增第二套“视觉小说状态”。

### PR-10：验证与发布质量

- 后端规则、迁移、重放、权限和可见性测试；
- 前端组件和 projection contract 测试；
- desktop/mobile Playwright 流程；
- 视觉回归；
- 键盘、焦点、ARIA、reduce-motion；
- 断线、重连、过期 intent、旧存档；
- 最终 changed-file audit，确保不混入其他任务。

## 7. 必须覆盖的技术风险

- 旧存档：theater 数据不能猜测成 grid；
- 多人同步：空间事件必须带 actor、from/to、state version；
- 权限：GM 隐藏 Token、玩家可见区域和私密笔记不能泄露；
- 随机性：骰子和战斗结果仍由服务端 RNG/规则 runtime 决定；
- 性能：地图 Token、长叙事和实时事件需要分层更新和虚拟化；
- 移动端：不能假设大屏、鼠标和悬停；
- 国际化：所有面板、状态、错误和规则解释都要走 locale；
- 上传与内容包：地图、Token、音频和冒险包继续经过现有安全校验和 attribution；
- 扩展性：面板和空间能力不得变成新的 service locator 或通用大文件；
- 可观测性：提交序号、事件 ledger、诊断和用户可见错误要能对应起来。

## 8. 验收标准

### 新手

- 新建游戏后无需理解内部规则字段；
- 玩家能看懂当前场景、可做行动和结果；
- 任何等待状态都有明确原因；
- 移动端可以完成一轮行动。

### 专业用户

- 可以展开规则、事件、来源和状态差异；
- GM 可以切换战斗、笔记、骰子、先攻和场景工具；
- 可导入冒险包、地图和规则扩展；
- 不需要绕过 UI 修改存档或手写隐藏状态。

### 工程

- 旧 theater 存档和旧战斗测试全部保持；
- 2D 模式的距离、移动、占用、LOS、AoE 和可见性由服务端验证；
- 玩家不能看到 GM-only 数据；
- 前端不含规则结算副本；
- 每个 PR 有明确 changed-file list、单元测试、集成测试和必要的 E2E。

## 9. 参考项目

- [Open Dungeon Master](https://github.com/Lebbitheplow/open-dungeon-master)：行动卡、叙事桌面和移动端；
- [Loreweaver](https://github.com/1A7432/loreweaver)：叙事工作台、规则 authority 和模块面板；
- [SillyTavern](https://github.com/SillyTavern/SillyTavern)：专家面板、Lorebook、扩展生命周期；
- [Ren'Py](https://github.com/renpy/renpy)：GAL/视觉小说场景体验；
- [Ink](https://github.com/inkle/ink)：分支叙事和作者工具；
- [Twine](https://github.com/klembot/twinejs)：低门槛剧情编排；
- [Owlbear Rodeo](https://www.owlbear.rodeo/)：五分钟入门、浏览器和扩展；
- [Alchemy RPG](https://alchemyrpg.com/)：scene-first、氛围和玩家/GM投影；
- [Foundry VTT](https://foundryvtt.com/)：专业扩展生态和权限模型；
- [Quest Portal](https://www.questportal.com/)：AI 辅助 GM 工作流；
- [AI Dungeon](https://help.aidungeon.com/faq/how-to-play)：明确的行动意图和编辑/撤销恢复。
