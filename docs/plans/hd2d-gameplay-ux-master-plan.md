# DiceFrame HD-2D 游戏化体验总方案

状态：待确认方案；本文件只描述产品、架构和实施路线，不代表已经实现。

分支：`codex/gameplay-ux-combat-roadmap`

## 1. 目标

DiceFrame 的目标不是把网页换成复古皮肤，而是把现有的规则运行时、多人权限、冒险图和 AI 叙事组合成一个真正可玩的 HD-2D 叙事游戏平台。

目标体验是：

- 像素角色；
- 3D/2.5D 立体场景；
- 正交或弱透视镜头；
- 景深、雾、灯光、阴影、粒子和天气；
- 对话、检定、战斗和结局都有演出；
- 新手能直接开始；
- 专业用户可以展开规则、事件、来源和 GM 工具；
- 规则和状态仍由服务端唯一掌权。

这不是复制《八方旅人》的素材或具体画面，而是采用“像素角色 + 立体场景 + 电影化演出”的 HD-2D 产品方向。

## 2. 当前基线

### 2.1 已有的能力

当前前端已经有可复用基础：

- `PlayView`、`GameTimeline`、`ActionComposer`；
- `PartyRail`、`CharacterQuickStatus`、`PlayUtilityMenu`；
- `GmWorkspace`；
- `RulesetPlayHost` 和 ruleset registry；
- `MapWorkspace` / `MapGraph`；
- D&D `CombatLiveBar`、initiative、actor cards、action cards；
- server-side ruleset `gameplay_view`、available intents 和 viewer projection。

所以不重写整个 `/play`，而是把现有能力组合成新的 GameTable。

### 2.2 待合 PR 是基础，不是阻碍

- #585：玩法 registry 和 gameplay package；
- #586：玩法 turn driver；
- #587：按玩法分发 `/play` 视图；
- #588/#589：GAL 好感度权威状态和结算；
- #590：冒险结局节点；
- #583/#584/#591/#592/#593：GameInstance、staged publication、control、action admission 和 configuration 拆分。

后续实现直接在这些边界上继续，不再创建第二套 gameplay registry、第二套路由或第二套状态。

### 2.3 已确认的战斗缺口

当前 D&D 战斗仍是单轴 theater：

- actor `position` 是整数；
- 距离是绝对差；
- 前端是横向 5 尺距离带；
- 没有真正的二维坐标、障碍、路径、占用、LOS、AoE 或战斗地图绑定。

现有 `MapGraph` 是地点关系图，不是战斗地图，不能复用其 x/y 作为战斗坐标。

## 3. 产品信息架构：GameTable

```text
GameTable
├── SceneStage          HD-2D 场景 / 叙事 / 战斗空间
├── DialogueOverlay     对话、选项、旁白、字幕
├── ActionSurface       当前合法行动、检定、结果
├── PlayerRail          角色、队伍、HP/SAN/Luck、状态
├── ActivityDock        Chat / Dice / Initiative / Notes / Session
└── GmWorkbench         私密信息、冒险节点、诊断和控制
```

### 3.1 默认新手路径

用户默认只看到：

- 当前场景；
- 当前叙事；
- 角色和队伍状态；
- 3～5 个推荐行动；
- 清晰的等待、结果和下一步；
- 必要的对白和选项。

### 3.2 专业路径

专业用户通过抽屉、快捷键或 GM 工作台打开：

- 原始骰式和难度；
- 状态差异；
- 事件序列；
- Lorebook 触发记录；
- 冒险节点和来源锚点；
- 隐藏信息和 viewer projection；
- 批量编辑、导入导出和插件面板。

新手和专业路径使用同一个 projection/action contract，不维护两套玩法。

## 4. 用户体验完整路线

### 4.1 创建游戏

新手：

```text
选择世界或冒险
→ 选择玩法
→ 接受推荐规则
→ 创建角色
→ 进入第一幕
```

专业用户：

```text
规则 / 世界 / 冒险 / 内容包 / GM 配置
→ 高级预览
→ 检查能力和权限
→ 开始游戏
```

### 4.2 捏人

基础流程：

```text
角色概念 → 默认头像 → 外观选择 → 开始游戏
```

高级流程：

