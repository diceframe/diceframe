# DiceFrame v3.2.1

3.1.2 之后的第一批功能与修复：Windows 安装包、启动器独立窗口、更省事的升级路径，以及联机、模型接入和跑团流程上的一批修复。

## 中文

### 安装与更新（新增）

- **Windows 安装包**：新增 `DiceFrame-v3.2.1-windows-setup.exe`，按当前用户安装，不需要管理员权限。
- **启动器独立窗口**：双击 `DiceFrame.exe` 后在独立应用窗口打开，不再占用一个新浏览器标签；关掉窗口即结束进程。
- **便携版覆盖升级**：新版便携包可以直接解压覆盖到旧目录完成升级，不必先清空旧版本。
- **长路径修复**：更新器解压便携版更新时不再受 Windows `MAX_PATH` 限制，此前深层路径（Python 依赖）会解压失败。

### 多人 / 联机（修复）

- 通过 Cloudflare Quick Tunnel 联机时，多人状态轮询恢复正常，客户端不再停在过期状态。

### 模型接入（修复）

- 自定义 `base_url` 本身已带版本段（如 `/v1`）时，生图 / TTS / ASR 端点拼接不再重复或错位。

### 跑团流程（修复）

- **GM 席位**：创建游戏时不再生成「未认领」的空 GM 席位；GM 可以重新收回自己的席位。
- **GM 私密感知**：私密感知内容在 GM 回合上下文中保留，不再丢失。
- **角色编辑**：已标记的技能身份在编辑角色后保持原样。
- **孤注一掷**：无人应答的 Push 超时后结束等待，并明确告诉 GM 如何关闭该功能，不再无限挂起。
- **存档席位**：日志窗口之外但有真实痕迹的席位（有角色、已准备、队列里有动作）不再被当成幽灵席位清理。

### 升级说明

- 本版本不新增存档 schema 迁移；3.1.2 与 3.2.0-beta.1 都可以直接升级。
- **安装版**：运行新的 `setup.exe` 完成覆盖安装。
- **便携版**：把新包解压覆盖到旧目录，或用更新器自动升级。
- **Docker**：镜像同时更新 `3.2.1` 与 `latest` 标签。
- 开着「预览版更新」的实例也会收到 3.2.1——正式版版本号高于 beta。
- de / ja / ru 界面下本版新增的 6 条文案（GM 收回席位、孤注一掷继续）暂时回退英文。

## English

The first batch of features and fixes after 3.1.2: a Windows installer, a launcher window of its own, a simpler upgrade path, plus fixes across multiplayer, model endpoints and table flow.

### Installing and updating (new)

- **Windows installer**: new `DiceFrame-v3.2.1-windows-setup.exe` installs per user and needs no administrator rights.
- **Launcher window**: double-clicking `DiceFrame.exe` now opens a dedicated application window instead of a new browser tab; closing the window stops the process.
- **Portable over-install upgrade**: a new portable archive can be unpacked straight over the old directory, no need to wipe the previous install first.
- **Long paths fixed**: the updater no longer hits the Windows `MAX_PATH` limit when extracting portable updates — deep dependency paths used to fail.

### Multiplayer (fix)

- Multiplayer state polling works again behind Cloudflare Quick Tunnels; clients no longer sit on a stale state.

### Model endpoints (fix)

- When a custom `base_url` already carries a version segment (e.g. `/v1`), image / TTS / ASR endpoint URLs are no longer duplicated or mis-joined.

### At the table (fix)

- **GM seat**: creating a game no longer leaves an unclaimed empty GM seat, and the GM can take their seat back.
- **Private perceptions**: keeper-only perceptions stay in the GM turn context instead of being dropped.
- **Character edits**: marked skill identities survive a character edit.
- **Push the roll**: an unanswered Push now times out instead of hanging forever, and tells the GM how to turn the feature off.
- **Seats in saves**: a seat outside the retained log window but with real evidence (a character, a ready mark, a queued action) is no longer purged as a ghost.

### Upgrade notes

- No save-schema migration in this release; both 3.1.2 and 3.2.0-beta.1 upgrade directly.
- **Installer**: run the new `setup.exe` to upgrade in place.
- **Portable**: unpack the new archive over the old directory, or let the updater do it.
- **Docker**: images are published under both `3.2.1` and `latest`.
- Instances with "Preview updates" enabled will also be offered 3.2.1 — the stable version outranks the beta.
- Six new strings (GM reclaiming a seat, continuing after a failed Push) fall back to English under de / ja / ru for now.

## Downloads

- **Windows 安装版 / Windows installer**: `DiceFrame-v3.2.1-windows-setup.exe`
- **Windows 便携版 / Windows portable**: `DiceFrame-v3.2.1-windows-portable.zip`
- **源码运行 / Source-run**: `DiceFrame-v3.2.1-windows.zip`
- **托管 Docker 更新 / Managed Docker update**: `DiceFrame-v3.2.1-docker-update-linux-amd64.zip`
- Release 同时提供以上文件的 `SHA256SUMS` 校验。
