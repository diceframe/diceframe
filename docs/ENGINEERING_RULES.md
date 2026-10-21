# DiceFrame Engineering Rules

> 所有适用于当前改动的规则都必须遵守；贡献者可以按当前任务只阅读相关章节。
>
> 本文规定“如何安全地修改 DiceFrame”，不是冻结当前架构。
>
> 当前实现的架构事实来源仍是：
>
> - `docs/ARCHITECTURE_CN.md`
> - `docs/ARCHITECTURE_EN.md`
>
> 测试与产品关键契约的原则见下方第 15 节。

## 1. 目的

DiceFrame 需要同时支持快速增加功能、长期维护旧数据、多人权限隔离、不同规则系统扩展，以及较大的重构和迁移。

因此本规范保护的是：

- 用户数据
- 权限与隐私
- 持久化 identity
- public / extension contracts
- ruleset mechanics 边界
- multiplayer authority
- migration correctness
- 可验证的产品行为

本规范**不保护**：

- 当前文件路径
- 当前类名和 helper
- 当前内部实现
- 当前模块数量
- 当前 UI 组织方式
- 当前技术方案本身

只要受影响的契约被有意识地处理，DiceFrame 允许拆分、合并、迁移、替换甚至重做现有架构。

---

## 2. 规范用语

本文使用：

- **MUST**：默认不可违反的产品/数据/安全要求。
- **SHOULD**：当前推荐架构或工程做法；有明确理由时可以改变。
- **MAY**：明确允许的做法。

如果某个改动需要改变 SHOULD 级规则，不视为违规；应在 PR 中说明新的边界，并同步更新架构事实来源。

如果确实需要改变 MUST 级契约，应把它视为显式 breaking / product-contract change，提供版本、迁移、兼容或明确拒绝策略，而不是静默改变。

需要区分两类 MUST：

- API、schema、canonical identity、产品行为等**可版本化的产品契约**，可以按上述方式显式 breaking；
- **安全不变量**不能仅因 intentional breaking change 被放宽，包括：

```text
unauthorized data access
private / GM 数据泄露
跨 actor 的未授权状态修改
客户端取代服务端 authority
secret 泄露
```

权限 / authority 架构本身 **MAY** 重构；但新模型 **MUST** 保持等价或更强的授权判定、actor 隔离与服务端 authority。安全不变量上的回归不是 breaking change，而是正确性缺陷——无论 PR 如何描述都必须修复，不能通过 versioning / migration 流程“合法化”。

---

## 3. Architecture is allowed to change

DiceFrame **MAY** 为了新功能、可靠性、性能、安全性、维护性或长期演进：

- 拆分或合并模块；
- 移动职责；
- 替换内部实现；
- 删除已经失去意义的 abstraction；
- 引入新的 abstraction；
- 修改持久化模型；
- 调整 API；
- 重新组织 runtime / services / frontend；
- 拆分仓库、package 或 subsystem；
- 进行跨模块的大型重构。

大型改动不会因为“与当前 `ARCHITECTURE` 不同”而被禁止。

但改动 **MUST** 显式处理它影响到的稳定表面，包括：

- persisted data；
- compatibility；
- public / extension API；
- security / permissions；
- multiplayer authority；
- ruleset mechanics；
- migration；
- tests；
- architecture documentation。

合并后，更新过的 `ARCHITECTURE` 才是新的事实来源。

---

## 4. Stable Surface：按稳定表面判断风险

不要按“改了多少行”判断改动危险程度。按它触碰了什么判断。

### Level 0 — Internal implementation

例如：

- 内部 helper；
- 私有 class；
- 文件/目录布局；
- 未对外暴露的内部函数；
- 内部算法与缓存实现；
- UI 组件拆分。

这些内容 **MAY** 自由重构。

如果行为契约不变，不需要兼容旧内部实现。

### Level 1 — Architectural boundary

例如：

- route / WebAPI / service / core 的职责边界；
- ruleset runtime 边界；
- frontend / backend authority 边界；
- migration / compatibility 组织方式；
- subsystem 或 repository 边界。

这些内容 **MAY** 改变。

发生变化时 **SHOULD**：

- 在 PR 中描述 `Current -> Proposed`；
- 说明为什么现有边界不再合适；
- 更新 `docs/ARCHITECTURE_*.md`；
- 若属于长期、有替代方案争议的重要决策，再增加 ADR。

### Level 2 — Public / extension contract

例如：

- Web API；
- Plugin contract；
- Content V2；
- Ruleset Bundle；
- Adventure Bundle；
- externally consumed schema；
- 对 Bot / client 暴露的稳定字段。

这些内容默认 **SHOULD** 保持兼容。

