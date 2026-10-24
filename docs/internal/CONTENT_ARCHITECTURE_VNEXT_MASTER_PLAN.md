# DiceFrame Content Architecture vNext

## World / Lorebook / Adventure / Module / Content Pack 全盘重构母方案

> 基线：`diceframe/diceframe` `main`，审计时 HEAD
> `5638c4827bd44daf07a792597be82fa7f4ad1b6c`（Lorebook v2 #398
> 已合并）。
>
> 本方案不是"补 World 编辑页"或"修 CreateView"的局部施工单，而是把
> **World、Lorebook、Adventure、Game、Module、Content Pack、Plugin
> Catalog、Import/Export、Character
> Card、地图/NPC/谜题投影、运行时检索、旧数据迁移**
> 放在同一内容架构下重新确定 authority、ownership、reference
> 与生命周期。
>
> 核心原则：**兼容只允许存在于输入迁移/适配边界；新业务代码不得继续携带
> `World = Lorebook` 的旧语义。**

------------------------------------------------------------------------

# 0. 审计范围与结论

本次先对仓库完整 tree 做了递归盘点：约 2282 个 tree entries；随后以
`world_id`、`book_id`、`starter_lorebook`、`list_entries`、`create_world`、`world_template`、`lorebook`、`binding`、`adventure_binding`、`world_seed`、`content_profile`、`content_delivery_mode`、`target_world_id`、`source_world_id`、import/export
等作为交叉索引，追踪到前端、WebAPI、service、store、migration、plugin
host、module、adventure、runtime projection、tests 和模板。

本方案覆盖的是**所有与这套内容模型发生依赖关系的代码路径**。AI
provider、网络、TTS、支付等与内容 ownership
无关的模块不应为了本次重构被修改。

结论不是"Lorebook v2 做错了"，而是：

1.  Lorebook v2 的 canonical `Book → Entry + Binding` 已经建立。
2.  旧 World 内容模型仍以
    `World → starter_lorebook[] / list_entries(world_id)` 运行。
3.  Create Game 仍通过复制/派生 World 来实现"换世界书"。
4.  Content Pack exporter 仍把 World 的 Entry 回塞为
    `world_template.starter_lorebook`。
5.  Plugin legacy autoimport 仍把 world template / NPC / item / spell /
    class 灌入 World 主世界书。
6.  Module 新架构已经开始使用 `catalog`，但底层内容类型仍继承旧 world
    template 结构。
7.  Narrative Lore Retrieval 已经按 Binding 解析多个 Book，但
    NPC、谜题、World Preview、部分地图/内容投影仍直接
    `list_entries(world_id)`，形成**运行时语义分裂**。
8.  World 本身没有完整 CRUD/editor，导致 Lorebook 页和 Create 页被迫承担
    World 管理职责。
9.  Import/Export、Character Card、Tavern Card、Content Pack、Module
    各有自己的"内容落地"逻辑，缺少统一 Content Draft / Commit Plan。
10. 继续局部兼容会让 `world_id` compatibility projection
    永久渗透到新业务。

因此必须先确定母模型，再拆 PR。

------------------------------------------------------------------------

# 1. 当前真实模型：三套体系叠加

## 1.1 Lorebook v2 canonical

当前已有：

``` text
World
Lorebook
LorebookBinding
LorebookEntry
LorebookEmbedding
```

canonical runtime 已支持：

``` text
Global ───────┐
World ────────┤
Game ─────────┼─ Binding → Book → Entries
Character ────┘
```

`src/lorebook/resolver.py` 会根据当前 instance 解析 active Books。

`src/lorebook/retrieval.py::ensure_lore_context()` 已经会：

``` text
resolve_active_books()
→ list_book_entries(book_id)
→ 合并多个 Book
→ keyword / semantic / visibility / budget / timed activation
```

这是新模型正确的 runtime authority。

## 1.2 旧 World-Lorebook 模型

仍大量存在：

``` text
World
└── starter_lorebook[]
```

持久层兼容：

``` text
LorebookEntry
├── book_id       # canonical
└── world_id      # legacy compatibility projection
```

`LorebookStore.list_entries(world_id)` 实际会找到：

``` text
primary_world_book_id(world_id) == "world:<world_id>"
```

然后读取 Primary Book。

问题不在这个 facade 本身，而在于大量新业务仍把这个 facade 当 canonical
API 使用。

## 1.3 Module / Content Pack 模型

当前 manifest 已有：

``` text
plugin_type: content-pack

content_profile:
- content-pack
- adventure-module

content_delivery_mode:
- legacy_autoimport
- catalog
```

其中 `adventure-module MUST catalog` 是正确方向。

但 legacy content-pack 仍执行：

``` text
启用
→ sync_plugin_lorebooks
→ 创建 World
→ starter_lorebook 写入 World
→ NPC/item/spell/class 再写入 target_world_id
```

因此 Package 层仍然建立在旧 World-Lorebook ownership 上。

------------------------------------------------------------------------

# 2. 当前代码中的架构冲突清单

## 2.1 Store 层

文件：

-   `src/lorebook/models.py`
-   `src/lorebook/store.py`
-   `src/migrations/lorebook.py`

现状：

``` text
lorebook_entries.book_id   = canonical ownership
lorebook_entries.world_id  = compatibility projection
```

但：

-   `add_entry()` 仍允许通过 `world_id` 隐式决定 Book。
-   `list_entries(world_id)` / `search_entries(world_id)` 仍是一等
    public service API。
-   `delete_world_cascade()` 仍会按 Entry `world_id` 做删除。
-   Primary Book 使用特殊 ID `world:<world_id>`。
-   `create_world()` 自动 ensure primary Book。
-   `delete_world()` 同时删 Primary Book。

这说明 DB 已经迁到 v2，但 service contract 还没完全迁。

### 目标

Store 内部最终只允许：

``` text
Entry ownership = book_id
Binding ownership = book_id + scope
World ownership ≠ Entry ownership
```

`world_id` 不再是 Entry canonical 字段。

------------------------------------------------------------------------

# 3. World 的最终定义

## 3.1 World 是内容对象，不是 Lorebook 容器

Canonical：

