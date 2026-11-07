# DiceFrame v3.1.1

## 中文

### 资料册与内容包

v3.1.0 之后我们集中整理了资料册的「一本书一个身份」——此前同一本书会在创建页、资料册页和内容包页呈现成不同的身份，这也是本版大部分修复的来源：

- **创建页不再混淆容器与内容**：尚无条目的世界主册显示为「随世界带入 · 暂无条目」且没有勾选框；包内资料册标注「内容包 · N 条 · 开局勾选时加入书库」，开局时才按需落地，声明不再「启用即灌注」；模块详情页可就地只读预览包内资料册的条目正文。
- **模组冒险的世界由模组决定**：模组声明了世界而未安装时，开局自动创建本局世界，不再混入无关的世界模板；场景图取材也与实际使用的世界一致；无名模块书获得可读名称（如 Dagon GM Guide）。资源包可以直接用自带的书开一局免费冒险。
- **资料册跨局隔离（安全修复）**：开局勾选的模块资料册只在该局生效，不再自动激活到同一世界的其他对局；同一冒险重复开局共用世界时，各局资料互不串扰。
- **数字口径说清楚**：资料册页的世界下拉，单书世界显示「N 条」、多书世界显示「M 本 · N 条」，不再与世界主册一行的数字看起来像重复。
- **用户改动优先**：包更新不再覆盖或删除用户编辑过的模块资料册；卸载包时同样保留。

### 模组与在线市场

- 新特性：在线内容架支持按**下载量、点赞、评分、名称**排序，清除筛选时还原推荐序。
- 新特性：已安装内容包在商店出现新版本时显示**更新徽章**，更新后内容自动刷新。
- 安装内容包后立即询问是否启用，并记住用户的选择；「已启用」状态只在宿主真正启用后才上报。

### 修复

- 对局进行中浏览其他世界时，资料册条目列表停留在旧书、可见性徽章整列消失。
- 导出 `.dfplugin` 时独立资料册的 id 未重写为合规格式，导致导出的内容包装不回去。
- 创建页不再把资料册管理面板指向书库打不开的世界。

### 升级说明

- 本版本不新增存档 schema 迁移。
- 模块资料册的跨局隔离仅对新开局生效，修复前已创建的对局不追溯。
- de/ja/ru 界面下本版新增文案暂时回退英文。

## English

### Lorebooks and content packs

After v3.1.0 we focused on giving every lorebook one identity — the same book used to appear as different things on the create page, the lorebook page, and the content-pack page, which is where most of this release's fixes come from:

- **The create page no longer mixes containers with content**: a world's primary book with no entries shows as "brought in with the world · no entries yet" without a checkbox; pack-declared books are labeled "content pack · N entries · added to the library when selected at start" and land on demand only when checked — declaration no longer means "pour in on enable"; the module detail page previews declared book entries in place, read-only.
- **A module adventure's world belongs to the module**: when a module declares a world that is not installed, the game creates its own world at start instead of pulling in an unrelated world template; scene art also follows the world actually in use; nameless module books get a readable name (e.g. Dagon GM Guide). Resource packs can start a free adventure straight from their own books.
- **Per-game lorebook isolation (security fix)**: a module book selected at start now applies to that game only and no longer auto-activates for other games in the same world; repeated starts of the same adventure sharing a world keep their books isolated.
- **The numbers say what they count**: in the lorebook page's world dropdown, single-book worlds show "N entries" and multi-book worlds show "M books · N entries", so the world line no longer looks like a duplicate of the primary-book line.
- **User edits win**: pack updates no longer overwrite or delete module books the user edited; uninstalling keeps them too.

### Modules and marketplace

- New: the online shelf can be sorted by **downloads, likes, rating, or name**; clearing filters restores the recommended order.
- New: installed content packs show an **update badge** when the store has a newer version, and content refreshes after updating.
- After installing a pack the app asks whether to enable it and remembers the choice; "enabled" is only reported once the host has really enabled it.

### Fixes

- While a game was running, browsing another world kept the entry list on the old book, so visibility badges disappeared entirely.
- Exported `.dfplugin` packages kept a non-canonical id on standalone lorebooks, so exported packages could not be installed back.
- The create page no longer points the book panel at a world the library cannot open.

### Upgrade notes

- This release adds no save-schema migration.
- Per-game module-book isolation applies to new games only; games created before this fix are not retrofitted.
- New UI strings fall back to English under de/ja/ru for now.

## Downloads

- **普通 Windows 用户 / Windows portable users**: `DiceFrame-v3.1.1-windows-portable.zip`
- **源码运行用户 / Source-run users**: `DiceFrame-v3.1.1-windows.zip`
- **托管 Docker 更新 / Managed Docker update**: `DiceFrame-v3.1.1-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
