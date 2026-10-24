# DiceFrame 内容信息架构与施工方案

> 状态：方案与代码盘查稿，2026-10-09
>
> 本文只记录内容边界、现状盘查和施工顺序。本轮不修改存档 schema、不改变 canonical identity、不改变插件生命周期。

## 1. 目标

DiceFrame 是泛文字游戏。冒险只是可玩的内容类型，不能成为所有内容入口的默认身份。

需要解决的核心问题：

- 用户分不清冒险、模组、内容包、世界、资料册和插件；
- 内容库、冒险管理、模组管理和创作工具互相跳转；
- D&D 模组的安装、选择冒险和创建游戏链路不够直观；
- 自制冒险现在可以和世界、资料册、规则一起导出为 `.dfplugin`，并通过安装回归测试保持冒险来源和资源目录；
- 剧本导入、外部模组导入和文本分配导入都集中在冒险页；
- 创建游戏中的资料册默认引用关系不够透明；
- 冒险不兼容时前端给出泛化错误，甚至把用户直接切换到自由游戏。

## 2. 统一的产品对象

| 对象 | 用户含义 | 主要内容 | 所有者 | 主要入口 |
| --- | --- | --- | --- | --- |
| 游戏 / 存档 | 正在进行的一局游戏 | 玩家选择、剧情进度、战斗结果、临时状态 | 当前存档 | 首页、游戏大厅、游玩页 |
| 规则运行时 | 游戏如何判定 | 检定、战斗、数值、资源和权限 | Ruleset runtime | 规则页、创建游戏 |
| 世界 | 故事发生的背景 | 地理、历史、势力、长期事实 | World | 世界页 |
| 资料册 | 可被 AI 检索的资料集合 | 世界设定、人物资料、地点资料、背景文本 | Lorebook / Book | 资料册页 |
| 冒险 | 一段可以玩的故事 | 章节、场景、分支、目标、结局、冒险专属资源 | Adventure | 内容库、冒险工作台 |
| 内容包 / 模组 | 一份可安装、可分享的来源包 | 一个或多个冒险、世界、资料册、NPC、物品、地图等 | Content module / PluginHost | 内容来源页、内容包详情 |
| 插件 | 提供功能的扩展 | AI、主题、外部服务、工具等 | PluginHost | 插件页 |
| 外部文件 | 尚未进入 DiceFrame 的内容 | Markdown、TXT、Foundry、Fantasy Grounds、PDF 等 | Import adapter | 冒险工作台或内容工具 |
| 文本分配任务 | 把一篇文本拆成多个内容对象 | 世界、资料册、角色模板、冒险片段 | Content review | 创作工具 |

### 2.1 模组、冒险包和插件的显示规则

用户口中的“D&D 模组”通常是一个可以安装的 D&D 内容包。它可能包含一个或多个冒险，也可能只有资源。

界面建议显示：

> D&D 内容包（模组）

“插件”只用于提供功能的扩展。`content-pack`、`adventure-module`、`legacy_autoimport` 和 `catalog` 是内部 manifest/运行时概念，不作为普通用户的首要术语。

冒险包是作者或文件格式层面的打包称呼。玩家看到的核心对象仍是“冒险”。

## 3. 对象关系

```text
内容包 / 模组
 ├─ 冒险 A
 ├─ 冒险 B
 ├─ 世界
 ├─ 资料册 1
 ├─ NPC / 怪物 / 物品
 ├─ 地图 / 场景
 └─ 遭遇资源

创建游戏时组合：
规则运行时 + 世界 + 明确选择的资料册 + 可选的一个冒险
```

冒险包可以携带世界和资料册，但冒险本身不是第二本世界书。冒险应通过稳定引用使用世界和资料册中的长期事实；冒险专属的临时 NPC、地点和遭遇属于冒险资源。

规则运行时独立于冒险和内容包。内容包可以声明“需要 D&D 2024”，但不能获得规则执行权，也不能静默替换规则。