``` text
World
├── id
├── name
├── description
├── language
├── default_rule / recommended_rules
├── scene defaults
├── map defaults / refs
├── GM style defaults
├── metadata / provenance
└── primary_lorebook_ref   # reference，不是 entries[]
```

World 的职责：

-   描述"这是哪个世界/设定"
-   给新 Game 提供默认规则、场景、地图、GM 风格
-   指向一个 Primary Lorebook
-   可被 Adventure 推荐/约束
-   可被 Content Package 分发

World 不负责：

-   直接拥有 Entry
-   保存当前 Game 的事实
-   保存剧情进度
-   保存 Memory
-   保存 Adventure graph

## 3.2 World Template

当前 `templates/worlds/*.json` 的 `starter_lorebook[]` 必须退出
canonical schema。

新 schema：

``` json
{
  "world_schema_version": 3,
  "world_id": "arkham",
  "world_name": "Arkham",
  "language": "zh-CN",
  "default_rule": "freeform_coc",
  "primary_lorebook_ref": "core:arkham-lore",
  "scene_image": {},
  "gm_style": {}
}
```

Book 独立：

``` text
templates/lorebooks/arkham-lore.json
```

旧 v1/v2 World Template：

``` text
starter_lorebook[]
```

只由 Legacy World Adapter 在加载/迁移时转换：

``` text
legacy WorldTemplate
→ WorldDraft
→ LorebookDraft
→ primary_lorebook_ref
```

新 service/runtime 不再读取 `starter_lorebook`。

------------------------------------------------------------------------

# 4. Lorebook 的最终定义

``` text
Lorebook
├── id
├── name
├── description
├── language
├── enabled
├── retrieval settings
├── provenance
└── Entries[]
```

Entry：

``` text
LorebookEntry
├── id
├── book_id          # 唯一 ownership
├── type
├── content
├── activation controls
├── visibility
├── recursion/timing/vector controls
└── provenance
```

### 必须废除的新业务写法

``` python
add_entry({"world_id": world_id, ...})
list_entries(world_id)
search_entries(world_id)
```

### 新业务只能使用

``` python
save_book_entry(book_id, ...)
list_book_entries(book_id)
search_book_entries(book_id, ...)
```

或者更高层：

``` python
resolve_content(context)
```

------------------------------------------------------------------------

# 5. Binding 的最终定义

Binding 是**使用关系**，不是 Book 内容的一部分。

``` text
Binding
├── book_id
├── scope_kind
│   ├── global
│   ├── world
│   ├── game
│   └── character
├── scope_id
├── role
├── enabled
└── order
```

World 的 `primary_lorebook_ref` 与 World-primary Binding 应保持一个
canonical invariant：

``` text
World.primary_lorebook_ref == Book.id
+
Binding(book_id, scope_kind=world, scope_id=World.id, role=primary)
```

不能让这两份关系独立漂移。

建议由一个 application service 原子维护：

``` text
WorldContentService.set_primary_lorebook(...)
```

------------------------------------------------------------------------

# 6. WorldState 与 World 内容必须彻底分名

仓库已有 World Runtime：

``` text
src/engine/world/*
src/engine/world_state.py
src/engine/world_events.py
src/engine/world_legality.py
```

它是**当前游戏事实 authority**。

因此必须明确：

``` text
World Definition
= 可复用静态内容

WorldState
= 某个 Game 当前发生后的权威事实
```

例如：

``` text
World Definition:
桥最初存在

WorldState:
桥已在第 12 回合被炸毁
```

Lorebook 不覆盖 WorldState。

Adventure 的 `world_seed` 也不是修改 World Definition，而是：

``` text
Adventure Definition
→ materialization adapter
→ World Ops
→ Game WorldState
```

现有 `src/adventures/materialization.py` 和
`src/webui/services/adventure_materialization.py` 的方向应保留。

------------------------------------------------------------------------

# 7. Adventure 的最终定义

Adventure 是独立内容对象：

``` text
Adventure
├── id
├── version / digest
├── required runtime
├── world_policy
├── recommended_world_ref
├── graph
├── encounters
├── world_seed
├── content_refs
└── assets
```

不要把 Lorebook 塞进 Adventure graph。

Adventure 需要 Lore 时声明 ContentRef：

``` text
required_content:
- lorebook:arkham-core

optional_content:
- lorebook:mythos-expanded
```

创建 Game 时由 application layer 建立 Game bindings。

------------------------------------------------------------------------

# 8. Game 的最终内容绑定

Game 不复制 World，也不复制 Book。

Canonical：

``` text
Game
├── world_ref
├── ruleset_ref
├── adventure_binding?   # source-aware + digest
├── game content bindings
├── character bindings
└── WorldState / Memory / runtime state
```

创建游戏：

``` text
选择 World
→ 引用 World

选择额外 Lorebook
→ 创建 game-scoped Binding

选择 Adventure
→ 保存 Adventure binding
→ 解析 required content
→ 建立相应 game-scoped Binding
→ materialize world_seed into WorldState
```

### 必须删除的创建语义

当前：

``` text
_blank_<timestamp>
_copy_<timestamp>
source_world_id
lorebook_world_id
create_lorebook
blank_lorebook
copy_lorebook_entries()
```

这些不应该继续进入新创建流程。

------------------------------------------------------------------------

# 9. CreateView 重做

当前 `CreateView.vue` 把：

``` text
World Template
Lorebook Source
Adventure
```

混成一个选择链。

新流程：

``` text
Step 1 世界与规则
  内容语言
  世界
  规则

Step 2 剧情
  自由游戏
  或 Adventure

Step 3 游戏资料
  自动启用：
    World Primary Lorebook
    Adventure Required Lorebooks

  可选：
    其他兼容 Lorebooks

Step 4 角色
Step 5 确认
```

"空白世界书"不再通过复制一个空 World 实现。

如果用户要：

``` text
World = Arkham
但不使用 Arkham Primary Lore
```

应表达成：

``` text
World 保持 Arkham
primary world binding 对本 Game 被 override/disabled
```

或产品上提供：

``` text
资料模式：
● 使用世界默认资料
○ 不使用世界默认资料
○ 自定义资料组合
```

具体 override 机制必须在母方案阶段定成一个 canonical
contract，不能继续制造临时 World。

------------------------------------------------------------------------

# 10. World 编辑器

`内容 → 世界` 应成为唯一 World 管理中心。