需要 breaking change 时 **MUST**：

- 显式标注；
- 提供版本边界；
- 给出 migration / adapter / deprecation / rejection strategy 中至少一种合理方案；
- 添加对应测试；
- 更新相关文档。

### Level 3 — Persisted / authority / security contract

例如：

- save；
- SQLite / persisted state；
- canonical persisted identity；
- GM / player / private visibility；
- actor ownership；
- multiplayer turn authority；
- 权威规则状态；
- 权限与认证边界。

这是最高保护级别。

改变这些内容时 **MUST** 优先保证数据正确性、权限正确性和确定性；不能为了“自动兼容”猜测用户数据。

---

## 5. 用户数据与持久化 identity

### 5.1 用户数据不是系统模板的缓存

系统默认模板和用户数据库是两个不同的 authority。

系统 **MUST NOT** 因为当前 bundled template 发生变化，就无条件覆盖已经存在的用户记录。

允许：

```text
能够证明仍是旧官方默认值
→ 有条件升级到新官方默认值
```

禁止：

```text
当前模板
→ 每次启动强制同步/覆盖用户数据库
```

只要无法证明某条数据仍是系统默认状态，就 **MUST** 按用户数据处理并保留。

### 5.2 Persisted Identity Rule

一旦某个 canonical ID 可能进入以下任一表面：

- save；
- database；
- world；
- lorebook；
- character；
- plugin；
- ruleset；
- adventure；
- network payload；
- externally referenced content；

它就不再只是内部命名。

重命名或删除 persisted identity 时 **MUST** 考虑：

- alias；
- migration；
- explicit version boundary；
- deprecation；
- explicit rejection。

**MUST NOT** 仅因为“源码里改名更好看”就让旧数据失去引用。

display name / locale text **MUST NOT** 替代 canonical identity。

---

## 6. Migration

### 6.1 默认边界

当前默认：

```text
persisted schema / persisted state upgrade
→ src/migrations/

historical external/runtime shape compatibility
→ src/compat/ 或明确 adapter 边界
```

这是 **SHOULD**，不是永远不可改变的目录规则。

如果未来重做 migration architecture，可以通过明确的架构改动重新定义。

### 6.2 Migration MUST

Migration **MUST**：

- 幂等；
- 可测试；
- 有明确 source version / identity / digest / snapshot 等安全边界；
- 不无意丢字段；
- 不无意覆盖用户修改；
- 对未知未来版本 fail closed；
- 对部分失败保持可预测行为；
- 尽可能使用明确、可解释的转换，而不是启发式猜测。

### 6.3 Correctness > completeness

> **Migration correctness is more important than migration completeness.**

如果无法可靠判断旧数据的含义：

允许：

```text
无法安全自动迁移
→ 保留 / 拒绝 / 要求用户确认
```

不允许：

```text
“看起来大概是这样”
→ 猜测并重写用户数据
```

无法证明安全迁移时 **MUST** fail closed。

### 6.4 已发布 migration

已发布 migration 的历史语义 **SHOULD NOT** 被静默改写。

需要补救时，优先新增版本化 migration 或明确 repair step，而不是让同一个旧 migration 在不同版本中产生不同结果。

---

## 7. Compatibility 与 Breaking Change

Backward compatibility 是默认策略，不是永恒义务。

DiceFrame **MAY** 在旧兼容包袱明显阻碍正确性、维护性、安全性或长期架构时进行 breaking change。

Breaking change **MUST** 是显式的，而不是偶然发生。

根据影响范围，应提供：

- migration；
- compatibility adapter；
- alias；
- deprecation window；
- major/schema/runtime version boundary；
- 明确的 unsupported / rejection behavior。

兼容逻辑 **SHOULD** 集中在 compatibility / adapter 边界。

**SHOULD NOT** 让大量 legacy `if/else` 永久散落在正常 runtime 业务路径中。

---

## 8. 当前架构默认方向

以下是当前推荐方向，属于 **SHOULD** 级架构约束：

```text
routes
  ↓
WebAPI
  ↓
services
  ↓
core / engine
```

- core / engine **SHOULD NOT** 反向依赖 `src.webui`；
- route **SHOULD** 保持薄层；
- WebAPI **SHOULD** 负责委托与边界转换，而不是重新实现业务规则；
- service 间协作 **SHOULD** 使用明确接口，而不是绕过边界访问内部状态。

如果某次重构要改变这条依赖方向，允许改变，但应按 Level 1 architecture change 处理。

---

## 9. Ruleset isolation

DiceFrame 的 generic engine 是多个规则系统共享的基础。

当前默认方向：

