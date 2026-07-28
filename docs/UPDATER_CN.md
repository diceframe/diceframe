# DiceFrame 应用更新说明

中文 | [English](UPDATER_EN.md)

本文说明 DiceFrame 主程序的更新方式。插件商店中的插件更新是另一套机制，不在本文范围内。

## 不同安装方式

| 安装方式 | 设置页行为 | 应用方式 |
|---|---|---|
| Windows 便携版 | 下载并应用更新 | 自动重启、健康检查；失败时自动回到旧版本 |
| 解压的源码发布包 | 下载并应用更新 | 事务式替换程序文件；成功后手动重启 |
| Git 开发目录 | 只检查和通知 | 使用 `git pull`，不会覆盖工作区 |
| Docker / NAS | 只检查和通知 | 拉取新镜像并重新创建容器 |

无论哪种方式，`data/` 都属于用户数据，不会被主程序更新包替换。升级前仍建议备份整个 `data/`。

## Windows 便携版

便携版把新程序安装到独立版本目录，不直接覆盖正在运行的版本：

```text
DiceFrame/
  DiceFrame.exe
  app/
  python/
  versions/
    vX.Y.Z/
      app/
      python/
  data/
    _updater/
```

应用更新后，启动器会：

1. 启动候选版本；
2. 请求公开健康接口并核对目标版本；
3. 继续观察 60 秒；
4. 通过后提交当前版本指针；
5. 启动失败、健康检查失败或观察期内退出时，终止候选并恢复旧版本。

v1.6.0 附带的旧启动器还没有监督能力。因此，从 v1.6.0 第一次升级到带新启动器的版本时仍需按发布说明手动升级一次；之后的便携版升级才可完成自动切换和回滚。

## 源码发布包

不含 `.git/` 的源码发布包可以在设置页应用更新。更新器会先备份将被替换的程序文件，再移入新文件；任一步骤失败都会尝试恢复备份。

以下内容不会被更新包覆盖：

- `data/`
- `logs/`
- `.git/`
- `.codex/`
- `.claude/`
- `dist/`

成功后设置页会提示手动重启。Git 克隆目录不会使用这套替换流程。

## Docker 与 NAS

Docker 容器内不会直接替换程序文件。使用 Compose 部署时：

```bash
docker compose pull
docker compose up -d
```

NAS 用户也可以在设备自带的容器管理界面检查镜像更新、拉取新镜像并重新创建容器。请确认 `data/` 已挂载到宿主机。

如果你从本地源码构建镜像，请拉取新源码后执行：

```bash
docker compose up -d --build
```

## 下载与安全检查

- 更新包下载到 `data/_updater/`。
- Release 同时提供 `.sha256` 时会校验 SHA-256。
- ZIP 解压会拒绝绝对路径、盘符路径、`..` 路径穿越、符号链接、文件数量异常和解压体积异常。
- 便携版候选必须包含主服务、内置 Python 和启动器。
- 健康接口只返回 `ok`、版本和进程 ID，不包含配置或密钥。

## 常见问题

### 检查更新出现 HTTP 403

这通常表示镜像源和 GitHub API 的匿名请求额度暂时用完。它只会影响版本检查，不影响游戏、存档或模型调用。稍后重试即可，也可以直接前往项目 Releases 页面查看。

### 自动更新失败

先保留 `data/_updater/state.json` 和相关日志用于排查，不要删除 `data/`。便携版若显示“已自动回滚”，说明旧版本已经重新启动；源码版失败时会报告是否成功恢复备份。

### 发布前验收

除自动化测试外，每个带更新器改动的发布版本都应使用真实便携包完成：

1. 一次正常升级；
2. 一次候选启动失败或提前退出；
3. 确认失败场景能自动回到旧版本。

## HTTP 接口

主程序更新使用以下接口：

- `GET /api/system/update-check`
- `GET /api/system/update/status`
- `POST /api/system/update/download?kind=source|portable`
- `POST /api/system/update/apply`
- `GET /api/system/update/health`

更新状态包括 `idle`、`downloading`、`verifying`、`staged`、`applying`、`restarting`、`done`、`rolled-back` 和 `failed`。
