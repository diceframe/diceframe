# DiceFrame v2.0.1

## 中文

这是 DiceFrame 2.0.1，在 2.0 正式版的基础上进一步优化了手机端对局体验。正式版频道现在可以更新体验。

### 移动端对局体验

- **沉浸式对局页**：手机上进入对局后，应用顶栏与底部导航自动隐藏，把整块屏幕让给对局内容，对话窗口显著变大。
- **精简顶栏与场景条**：对局页顶栏与场景条压缩为单行，只保留关键信息，对话与输入区获得更多空间。
- **对话区自适应高度**：浏览器或 App 壳的工具栏收起/展开时，对话区会自动拉长或收缩，不再出现"底部栏下去了、对话框却没跟着变"的错位。

### 新功能

- **插件商店与内容商店**：插件页拆分为"插件商店 / 内容商店"两个选项卡——内容商店专注展示内容包类资源，插件商店聚焦机器人接入、工具、主题等插件，查找更直接。
- **插件商店生态**：工具型插件可渲染专用操作卡片（如外网接入卡）；商店条目显示所需 DiceFrame 最低版本并提示升级；支持更大插件包为二进制进程插件铺路。
- **一键外网接入**：新增官方 **Cloudflare 快速隧道**插件，一键生成公网 HTTPS 地址并自动写入分享链接，朋友和群聊 Bot 无需公网 IP 或域名即可加入你的对局；在"插件 → 工具"页的外网接入卡片操作。
- **主题与皮肤**：新增 4 套内置配色皮肤——星海秘典、王廷鎏金、翡翠远境、绯红余烬，在"设置 → 主题"页卡片式一键切换，并可与明暗模式叠加；也可继续安装插件主题深度定制。
- **插件规则词库定制**：插件作者可扩展检定意图词表（extends 继承），自定义规则体系的检定匹配更贴合。

### 体验与玩法

- **世界书 / 游戏日志页**：未进入存档或未选择世界时显示整洁的单列空态引导，不再出现"只有一边一排"的错乱布局。
- **剧情提示**：推进回合后不再用顶部气泡重复弹出剧情全文，剧情只在时间线展示。
- **GM 指令目标解析**：角色真名（如"冒险者"）优先于泛指词匹配，修复"复活冒险者"被误判为歧义而拒绝的问题。
- **骰子 / 规则修复**：SAN 检定大成功不再按失败结算；CoC 自定义技能基础值兜底防超模建卡；物理破坏/撬/砸/拆解类行动明确提示应进行检定。
- **幸运机制**：多人幸运改判改为每玩家独立超时（默认 60 秒可配），不再一人挂机全桌等待。
- **难度机制**：硬核难度禁止复活（角色可永久死亡），落实难度差异。
- **Hub 访问**：请求超时从 3 秒/6 秒放宽到 30 秒，减少 Hub 站缓慢时误触发熔断、插件详情打不开。
- **默认模型**：默认模型调整为 `deepseek-v4-flash`，更快更省。
- **设置页"支持项目"与 GitHub Star**：并排一行展示、文字左对齐。

### 修复与体验

- **支付流程**：创建角色余额不足时，不再反复弹出支付窗口——自动取消未完成的支付订单，避免弹窗循环打扰。
- **幸运选择**："保留失败"按钮改为与"消耗幸运点"一致的红色 pill 样式，两个选项一眼可辨。
- **商店目录刷新**：目录缓存从 30 天缩短为 1 天，上架与更新能更快看到；目录过期时来源旁会出现刷新按钮。
- **地图包调整**：地图包（map-pack）暂不支持在线安装，相关条目不再出现在商店可安装列表中，避免误导。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v2.0.1-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v2.0.1-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

This is DiceFrame 2.0.1, built on the 2.0 stable release with further refinements to the mobile play experience. Stable-channel users can update and enjoy it.

### Mobile Play Experience

- **Immersive table**: the app header and bottom navigation are hidden on mobile during play, giving the whole screen to the table — the conversation window is much larger.
- **Compact HUD & scene strip**: the play HUD and scene strip collapse to a single row, keeping only the essentials and freeing more space for dialogue and input.
- **Adaptive conversation height**: when the browser or app-shell toolbar hides or expands, the conversation area stretches or shrinks automatically instead of leaving a mismatch.

### New Features

- **Plugin Store / Content Store**: the Plugins page now has "Plugin Store / Content Store" tabs — Content Store focuses on content-pack resources, while Plugin Store focuses on bot adapters, tools, themes and other plugins, making discovery more direct.
- **Plugin ecosystem**: tool plugins render dedicated operation cards (e.g. an external-access card); store entries show the minimum DiceFrame version and prompt upgrades; larger packages are supported, paving the way for binary process plugins.
- **One-click external access**: a new official **Cloudflare quick tunnel** plugin generates a public HTTPS address with one click and writes it into the share link, so friends and chat bots can join your table without a public IP or domain; operate it from the external-access card on Plugins → Tools.
- **Themes & skins**: four new built-in color skins — Star Sea Codex, Golden Court, Jade Frontier, and Crimson Ember — switchable from one-card taps on the Settings → Themes page and stackable with light/dark mode; plugin themes are still available for deeper customization.
- **Custom rule lexicons for plugins**: plugin authors can extend the check-intent lexicon (extends inheritance), so custom rule systems match their checks more closely.

### Play & Experience

- **Lorebook / game log pages**: clean single-column empty states when no save or world is selected, fixing the misaligned "only one side" layout.
- **Narration toast**: advancing a round no longer re-pops the full narration in a top toast; it is shown only in the timeline.
- **GM command target resolution**: exact character names (e.g. "Adventurer") now take priority over generic terms, fixing "revive Adventurer" being wrongly rejected as ambiguous.
- **Dice & rules fixes**: SAN critical success no longer resolves as failure; CoC custom-skill base fallback prevents over-powered sheets; physical actions (prying / breaking / dismantling) now clearly warrant a check.
- **Luck**: multiplayer luck decisions get a per-player timeout (default 60 s, configurable) instead of blocking the whole table for one player.
- **Difficulty**: hardcore difficulty disables revival (characters can permanently die), making difficulty levels meaningful.
- **Hub access**: request timeouts widened from 3 s/6 s to 30 s, reducing false circuit-breaking when the Hub site is slow and plugin details fail to open.
- **Default model**: the default model is now `deepseek-v4-flash`, faster and cheaper.
- **Support Project + GitHub Star buttons**: shown side by side in Settings, text left-aligned.

### Fixes & Experience

- **Payment flow**: when balance is insufficient while creating a character, the pending payment order is now cancelled automatically instead of repeatedly popping up the payment dialog.
- **Luck selection**: the "Keep failure" button now uses the same red pill style as "Spend luck point", so the two options are easy to tell apart.
- **Catalog refresh**: store catalog cache shortened from 30 days to 1 day so listings and updates appear faster; a refresh button appears next to the source when the catalog is stale.
- **Map pack**: map-pack is reserved for a future map editor and no longer appears as installable in the store.

### Download Guide

- **Regular Windows users**: download `DiceFrame-v2.0.1-windows-portable.zip`.
- **Source-run users**: download `DiceFrame-v2.0.1-windows.zip`.
- `.sha256` files are update checksums; regular users do not need to download them manually.