需要完整 CRUD：

``` text
基本信息
- 名称
- 描述
- 内容语言
- 默认规则
- 推荐规则
- 封面/场景

GM 默认
- style

地图/场景引用

世界资料
- Primary Lorebook
- [前往世界书]
```

内置/插件 Catalog World：

``` text
read-only
→ 克隆为“我的世界”
→ 才允许编辑
```

世界书页面不再承担 World CRUD，只保留 World context selector。

------------------------------------------------------------------------

# 11. Lorebook 编辑器

`内容 → 世界书`：

``` text
当前上下文世界 [World ▼]

当前世界书 [Book ▼]
状态
使用范围
新建
重命名
启停
导入
导出
Entry CRUD
Activation Inspector
Visibility Inspector
```

World selector 只用于筛选/上下文，不拥有 Book。

删除永久 Books Sidebar 的重复导航方案仍成立。

------------------------------------------------------------------------

# 12. Import：统一 Content Draft 管线

当前存在多套入口：

-   Lorebook v3 import
-   DiceFrame native lorebook import
-   SillyTavern
-   Character Card embedded book
-   Tavern NPC import
-   Plugin content import
-   Content Pack install
-   Module import
-   Adventure import
-   World clone
-   AI generated world

必须统一到：

``` text
Input
 ↓
Format Adapter
 ↓
ContentDraft
 ↓
Validation
 ↓
Preview
 ↓
Reference Resolution
 ↓
CommitPlan
 ↓
Canonical Stores
```

建议：

``` text
ContentDraft
├── worlds[]
├── lorebooks[]
├── adventures[]
├── characters[]
├── ruleset_content[]
├── maps[]
├── assets[]
├── refs[]
├── provenance[]
├── warnings[]
└── unsupported[]
```

`CommitPlan` 明确列出：

``` text
CREATE World ...
CREATE Book ...
BIND Book → World ...
IMPORT 42 Entries ...
REGISTER Adventure ...
COPY Asset ...
```

用户确认后一次事务/可恢复事务执行。

------------------------------------------------------------------------

# 13. Legacy 兼容只能在 Adapter Boundary

允许：

``` text
Legacy world template
starter_lorebook[]
      ↓
LegacyWorldAdapter
      ↓
WorldDraft + LorebookDraft
```

允许：

``` text
旧 content-pack
legacy_autoimport
      ↓
LegacyContentPackAdapter
      ↓
Canonical CommitPlan
```

不允许：

``` text
新 WorldService
if starter_lorebook...

新 CreateService
if source_world_id...

新 PluginService
entry["world_id"] = ...

新 MapService
list_entries(world_id)
```

最终目标：

``` text
Legacy Input → Adapter/Migration → Canonical
                              ↑
                      唯一兼容边界
```

------------------------------------------------------------------------

# 14. Export：与 Import 对称

统一：

``` text
Canonical Selection
 ↓
Reference Closure
 ↓
Portable ContentDraft
 ↓
Exporter
```

### 导出一本 Lorebook

``` text
Book
Entries
portable metadata
```

普通分享格式不携带当前用户的 Game/Character bindings。

Native Backup 可选择携带 bindings。

### 导出 World

默认：

``` text
World
Primary Lorebook
World assets
```

UI 可让用户勾选额外 World-scoped Books。

### 导出 Adventure

``` text
Adventure package
+ 自身声明的 refs
```

默认不复制引用内容；Content Pack exporter 可根据 reference closure
打包依赖。

### 导出 Content Package

选择器应该支持：

``` text
Worlds[]
Lorebooks[]
Adventures[]
Ruleset content[]
Characters[]
Maps/assets[]
```

不再只有：

``` text
world_id + rule_id + cards
```

------------------------------------------------------------------------

# 15. Content Pack / Module 统一模型

不要再把 Module 和 Content Pack 做成两个内容 authority。

统一为：

``` text
Content Package
├── manifest
├── profile
├── delivery mode
└── canonical resources
```

profile：

``` text
content-pack
adventure-module
```

其中：

``` text
content-pack
= 通用资源包

adventure-module
= Adventure 为主要入口的内容包
```

`adventure-module` 保持 `catalog` 强制规则。

Package 内建议：

``` text
content/worlds/
content/lorebooks/
content/characters/
content/npcs/
content/items/
content/spells/
content/classes/
content/maps/
adventures/
ruleset-catalogs/
assets/
```

`world_templates` 不再通过 `starter_lorebook` 携带 Book。

------------------------------------------------------------------------

# 16. Plugin Catalog 的改造

当前：

``` text
PluginContentCatalog.CONTENT_KINDS
= character_template, npc, item, spell, class, rule
```

World template 另走 contribution。

需要新增 canonical：

``` text
lorebook
world
adventure
```

Catalog 只提供**只读内容定义**。

安装/启用 catalog package：

``` text
register sources
```

不得：

``` text
create_world()
add_entry()
save_character_card()
```

用户显式"导入到我的内容"时才生成 CommitPlan。

------------------------------------------------------------------------

# 17. legacy_autoimport 的处理

不能继续保留"宿主到处特殊处理"的长期形态。

迁移策略：

1.  旧 package manifest 仍可识别 `legacy_autoimport`。
2.  安装时进入 `LegacyContentPackAdapter`。
3.  Adapter 产生 canonical `ContentDraft/CommitPlan`。
4.  Commit 时：
    -   World → canonical World
    -   `starter_lorebook` → canonical Book
    -   NPC/item/spell/class → 明确目标 Book 或 Ruleset Catalog
5.  后续运行时只看到 canonical 数据。
6.  Plugin uninstall 不再靠 `source_plugin + world_id` 猜
    ownership，而依据 provenance/import receipt。

------------------------------------------------------------------------

# 18. Provenance 与卸载

当前 plugin cleanup 会：

``` text
delete_entries_by_plugin(plugin_id)
list_plugin_worlds(plugin_id)
delete_world_cascade(...)
```

风险很高，因为"来源"和"ownership"混在一起。

新模型：

``` text
ImportReceipt
├── source_kind
├── source_id
├── source_version
├── source_digest
├── created_objects[]
├── updated_objects[]
└── user_detached_objects[]
```

卸载 catalog package：

``` text
删除 source registration
```