当前 `src/adventures/bundle.py` 的 `AdventureBundleManifest` 已包含 `world_policy`、`recommended_world_id` 和 `required_runtime`，没有把资料册作为冒险本体字段。这种边界应当保留。

## 4. 页面信息架构

### 4.1 首页

首页负责：

- 开始自由游戏；
- 继续或恢复存档；
- 进入内容库；
- 进入创作工坊。

“浏览内容”应进入 `/library`，默认显示“全部内容”，不能默认显示冒险列表。

### 4.2 内容库 `/library`

内容库是发现和使用内容的目录，不是编辑器和安装器。

默认分类：

```text
全部｜冒险｜世界｜资料册｜内容包｜资源
```

内容库负责：

- 搜索、筛选、查看内容；
- 显示来源、版本、规则要求和世界策略；
- 查看冒险详情；
- 从具体冒险进入创建游戏；
- 从内容包卡片进入内容包详情。

内容库不负责：

- 编辑冒险；
- 上传 PDF、Foundry 或 Fantasy Grounds 文件；
- 安装、更新、禁用和卸载模组；
- 修改世界书；
- 执行文本分配。

### 4.3 冒险工作台 `/adventures`

冒险工作台面向创作者，负责：

- 新建冒险；
- 复制和编辑冒险；
- AI 生成冒险草稿；
- 导入文字剧本；
- 导入外部冒险格式；
- 审核引用、公开范围和流程图；
- 发布版本；
- 导出单个原生冒险包。

建议内部拆为：

```text
冒险草稿
已发布冒险
导入记录
```

内容库中的“编辑”按钮只能跳到这里，不应在内容库中直接编辑。

### 4.4 创作工坊 `/creator`

创作工坊是入口编排页，不拥有具体领域数据。

建议分组：

- 故事创作：冒险工作台；
- 世界与资料：世界、资料册；
- 内容发布：创建内容包、导出内容包；
- 文本工具：文本整理与分配。

现有 `CreatorView.vue` 已经是合适的入口壳，但“冒险编辑”仍然指向职责混合的 `AdventuresView.vue`，后续应只拆内部职责，不新增第二套冒险编辑器。

### 4.5 内容来源 `/modules`

内容来源负责内容包生命周期：

- 导入 `.dfplugin`；
- 预览安装影响；
- 安装；
- 启用和禁用；
- 更新；
- 卸载；
- 查看包内冒险、资料册和资源；
- 查看哪些存档正在使用它。

内容来源不负责编辑冒险正文，也不负责文本导入。

### 4.6 内容包详情 `/modules/:moduleId`

内容包详情展示：

- 包名称、ID、版本和来源；
- 包内冒险清单；
- 包内世界和资料册；
- 包内 NPC、物品、地图、遭遇等资源；
- 规则兼容性；
- 使用该包的存档；
- 安装生命周期操作。

包内冒险使用“用此冒险创建游戏”进入 `/create`，并携带 `module_id`、冒险 ID 和来源身份。

如果包内没有冒险，只显示查看和应用资源，不显示开始冒险。

### 4.7 世界与资料册

世界负责身份、背景和兼容性；资料册负责知识条目和检索绑定。

一个世界可以有多本资料册：

- 一本主资料册；
- 多本附加资料册；
- 全局资料册；
- 游戏级资料册绑定。

世界页和资料册页负责编辑和绑定，不让冒险页代管世界书。

## 5. 三条主要用户链路

### 5.1 使用别人提供的 D&D 模组

```text
内容来源
→ 导入 .dfplugin
→ 预览安装影响
→ 安装内容包
→ 打开内容包详情
→ 选择具体冒险
→ 创建游戏
→ 确认规则、世界和资料册
→ 开始游戏
```

内容包声明的 `required_runtime` 只用于兼容性检查。没有兼容规则运行时，应提示安装或选择正确规则，不能改成自由游戏后继续隐藏冒险。

### 5.2 自制一个本地冒险