```text
specific ruleset
      ↓
generic runtime / engine primitives
```

而不是：

```text
generic engine
      ↓
specific D&D / CoC implementation
```

因此：

- D&D 专属行为 **SHOULD** 留在 D&D runtime / capability 边界；
- CoC 专属行为 **SHOULD** 留在 CoC 边界；
- generic d20 **SHOULD NOT** 因某一个规则系统的特例而改变通用语义；
- ruleset **MAY** 通过明确 capability / runtime contract 扩展通用体验。

这里保护的是**依赖方向和 mechanics authority**，不是永久锁死某个目录路径。

未来把某个 ruleset 移到 package、独立仓库或新 runtime 结构完全允许。

---

## 10. Canonical identity 与 Locale

Canonical identity **MUST** 与 display text 分离。

例如：

```text
fighter
longsword
npc_innkeeper
```

是 identity；

```text
战士 / Fighter
长剑 / Longsword
老汤姆 / Old Tom
```

是 display text。

Locale **MUST NOT**：

- 改变 canonical ID；
- 改变 mechanics；
- 改变规则技能池；
- 改变权限；
- 增删 canonical entity；
- 用翻译后的字符串充当 runtime identity。

如果未来 localization architecture 改变，这个 identity/mechanics 契约仍应保留，除非明确做 breaking content-model change。

---

## 11. Server authority、Security 与 Privacy

### 11.1 服务端是最终 authority

以下内容 **MUST NOT** 只依赖客户端约束：

- 权限；
- actor ownership；
- GM-only 操作；
- private visibility；
- turn authority；
- resource consumption；
- authoritative mechanics。

前端可以隐藏按钮，但隐藏按钮不是安全边界。

### 11.2 Private data

GM-only / player-private 数据 **MUST NOT** 先发给无权用户再靠 UI 隐藏。

权限过滤应在服务端或更早的 authoritative projection 边界完成。

### 11.3 输入不可信

以下均视为不可信输入：

- browser/client；
- Bot；
- Plugin；
- imported content；
- uploaded save；
- LLM output；
- migration source data。

所有权威状态修改都应经过对应验证边界。

---

## 12. Multiplayer authority

任何会修改多人游戏状态的新功能 **MUST** 明确：

- 当前 `game`；
- `actor`；
- actor ownership；
- turn / phase authority；
- 并发行为；
- 是否允许 GM override；
- failure semantics。

**MUST NOT** 假设：

> “前端正常情况下不会这样调用。”

不同游戏、不同玩家和不同 actor 的数据必须保持隔离。

涉及并发提交、round advance、资源消耗的行为 **SHOULD** 有 integration / concurrency coverage。

---

## 13. LLM 不是权威状态机

LLM 可以：

- 提议；
- 叙事；
- 解析自然语言；
- 生成候选内容；
- 给出 tool / intent candidate。

LLM 输出 **MUST NOT** 绕过 engine / reducer / validator 直接成为权威状态修改。

例如 HP、资源、战役事实、权限、冒险推进、战斗状态等，最终仍应经过确定性或可验证的 authoritative path。

---

## 14. Frontend boundary

Frontend 默认负责：

- interaction；
- presentation；
- local UI state；
- 对 backend authoritative result 的展示。

Frontend **SHOULD NOT** 重复实现一套与 backend 不同的核心 mechanics / permission model。

这不限制：

- Vue 组件如何拆；
- store 怎么组织；
- navigation 怎么重构；
- 页面如何重做；
- 未来是否替换 frontend 技术。

保护的是 authority，不是 UI 实现。

### 14.1 Navigation layout contract

内容库与管理页 **MUST** 遵守同一套导航停靠规则；新增同类页面沿用该规则。它不要求游玩页或公开玩家页增加应用顶栏。

| 层级 | 滚动与停靠行为 |
| --- | --- |
| 一级：应用顶栏 | 按应用壳的布局显示或滚出；二级导航不得反向强制它始终显示或隐藏。 |
| 二级：内容 / 管理栏目 | 一级仍可见时紧接其可见底边；一级向上移出时连续跟随，完全移出后停靠视口顶部；返回时恢复上下叠放。 |
| 页面内菜单：设置分类、日志索引等 | 吸顶时位于可见一级导航与二级导航之后，保留所属页面定义的间距；可用高度同步扣除导航占用，菜单末项必须可达。 |

