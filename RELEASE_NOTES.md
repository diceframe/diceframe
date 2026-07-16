# DiceFrame v1.0

DiceFrame 进入 1.0 版本。本次重点完善插件系统、插件商店和发布链路。

## 更新内容

- 插件管理页支持安装、卸载、启停、配置和更新插件。
- 插件商店支持从社区索引读取插件，并通过镜像源提高下载可用性。
- 内置 QQ / NapCat 插件已更新为新的 DiceFrame 插件格式，可在设置页统一管理。
- 插件开发文档补充了内容包、主题、地图包等插件类型的标准说明。
- 登录页增加忘记密码提示；忘记后台密码时，可通过 `data/reset_access_password.txt` 重置。
- 项目地址迁移到 `diceframe/diceframe`，插件社区索引迁移到 `diceframe/diceframe-plugins`。
- Docker 发布流程同步到 1.0，镜像会发布到 GHCR；Docker Hub 仍保留 `falconku/diceframe` 入口，方便 NAS 用户查找。

## 升级提示

升级前请保留 `data/` 目录。Docker 用户请拉取新版镜像或使用新版源码重新部署。