```text
冒险工作台
→ 新建冒险
→ 编辑章节、场景、分支和遭遇
→ 选择规则要求和世界策略
→ 审核
→ 发布冒险版本
→ 内容库
→ 用此冒险创建游戏
```

### 5.3 自制并分享完整 D&D 内容包

```text
创作工坊
→ 创建内容包
→ 选择一个或多个冒险
→ 选择世界、资料册和资源
→ 设置规则要求和世界策略
→ 检查引用和兼容性
→ 发布不可变版本
→ 导出 .dfplugin
```

已绑定存档的冒险版本不能原地修改。修改应复制为新身份或新版本，保持旧存档继续使用旧摘要。

新冒险内容包使用：

```text
content_profile: adventure-module
content_delivery_mode: catalog
```

这样内容注册到目录，不会在启用时静默灌入用户世界。旧内容包继续保留 `legacy_autoimport` 兼容路径。

## 6. 导入功能边界

### 6.1 导入内容包

入口：**内容来源 → 导入本地内容包**

输入：`.dfplugin`

结果：安装后的内容包。

### 6.2 导入剧本 / 外部模组

入口：**冒险工作台 → 导入剧本或外部冒险**

当前支持范围：

- UTF-8 Markdown / TXT；
- Foundry Adventure JSON；
- 未加密 Fantasy Grounds XML / `.mod`；
- 有文字层的 PDF。

流程：

```text
外部文件
→ 格式适配器
→ Adventure Draft
→ 预览 warnings / blockers / unresolved references
→ 人工编辑
→ 服务端重新校验
→ 发布为原生冒险
```

导入文件不会：

- 执行脚本、宏或代码；
- 自动下载远程图片；
- 把任意文档直接变成可运行 D&D 规则；
- 自动生成 HP、AC、伤害和奖励机制；
- 直接写入当前游戏。

外部文件里的规则数字先作为参考资料。只有作者明确映射到 DiceFrame 规则资源后，才参与运行时结算。

### 6.3 文本整理与分配

入口：**创作工坊 → 文本整理与分配**

它把普通文本按标题拆成片段，用户逐段选择保存目标：

```text
世界背景 → 世界
城市资料 → 资料册
人物介绍 → 角色模板
剧情章节 → 冒险
```

它不应成为冒险页的主要入口，也不自动推导规则数值。若从冒险工作台进入，应提供“从文本创建冒险”的固定目标快捷方式。

## 7. 创建游戏和资料册语义

创建游戏的顺序：

```text
选择规则
→ 选择世界
→ 选择本局资料册
→ 可选：选择冒险
→ 检查兼容性
→ 创建游戏
```

“本局自动引用的资料册”应改成可展开的“本局资料册”区域，显示：

- 资料册名称；
- 来源世界或内容包；
- 主资料册 / 附加资料册 / 全局资料册；
- 条目数量；
- 是否启用；
- 共享引用还是本局副本。

选择方式：

- 使用世界默认资料册；
- 为本局选择资料册；
- 新建空白资料册；
- 新建世界并共享引用来源主资料册。

当前前端选项的真实语义：

| 旧值 | 当前实际行为 | 新显示文案 |
| --- | --- | --- |
| `__builtin__` | 使用当前世界绑定的资料册 | 使用当前世界默认资料册 |
| `__blank__` | 新建世界和空白主资料册 | 新建世界，使用空白主资料册 |
| `copy:<worldId>` | 新建世界身份，共享引用来源主资料册，条目不复制 | 新建世界，共享引用「来源世界」主资料册 |

当前后端已经支持 `book_bindings`，所以第一版只需要把前端选择结果明确提交，不需要新增存档字段。

## 8. 兼容性引导

服务端已有结构化原因：

- `ruleset_runtime_unavailable`；
- `adventure_format_unsupported`；
- `runtime_id_mismatch`；
- `runtime_version_too_old`；
- `world_mismatch`。

前端应保留选中的冒险并显示对应操作：