- 同一状态 **MUST NOT** 出现顶栏已隐藏却仍预留完整顶栏高度的空带，也不得以负间距让菜单互相覆盖。
- 页面 **MUST** 复用导航 owner 提供的实时偏移与二级导航高度，**MUST NOT** 各自猜测固定的顶栏像素高度或复制滚动监听。缺失或隐藏的顶栏占用为零。
- 当前 owner 为 `SectionWorkspaceShell` / `useSectionHeaderOffset`，共享外观归 `styles/navigation.css`；页面只拥有内部菜单的布局、间距及断点。现有 CSS 变量 `--section-header-height` 表示顶栏在视口内的可见底边偏移，不是完整元素高度。
- 位置与高度 **MUST** 响应滚动、窗口尺寸、导航换行和字体 / 语言造成的尺寸变化。滚动测量 **SHOULD** 按动画帧合并；组件卸载时 **MUST** 清理监听、观察器和待执行帧。
- 窄屏菜单可以在自身内部横向滚动，但 **MUST NOT** 撑宽整个页面或挤掉菜单文字。底部导航与安全区占用 **MUST** 保留，交互入口不得被遮挡。
- 修改此行为时，浏览器验证 **MUST** 覆盖内容与管理两类页面、桌面与手机、一级导航完整 / 部分 / 完全移出、向上返回以及跨断点缩放；存在页面内吸顶菜单时还需验证间距与可达性。验收针对屏幕几何与可操作性，不绑定某个测试文件名或 DOM 层数。

---

## 15. Tests protect contracts, not implementations

测试的目标是保护产品承诺和风险边界，不是冻结当前实现。

测试 **MAY** 因重构被：

- 重写；
- 合并；
- 移动；
- 删除重复覆盖。

前提是对应 contract 的有效覆盖仍然存在。

测试保护行为契约，不维护“某契约必须对应某个具体测试文件名”的人工映射表——测试改名、合并、重构不应要求同步维护一张 Markdown 清单。

### 15.1 Critical testing areas

以下高风险领域的行为契约必须始终有有效测试覆盖；测试可以合并、改写、移动，但覆盖不能消失：

- Authentication / permission / private-data isolation
- Persisted data / save / migration
- Multiplayer actor / game / turn / concurrency isolation
- Ruleset mechanics / canonical identity
- LLM 输出不绕过 authoritative state path
- Plugin / imported content / path safety
- Transport / token / secret leakage
- Upgrade / rollback / integrity

新增高风险产品契约时 **SHOULD** 增加对应测试；改动落在上述领域时，同时确认旧覆盖没有因重构丢失。

不要为了测试而保留一个已经不合理的内部 helper / class / 文件结构。

同时不要通过删除测试来掩盖 regression。

---

## 16. PR 大小：保护完整性，不限制行数

DiceFrame 不设置：

- 最大改动行数；
- 最大文件数；
- 大型 PR 禁令。

原则是：

> **Prefer the smallest coherent change, not the smallest diff.**
>
> 优先最小“完整改动”，而不是最小“代码行数”。

一个完整 migration 可能合理地同时包含：

- migration；
- compatibility；
- backend；
- frontend；
- tests；
- docs。

如果把这些拆开会产生无法独立验证或不安全的中间状态，就不应为了“PR 小”强拆。

反过来，如果两个变化可以：

- 独立产生价值；
- 独立验证；
- 独立回滚；

则 **SHOULD** 分开提交，减少审计噪音。

---

## 17. PR 风险声明

PR 不需要先判断自己属于某个固定等级。

只需识别是否触碰这些影响面：

```text
API
Persisted Data
Migration
Compatibility / Breaking
Security / Permission
Multiplayer
Ruleset Mechanics
Architecture
Plugin / Extension Contract
UI only
```

触碰的风险面越多，review 和测试越应该集中到对应契约。

风险声明的目的不是阻止合并，而是避免“做了高风险改动却没人意识到”。

---

## 18. Design Note 与 ADR

### 普通 bug / 小功能

不需要 ADR。

### 需要在 PR 中写 Design Note 的情况

通常包括：

- 改变 subsystem 边界；
- 改变主要依赖方向；
- 引入新的 persistence model；
- breaking public/extension contract；
- 选择一个会长期影响后续工作的核心 abstraction。

Design Note 可以直接放在 PR description 中，不要求单独文件。

### 需要 ADR 的情况

只有当决策：

- 预计长期存在；
- 有多个长期可行方案；
- 未来维护者很可能问“为什么当初这样做”；
- 涉及 repository split、runtime model、content model、persistence architecture、plugin architecture 等重要方向；

才 **SHOULD** 新增 ADR。

ADR 不是审批凭证；它是长期的“为什么”。

详见 `docs/adr/README.md`。

---

## 19. Dependencies

新依赖不是禁止项。

新增 runtime / security-sensitive / large dependency 时 **SHOULD** 说明：

- 为什么现有能力不足；
- 它进入哪个边界；
- 对镜像体积、平台支持、安全和维护的明显影响。