不删除用户数据。

legacy autoimport/显式 materialize：

-   如果对象仍受 package 管理且未 detach，可按 receipt 清理。
-   用户编辑后应明确转为 detached/forked，不能插件卸载时误删。

------------------------------------------------------------------------

# 19. 当前最容易漏掉的运行时分裂：Narrative 已多 Book，其他功能仍 Primary Book

这是本次必须一起修的地方。

## 19.1 Narrative retrieval

已经正确：

``` text
resolve_active_books(instance)
→ 多 Book
```

## 19.2 Puzzle 初始化

当前：

``` text
initialize_puzzles_from_lorebook()
→ list_entries(instance.world_id)
```

只看 Primary Book。

必须改为 Runtime Content Projection：

``` text
resolve_active_entries(instance, type="puzzle", viewer="gm")
```

## 19.3 NPC 选择/管理

`src/webui/services/characters.py`：

``` text
list_entries(inst.world_id, "npc")
```

同样只看 Primary Book。

应改：

``` text
RuntimeContentProjection.npcs(instance)
```

并保留来源 book_id。

## 19.4 World Preview

`src/webui/services/knowledge.py` 当前按 `world_id` preview Primary
Book。

要区分两个产品语义：

``` text
World authoring preview
→ Primary Book / World-scoped selected Books

Game runtime preview
→ resolve_active_books(Game context)
```

不能共用一个含糊 `/api/lorebook/{world_id}/preview`。

## 19.5 Map / Location

Content Pack map exporter 当前从传入的 lore entries 里抽
`type=location`。

以后 Export 必须基于用户选择的 Book closure，而不是"World 所有
entries"。

运行时地图如果需要 Lorebook location，也应明确使用 Runtime Projection 或
World Primary Projection，不能隐式 `world_id`.

------------------------------------------------------------------------

# 20. Runtime Content Projection Service

为了避免每个 service 自己决定"该读哪本 Book"，新增 application-level
read service：

``` text
ContentProjectionService
```

提供：

``` text
for_world_authoring(world_id)
for_game(instance)
for_character(instance, character_uid)
```

以及：

``` text
entries(...)
npcs(...)
locations(...)
puzzles(...)
```

内部唯一调用：

``` text
BindingResolver
LorebookStore.list_book_entries()
Visibility
```

这样：

-   Narrative
-   NPC
-   Puzzle
-   Map
-   Inspector
-   Player-safe knowledge

不会再各自实现一套"世界书是什么"。

注意：它是 read projection，不是第二 authority，不复制 Lore 数据。

------------------------------------------------------------------------

# 21. Character Card / Tavern Card

这块已有一部分正确迁移：

`commit_character_book()` 已经：

``` text
character_book
→ lorebook_v3 adapter
→ canonical Book
→ character/world binding
```

这条保留。

但 Tavern-as-NPC 仍：

``` text
world_id
→ add_entry()
```

要改成显式：

``` text
target_book_id
```

UI 导入 NPC 时必须选择：

``` text
目标世界书 [Book ▼]
```

而不是选择 World 却显示"世界书"。

Character embedded lore：

优先：

``` text
character binding
```

没有 character uid 时： - 不应自动猜 World； -
产品明确让用户选择"绑定到角色 / 世界 / 暂不绑定"。

------------------------------------------------------------------------

# 22. Plugin Content 手动导入

当前前端：

``` text
contentTargetWorldId
selectLorebookTarget
```

这是明显语义错位。

改成：

``` text
contentTargetBookId
```

后端：

``` text
target_book_id
```

NPC / item / spell / class 是否都应该变 Lorebook Entry，需要进一步按
content kind 定义：

-   narrative NPC/location/faction/event → Lorebook
-   ruleset spell/class/item definition → 优先 Ruleset Catalog
-   instance item/NPC → Adventure/World seed 或 runtime state

不能再"一律塞世界书"。

这是 Content Type Registry 要解决的问题。

------------------------------------------------------------------------

# 23. Content Type Registry

建议建立声明式 registry：

``` text
ContentKind
├── world
├── lorebook
├── lorebook_entry
├── adventure
├── character_template
├── npc_definition
├── item_definition
├── spell_definition
├── class_definition
├── map
└── asset
```

每种 kind 声明：

``` text
authority
portable schema
allowed refs
import target
runtime projection
editable?
cloneable?
```

这样 Plugin、Module、Importer、Exporter 不再靠 `if kind == ...` 四处猜。

------------------------------------------------------------------------

# 24. 内容语言

UI locale 与 Content language 继续完全分离。

Canonical supported Locale：

``` text
zh-CN
en
ja
de
```

World、Lorebook、Adventure、Character、Catalog resource 都应显式
language/locale contract。

旧无 language 内容：

``` text
legacy adapter → zh-CN
```

不要在新 canonical service 里继续靠 `normalizeLocale(undefined)` 猜。

------------------------------------------------------------------------

# 25. Built-in Templates

当前 built-in world JSON 和 locale overlay 都把 `starter_lorebook` 当
World schema 一部分。

迁移：

``` text
templates/worlds/
templates/lorebooks/
```

Locale overlay 也拆：

``` text
templates/worlds/locales/...
templates/lorebooks/locales/...
```

World locale 只能覆盖 World display fields。

Lorebook locale 只能覆盖 Book/Entry 可本地化字段。

不要再由 `src/content/worlds.py::localize_lorebook_entries()` 跨对象做
overlay。

------------------------------------------------------------------------

# 26. AI Generate World / AI Generate Lore

当前 `generation/creator.py::generate_world()` 一次生成：

``` text
World + starter_lorebook
```

改为返回：

``` text
GeneratedContentDraft
├── world
└── primary_lorebook
```

commit 时一次创建两种 canonical 对象并建立 primary relation。

`generate_lorebook_entries()` 改为：

``` text
target_book_id
```

不是 `world_id`.

------------------------------------------------------------------------

# 27. API vNext

建议新增 canonical API，不再继续扩旧 URL。

## World

``` text
GET    /api/worlds
POST   /api/worlds
GET    /api/worlds/{world_id}
PUT    /api/worlds/{world_id}
DELETE /api/worlds/{world_id}
POST   /api/worlds/{world_id}/clone
```

## Lorebook

