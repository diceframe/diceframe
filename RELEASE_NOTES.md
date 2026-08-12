# DiceFrame v2.0.2-beta.1

## 中文

这是 2.0 系列的预览版，修复插件进程与 Docker 部署下的生命周期问题。预览版频道可以更新体验。

### 插件进程与 Docker 生命周期修复

- **插件重启恢复正常**：修复插件页点"重启"偶尔报错（HTTP 500）的问题。
- **QQ / NapCat 插件不再残留**：Docker 部署下主程序重启后，QQ / NapCat 插件进程可能残留并继续运行，导致插件开关显示"关"但 Bot 仍在工作、重新打开时提示"已在运行"无法启动。现在残留进程会自动退出并释放占用，插件开关恢复正常。
- **插件单实例锁自愈**：插件因旧主程序残留实例而无法启动时，会自动接管并清理上一个残留实例，不需要手动杀进程。

### 说明

预览版仅供预览频道体验，正式版频道暂不推送。发现问题欢迎反馈。

## English

This is a preview release of the 2.0 series, fixing plugin-process and Docker lifecycle issues. Preview-channel users can update and try it.

### Plugin Process & Docker Lifecycle Fixes

- **Plugin restart works again**: fixed the occasional error (HTTP 500) when clicking "Restart" on a plugin.
- **QQ / NapCat plugin no longer lingers**: after a Docker deployment restarts the main process, the QQ / NapCat plugin process could linger and keep running, so the plugin switch showed "off" while the Bot was still active and reopening reported "already running" and failed to start. Lingering processes now exit automatically and release their lock, and the plugin switch works normally again.
- **Plugin single-instance lock self-heals**: when the plugin cannot start because of a leftover instance from an old main process, it now takes over and cleans up the previous instance automatically — no manual process killing required.

### Note

This preview is for the preview channel only; the stable channel is not updated yet. Feedback is welcome.