- 身体、肤色、发型、服装、装备；
- 角色头像和场景 Sprite；
- 表情、朝向和动作；
- 自定义素材；
- 规则特化职业/背景由 ruleset builder 负责。

外观使用独立的 `CharacterAppearance` contract，存储部件引用、调色和素材 provenance，不把像素动画字段塞进规则属性。

### 4.3 第一幕

用户进入一个有纵深的 HD-2D 场景：

- 像素角色站在立体环境中；
- 镜头有轻微推拉和横移；
- 背景、前景、雾、光源和粒子分层；
- Vue HUD 显示对白、状态和行动。

### 4.4 行动和检定

```text
选择行动
→ 角色动画 / 镜头反馈
→ 服务端检定
→ 骰子和规则解释
→ 场景结果演出
→ 线索 / 状态 / 关系 / 进度变化
```

前端不能决定骰点、HP、SAN、好感、战斗位置或冒险进度。

### 4.5 探索、战斗和后果

- CoC：区域、房间、线索、压力和调查目标；
- GAL：对白、角色站位、路线和结局；
- D&D：二维方格、Token、路径、距离、LOS 和 AoE；
- theater 规则：继续使用距离带和行动卡。

战斗结束后回到场景，而不是跳到一个孤立的后台弹窗：战斗结果应该成为剧情的下一段。

### 4.6 成长和回顾

每幕或每场结束后显示：

- 角色状态；
- 新线索；
- NPC 关系；
- 新地点；
- 当前目标；
- 重要事件；
- 可继续的下一步。

## 5. HD-2D 表现层

### 5.1 渲染职责

```text
GameplayProjection + PresentationCue[]
                  ↓
          PresentationRuntime
                  ↓
  3D Scene Renderer + Pixel Sprite Layer
                  ↓
          Vue HUD / Accessibility Layer
```

第一版候选为 Three.js 或 Babylon.js 的可替换 adapter，使用正交/弱透视相机、低多边形场景和像素 Sprite。不要把 Godot 或完整 Phaser 应用嵌入 Vue。

PixiJS 可作为纯 2D/像素特效 renderer，但不作为真正 HD-2D 立体场景的唯一核心。

### 5.2 场景层

```text
Background 远景
Environment 3D 场景
Characters 像素角色平面 / billboard
Foreground 前景遮挡
Lighting 光照 / 雾 / 阴影
Effects 粒子 / 天气 / 命中特效
Dialogue Vue overlay
```

### 5.3 演出 Cue

冒险包或玩法包可以声明：

```json
{
  "type": "scene_cue",
  "scene_ref": "scene:fog_harbor_dock",
  "camera": "push_in",
  "actors": [{"id": "npc:keeper", "pose": "turn", "expression": "afraid"}],
  "effects": ["rain", "lantern_flicker"],
  "audio": "ambience:harbor_rain",
  "dialogue_ref": "dialogue:warehouse_warning"
}
```

Cue 只描述表现，不拥有 mechanics authority。播放失败、设备不支持 WebGL 或用户关闭演出时，降级为普通叙事卡片。

### 5.4 性能和降级

- 场景 renderer 懒加载；
- 场景、Sprite、音频按幕和地点分包；
- 使用 texture atlas、压缩资源和层级卸载；
- 像素 Sprite 使用 nearest filtering 和整数缩放；
- 移动端减少灯光、粒子和景深；
- 提供低功耗模式、跳过演出和纯文本模式；
- 关键剧情和行动永远有 DOM/文本 fallback。

## 6. 角色捏人和素材管线

### 6.1 第一阶段

- DiceBear：默认头像、NPC 头像、角色卡头像；
- Universal LPC：研究部件组合和 spritesheet 导出；
- 自有素材：逐项记录授权和来源；
- 角色存档只保存外观参数和资源引用。

### 6.2 生产素材格式

```text
CharacterAppearance
├── body_ref
├── skin_ref
├── hair_ref
├── outfit_ref
├── weapon_ref
├── palette_ref
├── animation_refs
└── provenance
```

### 6.3 地图管线

Tiled/LDtk 只作为 authoring 工具。导入后转成 DiceFrame 自己的：

```text
SceneMap / CombatMap
├── tile layers
├── environment objects
├── anchors
├── zones
├── blockers
├── light sources
├── spawn points
└── visibility policy
```

运行时不依赖第三方编辑器私有格式。