现有 `/api/lorebooks/*` 保留并作为唯一 canonical Book API。

## Content import/export

``` text
POST /api/content/import/preview
POST /api/content/import/commit

POST /api/content/export/preview
POST /api/content/export
```

## Runtime projection

内部 application service 为主；需要 UI 时：

``` text
POST /api/content/runtime-preview
```

必须传明确 context，不允许只传一个 world_id 就假装等于整个 runtime。

------------------------------------------------------------------------

# 28. 旧 API 的退出方式

以下全部标记 legacy：

``` text
GET  /api/lorebook/{world_id}
POST /api/lorebook
POST /api/lorebook/{world_id}/import
POST /api/lorebook/{world_id}/generate
PUT  /api/lorebook/{entry_id}
DELETE /api/lorebook/{entry_id}
```

但不要让它们永久调用新业务中的 world compatibility logic。

迁移阶段：

``` text
legacy route
→ LegacyLorebookFacade
→ resolve primary_book_id once
→ canonical Book service
```

新前端、新插件、新模块、新 service 禁止调用这些 route。

等 internal callers = 0、外部兼容周期完成，再删除。

------------------------------------------------------------------------

# 29. DB Migration vNext

目标 Entry 表：

``` text
lorebook_entries
- id
- book_id NOT NULL
- ...
```

最终删除：

``` text
world_id
```

迁移前 invariant 检查：

``` text
if world_id IS NOT NULL:
  assert book_id == "world:" + world_id
```

不满足则生成 migration report，不能静默修。

World 表增加/明确：

``` text
primary_lorebook_id
default_rule
gm_style / refs（按现有数据落点决定是否表字段或 metadata）
provenance
```

也可避免 DB 双写 primary relation：如果 Primary Binding 是唯一
authority，则 World 只通过 query 得到 primary
Book。两者必须二选一，不要长期双 authority。

本方案建议： **Primary Binding 为关系 authority；World API projection
返回 `primary_lorebook_id`。**

------------------------------------------------------------------------

# 30. 删除语义

## 删除 World

不能再：

``` text
delete_world_cascade(world_id)
```

然后猜哪些 Entry 属于它。

正确：

``` text
World
→ 删除 World scope bindings
→ Primary Book：
   如果 role=primary 且 ownership=world-owned：
      删除 Book + Entries
   如果是 shared Book：
      仅解绑
→ 检查 Game references
→ 删除/阻断
```

必须支持共享 Book，不应因为删 World 就删掉独立 Book。

## 删除 Book

-   被 World primary 使用：阻断，要求先换 Primary。
-   被 Game/Character 使用：显示引用并确认解绑策略。
-   删除 Book → Entries cascade。

------------------------------------------------------------------------

# 31. Reference Integrity

统一 ContentRef：

``` text
world:<id>
lorebook:<id>
adventure:<id>
character:<id>
module:<id>
...
```

不要继续让：

``` text
world_id
book_id
source_world_id
recommended_world_id
target_world_id
```

在不同域里靠字段名隐式表达引用语义。

内部 Python/TS 类型要使用 typed ref / explicit fields。

------------------------------------------------------------------------

# 32. Source Identity 与 ID 冲突

Package 内容必须拥有：

``` text
source_kind
source_id
source_version
source_digest
external_id
```

Import 时本地 canonical id 与 external id 分离。

禁止继续依赖：

``` text
f"{world_id}_{entry_id}"
f"{world_id}_plugin_{plugin_id}_..."
```

作为 provenance/ownership 机制。

------------------------------------------------------------------------

# 33. Modules 页面

Module Detail 目前显示：

``` text
metadata
compatibility
adventures
content count
usage
actions
```

结构可保留。

但 Content Tab 应展示 canonical kinds：

``` text
Worlds
Lorebooks
Adventures
Characters
Ruleset Content
Maps/Assets
```

Catalog module 内容默认只读。

"导入到我的内容"走统一 Content Import Preview。

------------------------------------------------------------------------

# 34. Content Pack Export UI

当前 ExportPackModal 的核心选择是：

``` text
World
Rule
Characters
Map/assets
```

改成 resource graph selector：

``` text
世界
  ☑ Arkham

世界书
  ☑ Arkham Core（World Primary，自动）
  ☐ Mythos Expanded

冒险
  ☑ Arkham Mystery

规则内容
  ...

角色
  ...

资源
  ...
```

显示依赖：

``` text
Arkham → Arkham Core
Arkham Mystery → Arkham
Arkham Mystery → Mythos Expanded
```

用户可以理解为什么某资源被自动选中。

------------------------------------------------------------------------

# 35. World / Lorebook / Adventure / Module 产品层最终关系

``` text
内容
├── 世界
│   └── 管理静态世界定义
│
├── 世界书
│   └── 管理知识 Book / Entry / 使用范围
│
├── 冒险
│   └── 管理剧情包 / graph / seed / refs
│
└── 模组
    └── 安装、浏览、更新、卸载内容容器
```

"模组"不是第四种 runtime authority。

它只是内容分发来源。

------------------------------------------------------------------------

# 36. 需要改动的主要代码域

## Lorebook / DB

-   `src/lorebook/models.py`
-   `src/lorebook/store.py`
-   `src/lorebook/bootstrap.py`
-   `src/lorebook/importer.py`
-   `src/lorebook/exporter.py`
-   `src/lorebook/resolver.py`
-   `src/lorebook/retrieval.py`
-   `src/migrations/lorebook.py`
-   `src/migrations/lorebook_content.py`

## World content

-   `src/content/worlds.py`
-   `src/webui/services/worlds.py`
-   `src/webui/routes/worlds.py`
-   `src/engine/world_template.py`
-   `src/generation/creator.py`

## Game creation

-   `src/webui/services/game_lifecycle.py`
-   `src/webui/services/game_creation_phases.py`
-   `src/webui/game_lifecycle_context.py`
-   `src/webui/routes/game_lifecycle_routes.py`
-   `frontend-v2/src/features/create/CreateView.vue`

## Runtime projections

-   `src/commands/round_actions.py`
-   `src/webui/services/characters.py`
-   `src/webui/services/knowledge.py`
-   map/location consumers
-   Lorebook inspector/preview callers

## Character/Tavern