| 原因 | 主操作 |
| --- | --- |
| 规则不存在 | 选择或安装兼容规则 |
| 规则版本过低 | 更新规则运行时 |
| 世界不匹配 | 切换到推荐世界 |
| 冒险格式不支持 | 返回选择其他冒险 |
| 内容来源不可用 | 前往内容来源启用或安装 |

“放弃此冒险，改为自由游戏”只能是用户主动选择的次要按钮，不能作为兼容失败的唯一按钮。

## 9. 代码盘查结果

### 9.1 前端入口

| 文件 | 当前情况 | 施工落点 |
| --- | --- | --- |
| `frontend-v2/src/router/index.ts` | 已有 `/library`、`/creator`、`/adventures`、`/modules`、`/modules/:moduleId`、`/create` | 保留这些 canonical routes，不新增重复入口 |
| `frontend-v2/src/navigation/appNavigation.ts` | 已有内容组和创作工坊，但冒险和模块仍可直接暴露 | 调整显示名称和 active alias，保持 route identity |
| `frontend-v2/src/features/overview/OverviewView.vue` | “浏览内容”进入 `/library` | 保持入口，内容中心默认展示全部内容 |
| `frontend-v2/src/features/library/ContentLibraryView.vue` | 已并行读取冒险、世界、规则和内容来源 | 只做发现、筛选和分流，不承载编辑或安装 |
| `frontend-v2/src/features/creator/CreatorView.vue` | 已有创作工坊壳，冒险卡片指向混合职责页面 | 保留壳，改为清晰的工作台入口 |

### 9.2 冒险与模组前端

| 文件 | 当前情况 | 施工落点 |
| --- | --- | --- |
| `frontend-v2/src/features/admin/AdventuresView.vue` | 1129 行，列表、新建、AI、导入、文本审核、编辑、导出、删除全部集中；查询已不再写死 D&D | 拆成 workbench、list、create、editor、import actions；暂时保持 `/adventures` 路由 |
| `frontend-v2/src/features/adventures/ScriptImportDialog.vue` | 已有剧本预览、审核、发布流程 | 保留组件，入口迁移到冒险工作台 |
| `frontend-v2/src/features/content-review/ContentTextReviewDialog.vue` | 已有文本分配审核和提交 | 保留领域组件，入口迁移到创作工具 |
| `frontend-v2/src/features/modules/ModulesView.vue` | 已有已安装、本地导入、在线内容包区块 | 主要做命名和引导优化，不重写生命周期 |
| `frontend-v2/src/features/modules/ModuleDetailView.vue` | 已有包内冒险、资源、兼容性、使用存档和生命周期操作 | 继续作为内容包详情，不承载冒险编辑 |
| `frontend-v2/src/features/modules/resources/ModuleResourceBrowser.vue` | 已有只读浏览和创建用户副本 | 继续保持原包只读，复制后进入对应内容库 |

### 9.3 创建游戏前端

| 文件 | 当前情况 | 施工落点 |
| --- | --- | --- |
| `frontend-v2/src/features/create/CreateView.vue` | 规则、世界、资料册和冒险均在一页，资料册仍主要只读 | 拆出资料册选择区和兼容性操作区，不增加业务所有权 |
| `frontend-v2/src/features/create/adventureSelection.ts` | 已将服务端作为兼容性权威，但把多种原因合并为通用错误 | 保留结构化原因，映射具体操作 |
| `frontend-v2/src/features/create/WorldLorebookSummary.vue` | 已展示最终资料册和引用语义，启用资料册默认勾选 | 保持逐本选择和共享/副本提示 |
| `frontend-v2/src/features/create/createPayload.ts` | 已支持 `book_bindings`、空白世界和来源引用 | 改文案和选择投影，保持 payload 契约 |

### 9.4 后端领域服务

