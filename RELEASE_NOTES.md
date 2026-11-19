# DiceFrame v3.3.1

3.3.0 的维护版本：修复安装版启动器的显示缩放模糊、更新后偶发的样式丢失与首启隐私弹窗卡顿，并补齐 D&D 2024 的 Hunter 特性、掩护等级、怪物 stat block 事实层与工具物品实体。

## 中文

### 启动器与显示（修复）

- **高分辨率/缩放屏不再模糊**：启动器声明 Per-Monitor V2 DPI 感知，显示缩放变化后 WebView2 内容保持清晰（此前标题栏清晰但网页被位图拉伸）。
- **更新后偶发样式丢失修复**：前端哈希资源改用 immutable 长缓存，启动时不再逐个回源重验证——更新重启窗口里的单次验证失败不再导致"原生按钮 + Markdown 裸文本"的无样式页面（此前需重启恢复）。

### 首启体验（修复）

- **隐私确认弹窗不再卡顿**：启动/偏好路径不再强制刷新远端法律文档清单，离线用户由内置快照立即兜底；确认按钮不再同步等待 Hub 遥测上报（断网时此前最长转圈 30 秒）。

### D&D 2024（内容与机制）

- **Hunter 子职五项特性**（S0-F19 第一部分）：Hunter's Lore、Hunter's Prey（Colossus Slayer / Horde Breaker，短/长休可换）、Defensive Tactics（Escape the Horde / Multiattack Defense）、Superior Hunter's Defense，选项组复用既有 feature_option 契约。
- **掩护等级作为声明式事实**（C2A）：半身/四分之三/全身掩护（+2/+5），声明→事件→状态→只读查询，取最高不叠加；actor 视图带出当前掩护等级。
- **怪物 stat block 事实层**（S0-MM1）：体型、感官、速度、抗性/免疫/易伤、CR/XP 忠实读回；条件免疫补形状校验（裸字符串不再炸成单字符状态）；XP 上界修正为 CR 30 = 155,000。
- **工具物品实体（36 条）**：全部 SRD 5.2.1 工具可作为物品持有；目录 key 统一物品 id 约定（去撇号），修复 4 处悬空 craft 引用并对齐 5 个装备包 key。
- **extreme 档可达**：dnd2024_srd 显式声明 `max_check_dc: 30`，DC 表的 extreme（25）不再被默认上限 20 钳掉。

### 其它

- Docker 发布工作流修复：正式版镜像现在会同步更新 `latest` 标签（此前该分支逻辑永远为否）。
- 冒烟测试以精确匹配定位「高级 JSON」标题，消除规则目录顺序不定导致的 strict mode 失败。

### 升级说明

- 可从 3.2.1 / 3.3.0 直接升级；无存档迁移。
- **安装版**：运行新的 `setup.exe` 覆盖安装；**便携版**：解压覆盖或用更新器；**Docker**：`3.3.1` 与 `latest` 标签同步更新。

## English

Maintenance release after 3.3.0: fixes launcher blur on scaled displays, intermittent unstyled pages after updates, and the first-run privacy dialog hanging offline — plus the D&D 2024 Hunter features, cover levels, monster stat-block facts, and tool item entities.

### Launcher & display (fixes)

- **No more blur on scaled displays**: the launcher declares Per-Monitor V2 DPI awareness, keeping WebView2 content crisp after display-scale changes.
- **Intermittent unstyled pages after updates fixed**: content-hashed frontend assets are now served immutable; launches no longer revalidate every chunk, so a single failed check inside the update-restart window can no longer strip the page's styles.

### First-run experience (fixes)

- **The privacy dialog no longer hangs**: startup/preferences no longer force-refresh the remote legal manifest (bundled snapshot serves offline users immediately), and confirming no longer waits on the hub telemetry sync (previously up to a 30s spinner offline).

### D&D 2024 (content & mechanics)

- **Hunter subclass, five features** (S0-F19 part one): Hunter's Lore, Hunter's Prey (Colossus Slayer / Horde Breaker, swappable on a rest), Defensive Tactics (Escape the Horde / Multiattack Defense), Superior Hunter's Defense — option groups reuse the existing feature_option contract.
- **Cover levels as declarative facts** (C2A): half/three-quarters/total (+2/+5), declare → event → state → read-only query, best-source-wins, never stacking; actor views carry the current cover level.
- **Monster stat block fact layer** (S0-MM1): size, senses, speeds, resistances/immunities/vulnerabilities, CR/XP read back faithfully; condition immunities are shape-validated (a bare string can no longer explode into per-character state); the XP ceiling is corrected to the CR 30 maximum of 155,000.
- **Tool item entities (36)**: every SRD 5.2.1 tool is now holdable as an item; catalog keys follow the entity-id convention (apostrophes dropped), four dangling craft references repaired, five pack keys aligned.
- **The extreme DC band is reachable**: dnd2024_srd declares `max_check_dc: 30`, so the table's extreme (25) is no longer clamped to 20.

### Other

- Docker publish fix: stable releases now move the `latest` tag again.
- The smoke suite matches the「高级 JSON」title exactly, removing a strict-mode flake caused by unordered rule listings.

### Upgrade notes

- Direct upgrades from 3.2.1 / 3.3.0 are supported; no save migration.
- **Installer**: run the new `setup.exe`; **portable**: unpack over the old directory or use the updater; **Docker**: `3.3.1` and `latest` update together.

## Downloads

- Windows 安装包 / installer：`DiceFrame-v3.3.1-windows-setup.exe`
- 便携版 / portable：`DiceFrame-v3.3.1-windows-portable.zip`
- Docker：`diceframe`（`3.3.1` 与 `latest` 标签）