-   `src/webui/services/character_cards.py`
-   `src/webui/services/tavern.py`
-   corresponding routes/frontend imports

## Plugin / Content Pack

-   `src/plugin_host/content.py`
-   `src/plugin_host/host.py`
-   `src/plugin_host/support.py`
-   `src/webui/services/plugins.py`
-   `src/webui/services/content_pack_maps.py`
-   plugin routes/API
-   `frontend-v2/src/features/plugins/*`
-   `frontend-v2/src/api/plugins.ts`

## Module

-   `src/webui/services/modules.py`
-   `src/webui/services/module_validation.py`
-   `frontend-v2/src/features/modules/*`
-   `frontend-v2/src/api/modules.ts`

## Adventure

-   `src/adventures/*` 中 refs / manifest / materialization 相关
-   `src/webui/services/adventures.py`
-   `src/webui/services/adventure_materialization.py`
-   `src/webui/services/adventure_runtime.py`
-   `src/webui/services/game_seed_lifecycle.py`

## Frontend content management

-   `frontend-v2/src/features/worlds/WorldsView.vue`
-   `frontend-v2/src/features/lorebook/*`
-   `frontend-v2/src/api/types.ts`
-   i18n messages

## Templates / examples / docs

-   `templates/worlds/*`
-   `templates/worlds/locales/*`
-   新 `templates/lorebooks/*`
-   `plugins/examples/*`
-   architecture/docs/user guides

------------------------------------------------------------------------

# 37. 测试必须一起迁

重点不是"让旧测试继续绿"，而是先把测试分成：

``` text
Canonical Contract Tests
Legacy Adapter Tests
Migration Tests
Product/E2E Tests
```

## Canonical 必测

1.  Entry 必须有 book_id。
2.  新业务无法用 world_id 创建 Entry。
3.  World Primary relation 唯一。
4.  删除 World 不误删 shared Book。
5.  Binding resolver 决定 runtime active Books。
6.  NPC/Puzzle/Map/Knowledge 与 Narrative 使用同一 projection policy。
7.  Game 创建不创建 `_copy_/_blank_` World。
8.  Adventure required lore 建 game binding。
9.  Content Pack roundtrip 不通过 starter_lorebook。
10. Module catalog enable 不写用户 store。

## Legacy Adapter 必测

1.  v2 World `starter_lorebook` → World + Book。
2.  legacy content-pack → canonical CommitPlan。
3.  老 `/api/lorebook/{world_id}` 只作为 primary Book facade。
4.  旧 DB `world_id` entry 正确迁入 `book_id`。
5.  迁移完成后 canonical path 不再读 legacy 字段。

## E2E

-   新建 World → 编辑 → Primary Book → 创建 Game。
-   一个 World + 多 Book。
-   一个 Book 绑定多个 World。
-   World + Adventure + required Book。
-   Character scoped Book。
-   导入 Lorebook。
-   导出 World+Book package → 新实例导入。
-   安装 Module → 创建 Adventure Game → 卸载保护。
-   删除/解绑引用完整性。

------------------------------------------------------------------------

# 38. Architecture Guards

新增 AST / dependency guards，防止以后重新长回去。

建议：

``` text
禁止 src/webui/services 新代码调用 LorebookStore.list_entries(world_id)
禁止 plugin_host 直接 add_entry/create_world 到用户 store
禁止 content_modules 写 World Runtime
禁止 Adventure definition 写 World Runtime（现有 guard 保留）
禁止 canonical Entry service 接收 world_id ownership
禁止新 frontend 调用 legacy /api/lorebook/{world_id}
禁止新 package schema 写 starter_lorebook
```

这些 guard 比文档更重要。

------------------------------------------------------------------------

# 39. 实施顺序：不要按页面拆，按 authority 拆

## PR A --- Canonical Content Contracts

只建立：

-   ContentRef
-   ContentDraft
-   CommitPlan
-   ContentKind registry
-   World/Lorebook canonical service contracts
-   Runtime Content Projection contract
-   architecture guards

不改产品行为。

## PR B --- Store / DB Ownership Cutover

-   Entry canonical `book_id`
-   Primary relation authority
-   新 Book-scoped APIs
-   migration validator
-   canonical deletion semantics
-   provenance/receipt 基础

旧 facade 只留 boundary。

## PR C --- Built-in World + Lorebook Split

-   world schema v3
-   `templates/lorebooks`
-   legacy world adapter
-   locale overlay 拆分
-   AI world generation输出 WorldDraft + LorebookDraft

完成后 canonical built-ins 不再含 starter_lorebook。

## PR D --- Runtime Projection Unification

-   Narrative
-   NPC
-   Puzzle
-   Map/location
-   Knowledge preview
-   player-safe projection

统一走 ContentProjectionService。

这是非常关键的一刀。

## PR E --- Game Creation Cutover

删除新流程中的：

-   `source_world_id`
-   `lorebook_world_id`
-   `create_lorebook`
-   `blank_lorebook`
-   `copy_lorebook_entries`
-   `_blank_`
-   `_copy_`

改为 World ref + Book bindings + Adventure refs。

## PR F --- World Product / Lorebook Product

-   World 完整 editor
-   Lorebook IA 整理
-   World selector / Book selector
-   使用范围
-   i18n
-   4 languages

## PR G --- Unified Import / Export

-   ContentDraft preview
-   CommitPlan
-   reference closure
-   Lorebook / World / Adventure exporters 接统一底层
-   Character/Tavern adapter 接入

## PR H --- Content Pack / Plugin / Module Cutover

-   package canonical resources
-   lorebooks contribution
-   catalog-only new path
-   legacy_autoimport 进入 adapter
-   receipt-based uninstall
-   plugin manual import target_book_id
-   content type routing

## PR I --- Legacy Removal

前提：

``` text
canonical callers = 100%
legacy internal callers = 0
migration telemetry/tests green
```

然后删除：

-   starter_lorebook canonical use
-   Entry.world_id
-   World-scoped Entry CRUD
-   old create copy semantics
-   internal legacy routes
-   obsolete sync paths

------------------------------------------------------------------------

# 40. 不能做的"假重构"

以下方案一律禁止：

### 40.1 只把 `list_entries(world_id)` 包一层

这只是换函数名，ownership 没变。