| 文件 | 当前情况 | 施工落点 |
| --- | --- | --- |
| `src/webui/services/adventures.py` | 已有来源感知目录、兼容性、CRUD、复制、导入、导出和存档保护 | 保持 Adventure owner，不再加入模组生命周期 |
| `src/webui/services/modules.py` | 已有内容包只读 facade、内容清单、冒险、资料册和使用存档 guard | 继续复用，不新建第二套 Module store |
| `src/webui/services/adventure_script_import.py` | 已有 Markdown/TXT 和外部格式的 Draft/Review/Commit 适配 | 继续使用统一导入链 |
| `src/webui/services/content_text_review.py` | 已支持世界、资料册、角色模板和冒险四种目标 | 只调整入口和文案，不与剧本导入合并 |
| `src/webui/services/game_lifecycle.py` | 已接收冒险来源和 `book_bindings` | 继续作为创建游戏写入 owner |
| `src/webui/services/game_creation_phases.py` | 同时存在 canonical binding 和旧复制兼容路径 | 新 UI 走 canonical binding，旧路径只保留兼容 |
| `src/webui/services/plugins.py` | 现有内容包导出以世界、规则、角色卡为锚点，不包含冒险 | 不继续增加冒险分支；新增独立内容包组装层 |
| `src/plugin_host/support.py` | 已有 `content_profile` 和 `content_delivery_mode` 校验 | 新冒险内容包使用 `adventure-module + catalog` |
| `src/webui/services/module_validation.py` | 已校验 `adventure_packages`、runtime 和 catalog | 作为自制内容包发布前的服务端校验入口 |

## 10. 推荐施工顺序

### P0：入口、命名和职责拆分

目标：让用户先走对路，不改存档和内容契约。

落点：

- `ContentLibraryView.vue`：内容库默认“全部”，移除导入和创作主按钮；
- `library/copy.ts`、`zh-CN.ts`：统一“内容库 / 冒险工作台 / 内容来源”文案；
- `CreatorView.vue`：承接创作和导入入口；
- `appNavigation.ts`：调整导航和 active alias；
- 保留旧 query intent 和路由别名。

验收：从首页“浏览内容”进入内容库，不直接进入冒险管理；内容库中的安装、创作、编辑入口职责清楚。

### P1：拆分冒险工作台前端

目标：停止向 1129 行的 `AdventuresView.vue` 继续堆功能。

拆分方向：

```text
AdventureWorkbenchView
 ├─ AdventureListPanel
 ├─ AdventureCreateDialog
 ├─ AdventureEditorDialog
 ├─ AdventureImportActions
 └─ existing ScriptImportDialog / ContentTextReviewDialog
```

后端 API 暂不重写，避免同时改变领域契约。

### P2：统一内容库读模型

第一步复用已有 `/adventures`、`/worlds`、`/lorebooks`、`/modules` 接口。

稳定后，如前端并行拼装开始重复，再增加只读的 `content_catalog` projection：

- `src/webui/services/content_catalog.py`；
- `src/webui/routes/content_catalog.py`；
- `frontend-v2/src/api/content.ts`。

该 projection 只返回卡片和来源摘要，不拥有任何内容，也不接受写操作。

### P3：创建游戏资料册与兼容性

目标：让用户看懂本局实际会使用哪些资料。

工作内容：

- 逐本选择资料册；
- 显示来源、角色、条目数量和是否共享；
- 正确提交 `book_bindings`；
- 更名“独立世界，引用主册”等容易误解的选项；
- 兼容性失败保留冒险，给出规则、世界、来源等具体操作。

### P4：自制内容包导出

不要继续扩大 `plugins.py` 的旧导出函数。

新增职责分离：

```text
src/content_modules/packaging.py
    纯内容包文件和 manifest 组合

src/webui/services/content_pack_export.py
    owner 权限、选择内容、调用组合器、返回下载

PluginHost
    .dfplugin 打包、安装和生命周期
```

内容包中使用现有 `adventure_packages` 声明冒险目录，使用已有 runtime 和 catalog 校验。世界、资料册、冒险和资源保持独立引用。

### P5：导入入口整理