## 7. 战斗空间

### 7.1 兼容模式

保留：

```json
{"position_mode": "theater", "position": 30}
```

新增 capability：

- `theater`：单轴距离；
- `zone`：区域、房间和威胁范围；
- `grid`：二维格子和战术板。

旧存档没有空间信息时，禁止猜测转换。

### 7.2 D&D grid authority

D&D runtime/reducer 拥有：

- 坐标和 footprint；
- 占用和碰撞；
- 移动成本和路径；
- 对角线规则；
- 距离、射程和近战；
- 障碍、LOS、fog 和可见投影；
- AoE、推拉和轨迹；
- stale intent 和 event replay。

前端只发送 destination/path/action intent。

### 7.3 CoC/GAL 场景板

不强制方格：

- CoC 使用区域、房间、线索点和威胁区域；
- GAL 使用场景、角色站位和演出锚点；
- 只有明确声明 grid capability 的规则才显示战术棋盘。

## 8. 代码边界

1. `GameInstance` 不保存 UI 布局、动画进度和面板开关。
2. `PlayView` 只做 orchestration。
3. `PresentationRuntime` 只消费 projection 和 cue。
4. Three.js/Babylon/Pixi/Howler 不进入 ruleset/domain 层。
5. `ApplicationAction` 和 ruleset intent 是唯一写入口。
6. 规则特化能力通过 registry/capability/adapter 扩展。
7. MapGraph 地点坐标和 CombatMap 战斗坐标完全分离。
8. 角色外观参数和规则属性分离。
9. 第三方编辑器格式先转换成 DiceFrame contract。
10. 所有隐藏信息都由服务端 viewer projection 过滤。

## 9. PR / 实施顺序

### PR-0：契约和 ADR

- GameTable contract；
- panel descriptor；
- `PresentationCue`；
- `SceneMap` / `CombatMap` 边界；
- `CharacterAppearance` 边界；
- `theater | zone | grid` capability；
- 不改现有存档和规则结算。

### PR-1：GameTable shell

- Stage、Dock、Rail、ActionSurface；
- desktop/mobile responsive；
- focus、keyboard、ARIA、reduce-motion；
- 复用现有 PlayView 和 GM workspace。

### PR-2：行动和生成状态

统一：

```text
idle → submitting → streaming → pending → committed
                              ↘ error → retry
```

覆盖剧情、检定、手动骰、Push、reaction、恢复和重试。

### PR-3：Panel registry

- Chat、Dice、Initiative、Notes、Session、Tactical、GM；
- audience/capability/priority/placement；
- 不把规则分支塞进通用组件。

### PR-4：HD-2D renderer foundation

- Three.js/Babylon adapter prototype；
- orthographic/weak-perspective camera；
- pixel Sprite billboard；
- scene layers；
- loading、fallback、skip、low-power；
- Vue HUD overlay；
- 不接真实规则写入。

### PR-5：HD-2D vertical slice

- 一个场景；
- 一个像素角色；
- 一个 NPC；
- 一段对白；
- 一个选择；
- 一个检定；
- 一个 cue：镜头、灯光、雨或粒子；
- 结果回到普通行动 surface。

### PR-6：角色捏人和素材 provenance

- DiceBear 头像；
- layered `CharacterAppearance`；
- LPC 风格 spritesheet 原型；
- JSON 配置导入导出；
- 逐素材许可证和来源记录。

### PR-7：地图导入和 SceneMap

- LDtk 优先导入；
- Tiled 可选导入；
- 转成自有 SceneMap；
- 场景层、对象层、灯光、锚点、区域和触发器；
- 不把编辑器格式传到规则运行时。

### PR-8：二维战斗后端

- grid contract、schema/version、事件；
- theater adapter 保持旧存档；
- 坐标、占用、移动、范围和距离；
- stale version、权限、重放和可见性测试。

### PR-9：TacticalScene

- theater / zone / grid 三种 renderer；
- Token、当前回合、可达格、目标和路径；
- LOS、fog、AoE 预览；
- Pixi 或 Three.js adapter 只消费 projection；
- 不能使用空间能力时回退 action list。

### PR-10：战斗和演出 UX

- initiative strip；
- action economy；
- 攻击、技能、反应和职业能力；
- 伤害、命中、倒地、死亡豁免演出；
- 战斗结束返回剧情场景。

