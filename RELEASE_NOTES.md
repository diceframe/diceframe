# DiceFrame v3.3.2

3.3.0 的维护版本：修复安装版启动器的显示缩放模糊、更新后偶发的样式丢失、首启隐私弹窗卡顿，以及多人联机中被误拦的"允许代操作"，并补齐 D&D 2024 的 Hunter 特性、掩护等级、怪物 stat block 事实层与工具物品实体。

## 中文

### 启动器与显示（修复）

- **高分辨率/缩放屏不再模糊**：启动器声明 Per-Monitor V2 DPI 感知，显示缩放变化后 WebView2 内容保持清晰（此前标题栏清晰但网页被位图拉伸）。
- **偶发样式丢失自愈**：前端哈希资源改用 immutable 长缓存，启动时不再逐个回源重验证；同时新增客户端自愈——页面加载后检测主样式表是否被客户端拒绝，失效即自动清缓存重载，"原生按钮 + Markdown 裸文本"的无样式页面不再需要手动重启。
- **WebView2 初始化加固**：同一进程内的重复窗口初始化不再产生半残环境（多开/接管场景此前可能直接失败）；证书钉扎日志只记录地址路径，不再带入查询参数。

### 首启体验（修复）

- **隐私确认弹窗不再卡顿**：启动/偏好路径不再强制刷新远端法律文档清单，离线用户由内置快照立即兜底；确认按钮不再同步等待 Hub 遥测上报（断网时此前最长转圈 30 秒）。

### 多人联机（修复）

- **受邀玩家可直接行动**：用实例访问密码登录的玩家打开邀请链接后，不再被要求先点「允许代操作」——浏览器持有的座位令牌现在会随请求提交，服务端据此识别"这是座位玩家本人，不是房主预览"。持有有效座位令牌时，令牌对应的座位就是行动身份（URL 里的旧座位参数不再覆盖它）；房主不带令牌查看玩家链接的预览语义保持不变。
- **座位令牌轮换后不再卡死**：令牌被 GM 重新生成后，旧令牌失效会明确返回并自动清理，已登录的玩家退回房主身份重新加入，而不是反复报错。

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
- **安装版**：运行新的 `setup.exe` 覆盖安装；**便携版**：解压覆盖或用更新器；**Docker**：`3.3.2` 与 `latest` 标签同步更新。

## English

Maintenance release after 3.3.0: fixes launcher blur on scaled displays, intermittent unstyled pages, the first-run privacy dialog hanging offline, and multiplayer players being blocked by the host-preview gate — plus the D&D 2024 Hunter features, cover levels, monster stat-block facts, and tool item entities.

### Launcher & display (fixes)

- **No more blur on scaled displays**: the launcher declares Per-Monitor V2 DPI awareness, keeping WebView2 content crisp after display-scale changes.
- **Unstyled pages now self-heal**: content-hashed assets are served immutable and the client detects a rejected main stylesheet on load, clearing the cache and reloading automatically — no more manual restarts for the "native buttons, raw markdown" state.
- **Hardened WebView2 initialization**: repeated window initialization in one process can no longer leave a half-initialized environment (multi-open / takeover scenarios).

### First-run experience (fixes)

- **The privacy dialog no longer hangs**: startup/preferences no longer force-refresh the remote legal manifest (bundled snapshot serves offline users immediately), and confirming no longer waits on the hub telemetry sync (previously up to a 30s spinner offline).

### Multiplayer (fixes)

- **Invited players can act right away**: players who log in with the instance access password and open an invite link are no longer required to enable delegated actions — the browser now submits the stored seat token with requests, and the server recognizes "this is the seat player, not the host previewing". With a valid seat token, that seat is the acting identity (stale seat parameters in the URL no longer override it); the host preview semantics for token-less links are unchanged.
- **Rotated seat tokens no longer wedge the page**: when the GM re-issues a seat link, the stale token is clearly rejected and cleared automatically; an owner session falls back to the host identity to rejoin instead of erroring forever.

### D&D 2024 (content & mechanics)

- **Hunter subclass, five features** (S0-F19 part one): Hunter's Lore, Hunter's Prey (Colossus Slayer / Horde Breaker, swappable on a rest), Defensive Tactics (Escape the Horde / Multiattack Defense), Superior Hunter's Defense — option groups reuse the existing feature_option contract.
- **Cover levels as declarative facts** (C2A): half/three-quarters/total (+2/+5), declare → event → state → read-only query, best-source-wins, never stacking; actor views carry the current cover level.
- **Monster stat block fact layer** (S0-MM1): size, senses, speeds, resistances/immunities/vulnerabilities, CR/XP read back faithfully; condition immunities are shape-validated; the XP ceiling is corrected to the CR 30 maximum of 155,000.
- **Tool item entities (36)**: every SRD 5.2.1 tool is now holdable as an item; catalog keys follow the entity-id convention (apostrophes dropped), four dangling craft references repaired, five pack keys aligned.
- **The extreme DC band is reachable**: dnd2024_srd declares `max_check_dc: 30`, so the table's extreme (25) is no longer clamped to 20.

### Other

- Docker publish fix: stable releases now move the `latest` tag again.
- The smoke suite matches the「高级 JSON」title exactly, removing a strict-mode flake caused by unordered rule listings.

### Upgrade notes

- Direct upgrades from 3.2.1 / 3.3.0 are supported; no save migration.
- **Installer**: run the new `setup.exe`; **portable**: unpack over the old directory or use the updater; **Docker**: `3.3.2` and `latest` update together.

## Downloads

- Windows 安装包 / installer：`DiceFrame-v3.3.2-windows-setup.exe`
- 便携版 / portable：`DiceFrame-v3.3.2-windows-portable.zip`
- Docker：`diceframe`（`3.3.2` 与 `latest` 标签）
