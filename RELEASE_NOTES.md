# DiceFrame v2.0.2

## 中文

### 2.0.2 修复 · 插件进程与 Docker 生命周期

- **插件重启恢复正常**：修复插件页点"重启"偶尔报错（HTTP 500）的问题。
- **QQ / NapCat 插件不再残留**：Docker 部署下主程序重启后，QQ / NapCat 插件进程可能残留并继续运行，导致插件开关显示"关"但 Bot 仍在工作、重新打开时提示"已在运行"无法启动。现在残留进程会自动退出并释放占用，开关恢复正常。
- **插件单实例锁自愈**：插件因旧主程序残留实例而无法启动时，会自动接管并清理上一个残留实例，不需要手动杀进程。

### 下载指南

- **普通 Windows 用户**：下载 `DiceFrame-v2.0.2-windows-portable.zip`。
- **源码运行用户**：下载 `DiceFrame-v2.0.2-windows.zip`。
- `.sha256` 是更新校验文件，普通用户不需要手动下载。

## English

### v2.0.2 Fixes · Plugin Process & Docker Lifecycle

- **Plugin restart works again**: fixed the occasional error (HTTP 500) when clicking "Restart" on a plugin.
- **QQ / NapCat plugin no longer lingers**: after a Docker deployment restarts the main process, the QQ / NapCat plugin process could linger and keep running, so the plugin switch showed "off" while the Bot was still active and reopening reported "already running" and failed to start. Lingering processes now exit automatically and release their lock, and the switch works normally again.
- **Plugin single-instance lock self-heals**: when the plugin cannot start because of a leftover instance from an old main process, it now takes over and cleans up the previous instance automatically — no manual process killing required.

### Download Guide

- **Regular Windows users**: download `DiceFrame-v2.0.2-windows-portable.zip`.
- **Source-run users**: download `DiceFrame-v2.0.2-windows.zip`.
- `.sha256` files are update checksums; regular users do not need to download them manually.