普通开发依赖无需写长篇论证。

不要为了“零依赖”重复实现已有成熟基础设施，也不要为了一个小 helper 引入过重依赖。

---

## 20. Generated / derived artifacts

如果某文件是从明确 source-of-truth 自动生成的：

- **SHOULD** 修改 source，而不是直接长期维护生成结果；
- 生成方式 **SHOULD** 可复现；
- 若生成文件需要入库，应在文档或文件头说明来源。

运行缓存、本机产物、临时测试结果 **MUST NOT** 作为源码提交。

### 20.1 Typed boundaries

外部 JSON、Plugin RPC、上传存档和历史兼容 payload 在校验前可以保持宽类型；通过校验后，稳定的模块边界 **SHOULD** 暴露明确的 typed contract。

Ruleset / Plugin 私有状态可以有意保持 opaque，但必须在 generic boundary 标明 ownership；不得为了 typing 让 generic engine 编码具体 ruleset mechanics。Mypy typed zone 应渐进扩展，不通过全仓 strict、大量 `cast(Any, ...)` 或 blanket `type: ignore` 制造虚假精确性。

---

## 21. AI-assisted development

DiceFrame 允许使用 Codex、Claude、ChatGPT 和其他 AI 辅助开发。

AI output 不是 authority。

AI agent 在修改前 **SHOULD**：

- 阅读相关现有架构和附近实现；
- 查证实际字段/API，而不是凭猜测创造；
- 识别是否触碰 persisted / security / multiplayer / ruleset contract；
- 避免无关清理扩大 diff。

AI agent **MAY**：

- 大型重构；
- 删除 obsolete code；
- 修改架构；
- 拆分模块；
- 新增 migration；
- 改 public API。

但必须像人类贡献一样处理相应契约、测试、迁移和文档。

“这是 AI 写的”不能作为不了解代码含义或跳过验证的理由。

---

## 21.5 Code Organization & Cohesion

DiceFrame 的代码组织目标是让一个功能的主要行为具有明确 owner，并让新增实现尽可能通过现有 contract 扩展，而不是不断扩大中央编排文件。

本节保护的是：

- responsibility ownership；
- dependency direction；
- 可审计的模块边界；
- 新功能的局部可理解性；
- 长期可拆分性。

本节**不冻结**：

- 当前目录名；
- 当前文件名；
- 当前 component / class / helper；
- 某个模块必须拆成多少文件；
- 某个文件必须低于固定行数。

### 21.5.1 Responsibility first, size second

大文件本身不是缺陷。

以下文件天然可能较大：

- composition root；
- cohesive parser / validator / reducer；
- generated / derived data；
- i18n dictionary；
- type declarations；
- 大型 contract test；
- 聚合多个稳定定义的数据文件。

真正需要关注的是一个模块是否：

- 同时承担多个能够独立变化的产品职责；
- 频繁被不相关功能共同修改；
- 为新增功能持续增加新的特例分支；
- 同时负责 UI、校验、持久化、网络和 domain mechanics；
- 成为其它模块获取内部状态的 service locator；
- 使一个局部功能必须理解大量无关上下文才能修改。

因此：

> **A large file is a review signal, not a failure by itself.**
>
> **Adding a distinct responsibility to an already multi-purpose module is a stronger smell than line count alone.**

对于 active logic file，建议使用以下 review 级别作为启发式信号：

```text
< 500 行
→ 通常处于舒适范围

500–800 行
→ 一般可接受，但应留意职责是否持续增长

800–1000 行
→ 应主动检查是否已经承担多个独立职责，并认真考虑拆分

> 1000 行
→ 默认应拆分，除非有明确的高内聚理由证明继续放在一起更合理

> 1500 行
→ 对活跃业务逻辑文件通常应视为架构热点，应优先进入拆分或职责治理计划
```

这些数字是 review signal，不是机械 CI 红线。

以下类型可以合理超过上述范围：

- i18n dictionary；
- generated / derived file；
- 大型 schema / type declarations；
- 数据表 / canonical definition；
- 高度内聚的 parser / validator / reducer；
- 大型 contract / integration test。

不得为了降低行数、函数数或复杂度指标而机械拆成大量没有清晰职责的小文件。

### 21.5.2 Every feature should have an owner

新增行为 **SHOULD** 首先识别所属 product/domain owner。

当前常见 owner 例如：

```text
frontend product feature
→ frontend-v2/src/features/<domain>/

HTTP adaptation
→ src/webui/routes/

application coordination
→ src/webui/services/ 或后续明确的 application-service boundary

generic mechanics / authoritative primitives
→ src/engine/

ruleset-specific mechanics
→ src/rulesets/<runtime>/

compatibility
→ src/compat/

persisted schema migration
→ src/migrations/

plugin host / plugin runtime boundary
→ src/plugin_host/
```