- `.dfplugin` → 内容来源；
- Markdown/TXT/Foundry/Fantasy Grounds/PDF → 冒险工作台；
- 普通文本拆分 → 创作工具；
- 新外部格式 → adapter registry，不在 View 中增加后缀分支。

### P6：兼容、测试和清理

- 旧路由继续可访问；
- 旧 canonical IDs 不改变；
- 旧内容包继续按旧 delivery mode 工作；
- 删除错误的“自动自由开局”文案；
- 补齐安装、导入、发布、开局和存档恢复的端到端测试。

## 11. 测试与验收矩阵

| 场景 | 预期结果 |
| --- | --- |
| 内容包包含三个冒险 | 内容来源显示一个包，内容库显示三个冒险 |
| 内容包只有资源 | 可以查看和复制资源，没有“开始冒险” |
| 从包内冒险开局 | 预选来源、版本、规则要求和推荐世界 |
| 固定世界不匹配 | 提示切换推荐世界，不丢失冒险选择 |
| 可携带冒险 | 可以换世界，并提示需要适配背景 |
| 缺少规则运行时 | 提示安装或选择兼容规则 |
| 导入 PDF | 生成 GM 草稿，不执行文件内容，不自动生成规则数值 |
| 文本分配 | 能分别保存到世界、资料册、角色模板或冒险 |
| 选择多本资料册 | 创建请求携带明确的 `book_bindings` |
| 共享引用主资料册 | 来源资料变化时界面明确提示共享关系 |
| 已绑定存档的模组 | 更新、禁用、卸载受到服务端保护 |
| 自制冒险导出 | 能打包成 `.dfplugin`，重新安装后仍能发现冒险 |
| 编辑已绑定冒险 | 创建新版本或副本，不覆盖存档绑定版本 |

建议复用和扩展现有测试：

- 前端：`ContentLibraryView.test.ts`、`AdventuresView.test.ts`、`ModulesView.test.ts`、`ModuleDetailView.test.ts`、创建页测试；
- 后端：`test_adventure_script_import.py`、`test_external_adventure_import.py`、`test_content_text_review.py`、`test_validate_content_module.py`、`test_game_content_bindings.py`、`test_game_adventure_projection.py`；

## 12. 本轮实现状态

本轮已经把方案中影响用户理解和实际开局的链路接通；仍保留的工作只属于进一步的结构拆分和端到端覆盖，不是产品功能空缺。

| 方案项 | 当前实现 | 落点 |
| --- | --- | --- |
| 内容库中性入口 | 已完成 | `frontend-v2/src/features/library/ContentLibraryView.vue`、`hubCopy.ts` |
| 首页浏览内容不默认进入冒险 | 已完成 | `SectionWorkspaceShell.vue` 保留 `/library` canonical route |
| 冒险工作台规则无关 | 已完成 | `features/admin/AdventuresView.vue` 移除固定 `rule_id` 查询 |
| 工作台图结构与结构化编辑 owner | 已完成渐进拆分 | `features/admin/adventureEditorGraph.ts`、`useAdventureWorkbenchEditor.ts`；列表/导入/保存仍由 `/adventures` 编排 |
| 本局多资料册选择 | 已完成 | `WorldLorebookSummary.vue`、`CreateView.vue`、`createPayload.ts` |
| 草稿保存资料册选择 | 已完成 | `createDraft.ts` 与 `CreateView.vue` |
| 冒险兼容引导 | 已完成 | `CreateView.vue`、冒险启动文案和来源工作台入口 |
| `.dfplugin` 安装与内容来源生命周期 | 已保留并接通 | `/modules` 与既有 PluginHost |
| 剧本/外部模组导入 | 已有独立 owner | `/adventures` 的导入对话框；外部文本先成为 GM 草稿，不自动变规则 |
| 文本整理与分配 | 已有独立 owner | `/creator` 的 `ContentTextReviewDialog.vue`，可分到世界、资料册、角色模板或冒险 |
| 自制完整内容包组装器 | 已完成 | `CreatorPackageExportDialog.vue` → `/plugins/export` → `content_pack_export.py` |
| 冒险来源保持 | 已完成 | 导出支持明确 `adventure_refs`；旧 ID 只有来源唯一时才接受 |
| 选包后安装并重新发现冒险 | 已完成并有回归测试 | `tests/test_plugin_content_pack.py` |
| 内容库/导出器读取全部资料册 | 已完成并有回归测试 | `/lorebooks?scope=all`、`lorebooks.list_lorebooks(include_all=True)` |