### 40.2 给 World 增加 Book selector，但 exporter 仍用 starter_lorebook

UI 新、数据旧，问题继续。

### 40.3 CreateView 改 Binding，但 Plugin 仍把内容灌 World

同一 Book 在不同入口拥有不同语义。

### 40.4 保留 Entry.world_id "以防以后用"

只要它存在并可写，就会重新被业务依赖。

### 40.5 每个 service 自己 resolve Books

必须只有一个 projection policy。

### 40.6 Module 再建自己的 Lorebook store

禁止第二 authority。

------------------------------------------------------------------------

# 41. 迁移完成后的关键不变量

最终代码应满足：

``` text
1. World 永远不是 Entry owner。
2. Lorebook Book 是 Entry 唯一 owner。
3. Binding 是 Book 进入 runtime 的唯一 scope 机制。
4. WorldState 是当前事实唯一 authority。
5. Adventure 通过 refs + materialization 与 World/Book 交互。
6. Module/Content Pack 只是 distribution container/source。
7. Plugin Catalog 默认只读，不静默写用户数据。
8. Import 都先 Draft/Preview，再 Commit。
9. Export 都从 canonical graph 做 reference closure。
10. Legacy 只存在 Adapter/Migration boundary。
11. Runtime 所有 Lore-derived feature 共用一个 projection policy。
12. 新 Game 不复制 World 来实现内容组合。
```

------------------------------------------------------------------------

# 42. 对当前 #399 / #400 / #401 的影响

这次母方案会影响原本 #399/#400 后续方向，尤其 #400 的 Module/Lorebook
surface。

建议：

-   已经合并/接近合并且不冲突的 UI 小修可以完成。
-   **不要继续扩展旧 `world_template.starter_lorebook` 或
    `target_world_id → lorebook entry` 能力。**
-   #400 如果仍以旧 World 内容模型设计 Native Module
    Lorebook，先按本母方案重新对齐。
-   #401 Imagegen storyboard 与本架构基本正交，按原最小 CAS scope
    可独立进行。

------------------------------------------------------------------------

# 43. Definition of Done

这次内容架构重构不是"页面看起来正常"就结束。

必须达到：

``` text
grep/search:
新业务中 starter_lorebook = 0
新业务中 Entry world_id ownership = 0
新前端 legacy lorebook world routes = 0
Create Game copy/blank World lore path = 0
Plugin manual lore import target_world_id = 0
Runtime NPC/Puzzle/Knowledge 直接 list_entries(world_id) = 0
```

允许残留的位置只有：

``` text
legacy adapters
DB migrations
legacy route facade
legacy fixtures/tests
migration docs
```

并且每个残留都要带明确 `LEGACY_BOUNDARY` 标记和删除条件。

最终跑：

-   backend full pytest
-   architecture guards
-   migration tests
-   import/export roundtrip
-   plugin/module lifecycle
-   adventure lifecycle
-   frontend unit tests
-   vue-tsc
-   lint
-   production build
-   Lorebook E2E
-   World E2E
-   Create Game E2E
-   Module E2E
-   responsive E2E

------------------------------------------------------------------------

# 44. 最终产品心智模型

用户只需要理解：

``` text
世界
= 我在哪里玩

世界书
= AI 关于这个世界/角色/游戏可以使用哪些资料

冒险
= 这次玩的剧情内容

模组
= 我安装的一包可复用内容

存档
= 这一次实际发生了什么
```

而代码内部对应：

``` text
World Definition
Lorebook + Binding
Adventure Definition
Content Package Source
Game + WorldState + Memory
```

这五层不再互相冒充。

------------------------------------------------------------------------

# 45. 最终建议

**不要再先做 World 编辑页，也不要先修 CreateView。**

第一步应先落地 `PR A — Canonical Content Contracts`，把本方案中的
authority、ContentRef、ContentDraft、CommitPlan、Projection policy 和
architecture guards 固定下来。

否则任何页面或 service 先改，都会再次把未定型的关系写死进代码。

这次应当先把"内容是什么、谁拥有谁、谁引用谁、谁能写谁、怎么导入导出、怎么进入
runtime"一次定死，再从底向上迁移。

------------------------------------------------------------------------

# 46. 增补：跨软件互通与交换标准（Interop Track）

> 2026-10-19 grill 定稿增补，未施工。
>
> 优先级：恋爱养成轨道 MVP 先行（见 `ROMANCE_SIM_GAMEPLAY_DESIGN.md`），
> 本轨道代码随后；标准草案（纯规范文本）可立即并行启动，不占开发排期。
>
> 本增补不修改 §1–§45 的任何既有决策，只在其上新增互通层。

## 46.1 定位与边界

世界书 canonical 模型（§4/§5）、统一 Import 管线（§12）、Export 对称
（§14）、Provenance（§18/§32）全部原样复用。本增补只新增两件事：

``` text
层1  diceframe ↔ diceframe
     自包含存档包：内容随存档走，跨实例导入即完整恢复

层2  diceframe ↔ 其他跑团软件
     中立开放交换标准：内容与叙事进度可交换，机制状态永不交换
```

明确不在本增补范围内：

``` text
跨引擎规则运行时标准（不做）
跨存档共享活世界（一个 Game 的事实影响另一个 Game，不做）
```

## 46.2 层1：自包含存档包

当前存档 ZIP 只携带引用（`world_id` / `book_refs`），世界书内容本体在
全局 `lorebook.db`，换实例导入后引用悬空。层1 升级为自包含：

``` text
Save Package
├── manifest                # schema_version + 内容清单 + digest
├── 既有存档载荷             # state.json / chatlog.jsonl / assets（不变）
├── World Definition
├── Primary Lorebook
├── game-scoped Books
├── character Books
└── 冒险绑定引用的 Adventure 定义
```

规则：

1.  **带书范围** = World Primary Book + 本局 game binding 的 Book +
    character binding 的 Book，按 reference closure 一次收齐（§14）。
2.  **embeddings 不随包**：向量与生成模型绑定，跨实例无意义；导入后由
    本机模型重建。
3.  **导入去重**：按 `source_digest` 记 ImportReceipt（§18）。同名世界
    一律不覆盖本地数据，新建副本并记 provenance；替换必须用户显式确认。