这些路径描述当前默认 ownership，不是永久目录契约；架构变化时可以调整。

如果新行为已经有 owner，**SHOULD** 放入 owner 附近，而不是因为某个中央文件“最容易拿到所有状态”就放入中央文件。

如果一个 feature 尚无合适 owner，优先建立一个 focused module / component / service，而不是把它放进无关的 `common` / `shared` / `utils` 大杂烩。

### 21.5.3 Orchestrators coordinate; feature modules implement

以下类型的模块允许作为 orchestration / facade / composition root：

- Vue page / view shell；
- HTTP route；
- `WebAPI` facade；
- registry；
- application bootstrap；
- `PluginHost` facade；
- runtime composition root；
- aggregate facade（例如高风险状态聚合根）。

它们可以比普通文件大，但 **SHOULD** 主要负责：

- assemble；
- route；
- delegate；
- coordinate；
- lifecycle；
- boundary conversion。

它们 **SHOULD NOT** 因为“这里能拿到所有依赖”而持续吸收：

- feature-specific validation；
- 独立持久化规则；
- provider / ruleset 特例；
- 与其它 feature 无关的状态机；
- 可独立演进的业务流程；
- 大量底层文件 / ZIP / DB / process 操作。

当一个新功能在 orchestration file 中需要一组可以被命名的独立状态、函数、校验或 I/O 流程时，应优先考虑抽到所属 feature/service。

### 21.5.4 Frontend feature ownership

Frontend page root **SHOULD** 负责页面组合，而不是成为所有子功能的实现容器。

当一个页面包含多个独立设置 / 工具 / editor domain 时，推荐：

```text
Page/View shell
  ↓
Pane / domain component
  ↓
feature composable / state
  ↓
API adapter / typed DTO / pure helper
```

独立 feature 的：

- refs；
- computed；
- watchers；
- request state；
- validation；
- save / rollback；
- dialog state；
- formatting helpers；

**SHOULD** 尽量与该 feature 共置。

对会写回 backend / persisted content 的复杂编辑器，**SHOULD** 分离：

```text
API DTO / parsed input
        ↓
validation / normalization
        ↓
editable draft
        ↓
serialization / save boundary
```

不要让 Vue template / event handler 直接长期维护任意结构的 persisted payload。

CSS **SHOULD** 跟随所属 feature / pane；不要因为已有一个大型样式文件就继续把不相关领域样式全部追加进去。

`shared` / `common` / `utils` 只适合真正跨 feature、语义稳定的复用能力。只被一个 feature 使用的 helper 默认留在该 feature。

### 21.5.5 Backend route and service ownership

HTTP route **SHOULD** 保持薄层，主要负责：

- request parsing；
- authentication / transport context；
- 调用公开 application/service contract；
- 将结果转换为 HTTP response。

从本规则生效后，新 route 代码 **MUST NOT** 新增对：

```text
api._*
registry._*
handler._*
```

等私有实现成员的直接访问。

已有存量访问属于待偿还架构债，不要求在无关 PR 中一次清零。

新 service **SHOULD** 优先接收明确、类型化的依赖，而不是为了拿若干内部对象接收整个 `WebAPI` 再把它当 service locator。

允许在渐进重构期间保留 `WebAPI` facade，但：

> **A facade may coordinate dependencies; feature services should not depend on the facade merely to reach its private internals.**

如果一个 service 已经同时承担多个独立 application capability，应按能力拆分，而不是只按“让文件更短”拆分。

### 21.5.6 Dependency direction is stronger than convenience

新代码 **MUST NOT** 因为调用方便而制造新的反向依赖。

尤其：

```text
generic engine / generic commands
    MUST NOT
import specific ruleset implementation
```

specific ruleset 行为应通过：

- runtime capability；
- protocol / contract；
- registry；
- injected strategy；

进入通用流程。

同样，新代码：

```text
core / engine / compat / migration
    MUST NOT
depend on src.webui implementation
```

需要被 WebUI 与 compatibility 同时复用的行为，应下沉到两者都能依赖的领域 / application boundary。

这条规则约束**新增依赖**。现有已知反向依赖按重构计划渐进修复，不要求在任意无关 PR 中顺手清零。

### 21.5.7 Prefer extension points over central branching

对具有多个实现的领域，例如：

```text
AI provider
ruleset runtime
content / plugin contribution
transport
image / speech backend
future external integrations
```

新增实现 **SHOULD** 优先使用：