这一轮没有新增存档 schema、第二套 registry 或任意文档转 mechanics 的隐式转换。

## 13. 防止屎山的施工规则

1. 不创建一个同时负责内容目录、安装、编辑和创建游戏的万能 `ContentView`。
2. 不把内容包生命周期移到冒险服务。
3. 不把冒险编辑逻辑移到内容库。
4. 不把冒险导出分支继续堆进旧的世界/角色包导出函数。
5. 不新增第二套 Module store、Adventure registry 或 Lorebook binding。
6. 不用展示名称判断身份，继续使用 canonical ID、来源和版本。
7. 不让前端取代服务端做最终兼容性判断。
8. 不让兼容失败自动切换为自由游戏。
9. 不把外部文本直接当作可执行规则。
10. 新行为优先通过现有 adapter、registry、projection 和 public facade 扩展。
11. UI 拆分优先于业务重写；先抽 owner，再改视觉。
12. 每个施工 PR 只解决一个边界：入口、工作台、创建游戏、导出包、导入工具分开提交。

## 14. 本轮施工范围与边界

本轮按“一把到位”的产品链路完成了入口、资料册和兼容性所需的可运行改动：

- 内容库改成中性内容入口，默认展示世界、冒险和内容来源；
- 内容来源继续负责 `.dfplugin` 安装和生命周期；
- 冒险工作台保留 `/adventures`，负责新建、编辑、剧本/外部模组导入、文本审核和导出；
- 创建游戏允许逐本选择本局资料册，并提交既有 `book_bindings`；
- 兼容失败给出规则、世界、来源的调整入口，用户明确选择后才可放弃冒险包；
- 首页“浏览内容”进入中性 `/library`，不把泛文字游戏默认导向冒险。

本轮刻意不改存档 schema、模组安装协议、规则运行时和外部格式解析器；冒险导出已经移到独立的 `content_pack_export.py`，旧 `plugins.py` 只做兼容转发。后续施工只做工作台子面板的渐进拆分和端到端覆盖，复用已经稳定的 route、ContentRef、来源字段和绑定契约。

## 15. 与未来 main 和拆分 PR 的关系

### 15.1 这次盘查得到的结论

盘查基准是当前 `codex/vnext-integrated-baseline`，HEAD 为 `2fe3b762`。当前工作区存在伙伴的未提交 UI 重建改动，因此不能在这个工作区直接做 merge、rebase 或 reset。

拆分 PR 实际分成两组，不能混为一谈：

| 拆分组 | 当前状态 | 对本方案的影响 |
| --- | --- | --- |
| `module/*`、`api/*`、`adv2/*`、`life/*`、`dndmod/*`、`ui/*`、`doc/*` | 这些分支的提交已经是当前 HEAD 的祖先，内容/冒险的公开契约已经在当前整合分支中 | 可以按当前公开契约继续做功能；未来 main 合入同一组 PR 后，原则上只需做常规回归，不应重写内容模型 |
| `codex/be-201-game-package-dependencies`、`codex/be-202-game-query-projections`、`codex/webapi-domain-services` 等后端职责拆分 | 这些分支仍从旧共同基线 `fa6524d9` 分出，不是当前 HEAD 的祖先；模拟合并已出现大量冲突 | 不能直接 cherry-pick 到当前功能分支；功能开发必须避开它们正在搬迁的文件，未来只在组合层接一次适配 |