4.  **版本 fail-closed**：manifest 带版本号，未知版本直接拒绝，不猜测
    解析（与迁移铁律一致）。
5.  **identity rebind 语义不变**：导入仍是新 run 身份，不是延续原局；
    本增补改变的是「内容引用是否随包完整」，不改变 run identity。

## 46.3 层2：交换范围与保真度

层2 交换标准包携带：

``` text
World
Lorebook（对齐酒馆 world info 超集）
Character Card（直接采纳 chara_card_v3）
Adventure（结构化文档）
Narrative Progress（新定义，见下）
```

Narrative Progress = 叙事状态的可序列化投影：

``` text
故事摘要 / 记忆事实
世界事实（WorldState facts 投影）
NPC 关系（relationships 文本投影）
聊天记录（可选）
冒险进度标记
```

硬边界：

``` text
机制状态（检定/经济/ruleset_runtime/模块槽）
→ 只存在于层1，永不进入层2

Narrative Progress
→ 是投影，不是 WorldState 的第二 authority
→ 导入侧由各软件自行物化为自己的运行时事实
```

保真度承诺为**双向有损**：映射不上的内容进 `unsupported[]` /
`warnings[]`（§12），绝不静默丢弃；明确不承诺 round-trip 无损。

## 46.4 交换标准与治理

战略：**标准中立开放，生态位靠实现占领。**

``` text
标准   → 独立仓库、中立名字、开放许可、多方共建
       → 不冠 diceframe 名，不作为 diceframe 私有格式

diceframe 扮演
├── 参考实现（第一个完整实现）
├── 转换枢纽（主流格式都能进、都能出）
└── 创作端（内容长在我们的格式里）
```

拼装不发明。真正新定义的只有两块，其余采纳现成事实标准：

``` text
新定义：Package Manifest（怎么打包）
新定义：Narrative Progress 节
复用  ：chara_card_v3 / 酒馆 world info 超集
```

治理与启动：

1.  **草案先行**：先出规范草案 + 参考实现（我们的导出直接按草案格式
    出），拿能跑的东西招募伙伴；不从零开筹备组。
2.  **版本策略**：v1 冻结后破坏性变更只能升 major；diceframe 承诺读取
    所有已发布 major 版本。
3.  **降级条款**：草案发布后六个月内无第二家实现，自动降级为 diceframe
    导出格式继续服务层1/层2，工程不白做。
4.  **共建对象**：缺世界书格式的国内跑团平台。酒馆不是共建对象——它有
    自己的格式，属于适配器互通（先行打磨目标，见 PR L）。

## 46.5 容器合一

一个容器，不维护两套格式：

``` text
diceframe 存档包（层1）
= 交换标准包（层2 形状）
+ x-diceframe/ 命名空间私有扩展节（机制状态放这里）
```

标准本身规定：**未知命名空间节必须忽略，不得报错**（向前兼容）；
未知标准 major 版本必须拒绝（fail-closed）。

因此每个 diceframe 存档天然就是一个合法的标准包——每次用户导出存档，
都是标准的一份活样本，直接服务 46.4 的生态位战略。

## 46.6 世界书显式收录（Explicit Curation）

本增补对世界书运行时唯一的新能力：

``` text
打完一局
→ GM 发起「收录本局新设定」
→ 系统列出候选（本局 WorldState facts / Memory facts 投影）
→ GM 逐条挑选
→ CommitPlan 入库为世界书条目，provenance 记 source game run
```

禁止运行时自动写回共享书：本局运行时发现的事实归 WorldState /
Memory（§6 不变）；收录是显式作者行为，防止一局的幻觉污染所有存档与
导出包。

恋爱轨道的条件注入（ROMANCE_SIM §3.E）归恋爱轨道自己的 PR，不算本
轨道工作量。

## 46.7 实施顺序：PR J / K / L

## PR J --- 层1 自包含存档包

-   Save Package manifest + 内容 closure（46.2 全部规则）
-   ImportReceipt 去重与不覆盖导入
-   fail-closed 版本门
-   embeddings 重建策略
-   包体积预算核验（现 50MB 上限是否需要按内容 closure 调整）

依赖：PR B（store ownership）、PR G（统一 import/export 底层）。

## PR K --- 交换标准草案 + 参考实现

-   独立仓库：中立规范文本（Manifest + Narrative Progress schema）+
    开放许可
-   diceframe 导出路径产出标准包形状
-   `x-diceframe/` 扩展节（容器合一）

依赖：纯规范部分无依赖、可立即启动；参考实现依赖 PR J。

## PR L --- 层2 转换枢纽

-   双向适配器，酒馆先行打磨（既有 adapter 对齐 Draft/CommitPlan）
-   `unsupported[]` / `warnings[]` 的 UI 呈现
-   Narrative Progress 导入侧物化策略

依赖：PR G、PR K 草案冻结。

## PR M --- 显式收录（小）

-   「本局新设定」候选投影 + GM 挑选 + CommitPlan 入库 + provenance

依赖：PR D（Runtime Projection Unification）。

## 46.8 增补不变量（并入 §41 列表续编）

``` text
13. 机制状态只存在于层1 存档包，永不进入层2 交换标准。
14. 标准包未知命名空间节必须忽略；未知 major 版本必须拒绝。
15. Narrative Progress 是投影，不是 WorldState 的第二 authority。
16. 运行时不自动写共享书；世界书新增条目必须是显式作者/收录行为。
17. 每个 diceframe 存档包必须同时是合法的交换标准包。
18. 导入永不覆盖本地同名内容；替换必须用户显式确认。
```

## 46.9 与其他轨道的关系

-   恋爱轨道 MVP 不被本增补阻塞，先行施工；本轨道代码随后。
-   #400 继续 HOLD，理由不变（`NATIVE_MODULE_CONTENT_PACKAGE_DIRECTION.md`
    §27 的四个 blocker 一个未消）。
-   本增补不新增 authority：所有导入仍走 §12 管线，所有 provenance 仍走
    §18/§32。

## 46.10 留白（明确推迟，不是默认）

-   标准的名字与首届共建伙伴名单：招募时定。
-   Narrative Progress 节具体 schema：PR K 第一项工作。
-   本机无同款 embedding 模型时的检索降级策略：PR J 时定。
-   包体积上限调整：PR J 核验后定。