- capability；
- adapter；
- registry；
- typed definition；
- strategy；
- generic compatible connector。

不应默认在 generic path 中追加：

```python
if provider == ...
elif provider == ...
```

或：

```python
if ruleset_id == ...
elif ruleset_id == ...
```

如果某个实现与已有通用协议兼容，应优先复用通用 connector / adapter。

只有确有：

- 不同认证模型；
- 不同 payload / protocol；
- 独有 lifecycle；
- 无法通过现有 capability 表达的重要行为；

时，才应引入 dedicated implementation。

如果必须在中央流程中增加特例分支，应尽量把判断限制在明确 boundary，并在 PR 中说明为什么现有 extension point 不足。

### 21.5.8 P2P / alternate transport parity

DiceFrame 的 P2P 不代理任意 HTTP，而是通过显式 semantic operation 保持安全边界。

因此新增 player-facing game operation 时，**SHOULD** 明确其：

```text
P2P supported
P2P intentionally unsupported
GM/server-only
```

之一。

涉及 authoritative state 时，P2P adapter 不能建立第二套 mechanics / permission model；仍应进入相同 authoritative service / runtime path。

“页面能打开”不能替代真实 operation / authority coverage。

### 21.5.9 Existing debt and refactor-in-progress

本节采用 **prospective / no-new-debt** 原则。

已有：

- large file；
- service locator；
- private-member route access；
- high-complexity function；
- broad exception；
- weak typing；

不会仅因本规则生效而自动成为阻塞所有 PR 的违规项。

允许：

```text
小修复
→ 在旧结构中最小修改

正在其它 PR 拆分
→ 不重复做同一重构

高风险核心
→ 保留 facade / aggregate，渐进抽取
```

但：

> **Existing debt is not precedent for new debt.**

当新改动会给旧热点增加新的独立职责时，贡献者 **SHOULD** 优先：

1. 放入已经存在的新 owner；
2. 抽取本次新增职责；
3. 如果抽取会显著扩大风险或与正在进行的重构冲突，则做最小原地修改，并在 PR 中说明暂缓原因。

不要为了遵守本节而进行与当前任务无关的大规模 opportunistic refactor。

一旦新的模块边界已经合并到 `main`，后续代码应使用新边界，不应继续把功能写回已被替代的旧中央路径。

### 21.5.10 Complexity and file size are trend signals

DiceFrame **MUST NOT** 仅因：

- 超过 N 行；
- 函数超过某个复杂度数字；
- imports 较多；
- test file 较大；

自动认定实现错误。

这些指标用于发现：

- responsibility accumulation；
- high-change hotspot；
- review difficulty；
- dependency fan-out。

对于 parser、validator、reducer 等天然分支较多但语义内聚的代码，优先按 typed intent / validation phase / event handler 组织，而不是为了降低复杂度数字破坏语义连续性。

长期可在 CI 中输出规模 / complexity trend，但默认不应建立全局 `max-lines` 式硬门槛。

### 21.5.11 AI-assisted changes must avoid repository spillover

AI agent 在修改前 **SHOULD**：

1. 先识别当前任务的 owning module；
2. 阅读 owner 附近的现有实现和 contract；
3. 如果最短路径是继续往大型 root / facade 中追加代码，先判断该行为是否属于独立职责；
4. 优先使用已有 public contract / capability / adapter。

AI agent 在提交前 **SHOULD** 检查：

```bash
git diff --name-only <base>...HEAD
git diff --stat <base>...HEAD
```

并确认每个 changed file 都能解释其与当前任务的关系。

AI / 人工贡献都 **SHOULD NOT** 在功能 PR 中顺手：

- 改无关注释；
- 大量格式化无关代码；
- 加无关日志；
- 修另一个功能；
- 搬动无关测试；
- 因其它 agent / review 建议而不加判断地扩大 scope。

AI review suggestion 不是自动执行命令；应用建议前应验证它是否符合当前仓库事实和任务范围。

### 21.5.12 Tests protect behavior, not file shape

模块拆分本身不自动要求增加测试数量。

纯 extraction / move 在行为契约未变化时，可以依赖现有有效 contract coverage。

新增测试优先用于：

- 新的高风险产品 contract；
- persistence / migration；
- permission / private data；
- multiplayer authority；
- ruleset mechanics；
- state save / rollback；
- public / extension contract；
- 真实发生过的 regression。

不要仅为了证明：

```text
“现在有 3 个组件”
“这个 helper 被搬到了另一个文件”
“CSS class 还在那里”
```

而增加脆弱的实现结构测试。

如果需要保护 dependency boundary，优先使用：

- static import check；
- lint rule；
- architecture check；