因此，“能不能移植”取决于功能是否依赖了后端拆分中的内部文件。只要新功能落在下面的稳定边界内，未来 main 合并伙伴 PR 后可以移植；如果继续向巨型 `WebAPI`、旧游戏服务或通用规则运行时里堆逻辑，后续就会把功能和拆分冲突绑在一起。

### 15.2 当前开发的文件边界

| 区域 | 当前策略 | 原因 |
| --- | --- | --- |
| `frontend-v2/src/features/library`、`creator`、`adventures`、`create`、`modules` | 允许新增页面、叶子组件、文案和测试；保留现有路由名与 API 类型 | 这些是产品职责边界，未来 UI 拆分只需要调整组装关系 |
| `src/webui/services/adventures.py`、`modules.py`、`adventure_script_import.py`、`content_text_review.py` | 允许增加内容能力，但使用显式依赖和既有 public facade | 这些是内容领域 owner，不应从路由反向读取内部状态 |
| `src/webui/api.py`、`src/webui/routes/games.py`、`src/webui/services/games.py`、`src/webui/services/game_packages.py`、`src/engine/game_instance.py` | 冻结为适配区；新功能不在这里增加业务分支 | 这些文件正是 be-201/be-202/WebAPI service 拆分的冲突中心 |
| `src/rulesets/contracts.py`、`src/rulesets/dnd2024/runtime.py` | 不为内容页面增加具体规则分支；只调用 capability/compatibility contract | 防止特定规则集反向污染通用引擎 |
| 旧 `src/webui/services/plugins.py` 的大型导出函数 | 冒险包逻辑已经迁到 `src/webui/services/content_pack_export.py`，旧入口只做兼容转发 | 避免内容包、插件生命周期和冒险导出重新耦合 |

新增代码遵守三条可移植原则：

1. 页面调用公开 API 类型和领域服务，不调用 `api._*`、`registry._*` 等私有成员。
2. 游戏创建只提交明确的 `world_id`、`book_bindings`、`adventure_id`、规则集和来源版本；不依赖“当前页面恰好选中的对象”。
3. 未来拆分只能改变依赖注入和路由组装，不能改变 canonical ID、绑定字段和返回对象的语义。

### 15.3 推荐的移植流程

1. 在当前脏工作区之外创建干净工作区，保留现有改动作为参考，不在原地 merge。
2. 先做 P0/P1 的页面职责与冒险工作台拆分。新文件尽量放在 feature 目录，后端只接现有 public facade。
3. 后端拆分 PR 合入未来 main 后，从新的 main 创建干净工作区，把功能提交逐个 cherry-pick 或按文件应用；不要把旧后端分支反向合回功能分支。
4. 若有冲突，只允许集中在路由注册、依赖注入、服务构造和 import 路径。内容规则、绑定字段和 UI 路由应保持不变。
5. 运行内容边界、冒险导入、游戏创建、模块校验和前端页面测试；再检查 API 响应中的 ID、来源、版本和 `book_bindings` 没有变化。

成功的移植应当表现为“一次小的适配提交”，而不是重做整套内容功能。若移植需要修改 `src/webui/api.py`、`src/webui/services/games.py` 或规则运行时的大段业务逻辑，说明功能越过了稳定边界，应先拆回内容领域服务或前端 feature owner。

### 15.4 对第一批施工顺序的调整

入口、资料册选择和兼容引导已经在本轮合并为一组可验证改动。未来 main 合并伙伴 PR 后，剩余施工顺序是：

1. 抽 `AdventuresView.vue` 的展示、创建、编辑、导入和审核子组件，路由仍保持 `/adventures`。
2. 把自制完整内容包的选择、引用检查、manifest 组合和导出放进独立 packaging owner；旧插件导出只保留兼容转发。
3. 用端到端测试覆盖“安装内容包 → 选择包内冒险 → 选择资料册 → 创建游戏 → 恢复存档”。

即使伙伴的后端职责拆分延后、改名或重新解决冲突，本轮前端能力也只依赖公开响应和稳定绑定字段，移植时只需在组合层重新接线。