### PR-11：CoC 调查和 GAL 叙事

- CoC：线索、人物、地点、SAN、压力、调查目标；
- GAL：场景、对白、选择、好感、路线、结局；
- 使用 #588/#589/#590 的权威 projection；
- 不在前端另存好感或结局状态。

### PR-12：Dock、音频和高级体验

- Chat / Dice / Initiative / Notes / Session；
- Howler 音频总线；
- UI 动画；
- GM 私密信息；
- 快捷键、密度、主题和低功耗设置。

### PR-13：全面验证

- 规则、迁移、重放、权限和 viewer projection；
- HD-2D renderer contract；
- desktop/mobile E2E；
- 视觉回归；
- WebGL fallback；
- 键盘、ARIA、reduce-motion；
- 旧 theater 存档；
- changed-file audit。

## 10. 测试策略

### 后端

- grid 坐标和 footprint；
- 移动成本、路径、占用和对角线；
- range、LOS、AoE、forced movement；
- viewer visibility；
- stale version、replay、旧存档；
- 规则行为不由前端输入决定。

### 前端

- GameTable panel registry；
- SceneStage loading/fallback/skip；
- `PresentationCue` 重放和顺序；
- 角色外观参数导入导出；
- mobile drawer 和键盘焦点；
- grid/theater/zone 交互；
- 无 WebGL、低性能和 reduce-motion。

### 产品验收

新手应能：

- 五分钟内创建角色并进入第一幕；
- 看懂当前场景和可执行行动；
- 完成一次检定和一次分支选择；
- 在移动端完成一轮行动。

专业用户应能：

- 展开规则、骰式、事件和来源；
- 管理地图、冒险节点、笔记和私密信息；
- 导入规则、冒险和地图内容包；
- 查看状态变化但不能绕过服务端 authority。

## 11. 主要风险

- 把第三方渲染器 API 扩散到规则代码；
- 同时引入 Pixi、Phaser、Three.js 造成三套渲染核心；
- 把像素动画状态写进 GameInstance；
- 旧 theater 存档被猜测迁移成 grid；
- Tiled/LDtk/GPL/像素素材许可证混淆；
- HD-2D 资源过大导致移动端崩溃；
- 玩家看到 GM-only 线索、Token 或结局；
- 视觉演出阻塞实际行动；
- 只有背景好看，但没有持续的角色、选择、资源、后果和成长反馈。

## 12. 参考轮子

- [PixiJS](https://github.com/pixijs/pixijs)：2D/像素层；
- [Phaser](https://github.com/phaserjs/phaser)：完整 Web 2D 游戏循环，暂不与 Pixi 同时作为核心；
- [Three.js](https://github.com/mrdoob/three.js)：HD-2D 立体场景候选；
- [Babylon.js](https://github.com/BabylonJS/Babylon.js)：另一套 Web 3D 场景候选；
- [LDtk](https://github.com/deepnight/ldtk)：易用的 2D 地图 authoring；
- [Tiled](https://github.com/mapeditor/tiled)：高级地图 authoring；
- [Ink](https://github.com/inkle/ink)：分支叙事；
- [Yarn Spinner](https://github.com/YarnSpinnerTool/YarnSpinner)：对话编译器和运行时；
- [Dialogic](https://github.com/dialogic-godot/dialogic)：时间线和对白编辑体验；
- [DiceBear](https://github.com/dicebear/dicebear)：头像和默认角色形象；
- [Universal LPC Generator](https://github.com/LiberatedPixelCup/Universal-LPC-Spritesheet-Character-Generator)：像素捏人研究；
- [howler.js](https://github.com/goldfire/howler.js)：音频；
- [Lottie Web](https://github.com/airbnb/lottie-web)：UI 演出；
- [RPGUI](https://github.com/RonenNess/RPGUI)：复古 RPG UI 参考；
- [Open Dungeon Master](https://github.com/Lebbitheplow/open-dungeon-master)：行动卡和数字桌面；
- [Alchemy RPG](https://alchemyrpg.com/)：scene-first 叙事舞台；
- [Owlbear Rodeo](https://docs.owlbear.rodeo/docs/getting-started/)：低门槛桌面体验；
- [Foundry VTT](https://foundryvtt.com/)：专业模块和权限生态。