而不是大量 source-string / DOM snapshot 测试。

### 21.5.13 Review smell checklist

以下不是自动拒绝条件，但 reviewer 遇到时应停下来确认 ownership：

```text
一个 root View 又新增一整组 refs/computed/watchers
一个 route 又直接访问 _private member
一个 generic module 新增 specific ruleset import
一个 provider 新增需要同时修改多个中央 switch/if
一个 service 接收完整 api 只为拿 api._x
一个 feature helper 被放入全局 utils 但只有一个调用方
一个组件同时 parse + validate + persist + render 同一复杂数据模型
一个 PR 出现无法解释的其它领域 test / file
一个 facade 新增了可独立命名、可独立演进的一整套业务流程
```

遇到这些情况时优先问：

> 这个行为真正属于谁？

而不是先问：

> 这个文件有多少行？

---

## 21.6 Application actions and Agent integration

新增用户可执行的业务能力 **SHOULD** 在 owning module 提供 `ApplicationAction`，由 composition root 注册至 `ActionRegistry`；暂不支持 Agent 时在该功能交付说明中写明原因。不能以新增中央提示词分支或第二套 Agent 专用业务实现代替领域命令。

- 动作 **MUST** 声明稳定 ID、版本、参数 schema、所属领域和实际 effect；非兼容契约变更 **MUST** 更新版本，使旧计划重新规划。
- handler **MUST** 复用领域权限、canonical identity、版本与权威写入。目录发现、聊天内容和授权模式均不能绕过领域拒绝。
- 可自动执行的普通修改 **MUST** 显式标记并提供 guard；信息披露不得归为日常自动修改。外部副作用与 destructive 操作仍需具体确认，插件自报声明不等于宿主权限或进程沙箱。
- 读操作不得隐藏持久化业务写入。预览、回执、取消和可恢复范围 **MUST** 反映实际领域契约，不得承诺通用事务或万能撤销。
- 验证 **SHOULD** 覆盖注册后可发现并执行、参数/权限拒绝、契约失效、相关并发与重复提交行为；目录与真实 handler 是能力事实来源，不维护第二份静态工具数量表。

See [ADR-0011](adr/0011-application-actions-and-agent-delegation.md).

---

## 22. Intentional exceptions

这些规则用于避免**无意的架构退化**，不是阻止**有意的架构演进**。

当某个改动确实需要：

- 改变当前 SHOULD 规则；
- 改变当前架构；
- 打破某个稳定 contract；

应：

1. 明确说明变化是 intentional；
2. 解释为什么旧边界不再适合；
3. 处理 migration / compatibility / security / tests；
4. 更新对应 architecture / contract 文档；
5. 重大长期决策必要时增加 ADR。

完成这些后，改变架构本身不是违规。

---

## 23. 最终判断原则

遇到规范没有明确写到的情况，按以下顺序判断：

1. 会不会破坏或泄露用户数据？
2. 会不会破坏权限、私密信息或 multiplayer authority？
3. 会不会让旧持久化 identity / save 无法解释？
4. 会不会无意改变 public / extension contract？
5. 会不会让 specific ruleset 反向污染 generic engine？
6. 有没有可验证的 migration / compatibility / test？
7. 如果以上都处理好了，这个重构是否让系统更清晰、更可靠？

前六项是风险控制。

第七项提醒我们：

> DiceFrame 的架构应该能够继续变得更好，而不是因为规范而停止演进。

---

## 24. 官方内容与渠道边界

适用于项目自身运营与分发的四个面：Hub、随仓库分发的内置内容、官方社区与文档口径、插件与玩法的默认行为。约束的是**项目分发了什么、默认做了什么**，不管制用户自托管实例中的私有内容。背景与完整决策见 [ADR-0010](adr/0010-gal-first-non-trpg-gameplay.md)。

- 随仓库分发的玩法内容（含样板 gal、默认提示词、模板）**MUST** 保持全年龄，**MUST NOT** 包含以情感依恋为留存目标的措辞设计。
- Hub 收录的内容 **MUST** 全年龄；NSFW 内容与版权角色二创卡 **MUST NOT** 上架；AI 生成的图像 / 语音内容 **MUST** 携带生成标注。
- 官方口径（README、官网、Release Notes、社区公告）**MUST** 使用游戏语言（章节、路线、结局、通关），**MUST NOT** 使用情感陪伴语言或暗示"AI 角色不是 AI"。
- 玩法模式的 transport 能力由模式契约声明（`bot_surface: full | read_only | none`），绑定准入单点执行；新增玩法 **MUST NOT** 在 bot bridge、平台插件或 generic path 中以具体玩法分支实现限制或放行。
